"""UnwindManager — automatic position unwinding on shutdown.

Handles graceful position exit when the production runner stops.
Supports multiple strategies:
  - aggressive: sell at best bid/ask (normal shutdown)
  - sweep: sell at mid - 5% (emergency, kill switch)
  - hold: do nothing (crash recovery default)

Workflow:
  1. Cancel all open orders
  2. Merge YES+NO pairs via CTF (saves spread cost)
  3. Sell remaining positions with progressive pricing
  4. Report results

Polymarket constraints:
  - No market orders — must use limit orders at aggressive price
  - Minimum order size = 5 shares
  - Dust positions (< threshold) are logged but not sold
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

from execution.ctf_merge import CTFMerger, MergeResult
from models.position import Position

logger = structlog.get_logger("execution.unwind")


class UnwindStrategy(str, Enum):
    """Unwind pricing strategy."""

    AGGRESSIVE = "aggressive"  # best bid/ask
    SWEEP = "sweep"           # mid - 5% (emergency)
    HOLD = "hold"             # do nothing (crash recovery)


@dataclass
class UnwindConfig:
    """Configuration for the UnwindManager."""

    enabled: bool = True
    max_time_seconds: float = 60.0
    strategy: UnwindStrategy = UnwindStrategy.AGGRESSIVE
    dust_threshold_shares: Decimal = Decimal("5")
    merge_enabled: bool = True
    progressive_pricing: list[Decimal] = field(default_factory=lambda: [
        Decimal("0"),   # attempt 1: at market
        Decimal("2"),   # attempt 2: 2% worse
        Decimal("5"),   # attempt 3: 5% worse
    ])
    alert_on_orphan: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UnwindConfig:
        """Create config from a dictionary (e.g., YAML section)."""
        pp = d.get("progressive_pricing", {})
        pricing = [
            Decimal(str(pp.get("attempt_1_offset_pct", 0))),
            Decimal(str(pp.get("attempt_2_offset_pct", 2))),
            Decimal(str(pp.get("attempt_3_offset_pct", 5))),
        ]

        strategy_map = d.get("strategies", {})
        # Default to aggressive for normal operations
        default_strategy = strategy_map.get("normal_shutdown", "aggressive")

        return cls(
            enabled=d.get("enabled", True),
            max_time_seconds=float(d.get("max_time_seconds", 60)),
            strategy=UnwindStrategy(default_strategy),
            dust_threshold_shares=Decimal(str(d.get("dust_threshold_shares", 5))),
            merge_enabled=d.get("merge_enabled", True),
            progressive_pricing=pricing,
            alert_on_orphan=d.get("alert_on_orphan", True),
        )


@dataclass
class SellResult:
    """Result of selling a single position side."""

    market_id: str
    token_side: str  # "YES" or "NO"
    shares_to_sell: Decimal
    shares_sold: Decimal
    avg_price: Decimal
    proceeds: Decimal
    attempts: int
    success: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_id": self.market_id,
            "token_side": self.token_side,
            "shares_to_sell": str(self.shares_to_sell),
            "shares_sold": str(self.shares_sold),
            "avg_price": str(self.avg_price),
            "proceeds": str(self.proceeds),
            "attempts": self.attempts,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class UnwindReport:
    """Full report of an unwind operation."""

    reason: str
    strategy: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    finished_at: str = ""
    duration_seconds: float = 0.0
    orders_cancelled: int = 0
    merges: list[dict[str, Any]] = field(default_factory=list)
    sells: list[dict[str, Any]] = field(default_factory=list)
    dust_skipped: list[dict[str, Any]] = field(default_factory=list)
    orphaned: list[dict[str, Any]] = field(default_factory=list)
    total_proceeds: Decimal = Decimal("0")
    total_merged_usdc: Decimal = Decimal("0")
    total_gas_cost: Decimal = Decimal("0")
    success: bool = False
    timed_out: bool = False

    def add_sell(self, result: SellResult) -> None:
        self.sells.append(result.to_dict())
        if result.success:
            self.total_proceeds += result.proceeds
        else:
            self.orphaned.append({
                "market_id": result.market_id,
                "token_side": result.token_side,
                "shares_remaining": str(result.shares_to_sell - result.shares_sold),
                "error": result.error,
            })

    def add_merge(self, result: MergeResult) -> None:
        self.merges.append(result.to_dict())
        if result.success:
            self.total_merged_usdc += result.usdc_received
            self.total_gas_cost += result.gas_cost_usd

    def add_dust(self, market_id: str, token_side: str, shares: Decimal) -> None:
        self.dust_skipped.append({
            "market_id": market_id,
            "token_side": token_side,
            "shares": str(shares),
        })

    def finalize(self) -> None:
        self.finished_at = datetime.now(timezone.utc).isoformat()
        if self.started_at:
            try:
                start = datetime.fromisoformat(self.started_at)
                end = datetime.fromisoformat(self.finished_at)
                self.duration_seconds = (end - start).total_seconds()
            except Exception:
                pass
        self.success = len(self.orphaned) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "strategy": self.strategy,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "orders_cancelled": self.orders_cancelled,
            "merges": self.merges,
            "sells": self.sells,
            "dust_skipped": self.dust_skipped,
            "orphaned": self.orphaned,
            "total_proceeds": str(self.total_proceeds),
            "total_merged_usdc": str(self.total_merged_usdc),
            "total_gas_cost": str(self.total_gas_cost),
            "success": self.success,
            "timed_out": self.timed_out,
        }

    def save(self, path: str | Path) -> None:
        """Save the report as JSON to the given path."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        logger.info("unwind.report_saved", path=str(p))


class UnwindManager:
    """Manages position unwinding on shutdown.

    Parameters
    ----------
    rest_client:
        Connected CLOBRestClient for order operations and price queries.
    ctf_merger:
        CTFMerger instance for on-chain merge operations.
    config:
        UnwindConfig with strategy, timing, and threshold settings.
    """

    def __init__(
        self,
        rest_client: Any,
        ctf_merger: CTFMerger | None = None,
        config: UnwindConfig | None = None,
    ) -> None:
        self._rest = rest_client
        self._merger = ctf_merger or CTFMerger()
        self._config = config or UnwindConfig()

    @property
    def config(self) -> UnwindConfig:
        return self._config

    async def unwind_all(
        self,
        positions: dict[str, Position],
        reason: str,
        strategy: UnwindStrategy | None = None,
        market_configs: list[Any] | None = None,
    ) -> UnwindReport:
        """Unwind all open positions.

        Parameters
        ----------
        positions:
            Dict of market_id → Position.
        reason:
            Human-readable reason for unwind (e.g., "SIGTERM", "KILL_SWITCH").
        strategy:
            Override strategy (defaults to config strategy).
        market_configs:
            Optional list of ProdMarketConfig for condition_id lookup.

        Returns
        -------
        UnwindReport
            Full report with all actions taken.
        """
        effective_strategy = strategy or self._config.strategy
        report = UnwindReport(reason=reason, strategy=effective_strategy.value)

        if effective_strategy == UnwindStrategy.HOLD:
            logger.info("unwind.holding", reason=reason)
            report.finalize()
            return report

        if not self._config.enabled:
            logger.info("unwind.disabled", reason=reason)
            report.finalize()
            return report

        logger.info(
            "unwind.starting",
            reason=reason,
            strategy=effective_strategy.value,
            positions=len(positions),
            max_time_s=self._config.max_time_seconds,
        )

        start_time = time.monotonic()
        deadline = start_time + self._config.max_time_seconds

        # Build condition_id map from market_configs
        condition_map: dict[str, str] = {}
        if market_configs:
            for mc in market_configs:
                condition_map[mc.market_id] = getattr(mc, "condition_id", mc.market_id)

        # Step 1: Cancel all open orders
        try:
            cancelled = await asyncio.wait_for(
                self._cancel_all_orders(),
                timeout=max(1.0, deadline - time.monotonic()),
            )
            report.orders_cancelled = cancelled
        except asyncio.TimeoutError:
            logger.warning("unwind.cancel_timeout")
        except Exception as e:
            logger.error("unwind.cancel_error", error=str(e))

        # Step 2: Merge YES+NO pairs if enabled
        if self._config.merge_enabled and time.monotonic() < deadline:
            for market_id, pos in positions.items():
                if time.monotonic() >= deadline:
                    report.timed_out = True
                    break
                if pos.can_merge:
                    condition_id = condition_map.get(market_id, market_id)
                    mergeable = self._merger.calculate_mergeable(pos.qty_yes, pos.qty_no)
                    if mergeable > Decimal("0"):
                        try:
                            merge_result = await asyncio.wait_for(
                                self._merger.merge_positions(
                                    condition_id=condition_id,
                                    amount=mergeable,
                                ),
                                timeout=max(1.0, deadline - time.monotonic()),
                            )
                            report.add_merge(merge_result)
                            if merge_result.success:
                                # Update position quantities after merge
                                pos = pos.model_copy(update={
                                    "qty_yes": pos.qty_yes - mergeable,
                                    "qty_no": pos.qty_no - mergeable,
                                })
                                positions[market_id] = pos
                        except asyncio.TimeoutError:
                            logger.warning("unwind.merge_timeout", market_id=market_id)
                            report.timed_out = True
                        except Exception as e:
                            logger.error("unwind.merge_error", market_id=market_id, error=str(e))

        # Step 3: Sell remaining positions
        if time.monotonic() < deadline:
            for market_id, pos in positions.items():
                if time.monotonic() >= deadline:
                    report.timed_out = True
                    break

                # Sell YES side
                if pos.qty_yes > Decimal("0"):
                    if pos.qty_yes < self._config.dust_threshold_shares:
                        report.add_dust(market_id, "YES", pos.qty_yes)
                    elif time.monotonic() < deadline:
                        result = await self._sell_position(
                            market_id=market_id,
                            token_id=pos.token_id_yes,
                            token_side="YES",
                            shares=pos.qty_yes,
                            strategy=effective_strategy,
                            deadline=deadline,
                        )
                        report.add_sell(result)

                # Sell NO side
                if pos.qty_no > Decimal("0"):
                    if pos.qty_no < self._config.dust_threshold_shares:
                        report.add_dust(market_id, "NO", pos.qty_no)
                    elif time.monotonic() < deadline:
                        result = await self._sell_position(
                            market_id=market_id,
                            token_id=pos.token_id_no,
                            token_side="NO",
                            shares=pos.qty_no,
                            strategy=effective_strategy,
                            deadline=deadline,
                        )
                        report.add_sell(result)

        if time.monotonic() >= deadline:
            report.timed_out = True

        report.finalize()

        logger.info(
            "unwind.complete",
            reason=reason,
            success=report.success,
            timed_out=report.timed_out,
            duration_s=round(report.duration_seconds, 2),
            proceeds=str(report.total_proceeds),
            merged_usdc=str(report.total_merged_usdc),
            orphaned=len(report.orphaned),
            dust=len(report.dust_skipped),
        )

        return report

    async def _cancel_all_orders(self) -> int:
        """Cancel all open orders. Returns count cancelled."""
        try:
            result = await self._rest.cancel_all_orders()
            logger.info("unwind.orders_cancelled")
            return 1 if result else 0
        except Exception as e:
            logger.error("unwind.cancel_all_error", error=str(e))
            return 0

    async def _sell_position(
        self,
        market_id: str,
        token_id: str,
        token_side: str,
        shares: Decimal,
        strategy: UnwindStrategy,
        deadline: float,
    ) -> SellResult:
        """Sell a position with progressive pricing.

        Attempts to sell at progressively worse prices until filled
        or the deadline is reached.
        """
        shares_sold = Decimal("0")
        total_proceeds = Decimal("0")
        attempts = 0

        pricing_offsets = self._config.progressive_pricing
        if strategy == UnwindStrategy.SWEEP:
            # Sweep uses the most aggressive offset immediately
            pricing_offsets = [Decimal("5")]

        for i, offset_pct in enumerate(pricing_offsets):
            if time.monotonic() >= deadline:
                break

            remaining = shares - shares_sold
            if remaining < self._config.dust_threshold_shares:
                break

            attempts += 1

            try:
                # Get current price
                mid_price = await self._get_mid_price(token_id)
                if mid_price <= Decimal("0"):
                    continue

                # Calculate sell price with offset
                if token_side == "YES":
                    # Selling YES: sell at mid - offset
                    price = mid_price * (Decimal("1") - offset_pct / Decimal("100"))
                else:
                    # Selling NO: sell at (1 - mid) - offset
                    # But on the CLOB we sell the NO token, so price is
                    # the best bid for NO token
                    price = mid_price * (Decimal("1") - offset_pct / Decimal("100"))

                # Clamp price to valid range
                price = max(Decimal("0.01"), min(Decimal("0.99"), price))

                # Place aggressive sell order (FOK or GTC with short TTL)
                order_result = await self._rest.create_and_post_order(
                    token_id=token_id,
                    price=float(price),
                    size=float(remaining),
                    side="SELL",
                    order_type="FOK",
                )

                # Check if filled
                if isinstance(order_result, dict):
                    error = order_result.get("error") or order_result.get("errorMsg")
                    if not error:
                        # FOK: either fully filled or rejected
                        shares_sold = remaining
                        total_proceeds = remaining * price
                        break
                    else:
                        logger.warning(
                            "unwind.sell_attempt_failed",
                            market_id=market_id,
                            token_side=token_side,
                            attempt=i + 1,
                            error=error,
                        )

            except Exception as e:
                logger.error(
                    "unwind.sell_error",
                    market_id=market_id,
                    token_side=token_side,
                    attempt=i + 1,
                    error=str(e),
                )

            # Wait between attempts (but respect deadline)
            if i < len(pricing_offsets) - 1:
                wait = min(3.0, max(0, deadline - time.monotonic()))
                if wait > 0:
                    await asyncio.sleep(wait)

        avg_price = total_proceeds / shares_sold if shares_sold > 0 else Decimal("0")
        success = shares_sold >= (shares - self._config.dust_threshold_shares)

        return SellResult(
            market_id=market_id,
            token_side=token_side,
            shares_to_sell=shares,
            shares_sold=shares_sold,
            avg_price=avg_price,
            proceeds=total_proceeds,
            attempts=attempts,
            success=success,
            error=None if success else f"Only sold {shares_sold}/{shares} after {attempts} attempts",
        )

    async def _get_mid_price(self, token_id: str) -> Decimal:
        """Get mid price for a token."""
        try:
            return await self._rest.get_midpoint(token_id)
        except Exception:
            try:
                return await self._rest.get_price(token_id)
            except Exception:
                return Decimal("0")
