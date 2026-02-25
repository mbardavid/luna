"""Tests for Phase 4 — Feature Engineering.

Covers:
- FeatureEngine computation with known inputs
- ToxicFlowDetector with normal vs extreme scenarios
- CLOBSentimentCollector with simulated events
- Rolling window aging behavior
- Integration: PaperVenue → FeatureEngine → FeatureVector
- Minimum 20 test cases
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from core.event_bus import EventBus
from data.collectors.oracles.sentiment import CLOBSentimentCollector
from models.feature_vector import FeatureVector
from models.market_state import MarketState, MarketType
from strategy.feature_engine import FeatureEngine, FeatureEngineConfig
from strategy.toxic_flow_detector import (
    ToxicFlowConfig,
    ToxicFlowDetector,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_market_state(
    market_id: str = "test-mkt",
    yes_bid: Decimal = Decimal("0.45"),
    yes_ask: Decimal = Decimal("0.55"),
    no_bid: Decimal = Decimal("0.45"),
    no_ask: Decimal = Decimal("0.55"),
    depth_yes_bid: Decimal = Decimal("200"),
    depth_yes_ask: Decimal = Decimal("200"),
    depth_no_bid: Decimal = Decimal("200"),
    depth_no_ask: Decimal = Decimal("200"),
) -> MarketState:
    return MarketState(
        market_id=market_id,
        condition_id="cond-001",
        token_id_yes="tok-yes",
        token_id_no="tok-no",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        depth_yes_bid=depth_yes_bid,
        depth_yes_ask=depth_yes_ask,
        depth_no_bid=depth_no_bid,
        depth_no_ask=depth_no_ask,
    )


def _make_orderbook(
    bids: list[tuple[str, str]] | None = None,
    asks: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    if bids is None:
        bids = [("0.45", "200"), ("0.44", "300"), ("0.43", "150")]
    if asks is None:
        asks = [("0.55", "200"), ("0.56", "300"), ("0.57", "150")]
    return {
        "bids": [{"price": Decimal(p), "size": Decimal(s)} for p, s in bids],
        "asks": [{"price": Decimal(p), "size": Decimal(s)} for p, s in asks],
        "timestamp": datetime.now(timezone.utc),
        "hash": "abc123",
    }


def _make_symmetric_orderbook(bid_size: str, ask_size: str) -> dict[str, Any]:
    """Single-level book for controlled imbalance tests."""
    return {
        "bids": [{"price": Decimal("0.50"), "size": Decimal(bid_size)}],
        "asks": [{"price": Decimal("0.52"), "size": Decimal(ask_size)}],
        "timestamp": datetime.now(timezone.utc),
        "hash": "sym",
    }


# ════════════════════════════════════════════════════════════════════
# 1. FeatureEngine Tests
# ════════════════════════════════════════════════════════════════════


class TestFeatureEngineBasic:
    """Basic FeatureEngine computation tests."""

    @pytest.mark.asyncio
    async def test_compute_returns_feature_vector(self):
        """compute() returns a FeatureVector instance."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob, oracle_price=0.50)
        assert isinstance(fv, FeatureVector)

    @pytest.mark.asyncio
    async def test_market_id_matches(self):
        """FeatureVector.market_id matches input MarketState."""
        engine = FeatureEngine()
        ms = _make_market_state(market_id="my-market")
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob)
        assert fv.market_id == "my-market"

    @pytest.mark.asyncio
    async def test_spread_bps_computed(self):
        """Spread BPS should be positive for a normal spread."""
        engine = FeatureEngine()
        ms = _make_market_state(yes_bid=Decimal("0.45"), yes_ask=Decimal("0.55"))
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob)
        # spread = 0.10, mid = 0.50, bps = 0.10/0.50 * 10000 = 2000
        assert fv.spread_bps > 0
        assert fv.spread_bps == Decimal("2000.00")

    @pytest.mark.asyncio
    async def test_spread_bps_zero_when_no_bid(self):
        """Spread BPS is 0 when there's no bid."""
        engine = FeatureEngine()
        ms = _make_market_state(yes_bid=Decimal("0"), yes_ask=Decimal("0.55"))
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob)
        assert fv.spread_bps == Decimal("0")

    @pytest.mark.asyncio
    async def test_book_imbalance_balanced(self):
        """Balanced book → imbalance ≈ 0."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_symmetric_orderbook("500", "500")
        fv = await engine.compute(ms, ob)
        assert abs(fv.book_imbalance) < 0.01

    @pytest.mark.asyncio
    async def test_book_imbalance_bid_heavy(self):
        """Bid-heavy book → positive imbalance."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_symmetric_orderbook("900", "100")
        fv = await engine.compute(ms, ob)
        assert fv.book_imbalance > 0.5

    @pytest.mark.asyncio
    async def test_book_imbalance_ask_heavy(self):
        """Ask-heavy book → negative imbalance."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_symmetric_orderbook("100", "900")
        fv = await engine.compute(ms, ob)
        assert fv.book_imbalance < -0.5

    @pytest.mark.asyncio
    async def test_oracle_delta_computed(self):
        """Oracle delta = mid - oracle_price."""
        engine = FeatureEngine()
        ms = _make_market_state(yes_bid=Decimal("0.48"), yes_ask=Decimal("0.52"))
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob, oracle_price=0.45)
        # mid = 0.50, oracle = 0.45, delta = 0.05
        assert abs(fv.oracle_delta - 0.05) < 0.001

    @pytest.mark.asyncio
    async def test_oracle_delta_none(self):
        """Oracle delta is 0 when no oracle provided."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob, oracle_price=None)
        assert fv.oracle_delta == 0.0

    @pytest.mark.asyncio
    async def test_expected_fee_bps_default(self):
        """Default expected fee is 2 bps."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob)
        assert fv.expected_fee_bps == Decimal("2")

    @pytest.mark.asyncio
    async def test_liquidity_score_range(self):
        """Liquidity score should be in [0, 1]."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()
        fv = await engine.compute(ms, ob)
        assert 0.0 <= fv.liquidity_score <= 1.0

    @pytest.mark.asyncio
    async def test_data_quality_score_full(self):
        """Fully populated data → quality near 1.0 (may have small penalty for insufficient rolling data)."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()
        # Feed multiple ticks to build up rolling data
        for _ in range(5):
            fv = await engine.compute(ms, ob)
        assert fv.data_quality_score >= 0.8

    @pytest.mark.asyncio
    async def test_data_quality_degrades_no_book(self):
        """Missing book data → quality drops significantly."""
        engine = FeatureEngine()
        ms = _make_market_state(yes_bid=Decimal("0"), yes_ask=Decimal("0"))
        ob = {"bids": [], "asks": []}
        fv = await engine.compute(ms, ob)
        assert fv.data_quality_score < 0.5


class TestFeatureEngineRollingWindows:
    """Test rolling window behavior."""

    @pytest.mark.asyncio
    async def test_momentum_builds_over_ticks(self):
        """Momentum should reflect upward trend after rising prices."""
        engine = FeatureEngine(FeatureEngineConfig(momentum_window=10, volatility_window=20))
        ob = _make_orderbook()

        # Simulate rising mid price: 0.40 → 0.60
        for i in range(10):
            bid = Decimal(str(round(0.40 + i * 0.02, 2)))
            ask = bid + Decimal("0.02")
            ms = _make_market_state(yes_bid=bid, yes_ask=ask)
            fv = await engine.compute(ms, ob)

        assert fv.micro_momentum > 0

    @pytest.mark.asyncio
    async def test_momentum_negative_on_decline(self):
        """Momentum should be negative after falling prices."""
        engine = FeatureEngine(FeatureEngineConfig(momentum_window=10, volatility_window=20))
        ob = _make_orderbook()

        for i in range(10):
            bid = Decimal(str(round(0.60 - i * 0.02, 2)))
            ask = bid + Decimal("0.02")
            ms = _make_market_state(yes_bid=bid, yes_ask=ask)
            fv = await engine.compute(ms, ob)

        assert fv.micro_momentum < 0

    @pytest.mark.asyncio
    async def test_volatility_increases_with_large_moves(self):
        """Volatility should increase with larger price swings."""
        engine = FeatureEngine(FeatureEngineConfig(volatility_window=20))
        ob = _make_orderbook()

        # Calm market
        for i in range(10):
            ms = _make_market_state(yes_bid=Decimal("0.49"), yes_ask=Decimal("0.51"))
            fv_calm = await engine.compute(ms, ob)

        engine.reset()

        # Volatile market
        for i in range(10):
            offset = Decimal(str(round(0.05 * ((-1) ** i), 2)))
            ms = _make_market_state(
                yes_bid=Decimal("0.45") + offset,
                yes_ask=Decimal("0.55") + offset,
            )
            fv_volatile = await engine.compute(ms, ob)

        assert fv_volatile.volatility_1m > fv_calm.volatility_1m

    @pytest.mark.asyncio
    async def test_rolling_window_maxlen(self):
        """Windows should not exceed configured maxlen."""
        config = FeatureEngineConfig(volatility_window=5, imbalance_window=5, momentum_window=5)
        engine = FeatureEngine(config)
        ms = _make_market_state()
        ob = _make_orderbook()

        for _ in range(20):
            await engine.compute(ms, ob)

        assert len(engine._mid_prices["test-mkt"]) <= 5
        assert len(engine._imbalances["test-mkt"]) <= 5

    @pytest.mark.asyncio
    async def test_reset_clears_windows(self):
        """reset() should clear all rolling data."""
        engine = FeatureEngine()
        ms = _make_market_state()
        ob = _make_orderbook()

        for _ in range(5):
            await engine.compute(ms, ob)

        assert len(engine._mid_prices.get("test-mkt", [])) > 0
        engine.reset("test-mkt")
        assert "test-mkt" not in engine._mid_prices

    @pytest.mark.asyncio
    async def test_reset_all(self):
        """reset() with no args clears everything."""
        engine = FeatureEngine()
        ob = _make_orderbook()

        for mkt in ["mkt-a", "mkt-b"]:
            ms = _make_market_state(market_id=mkt)
            await engine.compute(ms, ob)

        engine.reset()
        assert len(engine._mid_prices) == 0


# ════════════════════════════════════════════════════════════════════
# 2. ToxicFlowDetector Tests
# ════════════════════════════════════════════════════════════════════


class TestToxicFlowDetector:
    """Toxic flow detection tests."""

    def _make_fv(self, imbalance: float = 0.0, toxic_score: float = 0.0) -> FeatureVector:
        return FeatureVector(
            market_id="test-mkt",
            spread_bps=Decimal("100"),
            book_imbalance=imbalance,
            toxic_flow_score=toxic_score,
        )

    @pytest.mark.asyncio
    async def test_normal_not_toxic(self):
        """Normal imbalance should not be toxic."""
        detector = ToxicFlowDetector()
        # Build baseline of normal observations
        for _ in range(10):
            fv = self._make_fv(imbalance=0.0)
            detector.update(fv)

        fv = self._make_fv(imbalance=0.1)
        assert detector.is_toxic(fv) is False

    @pytest.mark.asyncio
    async def test_extreme_is_toxic(self):
        """Extreme imbalance spike should be detected as toxic."""
        config = ToxicFlowConfig(toxic_zscore_threshold=2.0, min_observations=5)
        detector = ToxicFlowDetector(config=config)

        # Build baseline of near-zero imbalance
        for _ in range(20):
            fv = self._make_fv(imbalance=0.05)
            detector.update(fv)

        # Extreme spike
        fv_spike = self._make_fv(imbalance=0.95, toxic_score=3.0)
        assert detector.is_toxic(fv_spike) is True

    @pytest.mark.asyncio
    async def test_should_halt_extreme(self):
        """Very extreme z-score should trigger halt."""
        config = ToxicFlowConfig(halt_zscore_threshold=3.5, min_observations=5)
        detector = ToxicFlowDetector(config=config)

        # Use direct toxic_flow_score > halt threshold
        fv = self._make_fv(imbalance=0.95, toxic_score=4.0)
        assert detector.should_halt(fv) is True

    @pytest.mark.asyncio
    async def test_should_halt_combined_signal(self):
        """Combined high z-score + extreme imbalance triggers halt."""
        config = ToxicFlowConfig(
            combined_zscore_threshold=2.5,
            imbalance_halt_threshold=0.7,
            min_observations=5,
        )
        detector = ToxicFlowDetector(config=config)

        fv = self._make_fv(imbalance=0.9, toxic_score=3.0)
        assert detector.should_halt(fv) is True

    @pytest.mark.asyncio
    async def test_should_not_halt_moderate(self):
        """Moderate signals should NOT halt."""
        config = ToxicFlowConfig(halt_zscore_threshold=3.5, min_observations=5)
        detector = ToxicFlowDetector(config=config)

        fv = self._make_fv(imbalance=0.3, toxic_score=1.5)
        assert detector.should_halt(fv) is False

    @pytest.mark.asyncio
    async def test_publishes_toxic_flow_event(self):
        """evaluate_and_publish() should publish to EventBus when toxic."""
        bus = EventBus()
        config = ToxicFlowConfig(toxic_zscore_threshold=1.0, min_observations=3)
        detector = ToxicFlowDetector(event_bus=bus, config=config)

        received: list[Any] = []

        async def _listen():
            async for event in bus.subscribe("toxic_flow"):
                received.append(event)
                break

        listener = asyncio.create_task(_listen())
        await asyncio.sleep(0.01)

        # Build baseline
        for _ in range(5):
            detector.update(self._make_fv(imbalance=0.0))

        # Trigger toxic
        fv = self._make_fv(imbalance=0.95, toxic_score=3.0)
        result = await detector.evaluate_and_publish(fv)

        # Wait for listener
        await asyncio.wait_for(listener, timeout=2.0)

        assert result is True
        assert len(received) == 1
        assert received[0].topic == "toxic_flow"
        assert received[0].payload["market_id"] == "test-mkt"

    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        """reset() should clear detector state."""
        detector = ToxicFlowDetector()
        for _ in range(10):
            detector.update(self._make_fv(imbalance=0.1))

        assert len(detector._imbalances.get("test-mkt", [])) > 0
        detector.reset("test-mkt")
        assert "test-mkt" not in detector._imbalances


# ════════════════════════════════════════════════════════════════════
# 3. CLOBSentimentCollector Tests
# ════════════════════════════════════════════════════════════════════


class TestCLOBSentimentCollector:
    """CLOBSentimentCollector tests."""

    @pytest.mark.asyncio
    async def test_empty_returns_neutral(self):
        """No trades → delta = 0."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)
        delta = await collector.get_delta("tok-yes")
        assert delta == 0.0

    @pytest.mark.asyncio
    async def test_buy_heavy_positive_delta(self):
        """More buy volume → positive delta."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for _ in range(8):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "BUY"})
        for _ in range(2):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "SELL"})

        delta = await collector.get_delta("tok-yes")
        assert delta > 0.3

    @pytest.mark.asyncio
    async def test_sell_heavy_negative_delta(self):
        """More sell volume → negative delta."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for _ in range(2):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "BUY"})
        for _ in range(8):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "SELL"})

        delta = await collector.get_delta("tok-yes")
        assert delta < -0.3

    @pytest.mark.asyncio
    async def test_volume_ratio_balanced(self):
        """Equal buy/sell → ratio ≈ 1.0."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for side in ["BUY", "SELL"]:
            for _ in range(5):
                collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": side})

        ratio = collector.get_volume_ratio("tok-yes")
        assert abs(ratio - 1.0) < 0.01

    @pytest.mark.asyncio
    async def test_volume_ratio_buy_heavy(self):
        """Buy-heavy → ratio > 1."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for _ in range(10):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "BUY"})
        collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "SELL"})

        ratio = collector.get_volume_ratio("tok-yes")
        assert ratio > 5.0

    @pytest.mark.asyncio
    async def test_momentum_upward(self):
        """Rising prices → positive momentum."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for i in range(10):
            price = str(round(0.40 + i * 0.02, 2))
            collector.inject_trade("tok-yes", {"price": price, "size": "100", "side": "BUY"})

        momentum = collector.get_momentum("tok-yes")
        assert momentum > 0

    @pytest.mark.asyncio
    async def test_momentum_downward(self):
        """Falling prices → negative momentum."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for i in range(10):
            price = str(round(0.60 - i * 0.02, 2))
            collector.inject_trade("tok-yes", {"price": price, "size": "100", "side": "SELL"})

        momentum = collector.get_momentum("tok-yes")
        assert momentum < 0

    @pytest.mark.asyncio
    async def test_trade_count(self):
        """Trade count reflects injected trades."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)

        for _ in range(7):
            collector.inject_trade("tok-yes", {"price": "0.50", "size": "100", "side": "BUY"})

        assert collector.get_trade_count("tok-yes") == 7

    @pytest.mark.asyncio
    async def test_eventbus_consumption(self):
        """Collector consumes events published to EventBus."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus, window_seconds=300)
        await collector.start()

        # Allow subscription to register
        await asyncio.sleep(0.05)

        # Publish trade events
        for i in range(5):
            await bus.publish("trade", {
                "token_id": "tok-yes",
                "price": str(round(0.50 + i * 0.01, 2)),
                "size": "100",
                "side": "BUY",
            })

        # Give time for consumption
        await asyncio.sleep(0.1)

        count = collector.get_trade_count("tok-yes")
        await collector.stop()

        assert count == 5

    @pytest.mark.asyncio
    async def test_fill_events_consumed(self):
        """Collector also consumes 'fill' events from EventBus."""
        bus = EventBus()
        collector = CLOBSentimentCollector(event_bus=bus)
        await collector.start()
        await asyncio.sleep(0.05)

        await bus.publish("fill", {
            "token_id": "tok-yes",
            "fill_price": "0.55",
            "fill_qty": "50",
            "side": "BUY",
        })

        await asyncio.sleep(0.1)

        count = collector.get_trade_count("tok-yes")
        await collector.stop()

        assert count == 1


# ════════════════════════════════════════════════════════════════════
# 4. Integration: PaperVenue → FeatureEngine → FeatureVector
# ════════════════════════════════════════════════════════════════════


class TestIntegrationPipeline:
    """End-to-end integration with PaperVenue."""

    @pytest.mark.asyncio
    async def test_paper_venue_to_feature_vector(self):
        """PaperVenue snapshot → FeatureEngine → valid FeatureVector."""
        from paper.paper_venue import PaperVenue, MarketSimConfig

        bus = EventBus()
        config = MarketSimConfig(
            market_id="integ-001",
            condition_id="cond-integ",
            token_id_yes="tok-yes-integ",
            token_id_no="tok-no-integ",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            initial_yes_mid=Decimal("0.50"),
            volatility=Decimal("0.005"),
        )
        venue = PaperVenue(
            event_bus=bus,
            configs=[config],
            seed=42,
            fill_latency_ms=0,
            heartbeat_interval_s=999,
        )

        await venue.connect()

        try:
            engine = FeatureEngine()
            markets = await venue.get_active_markets()
            assert len(markets) == 1

            ms = markets[0]
            ob = await venue.get_orderbook(config.token_id_yes)

            fv = await engine.compute(ms, ob, oracle_price=0.50)

            # Validate structure
            assert isinstance(fv, FeatureVector)
            assert fv.market_id == "integ-001"
            assert fv.spread_bps >= 0
            assert -1.0 <= fv.book_imbalance <= 1.0
            assert 0.0 <= fv.liquidity_score <= 1.0
            assert fv.expected_fee_bps == Decimal("2")
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_multi_tick_pipeline(self):
        """Multiple ticks through the pipeline build momentum/volatility."""
        from paper.paper_venue import PaperVenue, MarketSimConfig

        bus = EventBus()
        config = MarketSimConfig(
            market_id="integ-multi",
            condition_id="cond-multi",
            token_id_yes="tok-yes-multi",
            token_id_no="tok-no-multi",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            initial_yes_mid=Decimal("0.50"),
            volatility=Decimal("0.005"),
        )
        venue = PaperVenue(
            event_bus=bus,
            configs=[config],
            seed=123,
            fill_latency_ms=0,
            heartbeat_interval_s=999,
        )

        await venue.connect()

        try:
            engine = FeatureEngine(FeatureEngineConfig(
                momentum_window=5,
                volatility_window=10,
                min_data_points=2,
            ))

            features: list[FeatureVector] = []
            for _ in range(10):
                markets = await venue.get_active_markets()
                ms = markets[0]
                ob = await venue.get_orderbook(config.token_id_yes)
                fv = await engine.compute(ms, ob, oracle_price=0.50)
                features.append(fv)

            # After 10 ticks, we should have valid data quality
            last = features[-1]
            assert last.data_quality_score >= 0.5
            # Momentum and volatility should have real values (may be small)
            # Just check they're computed (not necessarily > 0)
            assert isinstance(last.micro_momentum, float)
            assert isinstance(last.volatility_1m, float)
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_full_pipeline_with_toxic_detector(self):
        """Full pipeline: PaperVenue → FeatureEngine → ToxicFlowDetector."""
        from paper.paper_venue import PaperVenue, MarketSimConfig

        bus = EventBus()
        config = MarketSimConfig(
            market_id="integ-toxic",
            condition_id="cond-toxic",
            token_id_yes="tok-yes-toxic",
            token_id_no="tok-no-toxic",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            initial_yes_mid=Decimal("0.50"),
            volatility=Decimal("0.005"),
        )
        venue = PaperVenue(
            event_bus=bus,
            configs=[config],
            seed=99,
            fill_latency_ms=0,
            heartbeat_interval_s=999,
        )

        await venue.connect()

        try:
            engine = FeatureEngine()
            detector = ToxicFlowDetector(event_bus=bus)

            markets = await venue.get_active_markets()
            ms = markets[0]
            ob = await venue.get_orderbook(config.token_id_yes)
            fv = await engine.compute(ms, ob, oracle_price=0.50)

            # Normal conditions should not be toxic
            detector.update(fv)
            # With only 1 observation, detector should not flag toxic
            assert detector.is_toxic(fv) is False
        finally:
            await venue.disconnect()
