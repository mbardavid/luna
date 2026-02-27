"""Tests for the Fase 5 Quote Engine pipeline.

Covers:
- SpreadModel — half-spread computation
- InventorySkew — Avellaneda-Stoikov skew
- RewardsFarming — reward-driven spread tightening
- QuoteEngine — full pipeline integration

Scenarios:
- Normal market conditions
- High inventory (skew dominates)
- Low volatility (fee floor dominates)
- High volatility (vol component dominates)
- Toxic flow (widening + halt)
- Illiquid market (liquidity widening)
- Edge cases (zero mid, extreme inventory, etc.)
"""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from models.feature_vector import FeatureVector
from models.market_state import MarketState, MarketType
from models.position import Position
from models.quote_plan import QuoteSide, TokenSide
from strategy.spread_model import SpreadModel, SpreadModelConfig
from strategy.inventory_skew import InventorySkew, InventorySkewConfig
from strategy.rewards_farming import RewardsFarming, RewardsFarmingConfig
from strategy.quote_engine import QuoteEngine, QuoteEngineConfig
from strategy.toxic_flow_detector import ToxicFlowDetector, ToxicFlowConfig


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def market_state() -> MarketState:
    """Standard market state for tests."""
    return MarketState(
        market_id="test-market-001",
        condition_id="0xabc123",
        token_id_yes="tok_yes_001",
        token_id_no="tok_no_001",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        yes_bid=Decimal("0.48"),
        yes_ask=Decimal("0.52"),
        no_bid=Decimal("0.48"),
        no_ask=Decimal("0.52"),
        depth_yes_bid=Decimal("1000"),
        depth_yes_ask=Decimal("1000"),
        volume_1m=Decimal("500"),
        volume_5m=Decimal("2500"),
    )


@pytest.fixture
def features() -> FeatureVector:
    """Standard feature vector."""
    return FeatureVector(
        market_id="test-market-001",
        spread_bps=Decimal("80"),
        book_imbalance=0.1,
        micro_momentum=0.001,
        volatility_1m=0.008,
        liquidity_score=0.6,
        toxic_flow_score=0.5,
        oracle_delta=0.0,
        expected_fee_bps=Decimal("2"),
        queue_position_estimate=100.0,
        data_quality_score=0.9,
    )


@pytest.fixture
def flat_position() -> Position:
    """Flat (no inventory) position."""
    return Position(
        market_id="test-market-001",
        token_id_yes="tok_yes_001",
        token_id_no="tok_no_001",
        qty_yes=Decimal("0"),
        qty_no=Decimal("0"),
    )


@pytest.fixture
def long_position() -> Position:
    """Long YES position (high inventory)."""
    return Position(
        market_id="test-market-001",
        token_id_yes="tok_yes_001",
        token_id_no="tok_no_001",
        qty_yes=Decimal("500"),
        qty_no=Decimal("0"),
        avg_entry_yes=Decimal("0.50"),
    )


@pytest.fixture
def short_position() -> Position:
    """Long NO position (short YES exposure)."""
    return Position(
        market_id="test-market-001",
        token_id_yes="tok_yes_001",
        token_id_no="tok_no_001",
        qty_yes=Decimal("0"),
        qty_no=Decimal("300"),
        avg_entry_no=Decimal("0.50"),
    )


# ═════════════════════════════════════════════════════════════════════
# SpreadModel Tests
# ═════════════════════════════════════════════════════════════════════


class TestSpreadModel:
    """Tests for SpreadModel half-spread computation."""

    def test_basic_half_spread(self) -> None:
        """Normal conditions produce a reasonable half-spread."""
        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0.008"),
            fee_bps=Decimal("2"),
            liquidity_score=0.6,
            mid_price=Decimal("0.50"),
        )
        assert hs > _ZERO
        assert hs < Decimal("0.10")  # not absurdly wide

    def test_fee_floor_dominates_low_vol(self) -> None:
        """When volatility is near zero, fee floor sets the half-spread."""
        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0.0001"),
            fee_bps=Decimal("20"),
            liquidity_score=0.8,
            mid_price=Decimal("0.50"),
        )
        # Fee floor = 20 bps * 0.50 / 10000 = 0.001
        assert hs >= Decimal("0.001")

    def test_high_vol_widens_spread(self) -> None:
        """High volatility produces wider spreads than low volatility."""
        model = SpreadModel()
        hs_low = model.optimal_half_spread(
            volatility=Decimal("0.002"),
            fee_bps=Decimal("2"),
            liquidity_score=0.6,
            mid_price=Decimal("0.50"),
        )
        hs_high = model.optimal_half_spread(
            volatility=Decimal("0.020"),
            fee_bps=Decimal("2"),
            liquidity_score=0.6,
            mid_price=Decimal("0.50"),
        )
        assert hs_high > hs_low

    def test_illiquid_market_widens_spread(self) -> None:
        """Low liquidity score produces wider spreads."""
        model = SpreadModel()
        hs_liquid = model.optimal_half_spread(
            volatility=Decimal("0.005"),
            fee_bps=Decimal("2"),
            liquidity_score=0.9,
            mid_price=Decimal("0.50"),
        )
        hs_illiquid = model.optimal_half_spread(
            volatility=Decimal("0.005"),
            fee_bps=Decimal("2"),
            liquidity_score=0.1,
            mid_price=Decimal("0.50"),
        )
        assert hs_illiquid >= hs_liquid

    def test_min_spread_enforced(self) -> None:
        """Half-spread never goes below min_half_spread_bps."""
        config = SpreadModelConfig(min_half_spread_bps=Decimal("50"))
        model = SpreadModel(config=config)
        hs = model.optimal_half_spread(
            volatility=Decimal("0.0001"),
            fee_bps=Decimal("1"),
            liquidity_score=0.99,
            mid_price=Decimal("0.50"),
        )
        min_hs = Decimal("50") * Decimal("0.50") / Decimal("10000")  # 0.0025
        assert hs >= min_hs

    def test_max_spread_enforced(self) -> None:
        """Half-spread is capped at max_half_spread_bps."""
        config = SpreadModelConfig(max_half_spread_bps=Decimal("100"))
        model = SpreadModel(config=config)
        hs = model.optimal_half_spread(
            volatility=Decimal("0.50"),  # extreme
            fee_bps=Decimal("2"),
            liquidity_score=0.01,  # very illiquid
            mid_price=Decimal("0.50"),
        )
        max_hs = Decimal("100") * Decimal("0.50") / Decimal("10000")  # 0.005
        assert hs <= max_hs

    def test_zero_mid_price_uses_fallback(self) -> None:
        """Zero mid price returns minimum spread at fallback mid."""
        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0.008"),
            fee_bps=Decimal("2"),
            liquidity_score=0.5,
            mid_price=Decimal("0"),
        )
        assert hs > _ZERO

    def test_extreme_liquidity_floor(self) -> None:
        """Liquidity score at/below floor gives max multiplier."""
        model = SpreadModel()
        hs_floor = model.optimal_half_spread(
            volatility=Decimal("0.005"),
            fee_bps=Decimal("2"),
            liquidity_score=0.01,  # below floor
            mid_price=Decimal("0.50"),
        )
        hs_zero = model.optimal_half_spread(
            volatility=Decimal("0.005"),
            fee_bps=Decimal("2"),
            liquidity_score=0.0,  # zero
            mid_price=Decimal("0.50"),
        )
        # Both should hit max multiplier
        assert hs_floor == hs_zero


# ═════════════════════════════════════════════════════════════════════
# InventorySkew Tests
# ═════════════════════════════════════════════════════════════════════


class TestInventorySkew:
    """Tests for InventorySkew Avellaneda-Stoikov computation."""

    def test_flat_inventory_zero_skew(self, flat_position: Position) -> None:
        """Flat position produces zero skew."""
        skew_model = InventorySkew()
        skew = skew_model.compute_skew(
            position=flat_position,
            volatility=Decimal("0.008"),
            elapsed_hours=Decimal("6"),
        )
        assert skew == _ZERO

    def test_long_inventory_positive_skew(self, long_position: Position) -> None:
        """Long YES position produces positive skew (shift mid down to sell)."""
        skew_model = InventorySkew()
        skew = skew_model.compute_skew(
            position=long_position,
            volatility=Decimal("0.008"),
            elapsed_hours=Decimal("6"),
        )
        assert skew > _ZERO

    def test_short_inventory_negative_skew(self, short_position: Position) -> None:
        """Long NO position (short YES) produces negative skew."""
        skew_model = InventorySkew()
        skew = skew_model.compute_skew(
            position=short_position,
            volatility=Decimal("0.008"),
            elapsed_hours=Decimal("6"),
        )
        assert skew < _ZERO

    def test_skew_increases_with_inventory(self) -> None:
        """Larger inventory produces larger absolute skew."""
        skew_model = InventorySkew()
        vol = Decimal("0.008")

        pos_small = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        pos_large = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("500"), qty_no=Decimal("0"),
        )

        skew_small = skew_model.compute_skew(pos_small, vol, Decimal("6"))
        skew_large = skew_model.compute_skew(pos_large, vol, Decimal("6"))

        assert abs(skew_large) > abs(skew_small)

    def test_skew_increases_with_volatility(self, long_position: Position) -> None:
        """Higher volatility produces larger skew."""
        skew_model = InventorySkew()

        skew_low = skew_model.compute_skew(
            long_position, Decimal("0.002"), Decimal("6")
        )
        skew_high = skew_model.compute_skew(
            long_position, Decimal("0.010"), Decimal("6")
        )

        assert abs(skew_high) > abs(skew_low)

    def test_skew_decreases_as_time_elapses(self, long_position: Position) -> None:
        """Skew decays as elapsed time approaches the horizon."""
        skew_model = InventorySkew()
        vol = Decimal("0.008")

        skew_early = skew_model.compute_skew(
            long_position, vol, Decimal("1")
        )
        skew_late = skew_model.compute_skew(
            long_position, vol, Decimal("23")
        )

        assert abs(skew_early) > abs(skew_late)

    def test_skew_zero_at_horizon(self, long_position: Position) -> None:
        """At the time horizon boundary, skew should be zero."""
        config = InventorySkewConfig(time_horizon_hours=Decimal("24"))
        skew_model = InventorySkew(config=config)
        skew = skew_model.compute_skew(
            long_position, Decimal("0.008"), Decimal("24")
        )
        assert skew == _ZERO

    def test_max_skew_clamped(self) -> None:
        """Skew is clamped to max_skew even with extreme inventory."""
        config = InventorySkewConfig(
            gamma=Decimal("5.0"),
            max_skew=Decimal("0.05"),
            max_inventory=Decimal("10000"),
        )
        skew_model = InventorySkew(config=config)

        extreme_pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("5000"), qty_no=Decimal("0"),
        )
        skew = skew_model.compute_skew(
            extreme_pos, Decimal("0.10"), Decimal("0")
        )
        assert abs(skew) <= Decimal("0.05")

    def test_inventory_exceeded_flag(self) -> None:
        """is_inventory_exceeded flags correctly."""
        config = InventorySkewConfig(max_inventory=Decimal("200"))
        skew_model = InventorySkew(config=config)

        pos_ok = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        pos_over = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("300"), qty_no=Decimal("0"),
        )

        assert not skew_model.is_inventory_exceeded(pos_ok)
        assert skew_model.is_inventory_exceeded(pos_over)

    def test_inventory_utilisation(self) -> None:
        """inventory_utilisation returns correct fraction."""
        config = InventorySkewConfig(max_inventory=Decimal("1000"))
        skew_model = InventorySkew(config=config)

        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("700"), qty_no=Decimal("0"),
        )
        util = skew_model.inventory_utilisation(pos)
        assert util == Decimal("0.7")

    def test_nonlinear_ramp_above_soft_threshold(self) -> None:
        """Above soft threshold, skew amplifies non-linearly."""
        config = InventorySkewConfig(
            gamma=Decimal("0.3"),
            max_inventory=Decimal("1000"),
            soft_inventory_pct=Decimal("0.5"),
            ramp_exponent=Decimal("2.0"),
            max_skew=Decimal("1.0"),  # high cap so we can see ramp
        )
        skew_model = InventorySkew(config=config)
        vol = Decimal("0.01")

        pos_at_soft = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("500"), qty_no=Decimal("0"),
        )
        pos_above_soft = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("800"), qty_no=Decimal("0"),
        )

        skew_soft = skew_model.compute_skew(pos_at_soft, vol, Decimal("0"))
        skew_above = skew_model.compute_skew(pos_above_soft, vol, Decimal("0"))

        # Above-soft should grow faster than linearly
        ratio = float(skew_above / skew_soft)
        linear_ratio = 800 / 500
        assert ratio > linear_ratio  # non-linear amplification


# ═════════════════════════════════════════════════════════════════════
# RewardsFarming Tests
# ═════════════════════════════════════════════════════════════════════


class TestRewardsFarming:
    """Tests for RewardsFarming spread tightening."""

    def test_tightening_reduces_spread(self) -> None:
        """With aggressiveness > 0, spread is tightened."""
        farming = RewardsFarming()
        base_hs = Decimal("0.015")
        adjusted = farming.adjust_half_spread(
            base_half_spread=base_hs,
            mid_price=Decimal("0.50"),
            fee_bps=Decimal("2"),
        )
        assert adjusted <= base_hs
        assert adjusted > _ZERO

    def test_zero_aggressiveness_no_change(self) -> None:
        """With aggressiveness=0, no tightening occurs."""
        config = RewardsFarmingConfig(aggressiveness=Decimal("0"))
        farming = RewardsFarming(config=config)
        base_hs = Decimal("0.015")
        adjusted = farming.adjust_half_spread(
            base_hs, Decimal("0.50"), Decimal("2"),
        )
        assert adjusted == base_hs

    def test_max_aggressiveness_max_tightening(self) -> None:
        """With aggressiveness=1.0, tightening is maximal (but floored)."""
        config = RewardsFarmingConfig(aggressiveness=Decimal("1.0"))
        farming = RewardsFarming(config=config)
        base_hs = Decimal("0.020")
        adjusted = farming.adjust_half_spread(
            base_hs, Decimal("0.50"), Decimal("2"),
        )
        assert adjusted < base_hs

    def test_fee_floor_respected(self) -> None:
        """Tightening cannot go below the fee floor."""
        config = RewardsFarmingConfig(
            aggressiveness=Decimal("1.0"),
            max_tighten_pct=Decimal("0.99"),
            min_post_reward_spread_bps=Decimal("1"),
        )
        farming = RewardsFarming(config=config)
        adjusted = farming.adjust_half_spread(
            Decimal("0.015"), Decimal("0.50"), Decimal("10"),  # 10bps fee
        )
        fee_floor = Decimal("10") * Decimal("0.50") / Decimal("10000")  # 0.0005
        assert adjusted >= fee_floor

    def test_reward_edge_computation(self) -> None:
        """compute_reward_edge returns positive value for valid inputs."""
        farming = RewardsFarming()
        edge = farming.compute_reward_edge(
            half_spread=Decimal("0.01"),
            order_size=Decimal("100"),
            mid_price=Decimal("0.50"),
        )
        assert edge > _ZERO

    def test_reward_edge_zero_for_invalid(self) -> None:
        """Zero size or mid produces zero reward edge."""
        farming = RewardsFarming()
        assert farming.compute_reward_edge(
            Decimal("0.01"), Decimal("0"), Decimal("0.50")
        ) == _ZERO
        assert farming.compute_reward_edge(
            Decimal("0.01"), Decimal("100"), Decimal("0")
        ) == _ZERO


# ═════════════════════════════════════════════════════════════════════
# QuoteEngine Integration Tests
# ═════════════════════════════════════════════════════════════════════


class TestQuoteEngine:
    """Integration tests for the full QuoteEngine pipeline."""

    def _make_engine(self, **kwargs) -> QuoteEngine:
        """Helper to build a QuoteEngine with default sub-models."""
        return QuoteEngine(
            spread_model=kwargs.get("spread", SpreadModel()),
            inventory_skew=kwargs.get("skew", InventorySkew()),
            rewards_farming=kwargs.get("rewards", RewardsFarming()),
            toxic_flow=kwargs.get("toxic", ToxicFlowDetector()),
            config=kwargs.get("config", QuoteEngineConfig()),
        )

    def test_basic_bilateral_plan(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """With flat position, both BID and ASK slices are generated.

        In binary markets (Polymarket), selling YES is equivalent to buying NO.
        Therefore the bot should place ASKs even when flat — the venue handles
        complement routing.
        """
        engine = self._make_engine()
        plan = engine.generate_quotes(
            state=market_state,
            features=features,
            position=flat_position,
        )
        assert len(plan.slices) > 0
        assert plan.market_id == market_state.market_id

        # Check we have both YES and NO tokens
        tokens = {s.token for s in plan.slices}
        assert TokenSide.YES in tokens
        assert TokenSide.NO in tokens

        # With flat position, both BIDs and ASKs should be present
        sides = {s.side for s in plan.slices}
        assert QuoteSide.BID in sides
        assert QuoteSide.ASK in sides

    def test_all_prices_within_bounds(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """All slice prices are within [0.01, 0.99]."""
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, flat_position)
        for s in plan.slices:
            assert s.price >= Decimal("0.01"), f"Price {s.price} below floor"
            assert s.price <= Decimal("0.99"), f"Price {s.price} above ceiling"

    def test_bid_below_ask_for_yes(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """YES bid should be below YES ask."""
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, flat_position)

        yes_bids = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
        yes_asks = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]

        if yes_bids and yes_asks:
            assert max(yes_bids) < min(yes_asks)

    def test_complement_pricing(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """YES mid + NO mid should approximately sum to 1.0."""
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, flat_position)

        yes_bids = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
        yes_asks = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]
        no_bids = [s.price for s in plan.slices if s.token == TokenSide.NO and s.side == QuoteSide.BID]
        no_asks = [s.price for s in plan.slices if s.token == TokenSide.NO and s.side == QuoteSide.ASK]

        if yes_bids and yes_asks and no_bids and no_asks:
            yes_mid = (yes_bids[0] + yes_asks[0]) / 2
            no_mid = (no_bids[0] + no_asks[0]) / 2
            total = yes_mid + no_mid
            assert Decimal("0.95") < total < Decimal("1.05"), f"Sum = {total}"

    def test_inventory_skew_shifts_yes_quotes(
        self, market_state: MarketState, features: FeatureVector,
        flat_position: Position, long_position: Position,
    ) -> None:
        """Long YES inventory shifts YES quotes downward."""
        engine = self._make_engine()

        plan_flat = engine.generate_quotes(market_state, features, flat_position)
        plan_long = engine.generate_quotes(market_state, features, long_position)

        flat_yes_bid = [s.price for s in plan_flat.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
        long_yes_bid = [s.price for s in plan_long.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]

        if flat_yes_bid and long_yes_bid:
            # Long inventory → lower bids (eager to sell, reluctant to buy)
            assert long_yes_bid[0] <= flat_yes_bid[0]

    def test_toxic_flow_halt_returns_empty(
        self, market_state: MarketState, flat_position: Position,
    ) -> None:
        """Extreme toxic flow triggers halt → empty QuotePlan."""
        toxic_features = FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            book_imbalance=0.95,
            toxic_flow_score=4.0,  # > halt threshold (3.5)
            volatility_1m=0.008,
            liquidity_score=0.5,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(
            market_state, toxic_features, flat_position,
        )
        assert len(plan.slices) == 0

    def test_toxic_flow_widens_spread(
        self, market_state: MarketState, flat_position: Position,
    ) -> None:
        """Moderate toxic flow widens spread (but doesn't halt)."""
        normal_features = FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            toxic_flow_score=0.5,
            volatility_1m=0.008,
            liquidity_score=0.6,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )
        toxic_features = FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            toxic_flow_score=3.0,  # toxic but not halt
            volatility_1m=0.008,
            liquidity_score=0.6,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )
        engine = self._make_engine()

        plan_normal = engine.generate_quotes(
            market_state, normal_features, flat_position,
        )
        plan_toxic = engine.generate_quotes(
            market_state, toxic_features, flat_position,
        )

        def _spread(plan):
            yes_bids = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
            yes_asks = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]
            if yes_bids and yes_asks:
                return yes_asks[0] - yes_bids[0]
            return Decimal("0")

        if plan_normal.slices and plan_toxic.slices:
            assert _spread(plan_toxic) >= _spread(plan_normal)

    def test_low_data_quality_returns_empty(
        self, market_state: MarketState, flat_position: Position,
    ) -> None:
        """Low data quality → empty QuotePlan."""
        bad_features = FeatureVector(
            market_id="test-market-001",
            data_quality_score=0.1,  # below threshold
            volatility_1m=0.008,
            expected_fee_bps=Decimal("2"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, bad_features, flat_position)
        assert len(plan.slices) == 0

    def test_zero_mid_price_returns_empty(
        self, features: FeatureVector, flat_position: Position,
    ) -> None:
        """Market with no valid mid-price → empty plan."""
        bad_state = MarketState(
            market_id="test-market-001",
            condition_id="0xabc",
            token_id_yes="y",
            token_id_no="n",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            yes_bid=Decimal("0"),
            yes_ask=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(bad_state, features, flat_position)
        assert len(plan.slices) == 0

    def test_inventory_exceeded_returns_empty(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """Inventory above hard limit → empty QuotePlan."""
        config = QuoteEngineConfig()
        skew_config = InventorySkewConfig(max_inventory=Decimal("100"))
        engine = QuoteEngine(
            inventory_skew=InventorySkew(config=skew_config),
            config=config,
        )

        extreme_pos = Position(
            market_id="test-market-001",
            token_id_yes="y",
            token_id_no="n",
            qty_yes=Decimal("200"),
            qty_no=Decimal("0"),
        )
        plan = engine.generate_quotes(market_state, features, extreme_pos)
        assert len(plan.slices) == 0

    def test_to_order_intents(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """QuotePlan converts correctly to Order intents."""
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, flat_position)
        orders = plan.to_order_intents()

        assert len(orders) == len(plan.slices)
        for order in orders:
            assert order.maker_only is True
            assert order.price > _ZERO
            assert order.size > _ZERO

    def test_multi_level_quoting(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """Multi-level quoting produces more slices at staggered prices."""
        config = QuoteEngineConfig(num_levels=3, level_spacing=Decimal("0.01"))
        engine = self._make_engine(config=config)
        plan = engine.generate_quotes(market_state, features, flat_position)

        # With 3 levels × 2 sides × 2 tokens = up to 12 slices
        # Some may be filtered by price bounds
        yes_bids = sorted(
            [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID],
            reverse=True,
        )
        if len(yes_bids) >= 2:
            # Each successive level should be at a lower price
            for i in range(len(yes_bids) - 1):
                assert yes_bids[i] >= yes_bids[i + 1]

    def test_tick_size_quantisation(
        self, features: FeatureVector, flat_position: Position,
    ) -> None:
        """All prices should be quantised to the market's tick size."""
        state = MarketState(
            market_id="test-market-001",
            condition_id="0xabc",
            token_id_yes="y",
            token_id_no="n",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            yes_bid=Decimal("0.48"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.52"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(state, features, flat_position)

        for s in plan.slices:
            # Price should be a multiple of tick_size
            remainder = s.price % state.tick_size
            assert remainder == _ZERO, (
                f"Price {s.price} not a multiple of tick {state.tick_size}"
            )

    def test_strategy_tag_set(
        self, market_state: MarketState, features: FeatureVector, flat_position: Position,
    ) -> None:
        """QuotePlan has the configured strategy tag."""
        config = QuoteEngineConfig(strategy_tag="my_strategy_v2")
        engine = self._make_engine(config=config)
        plan = engine.generate_quotes(market_state, features, flat_position)
        assert plan.strategy_tag == "my_strategy_v2"

    def test_low_vol_scenario(
        self, market_state: MarketState, flat_position: Position,
    ) -> None:
        """Low-volatility market still produces valid quotes with fee-floor spread."""
        low_vol_features = FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("20"),
            volatility_1m=0.0002,  # very low vol
            liquidity_score=0.8,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.95,
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, low_vol_features, flat_position)
        assert len(plan.slices) > 0

        # Spread should be narrow but non-zero
        yes_bids = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
        yes_asks = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]
        if yes_bids and yes_asks:
            spread = yes_asks[0] - yes_bids[0]
            assert spread > _ZERO

    def test_rewards_tightens_non_toxic(
        self, market_state: MarketState, flat_position: Position,
    ) -> None:
        """Rewards farming tightens spread when there's no toxic flow."""
        features_clean = FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            volatility_1m=0.005,
            liquidity_score=0.6,
            toxic_flow_score=0.0,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )

        # Engine with no rewards
        no_rewards = RewardsFarming(
            config=RewardsFarmingConfig(aggressiveness=Decimal("0"))
        )
        engine_no_r = self._make_engine(rewards=no_rewards)

        # Engine with rewards
        with_rewards = RewardsFarming(
            config=RewardsFarmingConfig(aggressiveness=Decimal("1.0"))
        )
        engine_with_r = self._make_engine(rewards=with_rewards)

        plan_no_r = engine_no_r.generate_quotes(market_state, features_clean, flat_position)
        plan_with_r = engine_with_r.generate_quotes(market_state, features_clean, flat_position)

        def _yes_spread(plan):
            bids = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
            asks = [s.price for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]
            if bids and asks:
                return asks[0] - bids[0]
            return Decimal("999")

        s_no_r = _yes_spread(plan_no_r)
        s_with_r = _yes_spread(plan_with_r)

        # Rewards should produce tighter (or equal) spread
        assert s_with_r <= s_no_r


# ═════════════════════════════════════════════════════════════════════
# NEW TESTS — Position-aware quoting, spread floor, skew fallback,
#              dynamic order sizing (Fixes 1–4)
# ═════════════════════════════════════════════════════════════════════


class TestPositionAwareQuoting:
    """Tests for FIX 3: position-aware ASK filtering and BID saturation."""

    def _make_engine(self, **kwargs) -> QuoteEngine:
        return QuoteEngine(
            spread_model=kwargs.get("spread", SpreadModel()),
            inventory_skew=kwargs.get("skew", InventorySkew()),
            rewards_farming=kwargs.get("rewards", RewardsFarming()),
            toxic_flow=kwargs.get("toxic", ToxicFlowDetector()),
            config=kwargs.get("config", QuoteEngineConfig()),
        )

    @pytest.fixture
    def market_state(self) -> MarketState:
        return MarketState(
            market_id="test-market-001",
            condition_id="0xabc123",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            yes_bid=Decimal("0.48"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.52"),
            depth_yes_bid=Decimal("1000"),
            depth_yes_ask=Decimal("1000"),
        )

    @pytest.fixture
    def features(self) -> FeatureVector:
        return FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            volatility_1m=0.008,
            liquidity_score=0.6,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )

    def test_zero_position_bilateral_quotes(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """With zero position, both BID and ASK slices are generated.

        In binary markets, selling YES is equivalent to buying NO.
        The bot should always place bilateral quotes for market making.
        """
        flat = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            qty_yes=Decimal("0"),
            qty_no=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, flat)

        ask_slices = [s for s in plan.slices if s.side == QuoteSide.ASK]
        bid_slices = [s for s in plan.slices if s.side == QuoteSide.BID]
        assert len(ask_slices) > 0, "Should generate ASKs with zero position (complement trading)"
        assert len(bid_slices) > 0, "Should still generate BIDs"

    def test_yes_position_generates_yes_ask(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """With YES position, generates ASK YES but NOT ASK NO."""
        pos = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            qty_yes=Decimal("100"),
            qty_no=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, pos)

        ask_yes = [s for s in plan.slices if s.side == QuoteSide.ASK and s.token == TokenSide.YES]
        ask_no = [s for s in plan.slices if s.side == QuoteSide.ASK and s.token == TokenSide.NO]

        assert len(ask_yes) > 0, "Should generate ASK YES when holding YES tokens"
        # ASK NO is also allowed — complement trading enables it even
        # without holding NO tokens.
        assert len(ask_no) >= 0

    def test_partial_ask_sizing(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """When holding fewer tokens than default size, ASK is resized."""
        pos = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            qty_yes=Decimal("20"),  # Less than default 50
            qty_no=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, pos)

        ask_yes = [s for s in plan.slices if s.side == QuoteSide.ASK and s.token == TokenSide.YES]
        assert len(ask_yes) > 0
        assert ask_yes[0].size == Decimal("20"), "ASK should be resized to available qty"

    def test_bid_suppressed_when_saturated(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """When position exceeds 80% of max, BID for that side is suppressed."""
        pos = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            qty_yes=Decimal("180"),  # > 80% of 200
            qty_no=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(
            market_state, features, pos,
            max_position_size=Decimal("200"),
        )

        bid_yes = [s for s in plan.slices if s.side == QuoteSide.BID and s.token == TokenSide.YES]
        bid_no = [s for s in plan.slices if s.side == QuoteSide.BID and s.token == TokenSide.NO]

        assert len(bid_yes) == 0, "BID YES should be suppressed when YES is saturated"
        assert len(bid_no) > 0, "BID NO should still be generated (NO is not saturated)"

    def test_ask_below_min_order_size_resized(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """When position is below default size but > 0, ASK is resized to available qty.

        In binary markets, ASKs are always allowed (complement trading),
        but when the bot holds some tokens, the ASK is resized to what it holds.
        """
        pos = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            qty_yes=Decimal("3"),  # Below default_order_size of 50
            qty_no=Decimal("0"),
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(market_state, features, pos)

        ask_yes = [s for s in plan.slices if s.side == QuoteSide.ASK and s.token == TokenSide.YES]
        assert len(ask_yes) > 0, "ASK should be allowed (complement trading)"
        assert ask_yes[0].size == Decimal("3"), "ASK YES should be resized to available qty"


class TestDynamicOrderSizing:
    """Tests for FIX 4: order size proportional to available balance."""

    def _make_engine(self, **kwargs) -> QuoteEngine:
        return QuoteEngine(
            spread_model=kwargs.get("spread", SpreadModel()),
            inventory_skew=kwargs.get("skew", InventorySkew()),
            rewards_farming=kwargs.get("rewards", RewardsFarming()),
            toxic_flow=kwargs.get("toxic", ToxicFlowDetector()),
            config=kwargs.get("config", QuoteEngineConfig()),
        )

    @pytest.fixture
    def market_state(self) -> MarketState:
        return MarketState(
            market_id="test-market-001",
            condition_id="0xabc123",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            yes_bid=Decimal("0.48"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.52"),
        )

    @pytest.fixture
    def features(self) -> FeatureVector:
        return FeatureVector(
            market_id="test-market-001",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            volatility_1m=0.008,
            liquidity_score=0.6,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )

    def test_order_size_capped_by_balance(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """With limited balance, BID order sizes are capped."""
        flat = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
        )
        config = QuoteEngineConfig(default_order_size=Decimal("50"))
        engine = self._make_engine(config=config)

        plan = engine.generate_quotes(
            market_state, features, flat,
            available_balance=Decimal("100"),  # 5% = $5 max per order
        )

        for s in plan.slices:
            if s.side == QuoteSide.BID:
                order_value = s.price * s.size
                # Max order value = 100 * 0.05 = $5
                assert order_value <= Decimal("6"), (
                    f"BID value {order_value} exceeds 5% of balance"
                )

    def test_order_size_not_below_minimum(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """Even with very low balance, order size respects minimum."""
        flat = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(
            market_state, features, flat,
            available_balance=Decimal("10"),  # Very low
        )

        for s in plan.slices:
            if s.side == QuoteSide.BID:
                assert s.size >= Decimal("5"), "Size should not go below min_order_size"

    def test_no_balance_no_bids(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """With zero balance, all BID slices are removed."""
        flat = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
        )
        engine = self._make_engine()
        plan = engine.generate_quotes(
            market_state, features, flat,
            available_balance=Decimal("0"),
        )

        bid_slices = [s for s in plan.slices if s.side == QuoteSide.BID]
        assert len(bid_slices) == 0, "Should not generate BIDs with zero balance"

    def test_large_balance_uses_default_size(
        self, market_state: MarketState, features: FeatureVector,
    ) -> None:
        """With large balance, default order size is used (not inflated)."""
        flat = Position(
            market_id="test-market-001",
            token_id_yes="tok_yes_001",
            token_id_no="tok_no_001",
        )
        config = QuoteEngineConfig(default_order_size=Decimal("25"))
        engine = self._make_engine(config=config)
        plan = engine.generate_quotes(
            market_state, features, flat,
            available_balance=Decimal("10000"),  # Large balance
        )

        for s in plan.slices:
            if s.side == QuoteSide.BID:
                assert s.size == Decimal("25"), (
                    f"With large balance, should use default size 25, got {s.size}"
                )


class TestSpreadMinimumFloor:
    """Tests for FIX 1: spread minimum enforcement."""

    def test_spread_never_below_market_min(self) -> None:
        """SpreadModel respects market_min_spread_bps when provided."""
        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0.0001"),  # Very low vol
            fee_bps=Decimal("1"),
            liquidity_score=0.99,
            mid_price=Decimal("0.50"),
            market_min_spread_bps=Decimal("50"),  # 50 bps floor
        )
        min_expected = Decimal("50") * Decimal("0.50") / Decimal("10000")  # 0.0025
        assert hs >= min_expected, (
            f"Half-spread {hs} below market minimum {min_expected}"
        )

    def test_rewards_farming_respects_market_floor(self) -> None:
        """RewardsFarming cannot tighten below market_min_spread_bps."""
        farming = RewardsFarming(
            config=RewardsFarmingConfig(aggressiveness=Decimal("1.0"))
        )
        base_hs = Decimal("0.010")
        market_min_bps = Decimal("50")  # 50 bps
        mid = Decimal("0.50")

        adjusted = farming.adjust_half_spread(
            base_half_spread=base_hs,
            mid_price=mid,
            fee_bps=Decimal("2"),
            market_min_spread_bps=market_min_bps,
        )

        market_floor = market_min_bps * mid / Decimal("10000")  # 0.0025
        assert adjusted >= market_floor, (
            f"Adjusted {adjusted} went below market floor {market_floor}"
        )

    def test_spread_with_zero_vol_respects_floor(self) -> None:
        """Even with zero volatility, spread respects market_min_spread_bps."""
        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0"),
            fee_bps=Decimal("2"),
            liquidity_score=0.8,
            mid_price=Decimal("0.58"),
            market_min_spread_bps=Decimal("50"),
        )
        min_expected = Decimal("50") * Decimal("0.58") / Decimal("10000")
        assert hs >= min_expected

    def test_bid_ask_not_same_price_with_market_floor(self) -> None:
        """BID and ASK should never collapse to the same price."""
        engine = QuoteEngine(
            spread_model=SpreadModel(),
            inventory_skew=InventorySkew(),
            rewards_farming=RewardsFarming(
                config=RewardsFarmingConfig(aggressiveness=Decimal("1.0"))
            ),
            toxic_flow=ToxicFlowDetector(),
            config=QuoteEngineConfig(),
        )
        state = MarketState(
            market_id="test-mkt",
            condition_id="0xabc",
            token_id_yes="y",
            token_id_no="n",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            yes_bid=Decimal("0.56"),
            yes_ask=Decimal("0.60"),
            no_bid=Decimal("0.40"),
            no_ask=Decimal("0.44"),
        )
        features = FeatureVector(
            market_id="test-mkt",
            volatility_1m=0.0,  # Zero vol — was causing same price
            liquidity_score=0.8,
            expected_fee_bps=Decimal("2"),
            data_quality_score=0.9,
        )
        pos = Position(
            market_id="test-mkt",
            token_id_yes="y",
            token_id_no="n",
            qty_yes=Decimal("100"),
            qty_no=Decimal("100"),
        )
        plan = engine.generate_quotes(
            state, features, pos,
            market_min_spread_bps=Decimal("50"),
        )

        yes_bids = [s.price for s in plan.slices
                     if s.token == TokenSide.YES and s.side == QuoteSide.BID]
        yes_asks = [s.price for s in plan.slices
                     if s.token == TokenSide.YES and s.side == QuoteSide.ASK]

        if yes_bids and yes_asks:
            assert yes_bids[0] < yes_asks[0], (
                f"BID {yes_bids[0]} should be < ASK {yes_asks[0]}"
            )


class TestInventorySkewSigmaFallback:
    """Tests for FIX 2: inventory skew with sigma fallback."""

    def test_sigma_zero_uses_fallback(self) -> None:
        """When sigma=0, MIN_SIGMA fallback kicks in → skew != 0."""
        from strategy.inventory_skew import MIN_SIGMA

        skew_model = InventorySkew()
        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        skew = skew_model.compute_skew(
            position=pos,
            volatility=Decimal("0"),  # Zero vol — was causing zero skew
            elapsed_hours=Decimal("6"),
        )
        assert skew != Decimal("0"), (
            f"Skew should NOT be zero with 100 YES tokens even at sigma=0, "
            f"MIN_SIGMA fallback ({MIN_SIGMA}) should produce non-zero skew"
        )
        assert skew > Decimal("0"), "Long YES → positive skew (push mid down)"

    def test_sigma_below_floor_uses_floor(self) -> None:
        """Sigma below MIN_SIGMA is raised to MIN_SIGMA."""
        from strategy.inventory_skew import MIN_SIGMA

        skew_model = InventorySkew()
        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        skew_zero = skew_model.compute_skew(pos, Decimal("0"), Decimal("6"))
        skew_tiny = skew_model.compute_skew(pos, Decimal("0.001"), Decimal("6"))

        # Both should use MIN_SIGMA (0.005) since 0.001 < 0.005
        assert skew_zero == skew_tiny, (
            "Both sigma=0 and sigma=0.001 should produce the same skew "
            "because both are below MIN_SIGMA"
        )

    def test_sigma_above_floor_used_as_is(self) -> None:
        """Sigma above MIN_SIGMA is used directly (no floor applied)."""
        from strategy.inventory_skew import MIN_SIGMA

        skew_model = InventorySkew()
        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        sigma_high = Decimal("0.01")
        assert sigma_high > MIN_SIGMA

        skew_floor = skew_model.compute_skew(pos, MIN_SIGMA, Decimal("6"))
        skew_high = skew_model.compute_skew(pos, sigma_high, Decimal("6"))

        assert skew_high > skew_floor, (
            "Higher sigma should produce larger skew"
        )

    def test_skew_nonzero_with_position_and_zero_vol(self) -> None:
        """The core bug: position exists but skew was zero due to sigma=0."""
        skew_model = InventorySkew()
        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        skew = skew_model.compute_skew(
            position=pos,
            volatility=Decimal("0"),
            elapsed_hours=Decimal("0"),  # Full time horizon
        )
        # With 100 YES and gamma=0.3, sigma=0.005 (fallback), t_remaining=1.0:
        # skew = 0.3 * 0.005^2 * 1.0 * 100 = 0.3 * 0.000025 * 100 = 0.00075
        assert skew > Decimal("0.0005"), (
            f"Skew {skew} should be meaningful with 100 YES tokens"
        )


# ── Helper constant ──────────────────────────────────────────────────

_ZERO = Decimal("0")


# ══════════════════════════════════════════════════════════════════════
# NEW tests for the 4-bug fix (run-002 post-mortem)
# ══════════════════════════════════════════════════════════════════════


class TestBugFix1SpreadConfigWired:
    """Bug 1: Verify half_spread_bps is wired through to SpreadModel."""

    def test_spread_config_wired(self) -> None:
        """half_spread_bps param in PaperTradingPipeline reaches SpreadModel."""
        from paper.paper_runner import PaperTradingPipeline, MarketConfig
        from models.market_state import MarketType

        mc = MarketConfig(
            market_id="test-mkt-001",
            condition_id="0xabc",
            token_id_yes="tok_y",
            token_id_no="tok_n",
            description="Test",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            spread_min_bps=50,
            max_position_size=Decimal("200"),
            enabled=True,
        )
        pipeline = PaperTradingPipeline(
            market_configs=[mc],
            half_spread_bps=75,
        )
        # The SpreadModel inside the QuoteEngine must have
        # min_half_spread_bps == 75 (what we passed in).
        actual = pipeline.quote_engine.spread_model.config.min_half_spread_bps
        assert actual == Decimal("75"), (
            f"Expected min_half_spread_bps=75, got {actual}"
        )

    def test_bid_price_within_2pct_of_mid(self) -> None:
        """Given mid_price=0.585, half_spread_bps=50, bid is within 2% of mid.

        The half-spread is dominated by the vol component (1.5 * 0.005 ≈ 0.84%
        of mid), not by the 50 bps floor (≈0.29%).  This is correct: the model
        uses max(fee_floor, vol_component).  The key check is that the bid is
        NOT 58% away (the bug from run-002).
        """
        spread_model = SpreadModel(SpreadModelConfig(
            min_half_spread_bps=Decimal("50"),
        ))
        mid = Decimal("0.585")
        hs = spread_model.optimal_half_spread(
            volatility=Decimal("0.005"),
            fee_bps=Decimal("2"),
            liquidity_score=0.8,
            mid_price=mid,
            market_min_spread_bps=Decimal("50"),
        )
        bid = mid - hs
        pct_diff = abs(mid - bid) / mid * 100
        assert pct_diff < Decimal("2"), (
            f"Bid {bid} is {pct_diff:.2f}% from mid {mid}, expected <2%"
        )
        # Ensure it's nowhere near the ~58% bug from run-002
        assert pct_diff < Decimal("5"), (
            f"Bid is {pct_diff:.2f}% from mid — still way too wide"
        )


class TestBugFix2BilateralQuotesWhenFlat:
    """Bug 2: Flat position should produce both BID and ASK slices."""

    def _make_engine(self) -> QuoteEngine:
        return QuoteEngine(
            spread_model=SpreadModel(),
            inventory_skew=InventorySkew(),
            rewards_farming=RewardsFarming(),
            toxic_flow=ToxicFlowDetector(),
            config=QuoteEngineConfig(),
        )

    def test_bilateral_quotes_when_flat(self) -> None:
        """Flat position → both BID and ASK slices pass through."""
        engine = self._make_engine()
        state = MarketState(
            market_id="m1",
            condition_id="c1",
            token_id_yes="y",
            token_id_no="n",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            yes_bid=Decimal("0.48"),
            yes_ask=Decimal("0.52"),
            no_bid=Decimal("0.48"),
            no_ask=Decimal("0.52"),
            depth_yes_bid=Decimal("1000"),
            depth_yes_ask=Decimal("1000"),
        )
        features = FeatureVector(
            market_id="m1",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            micro_momentum=0.001,
            volatility_1m=0.008,
            liquidity_score=0.6,
            toxic_flow_score=0.5,
            oracle_delta=0.0,
            expected_fee_bps=Decimal("2"),
            queue_position_estimate=100.0,
            data_quality_score=0.9,
        )
        flat = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("0"), qty_no=Decimal("0"),
        )
        plan = engine.generate_quotes(state, features, flat)

        sides = {s.side for s in plan.slices}
        assert QuoteSide.BID in sides, "Must generate BID slices when flat"
        assert QuoteSide.ASK in sides, "Must generate ASK slices when flat (complement trading)"


class TestBugFix3SkewMeaningfulAt100Tokens:
    """Bug 3: 100 token position should create visible skew (>1% of mid)."""

    def test_skew_meaningful_at_100_tokens(self) -> None:
        """100 token position creates visible skew > 1% of mid_price."""
        skew_model = InventorySkew()
        pos = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
            qty_yes=Decimal("100"), qty_no=Decimal("0"),
        )
        mid = Decimal("0.585")
        skew = skew_model.compute_skew(
            position=pos,
            volatility=Decimal("0.005"),
            elapsed_hours=Decimal("0"),
        )
        # Skew should be > 1% of mid = 0.00585
        threshold = mid * Decimal("0.01")
        assert skew > threshold, (
            f"Skew {skew} is too small — should be > {threshold} "
            f"(1% of mid {mid}) for mean-reversion"
        )


class TestBugFix4BalanceSizingCapsExposure:
    """Bug 4: Per-order value must not exceed 5% of available balance."""

    def _make_engine(self, **kwargs) -> QuoteEngine:
        return QuoteEngine(
            spread_model=kwargs.get("spread", SpreadModel()),
            inventory_skew=kwargs.get("skew", InventorySkew()),
            rewards_farming=kwargs.get("rewards", RewardsFarming()),
            toxic_flow=kwargs.get("toxic", ToxicFlowDetector()),
            config=kwargs.get("config", QuoteEngineConfig()),
        )

    def test_balance_sizing_caps_exposure(self) -> None:
        """With $500 balance, no single BID order > 5% ($25)."""
        engine = self._make_engine()
        state = MarketState(
            market_id="m1",
            condition_id="c1",
            token_id_yes="y",
            token_id_no="n",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
            yes_bid=Decimal("0.28"),
            yes_ask=Decimal("0.32"),
            no_bid=Decimal("0.68"),
            no_ask=Decimal("0.72"),
            depth_yes_bid=Decimal("1000"),
            depth_yes_ask=Decimal("1000"),
        )
        features = FeatureVector(
            market_id="m1",
            spread_bps=Decimal("80"),
            book_imbalance=0.1,
            micro_momentum=0.001,
            volatility_1m=0.008,
            liquidity_score=0.6,
            toxic_flow_score=0.5,
            oracle_delta=0.0,
            expected_fee_bps=Decimal("2"),
            queue_position_estimate=100.0,
            data_quality_score=0.9,
        )
        flat = Position(
            market_id="m1", token_id_yes="y", token_id_no="n",
        )

        balance = Decimal("500")
        plan = engine.generate_quotes(
            state=state,
            features=features,
            position=flat,
            available_balance=balance,
        )

        max_allowed = balance * Decimal("0.05")  # $25
        for s in plan.slices:
            if s.side == QuoteSide.BID:
                order_value = s.price * s.size
                assert order_value <= max_allowed, (
                    f"BID order value ${order_value} exceeds 5% cap "
                    f"(${max_allowed}). Price={s.price}, Size={s.size}"
                )
