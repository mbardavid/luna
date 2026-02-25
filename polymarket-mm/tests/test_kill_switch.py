"""Tests for core.kill_switch — every trigger type + state transitions."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from core.event_bus import EventBus
from core.kill_switch import KillSwitch, KillSwitchState, KillTrigger


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def cancel_all_mock() -> AsyncMock:
    mock = AsyncMock(return_value=3)
    return mock


@pytest.fixture
def cancel_market_mock() -> AsyncMock:
    mock = AsyncMock(return_value=1)
    return mock


@pytest.fixture
def ks(event_bus: EventBus, cancel_all_mock: AsyncMock, cancel_market_mock: AsyncMock) -> KillSwitch:
    return KillSwitch(
        event_bus=event_bus,
        alert_manager=None,
        order_cancel_callback=cancel_all_mock,
        market_cancel_callback=cancel_market_mock,
        max_daily_loss_usd=Decimal("100"),
        engine_restart_base_seconds=1,
        engine_restart_max_seconds=4,
        data_gap_tolerance_seconds=8,
    )


# ── Tests: Initial state ────────────────────────────────────────────


class TestKillSwitchInit:
    def test_initial_state_is_running(self, ks: KillSwitch) -> None:
        assert ks.state == KillSwitchState.RUNNING
        assert ks.is_running is True
        assert ks.is_halted is False

    def test_initial_daily_loss_is_zero(self, ks: KillSwitch) -> None:
        assert ks.daily_loss == Decimal("0")

    def test_initial_trigger_history_empty(self, ks: KillSwitch) -> None:
        assert ks.trigger_history == []

    def test_initial_paused_markets_empty(self, ks: KillSwitch) -> None:
        assert ks.paused_markets == set()


# ── Tests: ENGINE_RESTART ────────────────────────────────────────────


class TestEngineRestart:
    @pytest.mark.asyncio
    async def test_pauses_on_engine_restart(self, ks: KillSwitch) -> None:
        await ks.trigger_engine_restart()
        assert ks.state == KillSwitchState.PAUSED

    @pytest.mark.asyncio
    async def test_auto_resumes_after_backoff(self, ks: KillSwitch) -> None:
        await ks.trigger_engine_restart()
        assert ks.state == KillSwitchState.PAUSED
        # backoff = 1s * 2^0 = 1s
        await asyncio.sleep(1.2)
        assert ks.state == KillSwitchState.RUNNING

    @pytest.mark.asyncio
    async def test_exponential_backoff(self, ks: KillSwitch) -> None:
        # First restart: 1 * 2^0 = 1s
        await ks.trigger_engine_restart()
        history = ks.trigger_history
        assert history[-1].details["backoff_seconds"] == 1

        # Force state back to RUNNING without resetting counter
        # (simulates auto-resume completing)
        ks._state = KillSwitchState.RUNNING

        # Second restart: 1 * 2^1 = 2s
        await ks.trigger_engine_restart()
        history = ks.trigger_history
        assert history[-1].details["backoff_seconds"] == 2

        ks._state = KillSwitchState.RUNNING

        # Third restart: 1 * 2^2 = 4s (capped at max=4)
        await ks.trigger_engine_restart()
        history = ks.trigger_history
        assert history[-1].details["backoff_seconds"] == 4

    @pytest.mark.asyncio
    async def test_backoff_capped_at_max(self, ks: KillSwitch) -> None:
        # Trigger many restarts to exceed cap
        for _ in range(10):
            await ks.trigger_engine_restart()
            await ks.resume()

        await ks.trigger_engine_restart()
        history = ks.trigger_history
        assert history[-1].details["backoff_seconds"] <= 4

    @pytest.mark.asyncio
    async def test_records_trigger_history(self, ks: KillSwitch) -> None:
        await ks.trigger_engine_restart({"reason": "http_425"})
        assert len(ks.trigger_history) == 1
        assert ks.trigger_history[0].trigger == KillTrigger.ENGINE_RESTART

    @pytest.mark.asyncio
    async def test_publishes_event(self, ks: KillSwitch, event_bus: EventBus) -> None:
        events: list = []
        async def collect():
            async for event in event_bus.subscribe("kill_switch"):
                events.append(event)
                if event.payload.get("action") == "pause":
                    break

        task = asyncio.create_task(collect())
        await asyncio.sleep(0.05)
        await ks.trigger_engine_restart()
        await asyncio.wait_for(task, timeout=2.0)
        assert len(events) >= 1
        assert events[0].payload["action"] == "pause"
        assert events[0].payload["trigger"] == "ENGINE_RESTART"


# ── Tests: HEARTBEAT_MISSED ─────────────────────────────────────────


class TestHeartbeatMissed:
    @pytest.mark.asyncio
    async def test_halts_on_heartbeat_missed(self, ks: KillSwitch) -> None:
        await ks.trigger_heartbeat_missed()
        assert ks.state == KillSwitchState.HALTED
        assert ks.is_halted is True

    @pytest.mark.asyncio
    async def test_cancels_all_orders(self, ks: KillSwitch, cancel_all_mock: AsyncMock) -> None:
        await ks.trigger_heartbeat_missed()
        cancel_all_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_records_trigger(self, ks: KillSwitch) -> None:
        await ks.trigger_heartbeat_missed()
        assert len(ks.trigger_history) == 1
        assert ks.trigger_history[0].trigger == KillTrigger.HEARTBEAT_MISSED

    @pytest.mark.asyncio
    async def test_ignores_if_already_halted(self, ks: KillSwitch, cancel_all_mock: AsyncMock) -> None:
        await ks.trigger_heartbeat_missed()
        cancel_all_mock.reset_mock()
        await ks.trigger_heartbeat_missed()
        cancel_all_mock.assert_not_awaited()


# ── Tests: DATA_GAP ─────────────────────────────────────────────────


class TestDataGap:
    @pytest.mark.asyncio
    async def test_pauses_market_on_data_gap(self, ks: KillSwitch) -> None:
        await ks.trigger_data_gap("market-1", gap_seconds=10.0)
        assert "market-1" in ks.paused_markets

    @pytest.mark.asyncio
    async def test_cancels_market_orders(self, ks: KillSwitch, cancel_market_mock: AsyncMock) -> None:
        await ks.trigger_data_gap("market-1", gap_seconds=10.0)
        cancel_market_mock.assert_awaited_once_with("market-1")

    @pytest.mark.asyncio
    async def test_ignores_gap_below_tolerance(self, ks: KillSwitch, cancel_market_mock: AsyncMock) -> None:
        await ks.trigger_data_gap("market-1", gap_seconds=5.0)
        cancel_market_mock.assert_not_awaited()
        assert "market-1" not in ks.paused_markets

    @pytest.mark.asyncio
    async def test_does_not_halt_system(self, ks: KillSwitch) -> None:
        await ks.trigger_data_gap("market-1", gap_seconds=10.0)
        # DATA_GAP is contextual — system stays RUNNING
        assert ks.state == KillSwitchState.RUNNING

    @pytest.mark.asyncio
    async def test_data_update_unpauses_market(self, ks: KillSwitch) -> None:
        await ks.trigger_data_gap("market-1", gap_seconds=10.0)
        assert "market-1" in ks.paused_markets
        ks.record_data_update("market-1")
        assert "market-1" not in ks.paused_markets


# ── Tests: MAX_DRAWDOWN ─────────────────────────────────────────────


class TestMaxDrawdown:
    @pytest.mark.asyncio
    async def test_halts_on_max_drawdown(self, ks: KillSwitch) -> None:
        await ks.trigger_max_drawdown(Decimal("150"))
        assert ks.state == KillSwitchState.HALTED

    @pytest.mark.asyncio
    async def test_cancels_all_orders(self, ks: KillSwitch, cancel_all_mock: AsyncMock) -> None:
        await ks.trigger_max_drawdown(Decimal("150"))
        cancel_all_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_does_not_halt_below_limit(self, ks: KillSwitch) -> None:
        await ks.trigger_max_drawdown(Decimal("50"))
        assert ks.state == KillSwitchState.RUNNING

    @pytest.mark.asyncio
    async def test_tracks_daily_loss(self, ks: KillSwitch) -> None:
        await ks.trigger_max_drawdown(Decimal("50"))
        assert ks.daily_loss == Decimal("50")

    @pytest.mark.asyncio
    async def test_records_trigger_on_breach(self, ks: KillSwitch) -> None:
        await ks.trigger_max_drawdown(Decimal("150"))
        assert len(ks.trigger_history) == 1
        assert ks.trigger_history[0].trigger == KillTrigger.MAX_DRAWDOWN


# ── Tests: RECONCILIATION_MISMATCH ──────────────────────────────────


class TestReconciliationMismatch:
    @pytest.mark.asyncio
    async def test_halts_on_mismatch(self, ks: KillSwitch) -> None:
        mismatches = [{"type": "ghost_order", "detail": "order xyz missing from venue"}]
        await ks.trigger_reconciliation_mismatch(mismatches)
        assert ks.state == KillSwitchState.HALTED

    @pytest.mark.asyncio
    async def test_cancels_all_orders(self, ks: KillSwitch, cancel_all_mock: AsyncMock) -> None:
        mismatches = [{"type": "fill_mismatch", "detail": "qty diverged"}]
        await ks.trigger_reconciliation_mismatch(mismatches)
        cancel_all_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_records_trigger(self, ks: KillSwitch) -> None:
        mismatches = [{"type": "orphan_order", "detail": "unknown order"}]
        await ks.trigger_reconciliation_mismatch(mismatches)
        assert len(ks.trigger_history) == 1
        assert ks.trigger_history[0].trigger == KillTrigger.RECONCILIATION_MISMATCH


# ── Tests: Manual controls ──────────────────────────────────────────


class TestManualControls:
    @pytest.mark.asyncio
    async def test_manual_resume_from_paused(self, ks: KillSwitch) -> None:
        await ks.trigger_engine_restart()
        assert ks.state == KillSwitchState.PAUSED
        await ks.resume()
        assert ks.state == KillSwitchState.RUNNING

    @pytest.mark.asyncio
    async def test_resume_ignored_from_halted(self, ks: KillSwitch) -> None:
        await ks.trigger_heartbeat_missed()
        assert ks.state == KillSwitchState.HALTED
        await ks.resume()
        assert ks.state == KillSwitchState.HALTED  # still halted

    @pytest.mark.asyncio
    async def test_reset_from_halted(self, ks: KillSwitch) -> None:
        await ks.trigger_heartbeat_missed()
        assert ks.state == KillSwitchState.HALTED
        await ks.reset()
        assert ks.state == KillSwitchState.RUNNING
        assert ks.daily_loss == Decimal("0")
        assert ks.paused_markets == set()

    @pytest.mark.asyncio
    async def test_reset_from_paused(self, ks: KillSwitch) -> None:
        await ks.trigger_engine_restart()
        await ks.reset()
        assert ks.state == KillSwitchState.RUNNING


# ── Tests: Heartbeat tracking ───────────────────────────────────────


class TestHeartbeatTracking:
    def test_heartbeat_age_increases(self, ks: KillSwitch) -> None:
        ks.record_heartbeat()
        import time
        time.sleep(0.05)
        assert ks.heartbeat_age() >= 0.04

    def test_record_heartbeat_resets_restart_counter(self, ks: KillSwitch) -> None:
        ks._restart_consecutive = 5
        ks.record_heartbeat()
        assert ks._restart_consecutive == 0


# ── Tests: Data gap checking ────────────────────────────────────────


class TestDataGapCheck:
    def test_check_data_gaps_empty_when_fresh(self, ks: KillSwitch) -> None:
        ks.record_data_update("market-1")
        gaps = ks.check_data_gaps()
        assert len(gaps) == 0

    def test_check_data_gaps_detects_stale(self, ks: KillSwitch) -> None:
        import time as _time
        ks.record_data_update("market-1")
        # Manually set timestamp in the past
        ks._last_data_timestamps["market-1"] = _time.monotonic() - 20
        gaps = ks.check_data_gaps()
        assert "market-1" in gaps
        assert gaps["market-1"] >= 10
