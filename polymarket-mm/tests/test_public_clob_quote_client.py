from __future__ import annotations

from decimal import Decimal

import pytest

from data.public_clob_quote_client import PublicClobQuoteClient


@pytest.mark.asyncio
async def test_executable_sell_quote_uses_buy_side(monkeypatch):
    client = PublicClobQuoteClient()

    async def fake_get_json(path: str, *, params=None):
        assert path == "/price"
        assert params == {"token_id": "123", "side": "buy"}
        return {"price": "0.69"}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    quote = await client.get_executable_quote("123", action="sell_token")

    assert quote.action == "sell_token"
    assert quote.price == Decimal("0.69")


@pytest.mark.asyncio
async def test_executable_buy_quote_uses_sell_side(monkeypatch):
    client = PublicClobQuoteClient()

    async def fake_get_json(path: str, *, params=None):
        assert path == "/price"
        assert params == {"token_id": "456", "side": "sell"}
        return {"price": "0.70"}

    monkeypatch.setattr(client, "_get_json", fake_get_json)
    quote = await client.get_executable_quote("456", action="buy_token")

    assert quote.action == "buy_token"
    assert quote.price == Decimal("0.70")
