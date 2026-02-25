"""Tests for core.event_bus — publish, subscribe, fanout, edge cases."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from core.event_bus import Event, EventBus


# ──────────────────────────────────────────────
# Basic publish / subscribe
# ──────────────────────────────────────────────


class TestEventBusBasic:
    """Basic publish/subscribe behaviour."""

    @pytest.mark.asyncio
    async def test_single_subscriber_receives_event(self):
        bus = EventBus()
        received: list[Event] = []

        async def consumer():
            async for event in bus.subscribe("book"):
                received.append(event)
                break  # stop after one

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)  # let subscriber register

        await bus.publish("book", {"bid": "0.45"})
        await asyncio.sleep(0.01)

        assert len(received) == 1
        assert received[0].topic == "book"
        assert received[0].payload["bid"] == "0.45"
        assert received[0].trace_id  # auto-generated

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_explicit_trace_id(self):
        bus = EventBus()
        tid = str(uuid4())
        received: list[Event] = []

        async def consumer():
            async for event in bus.subscribe("trade"):
                received.append(event)
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        await bus.publish("trade", {"price": "100"}, trace_id=tid)
        await asyncio.sleep(0.01)

        assert received[0].trace_id == tid

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_auto_generated_trace_id(self):
        bus = EventBus()
        received: list[Event] = []

        async def consumer():
            async for event in bus.subscribe("test"):
                received.append(event)
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        await bus.publish("test", {"x": 1})
        await asyncio.sleep(0.01)

        assert received[0].trace_id  # UUID string, not empty
        assert len(received[0].trace_id) > 10

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ──────────────────────────────────────────────
# Fanout
# ──────────────────────────────────────────────


class TestEventBusFanout:
    """Fanout: multiple subscribers on the same topic."""

    @pytest.mark.asyncio
    async def test_fanout_to_two_subscribers(self):
        bus = EventBus()
        received_a: list[Event] = []
        received_b: list[Event] = []

        async def consumer_a():
            async for event in bus.subscribe("book"):
                received_a.append(event)
                break

        async def consumer_b():
            async for event in bus.subscribe("book"):
                received_b.append(event)
                break

        task_a = asyncio.create_task(consumer_a())
        task_b = asyncio.create_task(consumer_b())
        await asyncio.sleep(0.01)

        await bus.publish("book", {"ask": "0.55"})
        await asyncio.sleep(0.01)

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0].payload == received_b[0].payload
        assert received_a[0].trace_id == received_b[0].trace_id

        task_a.cancel()
        task_b.cancel()
        for t in (task_a, task_b):
            try:
                await t
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_fanout_to_three_subscribers(self):
        bus = EventBus()
        results: list[list[Event]] = [[], [], []]

        async def consumer(idx: int):
            async for event in bus.subscribe("trade"):
                results[idx].append(event)
                break

        tasks = [asyncio.create_task(consumer(i)) for i in range(3)]
        await asyncio.sleep(0.01)

        await bus.publish("trade", {"size": "10"})
        await asyncio.sleep(0.01)

        for r in results:
            assert len(r) == 1
            assert r[0].payload["size"] == "10"

        for t in tasks:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


# ──────────────────────────────────────────────
# Topic isolation
# ──────────────────────────────────────────────


class TestEventBusTopicIsolation:
    """Events are only delivered to matching topic subscribers."""

    @pytest.mark.asyncio
    async def test_different_topics_isolated(self):
        bus = EventBus()
        book_events: list[Event] = []
        trade_events: list[Event] = []

        async def book_consumer():
            async for event in bus.subscribe("book"):
                book_events.append(event)
                if len(book_events) >= 1:
                    break

        async def trade_consumer():
            async for event in bus.subscribe("trade"):
                trade_events.append(event)
                if len(trade_events) >= 1:
                    break

        t1 = asyncio.create_task(book_consumer())
        t2 = asyncio.create_task(trade_consumer())
        await asyncio.sleep(0.01)

        await bus.publish("book", {"type": "book_data"})
        await bus.publish("trade", {"type": "trade_data"})
        await asyncio.sleep(0.01)

        assert len(book_events) == 1
        assert book_events[0].payload["type"] == "book_data"
        assert len(trade_events) == 1
        assert trade_events[0].payload["type"] == "trade_data"

        t1.cancel()
        t2.cancel()
        for t in (t1, t2):
            try:
                await t
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_publish_to_empty_topic_no_error(self):
        bus = EventBus()
        # Should not raise even though nobody is subscribed
        await bus.publish("nonexistent", {"data": 1})
        assert bus.stats["published"] == 1


# ──────────────────────────────────────────────
# Subscriber cleanup
# ──────────────────────────────────────────────


class TestEventBusCleanup:
    """Subscriber cleanup on cancellation."""

    @pytest.mark.asyncio
    async def test_subscriber_removed_on_cancel(self):
        bus = EventBus()

        async def consumer():
            async for _ in bus.subscribe("book"):
                pass

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        assert bus.subscriber_count("book") == 1

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        await asyncio.sleep(0.01)
        assert bus.subscriber_count("book") == 0
        assert "book" not in bus.topics

    @pytest.mark.asyncio
    async def test_multiple_events_to_single_subscriber(self):
        bus = EventBus()
        received: list[Event] = []

        async def consumer():
            async for event in bus.subscribe("tick"):
                received.append(event)
                if len(received) >= 3:
                    break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        for i in range(3):
            await bus.publish("tick", {"seq": i})

        await asyncio.sleep(0.05)

        assert len(received) == 3
        assert [e.payload["seq"] for e in received] == [0, 1, 2]

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ──────────────────────────────────────────────
# Queue full / backpressure
# ──────────────────────────────────────────────


class TestEventBusBackpressure:
    """Queue full handling."""

    @pytest.mark.asyncio
    async def test_queue_full_drops_event(self):
        bus = EventBus(maxsize=2)
        received: list[Event] = []

        async def slow_consumer():
            async for event in bus.subscribe("data"):
                received.append(event)
                await asyncio.sleep(1)  # Deliberately slow

        task = asyncio.create_task(slow_consumer())
        await asyncio.sleep(0.01)

        # Publish more than queue can hold
        for i in range(5):
            await bus.publish("data", {"seq": i})

        await asyncio.sleep(0.05)

        # At least some should have been dropped
        assert bus.stats["dropped"] > 0

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ──────────────────────────────────────────────
# Stats and introspection
# ──────────────────────────────────────────────


class TestEventBusIntrospection:
    """Stats and topic introspection."""

    @pytest.mark.asyncio
    async def test_stats_counter(self):
        bus = EventBus()
        await bus.publish("a", {"x": 1})
        await bus.publish("b", {"y": 2})
        assert bus.stats["published"] == 2
        assert bus.stats["dropped"] == 0

    @pytest.mark.asyncio
    async def test_topics_list(self):
        bus = EventBus()

        async def consumer():
            async for _ in bus.subscribe("alpha"):
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        assert "alpha" in bus.topics

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_event_has_timestamp(self):
        bus = EventBus()
        received: list[Event] = []

        async def consumer():
            async for event in bus.subscribe("ts_test"):
                received.append(event)
                break

        task = asyncio.create_task(consumer())
        await asyncio.sleep(0.01)

        await bus.publish("ts_test", {})
        await asyncio.sleep(0.01)

        assert received[0].timestamp is not None

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
