"""CLOBWebSocketClient — Real WebSocket connection to Polymarket CLOB.

Connects to wss://ws-subscriptions-clob.polymarket.com/ws/market
for real-time orderbook updates.

Handles:
- Connection lifecycle with automatic reconnection (exponential backoff)
- Event parsing: book snapshots and updates
- Heartbeat / ping loop
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
import websockets
import websockets.exceptions

from core.event_bus import EventBus

logger = structlog.get_logger("data.ws_client")

# Polymarket CLOB WS event types we care about
_KNOWN_EVENT_TYPES = frozenset({"book", "tick_size_change", "trade", "price_change", "last_trade_price"})

# Default WS URL
_DEFAULT_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class CLOBWebSocketClient:
    """WebSocket client for the Polymarket CLOB streaming API.

    Subscribes to real-time orderbook updates for specified token IDs.

    Parameters
    ----------
    ws_url:
        WebSocket endpoint.
    event_bus:
        ``EventBus`` instance to publish parsed events into.
    token_ids:
        List of token IDs (asset IDs) to subscribe to on connect.
    ping_interval:
        Seconds between heartbeat pings (default 25).
    max_reconnect_delay:
        Maximum backoff delay in seconds (default 120).
    """

    def __init__(
        self,
        ws_url: str = _DEFAULT_WS_URL,
        event_bus: EventBus | None = None,
        token_ids: list[str] | None = None,
        ping_interval: float = 25.0,
        max_reconnect_delay: float = 120.0,
    ) -> None:
        self._ws_url = ws_url
        self._event_bus = event_bus
        self._token_ids = list(token_ids) if token_ids else []
        self._ping_interval = ping_interval
        self._max_reconnect_delay = max_reconnect_delay

        self._ws: Any = None
        self._running = False
        self._reconnect_attempt = 0
        self._tasks: list[asyncio.Task[None]] = []
        self._last_message_time: float = 0.0
        self._messages_received: int = 0

    @property
    def is_connected(self) -> bool:
        """Return True if the WebSocket is currently connected."""
        return self._ws is not None and self._running

    @property
    def messages_received(self) -> int:
        """Total messages received since start."""
        return self._messages_received

    @property
    def subscribed_tokens(self) -> list[str]:
        """Return list of subscribed token IDs."""
        return list(self._token_ids)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WS client: connect, subscribe, and begin the read loop."""
        self._running = True
        self._reconnect_attempt = 0
        self._messages_received = 0
        logger.info(
            "ws_client.starting",
            url=self._ws_url,
            token_count=len(self._token_ids),
        )
        # Run in background task so start() returns immediately
        task = asyncio.create_task(self._connect_and_run())
        self._tasks.append(task)

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
        logger.info(
            "ws_client.stopped",
            messages_received=self._messages_received,
        )

    def add_token(self, token_id: str) -> None:
        """Add a token ID to subscribe on next reconnect.

        If already connected, you need to call ``resubscribe()`` to
        update the subscription on the live connection.
        """
        if token_id not in self._token_ids:
            self._token_ids.append(token_id)

    def remove_token(self, token_id: str) -> None:
        """Remove a token ID from the subscription list."""
        if token_id in self._token_ids:
            self._token_ids.remove(token_id)

    async def resubscribe(self) -> None:
        """Re-send the subscription message with current token list.

        Call this after adding/removing tokens while connected.
        """
        if self._ws is not None:
            await self._send_subscribe()

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
                    error=str(exc)[:200],
                )
                if self._ws is not None:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                    self._ws = None
                await asyncio.sleep(delay)

    async def _connect(self) -> None:
        """Establish the real WS connection and send subscription message."""
        logger.info(
            "ws_client.connecting",
            url=self._ws_url,
            token_count=len(self._token_ids),
        )

        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=None,  # We manage our own pings
            ping_timeout=None,
            close_timeout=5,
            max_size=10 * 1024 * 1024,  # 10MB max message
        )

        # Send subscription
        await self._send_subscribe()

        logger.info("ws_client.connected")

    async def _send_subscribe(self) -> None:
        """Send subscription message for current token list."""
        if not self._ws or not self._token_ids:
            return

        subscribe_msg = json.dumps({
            "assets_ids": self._token_ids,
            "type": "market",
        })
        await self._ws.send(subscribe_msg)
        logger.info(
            "ws_client.subscribed",
            token_count=len(self._token_ids),
        )

    async def _run(self) -> None:
        """Main read loop: receive messages, parse, and publish to EventBus."""
        ping_task = asyncio.create_task(self._ping_loop())
        self._tasks.append(ping_task)

        try:
            async for raw_message in self._ws:
                if not self._running:
                    break
                self._last_message_time = asyncio.get_event_loop().time()
                self._messages_received += 1
                await self._handle_message(raw_message)
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning(
                "ws_client.connection_closed",
                code=exc.code,
                reason=str(exc.reason)[:100],
            )
            raise
        finally:
            ping_task.cancel()
            self._tasks = [t for t in self._tasks if t is not ping_task]

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep the WS connection alive."""
        while self._running:
            try:
                await asyncio.sleep(self._ping_interval)
                if self._ws is not None:
                    pong = await self._ws.ping()
                    await asyncio.wait_for(pong, timeout=10)
                    logger.debug("ws_client.ping_ok")
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                logger.warning("ws_client.ping_timeout")
                # Force reconnect by breaking the connection
                if self._ws:
                    await self._ws.close()
                break
            except Exception as exc:
                logger.warning("ws_client.ping_failed", error=str(exc)[:100])

    # ── Message handling ─────────────────────────────────────────

    async def _handle_message(self, raw: str) -> None:
        """Parse a raw WS message and publish to the EventBus.

        Polymarket WS sends messages as JSON arrays of event objects.
        """
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("ws_client.invalid_json", raw=raw[:200])
            return

        # Polymarket sends arrays of events
        if isinstance(data, list):
            for item in data:
                await self._process_event(item)
        elif isinstance(data, dict):
            await self._process_event(data)
        else:
            logger.debug("ws_client.unexpected_format", type=type(data).__name__)

    async def _process_event(self, data: dict[str, Any]) -> None:
        """Process a single event dict and publish to EventBus."""
        event_type = data.get("event_type") or data.get("type", "unknown")

        if event_type not in _KNOWN_EVENT_TYPES:
            logger.debug("ws_client.ignored_event", event_type=event_type)
            return

        # Normalise the payload
        payload = self._normalize_payload(event_type, data)

        if self._event_bus is not None:
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
            payload["market"] = data.get("market", "")
            payload["hash"] = data.get("hash", "")
            payload["ws_timestamp"] = data.get("timestamp", "")
            payload["tick_size"] = data.get("tick_size", "")
            payload["last_trade_price"] = data.get("last_trade_price", "")

            # Parse bids and asks
            raw_bids = data.get("bids", [])
            raw_asks = data.get("asks", [])

            payload["bids"] = [
                {"price": Decimal(str(lvl.get("price", "0"))),
                 "size": Decimal(str(lvl.get("size", "0")))}
                for lvl in raw_bids
                if isinstance(lvl, dict)
            ]
            payload["asks"] = [
                {"price": Decimal(str(lvl.get("price", "0"))),
                 "size": Decimal(str(lvl.get("size", "0")))}
                for lvl in raw_asks
                if isinstance(lvl, dict)
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

        elif event_type == "last_trade_price":
            payload["token_id"] = data.get("asset_id", "")
            payload["price"] = Decimal(str(data.get("price", "0")))

        return payload
