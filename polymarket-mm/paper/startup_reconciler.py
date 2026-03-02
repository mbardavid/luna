"""StartupReconciler — Pre-startup reconciliation for crash recovery.

Before the PMM begins quoting, this module:

1. **Cancel Stale Orders** — cancels ALL open orders on the CLOB
   to prevent duplicate exposure from pre-crash orders.
2. **Position Sync** — reads on-chain balances (CTF ERC-1155 + USDC.e)
   and initializes the position tracker with real quantities.
3. **Market State Refresh** — fetches a fresh orderbook snapshot
   to compute an accurate mid price (no stale cache).
4. **Safety Checks** — verifies kill switch conditions, balance
   minimums, and net inventory limits before allowing quoting.

If any phase fails irrecoverably (after retries), startup is aborted.

Usage::

    reconciler = StartupReconciler(
        rest_client=rest_client,
        market_configs=market_configs,
        config=StartupReconciliationConfig(...),
    )
    result = await reconciler.reconcile()
    if not result.passed:
        sys.exit(1)
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger("startup.reconciler")

_ZERO = Decimal("0")
_MICRO_UNITS = Decimal("1000000")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class StartupReconciliationConfig:
    """Configuration for startup reconciliation.

    All thresholds are configurable via YAML and have safe defaults.
    """

    # Whether startup reconciliation is enabled (backward compat)
    enabled: bool = True

    # Total timeout for all reconciliation phases (seconds)
    timeout_s: float = 120.0

    # Max retries per cancel operation
    cancel_max_retries: int = 3

    # Delay between cancel retries (seconds)
    cancel_retry_delay_s: float = 2.0

    # Safety: minimum USDC balance to allow quoting
    min_balance_to_quote: Decimal = Decimal("5")

    # Safety: maximum drawdown threshold (position value in USDC)
    max_drawdown_usd: Decimal = Decimal("50")

    # Safety: maximum position per side (shares)
    max_position_per_side: Decimal = Decimal("100")

    # Kill switch: max position value before refusing to start
    kill_switch_max_position_value: Decimal = Decimal("500")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StartupReconciliationConfig":
        """Create config from a dict (e.g. YAML section)."""
        return cls(
            enabled=bool(d.get("enabled", True)),
            timeout_s=float(d.get("timeout_s", 120.0)),
            cancel_max_retries=int(d.get("cancel_max_retries", 3)),
            cancel_retry_delay_s=float(d.get("cancel_retry_delay_s", 2.0)),
            min_balance_to_quote=Decimal(str(d.get("min_balance_to_quote", "5"))),
            max_drawdown_usd=Decimal(str(d.get("max_drawdown_usd", "50"))),
            max_position_per_side=Decimal(str(d.get("max_position_per_side", "100"))),
            kill_switch_max_position_value=Decimal(
                str(d.get("kill_switch_max_position_value", "500"))
            ),
        )


# ── Result ───────────────────────────────────────────────────────────


@dataclass
class ReconciliationResult:
    """Result of startup reconciliation."""

    passed: bool = False
    reason: str = ""

    # Phase 1: Cancelled orders
    cancelled_orders: list[dict[str, Any]] = field(default_factory=list)
    cancel_failures: list[dict[str, Any]] = field(default_factory=list)

    # Phase 2: Position sync
    positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    usdc_balance: Decimal = _ZERO

    # Phase 3: Market state
    market_states: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Phase 4: Safety
    safety_warnings: list[str] = field(default_factory=list)

    # Timing
    duration_s: float = 0.0


# ── StartupReconciler ────────────────────────────────────────────────


class StartupReconciler:
    """Pre-startup reconciliation engine.

    Parameters
    ----------
    rest_client:
        Connected CLOBRestClient for API calls.
    market_configs:
        List of market configs (ProdMarketConfig or similar with
        token_id_yes, token_id_no, market_id attributes).
    config:
        Reconciliation configuration.
    """

    def __init__(
        self,
        rest_client: Any,
        market_configs: list[Any],
        config: StartupReconciliationConfig | None = None,
    ) -> None:
        self._rest = rest_client
        self._markets = market_configs
        self._config = config or StartupReconciliationConfig()

    @property
    def config(self) -> StartupReconciliationConfig:
        return self._config

    async def reconcile(self) -> ReconciliationResult:
        """Run all reconciliation phases.

        Returns a ReconciliationResult. If ``result.passed`` is False,
        the bot should NOT start quoting.
        """
        result = ReconciliationResult()
        start = time.monotonic()

        logger.info(
            "startup.reconciliation.begin",
            markets=len(self._markets),
            timeout_s=self._config.timeout_s,
        )

        try:
            # Wrap the entire reconciliation in a timeout
            await asyncio.wait_for(
                self._run_phases(result),
                timeout=self._config.timeout_s,
            )
        except asyncio.TimeoutError:
            result.passed = False
            result.reason = f"startup reconciliation timed out after {self._config.timeout_s}s"
            logger.error("startup.reconciliation.timeout", timeout_s=self._config.timeout_s)
        except Exception as e:
            result.passed = False
            result.reason = f"startup reconciliation failed: {e}"
            logger.error("startup.reconciliation.error", error=str(e))

        result.duration_s = time.monotonic() - start

        logger.info(
            "startup.reconciliation.complete",
            passed=result.passed,
            reason=result.reason,
            duration_s=round(result.duration_s, 2),
            cancelled_orders=len(result.cancelled_orders),
            cancel_failures=len(result.cancel_failures),
        )

        return result

    async def _run_phases(self, result: ReconciliationResult) -> None:
        """Execute all reconciliation phases sequentially."""
        # Phase 1: Cancel stale orders
        if not await self._phase_cancel_stale_orders(result):
            return

        # Phase 2: On-chain position sync
        await self._phase_position_sync(result)

        # Phase 3: Market state refresh
        await self._phase_market_state_refresh(result)

        # Phase 4: Safety checks
        self._phase_safety_checks(result)

    # ── Phase 1: Cancel Stale Orders ─────────────────────────────

    async def _phase_cancel_stale_orders(self, result: ReconciliationResult) -> bool:
        """Cancel ALL open orders before starting.

        Returns True if all cancellations succeeded (or no orders).
        Returns False if cancellation failed after retries → abort startup.
        """
        logger.info("startup.phase1.cancel_stale_orders.begin")

        try:
            open_orders = await self._rest.get_open_orders()
        except Exception as e:
            result.passed = False
            result.reason = f"failed to fetch open orders: {e}"
            logger.error("startup.phase1.fetch_orders_failed", error=str(e))
            return False

        if not open_orders:
            logger.info("startup.phase1.no_stale_orders")
            return True

        logger.info("startup.phase1.found_stale_orders", count=len(open_orders))

        # Cancel each order individually with retries
        for order in open_orders:
            order_id = self._extract_order_id(order)
            if not order_id:
                logger.warning("startup.phase1.no_order_id", order=str(order)[:200])
                continue

            price = self._extract_field(order, "price", "unknown")
            side = self._extract_field(order, "side", "unknown")

            success = await self._cancel_with_retry(order_id)
            order_info = {
                "order_id": order_id,
                "price": str(price),
                "side": str(side),
            }

            if success:
                result.cancelled_orders.append(order_info)
                logger.info(
                    "startup.cancelled_stale_order",
                    order_id=order_id,
                    price=str(price),
                    side=str(side),
                )
            else:
                result.cancel_failures.append(order_info)
                logger.error(
                    "startup.phase1.cancel_failed",
                    order_id=order_id,
                    price=str(price),
                    side=str(side),
                )

        # If ANY cancel failed, abort startup
        if result.cancel_failures:
            result.passed = False
            result.reason = (
                f"failed to cancel {len(result.cancel_failures)} stale orders"
            )
            logger.error(
                "startup.phase1.abort",
                failed_count=len(result.cancel_failures),
            )
            return False

        logger.info(
            "startup.phase1.complete",
            cancelled=len(result.cancelled_orders),
        )
        return True

    async def _cancel_with_retry(self, order_id: str) -> bool:
        """Cancel an order with retries."""
        cfg = self._config
        for attempt in range(1, cfg.cancel_max_retries + 1):
            try:
                success = await self._rest.cancel_order(order_id)
                if success:
                    return True
                logger.warning(
                    "startup.phase1.cancel_retry",
                    order_id=order_id,
                    attempt=attempt,
                )
            except Exception as e:
                logger.warning(
                    "startup.phase1.cancel_error",
                    order_id=order_id,
                    attempt=attempt,
                    error=str(e),
                )

            if attempt < cfg.cancel_max_retries:
                await asyncio.sleep(cfg.cancel_retry_delay_s)

        return False

    # ── Phase 2: On-Chain Position Sync ──────────────────────────

    async def _phase_position_sync(self, result: ReconciliationResult) -> None:
        """Read on-chain balances for all configured token IDs + USDC."""
        logger.info("startup.phase2.position_sync.begin")

        # Read USDC.e balance
        try:
            balance_info = await self._rest.get_balance_allowance("COLLATERAL")
            raw_balance = Decimal(str(balance_info.get("balance", "0")))
            # Lesson 3: Normalize micro-units at API boundary
            result.usdc_balance = raw_balance / _MICRO_UNITS
            logger.info(
                "startup.phase2.usdc_balance",
                raw_micro_usdc=str(raw_balance),
                usdc_balance=str(result.usdc_balance),
            )
        except Exception as e:
            logger.warning("startup.phase2.usdc_balance_error", error=str(e))
            result.usdc_balance = _ZERO

        # Read conditional token balances for each market
        for mc in self._markets:
            market_id = mc.market_id
            token_id_yes = mc.token_id_yes
            token_id_no = mc.token_id_no

            yes_shares = _ZERO
            no_shares = _ZERO

            try:
                yes_info = await self._rest.get_balance_allowance(
                    "CONDITIONAL", token_id=token_id_yes
                )
                raw_yes = Decimal(str(yes_info.get("balance", "0")))
                yes_shares = raw_yes / _MICRO_UNITS
            except Exception as e:
                logger.warning(
                    "startup.phase2.yes_balance_error",
                    market_id=market_id,
                    error=str(e),
                )

            try:
                no_info = await self._rest.get_balance_allowance(
                    "CONDITIONAL", token_id=token_id_no
                )
                raw_no = Decimal(str(no_info.get("balance", "0")))
                no_shares = raw_no / _MICRO_UNITS
            except Exception as e:
                logger.warning(
                    "startup.phase2.no_balance_error",
                    market_id=market_id,
                    error=str(e),
                )

            result.positions[market_id] = {
                "yes_shares": yes_shares,
                "no_shares": no_shares,
                "token_id_yes": token_id_yes,
                "token_id_no": token_id_no,
            }

            # Lesson 1: Log YES + NO as pair, not individually
            logger.info(
                "startup.position_sync",
                market_id=market_id,
                yes=str(yes_shares),
                no=str(no_shares),
                usdc_available=str(result.usdc_balance),
            )

    # ── Phase 3: Market State Refresh ────────────────────────────

    async def _phase_market_state_refresh(self, result: ReconciliationResult) -> None:
        """Fetch fresh orderbook and compute mid price for each market."""
        logger.info("startup.phase3.market_state_refresh.begin")

        for mc in self._markets:
            market_id = mc.market_id
            token_id_yes = mc.token_id_yes

            mid = _ZERO
            spread_bps = _ZERO
            best_bid = _ZERO
            best_ask = _ZERO

            try:
                ob = await self._rest.get_orderbook(token_id_yes)
                bids = ob.get("bids", [])
                asks = ob.get("asks", [])

                if bids and asks:
                    best_bid = Decimal(str(bids[0]["price"]))
                    best_ask = Decimal(str(asks[0]["price"]))
                    mid = (best_bid + best_ask) / Decimal("2")

                    if mid > _ZERO:
                        spread_bps = (best_ask - best_bid) / mid * Decimal("10000")
                elif bids:
                    mid = Decimal(str(bids[0]["price"]))
                elif asks:
                    mid = Decimal(str(asks[0]["price"]))

            except Exception as e:
                logger.warning(
                    "startup.phase3.orderbook_error",
                    market_id=market_id,
                    error=str(e),
                )

            result.market_states[market_id] = {
                "mid": mid,
                "spread_bps": spread_bps,
                "best_bid": best_bid,
                "best_ask": best_ask,
            }

            logger.info(
                "startup.market_state",
                market_id=market_id,
                mid=str(mid),
                spread_bps=str(spread_bps.quantize(Decimal("0.1")) if spread_bps else "0"),
            )

    # ── Phase 4: Safety Checks ───────────────────────────────────

    def _phase_safety_checks(self, result: ReconciliationResult) -> None:
        """Verify safety conditions before allowing quoting."""
        logger.info("startup.phase4.safety_checks.begin")
        cfg = self._config
        passed = True
        reason_parts: list[str] = []

        # Check 1: Kill switch — total position value
        total_position_value = _ZERO
        for market_id, pos_data in result.positions.items():
            ms = result.market_states.get(market_id, {})
            mid = ms.get("mid", _ZERO)
            if mid > _ZERO:
                yes_val = pos_data["yes_shares"] * mid
                no_val = pos_data["no_shares"] * (Decimal("1") - mid)
                total_position_value += yes_val + no_val

        if total_position_value > cfg.kill_switch_max_position_value:
            passed = False
            reason_parts.append(
                f"position_value={total_position_value} > max={cfg.kill_switch_max_position_value}"
            )
            logger.error(
                "startup.safety_check.kill_switch",
                position_value=str(total_position_value),
                max_value=str(cfg.kill_switch_max_position_value),
            )

        # Check 2: Minimum balance
        if result.usdc_balance < cfg.min_balance_to_quote:
            passed = False
            reason_parts.append(
                f"usdc_balance={result.usdc_balance} < min={cfg.min_balance_to_quote}"
            )
            logger.error(
                "startup.safety_check.insufficient_balance",
                usdc_balance=str(result.usdc_balance),
                min_balance=str(cfg.min_balance_to_quote),
            )

        # Check 3: Net inventory warning + skew flag
        for market_id, pos_data in result.positions.items():
            yes_shares = pos_data["yes_shares"]
            no_shares = pos_data["no_shares"]
            net_inventory = abs(yes_shares - no_shares)

            if net_inventory > cfg.max_position_per_side:
                warning = (
                    f"market={market_id}: net_inventory={net_inventory} "
                    f"> max_position_per_side={cfg.max_position_per_side}"
                )
                result.safety_warnings.append(warning)
                pos_data["needs_skew_adjustment"] = True
                logger.warning(
                    "startup.safety_check.high_net_inventory",
                    market_id=market_id,
                    yes=str(yes_shares),
                    no=str(no_shares),
                    net=str(net_inventory),
                    max_per_side=str(cfg.max_position_per_side),
                )

        reason = "; ".join(reason_parts) if reason_parts else "all checks passed"
        result.passed = passed
        result.reason = reason

        logger.info(
            "startup.safety_check",
            passed=passed,
            reason=reason,
            warnings=len(result.safety_warnings),
            total_position_value=str(total_position_value),
            usdc_balance=str(result.usdc_balance),
        )

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _extract_order_id(order: Any) -> str:
        """Extract order ID from various order formats."""
        if isinstance(order, dict):
            return str(
                order.get("id", "")
                or order.get("order_id", "")
                or order.get("orderID", "")
            )
        # Object with attributes
        for attr in ("id", "order_id", "orderID"):
            val = getattr(order, attr, None)
            if val:
                return str(val)
        return ""

    @staticmethod
    def _extract_field(order: Any, field_name: str, default: str = "") -> str:
        """Extract a field from order (dict or object)."""
        if isinstance(order, dict):
            return str(order.get(field_name, default))
        return str(getattr(order, field_name, default))

    def apply_to_wallet(
        self,
        wallet: Any,
        result: ReconciliationResult,
    ) -> None:
        """Apply reconciled positions to a ProductionWallet.

        Initializes the wallet's position tracker with real on-chain
        balances and sets the available balance to match USDC.

        Parameters
        ----------
        wallet:
            ProductionWallet instance.
        result:
            ReconciliationResult from reconcile().
        """
        for market_id, pos_data in result.positions.items():
            yes_shares = pos_data["yes_shares"]
            no_shares = pos_data["no_shares"]
            token_id_yes = pos_data["token_id_yes"]
            token_id_no = pos_data["token_id_no"]

            # Ensure position is initialized
            wallet.init_position(market_id, token_id_yes, token_id_no)

            # Set real balances on the position
            pos = wallet.get_position(market_id)
            if pos is not None:
                wallet._positions[market_id] = pos.model_copy(
                    update={
                        "qty_yes": yes_shares,
                        "qty_no": no_shares,
                    }
                )

        # Lesson 11: Set available balance from on-chain USDC
        # so balance-aware quoting (Gate 5) works from the first cycle
        if result.usdc_balance > _ZERO:
            wallet._available_balance = result.usdc_balance

        logger.info(
            "startup.wallet_synced",
            markets=len(result.positions),
            usdc_available=str(result.usdc_balance),
        )
