"""Tests for web3_infra/eip712_signer.py."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from web3_infra.eip712_signer import EIP712Signer, SignedOrder


class TestEIP712Signer:

    @pytest.fixture
    def signer(self) -> EIP712Signer:
        s = EIP712Signer(
            private_key="deadbeef" * 8,
            domain_separator="TestDomain",
            max_workers=2,
        )
        s.start()
        yield s
        s.shutdown()

    @pytest.mark.asyncio
    async def test_sign_order_returns_signed(self, signer: EIP712Signer) -> None:
        order_data = {
            "token_id": "tok-001",
            "price": "0.50",
            "size": "100",
            "side": "BUY",
            "maker": "0xabc",
            "nonce": "1",
        }
        result = await signer.sign_order(order_data)
        assert isinstance(result, SignedOrder)
        assert result.order_hash.startswith("0x")
        assert result.signature.startswith("0x")
        assert result.order_data == order_data

    @pytest.mark.asyncio
    async def test_deterministic_signing(self, signer: EIP712Signer) -> None:
        order_data = {
            "token_id": "tok-001",
            "price": "0.50",
            "size": "100",
            "side": "BUY",
        }
        r1 = await signer.sign_order(order_data)
        r2 = await signer.sign_order(order_data)
        assert r1.order_hash == r2.order_hash
        assert r1.signature == r2.signature

    @pytest.mark.asyncio
    async def test_different_data_different_sig(self, signer: EIP712Signer) -> None:
        r1 = await signer.sign_order({"price": "0.50", "side": "BUY"})
        r2 = await signer.sign_order({"price": "0.51", "side": "BUY"})
        assert r1.order_hash != r2.order_hash
        assert r1.signature != r2.signature

    @pytest.mark.asyncio
    async def test_not_started_raises(self) -> None:
        signer = EIP712Signer(private_key="abc", max_workers=1)
        with pytest.raises(RuntimeError, match="not started"):
            await signer.sign_order({"price": "0.50"})

    @pytest.mark.asyncio
    async def test_concurrent_signing(self, signer: EIP712Signer) -> None:
        tasks = [
            signer.sign_order({"price": f"0.{i:02d}", "i": str(i)})
            for i in range(1, 11)
        ]
        results = await asyncio.gather(*tasks)
        assert len(results) == 10
        # All should be unique
        hashes = {r.order_hash for r in results}
        assert len(hashes) == 10

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        async with EIP712Signer(
            private_key="test_key",
            max_workers=1,
        ) as signer:
            result = await signer.sign_order({"test": "data"})
            assert isinstance(result, SignedOrder)
        # After exit, pool should be shut down
        assert signer._pool is None

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self) -> None:
        signer = EIP712Signer(private_key="key", max_workers=1)
        signer.start()
        signer.shutdown()
        signer.shutdown()  # Should not raise
        assert signer._pool is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        signer = EIP712Signer(private_key="key", max_workers=1)
        signer.start()
        pool1 = signer._pool
        signer.start()  # Should not create a new pool
        assert signer._pool is pool1
        signer.shutdown()
