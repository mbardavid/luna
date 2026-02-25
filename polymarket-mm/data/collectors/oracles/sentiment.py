"""CLOBSentimentCollector — volume/momentum signals from the CLOB itself.

Uses order-book volume and recent trade flow as a sentiment signal.
This is the fallback oracle for non-crypto markets (politics, sports, etc.)
where no external price feed is available.

NOTE: Stub implementation.  In production, this would consume EventBus
``trade`` and ``book`` events to build rolling volume and momentum stats.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from core.event_bus import EventBus

logger = structlog.get_logger("data.collectors.oracles.sentiment")


class CLOBSentimentCollector:
    """Collects volume and momentum signals from the CLOB itself.

    Computes a simple sentiment score based on:
    - Recent trade volume (1m / 5m ratio)
    - Directional flow (buy vs sell volume)
    - Price momentum (recent price changes)

    Parameters
    ----------
    event_bus:
        EventBus to subscribe to ``trade`` events and publish ``oracle_delta``.
    window_seconds:
        Rolling window size in seconds (default 300 = 5 min).
    """

    def __init__(
        self,
        event_bus: EventBus,
        window_seconds: int = 300,
    ) -> None:
        self._event_bus = event_bus
        self._window_seconds = window_seconds
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Rolling trade data per token_id
        self._trades: dict[str, deque[dict[str, Any]]] = {}

    async def start(self) -> None:
        """Start consuming trade events from the EventBus."""
        self._running = True
        self._task = asyncio.create_task(self._consume_trades())
        logger.info("sentiment_collector.started")

    async def stop(self) -> None:
        """Stop the collector."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("sentiment_collector.stopped")

    async def get_delta(self, token_id: str) -> float:
        """Compute a sentiment-based delta for the given token.

        Returns a float in [-1, 1]:
        - Positive: bullish sentiment (more buy volume)
        - Negative: bearish sentiment (more sell volume)
        - Zero: neutral or insufficient data

        NOTE: Stub — always returns 0.0.  In production, computes from
        the rolling trade window.
        """
        trades = self._trades.get(token_id, deque())
        if not trades:
            return 0.0

        # Stub: simple buy/sell ratio
        buy_volume = Decimal("0")
        sell_volume = Decimal("0")
        for t in trades:
            size = Decimal(str(t.get("size", "0")))
            if t.get("side") == "BUY":
                buy_volume += size
            else:
                sell_volume += size

        total = buy_volume + sell_volume
        if total == 0:
            return 0.0

        # Normalise to [-1, 1]
        imbalance = float((buy_volume - sell_volume) / total)
        return imbalance

    # ── Internal ─────────────────────────────────────────────────

    async def _consume_trades(self) -> None:
        """Subscribe to ``trade`` events and accumulate in rolling window.

        NOTE: Stub — sleeps indefinitely.  In production::

            async for event in self._event_bus.subscribe("trade"):
                token_id = event.payload.get("token_id", "")
                self._add_trade(token_id, event.payload)
        """
        try:
            await asyncio.sleep(3600)  # Stub: block
        except asyncio.CancelledError:
            pass

    def _add_trade(self, token_id: str, trade_data: dict[str, Any]) -> None:
        """Add a trade to the rolling window, pruning old entries."""
        if token_id not in self._trades:
            self._trades[token_id] = deque(maxlen=10000)

        self._trades[token_id].append({
            "price": trade_data.get("price"),
            "size": trade_data.get("size"),
            "side": trade_data.get("side"),
            "timestamp": datetime.now(timezone.utc),
        })

        # Prune entries outside the window
        self._prune(token_id)

    def _prune(self, token_id: str) -> None:
        """Remove trades older than the window."""
        cutoff = datetime.now(timezone.utc).timestamp() - self._window_seconds
        trades = self._trades.get(token_id, deque())
        while trades and trades[0].get("timestamp", datetime.min).timestamp() < cutoff:
            trades.popleft()
