"""Tests for runner.market_health — MarketHealthMonitor."""

from __future__ import annotations

import time
from decimal import Decimal
from unittest.mock import patch

import pytest

from models.position import Position
from runner.config import RotationConfig
from runner.market_health import (
    MarketHealthMonitor,
    MarketHealthSnapshot,
    MarketHealthStatus,
)

MARKET_ID = "test-health-market"
TOKEN_YES = "tok-yes-health"
TOKEN_NO = "tok-no-health"


@pytest.fixture
def config() -> RotationConfig:
    return RotationConfig(
        market_rotation=True,
        min_market_health_score=0.3,
        max_spread_bps=500,
        min_fill_rate_pct=1.0,
        fill_rate_window_hours=2.0,
        max_inventory_skew_pct=80.0,
    )


@pytest.fixture
def monitor(config: RotationConfig) -> MarketHealthMonitor:
    return MarketHealthMonitor(config)


@pytest.fixture
def balanced_position() -> Position:
    return Position(
        market_id=MARKET_ID,
        token_id_yes=TOKEN_YES,
        token_id_no=TOKEN_NO,
        qty_yes=Decimal("50"),
        qty_no=Decimal("50"),
    )


@pytest.fixture
def skewed_position() -> Position:
    return Position(
        market_id=MARKET_ID,
        token_id_yes=TOKEN_YES,
        token_id_no=TOKEN_NO,
        qty_yes=Decimal("100"),
        qty_no=Decimal("5"),
    )


class TestMarketHealthMonitorBasic:
    """Basic health evaluation with no data."""

    def test_no_data_returns_neutral(self, monitor: MarketHealthMonitor) -> None:
        """With no data, health should be neutral (scores ~0.5)."""
        snapshot = monitor.evaluate(MARKET_ID)
        # No data → spread_score=0.5, fill_score=0.5, skew=0.0 (no position)
        assert snapshot.health_score > 0
        assert snapshot.status != MarketHealthStatus.UNHEALTHY

    def test_no_data_no_position(self, monitor: MarketHealthMonitor) -> None:
        snapshot = monitor.evaluate(MARKET_ID, position=None)
        assert snapshot.inventory_skew_pct == 0.0


class TestMarketHealthSpread:
    """Spread-based health scoring."""

    def test_healthy_spread(self, monitor: MarketHealthMonitor) -> None:
        """Spread within threshold → good spread score."""
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 100.0)  # 100 bps
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.spread_score > 0.5
        assert snapshot.spread_bps == pytest.approx(100.0)

    def test_unhealthy_spread(self, monitor: MarketHealthMonitor) -> None:
        """Spread exceeding threshold → spread_score = 0, triggers UNHEALTHY."""
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 600.0)  # 600 bps > 500 threshold
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.spread_score == 0.0
        assert snapshot.is_unhealthy

    def test_borderline_spread(self, monitor: MarketHealthMonitor) -> None:
        """Spread at exactly threshold → spread_score ≈ 0."""
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 500.0)
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.spread_score == pytest.approx(0.0, abs=0.01)


class TestMarketHealthFillRate:
    """Fill rate scoring."""

    def test_good_fill_rate(self, monitor: MarketHealthMonitor) -> None:
        """Fill rate above threshold → healthy."""
        for _ in range(100):
            monitor.record_order(MARKET_ID)
        for _ in range(5):
            monitor.record_fill(MARKET_ID)
        # fill rate = 5/100 * 100 = 5%, well above 1% threshold
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.fill_rate_pct == pytest.approx(5.0)
        assert snapshot.fill_score > 0

    def test_zero_fill_rate(self, monitor: MarketHealthMonitor) -> None:
        """No fills with many orders → fill_rate = 0% → UNHEALTHY."""
        for _ in range(100):
            monitor.record_order(MARKET_ID)
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.fill_rate_pct == pytest.approx(0.0)
        assert snapshot.is_unhealthy

    def test_no_orders_neutral(self, monitor: MarketHealthMonitor) -> None:
        """No orders → can't compute fill rate → neutral."""
        snapshot = monitor.evaluate(MARKET_ID)
        assert snapshot.fill_rate_pct < 0  # sentinel value
        assert snapshot.fill_score == pytest.approx(0.5)


class TestMarketHealthInventorySkew:
    """Inventory skew scoring."""

    def test_balanced_position_low_skew(
        self,
        monitor: MarketHealthMonitor,
        balanced_position: Position,
    ) -> None:
        """50/50 position → 0% skew → healthy."""
        snapshot = monitor.evaluate(MARKET_ID, position=balanced_position)
        assert snapshot.inventory_skew_pct == pytest.approx(0.0)
        assert snapshot.skew_score > 0.5

    def test_skewed_position_high_skew(
        self,
        monitor: MarketHealthMonitor,
        skewed_position: Position,
    ) -> None:
        """100/5 position → ~90.5% skew → UNHEALTHY (> 80%)."""
        snapshot = monitor.evaluate(MARKET_ID, position=skewed_position)
        assert snapshot.inventory_skew_pct > 80.0
        assert snapshot.is_unhealthy

    def test_no_position_zero_skew(self, monitor: MarketHealthMonitor) -> None:
        snapshot = monitor.evaluate(MARKET_ID, position=None)
        assert snapshot.inventory_skew_pct == 0.0


class TestMarketHealthComposite:
    """Composite health score and status classification."""

    def test_all_healthy(
        self,
        monitor: MarketHealthMonitor,
        balanced_position: Position,
    ) -> None:
        """Good spread + good fills + balanced position → HEALTHY."""
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 50.0)  # low spread
        for _ in range(100):
            monitor.record_order(MARKET_ID)
        for _ in range(10):
            monitor.record_fill(MARKET_ID)
        snapshot = monitor.evaluate(MARKET_ID, position=balanced_position)
        assert snapshot.status == MarketHealthStatus.HEALTHY
        assert snapshot.health_score > 0.3

    def test_unhealthy_triggers_on_single_metric(
        self,
        monitor: MarketHealthMonitor,
    ) -> None:
        """Even if composite is OK, a single metric tripping triggers UNHEALTHY."""
        # Good spread, good fill rate, but...
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 50.0)
        for _ in range(100):
            monitor.record_order(MARKET_ID)
        for _ in range(10):
            monitor.record_fill(MARKET_ID)

        # ... extreme skew
        extreme_skew = Position(
            market_id=MARKET_ID,
            token_id_yes=TOKEN_YES,
            token_id_no=TOKEN_NO,
            qty_yes=Decimal("200"),
            qty_no=Decimal("1"),
        )
        snapshot = monitor.evaluate(MARKET_ID, position=extreme_skew)
        assert snapshot.is_unhealthy

    def test_prune_market(self, monitor: MarketHealthMonitor) -> None:
        """After pruning, evaluating should return neutral scores."""
        for _ in range(20):
            monitor.record_spread(MARKET_ID, 600.0)
        for _ in range(100):
            monitor.record_order(MARKET_ID)
        monitor.prune_market(MARKET_ID)
        snapshot = monitor.evaluate(MARKET_ID)
        # After prune, should be back to neutral (no data)
        assert not snapshot.is_unhealthy
