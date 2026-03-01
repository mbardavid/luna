"""Tests for TASK 2 — OrderManager terminal order cleanup (mem-leak fix).

Verifies that terminal orders (FILLED/CANCELLED/REJECTED/EXPIRED) are
automatically purged from the internal tracking dict after 5 minutes,
preventing unbounded memory growth during continuous operation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from execution.order_manager import OrderManager, _TERMINAL_TTL_SECONDS, _TERMINAL_STATUSES
from models.order import Order, OrderStatus, Side


# ── Fake ExecutionProvider ───────────────────────────────────────────


class FakeProvider:
    """Minimal ExecutionProvider stub for unit tests."""

    def __init__(self) -> None:
        self.submitted: list[Order] = []
        self._reject_next = False
        self._fill_next = False

    def reject_next(self) -> None:
        self._reject_next = True

    def fill_next(self) -> None:
        self._fill_next = True

    async def submit_order(self, order: Order) -> Order:
        if self._reject_next:
            self._reject_next = False
            return order.model_copy(update={"status": OrderStatus.REJECTED})
        if self._fill_next:
            self._fill_next = False
            return order.model_copy(update={"status": OrderStatus.FILLED})
        self.submitted.append(order)
        return order.model_copy(update={"status": OrderStatus.OPEN})

    async def cancel_order(self, client_order_id) -> bool:
        return True

    async def amend_order(self, client_order_id, new_price, new_size) -> Order:
        for o in self.submitted:
            if o.client_order_id == client_order_id:
                return o.model_copy(
                    update={"price": new_price, "size": new_size, "status": OrderStatus.OPEN}
                )
        raise ValueError(f"Order {client_order_id} not found")

    async def get_open_orders(self) -> list[Order]:
        return list(self.submitted)


def _make_order(**kwargs) -> Order:
    defaults = {
        "market_id": "test-mkt-001",
        "token_id": "test-tok-001",
        "side": Side.BUY,
        "price": Decimal("0.50"),
        "size": Decimal("100"),
    }
    defaults.update(kwargs)
    return Order(**defaults)


class TestOrderManagerTerminalCleanup:
    """Tests for automatic terminal order cleanup."""

    @pytest.fixture
    def provider(self) -> FakeProvider:
        return FakeProvider()

    @pytest.fixture
    def manager(self, provider: FakeProvider) -> OrderManager:
        return OrderManager(provider)

    def test_terminal_ttl_constant(self) -> None:
        """TTL constant is 300 seconds (5 minutes)."""
        assert _TERMINAL_TTL_SECONDS == 300

    @pytest.mark.asyncio
    async def test_rejected_order_tracked_initially(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """Rejected orders are tracked immediately after submission."""
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)
        assert manager.tracked_count == 1
        assert order.client_order_id in manager._terminal_timestamps

    @pytest.mark.asyncio
    async def test_cancelled_order_gets_terminal_timestamp(
        self, manager: OrderManager,
    ) -> None:
        """Cancelled orders get a terminal timestamp."""
        order = _make_order()
        await manager.submit(order)
        await manager.cancel(order.client_order_id)
        assert order.client_order_id in manager._terminal_timestamps

    @pytest.mark.asyncio
    async def test_filled_order_gets_terminal_timestamp(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """Filled orders (submitted as filled) get a terminal timestamp."""
        provider.fill_next()
        order = _make_order()
        await manager.submit(order)
        assert order.client_order_id in manager._terminal_timestamps

    @pytest.mark.asyncio
    async def test_stale_terminal_orders_purged_on_get_active(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """Terminal orders older than TTL are purged when get_active_orders is called."""
        # Submit and reject an order
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)
        assert manager.tracked_count == 1

        # Backdate the terminal timestamp to simulate aging
        old_time = datetime.now(timezone.utc) - timedelta(seconds=_TERMINAL_TTL_SECONDS + 10)
        manager._terminal_timestamps[order.client_order_id] = old_time

        # Calling get_active_orders triggers cleanup
        active = manager.get_active_orders()
        assert len(active) == 0
        assert manager.tracked_count == 0  # purged
        assert order.client_order_id not in manager._orders
        assert order.client_order_id not in manager._terminal_timestamps

    @pytest.mark.asyncio
    async def test_fresh_terminal_orders_not_purged(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """Terminal orders within TTL are NOT purged."""
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)

        # Don't backdate — it's fresh
        active = manager.get_active_orders()
        assert len(active) == 0  # no active orders
        assert manager.tracked_count == 1  # still tracked

    @pytest.mark.asyncio
    async def test_active_orders_never_purged(
        self, manager: OrderManager,
    ) -> None:
        """Active (non-terminal) orders are never purged."""
        order = _make_order()
        await manager.submit(order)

        # Even after multiple get_active_orders calls
        for _ in range(10):
            active = manager.get_active_orders()

        assert len(active) == 1
        assert manager.tracked_count == 1

    @pytest.mark.asyncio
    async def test_mixed_orders_only_stale_purged(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """With a mix of active and stale terminal orders, only stale ones are purged."""
        # Active order
        active_order = _make_order()
        await manager.submit(active_order)

        # Stale rejected order
        provider.reject_next()
        stale_order = _make_order()
        await manager.submit(stale_order)
        old_time = datetime.now(timezone.utc) - timedelta(seconds=_TERMINAL_TTL_SECONDS + 10)
        manager._terminal_timestamps[stale_order.client_order_id] = old_time

        # Fresh cancelled order
        fresh_cancelled = _make_order()
        await manager.submit(fresh_cancelled)
        await manager.cancel(fresh_cancelled.client_order_id)

        assert manager.tracked_count == 3

        active = manager.get_active_orders()

        # 1 active order left
        assert len(active) == 1
        assert active[0].client_order_id == active_order.client_order_id

        # Stale was purged, fresh cancelled still tracked
        assert manager.tracked_count == 2
        assert stale_order.client_order_id not in manager._orders
        assert fresh_cancelled.client_order_id in manager._orders

    @pytest.mark.asyncio
    async def test_bulk_terminal_orders_purged(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """Simulate many terminal orders accumulating and verify cleanup."""
        old_time = datetime.now(timezone.utc) - timedelta(seconds=_TERMINAL_TTL_SECONDS + 10)

        # Submit 100 orders that get rejected
        for _ in range(100):
            provider.reject_next()
            order = _make_order()
            await manager.submit(order)
            manager._terminal_timestamps[order.client_order_id] = old_time

        assert manager.tracked_count == 100

        # One call to get_active_orders should purge all
        active = manager.get_active_orders()
        assert len(active) == 0
        assert manager.tracked_count == 0

    @pytest.mark.asyncio
    async def test_get_order_still_works_before_purge(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """get_order can find terminal orders before they are purged."""
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)

        # Can still look it up
        tracked = manager.get_order(order.client_order_id)
        assert tracked is not None
        assert tracked.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_get_order_returns_none_after_purge(
        self, manager: OrderManager, provider: FakeProvider,
    ) -> None:
        """get_order returns None after a terminal order has been purged."""
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)

        # Backdate and trigger cleanup
        old_time = datetime.now(timezone.utc) - timedelta(seconds=_TERMINAL_TTL_SECONDS + 10)
        manager._terminal_timestamps[order.client_order_id] = old_time
        manager.get_active_orders()

        assert manager.get_order(order.client_order_id) is None
