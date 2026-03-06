"""Tests for paper.db.supabase_logger — fire-and-forget Supabase logging."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from paper.db.supabase_logger import SupabaseLogger


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def env_vars(monkeypatch):
    """Set required Supabase env vars."""
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key-123")


@pytest.fixture
def anon_only_env_vars(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon-key-123")


@pytest.fixture
def no_env_vars(monkeypatch):
    """Ensure Supabase env vars are NOT set."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)


@pytest.fixture
def mock_httpx_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.text = ""
    client.post = AsyncMock(return_value=mock_resp)
    client.patch = AsyncMock(return_value=mock_resp)
    client.aclose = AsyncMock()
    return client


async def _make_logger(env_vars_fixture, mock_client, run_id="run-001"):
    """Helper: create a started logger with mocked HTTP client."""
    logger = SupabaseLogger(run_id=run_id, enabled=True)
    # Patch httpx import inside start()
    with patch.dict("sys.modules", {}):
        pass
    # Just start and then override the client
    with patch("httpx.AsyncClient", return_value=mock_client):
        with patch("httpx.Timeout"):
            await logger.start()
    # Override client directly (the mock may not match exactly)
    logger._client = mock_client
    return logger


# ── No-op when disabled ────────────────────────────────────────────


class TestDisabled:
    """Logger is no-op when env vars missing or disabled."""

    def test_disabled_when_no_env_vars(self, no_env_vars):
        logger = SupabaseLogger(run_id="test", enabled=True)
        assert logger.enabled is False

    def test_disabled_when_explicit_false(self, env_vars):
        logger = SupabaseLogger(run_id="test", enabled=False)
        assert logger.enabled is False

    @pytest.mark.asyncio
    async def test_noop_log_fill_no_env(self, no_env_vars):
        logger = SupabaseLogger(run_id="test", enabled=True)
        logger.log_fill(market_id="abc", side="BUY", price="0.5", size="10")

    @pytest.mark.asyncio
    async def test_noop_log_order_no_env(self, no_env_vars):
        logger = SupabaseLogger(run_id="test", enabled=True)
        logger.log_order(market_id="abc", side="SELL", price="0.6", size="5")

    @pytest.mark.asyncio
    async def test_noop_log_exit_no_env(self, no_env_vars):
        logger = SupabaseLogger(run_id="test", enabled=True)
        logger.log_exit(market_id="abc", reason="test")

    @pytest.mark.asyncio
    async def test_noop_start_stop_no_env(self, no_env_vars):
        logger = SupabaseLogger(run_id="test", enabled=True)
        await logger.start()
        await logger.stop()


# ── Enabled operation ──────────────────────────────────────────────


class TestEnabled:
    """Logger makes HTTP calls when enabled."""

    @pytest.mark.asyncio
    async def test_log_fill_posts_to_supabase(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-001")

        logger.log_fill(
            market_id="market-abc",
            trade_id="trade-1",
            order_id="order-1",
            side="BUY",
            token_side="YES",
            price=Decimal("0.55"),
            size=Decimal("10"),
            fee=Decimal("0.01"),
        )
        await asyncio.sleep(0.1)

        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        assert "pmm_fills" in call_args[0][0]
        body = json.loads(call_args[1]["content"])
        assert body["run_id"] == "run-001"
        assert body["market_id"] == "market-abc"
        assert body["side"] == "BUY"
        assert body["price"] == 0.55

    @pytest.mark.asyncio
    async def test_log_order_posts_to_supabase(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-002")

        logger.log_order(
            market_id="market-xyz",
            order_id="ord-abc",
            side="SELL",
            token_side="NO",
            price="0.45",
            size="20",
            complement_routed=True,
        )
        await asyncio.sleep(0.1)

        mock_httpx_client.post.assert_called_once()
        body = json.loads(mock_httpx_client.post.call_args[1]["content"])
        assert body["run_id"] == "run-002"
        assert body["side"] == "SELL"
        assert body["complement_routed"] is True

    @pytest.mark.asyncio
    async def test_log_exit_posts_to_supabase(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-003")

        logger.log_exit(
            market_id="market-exit",
            token_side="YES",
            entry_price="0.40",
            exit_price="0.55",
            quantity="15",
            pnl="2.25",
            reason="kill_switch",
        )
        await asyncio.sleep(0.1)

        mock_httpx_client.post.assert_called_once()
        body = json.loads(mock_httpx_client.post.call_args[1]["content"])
        assert body["reason"] == "kill_switch"
        assert body["pnl"] == 2.25

    @pytest.mark.asyncio
    async def test_log_run_start(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-004")

        logger.log_run_start(config={"markets": ["m1"], "initial_balance": "25"})
        await asyncio.sleep(0.1)

        mock_httpx_client.post.assert_called_once()
        body = json.loads(mock_httpx_client.post.call_args[1]["content"])
        assert body["run_id"] == "run-004"
        assert body["status"] == "running"
        assert body["config"]["markets"] == ["m1"]


    @pytest.mark.asyncio
    async def test_log_position_snapshot_posts_to_supabase(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-pos")

        logger.log_position_snapshot(
            wallet_address="0xabc",
            phase="startup_reconciliation",
            source="blockscout",
            usdc_balance=Decimal("42.5"),
            positions=[{"token_id": "101", "shares": Decimal("10"), "price": Decimal("0.6"), "value_usd": Decimal("6")}],
            warnings=["complement mismatch"],
        )
        await asyncio.sleep(0.1)

        mock_httpx_client.post.assert_called_once()
        body = json.loads(mock_httpx_client.post.call_args[1]["content"])
        assert body["wallet_address"] == "0xabc"
        assert body["phase"] == "startup_reconciliation"
        assert body["positions_count"] == 1
        assert body["positions"][0]["token_id"] == "101"

    @pytest.mark.asyncio
    async def test_log_run_end_patches(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-005")

        logger.log_run_end(
            total_pnl="1.50",
            total_fills=10,
            total_orders=20,
            status="completed",
        )
        await asyncio.sleep(0.1)

        mock_httpx_client.patch.assert_called_once()
        call_url = mock_httpx_client.patch.call_args[0][0]
        assert "run_id=eq.run-005" in call_url
        body = json.loads(mock_httpx_client.patch.call_args[1]["content"])
        assert body["total_fills"] == 10
        assert body["status"] == "completed"

    def test_anon_key_is_accepted_as_fallback(self, anon_only_env_vars):
        logger = SupabaseLogger(run_id="fallback", enabled=True)
        assert logger.enabled is True

    @pytest.mark.asyncio
    async def test_auth_failure_disables_logger(self, env_vars):
        client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = '{"message":"Invalid API key"}'
        client.post = AsyncMock(return_value=mock_resp)
        client.patch = AsyncMock(return_value=mock_resp)
        client.aclose = AsyncMock()

        logger = await _make_logger(env_vars, client, "run-auth")
        logger.log_run_start(config={"markets": ["m1"]})
        await asyncio.sleep(0.1)

        assert logger.enabled is False


# ── Fire-and-forget resilience ─────────────────────────────────────


class TestResilience:
    """Logger failures must never propagate to caller."""

    @pytest.mark.asyncio
    async def test_post_failure_does_not_raise(self, env_vars, mock_httpx_client):
        mock_httpx_client.post = AsyncMock(side_effect=Exception("connection refused"))
        logger = await _make_logger(env_vars, mock_httpx_client, "run-err")

        logger.log_fill(market_id="abc", side="BUY", price="0.5", size="10")
        await asyncio.sleep(0.3)
        # No exception = pass

    @pytest.mark.asyncio
    async def test_http_error_status_retries(self, env_vars, mock_httpx_client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_httpx_client.post = AsyncMock(return_value=mock_resp)
        logger = await _make_logger(env_vars, mock_httpx_client, "run-500")

        logger.log_order(market_id="abc", side="SELL", price="0.6", size="5")
        await asyncio.sleep(1.5)  # need time for retry sleep(1)

        # Called twice: initial + 1 retry
        assert mock_httpx_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_closes_client(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-stop")
        await logger.stop()
        mock_httpx_client.aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_decimal_values_serialized(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-dec")

        logger.log_fill(
            market_id="abc",
            side="BUY",
            price=Decimal("0.123456"),
            size=Decimal("100.5"),
            fee=Decimal("0.005"),
        )
        await asyncio.sleep(0.1)

        body = json.loads(mock_httpx_client.post.call_args[1]["content"])
        assert body["price"] == 0.123456
        assert body["size"] == 100.5


# ── Headers ────────────────────────────────────────────────────────


class TestHeaders:
    """Verify correct Supabase headers are sent."""

    @pytest.mark.asyncio
    async def test_headers_include_apikey_and_bearer(self, env_vars, mock_httpx_client):
        logger = await _make_logger(env_vars, mock_httpx_client, "run-hdr")

        logger.log_fill(market_id="abc", side="BUY", price="0.5", size="10")
        await asyncio.sleep(0.1)

        call_kwargs = mock_httpx_client.post.call_args[1]
        headers = call_kwargs["headers"]
        assert headers["apikey"] == "test-key-123"
        assert headers["Authorization"] == "Bearer test-key-123"
        assert headers["Content-Type"] == "application/json"
