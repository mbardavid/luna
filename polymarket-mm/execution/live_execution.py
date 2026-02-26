"""LiveExecution — Real Polymarket CLOB execution provider.

Implements the ExecutionProvider interface using CLOBRestClient
for real order submission, cancellation, and management.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

import structlog

from data.rest_client import CLOBRestClient
from execution.execution_provider import ExecutionProvider
from models.order import Order, OrderStatus, Side

logger = structlog.get_logger("execution.live_execution")


class LiveExecution(ExecutionProvider):
    """Live execution backend using the Polymarket CLOB API.

    Parameters
    ----------
    rest_client:
        Connected ``CLOBRestClient`` instance.
    default_tick_size:
        Default tick size if not specified per-order.
    default_neg_risk:
        Default neg_risk flag if not specified per-order.
    """

    def __init__(
        self,
        rest_client: CLOBRestClient,
        default_tick_size: str = "0.01",
        default_neg_risk: bool = False,
    ) -> None:
        self._rest = rest_client
        self._default_tick_size = default_tick_size
        self._default_neg_risk = default_neg_risk
        # Map client_order_id -> exchange_order_id for cancellation
        self._order_id_map: dict[UUID, str] = {}

    async def submit_order(self, order: Order) -> Order:
        """Submit an order to Polymarket CLOB.

        Uses create_and_post_order for atomic sign+submit.
        Returns the order with updated status.
        """
        try:
            side_str = "BUY" if order.side == Side.BUY else "SELL"

            result = await self._rest.create_and_post_order(
                token_id=order.token_id,
                price=float(order.price),
                size=float(order.size),
                side=side_str,
                order_type=order.order_type.value,
                post_only=order.maker_only,
                tick_size=self._default_tick_size,
                neg_risk=self._default_neg_risk,
            )

            # Extract exchange order ID from result
            exchange_id = None
            if isinstance(result, dict):
                exchange_id = result.get("orderID") or result.get("id") or result.get("order_id")
                error = result.get("error") or result.get("errorMsg")
                if error:
                    logger.warning(
                        "live_execution.order_rejected",
                        client_order_id=str(order.client_order_id),
                        error=error,
                    )
                    order.status = OrderStatus.REJECTED
                    return order

            if exchange_id:
                self._order_id_map[order.client_order_id] = str(exchange_id)

            order.status = OrderStatus.OPEN
            logger.info(
                "live_execution.order_submitted",
                client_order_id=str(order.client_order_id),
                exchange_id=exchange_id,
                side=side_str,
                price=str(order.price),
                size=str(order.size),
            )
            return order

        except Exception as exc:
            logger.error(
                "live_execution.submit_failed",
                client_order_id=str(order.client_order_id),
                error=str(exc)[:200],
            )
            order.status = OrderStatus.REJECTED
            return order

    async def cancel_order(self, client_order_id: UUID) -> bool:
        """Cancel an open order by its client_order_id.

        Returns True if cancelled successfully.
        """
        exchange_id = self._order_id_map.get(client_order_id)
        if not exchange_id:
            logger.warning(
                "live_execution.cancel_unknown_order",
                client_order_id=str(client_order_id),
            )
            return False

        success = await self._rest.cancel_order(exchange_id)
        if success:
            self._order_id_map.pop(client_order_id, None)

        return success

    async def amend_order(
        self,
        client_order_id: UUID,
        new_price: Decimal,
        new_size: Decimal,
    ) -> Order:
        """Amend an open order's price and/or size.

        Polymarket doesn't support native amend, so we cancel + resubmit.
        """
        raise NotImplementedError(
            "Polymarket CLOB does not support atomic amend. "
            "Use cancel + new order instead."
        )

    async def get_open_orders(self) -> list[Order]:
        """Return all currently open (non-terminal) orders."""
        raw_orders = await self._rest.get_open_orders()

        orders: list[Order] = []
        for raw in raw_orders:
            try:
                side = Side.BUY if raw.get("side", "").upper() == "BUY" else Side.SELL
                orders.append(Order(
                    market_id=raw.get("market", raw.get("condition_id", "")),
                    token_id=raw.get("asset_id", raw.get("token_id", "")),
                    side=side,
                    price=Decimal(str(raw.get("price", "0"))),
                    size=Decimal(str(raw.get("original_size", raw.get("size", "0")))),
                    filled_qty=Decimal(str(raw.get("size_matched", "0"))),
                    status=OrderStatus.OPEN,
                ))
            except Exception as exc:
                logger.warning(
                    "live_execution.parse_order_failed",
                    error=str(exc),
                    raw=str(raw)[:200],
                )

        return orders
