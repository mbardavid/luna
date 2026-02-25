"""Tests for strategy.complete_set — Complete-set arbitrage state machine."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from models.market_state import MarketState
from strategy.complete_set import (
    ArbitrageDirection,
    ArbitrageSignal,
    CompleteSetConfig,
    CompleteSetStrategy,
    InvalidTransitionError,
    LegOrder,
    PairState,
    PairTrade,
    VALID_TRANSITIONS,
)


# ── Helpers ──────────────────────────────────────────────────────────

_ZERO = Decimal("0")
_ONE = Decimal("1")


def _make_market_state(
    yes_bid: Decimal | None = None,
    yes_ask: Decimal | None = None,
    no_bid: Decimal | None = None,
    no_ask: Decimal | None = None,
    depth: Decimal = Decimal("500"),
    market_id: str = "test-market",
    condition_id: str = "0x" + "aa" * 32,
) -> MarketState:
    """Create a MarketState for testing.

    Automatically sets bid = ask - 0.01 if bid is not provided,
    to satisfy the yes_ask >= yes_bid validator.
    """
    if yes_ask is None:
        yes_ask = Decimal("0.46")
    if yes_bid is None:
        yes_bid = yes_ask - Decimal("0.01")
    if no_ask is None:
        no_ask = Decimal("0.46")
    if no_bid is None:
        no_bid = no_ask - Decimal("0.01")

    return MarketState(
        market_id=market_id,
        condition_id=condition_id,
        token_id_yes="token_yes_123",
        token_id_no="token_no_456",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        depth_yes_bid=depth,
        depth_yes_ask=depth,
        depth_no_bid=depth,
        depth_no_ask=depth,
    )


@pytest.fixture
def strategy() -> CompleteSetStrategy:
    """Create a strategy with test configuration."""
    return CompleteSetStrategy(
        config=CompleteSetConfig(
            min_profit_usd=Decimal("0.50"),
            gas_cost_per_operation_usd=Decimal("1.00"),
            max_trade_size_usd=Decimal("500"),
            min_trade_size_usd=Decimal("10"),
            clob_fee_bps=Decimal("0"),
            slippage_buffer_bps=Decimal("10"),
            max_concurrent_trades=3,
            max_trade_duration_s=300.0,
        )
    )


# ── Configuration Tests ─────────────────────────────────────────────


class TestCompleteSetConfig:
    """Tests for CompleteSetConfig."""

    def test_default_values(self) -> None:
        """Should have sensible defaults."""
        cfg = CompleteSetConfig()
        assert cfg.min_profit_usd == Decimal("0.50")
        assert cfg.gas_cost_per_operation_usd == Decimal("1.00")
        assert cfg.max_concurrent_trades == 3

    def test_custom_values(self) -> None:
        """Should accept custom values."""
        cfg = CompleteSetConfig(
            min_profit_usd=Decimal("1.00"),
            max_trade_size_usd=Decimal("1000"),
        )
        assert cfg.min_profit_usd == Decimal("1.00")
        assert cfg.max_trade_size_usd == Decimal("1000")


# ── Opportunity Detection ────────────────────────────────────────────


class TestEvaluateMerge:
    """Tests for merge opportunity detection."""

    def test_no_opportunity_fair_price(self, strategy: CompleteSetStrategy) -> None:
        """No opportunity when YES + NO ≈ 1.0."""
        state = _make_market_state(
            yes_ask=Decimal("0.50"),
            no_ask=Decimal("0.50"),
        )
        signal = strategy.evaluate(state)
        assert signal is None

    def test_merge_opportunity_detected(self, strategy: CompleteSetStrategy) -> None:
        """Detect merge when YES_ask + NO_ask < 1.0 (large enough margin)."""
        state = _make_market_state(
            yes_bid=Decimal("0.39"),
            yes_ask=Decimal("0.40"),
            no_bid=Decimal("0.39"),
            no_ask=Decimal("0.40"),
            depth=Decimal("500"),
        )
        # Combined ask = 0.80, margin per pair = 0.20 - costs
        signal = strategy.evaluate(state)
        assert signal is not None
        assert signal.direction == ArbitrageDirection.MERGE
        assert signal.combined_price == Decimal("0.80")
        assert signal.margin > _ZERO

    def test_no_merge_too_expensive(self, strategy: CompleteSetStrategy) -> None:
        """No merge when combined ask is very close to 1.0 and size is small."""
        state = _make_market_state(
            yes_ask=Decimal("0.50"),
            no_ask=Decimal("0.50"),
            depth=Decimal("20"),  # Small depth
        )
        # Combined = 1.00, margin = 0, no opportunity
        signal = strategy.evaluate(state)
        assert signal is None

    def test_no_opportunity_zero_prices(self, strategy: CompleteSetStrategy) -> None:
        """No opportunity when prices are zero."""
        state = _make_market_state(
            yes_bid=_ZERO, yes_ask=_ZERO,
            no_bid=_ZERO, no_ask=_ZERO,
        )
        signal = strategy.evaluate(state)
        assert signal is None

    def test_merge_respects_depth(self, strategy: CompleteSetStrategy) -> None:
        """Size should be limited by book depth."""
        state = _make_market_state(
            yes_bid=Decimal("0.39"),
            yes_ask=Decimal("0.40"),
            no_bid=Decimal("0.39"),
            no_ask=Decimal("0.40"),
            depth=Decimal("50"),  # Small depth
        )
        signal = strategy.evaluate(state)
        assert signal is not None
        assert signal.max_size <= Decimal("50")

    def test_no_opportunity_below_min_size(self, strategy: CompleteSetStrategy) -> None:
        """No opportunity if depth is below min trade size."""
        state = _make_market_state(
            yes_bid=Decimal("0.39"),
            yes_ask=Decimal("0.40"),
            no_bid=Decimal("0.39"),
            no_ask=Decimal("0.40"),
            depth=Decimal("5"),  # Below min_trade_size_usd=10
        )
        signal = strategy.evaluate(state)
        assert signal is None


class TestEvaluateSplit:
    """Tests for split (reverse) opportunity detection."""

    def test_split_opportunity_detected(self, strategy: CompleteSetStrategy) -> None:
        """Detect split when YES_bid + NO_bid > 1.0 (large enough margin)."""
        state = _make_market_state(
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.61"),
            no_bid=Decimal("0.60"),
            no_ask=Decimal("0.61"),
            depth=Decimal("500"),
        )
        # Combined bid = 1.20, margin = 0.20 - costs
        signal = strategy.evaluate(state)
        assert signal is not None
        assert signal.direction == ArbitrageDirection.SPLIT
        assert signal.combined_price == Decimal("1.20")

    def test_no_split_close_to_one(self, strategy: CompleteSetStrategy) -> None:
        """No split when combined bid is close to 1.0."""
        state = _make_market_state(
            yes_bid=Decimal("0.50"),
            yes_ask=Decimal("0.51"),
            no_bid=Decimal("0.50"),
            no_ask=Decimal("0.51"),
        )
        # Combined bid = 1.00, margin = 0 - costs < 0
        signal = strategy.evaluate(state)
        # Should be None or a MERGE signal, not a SPLIT
        if signal is not None:
            assert signal.direction != ArbitrageDirection.SPLIT


class TestConcurrentTradeLimit:
    """Tests for concurrent trade limits."""

    def test_respects_max_concurrent(self, strategy: CompleteSetStrategy) -> None:
        """Should not detect opportunity if max concurrent trades reached."""
        state = _make_market_state(
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.40"),
        )

        # Plan 3 trades (max)
        for _ in range(3):
            signal = strategy.evaluate(state)
            assert signal is not None
            strategy.plan_trade(signal, state)

        # 4th should be blocked
        signal = strategy.evaluate(state)
        assert signal is None


class TestGasCostOverride:
    """Tests for custom gas cost in evaluate."""

    def test_high_gas_kills_opportunity(self, strategy: CompleteSetStrategy) -> None:
        """High gas cost should eliminate marginal opportunities."""
        state = _make_market_state(
            yes_ask=Decimal("0.48"),
            no_ask=Decimal("0.48"),
            depth=Decimal("100"),
        )
        # Combined = 0.96, margin ≈ 0.04 per pair, $4 total for 100 pairs
        # With $1 gas: $4 - $1 = $3 > $0.50 → opportunity
        signal_cheap = strategy.evaluate(state, gas_cost_usd=Decimal("1.00"))
        assert signal_cheap is not None

        # With $100 gas: $4 - ($100/100) = $3 → still profitable per pair,
        # but gas_per_pair = $1, total only $3
        signal_expensive = strategy.evaluate(state, gas_cost_usd=Decimal("100"))
        # $100 gas / 100 pairs = $1 per pair, margin = 0.04 - 0.001 - $1 < 0
        assert signal_expensive is None


# ── Trade Planning ───────────────────────────────────────────────────


class TestPlanTrade:
    """Tests for trade planning."""

    def test_plan_merge_trade(self, strategy: CompleteSetStrategy) -> None:
        """Planning a merge trade should create proper legs."""
        state = _make_market_state(
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.45"),
        )
        signal = strategy.evaluate(state)
        assert signal is not None

        trade = strategy.plan_trade(signal, state)
        assert trade.state == PairState.PAIR_PLANNED
        assert trade.direction == ArbitrageDirection.MERGE
        assert trade.market_id == "test-market"
        assert trade.leg1.side == "BUY"
        assert trade.leg2.side == "BUY"
        # Cheaper side first
        assert trade.leg1.target_price <= trade.leg2.target_price
        assert trade.trade_id in strategy.active_trades

    def test_plan_split_trade(self, strategy: CompleteSetStrategy) -> None:
        """Planning a split trade should sell more expensive side first."""
        state = _make_market_state(
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.61"),
            no_bid=Decimal("0.70"),
            no_ask=Decimal("0.71"),
        )
        signal = strategy.evaluate(state)
        assert signal is not None
        assert signal.direction == ArbitrageDirection.SPLIT

        trade = strategy.plan_trade(signal, state)
        assert trade.direction == ArbitrageDirection.SPLIT
        assert trade.leg1.side == "SELL"
        # More expensive side first
        assert trade.leg1.target_price >= trade.leg2.target_price

    def test_plan_trade_sets_token_ids(self, strategy: CompleteSetStrategy) -> None:
        """Trade should carry token IDs from market state."""
        state = _make_market_state(yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"))
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)
        assert trade.token_id_yes == "token_yes_123"
        assert trade.token_id_no == "token_no_456"


# ── State Machine Transitions ───────────────────────────────────────


class TestStateMachine:
    """Tests for state machine transitions."""

    def _create_planned_trade(self, strategy: CompleteSetStrategy) -> PairTrade:
        """Helper to create a planned trade."""
        state = _make_market_state(
            yes_ask=Decimal("0.40"),
            no_ask=Decimal("0.40"),
        )
        signal = strategy.evaluate(state)
        assert signal is not None
        return strategy.plan_trade(signal, state)

    def test_merge_happy_path(self, strategy: CompleteSetStrategy) -> None:
        """Full merge flow: IDLE → PAIR_PLANNED → LEG1_WORKING → ... → COMPLETED."""
        trade = self._create_planned_trade(strategy)

        # PAIR_PLANNED → LEG1_WORKING
        strategy.transition(trade.trade_id, PairState.LEG1_WORKING)
        assert trade.state == PairState.LEG1_WORKING

        # LEG1_WORKING → LEG1_FILLED (via on_leg_filled)
        strategy.on_leg_filled(
            trade.trade_id, leg=1,
            fill_price=Decimal("0.40"), fill_size=Decimal("100"),
        )
        assert trade.state == PairState.LEG1_FILLED

        # LEG1_FILLED → LEG2_WORKING
        strategy.transition(trade.trade_id, PairState.LEG2_WORKING)
        assert trade.state == PairState.LEG2_WORKING

        # LEG2_WORKING → BOTH_FILLED (via on_leg_filled)
        strategy.on_leg_filled(
            trade.trade_id, leg=2,
            fill_price=Decimal("0.40"), fill_size=Decimal("100"),
        )
        assert trade.state == PairState.BOTH_FILLED

        # BOTH_FILLED → MERGING
        strategy.transition(trade.trade_id, PairState.MERGING)
        assert trade.state == PairState.MERGING

        # MERGING → MERGED → COMPLETED (via on_merge_complete)
        strategy.on_merge_complete(
            trade.trade_id,
            tx_hash="0xabc123",
            gas_cost_usd=Decimal("0.80"),
        )
        assert trade.state == PairState.COMPLETED
        assert trade.is_terminal
        assert trade.merge_tx_hash == "0xabc123"

        # Should be in completed_trades now
        assert trade not in strategy.active_trades.values()
        assert trade in strategy.completed_trades

    def test_split_happy_path(self, strategy: CompleteSetStrategy) -> None:
        """Full split flow: PAIR_PLANNED → SPLITTING → ... → COMPLETED."""
        state = _make_market_state(
            yes_bid=Decimal("0.60"),
            yes_ask=Decimal("0.61"),
            no_bid=Decimal("0.60"),
            no_ask=Decimal("0.61"),
        )
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)
        assert trade.direction == ArbitrageDirection.SPLIT

        # PAIR_PLANNED → SPLITTING
        strategy.transition(trade.trade_id, PairState.SPLITTING)

        # SPLITTING → SPLIT_DONE
        strategy.on_split_complete(
            trade.trade_id, tx_hash="0xsplit", gas_cost_usd=Decimal("0.50"),
        )
        assert trade.state == PairState.SPLIT_DONE

        # SPLIT_DONE → LEG1_WORKING
        strategy.transition(trade.trade_id, PairState.LEG1_WORKING)

        # Sell leg1
        strategy.on_leg_filled(
            trade.trade_id, leg=1,
            fill_price=Decimal("0.60"), fill_size=Decimal("100"),
        )
        assert trade.state == PairState.LEG1_FILLED

        # LEG1_FILLED → LEG2_WORKING
        strategy.transition(trade.trade_id, PairState.LEG2_WORKING)

        # Sell leg2
        strategy.on_leg_filled(
            trade.trade_id, leg=2,
            fill_price=Decimal("0.60"), fill_size=Decimal("100"),
        )
        assert trade.state == PairState.BOTH_SOLD

        # BOTH_SOLD → COMPLETED is valid
        strategy.transition(trade.trade_id, PairState.COMPLETED)
        assert trade.is_terminal

    def test_invalid_transition_raises(self, strategy: CompleteSetStrategy) -> None:
        """Invalid state transitions should raise InvalidTransitionError."""
        trade = self._create_planned_trade(strategy)

        with pytest.raises(InvalidTransitionError):
            strategy.transition(trade.trade_id, PairState.MERGED)

    def test_invalid_transition_from_idle(self, strategy: CompleteSetStrategy) -> None:
        """IDLE can only transition to PAIR_PLANNED."""
        trade = PairTrade(
            market_id="test",
            condition_id="0xabc",
            token_id_yes="y",
            token_id_no="n",
            state=PairState.IDLE,
        )
        strategy._active_trades[trade.trade_id] = trade

        with pytest.raises(InvalidTransitionError):
            strategy.transition(trade.trade_id, PairState.LEG1_WORKING)

    def test_terminal_no_transitions(self, strategy: CompleteSetStrategy) -> None:
        """Terminal states (COMPLETED, FAILED, CANCELLED) have no transitions."""
        assert len(VALID_TRANSITIONS[PairState.COMPLETED]) == 0
        assert len(VALID_TRANSITIONS[PairState.FAILED]) == 0
        assert len(VALID_TRANSITIONS[PairState.CANCELLED]) == 0

    def test_failure_from_leg1_working(self, strategy: CompleteSetStrategy) -> None:
        """LEG1_WORKING can transition to FAILED."""
        trade = self._create_planned_trade(strategy)
        strategy.transition(trade.trade_id, PairState.LEG1_WORKING)

        strategy.on_failure(trade.trade_id, error="Order rejected")
        assert trade.state == PairState.FAILED
        assert trade.last_error == "Order rejected"
        assert trade.is_terminal

    def test_cancel_from_pair_planned(self, strategy: CompleteSetStrategy) -> None:
        """Should be able to cancel from PAIR_PLANNED."""
        trade = self._create_planned_trade(strategy)
        strategy.cancel_trade(trade.trade_id, reason="market moved")
        assert trade.state == PairState.CANCELLED
        assert "market moved" in (trade.last_error or "")


class TestTradeNotFound:
    """Tests for missing trade IDs."""

    def test_transition_unknown_trade(self, strategy: CompleteSetStrategy) -> None:
        """Should raise KeyError for unknown trade ID."""
        with pytest.raises(KeyError):
            strategy.transition(uuid4(), PairState.LEG1_WORKING)

    def test_on_leg_filled_unknown(self, strategy: CompleteSetStrategy) -> None:
        """Should raise KeyError for unknown trade ID."""
        with pytest.raises(KeyError):
            strategy.on_leg_filled(uuid4(), leg=1, fill_price=_ONE, fill_size=_ONE)


# ── PnL Calculation ─────────────────────────────────────────────────


class TestPnLCalculation:
    """Tests for actual profit calculation."""

    def test_merge_profit(self) -> None:
        """Merge profit = merged_amount - cost_leg1 - cost_leg2 - gas."""
        trade = PairTrade(
            direction=ArbitrageDirection.MERGE,
            target_amount=Decimal("100"),
            actual_gas_cost_usd=Decimal("0.80"),
        )
        trade.leg1 = LegOrder(
            filled_price=Decimal("0.40"), filled_size=Decimal("100"),
        )
        trade.leg2 = LegOrder(
            filled_price=Decimal("0.45"), filled_size=Decimal("100"),
        )
        # Revenue = min(100, 100) = 100 (merged)
        # Cost = 0.40 * 100 + 0.45 * 100 + 0.80 = 40 + 45 + 0.80 = 85.80
        # Profit = 100 - 85.80 = 14.20
        assert trade.actual_profit_usd == Decimal("14.20")

    def test_split_profit(self) -> None:
        """Split profit = sold_revenue - split_cost - gas."""
        trade = PairTrade(
            direction=ArbitrageDirection.SPLIT,
            target_amount=Decimal("100"),
            actual_gas_cost_usd=Decimal("0.50"),
        )
        trade.leg1 = LegOrder(
            filled_price=Decimal("0.60"), filled_size=Decimal("100"),
        )
        trade.leg2 = LegOrder(
            filled_price=Decimal("0.55"), filled_size=Decimal("100"),
        )
        # Cost = 100 + 0.50 = 100.50
        # Revenue = 0.60 * 100 + 0.55 * 100 = 60 + 55 = 115
        # Profit = 115 - 100.50 = 14.50
        assert trade.actual_profit_usd == Decimal("14.50")

    def test_zero_profit_on_unfilled(self) -> None:
        """Profit should be negative if legs are empty."""
        trade = PairTrade(
            direction=ArbitrageDirection.MERGE,
            target_amount=Decimal("100"),
            actual_gas_cost_usd=Decimal("0.80"),
        )
        # Unfilled legs: all zeros
        assert trade.actual_profit_usd == Decimal("-0.80")


# ── PnL Summary ─────────────────────────────────────────────────────


class TestPnLSummary:
    """Tests for PnL summary aggregation."""

    def test_empty_summary(self, strategy: CompleteSetStrategy) -> None:
        """Empty strategy should return zero PnL."""
        summary = strategy.get_pnl_summary()
        assert summary["total_profit_usd"] == _ZERO
        assert summary["num_completed"] == 0

    def test_summary_after_trades(self, strategy: CompleteSetStrategy) -> None:
        """Summary should aggregate completed trades."""
        # Manually add completed trades
        trade = PairTrade(
            state=PairState.COMPLETED,
            direction=ArbitrageDirection.MERGE,
            target_amount=Decimal("100"),
            actual_gas_cost_usd=Decimal("0.80"),
        )
        trade.leg1 = LegOrder(filled_price=Decimal("0.40"), filled_size=Decimal("100"))
        trade.leg2 = LegOrder(filled_price=Decimal("0.40"), filled_size=Decimal("100"))
        strategy._completed_trades.append(trade)

        summary = strategy.get_pnl_summary()
        assert summary["num_completed"] == 1
        assert summary["total_profit_usd"] > _ZERO


# ── Housekeeping ─────────────────────────────────────────────────────


class TestCleanupStaleTrades:
    """Tests for stale trade cleanup."""

    def test_stale_trade_cancelled(self, strategy: CompleteSetStrategy) -> None:
        """Trades exceeding max_trade_duration_s should be cancelled."""
        state = _make_market_state(yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"))
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)

        # Simulate old trade by backdating created_at
        import time
        trade.created_at = time.monotonic() - 400  # > 300s max

        stale = strategy.cleanup_stale_trades()
        assert len(stale) == 1
        assert stale[0].state == PairState.CANCELLED


class TestArbitrageSignal:
    """Tests for ArbitrageSignal data class."""

    def test_signal_creation(self) -> None:
        """Should create signal with all fields."""
        sig = ArbitrageSignal(
            market_id="test",
            condition_id="0xabc",
            direction=ArbitrageDirection.MERGE,
            yes_price=Decimal("0.40"),
            no_price=Decimal("0.40"),
            combined_price=Decimal("0.80"),
            margin=Decimal("0.18"),
            estimated_gas_cost_usd=Decimal("1.00"),
            expected_profit_usd=Decimal("89"),
            max_size=Decimal("500"),
        )
        assert sig.direction == ArbitrageDirection.MERGE
        assert sig.margin > _ZERO


# ── Edge Cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    """Tests for edge cases in the strategy."""

    def test_partial_fill_handled(self, strategy: CompleteSetStrategy) -> None:
        """Partial fills should be recorded correctly."""
        state = _make_market_state(yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"))
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)

        strategy.transition(trade.trade_id, PairState.LEG1_WORKING)
        strategy.on_leg_filled(
            trade.trade_id, leg=1,
            fill_price=Decimal("0.41"),  # Slight slippage
            fill_size=Decimal("80"),  # Partial fill
        )
        assert trade.leg1.filled_price == Decimal("0.41")
        assert trade.leg1.filled_size == Decimal("80")

    def test_multiple_markets_independent(self, strategy: CompleteSetStrategy) -> None:
        """Trades on different markets should be independent."""
        state1 = _make_market_state(
            yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"),
            market_id="market-1",
        )
        state2 = _make_market_state(
            yes_ask=Decimal("0.35"), no_ask=Decimal("0.35"),
            market_id="market-2",
        )

        signal1 = strategy.evaluate(state1)
        signal2 = strategy.evaluate(state2)
        assert signal1 is not None
        assert signal2 is not None

        trade1 = strategy.plan_trade(signal1, state1)
        trade2 = strategy.plan_trade(signal2, state2)

        assert trade1.market_id == "market-1"
        assert trade2.market_id == "market-2"
        assert len(strategy.active_trades) == 2

    def test_state_history_tracked(self, strategy: CompleteSetStrategy) -> None:
        """State history should record all transitions."""
        state = _make_market_state(yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"))
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)

        strategy.transition(trade.trade_id, PairState.LEG1_WORKING)

        # IDLE→PAIR_PLANNED (from plan_trade) + PAIR_PLANNED→LEG1_WORKING
        assert len(trade.state_history) == 2
        assert trade.state_history[0][0] == PairState.PAIR_PLANNED
        assert trade.state_history[1][0] == PairState.LEG1_WORKING

    def test_get_trade_active(self, strategy: CompleteSetStrategy) -> None:
        """get_trade should find active trades."""
        state = _make_market_state(yes_ask=Decimal("0.40"), no_ask=Decimal("0.40"))
        signal = strategy.evaluate(state)
        assert signal is not None
        trade = strategy.plan_trade(signal, state)

        found = strategy.get_trade(trade.trade_id)
        assert found is not None
        assert found.trade_id == trade.trade_id

    def test_get_trade_not_found(self, strategy: CompleteSetStrategy) -> None:
        """get_trade should return None for unknown IDs."""
        assert strategy.get_trade(uuid4()) is None

    def test_elapsed_seconds(self) -> None:
        """elapsed_seconds should track time since creation."""
        trade = PairTrade(market_id="test", condition_id="0x", token_id_yes="y", token_id_no="n")
        assert trade.elapsed_seconds >= 0
        assert not trade.is_terminal
