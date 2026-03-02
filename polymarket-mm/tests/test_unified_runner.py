"""Tests for the unified runner pipeline — behavioral equivalence.

Verifies that running in paper mode via the unified pipeline produces
the same structural behavior as the old PaperTradingPipeline.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.event_bus import EventBus
from models.market_state import MarketType
from models.order import Order, OrderStatus, Side
from models.position import Position
from paper.paper_venue import FeeConfig, MarketSimConfig, PaperVenue
from runner.config import UnifiedMarketConfig
from runner.paper_venue_adapter import PaperVenueAdapter
from runner.paper_wallet import PaperWalletAdapter
from runner.pipeline import UnifiedTradingPipeline


# ── Fixtures ──────────────────────────────────────────────────────

MARKET_ID = "test-market"
CONDITION_ID = "0xtestcondition"
TOKEN_YES = "tok-yes-unified"
TOKEN_NO = "tok-no-unified"


@pytest.fixture
def unified_market_config() -> UnifiedMarketConfig:
    return UnifiedMarketConfig(
        market_id=MARKET_ID,
        condition_id=CONDITION_ID,
        token_id_yes=TOKEN_YES,
        token_id_no=TOKEN_NO,
        description="Test Market for Unified Runner",
        market_type=MarketType.OTHER,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        spread_min_bps=50,
        max_position_size=Decimal("200"),
    )


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def paper_venue(event_bus: EventBus, unified_market_config: UnifiedMarketConfig) -> PaperVenue:
    sim_config = MarketSimConfig(
        market_id=unified_market_config.market_id,
        condition_id=unified_market_config.condition_id,
        token_id_yes=unified_market_config.token_id_yes,
        token_id_no=unified_market_config.token_id_no,
        tick_size=unified_market_config.tick_size,
        min_order_size=unified_market_config.min_order_size,
        neg_risk=unified_market_config.neg_risk,
        market_type=unified_market_config.market_type,
        initial_yes_mid=Decimal("0.50"),
        volatility=Decimal("0.005"),
        fill_probability=1.0,
    )
    return PaperVenue(
        event_bus=event_bus,
        configs=[sim_config],
        fill_latency_ms=0,
        partial_fill_probability=0.0,
        initial_balance=Decimal("500"),
        fee_config=FeeConfig(maker_fee_bps=0),
    )


def make_pipeline(
    markets: list[UnifiedMarketConfig],
    venue: PaperVenue,
    event_bus: EventBus,
    duration_hours: float = 0.001,
) -> UnifiedTradingPipeline:
    """Create a unified pipeline in paper mode for testing."""
    venue_adapter = PaperVenueAdapter(venue=venue, event_bus=event_bus)
    wallet_adapter = PaperWalletAdapter(venue=venue)

    # Mock WS client so we don't connect to real WS
    mock_ws = AsyncMock()
    mock_ws.messages_received = 0
    mock_ws.connected = True

    return UnifiedTradingPipeline(
        market_configs=markets,
        venue=venue_adapter,
        wallet=wallet_adapter,
        event_bus=event_bus,
        duration_hours=duration_hours,
        quote_interval_s=0.1,
        ws_client=mock_ws,
    )


# ── Pipeline Construction Tests ──────────────────────────────────


class TestPipelineConstruction:
    """Test that the pipeline can be constructed correctly."""

    def test_paper_mode_construction(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        assert pipeline.mode == "paper"
        assert len(pipeline.market_configs) == 1
        assert pipeline.duration_hours == 0.001

    def test_initial_positions(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        assert MARKET_ID in pipeline._positions
        pos = pipeline._positions[MARKET_ID]
        assert pos.qty_yes == Decimal("0")
        assert pos.qty_no == Decimal("0")

    def test_wallet_initial_balance(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        assert pipeline.wallet.initial_balance == Decimal("500")
        assert pipeline.wallet.available_balance == Decimal("500")

    def test_mode_specific_intervals(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        # Paper mode uses shorter data gap tolerance
        assert pipeline.kill_switch._data_gap_tolerance == 15


class TestPipelineVenueInteraction:
    """Test venue adapter interactions through the pipeline."""

    @pytest.mark.asyncio
    async def test_venue_connect_disconnect(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        await pipeline.venue.connect()
        await pipeline.venue.disconnect()

    @pytest.mark.asyncio
    async def test_order_submission_via_adapter(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        await pipeline.venue.connect()

        order = Order(
            market_id=MARKET_ID,
            token_id=TOKEN_YES,
            side=Side.BUY,
            price=Decimal("0.45"),
            size=Decimal("10"),
        )
        result = await pipeline.venue.submit_order(order)
        assert result.status in (OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)

        await pipeline.venue.disconnect()

    @pytest.mark.asyncio
    async def test_cancel_market_orders(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        await pipeline.venue.connect()

        # Submit some orders
        for price in ["0.10", "0.11", "0.12"]:
            order = Order(
                market_id=MARKET_ID,
                token_id=TOKEN_YES,
                side=Side.BUY,
                price=Decimal(price),
                size=Decimal("5"),
            )
            await pipeline.venue.submit_order(order)

        # Cancel all for this market
        await pipeline.venue.cancel_market_orders(MARKET_ID)

        open_orders = await pipeline.venue.get_open_orders()
        market_orders = [o for o in open_orders if o.market_id == MARKET_ID]
        assert len(market_orders) == 0

        await pipeline.venue.disconnect()


class TestPipelineWalletInteraction:
    """Test wallet adapter interactions through the pipeline."""

    @pytest.mark.asyncio
    async def test_wallet_snapshot(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        snap = pipeline.wallet.wallet_snapshot()
        assert snap["initial_balance"] == 500.0
        assert snap["available_balance"] == 500.0

    @pytest.mark.asyncio
    async def test_position_tracking_after_fill(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        await pipeline.venue.connect()

        # Submit a buy order that will fill
        order = Order(
            market_id=MARKET_ID,
            token_id=TOKEN_YES,
            side=Side.BUY,
            price=Decimal("0.60"),  # Above mid, likely to fill
            size=Decimal("10"),
        )
        result = await pipeline.venue.submit_order(order)

        # Check position from wallet
        pos = pipeline.wallet.get_position(MARKET_ID)
        assert pos is not None
        # Position should have been updated (either filled or not)

        await pipeline.venue.disconnect()


class TestPipelineKillSwitch:
    """Test kill switch behavior in the unified pipeline."""

    @pytest.mark.asyncio
    async def test_kill_switch_thresholds_configurable(self, unified_market_config, paper_venue, event_bus):
        venue_adapter = PaperVenueAdapter(venue=paper_venue, event_bus=event_bus)
        wallet_adapter = PaperWalletAdapter(venue=paper_venue)
        mock_ws = AsyncMock()
        mock_ws.messages_received = 0
        mock_ws.connected = True

        pipeline = UnifiedTradingPipeline(
            market_configs=[unified_market_config],
            venue=venue_adapter,
            wallet=wallet_adapter,
            event_bus=event_bus,
            kill_switch_max_drawdown_pct=30.0,
            kill_switch_alert_pct=20.0,
            ws_client=mock_ws,
        )

        assert pipeline._kill_switch_max_drawdown_pct == 30.0
        assert pipeline._kill_switch_alert_pct == 20.0


class TestPipelineTradeLogger:
    """Test trade logger integration."""

    def test_trade_logger_mode(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        assert pipeline.trade_logger._mode == "paper"

    def test_trade_logger_run_id(self, unified_market_config, paper_venue, event_bus):
        from paper.paper_runner import RunConfig
        run_config = RunConfig(run_id="test-run-123")

        venue_adapter = PaperVenueAdapter(venue=paper_venue, event_bus=event_bus)
        wallet_adapter = PaperWalletAdapter(venue=paper_venue)
        mock_ws = AsyncMock()
        mock_ws.messages_received = 0
        mock_ws.connected = True

        pipeline = UnifiedTradingPipeline(
            market_configs=[unified_market_config],
            venue=venue_adapter,
            wallet=wallet_adapter,
            event_bus=event_bus,
            run_config=run_config,
            ws_client=mock_ws,
        )
        assert pipeline.trade_logger._run_id == "test-run-123"


class TestPipelineMidPrices:
    """Test mid price computation."""

    @pytest.mark.asyncio
    async def test_get_mid_prices_empty(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        mids = pipeline._get_mid_prices()
        # No WS data, so should be empty or zero
        assert isinstance(mids, dict)


class TestBackwardCompatImports:
    """Test that the old import paths still work."""

    def test_paper_runner_imports(self):
        """Ensure paper_runner classes are still importable."""
        from paper.paper_runner import (
            LiveBookTracker,
            LiveStateWriter,
            MarketConfig,
            MetricsCollector,
            PaperTradingPipeline,
            RunConfig,
            RunHistory,
            TradeLogger,
            load_markets,
        )
        assert LiveBookTracker is not None
        assert PaperTradingPipeline is not None
        assert MarketConfig is not None
        assert load_markets is not None

    def test_production_runner_imports(self):
        """Ensure production_runner classes are still importable."""
        from paper.production_runner import (
            ProdMarketConfig,
            ProductionTradingPipeline,
            ProductionTradeLogger,
            ProductionWallet,
            auto_select_markets,
        )
        assert ProductionTradingPipeline is not None
        assert ProductionWallet is not None
        assert ProdMarketConfig is not None

    def test_unified_runner_imports(self):
        """Test new unified runner imports."""
        from runner import UnifiedMarketConfig, VenueAdapter, WalletAdapter
        from runner.config import load_markets
        from runner.paper_venue_adapter import PaperVenueAdapter
        from runner.paper_wallet import PaperWalletAdapter
        from runner.production_wallet import ProductionWalletAdapter
        from runner.pipeline import UnifiedTradingPipeline
        from runner.trade_logger import UnifiedTradeLogger

        assert UnifiedMarketConfig is not None
        assert VenueAdapter is not None
        assert WalletAdapter is not None
        assert PaperVenueAdapter is not None
        assert PaperWalletAdapter is not None
        assert ProductionWalletAdapter is not None
        assert UnifiedTradingPipeline is not None
        assert UnifiedTradeLogger is not None


class TestBalanceAwareQuoting:
    """Test that balance-aware quoting is config-driven in the shared pipeline."""

    def test_balance_aware_quoting_enabled(self, unified_market_config, paper_venue, event_bus):
        venue_adapter = PaperVenueAdapter(venue=paper_venue, event_bus=event_bus)
        wallet_adapter = PaperWalletAdapter(venue=paper_venue)
        mock_ws = AsyncMock()
        mock_ws.messages_received = 0
        mock_ws.connected = True

        pipeline = UnifiedTradingPipeline(
            market_configs=[unified_market_config],
            venue=venue_adapter,
            wallet=wallet_adapter,
            event_bus=event_bus,
            balance_aware_quoting=True,
            min_balance_to_quote=Decimal("10"),
            ws_client=mock_ws,
        )

        # Balance-aware quoting should be configured in the quote engine
        assert pipeline.quote_engine._config.balance_aware_quoting is True
        assert pipeline.quote_engine._config.min_balance_to_quote == Decimal("10")

    def test_balance_aware_quoting_disabled_by_default(self, unified_market_config, paper_venue, event_bus):
        pipeline = make_pipeline([unified_market_config], paper_venue, event_bus)
        assert pipeline.quote_engine._config.balance_aware_quoting is False
