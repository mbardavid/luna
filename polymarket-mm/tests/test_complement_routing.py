"""Tests for complement routing and position cap in ProductionTradingPipeline.

Verifies that:
- SELL orders are complement-routed (SELL YES → BUY NO) when no position held
- SELL orders stay as SELL when sufficient position exists
- Complement price = 1 - original price
- Position cap blocks BUY orders that would exceed the limit
- Complement routing can be disabled via config
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from models.market_state import MarketType
from models.order import Order, OrderStatus, OrderType, Side
from models.position import Position
from paper.production_runner import (
    ProdMarketConfig,
    ProductionTradingPipeline,
    ProductionWallet,
)


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def market_cfg():
    return ProdMarketConfig(
        market_id="cr-test-001",
        condition_id="cr-cond-001",
        token_id_yes="tok-yes-cr",
        token_id_no="tok-no-cr",
        description="Complement Routing Test Market",
        market_type=MarketType.OTHER,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        max_position_size=Decimal("100"),
    )


@pytest.fixture
def mock_rest():
    rest = MagicMock()
    rest.connect = AsyncMock()
    rest.disconnect = AsyncMock()
    rest.get_balance_allowance = AsyncMock(return_value={"balance": "25000000"})
    rest.cancel_all_orders = AsyncMock()
    return rest


def _make_pipeline(market_cfg, mock_rest, complement_routing=True, max_position_per_side=Decimal("100")):
    """Helper to create a pipeline with sane test defaults."""
    return ProductionTradingPipeline(
        market_configs=[market_cfg],
        rest_client=mock_rest,
        duration_hours=0.01,
        quote_interval_s=1.0,
        order_size=Decimal("5"),
        half_spread_bps=50,
        gamma=0.3,
        initial_balance=Decimal("25"),
        complement_routing=complement_routing,
        max_position_per_side=max_position_per_side,
    )


# ── Complement Routing Tests ────────────────────────────────────────

class TestComplementRouting:
    """Tests for SELL → BUY complement routing."""

    @pytest.mark.asyncio
    async def test_sell_yes_without_position_routes_to_buy_no(self, market_cfg, mock_rest):
        """SELL YES with no YES position → BUY NO @ (1 - price)."""
        pipeline = _make_pipeline(market_cfg, mock_rest)

        # No position (default: qty_yes=0, qty_no=0)
        pos = pipeline.wallet.get_position(market_cfg.market_id)
        assert pos.qty_yes == Decimal("0")

        # Create a SELL YES order
        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.SELL,
            price=Decimal("0.60"),
            size=Decimal("5"),
        )

        # Mock the execution to capture what gets submitted
        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        # Mock quote engine to return our test order
        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)

        # Mock feature engine
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())

        # Mock book tracker to return valid market state
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())

        # Mock cancel
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        assert len(submitted_orders) == 1
        routed = submitted_orders[0]
        assert routed.side == Side.BUY
        assert routed.token_id == market_cfg.token_id_no
        assert routed.price == Decimal("0.40")  # 1 - 0.60

    @pytest.mark.asyncio
    async def test_sell_no_without_position_routes_to_buy_yes(self, market_cfg, mock_rest):
        """SELL NO with no NO position → BUY YES @ (1 - price)."""
        pipeline = _make_pipeline(market_cfg, mock_rest)

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_no,
            side=Side.SELL,
            price=Decimal("0.45"),
            size=Decimal("5"),
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        assert len(submitted_orders) == 1
        routed = submitted_orders[0]
        assert routed.side == Side.BUY
        assert routed.token_id == market_cfg.token_id_yes
        assert routed.price == Decimal("0.55")  # 1 - 0.45

    @pytest.mark.asyncio
    async def test_sell_with_position_stays_as_sell(self, market_cfg, mock_rest):
        """SELL YES with sufficient YES position stays as SELL YES."""
        pipeline = _make_pipeline(market_cfg, mock_rest)

        # Give wallet 10 YES shares
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("10"),
            fee=Decimal("0"),
        )
        pos = pipeline.wallet.get_position(market_cfg.market_id)
        assert pos.qty_yes == Decimal("10")

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.SELL,
            price=Decimal("0.60"),
            size=Decimal("5"),
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        assert len(submitted_orders) == 1
        kept = submitted_orders[0]
        # Should NOT be routed — stays as SELL YES
        assert kept.side == Side.SELL
        assert kept.token_id == market_cfg.token_id_yes
        assert kept.price == Decimal("0.60")

    def test_complement_price_calculation(self):
        """Complement price = 1 - original price."""
        test_cases = [
            (Decimal("0.60"), Decimal("0.40")),
            (Decimal("0.45"), Decimal("0.55")),
            (Decimal("0.01"), Decimal("0.99")),
            (Decimal("0.99"), Decimal("0.01")),
            (Decimal("0.50"), Decimal("0.50")),
            (Decimal("0.33"), Decimal("0.67")),
        ]
        for price, expected_complement in test_cases:
            complement = Decimal("1") - price
            assert complement == expected_complement, (
                f"Complement of {price} should be {expected_complement}, got {complement}"
            )

    @pytest.mark.asyncio
    async def test_complement_routing_disabled_config(self, market_cfg, mock_rest):
        """With complement_routing=False, SELL without position is skipped entirely."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, complement_routing=False,
        )

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.SELL,
            price=Decimal("0.60"),
            size=Decimal("5"),
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        # No orders should be submitted — SELL skipped, not routed
        assert len(submitted_orders) == 0

    @pytest.mark.asyncio
    async def test_partial_position_triggers_complement(self, market_cfg, mock_rest):
        """SELL 10 when only 3 shares held → complement route (not enough)."""
        pipeline = _make_pipeline(market_cfg, mock_rest)

        # Give wallet 3 YES shares (less than order size of 10)
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("3"),
            fee=Decimal("0"),
        )

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.SELL,
            price=Decimal("0.60"),
            size=Decimal("10"),
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        assert len(submitted_orders) == 1
        routed = submitted_orders[0]
        assert routed.side == Side.BUY
        assert routed.token_id == market_cfg.token_id_no
        assert routed.price == Decimal("0.40")


# ── Position Cap Tests ──────────────────────────────────────────────

class TestPositionCap:
    """Tests for max_position_per_side cap."""

    @pytest.mark.asyncio
    async def test_position_cap_blocks_buy_over_limit(self, market_cfg, mock_rest):
        """BUY that would exceed position cap should be skipped."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, max_position_per_side=Decimal("50"),
        )

        # Give wallet 48 YES shares
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("48"),
            fee=Decimal("0"),
        )

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.BUY,
            price=Decimal("0.50"),
            size=Decimal("5"),  # 48 + 5 = 53 > 50 cap
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        # No order submitted — cap exceeded
        assert len(submitted_orders) == 0

    @pytest.mark.asyncio
    async def test_position_cap_allows_buy_under_limit(self, market_cfg, mock_rest):
        """BUY that stays under position cap should be submitted."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, max_position_per_side=Decimal("50"),
        )

        # Give wallet 40 YES shares
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("40"),
            fee=Decimal("0"),
        )

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.BUY,
            price=Decimal("0.50"),
            size=Decimal("5"),  # 40 + 5 = 45 < 50 cap
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        assert len(submitted_orders) == 1
        assert submitted_orders[0].side == Side.BUY

    @pytest.mark.asyncio
    async def test_position_cap_applies_after_complement_routing(self, market_cfg, mock_rest):
        """Complement-routed order (now BUY) should also be checked against cap."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, max_position_per_side=Decimal("50"),
        )

        # Give wallet 48 NO shares (complement route will target NO → YES, but
        # for SELL YES → BUY NO, it checks NO position)
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=False,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("48"),
            fee=Decimal("0"),
        )

        # SELL YES @ 0.60 → complement route → BUY NO @ 0.40
        # But NO position = 48 + 5 = 53 > 50 cap → blocked
        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.SELL,
            price=Decimal("0.60"),
            size=Decimal("5"),
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        # Complement routed to BUY NO, but cap blocks it
        assert len(submitted_orders) == 0

    @pytest.mark.asyncio
    async def test_position_cap_exact_boundary(self, market_cfg, mock_rest):
        """BUY that would reach exactly the cap should be allowed (not >)."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, max_position_per_side=Decimal("50"),
        )

        # 45 + 5 = 50 = cap (not > cap) → should be allowed
        pipeline.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("45"),
            fee=Decimal("0"),
        )

        order = Order(
            market_id=market_cfg.market_id,
            token_id=market_cfg.token_id_yes,
            side=Side.BUY,
            price=Decimal("0.50"),
            size=Decimal("5"),  # 45 + 5 = 50 = cap
        )

        submitted_orders = []

        async def capture_submit(o):
            submitted_orders.append(o)
            return o.model_copy(update={"status": OrderStatus.OPEN})

        pipeline.execution.submit_order = capture_submit

        mock_plan = MagicMock()
        mock_plan.slices = [MagicMock()]
        mock_plan.to_order_intents.return_value = [order]
        pipeline.quote_engine.generate_quotes = MagicMock(return_value=mock_plan)
        pipeline.feature_engine.compute = AsyncMock(return_value=MagicMock())
        mock_ms = MagicMock()
        mock_ms.mid_price = Decimal("0.55")
        pipeline.book_tracker.get_market_state = MagicMock(return_value=mock_ms)
        pipeline.book_tracker.get_book = MagicMock(return_value=MagicMock())
        pipeline._cancel_market_orders = AsyncMock()

        await pipeline._process_market(market_cfg, Decimal("0"))

        # 50 is not > 50, so should be allowed
        assert len(submitted_orders) == 1


# ── Config Params Tests ─────────────────────────────────────────────

class TestConfigParams:
    """Tests for complement_routing and max_position_per_side config."""

    def test_default_complement_routing_enabled(self, market_cfg, mock_rest):
        """Default complement_routing should be True."""
        pipeline = ProductionTradingPipeline(
            market_configs=[market_cfg],
            rest_client=mock_rest,
            duration_hours=0.01,
        )
        assert pipeline._complement_routing is True

    def test_default_max_position_per_side(self, market_cfg, mock_rest):
        """Default max_position_per_side should be 100."""
        pipeline = ProductionTradingPipeline(
            market_configs=[market_cfg],
            rest_client=mock_rest,
            duration_hours=0.01,
        )
        assert pipeline._max_position_per_side == Decimal("100")

    def test_custom_complement_routing_disabled(self, market_cfg, mock_rest):
        """complement_routing=False should be respected."""
        pipeline = _make_pipeline(market_cfg, mock_rest, complement_routing=False)
        assert pipeline._complement_routing is False

    def test_custom_max_position_per_side(self, market_cfg, mock_rest):
        """Custom max_position_per_side should be respected."""
        pipeline = _make_pipeline(
            market_cfg, mock_rest, max_position_per_side=Decimal("50"),
        )
        assert pipeline._max_position_per_side == Decimal("50")
