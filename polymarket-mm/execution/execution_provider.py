"""ExecutionProvider — ABC interface for order execution backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from uuid import UUID

from models.order import Order


class ExecutionProvider(ABC):
    """Abstract base class for execution backends.

    Implementations:
    - ``PaperExecution`` — simulated fills for paper trading
    - ``LiveExecution`` — real Polymarket CLOB via REST/WS

    All prices MUST be ``Decimal``.  Implementations MUST be async-safe.
    """

    @abstractmethod
    async def submit_order(self, order: Order) -> Order:
        """Submit an order to the venue.

        Returns the order with updated status (OPEN, REJECTED, etc.).
        Must be idempotent on ``client_order_id``: re-submitting the same
        id returns the existing order without side effects.
        """

    @abstractmethod
    async def cancel_order(self, client_order_id: UUID) -> bool:
        """Cancel an open order by its client_order_id.

        Returns True if the order was successfully cancelled, False if
        the order was not found or already in a terminal state.
        """

    @abstractmethod
    async def amend_order(
        self,
        client_order_id: UUID,
        new_price: Decimal,
        new_size: Decimal,
    ) -> Order:
        """Amend an open order's price and/or size.

        Returns the updated order.  Raises ``ValueError`` if the order
        is not found or in a non-amendable state.
        """

    @abstractmethod
    async def get_open_orders(self) -> list[Order]:
        """Return all currently open (non-terminal) orders."""
