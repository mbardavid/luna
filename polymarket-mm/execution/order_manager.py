"""OrderManager — high-level order lifecycle manager.

Wraps an ``ExecutionProvider`` with:
- Internal tracking of active orders by ``client_order_id``
- Idempotent submission (deduplication)
- Bulk cancel
- Structured logging via ``structlog``
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import structlog

from execution.execution_provider import ExecutionProvider
from models.order import Order, OrderStatus

logger = structlog.get_logger("execution.order_manager")

# Terminal statuses — orders in these states are no longer "active".
_TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
)


class OrderManager:
    """Tracks and manages orders through their lifecycle.

    Parameters
    ----------
    provider:
        The execution backend to delegate actual order operations to.
    """

    def __init__(self, provider: ExecutionProvider) -> None:
        self._provider = provider
        self._orders: dict[UUID, Order] = {}

    # ── Public API ───────────────────────────────────────────────

    async def submit(self, order: Order) -> Order:
        """Submit an order to the venue.

        Idempotent: if an order with the same ``client_order_id`` has
        already been submitted and is still tracked, the cached version
        is returned without hitting the provider again.
        """
        # Idempotency check
        if order.client_order_id in self._orders:
            existing = self._orders[order.client_order_id]
            logger.debug(
                "order_manager.idempotent_hit",
                client_order_id=str(order.client_order_id),
                status=existing.status.value,
            )
            return existing

        logger.info(
            "order_manager.submit",
            client_order_id=str(order.client_order_id),
            market_id=order.market_id,
            side=order.side.value,
            price=str(order.price),
            size=str(order.size),
        )

        result = await self._provider.submit_order(order)
        self._orders[result.client_order_id] = result

        logger.info(
            "order_manager.submitted",
            client_order_id=str(result.client_order_id),
            status=result.status.value,
        )
        return result

    async def amend(
        self,
        client_order_id: UUID,
        new_price: Decimal,
        new_size: Decimal,
    ) -> Order:
        """Amend an active order's price and/or size.

        Raises ``ValueError`` if the order is not tracked or is terminal.
        """
        existing = self._orders.get(client_order_id)
        if existing is None:
            raise ValueError(f"Order {client_order_id} is not tracked")
        if existing.status in _TERMINAL_STATUSES:
            raise ValueError(
                f"Order {client_order_id} is in terminal state {existing.status.value}"
            )

        logger.info(
            "order_manager.amend",
            client_order_id=str(client_order_id),
            old_price=str(existing.price),
            new_price=str(new_price),
            old_size=str(existing.size),
            new_size=str(new_size),
        )

        result = await self._provider.amend_order(client_order_id, new_price, new_size)
        self._orders[client_order_id] = result

        logger.info(
            "order_manager.amended",
            client_order_id=str(client_order_id),
            status=result.status.value,
        )
        return result

    async def cancel(self, client_order_id: UUID) -> bool:
        """Cancel a single order by ``client_order_id``.

        Returns True if the cancellation was accepted by the provider.
        """
        existing = self._orders.get(client_order_id)
        if existing is None:
            logger.warning(
                "order_manager.cancel_unknown",
                client_order_id=str(client_order_id),
            )
            return False

        if existing.status in _TERMINAL_STATUSES:
            logger.debug(
                "order_manager.cancel_terminal",
                client_order_id=str(client_order_id),
                status=existing.status.value,
            )
            return False

        logger.info(
            "order_manager.cancel",
            client_order_id=str(client_order_id),
        )

        success = await self._provider.cancel_order(client_order_id)
        if success:
            self._orders[client_order_id] = existing.model_copy(
                update={"status": OrderStatus.CANCELLED}
            )
        return success

    async def cancel_all(self) -> int:
        """Cancel all active (non-terminal) orders.

        Returns the number of orders successfully cancelled.
        """
        active = self.get_active_orders()
        cancelled = 0
        for order in active:
            try:
                success = await self.cancel(order.client_order_id)
                if success:
                    cancelled += 1
            except Exception:
                logger.exception(
                    "order_manager.cancel_all_error",
                    client_order_id=str(order.client_order_id),
                )
        logger.info("order_manager.cancel_all", cancelled=cancelled, total=len(active))
        return cancelled

    def get_active_orders(self) -> list[Order]:
        """Return all orders that are not in a terminal state."""
        return [
            order
            for order in self._orders.values()
            if order.status not in _TERMINAL_STATUSES
        ]

    def get_order(self, client_order_id: UUID) -> Order | None:
        """Look up a tracked order by ``client_order_id``."""
        return self._orders.get(client_order_id)

    @property
    def tracked_count(self) -> int:
        """Total number of tracked orders (active + terminal)."""
        return len(self._orders)
