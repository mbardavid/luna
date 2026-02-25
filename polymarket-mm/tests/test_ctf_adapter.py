"""Tests for web3_infra.ctf_adapter — LEGACY module kept for backward compatibility.

The CTFAdapter is still importable from web3_infra.ctf_adapter for any
downstream code that hasn't migrated. These tests verify the module
remains importable and the core data classes still work.

For the new A2A delegation tests, see test_ctf_delegate.py.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from web3_infra.ctf_adapter import (
    CTFAdapterConfig,
    CTFTransactionError,
    CTFTxResult,
    GasAbortError,
    TxStatus,
)


# ── Unit Tests (kept from original — no on-chain mocking needed) ────

_ZERO = Decimal("0")


class TestCTFAdapterConfig:
    """Tests for CTFAdapterConfig defaults."""

    def test_default_values(self) -> None:
        """Default config should have sensible values."""
        cfg = CTFAdapterConfig()
        assert cfg.gas_limit_merge == 300_000
        assert cfg.gas_limit_split == 300_000
        assert cfg.usdc_decimals == 6
        assert cfg.max_gas_price_gwei == Decimal("100")
        assert cfg.required_confirmations == 3

    def test_custom_values(self) -> None:
        """Should accept custom configuration."""
        cfg = CTFAdapterConfig(
            gas_limit_merge=500_000,
            max_gas_price_gwei=Decimal("200"),
            matic_price_usd=Decimal("1.00"),
        )
        assert cfg.gas_limit_merge == 500_000
        assert cfg.max_gas_price_gwei == Decimal("200")


class TestCTFTxResult:
    """Tests for CTFTxResult data class."""

    def test_creation(self) -> None:
        """Should create result with all fields."""
        result = CTFTxResult(
            tx_hash="0xabc",
            status=TxStatus.CONFIRMED,
            gas_used=150_000,
            gas_price_gwei=Decimal("30"),
            cost_usd=Decimal("0.0025"),
            block_number=12345678,
        )
        assert result.tx_hash == "0xabc"
        assert result.status == TxStatus.CONFIRMED
        assert result.error is None

    def test_failed_result(self) -> None:
        """Should store error message."""
        result = CTFTxResult(
            tx_hash="0xdef",
            status=TxStatus.REVERTED,
            gas_used=100_000,
            gas_price_gwei=Decimal("30"),
            cost_usd=Decimal("0.0015"),
            block_number=12345678,
            error="Transaction reverted",
        )
        assert result.status == TxStatus.REVERTED
        assert result.error is not None


class TestGasAbortError:
    """Tests for GasAbortError exception."""

    def test_gas_abort(self) -> None:
        """Should raise with message."""
        with pytest.raises(GasAbortError, match="exceeds maximum"):
            raise GasAbortError("Gas price 150 Gwei exceeds maximum 100 Gwei")


class TestCTFTransactionError:
    """Tests for CTFTransactionError exception."""

    def test_with_tx_hash(self) -> None:
        """Should store tx_hash."""
        err = CTFTransactionError("Reverted", tx_hash="0xabc")
        assert err.tx_hash == "0xabc"
        assert err.result is None

    def test_with_result(self) -> None:
        """Should store result."""
        result = CTFTxResult(
            tx_hash="0xdef",
            status=TxStatus.REVERTED,
            gas_used=100_000,
            gas_price_gwei=Decimal("30"),
            cost_usd=Decimal("0.0015"),
            block_number=0,
            error="Reverted",
        )
        err = CTFTransactionError("Failed", result=result)
        assert err.result is not None
        assert err.result.status == TxStatus.REVERTED
