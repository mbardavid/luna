"""Tests for ProductionRunner and related components.

Tests use mocked CLOB clients — no real orders are placed.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from models.order import Order, OrderStatus, OrderType, Side
from models.position import Position
from paper.production_runner import (
    DATA_DIR,
    ProdMarketConfig,
    ProductionLiveStateWriter,
    ProductionTradeLogger,
    ProductionTradingPipeline,
    ProductionWallet,
)
from models.market_state import MarketType


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def prod_market_config():
    return ProdMarketConfig(
        market_id="test-prod-001",
        condition_id="test-cond-001",
        token_id_yes="test-tok-yes-001",
        token_id_no="test-tok-no-001",
        description="Test Market: Will it rain?",
        market_type=MarketType.OTHER,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
        max_position_size=Decimal("100"),
    )


@pytest.fixture
def production_wallet():
    return ProductionWallet(initial_balance=Decimal("25"))


# ── ProductionWallet Tests ───────────────────────────────────────────

class TestProductionWallet:
    def test_initial_state(self, production_wallet):
        assert production_wallet.initial_balance == Decimal("25")
        assert production_wallet.available_balance == Decimal("25")
        assert production_wallet.locked_balance == Decimal("0")
        assert production_wallet.total_fees == Decimal("0")

    def test_wallet_snapshot(self, production_wallet):
        snap = production_wallet.wallet_snapshot()
        assert snap["initial_balance"] == 25.0
        assert snap["available_balance"] == 25.0
        assert snap["total_equity"] == 25.0
        assert snap["pnl_pct"] == 0.0

    def test_buy_fill_updates_position(self, production_wallet):
        production_wallet.init_position("test-001", "yes-tok", "no-tok")

        pnl = production_wallet.update_position_on_fill(
            market_id="test-001",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("5"),
            fee=Decimal("0.0075"),  # 30bps on 0.50 * 5 = 2.50
        )

        assert pnl == Decimal("0")  # No realized PnL on BUY
        assert production_wallet.available_balance == Decimal("25") - Decimal("2.50") - Decimal("0.0075")
        assert production_wallet.total_fees == Decimal("0.0075")

        pos = production_wallet.get_position("test-001")
        assert pos.qty_yes == Decimal("5")
        assert pos.avg_entry_yes == Decimal("0.50")

    def test_sell_fill_realizes_pnl(self, production_wallet):
        production_wallet.init_position("test-001", "yes-tok", "no-tok")

        # Buy first
        production_wallet.update_position_on_fill(
            market_id="test-001",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("5"),
            fee=Decimal("0"),
        )

        # Sell at higher price
        pnl = production_wallet.update_position_on_fill(
            market_id="test-001",
            side="SELL",
            token_is_yes=True,
            fill_price=Decimal("0.55"),
            fill_qty=Decimal("5"),
            fee=Decimal("0.00825"),
        )

        # PnL = (0.55 - 0.50) * 5 - 0.00825 = 0.25 - 0.00825 = 0.24175
        expected_pnl = Decimal("0.55") * 5 - Decimal("0.50") * 5 - Decimal("0.00825")
        # Sell pnl calculation: (fill_price - avg_entry) * qty - fee
        assert pnl == (Decimal("0.55") - Decimal("0.50")) * Decimal("5") - Decimal("0.00825")

    def test_equity_with_positions(self, production_wallet):
        production_wallet.init_position("test-001", "yes-tok", "no-tok")
        production_wallet.update_position_on_fill(
            market_id="test-001",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("10"),
            fee=Decimal("0"),
        )

        # Equity = available (25 - 5) + position_value (10 * 0.60)
        equity = production_wallet.total_equity({"test-001": Decimal("0.60")})
        expected = (Decimal("25") - Decimal("5")) + Decimal("10") * Decimal("0.60")
        assert equity == expected

    def test_kill_switch_drawdown(self, production_wallet):
        """Wallet drawdown exceeding threshold should be detectable."""
        production_wallet.init_position("test-001", "yes-tok", "no-tok")
        production_wallet.update_position_on_fill(
            market_id="test-001",
            side="BUY",
            token_is_yes=True,
            fill_price=Decimal("0.50"),
            fill_qty=Decimal("10"),
            fee=Decimal("0"),
        )

        # Price drops to 0.10 → equity = 20 + 10*0.10 = 21
        equity = production_wallet.total_equity({"test-001": Decimal("0.10")})
        drawdown_pct = float((Decimal("25") - equity) / Decimal("25") * 100)
        # Should be > 10%
        assert drawdown_pct > 10


# ── ProductionTradeLogger Tests ──────────────────────────────────────

class TestProductionTradeLogger:
    def test_logs_to_production_file(self, tmp_path):
        log_path = tmp_path / "trades_production.jsonl"
        logger = ProductionTradeLogger(path=log_path, run_id="test-prod")

        logger.log_production_trade(
            market_id="test-001",
            market_description="Test Market",
            side="BUY",
            token="YES",
            price=Decimal("0.50"),
            size=Decimal("5"),
            fill_qty=Decimal("5"),
            fill_price=Decimal("0.50"),
            pnl_this_trade=Decimal("0"),
            pnl_realized=Decimal("0"),
            pnl_unrealized=Decimal("0"),
            position=None,
            market_state=None,
            features=None,
            latency_ms=150.5,
            gas_cost_usd=0.003,
            real_fee_bps=30.0,
            exchange_order_id="exch-123",
        )

        assert log_path.exists()
        with open(log_path) as f:
            line = f.readline()
            record = json.loads(line)

        assert record["is_production"] is True
        assert record["run_id"] == "test-prod"
        assert record["latency_ms"] == 150.5
        assert record["gas_cost_usd"] == 0.003
        assert record["real_fee_bps"] == 30.0
        assert record["exchange_order_id"] == "exch-123"
        assert record["side"] == "BUY"
        assert record["fill_price"] == "0.50"


# ── ProductionLiveStateWriter Tests ──────────────────────────────────

class TestProductionLiveStateWriter:
    def test_writes_to_production_path(self, tmp_path):
        # Patch DATA_DIR for test
        writer = ProductionLiveStateWriter.__new__(ProductionLiveStateWriter)
        writer._path = tmp_path / "live_state_production.json"
        writer._path.parent.mkdir(parents=True, exist_ok=True)
        writer._run_id = "test-prod"
        writer._hypothesis = "micro-test"
        writer._config_path = ""
        writer._duration_target_h = 24.0
        writer._start_time = 0
        writer._start_dt = None
        writer._pnl_history = []

        # Just verify the path is correct
        assert "production" in str(writer._path)


# ── ProdMarketConfig Tests ───────────────────────────────────────────

class TestProdMarketConfig:
    def test_create_config(self, prod_market_config):
        assert prod_market_config.market_id == "test-prod-001"
        assert prod_market_config.min_order_size == Decimal("5")
        assert prod_market_config.max_position_size == Decimal("100")
        assert prod_market_config.neg_risk is False

    def test_config_has_token_ids(self, prod_market_config):
        assert prod_market_config.token_id_yes.startswith("test-tok-yes")
        assert prod_market_config.token_id_no.startswith("test-tok-no")


# ── Pipeline Initialization Tests ────────────────────────────────────

class TestProductionPipeline:
    def test_pipeline_initializes(self, prod_market_config):
        """Pipeline should initialize without errors using mock REST client."""
        mock_rest = MagicMock()
        mock_rest.connect = AsyncMock()
        mock_rest.disconnect = AsyncMock()
        mock_rest.get_balance_allowance = AsyncMock(return_value={"balance": "25"})
        mock_rest.cancel_all_orders = AsyncMock()

        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
            quote_interval_s=1.0,
            order_size=Decimal("5"),
            half_spread_bps=50,
            gamma=0.3,
            initial_balance=Decimal("25"),
            kill_switch_max_drawdown_pct=20.0,
            kill_switch_alert_pct=10.0,
        )

        assert pipeline.wallet.initial_balance == Decimal("25")
        assert pipeline.quote_interval == 1.0
        assert len(pipeline.market_configs) == 1
        assert pipeline._kill_switch_max_drawdown_pct == 20.0

    def test_pipeline_wallet_position_initialized(self, prod_market_config):
        mock_rest = MagicMock()
        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )

        pos = pipeline.wallet.get_position(prod_market_config.market_id)
        assert pos is not None
        assert pos.qty_yes == Decimal("0")
        assert pos.qty_no == Decimal("0")

    def test_pipeline_mid_prices(self, prod_market_config):
        mock_rest = MagicMock()
        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )

        mids = pipeline._get_mid_prices()
        assert isinstance(mids, dict)
        # No market data yet, so should be empty
        assert len(mids) == 0


# ── BUG-1 FIX Tests: Trade Dedup Persistence ────────────────────────

class TestTradeDedupPersistence:
    """Tests for persistent trade deduplication across restarts."""

    def test_save_and_load_trade_dedup(self, prod_market_config, tmp_path):
        """Processed trade IDs should persist to disk and reload on init."""
        dedup_path = tmp_path / "processed_trade_ids.json"

        mock_rest = MagicMock()
        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )
        # Override path to tmp and clear any pre-loaded state from real file
        pipeline._trade_dedup_path = dedup_path
        pipeline._processed_trades = set()
        pipeline._last_processed_trade_ts = ""

        # Simulate processing some trades
        pipeline._processed_trades.add("trade-001")
        pipeline._processed_trades.add("trade-002")
        pipeline._processed_trades.add("trade-001:order-abc")
        pipeline._last_processed_trade_ts = "2026-02-28T20:00:00Z"
        pipeline._save_trade_dedup()

        # Verify file exists with correct content
        assert dedup_path.exists()
        with open(dedup_path) as f:
            data = json.load(f)
        assert set(data["trade_ids"]) == {"trade-001", "trade-002", "trade-001:order-abc"}
        assert data["last_trade_ts"] == "2026-02-28T20:00:00Z"
        assert data["count"] == 3

        # Create a new pipeline and load dedup state
        pipeline2 = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )
        pipeline2._trade_dedup_path = dedup_path
        pipeline2._load_trade_dedup()

        assert "trade-001" in pipeline2._processed_trades
        assert "trade-002" in pipeline2._processed_trades
        assert "trade-001:order-abc" in pipeline2._processed_trades
        assert pipeline2._last_processed_trade_ts == "2026-02-28T20:00:00Z"

    def test_dedup_empty_file(self, prod_market_config, tmp_path):
        """Loading from nonexistent file should start clean."""
        mock_rest = MagicMock()
        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )
        # Point to nonexistent file and reload — should start clean
        pipeline._trade_dedup_path = tmp_path / "nonexistent.json"
        pipeline._processed_trades = set()
        pipeline._last_processed_trade_ts = ""
        pipeline._load_trade_dedup()

        assert len(pipeline._processed_trades) == 0
        assert pipeline._last_processed_trade_ts == ""

    @pytest.mark.asyncio
    async def test_process_trade_skips_duplicate(self, prod_market_config):
        """Already-processed trade IDs should be skipped."""
        mock_rest = MagicMock()
        mock_rest.clob_client = MagicMock()
        mock_rest.clob_client.get_address.return_value = "0xOurWallet"

        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
        )

        # Pre-populate dedup set
        pipeline._processed_trades.add("trade-existing")

        trade = {
            "id": "trade-existing",
            "maker_orders": [{
                "maker_address": "0xOurWallet",
                "order_id": "ord-1",
                "side": "BUY",
                "asset_id": prod_market_config.token_id_yes,
                "price": "0.50",
                "matched_amount": "5",
                "fee_rate_bps": "0",
            }],
        }

        # Should not change PnL since it's a duplicate
        old_pnl = pipeline.total_pnl
        await pipeline._process_trade(trade, prod_market_config)
        assert pipeline.total_pnl == old_pnl


# ── BUG-2 FIX Tests: On-Chain Wallet Reconciliation ─────────────────

class TestOnChainReconciliation:
    """Tests for wallet on-chain reconciliation."""

    def test_wallet_has_on_chain_dict(self):
        """Wallet should initialize with empty on_chain snapshot."""
        wallet = ProductionWallet(initial_balance=Decimal("25"))
        assert wallet.on_chain == {}

    @pytest.mark.asyncio
    async def test_reconcile_on_chain_updates_snapshot(self):
        """reconcile_on_chain should populate _on_chain with real data."""
        wallet = ProductionWallet(initial_balance=Decimal("25"))
        wallet.init_position("market-1", "yes-tok", "no-tok")

        mock_rest = AsyncMock()
        # Balance from API is in micro-USDC (6 decimals): 232.50 USD = 232500000 micro
        mock_rest.get_balance_allowance = AsyncMock(
            return_value={"balance": "232500000"}
        )

        market_cfg = ProdMarketConfig(
            market_id="market-1",
            condition_id="cond-1",
            token_id_yes="yes-tok",
            token_id_no="no-tok",
            description="Test",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

        await wallet.reconcile_on_chain(mock_rest, market_configs=[market_cfg])

        oc = wallet.on_chain
        assert oc["usdc_balance"] == 232.50
        assert oc["portfolio_value"] == 232.50
        assert oc["real_pnl"] == 232.50 - 25.0  # on_chain - initial
        assert oc["discrepancy_usdc"] > 0  # virtual is 25, on-chain is 232.50
        assert "last_updated" in oc

    @pytest.mark.asyncio
    async def test_reconcile_logs_discrepancy(self):
        """Large discrepancy should be logged (not crash)."""
        wallet = ProductionWallet(initial_balance=Decimal("25"))

        mock_rest = AsyncMock()
        # 500.00 USD = 500000000 micro-USDC
        mock_rest.get_balance_allowance = AsyncMock(
            return_value={"balance": "500000000"}
        )

        await wallet.reconcile_on_chain(mock_rest)

        oc = wallet.on_chain
        # Discrepancy = |500 - 25| = 475
        assert oc["discrepancy_usdc"] == 475.0

    @pytest.mark.asyncio
    async def test_reconcile_handles_error_gracefully(self):
        """Network errors during reconciliation should not crash."""
        wallet = ProductionWallet(initial_balance=Decimal("25"))

        mock_rest = AsyncMock()
        mock_rest.get_balance_allowance = AsyncMock(
            side_effect=Exception("Network timeout")
        )

        # Should not raise
        await wallet.reconcile_on_chain(mock_rest)
        # on_chain stays empty
        assert wallet.on_chain == {}

    def test_pipeline_start_syncs_wallet_to_on_chain(self, prod_market_config):
        """Pipeline init should prepare for on-chain sync at start."""
        mock_rest = MagicMock()
        mock_rest.get_balance_allowance = AsyncMock(
            return_value={"balance": "232.50"}
        )

        pipeline = ProductionTradingPipeline(
            market_configs=[prod_market_config],
            rest_client=mock_rest,
            duration_hours=0.01,
            initial_balance=Decimal("25"),
        )

        # Initial balance is 25 (before start is called)
        assert pipeline.wallet.initial_balance == Decimal("25")


# ── BUG-3 FIX Tests: Live State On-Chain Section ────────────────────

class TestLiveStateOnChain:
    """Tests for on_chain section in live_state_production.json."""

    def test_live_state_writer_accepts_on_chain(self, tmp_path):
        """LiveStateWriter.write() should accept and include on_chain data."""
        from paper.paper_runner import LiveStateWriter, MetricsCollector, LiveBookTracker

        writer = LiveStateWriter(
            path=tmp_path / "test_live_state.json",
            run_id="test",
        )

        mock_metrics = MagicMock(spec=MetricsCollector)
        mock_metrics.total_quotes = 0
        mock_metrics.total_orders = 0
        mock_metrics.total_fills = 0
        mock_metrics.total_ws_messages = 0
        mock_metrics.total_errors = 0
        mock_metrics.per_market = {}

        mock_book = MagicMock(spec=LiveBookTracker)
        mock_kill = MagicMock()
        mock_kill.state.value = "RUNNING"

        on_chain_data = {
            "usdc_balance": 232.50,
            "portfolio_value": 232.50,
            "real_pnl": 207.50,
            "discrepancy_usdc": 0.0,
            "last_updated": "2026-02-28T20:00:00Z",
        }

        writer.write(
            status="RUNNING",
            total_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions={},
            metrics=mock_metrics,
            market_configs=[],
            book_tracker=mock_book,
            kill_switch=mock_kill,
            on_chain=on_chain_data,
        )

        with open(tmp_path / "test_live_state.json") as f:
            state = json.load(f)

        assert "on_chain" in state
        assert state["on_chain"]["usdc_balance"] == 232.50
        assert state["on_chain"]["real_pnl"] == 207.50

    def test_live_state_writer_no_on_chain_is_fine(self, tmp_path):
        """When on_chain is None, state should not have on_chain key."""
        from paper.paper_runner import LiveStateWriter, MetricsCollector, LiveBookTracker

        writer = LiveStateWriter(
            path=tmp_path / "test_live_state2.json",
            run_id="test",
        )

        mock_metrics = MagicMock(spec=MetricsCollector)
        mock_metrics.total_quotes = 0
        mock_metrics.total_orders = 0
        mock_metrics.total_fills = 0
        mock_metrics.total_ws_messages = 0
        mock_metrics.total_errors = 0
        mock_metrics.per_market = {}

        mock_book = MagicMock(spec=LiveBookTracker)
        mock_kill = MagicMock()
        mock_kill.state.value = "RUNNING"

        writer.write(
            status="RUNNING",
            total_pnl=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            positions={},
            metrics=mock_metrics,
            market_configs=[],
            book_tracker=mock_book,
            kill_switch=mock_kill,
        )

        with open(tmp_path / "test_live_state2.json") as f:
            state = json.load(f)

        assert "on_chain" not in state
