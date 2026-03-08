"""Tests for unified runner — VenueAdapter and WalletAdapter ABC implementations."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from core.event_bus import EventBus
from models.order import Order, OrderStatus, Side
from models.position import Position
from paper.paper_venue import FeeConfig, MarketSimConfig, PaperVenue
from runner.config import UnifiedMarketConfig, load_markets
from runner.paper_venue_adapter import PaperVenueAdapter
from runner.paper_wallet import PaperWalletAdapter
from runner.venue_adapter import VenueAdapter
from runner.wallet_adapter import WalletAdapter


# ── Fixtures ──────────────────────────────────────────────────────


MARKET_ID = "test-market-1"
CONDITION_ID = "0xtest123"
TOKEN_YES = "token-yes-1"
TOKEN_NO = "token-no-1"


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def market_sim_config() -> MarketSimConfig:
    from models.market_state import MarketType
    return MarketSimConfig(
        market_id=MARKET_ID,
        condition_id=CONDITION_ID,
        token_id_yes=TOKEN_YES,
        token_id_no=TOKEN_NO,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        market_type=MarketType.OTHER,
        initial_yes_mid=Decimal("0.50"),
        volatility=Decimal("0.005"),
        fill_probability=1.0,  # Always fill for testing
    )


@pytest.fixture
def unified_market_config() -> UnifiedMarketConfig:
    from models.market_state import MarketType
    return UnifiedMarketConfig(
        market_id=MARKET_ID,
        condition_id=CONDITION_ID,
        token_id_yes=TOKEN_YES,
        token_id_no=TOKEN_NO,
        description="Test Market",
        market_type=MarketType.OTHER,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )


@pytest.fixture
def paper_venue(event_bus: EventBus, market_sim_config: MarketSimConfig) -> PaperVenue:
    return PaperVenue(
        event_bus=event_bus,
        configs=[market_sim_config],
        fill_latency_ms=0,
        partial_fill_probability=0.0,
        initial_balance=Decimal("500"),
        fee_config=FeeConfig(maker_fee_bps=0),
    )


@pytest.fixture
def paper_venue_adapter(paper_venue: PaperVenue, event_bus: EventBus) -> PaperVenueAdapter:
    return PaperVenueAdapter(venue=paper_venue, event_bus=event_bus)


@pytest.fixture
def paper_wallet_adapter(paper_venue: PaperVenue) -> PaperWalletAdapter:
    return PaperWalletAdapter(venue=paper_venue)


# ── VenueAdapter ABC Tests ────────────────────────────────────────


class TestVenueAdapterABC:
    """Test that VenueAdapter ABC cannot be instantiated directly."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            VenueAdapter()  # type: ignore


class TestWalletAdapterABC:
    """Test that WalletAdapter ABC cannot be instantiated directly."""

    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            WalletAdapter()  # type: ignore


# ── PaperVenueAdapter Tests ──────────────────────────────────────


class TestPaperVenueAdapter:
    """Tests for PaperVenueAdapter."""

    def test_mode_is_paper(self, paper_venue_adapter: PaperVenueAdapter):
        assert paper_venue_adapter.mode == "paper"

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()
        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_submit_order(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()

        order = Order(
            market_id=MARKET_ID,
            token_id=TOKEN_YES,
            side=Side.BUY,
            price=Decimal("0.45"),
            size=Decimal("10"),
        )
        result = await paper_venue_adapter.submit_order(order)
        assert result is not None
        assert result.status in (OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)

        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_cancel_order(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()

        order = Order(
            market_id=MARKET_ID,
            token_id=TOKEN_YES,
            side=Side.BUY,
            price=Decimal("0.10"),  # Far from mid, unlikely to fill
            size=Decimal("10"),
        )
        result = await paper_venue_adapter.submit_order(order)

        # If order is still open, cancel it
        if result.status == OrderStatus.OPEN:
            cancelled = await paper_venue_adapter.cancel_order(result.client_order_id)
            assert cancelled is True

        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_cancel_all_orders(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()
        # Should not raise
        await paper_venue_adapter.cancel_all_orders()
        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_cancel_market_orders(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()
        await paper_venue_adapter.cancel_market_orders(MARKET_ID)
        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_get_open_orders(self, paper_venue_adapter: PaperVenueAdapter):
        await paper_venue_adapter.connect()
        orders = await paper_venue_adapter.get_open_orders()
        assert isinstance(orders, list)
        await paper_venue_adapter.disconnect()

    @pytest.mark.asyncio
    async def test_process_fills_empty(self, paper_venue_adapter: PaperVenueAdapter):
        fills = await paper_venue_adapter.process_fills()
        assert fills == []

    def test_drain_fill_event(self, paper_venue_adapter: PaperVenueAdapter):
        payload = {
            "market_id": MARKET_ID,
            "token_id": TOKEN_YES,
            "side": "BUY",
            "fill_price": "0.50",
            "fill_qty": "10",
            "fee": "0",
        }
        paper_venue_adapter.drain_fill_event(payload)
        # Should not raise

    @pytest.mark.asyncio
    async def test_drain_then_process(self, paper_venue_adapter: PaperVenueAdapter):
        payload = {
            "market_id": MARKET_ID,
            "token_id": TOKEN_YES,
            "side": "BUY",
            "fill_price": "0.50",
            "fill_qty": "10",
            "fee": "0.01",
        }
        paper_venue_adapter.drain_fill_event(payload)
        paper_venue_adapter.drain_fill_event(payload)

        fills = await paper_venue_adapter.process_fills()
        assert len(fills) == 2
        assert fills[0]["fill_price"] == Decimal("0.50")
        assert fills[0]["fill_id"] == "paper-1"
        assert fills[1]["fill_id"] == "paper-2"

        # Second call should be empty
        fills2 = await paper_venue_adapter.process_fills()
        assert len(fills2) == 0


# ── PaperWalletAdapter Tests ────────────────────────────────────


class TestPaperWalletAdapter:
    """Tests for PaperWalletAdapter."""

    @pytest.mark.asyncio
    async def test_initial_balance(self, paper_wallet_adapter: PaperWalletAdapter):
        assert paper_wallet_adapter.initial_balance == Decimal("500")

    @pytest.mark.asyncio
    async def test_available_balance(self, paper_wallet_adapter: PaperWalletAdapter):
        assert paper_wallet_adapter.available_balance == Decimal("500")

    @pytest.mark.asyncio
    async def test_locked_balance(self, paper_wallet_adapter: PaperWalletAdapter):
        assert paper_wallet_adapter.locked_balance == Decimal("0")

    @pytest.mark.asyncio
    async def test_total_equity(self, paper_wallet_adapter: PaperWalletAdapter):
        equity = paper_wallet_adapter.total_equity()
        assert equity == Decimal("500")

    @pytest.mark.asyncio
    async def test_wallet_snapshot(self, paper_wallet_adapter: PaperWalletAdapter):
        snap = paper_wallet_adapter.wallet_snapshot()
        assert isinstance(snap, dict)
        assert "available_balance" in snap
        assert "initial_balance" in snap
        assert snap["initial_balance"] == 500.0

    @pytest.mark.asyncio
    async def test_get_position(self, paper_wallet_adapter: PaperWalletAdapter, paper_venue: PaperVenue):
        await paper_venue.connect()
        pos = paper_wallet_adapter.get_position(MARKET_ID)
        assert pos is not None
        assert pos.market_id == MARKET_ID
        assert pos.qty_yes == Decimal("0")
        assert pos.qty_no == Decimal("0")
        await paper_venue.disconnect()

    @pytest.mark.asyncio
    async def test_positions_dict(self, paper_wallet_adapter: PaperWalletAdapter, paper_venue: PaperVenue):
        await paper_venue.connect()
        positions = paper_wallet_adapter.positions
        assert isinstance(positions, dict)
        assert MARKET_ID in positions
        await paper_venue.disconnect()

    @pytest.mark.asyncio
    async def test_total_fees(self, paper_wallet_adapter: PaperWalletAdapter):
        assert paper_wallet_adapter.total_fees == Decimal("0")

    @pytest.mark.asyncio
    async def test_init_position_is_noop(self, paper_wallet_adapter: PaperWalletAdapter):
        # Should not raise
        paper_wallet_adapter.init_position("new-market", "tok-yes", "tok-no")


# ── UnifiedMarketConfig Tests ───────────────────────────────────


class TestUnifiedMarketConfig:
    """Tests for UnifiedMarketConfig and load_markets."""

    def test_create_config(self, unified_market_config: UnifiedMarketConfig):
        assert unified_market_config.market_id == MARKET_ID
        assert unified_market_config.tick_size == Decimal("0.01")
        assert unified_market_config.max_position_size == Decimal("500")

    def test_load_markets_from_yaml(self, tmp_path):
        yaml_content = """
markets:
  - market_id: "test-mkt"
    condition_id: "0xabc"
    token_id_yes: "tok-yes"
    token_id_no: "tok-no"
    description: "Test"
    market_type: "OTHER"
    enabled: true
    params:
      tick_size: "0.01"
      min_order_size: "5"
      neg_risk: false
      spread_min_bps: 40
      max_position_size: "200"
  - market_id: "disabled-mkt"
    condition_id: "0xdef"
    token_id_yes: "tok-yes-2"
    token_id_no: "tok-no-2"
    enabled: false
    params: {}
"""
        yaml_path = tmp_path / "markets.yaml"
        yaml_path.write_text(yaml_content)

        markets = load_markets(yaml_path)
        assert len(markets) == 1
        assert markets[0].market_id == "test-mkt"
        assert markets[0].spread_min_bps == 40
        assert markets[0].max_position_size == Decimal("200")


# ── ProductionWalletAdapter Tests ────────────────────────────────


class TestProductionWalletAdapter:
    """Tests for ProductionWalletAdapter."""

    def test_initial_balance(self):
        from paper.production_runner import ProductionWallet
        from runner.production_wallet import ProductionWalletAdapter

        wallet = ProductionWallet(initial_balance=Decimal("25"))
        adapter = ProductionWalletAdapter(wallet=wallet)

        assert adapter.initial_balance == Decimal("25")
        assert adapter.available_balance == Decimal("25")
        assert adapter.locked_balance == Decimal("0")
        assert adapter.test_capital == Decimal("25")

    def test_wallet_snapshot(self):
        from paper.production_runner import ProductionWallet
        from runner.production_wallet import ProductionWalletAdapter

        wallet = ProductionWallet(initial_balance=Decimal("100"))
        adapter = ProductionWalletAdapter(wallet=wallet)

        snap = adapter.wallet_snapshot()
        assert snap["initial_balance"] == 100.0
        assert snap["available_balance"] == 100.0

    def test_position_operations(self):
        from paper.production_runner import ProductionWallet
        from runner.production_wallet import ProductionWalletAdapter

        wallet = ProductionWallet(initial_balance=Decimal("100"))
        adapter = ProductionWalletAdapter(wallet=wallet)

        # Init position
        adapter.init_position("mkt-1", "tok-y", "tok-n")
        pos = adapter.get_position("mkt-1")
        assert pos is not None
        assert pos.qty_yes == Decimal("0")

        # Update on fill
        pnl = adapter.update_position_on_fill(
            market_id="mkt-1",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("10"),
            fee=Decimal("0.01"),
        )
        assert pnl == Decimal("0")  # BUY doesn't generate PnL

        pos = adapter.get_position("mkt-1")
        assert pos is not None
        assert pos.qty_yes == Decimal("10")
        assert adapter.available_balance < Decimal("100")

    def test_total_fees(self):
        from paper.production_runner import ProductionWallet
        from runner.production_wallet import ProductionWalletAdapter

        wallet = ProductionWallet(initial_balance=Decimal("100"))
        adapter = ProductionWalletAdapter(wallet=wallet)
        adapter.init_position("mkt-1", "tok-y", "tok-n")

        adapter.update_position_on_fill(
            market_id="mkt-1",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("10"),
            fee=Decimal("0.05"),
        )
        assert adapter.total_fees == Decimal("0.05")


# ── TradeLogger Tests ────────────────────────────────────────────


class TestUnifiedTradeLogger:
    """Tests for UnifiedTradeLogger."""

    def test_paper_mode_log(self, tmp_path):
        from runner.trade_logger import UnifiedTradeLogger

        log_path = tmp_path / "trades.jsonl"
        tl = UnifiedTradeLogger(mode="paper", path=log_path, run_id="test-run")

        tl.log_trade(
            market_id="mkt-1",
            market_description="Test Market",
            side="BUY",
            token="YES",
            price=Decimal("0.50"),
            size=Decimal("10"),
            fill_qty=Decimal("10"),
            fill_price=Decimal("0.50"),
            pnl_this_trade=Decimal("0"),
            pnl_realized=Decimal("0"),
            pnl_unrealized=Decimal("0"),
            position=None,
            market_state=None,
            features=None,
        )

        import json
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["market_id"] == "mkt-1"
        assert "is_production" not in record  # Paper mode

    def test_live_mode_log(self, tmp_path):
        from runner.trade_logger import UnifiedTradeLogger

        log_path = tmp_path / "trades_prod.jsonl"
        tl = UnifiedTradeLogger(mode="live", path=log_path, run_id="prod-run")

        tl.log_trade(
            market_id="mkt-1",
            market_description="Test Market",
            side="BUY",
            token="YES",
            price=Decimal("0.50"),
            size=Decimal("10"),
            fill_qty=Decimal("10"),
            fill_price=Decimal("0.50"),
            pnl_this_trade=Decimal("0.05"),
            pnl_realized=Decimal("0.05"),
            pnl_unrealized=Decimal("0"),
            position=None,
            market_state=None,
            features=None,
            latency_ms=15.5,
            exchange_order_id="exch-123",
        )

        import json
        lines = log_path.read_text().strip().split("\n")
        record = json.loads(lines[0])
        assert record["is_production"] is True
        assert record["latency_ms"] == 15.5
        assert record["exchange_order_id"] == "exch-123"
        assert record["cashflow_this_trade"] == "-5.00"
        assert record["realized_pnl_cumulative"] == "0.05"

    def test_logger_records_economic_equity_fields(self, tmp_path):
        from runner.trade_logger import UnifiedTradeLogger

        log_path = tmp_path / "trades_prod.jsonl"
        tl = UnifiedTradeLogger(mode="live", path=log_path, run_id="prod-run")

        tl.log_trade(
            market_id="mkt-1",
            market_description="Test Market",
            side="BUY",
            token="YES",
            price=Decimal("0.50"),
            size=Decimal("10"),
            fill_qty=Decimal("10"),
            fill_price=Decimal("0.50"),
            pnl_this_trade=Decimal("0"),
            pnl_realized=Decimal("0"),
            pnl_unrealized=Decimal("0"),
            position=None,
            market_state=None,
            features=None,
            wallet_after={"total_equity": 95.0},
        )
        tl.log_trade(
            market_id="mkt-1",
            market_description="Test Market",
            side="SELL",
            token="YES",
            price=Decimal("0.55"),
            size=Decimal("10"),
            fill_qty=Decimal("10"),
            fill_price=Decimal("0.55"),
            pnl_this_trade=Decimal("0.50"),
            pnl_realized=Decimal("0.50"),
            pnl_unrealized=Decimal("0"),
            position=None,
            market_state=None,
            features=None,
            wallet_after={"total_equity": 96.5},
        )

        import json
        records = [json.loads(line) for line in log_path.read_text().strip().split("\n")]
        assert records[0]["ledger"]["economic_pnl_cumulative_usd"] == 0.0
        assert records[1]["ledger"]["equity_delta_from_prev_usd"] == 1.5
        assert records[1]["ledger"]["economic_pnl_cumulative_usd"] == 1.5
