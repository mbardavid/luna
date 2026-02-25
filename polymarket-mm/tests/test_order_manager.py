"""Tests for execution/order_manager.py."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from execution.order_manager import OrderManager
from models.order import Order, OrderStatus, Side


# ── Fake ExecutionProvider ───────────────────────────────────────────


class FakeProvider:
    """Minimal ExecutionProvider stub for unit tests."""

    def __init__(self) -> None:
        self.submitted: list[Order] = []
        self.cancelled: list = []
        self.amended: list = []
        self._reject_next = False

    def reject_next(self) -> None:
        self._reject_next = True

    async def submit_order(self, order: Order) -> Order:
        if self._reject_next:
            self._reject_next = False
            return order.model_copy(update={"status": OrderStatus.REJECTED})
        self.submitted.append(order)
        return order.model_copy(update={"status": OrderStatus.OPEN})

    async def cancel_order(self, client_order_id) -> bool:
        self.cancelled.append(client_order_id)
        return True

    async def amend_order(self, client_order_id, new_price, new_size) -> Order:
        self.amended.append((client_order_id, new_price, new_size))
        # Find in submitted
        for o in self.submitted:
            if o.client_order_id == client_order_id:
                return o.model_copy(
                    update={"price": new_price, "size": new_size, "status": OrderStatus.OPEN}
                )
        raise ValueError(f"Order {client_order_id} not found")

    async def get_open_orders(self) -> list[Order]:
        return list(self.submitted)


# ── Helpers ──────────────────────────────────────────────────────────


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


# ── Tests ────────────────────────────────────────────────────────────


class TestOrderManager:

    @pytest.fixture
    def provider(self) -> FakeProvider:
        return FakeProvider()

    @pytest.fixture
    def manager(self, provider: FakeProvider) -> OrderManager:
        return OrderManager(provider)

    @pytest.mark.asyncio
    async def test_submit_returns_open_order(self, manager: OrderManager) -> None:
        order = _make_order()
        result = await manager.submit(order)
        assert result.status == OrderStatus.OPEN
        assert result.client_order_id == order.client_order_id

    @pytest.mark.asyncio
    async def test_submit_idempotent(self, manager: OrderManager, provider: FakeProvider) -> None:
        order = _make_order()
        r1 = await manager.submit(order)
        r2 = await manager.submit(order)
        assert r1.client_order_id == r2.client_order_id
        # Provider should only have been called once
        assert len(provider.submitted) == 1

    @pytest.mark.asyncio
    async def test_submit_rejected(self, manager: OrderManager, provider: FakeProvider) -> None:
        provider.reject_next()
        order = _make_order()
        result = await manager.submit(order)
        assert result.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_cancel_success(self, manager: OrderManager, provider: FakeProvider) -> None:
        order = _make_order()
        await manager.submit(order)
        success = await manager.cancel(order.client_order_id)
        assert success is True
        # Order should now be CANCELLED in tracking
        tracked = manager.get_order(order.client_order_id)
        assert tracked is not None
        assert tracked.status == OrderStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_unknown_order(self, manager: OrderManager) -> None:
        success = await manager.cancel(uuid4())
        assert success is False

    @pytest.mark.asyncio
    async def test_cancel_terminal_order(self, manager: OrderManager, provider: FakeProvider) -> None:
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)
        # Order is REJECTED (terminal) — cancel should return False
        success = await manager.cancel(order.client_order_id)
        assert success is False

    @pytest.mark.asyncio
    async def test_cancel_all(self, manager: OrderManager) -> None:
        o1 = _make_order()
        o2 = _make_order()
        await manager.submit(o1)
        await manager.submit(o2)
        cancelled = await manager.cancel_all()
        assert cancelled == 2
        assert len(manager.get_active_orders()) == 0

    @pytest.mark.asyncio
    async def test_amend_success(self, manager: OrderManager) -> None:
        order = _make_order()
        await manager.submit(order)
        result = await manager.amend(
            order.client_order_id,
            new_price=Decimal("0.55"),
            new_size=Decimal("200"),
        )
        assert result.price == Decimal("0.55")
        assert result.size == Decimal("200")

    @pytest.mark.asyncio
    async def test_amend_untracked_raises(self, manager: OrderManager) -> None:
        with pytest.raises(ValueError, match="not tracked"):
            await manager.amend(uuid4(), Decimal("0.5"), Decimal("100"))

    @pytest.mark.asyncio
    async def test_amend_terminal_raises(self, manager: OrderManager, provider: FakeProvider) -> None:
        provider.reject_next()
        order = _make_order()
        await manager.submit(order)
        with pytest.raises(ValueError, match="terminal state"):
            await manager.amend(order.client_order_id, Decimal("0.5"), Decimal("100"))

    @pytest.mark.asyncio
    async def test_get_active_orders(self, manager: OrderManager, provider: FakeProvider) -> None:
        o1 = _make_order()
        o2 = _make_order()
        await manager.submit(o1)
        provider.reject_next()
        await manager.submit(o2)
        active = manager.get_active_orders()
        assert len(active) == 1
        assert active[0].client_order_id == o1.client_order_id

    @pytest.mark.asyncio
    async def test_tracked_count(self, manager: OrderManager, provider: FakeProvider) -> None:
        o1 = _make_order()
        o2 = _make_order()
        await manager.submit(o1)
        provider.reject_next()
        await manager.submit(o2)
        assert manager.tracked_count == 2  # both tracked, one active one rejected
