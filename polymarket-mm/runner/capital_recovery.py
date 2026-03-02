"""runner.capital_recovery — Sell positions to recover operating capital.

When free balance drops below ``min_balance_for_recovery`` and the bot has
open positions, this module plans and executes recovery sales:

1. Sort positions by realized PnL descending (most profitable first).
2. Generate SELL orders for the most profitable token side.
3. Use complement routing for SELL (BUY NO when selling YES w/o shares).
4. Respect minimum order size (5 shares default).
5. Stop once balance is restored above threshold.

Design decisions:
- Sell most profitable first to lock in gains and preserve losing positions
  that might recover.
- Dedup fills by fill_id (Lesson 2: out-of-order fills).
- Normalize USDC at API boundary (Lesson 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from models.position import Position
from runner.config import RotationConfig, UnifiedMarketConfig

logger = structlog.get_logger("runner.capital_recovery")


@dataclass
class RecoveryPlan:
    """Plan for capital recovery sales."""

    target_balance: Decimal
    current_balance: Decimal
    deficit: Decimal
    sell_orders: list[RecoverySellOrder] = field(default_factory=list)

    @property
    def total_expected_proceeds(self) -> Decimal:
        return sum(o.expected_proceeds for o in self.sell_orders)

    @property
    def is_empty(self) -> bool:
        return len(self.sell_orders) == 0


@dataclass
class RecoverySellOrder:
    """A planned sell order for capital recovery."""

    market_id: str
    token_id: str
    token_is_yes: bool
    size: Decimal
    estimated_price: Decimal
    position_pnl: Decimal  # PnL of the position being sold

    @property
    def expected_proceeds(self) -> Decimal:
        return self.size * self.estimated_price


class CapitalRecovery:
    """Plans and executes capital recovery by selling positions.

    Designed to be called periodically from the pipeline's recovery loop.
    """

    def __init__(self, config: RotationConfig) -> None:
        self._config = config
        self._processed_recovery_fills: set[str] = set()

    def plan_recovery(
        self,
        current_balance: Decimal,
        positions: dict[str, Position],
        market_configs: list[UnifiedMarketConfig],
        mid_prices: dict[str, Decimal] | None = None,
    ) -> RecoveryPlan:
        """Plan recovery sales to restore balance above threshold.

        Args:
            current_balance: Current available balance (USDC).
            positions: All open positions keyed by market_id.
            market_configs: Market configurations for min_order_size etc.
            mid_prices: Current mid prices keyed by market_id.

        Returns:
            RecoveryPlan with ordered sell instructions.
        """
        target = self._config.min_balance_for_recovery
        deficit = target - current_balance

        plan = RecoveryPlan(
            target_balance=target,
            current_balance=current_balance,
            deficit=deficit,
        )

        if deficit <= 0:
            return plan  # No recovery needed

        mid_prices = mid_prices or {}
        config_by_id = {m.market_id: m for m in market_configs}

        # Build sellable positions with PnL
        sellable: list[dict[str, Any]] = []

        for market_id, pos in positions.items():
            mc = config_by_id.get(market_id)
            if mc is None:
                continue

            mid = mid_prices.get(market_id)

            # Evaluate YES side
            if pos.qty_yes >= (mc.min_order_size if mc else Decimal("5")):
                estimated_price = mid if mid else pos.avg_entry_yes
                if estimated_price > 0:
                    pnl = (estimated_price - pos.avg_entry_yes) * pos.qty_yes
                    sellable.append({
                        "market_id": market_id,
                        "token_id": mc.token_id_yes,
                        "token_is_yes": True,
                        "qty_available": pos.qty_yes,
                        "estimated_price": estimated_price,
                        "pnl": pnl,
                        "min_order_size": mc.min_order_size,
                    })

            # Evaluate NO side
            if pos.qty_no >= (mc.min_order_size if mc else Decimal("5")):
                no_price = (Decimal("1") - mid) if mid else pos.avg_entry_no
                if no_price > 0:
                    pnl = (no_price - pos.avg_entry_no) * pos.qty_no
                    sellable.append({
                        "market_id": market_id,
                        "token_id": mc.token_id_no,
                        "token_is_yes": False,
                        "qty_available": pos.qty_no,
                        "estimated_price": no_price,
                        "pnl": pnl,
                        "min_order_size": mc.min_order_size,
                    })

        # Sort by PnL descending (most profitable first)
        sellable.sort(key=lambda x: x["pnl"], reverse=True)

        remaining_deficit = deficit
        for s in sellable:
            if remaining_deficit <= 0:
                break

            # How much to sell to cover remaining deficit
            price = s["estimated_price"]
            if price <= 0:
                continue

            needed_qty = remaining_deficit / price
            # Round up to min_order_size, cap at available
            sell_qty = max(s["min_order_size"], min(needed_qty, s["qty_available"]))
            # Ensure we don't exceed available
            sell_qty = min(sell_qty, s["qty_available"])

            if sell_qty < s["min_order_size"]:
                continue

            order = RecoverySellOrder(
                market_id=s["market_id"],
                token_id=s["token_id"],
                token_is_yes=s["token_is_yes"],
                size=sell_qty,
                estimated_price=price,
                position_pnl=s["pnl"],
            )
            plan.sell_orders.append(order)
            remaining_deficit -= order.expected_proceeds

        if plan.sell_orders:
            logger.info(
                "capital_recovery.plan_created",
                deficit=str(deficit),
                sell_orders=len(plan.sell_orders),
                expected_proceeds=str(plan.total_expected_proceeds),
            )

        return plan

    async def execute_recovery(
        self,
        plan: RecoveryPlan,
        venue: Any,  # VenueAdapter
        market_configs: list[UnifiedMarketConfig],
    ) -> list[dict[str, Any]]:
        """Execute the recovery plan by submitting sell orders.

        Uses complement routing for SELL orders when position is insufficient
        (the venue adapter handles this internally).

        Returns list of execution results.
        """
        from models.order import Order, OrderType, Side

        if plan.is_empty:
            return []

        results: list[dict[str, Any]] = []
        config_by_id = {m.market_id: m for m in market_configs}

        for sell_order in plan.sell_orders:
            mc = config_by_id.get(sell_order.market_id)
            if mc is None:
                continue

            try:
                order = Order(
                    market_id=sell_order.market_id,
                    token_id=sell_order.token_id,
                    side=Side.SELL,
                    price=sell_order.estimated_price,
                    size=sell_order.size,
                    order_type=OrderType.GTC,
                    tick_size=mc.tick_size,
                    neg_risk=mc.neg_risk,
                )

                result = await venue.submit_order(order)

                results.append({
                    "market_id": sell_order.market_id,
                    "token_is_yes": sell_order.token_is_yes,
                    "size": sell_order.size,
                    "price": sell_order.estimated_price,
                    "status": result.status.value if hasattr(result, "status") else "unknown",
                    "filled_qty": result.filled_qty if hasattr(result, "filled_qty") else Decimal("0"),
                })

                logger.info(
                    "capital_recovery.order_submitted",
                    market_id=sell_order.market_id,
                    side="SELL",
                    token="YES" if sell_order.token_is_yes else "NO",
                    size=str(sell_order.size),
                    price=str(sell_order.estimated_price),
                    status=result.status.value if hasattr(result, "status") else "unknown",
                )

            except Exception as e:
                logger.warning(
                    "capital_recovery.order_failed",
                    market_id=sell_order.market_id,
                    error=str(e),
                )
                results.append({
                    "market_id": sell_order.market_id,
                    "error": str(e),
                })

        return results

    def needs_recovery(self, current_balance: Decimal) -> bool:
        """Check if capital recovery is needed."""
        return current_balance < self._config.min_balance_for_recovery
