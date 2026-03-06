from __future__ import annotations

from decimal import Decimal

import pytest

from execution.live_execution import LiveExecution
from models.order import Order, OrderStatus, Side


class FakeLatencyRecorder:
    def __init__(self) -> None:
        self.order_acks = []
        self.rejections = []

    def record_order_ack(self, ack_ms, *, market_id, decision_id, status, error_code=""):
        self.order_acks.append({
            "ack_ms": ack_ms,
            "market_id": market_id,
            "decision_id": decision_id,
            "status": status,
            "error_code": error_code,
        })

    def record_rejection(self, *, market_id, decision_id, rejection_reason, latency_bucket):
        self.rejections.append({
            "market_id": market_id,
            "decision_id": decision_id,
            "rejection_reason": rejection_reason,
            "latency_bucket": latency_bucket,
        })


class RejectingRestClient:
    async def create_and_post_order(self, **kwargs):
        return {"error": "not enough balance / allowance"}

    async def cancel_order(self, exchange_id):
        return True

    async def get_open_orders(self):
        return []


class OpenOrdersRestClient(RejectingRestClient):
    async def get_open_orders(self):
        return [
            {
                "id": "venue-order-123",
                "market": "0xmarket",
                "asset_id": "0xtoken",
                "side": "BUY",
                "price": "0.45",
                "original_size": "20",
                "size_matched": "5",
            }
        ]


@pytest.mark.asyncio
async def test_live_execution_raises_critical_alert_for_balance_allowance():
    recorder = FakeLatencyRecorder()
    execution = LiveExecution(
        rest_client=RejectingRestClient(),
        latency_recorder=recorder,
        decision_id="quant-001",
    )
    order = Order(
        market_id="0xmarket",
        token_id="0xtoken",
        side=Side.BUY,
        price=Decimal("0.45"),
        size=Decimal("20"),
    )

    result = await execution.submit_order(order)

    assert result.status == OrderStatus.REJECTED
    alerts = execution.drain_alerts()
    assert len(alerts) == 1
    assert alerts[0].code == "BALANCE_ALLOWANCE_MISMATCH"
    assert alerts[0].critical is True
    assert recorder.order_acks[0]["status"] == "REJECTED"
    assert recorder.rejections[0]["rejection_reason"] == "BALANCE_ALLOWANCE_MISMATCH"


@pytest.mark.asyncio
async def test_live_execution_flags_cancel_unknown_order():
    execution = LiveExecution(rest_client=RejectingRestClient(), decision_id="quant-001")
    order = Order(
        market_id="0xmarket",
        token_id="0xtoken",
        side=Side.BUY,
        price=Decimal("0.45"),
        size=Decimal("20"),
    )
    ok = await execution.cancel_order(order.client_order_id)
    assert ok is False
    alerts = execution.drain_alerts()
    assert alerts[0].code == "CANCEL_UNKNOWN_ORDER"


@pytest.mark.asyncio
async def test_live_execution_preserves_exchange_order_id_on_open_orders():
    execution = LiveExecution(rest_client=OpenOrdersRestClient(), decision_id="quant-001")

    orders = await execution.get_open_orders()

    assert len(orders) == 1
    assert orders[0].exchange_order_id == "venue-order-123"
    assert orders[0].status == OrderStatus.OPEN
