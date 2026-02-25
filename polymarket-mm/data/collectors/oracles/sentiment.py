"""CLOBSentimentCollector — volume/momentum signals from the CLOB itself.

Uses order-book volume and recent trade flow as a sentiment signal.
This is the fallback oracle for non-crypto markets (politics, sports, etc.)
where no external price feed is available.

Consumes ``trade`` and ``fill`` events from EventBus to build rolling
volume and momentum statistics in real time.
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
        EventBus to subscribe to ``trade`` and ``fill`` events.
    window_seconds:
        Rolling window size in seconds (default 300 = 5 min).
    momentum_window:
        Number of recent price ticks for momentum computation.
    """

    def __init__(
        self,
        event_bus: EventBus,
        window_seconds: int = 300,
        momentum_window: int = 20,
    ) -> None:
        self._event_bus = event_bus
        self._window_seconds = window_seconds
        self._momentum_window = momentum_window
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

        # Rolling trade data per token_id
        self._trades: dict[str, deque[dict[str, Any]]] = {}
        # Rolling price ticks per token_id for momentum
        self._price_ticks: dict[str, deque[float]] = {}

    async def start(self) -> None:
        """Start consuming trade and fill events from the EventBus."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._consume_events("trade")),
            asyncio.create_task(self._consume_events("fill")),
        ]
        logger.info("sentiment_collector.started")

    async def stop(self) -> None:
        """Stop the collector."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks = []
        logger.info("sentiment_collector.stopped")

    async def get_delta(self, token_id: str) -> float:
        """Compute a sentiment-based delta for the given token.

        Returns a float in [-1, 1]:
        - Positive: bullish sentiment (more buy volume)
        - Negative: bearish sentiment (more sell volume)
        - Zero: neutral or insufficient data
        """
        self._prune(token_id)
        trades = self._trades.get(token_id, deque())
        if not trades:
            return 0.0

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
        return max(-1.0, min(1.0, imbalance))

    def get_volume_ratio(self, token_id: str) -> float:
        """Return buy/sell volume ratio for the rolling window.

        Returns > 1.0 for buy-heavy, < 1.0 for sell-heavy, 1.0 for balanced.
        Returns 1.0 if insufficient data.
        """
        self._prune(token_id)
        trades = self._trades.get(token_id, deque())
        if not trades:
            return 1.0

        buy_volume = 0.0
        sell_volume = 0.0
        for t in trades:
            size = float(t.get("size", 0))
            if t.get("side") == "BUY":
                buy_volume += size
            else:
                sell_volume += size

        if sell_volume == 0:
            return 2.0 if buy_volume > 0 else 1.0
        return buy_volume / sell_volume

    def get_momentum(self, token_id: str) -> float:
        """Compute micro-momentum from recent price changes.

        Returns the average price change over the momentum window.
        Positive = upward trend, negative = downward trend.
        """
        ticks = self._price_ticks.get(token_id, deque())
        if len(ticks) < 2:
            return 0.0

        recent = list(ticks)[-self._momentum_window:]
        if len(recent) < 2:
            return 0.0

        changes = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
        return sum(changes) / len(changes)

    def get_trade_count(self, token_id: str) -> int:
        """Return number of trades in the rolling window."""
        self._prune(token_id)
        return len(self._trades.get(token_id, deque()))

    # ── Internal ─────────────────────────────────────────────────

    async def _consume_events(self, topic: str) -> None:
        """Subscribe to events and accumulate in rolling window."""
        try:
            async for event in self._event_bus.subscribe(topic):
                if not self._running:
                    break
                token_id = event.payload.get("token_id", "")
                if not token_id:
                    continue
                self._add_trade(token_id, event.payload)
        except asyncio.CancelledError:
            pass

    def _add_trade(self, token_id: str, trade_data: dict[str, Any]) -> None:
        """Add a trade to the rolling window, pruning old entries."""
        if token_id not in self._trades:
            self._trades[token_id] = deque(maxlen=10000)
        if token_id not in self._price_ticks:
            self._price_ticks[token_id] = deque(maxlen=self._momentum_window * 2)

        now = datetime.now(timezone.utc)

        self._trades[token_id].append({
            "price": trade_data.get("price", trade_data.get("fill_price")),
            "size": trade_data.get("size", trade_data.get("fill_qty", "0")),
            "side": trade_data.get("side", "BUY"),
            "timestamp": now,
        })

        # Record price tick for momentum
        price_raw = trade_data.get("price", trade_data.get("fill_price"))
        if price_raw is not None:
            try:
                self._price_ticks[token_id].append(float(price_raw))
            except (ValueError, TypeError):
                pass

        # Prune entries outside the window
        self._prune(token_id)

    def _prune(self, token_id: str) -> None:
        """Remove trades older than the window."""
        trades = self._trades.get(token_id)
        if not trades:
            return
        cutoff = datetime.now(timezone.utc).timestamp() - self._window_seconds
        while trades and trades[0].get("timestamp", datetime.min.replace(tzinfo=timezone.utc)).timestamp() < cutoff:
            trades.popleft()

    def inject_trade(self, token_id: str, trade_data: dict[str, Any]) -> None:
        """Directly inject a trade event (for testing / external callers).

        Parameters
        ----------
        token_id:
            The token ID for the trade.
        trade_data:
            Dict with at least ``price``, ``size``, ``side`` keys.
        """
        self._add_trade(token_id, trade_data)
