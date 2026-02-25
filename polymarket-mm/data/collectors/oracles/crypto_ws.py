"""CryptoOracleWS — Binance WebSocket oracle for crypto markets.

Connects to Binance WS ``btcusdt@aggTrade`` stream to get real-time
BTC price data.  Computes ``oracle_delta`` — the divergence between
the external BTC price and the CLOB market price.

NOTE: This is a **stub/mock** implementation.  In production, replace
the ``_StubBinanceWS`` with a real ``websockets`` connection to
``wss://stream.binance.com:9443/ws/btcusdt@aggTrade``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog

from core.event_bus import EventBus

logger = structlog.get_logger("data.collectors.oracles.crypto_ws")


class CryptoOracleWS:
    """WebSocket oracle consuming Binance aggTrade for crypto markets.

    Publishes ``oracle_delta`` events to the EventBus whenever a new
    external price is received.

    Parameters
    ----------
    event_bus:
        EventBus to publish oracle_delta events.
    stream_url:
        Binance WS stream URL (default: btcusdt@aggTrade).
    """

    def __init__(
        self,
        event_bus: EventBus,
        stream_url: str = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade",
    ) -> None:
        self._event_bus = event_bus
        self._stream_url = stream_url
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_price: Decimal = Decimal("0")
        self._reference_prices: dict[str, Decimal] = {}

    async def start(self) -> None:
        """Start the Binance WS connection loop."""
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("crypto_oracle_ws.started", stream_url=self._stream_url)

    async def stop(self) -> None:
        """Stop the Binance WS connection."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("crypto_oracle_ws.stopped")

    async def get_delta(self, token_id: str) -> float:
        """Compute oracle delta for a given token.

        The delta represents how far the CLOB price diverges from the
        external reference.  Positive = CLOB is cheap relative to oracle.

        NOTE: Stub always returns 0.0.  In production, compare
        ``self._last_price`` with the CLOB mid price for the token.
        """
        # Stub: return 0 delta
        return 0.0

    def set_reference_price(self, token_id: str, clob_mid: Decimal) -> None:
        """Update the CLOB mid-price reference for delta computation."""
        self._reference_prices[token_id] = clob_mid

    @property
    def last_price(self) -> Decimal:
        """Last received external price."""
        return self._last_price

    # ── Internal ─────────────────────────────────────────────────

    async def _run(self) -> None:
        """Main loop: connect to Binance WS and read aggTrade events.

        STUB: Simulates by sleeping.  In production, use websockets:

            async with websockets.connect(self._stream_url) as ws:
                async for msg in ws:
                    data = json.loads(msg)
                    price = Decimal(data["p"])
                    self._last_price = price
                    await self._publish_delta(price)
        """
        while self._running:
            try:
                await asyncio.sleep(3600)  # Stub: sleep forever
            except asyncio.CancelledError:
                break

    async def _publish_delta(self, external_price: Decimal) -> None:
        """Publish oracle_delta event to EventBus."""
        for token_id, clob_mid in self._reference_prices.items():
            if clob_mid > 0:
                delta = float(external_price - clob_mid) / float(clob_mid)
            else:
                delta = 0.0

            await self._event_bus.publish(
                topic="oracle_delta",
                payload={
                    "source": "binance_btcusdt",
                    "token_id": token_id,
                    "external_price": str(external_price),
                    "clob_mid": str(clob_mid),
                    "delta": delta,
                },
            )
