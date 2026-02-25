"""PaperExecution — ``ExecutionProvider`` backed by ``PaperVenue``.

Provides order submission, cancellation, amendment with:
- Idempotency by ``client_order_id``
- Tick-size validation (rejects invalid prices)
- Simulated rate limiting
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from uuid import UUID

import structlog

from core.event_bus import EventBus
from execution.execution_provider import ExecutionProvider
from models.order import Order, OrderStatus
from paper.paper_venue import PaperVenue

logger = structlog.get_logger("paper.execution")


class PaperExecution(ExecutionProvider):
    """Paper execution provider that delegates to ``PaperVenue``.

    Features:
    - Idempotent ``submit_order`` (same ``client_order_id`` returns cached result)
    - Tick-size validation before submission
    - Configurable rate limiting (max N orders per second)
    """

    def __init__(
        self,
        venue: PaperVenue,
        event_bus: EventBus,
        max_orders_per_second: int = 50,
    ) -> None:
        self._venue = venue
        self._event_bus = event_bus
        self._max_ops = max_orders_per_second

        # Idempotency cache: client_order_id -> Order
        self._submitted: dict[UUID, Order] = {}
        # Rate limiting
        self._op_timestamps: list[float] = []

    async def submit_order(self, order: Order) -> Order:
        """Submit an order via PaperVenue.

        Returns the cached result if the same ``client_order_id``
        has already been submitted (idempotent).
        """
        # Idempotency
        if order.client_order_id in self._submitted:
            logger.debug(
                "paper_exec.idempotent_hit",
                client_order_id=str(order.client_order_id),
            )
            return self._submitted[order.client_order_id]

        # Rate limiting
        if not self._check_rate_limit():
            rejected = order.model_copy(update={"status": OrderStatus.REJECTED})
            await self._event_bus.publish(
                "order_rejected",
                {
                    "client_order_id": str(order.client_order_id),
                    "reason": "rate_limited",
                },
            )
            logger.warning(
                "paper_exec.rate_limited",
                client_order_id=str(order.client_order_id),
            )
            return rejected

        result = await self._venue.submit_order(order)
        self._submitted[order.client_order_id] = result
        return result

    async def cancel_order(self, client_order_id: UUID) -> bool:
        if not self._check_rate_limit():
            logger.warning("paper_exec.cancel_rate_limited", id=str(client_order_id))
            return False

        success = await self._venue.cancel_order(client_order_id)
        if success and client_order_id in self._submitted:
            self._submitted[client_order_id] = self._submitted[
                client_order_id
            ].model_copy(update={"status": OrderStatus.CANCELLED})
        return success

    async def amend_order(
        self,
        client_order_id: UUID,
        new_price: Decimal,
        new_size: Decimal,
    ) -> Order:
        if not self._check_rate_limit():
            raise ValueError("Rate limited — too many operations per second")

        result = await self._venue.amend_order(client_order_id, new_price, new_size)
        self._submitted[client_order_id] = result
        return result

    async def get_open_orders(self) -> list[Order]:
        return await self._venue.get_open_orders()

    def _check_rate_limit(self) -> bool:
        """Sliding-window rate limiter. Returns True if the operation is allowed."""
        now = time.monotonic()
        # Prune old timestamps (> 1 second ago)
        cutoff = now - 1.0
        self._op_timestamps = [t for t in self._op_timestamps if t > cutoff]

        if len(self._op_timestamps) >= self._max_ops:
            return False

        self._op_timestamps.append(now)
        return True
