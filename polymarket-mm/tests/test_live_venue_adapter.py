from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from models.market_state import MarketType
from models.order import Order, Side
from runner.config import UnifiedMarketConfig
from runner.live_venue_adapter import LiveVenueAdapter


class FakeExecution:
    def __init__(self, open_orders):
        self._open_orders = open_orders
        self.cancelled_client_order_ids = []
        self._default_tick_size = "0.01"
        self._default_neg_risk = False

    async def get_open_orders(self):
        return list(self._open_orders)

    async def cancel_order(self, client_order_id):
        self.cancelled_client_order_ids.append(client_order_id)
        return True

    def drain_alerts(self):
        return []


class FakeRestClient:
    def __init__(self):
        self.cancelled_exchange_order_ids = []
        self.clob_client = SimpleNamespace(get_address=lambda: "0xabc")

    async def cancel_order(self, exchange_order_id):
        self.cancelled_exchange_order_ids.append(exchange_order_id)
        return True

    async def connect(self):
        return None

    async def disconnect(self):
        return None


def _market_config() -> UnifiedMarketConfig:
    return UnifiedMarketConfig(
        market_id="mkt-1",
        condition_id="cond-1",
        token_id_yes="yes-1",
        token_id_no="no-1",
        description="Test",
        market_type=MarketType.OTHER,
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        neg_risk=False,
    )


@pytest.mark.asyncio
async def test_cancel_market_orders_prefers_exchange_order_id():
    rest_client = FakeRestClient()
    open_order = Order(
        exchange_order_id="venue-order-123",
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.BUY,
        price=Decimal("0.45"),
        size=Decimal("10"),
    )
    execution = FakeExecution([open_order])
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()])

    await adapter.cancel_market_orders("mkt-1")

    assert rest_client.cancelled_exchange_order_ids == ["venue-order-123"]
    assert execution.cancelled_client_order_ids == []


@pytest.mark.asyncio
async def test_cancel_market_orders_falls_back_to_client_order_id():
    rest_client = FakeRestClient()
    open_order = Order(
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.BUY,
        price=Decimal("0.45"),
        size=Decimal("10"),
    )
    execution = FakeExecution([open_order])
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()])

    await adapter.cancel_market_orders("mkt-1")

    assert rest_client.cancelled_exchange_order_ids == []
    assert execution.cancelled_client_order_ids == [open_order.client_order_id]
