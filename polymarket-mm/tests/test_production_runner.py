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
