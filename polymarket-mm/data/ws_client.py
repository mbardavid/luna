"""CLOBWebSocketClient — WebSocket connection to Polymarket CLOB.

Handles:
- Connection lifecycle with automatic reconnection (exponential backoff)
- Event parsing: book, tick_size_change, trade, price_change
- Heartbeat / ping loop (5 s interval)
- Publishing parsed events to the EventBus
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog

from core.event_bus import EventBus

logger = structlog.get_logger("data.ws_client")

# Polymarket CLOB WS event types we care about
_KNOWN_EVENT_TYPES = frozenset({"book", "tick_size_change", "trade", "price_change"})


class CLOBWebSocketClient:
    """WebSocket client for the Polymarket CLOB streaming API.

    Parameters
    ----------
    ws_url:
        WebSocket endpoint (e.g. ``wss://ws-subscriptions-clob.polymarket.com/ws/market``).
    event_bus:
        ``EventBus`` instance to publish parsed events into.
    token_ids:
        List of token IDs to subscribe to on connect.
    ping_interval:
        Seconds between heartbeat pings (default 5).
    max_reconnect_delay:
        Maximum backoff delay in seconds (default 120).
    """

    def __init__(
        self,
        ws_url: str,
        event_bus: EventBus,
        token_ids: list[str] | None = None,
        ping_interval: float = 5.0,
        max_reconnect_delay: float = 120.0,
    ) -> None:
        self._ws_url = ws_url
        self._event_bus = event_bus
        self._token_ids = token_ids or []
        self._ping_interval = ping_interval
        self._max_reconnect_delay = max_reconnect_delay

        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._running = False
        self._reconnect_attempt = 0
        self._tasks: list[asyncio.Task[None]] = []

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WS client: connect, subscribe, and begin the read loop."""
        self._running = True
        self._reconnect_attempt = 0
        await self._connect_and_run()

    async def stop(self) -> None:
        """Gracefully stop the WS client."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        logger.info("ws_client.stopped")

    # ── Internal: connect + run ──────────────────────────────────

    async def _connect_and_run(self) -> None:
        """Connect with exponential backoff and run the read loop."""
        while self._running:
            try:
                await self._connect()
                self._reconnect_attempt = 0
                await self._run()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._reconnect_attempt += 1
                delay = min(
                    2 ** self._reconnect_attempt,
                    self._max_reconnect_delay,
                )
                logger.warning(
                    "ws_client.reconnecting",
                    attempt=self._reconnect_attempt,
                    delay=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

    async def _connect(self) -> None:
        """Establish the WS connection and send subscription message.

        NOTE: This is a stub — in production, replace with real
        ``websockets.connect()`` call.
        """
        logger.info(
            "ws_client.connecting",
            url=self._ws_url,
            token_ids=self._token_ids,
        )
        # STUB: In production, use:
        #   import websockets
        #   self._ws = await websockets.connect(self._ws_url)
        #   subscribe_msg = json.dumps({
        #       "type": "subscribe",
        #       "markets": self._token_ids,
        #       "channels": ["book", "trades", "ticker"],
        #   })
        #   await self._ws.send(subscribe_msg)
        self._ws = _StubWebSocket()
        logger.info("ws_client.connected")

    async def _run(self) -> None:
        """Main read loop: receive messages, parse, and publish to EventBus."""
        ping_task = asyncio.create_task(self._ping_loop())
        self._tasks.append(ping_task)

        try:
            async for raw_message in self._ws:
                if not self._running:
                    break
                await self._handle_message(raw_message)
        finally:
            ping_task.cancel()
            self._tasks = [t for t in self._tasks if t is not ping_task]

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep the WS connection alive."""
        while self._running:
            try:
                await asyncio.sleep(self._ping_interval)
                if self._ws is not None:
                    # STUB: In production → await self._ws.ping()
                    logger.debug("ws_client.ping_sent")
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("ws_client.ping_failed", error=str(exc))

    # ── Message handling ─────────────────────────────────────────

    async def _handle_message(self, raw: str) -> None:
        """Parse a raw WS message and publish to the EventBus."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ws_client.invalid_json", raw=raw[:200])
            return

        event_type = data.get("event_type") or data.get("type", "unknown")

        if event_type not in _KNOWN_EVENT_TYPES:
            logger.debug("ws_client.ignored_event", event_type=event_type)
            return

        # Normalise numeric fields to Decimal strings
        payload = self._normalize_payload(event_type, data)

        trace_id = str(uuid4())
        await self._event_bus.publish(
            topic=event_type,
            payload=payload,
            trace_id=trace_id,
        )

    @staticmethod
    def _normalize_payload(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        """Normalise raw WS data into a clean payload dict.

        Converts price/size strings to ``Decimal`` where applicable.
        """
        payload: dict[str, Any] = {
            "event_type": event_type,
            "raw": data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if event_type == "book":
            payload["token_id"] = data.get("asset_id", "")
            payload["bids"] = [
                {"price": Decimal(str(lvl[0])), "size": Decimal(str(lvl[1]))}
                for lvl in data.get("bids", [])
            ]
            payload["asks"] = [
                {"price": Decimal(str(lvl[0])), "size": Decimal(str(lvl[1]))}
                for lvl in data.get("asks", [])
            ]

        elif event_type == "trade":
            payload["token_id"] = data.get("asset_id", "")
            payload["price"] = Decimal(str(data.get("price", "0")))
            payload["size"] = Decimal(str(data.get("size", "0")))
            payload["side"] = data.get("side", "")

        elif event_type == "tick_size_change":
            payload["token_id"] = data.get("asset_id", "")
            payload["old_tick_size"] = Decimal(str(data.get("old_tick_size", "0")))
            payload["new_tick_size"] = Decimal(str(data.get("new_tick_size", "0")))

        elif event_type == "price_change":
            payload["token_id"] = data.get("asset_id", "")
            payload["price"] = Decimal(str(data.get("price", "0")))
            payload["change_pct"] = data.get("change_pct")

        return payload


# ── Stub for development ────────────────────────────────────────

class _StubWebSocket:
    """Minimal async-iterator stub simulating a WS connection.

    In production, replaced by a real ``websockets`` connection.
    """

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        # Block indefinitely — no real messages in stub mode
        await asyncio.sleep(3600)
        raise StopAsyncIteration

    async def close(self) -> None:
        pass

    async def ping(self) -> None:
        pass
