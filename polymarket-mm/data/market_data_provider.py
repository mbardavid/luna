"""MarketDataProvider — ABC interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from models.market_state import MarketState


class MarketDataProvider(ABC):
    """Abstract base class for market data sources.

    Implementations:
    - ``LiveMarketDataProvider`` — real Polymarket CLOB (WS + REST)
    - ``PaperMarketDataProvider`` — simulated venue for backtesting

    All prices MUST be ``Decimal``.  Implementations MUST be async-safe.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish connections (WS, REST sessions, etc.)."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close all connections and release resources."""

    @abstractmethod
    async def get_active_markets(self) -> list[MarketState]:
        """Fetch the current list of active markets with metadata.

        Returns a list of ``MarketState`` objects populated with at least:
        - market_id, condition_id, token_id_yes, token_id_no
        - tick_size, min_order_size, neg_risk, market_type
        """

    @abstractmethod
    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Fetch a full orderbook snapshot for a single token.

        Returns a dict with at least::

            {
                "bids": [{"price": Decimal, "size": Decimal}, ...],
                "asks": [{"price": Decimal, "size": Decimal}, ...],
                "timestamp": datetime,
                "hash": str | None,
            }
        """

    @abstractmethod
    async def subscribe_book_updates(
        self, token_ids: list[str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream incremental book updates for the given token_ids.

        Each yielded dict represents a single update event with at least::

            {
                "event_type": str,   # "book" | "trade" | "tick_size_change" | "price_change"
                "token_id": str,
                "data": dict,
                "timestamp": datetime,
            }
        """
        # Make this an async generator (yield is required for ABC async iterators)
        yield {}  # pragma: no cover
        raise NotImplementedError  # pragma: no cover
