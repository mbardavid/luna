"""Tests for execution/queue_tracker.py."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from execution.queue_tracker import QueueTracker
from models.order import Order, Side


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


class TestQueueTracker:

    def test_register_and_position(self) -> None:
        tracker = QueueTracker()
        order = _make_order()
        tracker.register_order(order, depth_ahead=Decimal("500"))
        pos = tracker.estimated_position(order)
        # 500 ahead / 100 our size = position 5
        assert pos == 5

    def test_unregistered_returns_negative(self) -> None:
        tracker = QueueTracker()
        order = _make_order()
        assert tracker.estimated_position(order) == -1

    def test_unregister_removes_order(self) -> None:
        tracker = QueueTracker()
        order = _make_order()
        tracker.register_order(order, depth_ahead=Decimal("200"))
        tracker.unregister_order(order.client_order_id)
        assert tracker.estimated_position(order) == -1

    def test_update_decreases_position(self) -> None:
        tracker = QueueTracker()
        order = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        # Simulate 200 units being filled (removed) at our price level
        tracker.update({
            "side": "BUY",
            "price": Decimal("0.50"),
            "old_size": Decimal("600"),
            "new_size": Decimal("400"),
        })

        ahead = tracker.estimated_ahead(order)
        assert ahead == Decimal("300")  # 500 - 200

    def test_update_clamps_at_zero(self) -> None:
        tracker = QueueTracker()
        order = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("100"))

        # Remove more than what's ahead
        tracker.update({
            "side": "BUY",
            "price": Decimal("0.50"),
            "old_size": Decimal("200"),
            "new_size": Decimal("0"),
        })

        ahead = tracker.estimated_ahead(order)
        assert ahead == Decimal("0")
        assert tracker.estimated_position(order) == 0

    def test_update_ignores_different_side(self) -> None:
        tracker = QueueTracker()
        order = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        tracker.update({
            "side": "SELL",
            "price": Decimal("0.50"),
            "old_size": Decimal("300"),
            "new_size": Decimal("100"),
        })

        # Should not change since sides don't match
        assert tracker.estimated_ahead(order) == Decimal("500")

    def test_update_ignores_different_price(self) -> None:
        tracker = QueueTracker()
        order = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        tracker.update({
            "side": "BUY",
            "price": Decimal("0.49"),
            "old_size": Decimal("300"),
            "new_size": Decimal("100"),
        })

        assert tracker.estimated_ahead(order) == Decimal("500")

    def test_update_ignores_size_increase(self) -> None:
        tracker = QueueTracker()
        order = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        # New size > old size — orders added behind us
        tracker.update({
            "side": "BUY",
            "price": Decimal("0.50"),
            "old_size": Decimal("500"),
            "new_size": Decimal("700"),
        })

        assert tracker.estimated_ahead(order) == Decimal("500")

    def test_should_reprice_same_price(self) -> None:
        tracker = QueueTracker()
        order = _make_order(price=Decimal("0.50"))
        tracker.register_order(order, depth_ahead=Decimal("500"))
        # Same price — should not reprice
        assert tracker.should_reprice(order, Decimal("0.50")) is False

    def test_should_reprice_untracked(self) -> None:
        tracker = QueueTracker()
        order = _make_order(price=Decimal("0.50"))
        # Untracked — always reprice (no queue to lose)
        assert tracker.should_reprice(order, Decimal("0.51")) is True

    def test_should_reprice_early_in_queue(self) -> None:
        tracker = QueueTracker(reprice_threshold=Decimal("0.5"))
        order = _make_order(price=Decimal("0.50"), size=Decimal("100"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        # We've consumed nothing yet (fraction_consumed = 1 - 500/600 ≈ 0.17)
        # 0.17 < 0.5 threshold → should reprice
        assert tracker.should_reprice(order, Decimal("0.51")) is True

    def test_should_not_reprice_near_front(self) -> None:
        tracker = QueueTracker(reprice_threshold=Decimal("0.5"))
        order = _make_order(price=Decimal("0.50"), size=Decimal("100"))
        tracker.register_order(order, depth_ahead=Decimal("500"))

        # Simulate most of queue being consumed
        tracker.update({
            "side": "BUY",
            "price": Decimal("0.50"),
            "old_size": Decimal("600"),
            "new_size": Decimal("150"),
        })
        # Now ahead = 500 - 450 = 50
        # fraction_consumed = 1 - 50/150 ≈ 0.67 > 0.5 → don't reprice
        assert tracker.should_reprice(order, Decimal("0.51")) is False

    def test_multiple_orders_tracked(self) -> None:
        tracker = QueueTracker()
        o1 = _make_order(side=Side.BUY, price=Decimal("0.50"))
        o2 = _make_order(side=Side.BUY, price=Decimal("0.50"))
        tracker.register_order(o1, depth_ahead=Decimal("200"))
        tracker.register_order(o2, depth_ahead=Decimal("400"))

        # Update affects both at the same price
        tracker.update({
            "side": "BUY",
            "price": Decimal("0.50"),
            "old_size": Decimal("600"),
            "new_size": Decimal("500"),
        })

        assert tracker.estimated_ahead(o1) == Decimal("100")
        assert tracker.estimated_ahead(o2) == Decimal("300")
