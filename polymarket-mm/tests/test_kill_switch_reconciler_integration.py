"""Tests for kill_switch + reconciler integration."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from core.event_bus import EventBus
from core.kill_switch import KillSwitch, KillSwitchState
from execution.execution_provider import ExecutionProvider
from execution.order_manager import OrderManager
from execution.reconciler import Reconciler
from models.order import Order, OrderStatus, Side


# ── Mock provider ────────────────────────────────────────────────────


class _MockProvider(ExecutionProvider):
    """Minimal mock for integration tests."""

    def __init__(self, open_orders: list[Order] | None = None) -> None:
        self._open_orders = open_orders or []
        self._cancel_count = 0

    async def submit_order(self, order: Order) -> Order:
        return order.model_copy(update={"status": OrderStatus.OPEN})

    async def cancel_order(self, client_order_id) -> bool:
        self._cancel_count += 1
        return True

    async def amend_order(self, client_order_id, new_price, new_size) -> Order:
        raise NotImplementedError

    async def get_open_orders(self) -> list[Order]:
        return list(self._open_orders)


def _make_order(**kwargs) -> Order:
    defaults = {
        "market_id": "test-market",
        "token_id": "test-token",
        "side": Side.BUY,
        "price": Decimal("0.50"),
        "size": Decimal("100"),
        "status": OrderStatus.OPEN,
    }
    defaults.update(kwargs)
    return Order(**defaults)


# ── Integration Tests ────────────────────────────────────────────────


class TestKillSwitchReconcilerIntegration:
    """Full integration: Reconciler detects mismatch → KillSwitch halts."""

    @pytest.mark.asyncio
    async def test_reconciler_mismatch_triggers_kill_switch_halt(self) -> None:
        event_bus = EventBus()
        provider = _MockProvider()
        om = OrderManager(provider=provider)

        # Create a kill switch wired to the order manager
        ks = KillSwitch(
            event_bus=event_bus,
            order_cancel_callback=om.cancel_all,
        )

        # Create reconciler wired to kill switch
        reconciler = Reconciler(
            event_bus=event_bus,
            order_manager=om,
            execution_provider=provider,
            mismatch_callback=ks.trigger_reconciliation_mismatch,
        )

        # Submit an order locally but NOT on venue → ghost order
        order = _make_order()
        om._orders[order.client_order_id] = order
        provider._open_orders = []  # venue doesn't know about it

        # Run reconciliation
        mismatches = await reconciler.reconcile()

        # Verify: mismatch detected AND kill switch halted
        assert len(mismatches) == 1
        assert mismatches[0].type == "ghost_order"
        assert ks.state == KillSwitchState.HALTED

    @pytest.mark.asyncio
    async def test_reconciler_clean_does_not_trigger_halt(self) -> None:
        event_bus = EventBus()
        provider = _MockProvider()
        om = OrderManager(provider=provider)

        ks = KillSwitch(
            event_bus=event_bus,
            order_cancel_callback=om.cancel_all,
        )

        reconciler = Reconciler(
            event_bus=event_bus,
            order_manager=om,
            execution_provider=provider,
            mismatch_callback=ks.trigger_reconciliation_mismatch,
        )

        # Both sides empty — clean
        mismatches = await reconciler.reconcile()
        assert mismatches == []
        assert ks.state == KillSwitchState.RUNNING

    @pytest.mark.asyncio
    async def test_orphan_order_triggers_halt(self) -> None:
        event_bus = EventBus()
        provider = _MockProvider()
        om = OrderManager(provider=provider)

        ks = KillSwitch(
            event_bus=event_bus,
            order_cancel_callback=om.cancel_all,
        )

        reconciler = Reconciler(
            event_bus=event_bus,
            order_manager=om,
            execution_provider=provider,
            mismatch_callback=ks.trigger_reconciliation_mismatch,
        )

        # Order on venue but not tracked locally → orphan
        orphan = _make_order()
        provider._open_orders = [orphan]

        mismatches = await reconciler.reconcile()
        assert len(mismatches) == 1
        assert mismatches[0].type == "orphan_order"
        assert ks.state == KillSwitchState.HALTED

    @pytest.mark.asyncio
    async def test_fill_mismatch_triggers_halt(self) -> None:
        event_bus = EventBus()
        provider = _MockProvider()
        om = OrderManager(provider=provider)

        ks = KillSwitch(
            event_bus=event_bus,
            order_cancel_callback=om.cancel_all,
        )

        reconciler = Reconciler(
            event_bus=event_bus,
            order_manager=om,
            execution_provider=provider,
            mismatch_callback=ks.trigger_reconciliation_mismatch,
        )

        # Same order id, different filled_qty
        cid = uuid4()
        local_order = _make_order(
            client_order_id=cid,
            filled_qty=Decimal("10"),
            status=OrderStatus.PARTIALLY_FILLED,
        )
        venue_order = _make_order(
            client_order_id=cid,
            filled_qty=Decimal("90"),
            status=OrderStatus.PARTIALLY_FILLED,
        )

        om._orders[cid] = local_order
        provider._open_orders = [venue_order]

        mismatches = await reconciler.reconcile()
        assert len(mismatches) == 1
        assert mismatches[0].type == "fill_mismatch"
        assert ks.state == KillSwitchState.HALTED

    @pytest.mark.asyncio
    async def test_event_bus_receives_both_events(self) -> None:
        """Both reconciliation and kill_switch events are published."""
        event_bus = EventBus()
        provider = _MockProvider()
        om = OrderManager(provider=provider)

        ks = KillSwitch(
            event_bus=event_bus,
            order_cancel_callback=om.cancel_all,
        )

        reconciler = Reconciler(
            event_bus=event_bus,
            order_manager=om,
            execution_provider=provider,
            mismatch_callback=ks.trigger_reconciliation_mismatch,
        )

        # Collect events
        kill_events: list = []
        recon_events: list = []

        async def collect_kill():
            async for event in event_bus.subscribe("kill_switch"):
                kill_events.append(event)
                break

        async def collect_recon():
            async for event in event_bus.subscribe("reconciliation"):
                recon_events.append(event)
                break

        task_kill = asyncio.create_task(collect_kill())
        task_recon = asyncio.create_task(collect_recon())
        await asyncio.sleep(0.05)

        # Create mismatch scenario
        orphan = _make_order()
        provider._open_orders = [orphan]

        await reconciler.reconcile()

        await asyncio.wait_for(
            asyncio.gather(task_kill, task_recon),
            timeout=2.0,
        )

        # Kill switch event
        assert len(kill_events) == 1
        assert kill_events[0].payload["action"] == "halt"
        assert kill_events[0].payload["trigger"] == "RECONCILIATION_MISMATCH"

        # Reconciliation event
        assert len(recon_events) == 1
        assert recon_events[0].payload["status"] == "mismatch"
