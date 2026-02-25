"""Tests for execution.reconciler — reconciliation with mocks."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from core.event_bus import EventBus
from execution.execution_provider import ExecutionProvider
from execution.order_manager import OrderManager
from execution.reconciler import Mismatch, Reconciler
from models.order import Order, OrderStatus, Side


# ── Helpers ──────────────────────────────────────────────────────────


def _make_order(
    *,
    client_order_id=None,
    market_id: str = "test-market",
    token_id: str = "test-token",
    side: Side = Side.BUY,
    price: Decimal = Decimal("0.50"),
    size: Decimal = Decimal("100"),
    filled_qty: Decimal = Decimal("0"),
    status: OrderStatus = OrderStatus.OPEN,
) -> Order:
    return Order(
        client_order_id=client_order_id or uuid4(),
        market_id=market_id,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
        filled_qty=filled_qty,
        status=status,
    )


class MockProvider(ExecutionProvider):
    """Mock execution provider for testing."""

    def __init__(self, open_orders: list[Order] | None = None) -> None:
        self._open_orders = open_orders or []

    async def submit_order(self, order: Order) -> Order:
        return order.model_copy(update={"status": OrderStatus.OPEN})

    async def cancel_order(self, client_order_id) -> bool:
        return True

    async def amend_order(self, client_order_id, new_price, new_size) -> Order:
        raise NotImplementedError

    async def get_open_orders(self) -> list[Order]:
        return list(self._open_orders)


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def order_manager(mock_provider: MockProvider) -> OrderManager:
    return OrderManager(provider=mock_provider)


# ── Tests: Clean reconciliation ─────────────────────────────────────


class TestReconcilerClean:
    @pytest.mark.asyncio
    async def test_no_mismatches_when_empty(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        reconciler = Reconciler(event_bus, order_manager, mock_provider)
        mismatches = await reconciler.reconcile()
        assert mismatches == []

    @pytest.mark.asyncio
    async def test_no_mismatches_when_synced(
        self, event_bus: EventBus, mock_provider: MockProvider
    ) -> None:
        order = _make_order(status=OrderStatus.OPEN)
        mock_provider._open_orders = [order]

        om = OrderManager(provider=mock_provider)
        # Manually inject into tracking
        om._orders[order.client_order_id] = order

        reconciler = Reconciler(event_bus, om, mock_provider)
        mismatches = await reconciler.reconcile()
        assert mismatches == []

    @pytest.mark.asyncio
    async def test_stats_updated_after_clean_run(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        reconciler = Reconciler(event_bus, order_manager, mock_provider)
        await reconciler.reconcile()
        stats = reconciler.stats
        assert stats["total_runs"] == 1
        assert stats["total_mismatches"] == 0
        assert stats["last_run"] is not None


# ── Tests: Ghost orders ─────────────────────────────────────────────


class TestGhostOrders:
    @pytest.mark.asyncio
    async def test_detects_ghost_order(
        self, event_bus: EventBus, mock_provider: MockProvider
    ) -> None:
        """Order tracked locally but not on venue."""
        order = _make_order(status=OrderStatus.OPEN)

        om = OrderManager(provider=mock_provider)
        om._orders[order.client_order_id] = order
        # Venue returns empty
        mock_provider._open_orders = []

        reconciler = Reconciler(event_bus, om, mock_provider)
        mismatches = await reconciler.reconcile()

        assert len(mismatches) == 1
        assert mismatches[0].type == "ghost_order"
        assert mismatches[0].local_order == order


# ── Tests: Orphan orders ────────────────────────────────────────────


class TestOrphanOrders:
    @pytest.mark.asyncio
    async def test_detects_orphan_order(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        """Order on venue but not tracked locally."""
        orphan = _make_order(status=OrderStatus.OPEN)
        mock_provider._open_orders = [orphan]

        reconciler = Reconciler(event_bus, order_manager, mock_provider)
        mismatches = await reconciler.reconcile()

        assert len(mismatches) == 1
        assert mismatches[0].type == "orphan_order"
        assert mismatches[0].venue_order == orphan


# ── Tests: Fill mismatches ──────────────────────────────────────────


class TestFillMismatches:
    @pytest.mark.asyncio
    async def test_detects_fill_qty_divergence(
        self, event_bus: EventBus, mock_provider: MockProvider
    ) -> None:
        """Same order on both sides, but filled_qty differs."""
        cid = uuid4()
        local_order = _make_order(
            client_order_id=cid,
            filled_qty=Decimal("10"),
            status=OrderStatus.PARTIALLY_FILLED,
        )
        venue_order = _make_order(
            client_order_id=cid,
            filled_qty=Decimal("50"),
            status=OrderStatus.PARTIALLY_FILLED,
        )

        om = OrderManager(provider=mock_provider)
        om._orders[cid] = local_order
        mock_provider._open_orders = [venue_order]

        reconciler = Reconciler(event_bus, om, mock_provider)
        mismatches = await reconciler.reconcile()

        assert len(mismatches) == 1
        assert mismatches[0].type == "fill_mismatch"
        assert mismatches[0].extra["local_filled"] == "10"
        assert mismatches[0].extra["venue_filled"] == "50"


# ── Tests: Mismatch callback ────────────────────────────────────────


class TestMismatchCallback:
    @pytest.mark.asyncio
    async def test_invokes_callback_on_mismatch(
        self, event_bus: EventBus, mock_provider: MockProvider
    ) -> None:
        callback = AsyncMock()
        orphan = _make_order(status=OrderStatus.OPEN)
        mock_provider._open_orders = [orphan]

        om = OrderManager(provider=mock_provider)
        reconciler = Reconciler(
            event_bus, om, mock_provider, mismatch_callback=callback
        )
        await reconciler.reconcile()

        callback.assert_awaited_once()
        args = callback.call_args[0][0]
        assert len(args) == 1
        assert args[0]["type"] == "orphan_order"

    @pytest.mark.asyncio
    async def test_no_callback_when_clean(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        callback = AsyncMock()
        reconciler = Reconciler(
            event_bus, order_manager, mock_provider, mismatch_callback=callback
        )
        await reconciler.reconcile()
        callback.assert_not_awaited()


# ── Tests: Event publishing ─────────────────────────────────────────


class TestReconcilerEvents:
    @pytest.mark.asyncio
    async def test_publishes_clean_event(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        events: list = []

        async def collect():
            async for event in event_bus.subscribe("reconciliation"):
                events.append(event)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        reconciler = Reconciler(event_bus, order_manager, mock_provider)
        await reconciler.reconcile()
        await asyncio.wait_for(task, timeout=2.0)

        assert len(events) == 1
        assert events[0].payload["status"] == "clean"
        assert events[0].payload["mismatch_count"] == 0

    @pytest.mark.asyncio
    async def test_publishes_mismatch_event(
        self, event_bus: EventBus, mock_provider: MockProvider
    ) -> None:
        events: list = []

        async def collect():
            async for event in event_bus.subscribe("reconciliation"):
                events.append(event)
                break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)

        orphan = _make_order(status=OrderStatus.OPEN)
        mock_provider._open_orders = [orphan]

        om = OrderManager(provider=mock_provider)
        reconciler = Reconciler(event_bus, om, mock_provider)
        await reconciler.reconcile()
        await asyncio.wait_for(task, timeout=2.0)

        assert len(events) == 1
        assert events[0].payload["status"] == "mismatch"
        assert events[0].payload["mismatch_count"] == 1


# ── Tests: Venue fetch error ────────────────────────────────────────


class TestVenueFetchError:
    @pytest.mark.asyncio
    async def test_handles_venue_fetch_failure(
        self, event_bus: EventBus, order_manager: OrderManager
    ) -> None:
        """When venue fetch fails, report it as a mismatch."""
        failing_provider = MockProvider()
        # Override get_open_orders to raise
        failing_provider.get_open_orders = AsyncMock(side_effect=RuntimeError("connection lost"))  # type: ignore[method-assign]

        callback = AsyncMock()
        reconciler = Reconciler(
            event_bus, order_manager, failing_provider, mismatch_callback=callback
        )
        mismatches = await reconciler.reconcile()

        assert len(mismatches) == 1
        assert mismatches[0].type == "venue_fetch_error"
        callback.assert_awaited_once()


# ── Tests: Mismatch serialization ───────────────────────────────────


class TestMismatchSerialization:
    def test_to_dict_basic(self) -> None:
        m = Mismatch(type="ghost_order", detail="order xyz gone")
        d = m.to_dict()
        assert d["type"] == "ghost_order"
        assert d["detail"] == "order xyz gone"

    def test_to_dict_with_orders(self) -> None:
        order = _make_order()
        m = Mismatch(type="fill_mismatch", detail="qty off", local_order=order)
        d = m.to_dict()
        assert "local_client_order_id" in d
        assert d["local_client_order_id"] == str(order.client_order_id)

    def test_to_dict_with_extra(self) -> None:
        m = Mismatch(type="test", detail="test", extra={"foo": "bar"})
        d = m.to_dict()
        assert d["foo"] == "bar"


# ── Tests: Periodic loop ────────────────────────────────────────────


class TestReconcilerLoop:
    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        reconciler = Reconciler(
            event_bus, order_manager, mock_provider, interval_seconds=1
        )
        await reconciler.start()
        assert reconciler.stats["running"] is True
        await asyncio.sleep(0.1)
        await reconciler.stop()
        assert reconciler.stats["running"] is False

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(
        self, event_bus: EventBus, order_manager: OrderManager, mock_provider: MockProvider
    ) -> None:
        reconciler = Reconciler(
            event_bus, order_manager, mock_provider, interval_seconds=60
        )
        await reconciler.start()
        await reconciler.start()  # should not create a second task
        await reconciler.stop()
