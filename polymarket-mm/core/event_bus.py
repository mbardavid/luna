"""EventBus — asyncio.Queue backbone with trace_id and fanout to multiple subscribers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from uuid import uuid4

import structlog

logger = structlog.get_logger("core.event_bus")


@dataclass(frozen=True, slots=True)
class Event:
    """Immutable event flowing through the EventBus."""

    topic: str
    payload: dict[str, Any]
    trace_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class EventBus:
    """Fan-out pub/sub event bus backed by asyncio.Queue.

    Each ``subscribe(topic)`` call creates an independent queue so that
    multiple consumers can process events at their own pace.  A single
    ``publish()`` copies the event into every subscriber queue that matches
    the topic.

    Usage::

        bus = EventBus()
        async for event in bus.subscribe("book"):
            process(event)

        await bus.publish("book", {"bid": "0.45"})
    """

    def __init__(self, maxsize: int = 4096) -> None:
        self._maxsize = maxsize
        # topic -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}
        self._lock = asyncio.Lock()
        self._stats_published: int = 0
        self._stats_dropped: int = 0

    # ── Publish ──────────────────────────────────────────────────

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        trace_id: str | None = None,
    ) -> None:
        """Publish an event to all subscribers of *topic*.

        Parameters
        ----------
        topic:
            Event topic string (e.g. ``"book"``, ``"trade"``).
        payload:
            Arbitrary dict payload.
        trace_id:
            Optional correlation id; auto-generated UUID4 if omitted.
        """
        if trace_id is None:
            trace_id = str(uuid4())

        event = Event(topic=topic, payload=payload, trace_id=trace_id)

        queues = self._subscribers.get(topic, [])
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._stats_dropped += 1
                logger.warning(
                    "event_bus.queue_full",
                    topic=topic,
                    trace_id=trace_id,
                    queue_size=q.qsize(),
                )

        self._stats_published += 1

    # ── Subscribe ────────────────────────────────────────────────

    async def subscribe(self, topic: str) -> AsyncIterator[Event]:
        """Subscribe to *topic* and yield events as they arrive.

        Each call creates an independent queue.  The iterator runs
        indefinitely; cancel the consuming task to unsubscribe.
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)

        async with self._lock:
            self._subscribers.setdefault(topic, []).append(queue)

        try:
            while True:
                event = await queue.get()
                yield event
        finally:
            # Cleanup on consumer cancellation
            async with self._lock:
                subs = self._subscribers.get(topic, [])
                if queue in subs:
                    subs.remove(queue)
                if not subs:
                    self._subscribers.pop(topic, None)

    # ── Introspection ────────────────────────────────────────────

    @property
    def topics(self) -> list[str]:
        """Return list of topics with active subscribers."""
        return list(self._subscribers.keys())

    def subscriber_count(self, topic: str) -> int:
        """Return number of active subscribers for *topic*."""
        return len(self._subscribers.get(topic, []))

    @property
    def stats(self) -> dict[str, int]:
        """Return basic stats: published and dropped counts."""
        return {
            "published": self._stats_published,
            "dropped": self._stats_dropped,
        }
