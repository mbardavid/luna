"""Tests for StartupReconciler — pre-startup reconciliation.

All tests use mocked REST clients. No real API calls are made.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paper.startup_reconciler import (
    ReconciliationResult,
    StartupReconciler,
    StartupReconciliationConfig,
)


# ── Fixtures ─────────────────────────────────────────────────────────


class FakeMarketConfig:
    """Minimal market config matching ProdMarketConfig interface."""

    def __init__(
        self,
        market_id: str = "test-cond-001",
        token_id_yes: str = "tok-yes-001",
        token_id_no: str = "tok-no-001",
    ):
        self.market_id = market_id
        self.condition_id = market_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.description = "Test Market"


@pytest.fixture
def market_config():
    return FakeMarketConfig()


@pytest.fixture
def market_configs():
    return [FakeMarketConfig()]


@pytest.fixture
def mock_rest_client():
    """REST client mock with sensible defaults."""
    client = AsyncMock()

    # No open orders by default
    client.get_open_orders = AsyncMock(return_value=[])

    # Default USDC balance: $50 (in micro-units)
    client.get_balance_allowance = AsyncMock(
        return_value={"balance": "50000000"}  # 50 USDC
    )

    # Default cancel succeeds
    client.cancel_order = AsyncMock(return_value=True)

    # Default orderbook
    client.get_orderbook = AsyncMock(return_value={
        "bids": [{"price": "0.48", "size": "100"}],
        "asks": [{"price": "0.52", "size": "100"}],
    })

    return client


@pytest.fixture
def default_config():
    return StartupReconciliationConfig()


# ── Config Tests ─────────────────────────────────────────────────────


class TestStartupReconciliationConfig:
    def test_defaults(self):
        cfg = StartupReconciliationConfig()
        assert cfg.enabled is True
        assert cfg.timeout_s == 120.0
        assert cfg.cancel_max_retries == 3
        assert cfg.min_balance_to_quote == Decimal("5")
        assert cfg.max_position_per_side == Decimal("100")

    def test_from_dict(self):
        d = {
            "enabled": False,
            "timeout_s": 60,
            "cancel_max_retries": 5,
            "min_balance_to_quote": "10",
            "max_drawdown_usd": "100",
            "max_position_per_side": "200",
            "kill_switch_max_position_value": "1000",
        }
        cfg = StartupReconciliationConfig.from_dict(d)
        assert cfg.enabled is False
        assert cfg.timeout_s == 60.0
        assert cfg.cancel_max_retries == 5
        assert cfg.min_balance_to_quote == Decimal("10")
        assert cfg.max_drawdown_usd == Decimal("100")
        assert cfg.max_position_per_side == Decimal("200")
        assert cfg.kill_switch_max_position_value == Decimal("1000")

    def test_from_dict_defaults(self):
        """Empty dict should use defaults."""
        cfg = StartupReconciliationConfig.from_dict({})
        assert cfg.enabled is True
        assert cfg.timeout_s == 120.0


# ── Phase 1: Cancel Stale Orders ────────────────────────────────────


class TestPhase1CancelStaleOrders:
    @pytest.mark.asyncio
    async def test_no_stale_orders(self, mock_rest_client, market_configs):
        """No open orders → passes cleanly."""
        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert len(result.cancelled_orders) == 0
        assert len(result.cancel_failures) == 0

    @pytest.mark.asyncio
    async def test_cancels_all_stale_orders(self, mock_rest_client, market_configs):
        """All stale orders cancelled successfully."""
        mock_rest_client.get_open_orders = AsyncMock(return_value=[
            {"id": "order-1", "price": "0.45", "side": "BUY"},
            {"id": "order-2", "price": "0.55", "side": "SELL"},
            {"id": "order-3", "price": "0.50", "side": "BUY"},
        ])
        mock_rest_client.cancel_order = AsyncMock(return_value=True)

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert len(result.cancelled_orders) == 3
        assert mock_rest_client.cancel_order.call_count == 3

    @pytest.mark.asyncio
    async def test_cancel_failure_aborts_startup(self, mock_rest_client, market_configs):
        """If any cancel fails after retries, startup is aborted."""
        mock_rest_client.get_open_orders = AsyncMock(return_value=[
            {"id": "order-1", "price": "0.45", "side": "BUY"},
            {"id": "order-fail", "price": "0.55", "side": "SELL"},
        ])

        # First order succeeds, second always fails
        cancel_results = {"order-1": True, "order-fail": False}
        mock_rest_client.cancel_order = AsyncMock(
            side_effect=lambda oid: cancel_results.get(oid, False)
        )

        config = StartupReconciliationConfig(cancel_retry_delay_s=0.01)
        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        assert result.passed is False
        assert "failed to cancel" in result.reason
        assert len(result.cancel_failures) == 1

    @pytest.mark.asyncio
    async def test_cancel_retry_succeeds(self, mock_rest_client, market_configs):
        """Cancel fails first attempt but succeeds on retry."""
        mock_rest_client.get_open_orders = AsyncMock(return_value=[
            {"id": "order-flaky", "price": "0.45", "side": "BUY"},
        ])

        # Fail first time, succeed second time
        call_count = {"n": 0}

        async def flaky_cancel(oid):
            call_count["n"] += 1
            return call_count["n"] >= 2

        mock_rest_client.cancel_order = AsyncMock(side_effect=flaky_cancel)
        config = StartupReconciliationConfig(cancel_retry_delay_s=0.01)

        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert len(result.cancelled_orders) == 1
        assert call_count["n"] == 2  # retried once

    @pytest.mark.asyncio
    async def test_fetch_orders_failure_aborts(self, mock_rest_client, market_configs):
        """If fetching open orders fails, startup is aborted."""
        mock_rest_client.get_open_orders = AsyncMock(
            side_effect=Exception("API down")
        )

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is False
        assert "failed to fetch open orders" in result.reason

    @pytest.mark.asyncio
    async def test_cancel_logs_order_details(self, mock_rest_client, market_configs):
        """Cancelled orders include price and side details."""
        mock_rest_client.get_open_orders = AsyncMock(return_value=[
            {"id": "ord-123", "price": "0.42", "side": "BUY"},
        ])
        mock_rest_client.cancel_order = AsyncMock(return_value=True)

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert result.cancelled_orders[0]["order_id"] == "ord-123"
        assert result.cancelled_orders[0]["price"] == "0.42"
        assert result.cancelled_orders[0]["side"] == "BUY"


# ── Phase 2: Position Sync ──────────────────────────────────────────


class TestPhase2PositionSync:
    @pytest.mark.asyncio
    async def test_position_sync_reads_balances(self, mock_rest_client, market_configs):
        """Reads YES, NO shares and USDC balance correctly."""
        call_map = {
            ("COLLATERAL", None): {"balance": "50000000"},       # 50 USDC
            ("CONDITIONAL", "tok-yes-001"): {"balance": "224000000"},  # 224 YES
            ("CONDITIONAL", "tok-no-001"): {"balance": "219000000"},   # 219 NO
        }

        async def fake_balance(asset_type, token_id=None):
            return call_map.get((asset_type, token_id), {"balance": "0"})

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert result.usdc_balance == Decimal("50")

        pos = result.positions["test-cond-001"]
        # Lesson 1: YES + NO treated as pair
        assert pos["yes_shares"] == Decimal("224")
        assert pos["no_shares"] == Decimal("219")

    @pytest.mark.asyncio
    async def test_position_sync_micro_units(self, mock_rest_client, market_configs):
        """Lesson 3: micro-units (1e6) are normalized correctly."""
        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "25500000"}  # 25.5 USDC
            if token_id == "tok-yes-001":
                return {"balance": "10000000"}  # 10 YES
            return {"balance": "5500000"}  # 5.5 NO

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.usdc_balance == Decimal("25.5")
        assert result.positions["test-cond-001"]["yes_shares"] == Decimal("10")
        assert result.positions["test-cond-001"]["no_shares"] == Decimal("5.5")

    @pytest.mark.asyncio
    async def test_position_sync_balance_error_continues(
        self, mock_rest_client, market_configs
    ):
        """If one token balance fails, the other still gets read."""
        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "10000000"}
            if token_id == "tok-yes-001":
                raise Exception("RPC error")
            return {"balance": "100000000"}  # 100 NO

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        pos = result.positions["test-cond-001"]
        assert pos["yes_shares"] == Decimal("0")  # failed, defaults to 0
        assert pos["no_shares"] == Decimal("100")


# ── Phase 3: Market State Refresh ────────────────────────────────────


class TestPhase3MarketStateRefresh:
    @pytest.mark.asyncio
    async def test_market_state_computes_mid(self, mock_rest_client, market_configs):
        """Mid price computed from fresh orderbook."""
        mock_rest_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.48", "size": "100"}],
            "asks": [{"price": "0.52", "size": "100"}],
        })

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        ms = result.market_states["test-cond-001"]
        assert ms["mid"] == Decimal("0.50")
        assert ms["best_bid"] == Decimal("0.48")
        assert ms["best_ask"] == Decimal("0.52")
        # spread_bps = (0.52 - 0.48) / 0.50 * 10000 = 800
        assert ms["spread_bps"] == Decimal("800")

    @pytest.mark.asyncio
    async def test_market_state_orderbook_error_continues(
        self, mock_rest_client, market_configs
    ):
        """Orderbook fetch error → mid=0 but doesn't abort."""
        mock_rest_client.get_orderbook = AsyncMock(
            side_effect=Exception("API error")
        )

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        ms = result.market_states["test-cond-001"]
        assert ms["mid"] == Decimal("0")


# ── Phase 4: Safety Checks ──────────────────────────────────────────


class TestPhase4SafetyChecks:
    @pytest.mark.asyncio
    async def test_insufficient_balance_fails(self, mock_rest_client, market_configs):
        """Balance below min → startup fails."""
        mock_rest_client.get_balance_allowance = AsyncMock(
            return_value={"balance": "2000000"}  # 2 USDC, below default min of 5
        )

        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is False
        assert "usdc_balance" in result.reason
        assert "min" in result.reason

    @pytest.mark.asyncio
    async def test_position_value_exceeds_kill_switch(
        self, mock_rest_client, market_configs
    ):
        """Total position value above kill switch → startup fails."""
        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "100000000"}  # 100 USDC
            if token_id == "tok-yes-001":
                return {"balance": "600000000"}  # 600 YES shares
            return {"balance": "600000000"}  # 600 NO shares

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)
        mock_rest_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.50", "size": "100"}],
            "asks": [{"price": "0.50", "size": "100"}],
        })

        config = StartupReconciliationConfig(
            kill_switch_max_position_value=Decimal("500")
        )
        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        assert result.passed is False
        assert "position_value" in result.reason

    @pytest.mark.asyncio
    async def test_high_net_inventory_warning(self, mock_rest_client, market_configs):
        """Net inventory > max_position_per_side → warning + skew flag."""
        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "100000000"}
            if token_id == "tok-yes-001":
                return {"balance": "200000000"}  # 200 YES
            return {"balance": "50000000"}  # 50 NO → net = 150

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        config = StartupReconciliationConfig(
            max_position_per_side=Decimal("100"),
            kill_switch_max_position_value=Decimal("5000"),
        )
        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        assert result.passed is True  # warning, not failure
        assert len(result.safety_warnings) == 1
        pos = result.positions["test-cond-001"]
        assert pos.get("needs_skew_adjustment") is True

    @pytest.mark.asyncio
    async def test_all_checks_pass(self, mock_rest_client, market_configs):
        """Normal startup with reasonable balances passes all checks."""
        reconciler = StartupReconciler(mock_rest_client, market_configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert result.reason == "all checks passed"
        assert len(result.safety_warnings) == 0


# ── Timeout ──────────────────────────────────────────────────────────


class TestTimeout:
    @pytest.mark.asyncio
    async def test_reconciliation_timeout(self, mock_rest_client, market_configs):
        """Reconciliation times out → startup fails."""
        async def slow_fetch():
            await asyncio.sleep(10)
            return []

        mock_rest_client.get_open_orders = AsyncMock(side_effect=slow_fetch)

        config = StartupReconciliationConfig(timeout_s=0.1)
        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        assert result.passed is False
        assert "timed out" in result.reason


# ── apply_to_wallet ──────────────────────────────────────────────────


class TestApplyToWallet:
    def test_apply_positions_to_wallet(self, market_configs):
        """apply_to_wallet sets positions and balance on the wallet."""
        # Minimal wallet mock
        wallet = MagicMock()
        wallet._positions = {}
        wallet._available_balance = Decimal("0")

        pos = MagicMock()
        pos.model_copy = MagicMock(return_value=pos)
        wallet.get_position = MagicMock(return_value=pos)
        wallet.init_position = MagicMock()

        result = ReconciliationResult(
            passed=True,
            reason="all checks passed",
            positions={
                "test-cond-001": {
                    "yes_shares": Decimal("224"),
                    "no_shares": Decimal("219"),
                    "token_id_yes": "tok-yes-001",
                    "token_id_no": "tok-no-001",
                }
            },
            usdc_balance=Decimal("50"),
        )

        reconciler = StartupReconciler(MagicMock(), market_configs)
        reconciler.apply_to_wallet(wallet, result)

        wallet.init_position.assert_called_once_with(
            "test-cond-001", "tok-yes-001", "tok-no-001"
        )
        # Lesson 11: USDC balance set from on-chain
        assert wallet._available_balance == Decimal("50")


# ── Integration-style Tests ──────────────────────────────────────────


class TestIntegration:
    @pytest.mark.asyncio
    async def test_full_reconciliation_flow(self, mock_rest_client, market_configs):
        """Full flow: stale orders → position sync → market state → safety."""
        # Phase 1: 2 stale orders
        mock_rest_client.get_open_orders = AsyncMock(return_value=[
            {"id": "stale-1", "price": "0.40", "side": "BUY"},
            {"id": "stale-2", "price": "0.60", "side": "SELL"},
        ])
        mock_rest_client.cancel_order = AsyncMock(return_value=True)

        # Phase 2: on-chain positions (prod-002 scenario)
        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "50000000"}  # 50 USDC
            if token_id == "tok-yes-001":
                return {"balance": "224000000"}  # 224 YES
            return {"balance": "219000000"}  # 219 NO

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        # Phase 3: fresh orderbook
        mock_rest_client.get_orderbook = AsyncMock(return_value={
            "bids": [{"price": "0.49", "size": "500"}],
            "asks": [{"price": "0.51", "size": "500"}],
        })

        config = StartupReconciliationConfig(
            cancel_retry_delay_s=0.01,
            kill_switch_max_position_value=Decimal("5000"),
        )
        reconciler = StartupReconciler(mock_rest_client, market_configs, config)
        result = await reconciler.reconcile()

        # Assertions
        assert result.passed is True
        assert len(result.cancelled_orders) == 2
        assert result.usdc_balance == Decimal("50")
        assert result.positions["test-cond-001"]["yes_shares"] == Decimal("224")
        assert result.positions["test-cond-001"]["no_shares"] == Decimal("219")
        assert result.market_states["test-cond-001"]["mid"] == Decimal("0.50")
        assert result.duration_s > 0

    @pytest.mark.asyncio
    async def test_multiple_markets(self, mock_rest_client):
        """Reconciliation works with multiple market configs."""
        configs = [
            FakeMarketConfig("mkt-1", "yes-1", "no-1"),
            FakeMarketConfig("mkt-2", "yes-2", "no-2"),
        ]

        async def fake_balance(asset_type, token_id=None):
            if asset_type == "COLLATERAL":
                return {"balance": "100000000"}
            return {"balance": "50000000"}  # 50 shares each

        mock_rest_client.get_balance_allowance = AsyncMock(side_effect=fake_balance)

        reconciler = StartupReconciler(mock_rest_client, configs)
        result = await reconciler.reconcile()

        assert result.passed is True
        assert "mkt-1" in result.positions
        assert "mkt-2" in result.positions
        assert "mkt-1" in result.market_states
        assert "mkt-2" in result.market_states


# ── Helper Tests ─────────────────────────────────────────────────────


class TestHelpers:
    def test_extract_order_id_dict(self):
        """Extracts order ID from dict with various key names."""
        assert StartupReconciler._extract_order_id({"id": "abc"}) == "abc"
        assert StartupReconciler._extract_order_id({"order_id": "xyz"}) == "xyz"
        assert StartupReconciler._extract_order_id({"orderID": "123"}) == "123"
        assert StartupReconciler._extract_order_id({}) == ""

    def test_extract_order_id_object(self):
        """Extracts order ID from object with attributes."""
        obj = MagicMock()
        obj.id = "obj-id"
        assert StartupReconciler._extract_order_id(obj) == "obj-id"

    def test_extract_field(self):
        """Extracts fields from dict or object."""
        assert StartupReconciler._extract_field({"price": "0.50"}, "price") == "0.50"
        assert StartupReconciler._extract_field({"side": "BUY"}, "side") == "BUY"
        assert StartupReconciler._extract_field({}, "price", "N/A") == "N/A"
