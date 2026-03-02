"""runner.venue_adapter — Abstract interface for venue operations.

Defines the VenueAdapter ABC that both PaperVenueAdapter and
LiveVenueAdapter implement, enabling the unified pipeline to
work with either simulated or real order execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from models.order import Order


class VenueAdapter(ABC):
    """Abstract venue adapter for the unified trading pipeline.

    Provides a uniform interface for:
    - Order submission (paper = PaperVenue, live = LiveExecution + CLOB REST)
    - Order cancellation
    - Open order queries
    - Fill processing

    Implementations handle mode-specific details (e.g., fill dedup in live,
    EventBus draining in paper).
    """

    @property
    @abstractmethod
    def mode(self) -> str:
        """Return 'paper' or 'live'."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Connect to the venue (initialize resources)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the venue (cleanup resources)."""
        ...

    @abstractmethod
    async def submit_order(self, order: Order) -> Order:
        """Submit an order. Returns the order with updated status/filled_qty."""
        ...

    @abstractmethod
    async def cancel_order(self, client_order_id: UUID) -> bool:
        """Cancel a specific order. Returns True on success."""
        ...

    @abstractmethod
    async def cancel_all_orders(self) -> None:
        """Cancel all open orders across all markets."""
        ...

    @abstractmethod
    async def cancel_market_orders(self, market_id: str) -> None:
        """Cancel all open orders for a specific market."""
        ...

    @abstractmethod
    async def get_open_orders(self) -> list[Order]:
        """Return all currently open orders."""
        ...

    @abstractmethod
    async def process_fills(self) -> list[dict]:
        """Process and return new fills since last call.

        Each fill dict must contain:
        - market_id: str
        - token_id: str
        - side: str ('BUY' or 'SELL')
        - fill_price: Decimal
        - fill_qty: Decimal
        - fee: Decimal
        - fill_id: str (unique, for dedup)

        Paper mode: drains fill events from EventBus.
        Live mode: polls REST API with trade dedup by fill_id.
        """
        ...
