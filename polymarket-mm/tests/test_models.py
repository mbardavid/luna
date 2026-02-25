"""Testes unitários para todos os modelos da Fase 1."""

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from models import (
    FeatureVector,
    MarketState,
    MarketType,
    Order,
    OrderStatus,
    OrderType,
    Position,
    QuotePlan,
    QuoteSide,
    QuoteSlice,
    Side,
    TokenSide,
)


# ──────────────────────────────────────────────
# MarketState
# ──────────────────────────────────────────────

class TestMarketState:
    """Testes para MarketState."""

    def _make(self, **overrides) -> MarketState:
        defaults = dict(
            market_id="mkt-1",
            condition_id="cond-1",
            token_id_yes="tok-yes",
            token_id_no="tok-no",
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            yes_bid=Decimal("0.45"),
            yes_ask=Decimal("0.55"),
            no_bid=Decimal("0.40"),
            no_ask=Decimal("0.60"),
        )
        defaults.update(overrides)
        return MarketState(**defaults)

    def test_create_valid(self):
        ms = self._make()
        assert ms.market_id == "mkt-1"
        assert ms.tick_size == Decimal("0.01")
        assert ms.neg_risk is False
        assert isinstance(ms.timestamp, datetime)

    def test_mid_price_computed(self):
        ms = self._make(yes_bid=Decimal("0.40"), yes_ask=Decimal("0.60"))
        assert ms.mid_price == Decimal("0.50")

    def test_mid_price_zero_when_no_quotes(self):
        ms = self._make(yes_bid=Decimal("0"), yes_ask=Decimal("0"))
        assert ms.mid_price == Decimal("0")

    def test_spread_yes_computed(self):
        ms = self._make(yes_bid=Decimal("0.45"), yes_ask=Decimal("0.55"))
        assert ms.spread_yes == Decimal("0.10")

    def test_spread_no_computed(self):
        ms = self._make(no_bid=Decimal("0.40"), no_ask=Decimal("0.60"))
        assert ms.spread_no == Decimal("0.20")

    def test_market_type_enum(self):
        ms = self._make(market_type=MarketType.CRYPTO_5M)
        assert ms.market_type == MarketType.CRYPTO_5M

    def test_tick_size_must_be_positive(self):
        with pytest.raises(ValidationError, match="tick_size"):
            self._make(tick_size=Decimal("0"))

    def test_negative_tick_size_rejected(self):
        with pytest.raises(ValidationError, match="tick_size"):
            self._make(tick_size=Decimal("-0.01"))

    def test_ask_below_bid_rejected(self):
        with pytest.raises(ValidationError, match="yes_ask must be >= yes_bid"):
            self._make(yes_bid=Decimal("0.60"), yes_ask=Decimal("0.40"))

    def test_no_ask_below_no_bid_rejected(self):
        with pytest.raises(ValidationError, match="no_ask must be >= no_bid"):
            self._make(no_bid=Decimal("0.70"), no_ask=Decimal("0.30"))

    def test_empty_market_id_rejected(self):
        with pytest.raises(ValidationError, match="market_id"):
            self._make(market_id="")


# ──────────────────────────────────────────────
# Order
# ──────────────────────────────────────────────

class TestOrder:
    """Testes para Order."""

    def _make(self, **overrides) -> Order:
        defaults = dict(
            market_id="mkt-1",
            token_id="tok-yes",
            side=Side.BUY,
            price=Decimal("0.55"),
            size=Decimal("100"),
        )
        defaults.update(overrides)
        return Order(**defaults)

    def test_create_valid(self):
        o = self._make()
        assert isinstance(o.client_order_id, UUID)
        assert o.side == Side.BUY
        assert o.status == OrderStatus.PENDING
        assert o.maker_only is True
        assert o.order_type == OrderType.GTC

    def test_price_must_be_positive(self):
        with pytest.raises(ValidationError, match="price"):
            self._make(price=Decimal("0"))

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError, match="price"):
            self._make(price=Decimal("-1"))

    def test_size_must_be_positive(self):
        with pytest.raises(ValidationError, match="size"):
            self._make(size=Decimal("0"))

    def test_filled_qty_default_zero(self):
        o = self._make()
        assert o.filled_qty == Decimal("0")

    def test_filled_qty_cannot_exceed_size(self):
        with pytest.raises(ValidationError, match="filled_qty cannot exceed size"):
            self._make(size=Decimal("100"), filled_qty=Decimal("150"))

    def test_filled_qty_equal_to_size_ok(self):
        o = self._make(size=Decimal("100"), filled_qty=Decimal("100"))
        assert o.filled_qty == Decimal("100")

    def test_all_statuses(self):
        for status in OrderStatus:
            o = self._make(status=status)
            assert o.status == status

    def test_all_order_types(self):
        for ot in OrderType:
            o = self._make(order_type=ot)
            assert o.order_type == ot

    def test_sell_side(self):
        o = self._make(side=Side.SELL)
        assert o.side == Side.SELL


# ──────────────────────────────────────────────
# QuotePlan & QuoteSlice
# ──────────────────────────────────────────────

class TestQuotePlan:
    """Testes para QuotePlan e to_order_intents()."""

    def _make_plan(self, **overrides) -> QuotePlan:
        defaults = dict(
            market_id="mkt-1",
            token_id_yes="tok-yes",
            token_id_no="tok-no",
            strategy_tag="test-strat",
            slices=[
                QuoteSlice(
                    side=QuoteSide.BID,
                    token=TokenSide.YES,
                    price=Decimal("0.45"),
                    size=Decimal("50"),
                    ttl_ms=15_000,
                ),
                QuoteSlice(
                    side=QuoteSide.ASK,
                    token=TokenSide.YES,
                    price=Decimal("0.55"),
                    size=Decimal("50"),
                    ttl_ms=15_000,
                ),
                QuoteSlice(
                    side=QuoteSide.BID,
                    token=TokenSide.NO,
                    price=Decimal("0.40"),
                    size=Decimal("30"),
                ),
                QuoteSlice(
                    side=QuoteSide.ASK,
                    token=TokenSide.NO,
                    price=Decimal("0.60"),
                    size=Decimal("30"),
                ),
            ],
        )
        defaults.update(overrides)
        return QuotePlan(**defaults)

    def test_create_valid(self):
        qp = self._make_plan()
        assert qp.market_id == "mkt-1"
        assert isinstance(qp.trace_id, UUID)
        assert len(qp.slices) == 4
        assert qp.strategy_tag == "test-strat"

    def test_empty_slices_ok(self):
        qp = QuotePlan(market_id="mkt-1", slices=[])
        assert len(qp.slices) == 0

    def test_slice_price_must_be_positive(self):
        with pytest.raises(ValidationError, match="price"):
            QuoteSlice(
                side=QuoteSide.BID,
                token=TokenSide.YES,
                price=Decimal("0"),
                size=Decimal("10"),
            )

    def test_slice_size_must_be_positive(self):
        with pytest.raises(ValidationError, match="size"):
            QuoteSlice(
                side=QuoteSide.BID,
                token=TokenSide.YES,
                price=Decimal("0.50"),
                size=Decimal("-1"),
            )

    def test_to_order_intents_count(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        assert len(orders) == 4

    def test_to_order_intents_bid_maps_to_buy(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        bid_orders = [o for o in orders if o.side == Side.BUY]
        assert len(bid_orders) == 2  # BID YES + BID NO

    def test_to_order_intents_ask_maps_to_sell(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        ask_orders = [o for o in orders if o.side == Side.SELL]
        assert len(ask_orders) == 2  # ASK YES + ASK NO

    def test_to_order_intents_token_mapping(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        yes_orders = [o for o in orders if o.token_id == "tok-yes"]
        no_orders = [o for o in orders if o.token_id == "tok-no"]
        assert len(yes_orders) == 2
        assert len(no_orders) == 2

    def test_to_order_intents_preserves_price_size(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        first = orders[0]
        assert first.price == Decimal("0.45")
        assert first.size == Decimal("50")

    def test_to_order_intents_maker_only(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        assert all(o.maker_only is True for o in orders)

    def test_to_order_intents_strategy_tag_propagated(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        assert all(o.strategy_tag == "test-strat" for o in orders)

    def test_to_order_intents_ttl_propagated(self):
        qp = self._make_plan()
        orders = qp.to_order_intents()
        assert orders[0].ttl_ms == 15_000
        assert orders[2].ttl_ms == 30_000  # default

    def test_to_order_intents_empty_slices(self):
        qp = QuotePlan(market_id="mkt-1", slices=[])
        assert qp.to_order_intents() == []

    def test_to_order_intents_fallback_token_id(self):
        """Sem token_id_yes/no, usa fallback market_id_YES/NO."""
        qp = QuotePlan(
            market_id="mkt-1",
            slices=[
                QuoteSlice(
                    side=QuoteSide.BID,
                    token=TokenSide.YES,
                    price=Decimal("0.50"),
                    size=Decimal("10"),
                ),
            ],
        )
        orders = qp.to_order_intents()
        assert orders[0].token_id == "mkt-1_YES"


# ──────────────────────────────────────────────
# FeatureVector
# ──────────────────────────────────────────────

class TestFeatureVector:
    """Testes para FeatureVector."""

    def _make(self, **overrides) -> FeatureVector:
        defaults = dict(market_id="mkt-1")
        defaults.update(overrides)
        return FeatureVector(**defaults)

    def test_create_valid(self):
        fv = self._make()
        assert fv.market_id == "mkt-1"
        assert fv.spread_bps == Decimal("0")
        assert fv.data_quality_score == 1.0
        assert isinstance(fv.trace_id, UUID)

    def test_all_features(self):
        fv = self._make(
            spread_bps=Decimal("50"),
            book_imbalance=0.3,
            micro_momentum=-0.1,
            volatility_1m=0.02,
            liquidity_score=0.8,
            toxic_flow_score=1.5,
            oracle_delta=0.005,
            expected_fee_bps=Decimal("2"),
            queue_position_estimate=3.0,
            data_quality_score=0.95,
        )
        assert fv.spread_bps == Decimal("50")
        assert fv.book_imbalance == 0.3
        assert fv.toxic_flow_score == 1.5

    def test_book_imbalance_range(self):
        # Valid extremes
        self._make(book_imbalance=-1.0)
        self._make(book_imbalance=1.0)
        # Out of range
        with pytest.raises(ValidationError, match="book_imbalance"):
            self._make(book_imbalance=1.5)
        with pytest.raises(ValidationError, match="book_imbalance"):
            self._make(book_imbalance=-1.5)

    def test_negative_spread_bps_rejected(self):
        with pytest.raises(ValidationError, match="spread_bps"):
            self._make(spread_bps=Decimal("-10"))

    def test_liquidity_score_range(self):
        with pytest.raises(ValidationError, match="liquidity_score"):
            self._make(liquidity_score=1.5)

    def test_data_quality_range(self):
        with pytest.raises(ValidationError, match="data_quality_score"):
            self._make(data_quality_score=-0.1)

    def test_volatility_non_negative(self):
        with pytest.raises(ValidationError, match="volatility_1m"):
            self._make(volatility_1m=-0.01)


# ──────────────────────────────────────────────
# Position
# ──────────────────────────────────────────────

class TestPosition:
    """Testes para Position."""

    def _make(self, **overrides) -> Position:
        defaults = dict(
            market_id="mkt-1",
            token_id_yes="tok-yes",
            token_id_no="tok-no",
        )
        defaults.update(overrides)
        return Position(**defaults)

    def test_create_valid(self):
        p = self._make()
        assert p.market_id == "mkt-1"
        assert p.qty_yes == Decimal("0")
        assert p.qty_no == Decimal("0")
        assert p.realized_pnl == Decimal("0")

    def test_can_merge_true_when_both_positive(self):
        p = self._make(qty_yes=Decimal("100"), qty_no=Decimal("50"))
        assert p.can_merge is True

    def test_can_merge_false_when_one_zero(self):
        p = self._make(qty_yes=Decimal("100"), qty_no=Decimal("0"))
        assert p.can_merge is False

    def test_can_merge_false_when_both_zero(self):
        p = self._make()
        assert p.can_merge is False

    def test_negative_qty_rejected(self):
        with pytest.raises(ValidationError, match="qty_yes"):
            self._make(qty_yes=Decimal("-1"))

    def test_negative_qty_no_rejected(self):
        with pytest.raises(ValidationError, match="qty_no"):
            self._make(qty_no=Decimal("-10"))

    def test_pnl_can_be_negative(self):
        p = self._make(
            unrealized_pnl=Decimal("-50.25"),
            realized_pnl=Decimal("-100"),
            net_exposure_usd=Decimal("-200"),
        )
        assert p.unrealized_pnl == Decimal("-50.25")
        assert p.realized_pnl == Decimal("-100")

    def test_avg_entry_non_negative(self):
        with pytest.raises(ValidationError, match="avg_entry_yes"):
            self._make(avg_entry_yes=Decimal("-0.5"))
