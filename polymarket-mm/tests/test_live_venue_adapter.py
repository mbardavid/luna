from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from models.market_state import MarketType
from models.order import Order, OrderStatus, Side
from runner.config import UnifiedMarketConfig
from runner.live_venue_adapter import LiveVenueAdapter


class FakeExecution:
    def __init__(self, open_orders):
        self._open_orders = open_orders
        self.cancelled_client_order_ids = []
        self.submitted_orders = []
        self._default_tick_size = "0.01"
        self._default_neg_risk = False

    async def get_open_orders(self):
        return list(self._open_orders)

    async def cancel_order(self, client_order_id):
        self.cancelled_client_order_ids.append(client_order_id)
        return True

    async def submit_order(self, order):
        self.submitted_orders.append(order)
        return order.model_copy(update={"status": OrderStatus.OPEN})

    def drain_alerts(self):
        return []


class FakeRestClient:
    def __init__(self, balance="25000000"):
        self.cancelled_exchange_order_ids = []
        self.clob_client = SimpleNamespace(get_address=lambda: "0xabc")
        self._balance = balance

    async def cancel_order(self, exchange_order_id):
        self.cancelled_exchange_order_ids.append(exchange_order_id)
        return True

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def get_balance_allowance(self, asset_type="COLLATERAL", token_id=None):
        return {"balance": self._balance}


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


def _wallet(qty_yes="0", qty_no="0", available_balance="100"):
    position = SimpleNamespace(qty_yes=Decimal(qty_yes), qty_no=Decimal(qty_no))
    return SimpleNamespace(
        available_balance=Decimal(available_balance),
        get_position=lambda _market_id: position,
    )


@pytest.mark.asyncio
async def test_submit_order_clamps_partial_sell_instead_of_complement_buy():
    rest_client = FakeRestClient()
    execution = FakeExecution([])
    wallet = _wallet(qty_yes="7")
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()], wallet_adapter=wallet)
    order = Order(
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.SELL,
        price=Decimal("0.60"),
        size=Decimal("10"),
    )

    submitted = await adapter.submit_order(order)

    assert submitted.side == Side.SELL
    assert submitted.token_id == "yes-1"
    assert submitted.size == Decimal("7")
    assert len(execution.submitted_orders) == 1


@pytest.mark.asyncio
async def test_submit_order_rejects_subminimum_partial_sell():
    rest_client = FakeRestClient()
    execution = FakeExecution([])
    wallet = _wallet(qty_yes="3")
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()], wallet_adapter=wallet)
    order = Order(
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.SELL,
        price=Decimal("0.60"),
        size=Decimal("10"),
    )

    submitted = await adapter.submit_order(order)

    assert submitted.status == OrderStatus.REJECTED
    assert execution.submitted_orders == []


@pytest.mark.asyncio
async def test_submit_order_routes_complement_only_when_inventory_is_zero():
    rest_client = FakeRestClient()
    execution = FakeExecution([])
    wallet = _wallet(qty_yes="0", available_balance="100")
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()], wallet_adapter=wallet)
    order = Order(
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.SELL,
        price=Decimal("0.60"),
        size=Decimal("5"),
    )

    submitted = await adapter.submit_order(order)

    assert submitted.side == Side.BUY
    assert submitted.token_id == "no-1"
    assert submitted.price == Decimal("0.40")
    assert len(execution.submitted_orders) == 1


@pytest.mark.asyncio
async def test_submit_order_rejects_buy_when_collateral_is_insufficient():
    rest_client = FakeRestClient(balance="1000000")
    execution = FakeExecution([])
    wallet = _wallet(available_balance="1")
    adapter = LiveVenueAdapter(execution, rest_client, [_market_config()], wallet_adapter=wallet)
    order = Order(
        market_id="mkt-1",
        token_id="yes-1",
        side=Side.BUY,
        price=Decimal("0.50"),
        size=Decimal("5"),
    )

    submitted = await adapter.submit_order(order)

    assert submitted.status == OrderStatus.REJECTED
    assert execution.submitted_orders == []
