"""ReplayEngine — replay historical market data through the paper venue pipeline.

Currently a stub with the interface ready for future implementation.
Will support replaying from JSON/CSV files through the same EventBus pipeline
used by PaperVenue, enabling accurate backtesting with real market data.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, AsyncIterator

import structlog

from core.event_bus import EventBus
from data.market_data_provider import MarketDataProvider
from models.market_state import MarketState

logger = structlog.get_logger("paper.replay_engine")


@dataclass
class ReplayConfig:
    """Configuration for the replay engine."""

    data_path: Path = Path("data/replay")
    speed_multiplier: float = 1.0  # 1x = real time, 10x = 10x faster
    loop: bool = False  # restart from beginning when done


class ReplayEngine(MarketDataProvider):
    """Replays historical CLOB data through the EventBus pipeline.

    Reads timestamped events from JSON/CSV files and publishes them
    to the EventBus at the original cadence (adjusted by ``speed_multiplier``).

    This is a **stub** — the interface is defined but the implementation
    will be completed when historical data collection is in place.

    Usage::

        engine = ReplayEngine(event_bus, config=ReplayConfig(
            data_path=Path("data/replay/2024-01-15"),
            speed_multiplier=10.0,
        ))
        await engine.connect()
        markets = await engine.get_active_markets()
    """

    def __init__(
        self,
        event_bus: EventBus,
        config: ReplayConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._config = config or ReplayConfig()
        self._connected = False
        self._markets: list[MarketState] = []

    async def connect(self) -> None:
        """Load replay data files and prepare for replay.

        Stub: logs a warning and sets connected state.
        """
        self._connected = True
        logger.warning(
            "replay_engine.stub",
            msg="ReplayEngine is a stub — no data will be replayed",
            data_path=str(self._config.data_path),
        )

    async def disconnect(self) -> None:
        self._connected = False
        logger.info("replay_engine.disconnected")

    async def get_active_markets(self) -> list[MarketState]:
        """Return markets discovered in the replay data.

        Stub: returns an empty list.
        """
        return list(self._markets)

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Return the orderbook snapshot at the current replay position.

        Stub: returns an empty book.
        """
        return {
            "bids": [],
            "asks": [],
            "timestamp": None,
            "hash": None,
        }

    async def subscribe_book_updates(
        self, token_ids: list[str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream replayed book updates.

        Stub: yields nothing and returns immediately.
        """
        return
        yield  # Make this a valid async generator  # noqa: E711
