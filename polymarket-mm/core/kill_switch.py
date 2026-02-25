"""KillSwitch — contextual risk management with multiple trigger types.

Provides protection against:
- ``ENGINE_RESTART`` (HTTP 425): exponential backoff pause + auto-resume
- ``HEARTBEAT_MISSED``: full order cancellation + fatal alert
- ``DATA_GAP > 8s``: per-market order cancellation
- ``MAX_DRAWDOWN``: configurable daily loss limit, full halt
- ``RECONCILIATION_MISMATCH``: complete halt + alert

Integrates with ``EventBus`` for event-driven state transitions and
``AlertManager`` for webhook-based notifications.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

import structlog

from config.settings import settings
from core.alert_manager import AlertManager, AlertSeverity
from core.event_bus import EventBus

logger = structlog.get_logger("core.kill_switch")


class KillTrigger(str, Enum):
    """Enumeration of kill switch trigger types."""

    ENGINE_RESTART = "ENGINE_RESTART"
    HEARTBEAT_MISSED = "HEARTBEAT_MISSED"
    DATA_GAP = "DATA_GAP"
    MAX_DRAWDOWN = "MAX_DRAWDOWN"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"


class KillSwitchState(str, Enum):
    """Overall kill switch state."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"         # temporary pause (ENGINE_RESTART backoff)
    HALTED = "HALTED"         # full stop — requires manual intervention


@dataclass
class TriggerRecord:
    """Record of a kill switch trigger activation."""

    trigger: KillTrigger
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict[str, Any] = field(default_factory=dict)


class KillSwitch:
    """Contextual kill switch with multiple trigger types.

    Parameters
    ----------
    event_bus:
        EventBus for publishing kill/pause/resume events.
    alert_manager:
        AlertManager for sending webhook notifications.
    order_cancel_callback:
        Async callable that cancels all orders.  Signature:
        ``async () -> int`` returning number of orders cancelled.
    market_cancel_callback:
        Async callable that cancels orders for a specific market.
        Signature: ``async (market_id: str) -> int``.
    max_daily_loss_usd:
        Maximum daily loss in USD before MAX_DRAWDOWN trigger.
    engine_restart_base_seconds:
        Base backoff for ENGINE_RESTART (exponential).
    engine_restart_max_seconds:
        Maximum backoff cap for ENGINE_RESTART.
    data_gap_tolerance_seconds:
        Seconds of data gap before DATA_GAP trigger fires.
    """

    def __init__(
        self,
        event_bus: EventBus,
        alert_manager: AlertManager | None = None,
        order_cancel_callback: Any = None,
        market_cancel_callback: Any = None,
        max_daily_loss_usd: Decimal | None = None,
        engine_restart_base_seconds: int | None = None,
        engine_restart_max_seconds: int | None = None,
        data_gap_tolerance_seconds: int | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._alert_manager = alert_manager
        self._cancel_all = order_cancel_callback
        self._cancel_market = market_cancel_callback

        # Configuration with defaults from settings
        self._max_daily_loss = max_daily_loss_usd or settings.MAX_DAILY_LOSS_USD
        self._restart_base = engine_restart_base_seconds or settings.ENGINE_RESTART_BACKOFF_BASE_SECONDS
        self._restart_max = engine_restart_max_seconds or settings.ENGINE_RESTART_BACKOFF_MAX_SECONDS
        self._data_gap_tolerance = data_gap_tolerance_seconds or settings.DATA_GAP_TOLERANCE_SECONDS

        # State
        self._state = KillSwitchState.RUNNING
        self._restart_consecutive: int = 0
        self._daily_loss: Decimal = Decimal("0")
        self._daily_loss_reset_date: str = ""
        self._trigger_history: list[TriggerRecord] = []
        self._paused_markets: set[str] = set()
        self._last_heartbeat: float = time.monotonic()
        self._last_data_timestamps: dict[str, float] = {}
        self._resume_task: asyncio.Task[None] | None = None

    # ── Properties ───────────────────────────────────────────────

    @property
    def state(self) -> KillSwitchState:
        """Current kill switch state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """True if the system is in RUNNING state."""
        return self._state == KillSwitchState.RUNNING

    @property
    def is_halted(self) -> bool:
        """True if the system is in HALTED state (requires manual reset)."""
        return self._state == KillSwitchState.HALTED

    @property
    def trigger_history(self) -> list[TriggerRecord]:
        """Return a copy of the trigger history."""
        return list(self._trigger_history)

    @property
    def paused_markets(self) -> set[str]:
        """Return set of markets currently paused due to DATA_GAP."""
        return set(self._paused_markets)

    @property
    def daily_loss(self) -> Decimal:
        """Current tracked daily loss."""
        return self._daily_loss

    # ── Trigger handlers ─────────────────────────────────────────

    async def trigger_engine_restart(self, details: dict[str, Any] | None = None) -> None:
        """Handle ENGINE_RESTART (e.g. HTTP 425).

        Pauses for N seconds with exponential backoff, then auto-resumes.
        """
        if self._state == KillSwitchState.HALTED:
            return

        self._restart_consecutive += 1
        backoff = min(
            self._restart_base * (2 ** (self._restart_consecutive - 1)),
            self._restart_max,
        )

        record = TriggerRecord(
            trigger=KillTrigger.ENGINE_RESTART,
            details={**(details or {}), "backoff_seconds": backoff, "consecutive": self._restart_consecutive},
        )
        self._trigger_history.append(record)

        logger.warning(
            "kill_switch.engine_restart",
            backoff=backoff,
            consecutive=self._restart_consecutive,
        )

        self._state = KillSwitchState.PAUSED

        await self._event_bus.publish(
            "kill_switch",
            {
                "action": "pause",
                "trigger": KillTrigger.ENGINE_RESTART.value,
                "backoff_seconds": backoff,
                "consecutive": self._restart_consecutive,
            },
        )

        await self._send_alert(
            "Engine Restart — Paused",
            f"Backoff: {backoff}s (attempt #{self._restart_consecutive})",
            AlertSeverity.WARNING,
            record.details,
        )

        # Schedule auto-resume
        if self._resume_task and not self._resume_task.done():
            self._resume_task.cancel()
        self._resume_task = asyncio.create_task(self._auto_resume(backoff))

    async def trigger_heartbeat_missed(self, details: dict[str, Any] | None = None) -> None:
        """Handle HEARTBEAT_MISSED — cancel all orders + fatal alert."""
        if self._state == KillSwitchState.HALTED:
            return

        record = TriggerRecord(
            trigger=KillTrigger.HEARTBEAT_MISSED,
            details=details or {},
        )
        self._trigger_history.append(record)

        logger.critical("kill_switch.heartbeat_missed")

        self._state = KillSwitchState.HALTED

        cancelled = 0
        if self._cancel_all:
            try:
                cancelled = await self._cancel_all()
            except Exception:
                logger.exception("kill_switch.cancel_all_failed")

        await self._event_bus.publish(
            "kill_switch",
            {
                "action": "halt",
                "trigger": KillTrigger.HEARTBEAT_MISSED.value,
                "orders_cancelled": cancelled,
            },
        )

        await self._send_alert(
            "HEARTBEAT MISSED — HALTED",
            f"All orders cancelled ({cancelled}). System halted. Manual intervention required.",
            AlertSeverity.FATAL,
            {**(details or {}), "orders_cancelled": cancelled},
        )

    async def trigger_data_gap(self, market_id: str, gap_seconds: float, details: dict[str, Any] | None = None) -> None:
        """Handle DATA_GAP > tolerance — cancel orders for affected market."""
        if self._state == KillSwitchState.HALTED:
            return

        if gap_seconds < self._data_gap_tolerance:
            return

        record = TriggerRecord(
            trigger=KillTrigger.DATA_GAP,
            details={**(details or {}), "market_id": market_id, "gap_seconds": gap_seconds},
        )
        self._trigger_history.append(record)

        logger.warning(
            "kill_switch.data_gap",
            market_id=market_id,
            gap_seconds=gap_seconds,
            tolerance=self._data_gap_tolerance,
        )

        self._paused_markets.add(market_id)

        cancelled = 0
        if self._cancel_market:
            try:
                cancelled = await self._cancel_market(market_id)
            except Exception:
                logger.exception("kill_switch.cancel_market_failed", market_id=market_id)

        await self._event_bus.publish(
            "kill_switch",
            {
                "action": "pause_market",
                "trigger": KillTrigger.DATA_GAP.value,
                "market_id": market_id,
                "gap_seconds": gap_seconds,
                "orders_cancelled": cancelled,
            },
        )

        await self._send_alert(
            f"Data Gap — Market Paused",
            f"Market `{market_id}` paused after {gap_seconds:.1f}s data gap.",
            AlertSeverity.WARNING,
            {"market_id": market_id, "gap_seconds": gap_seconds, "orders_cancelled": cancelled},
        )

    async def trigger_max_drawdown(self, current_loss: Decimal, details: dict[str, Any] | None = None) -> None:
        """Handle MAX_DRAWDOWN — full halt when daily loss exceeds limit."""
        if self._state == KillSwitchState.HALTED:
            return

        self._reset_daily_loss_if_new_day()
        self._daily_loss = current_loss

        if current_loss < self._max_daily_loss:
            return

        record = TriggerRecord(
            trigger=KillTrigger.MAX_DRAWDOWN,
            details={
                **(details or {}),
                "current_loss": str(current_loss),
                "max_allowed": str(self._max_daily_loss),
            },
        )
        self._trigger_history.append(record)

        logger.critical(
            "kill_switch.max_drawdown",
            current_loss=str(current_loss),
            max_daily_loss=str(self._max_daily_loss),
        )

        self._state = KillSwitchState.HALTED

        cancelled = 0
        if self._cancel_all:
            try:
                cancelled = await self._cancel_all()
            except Exception:
                logger.exception("kill_switch.cancel_all_failed")

        await self._event_bus.publish(
            "kill_switch",
            {
                "action": "halt",
                "trigger": KillTrigger.MAX_DRAWDOWN.value,
                "current_loss": str(current_loss),
                "max_daily_loss": str(self._max_daily_loss),
                "orders_cancelled": cancelled,
            },
        )

        await self._send_alert(
            "MAX DRAWDOWN — HALTED",
            f"Daily loss ${current_loss} exceeded limit ${self._max_daily_loss}. All orders cancelled.",
            AlertSeverity.FATAL,
            record.details,
        )

    async def trigger_reconciliation_mismatch(
        self, mismatches: list[dict[str, Any]], details: dict[str, Any] | None = None,
    ) -> None:
        """Handle RECONCILIATION_MISMATCH — complete halt + alert."""
        if self._state == KillSwitchState.HALTED:
            return

        record = TriggerRecord(
            trigger=KillTrigger.RECONCILIATION_MISMATCH,
            details={**(details or {}), "mismatch_count": len(mismatches)},
        )
        self._trigger_history.append(record)

        logger.critical(
            "kill_switch.reconciliation_mismatch",
            mismatch_count=len(mismatches),
            mismatches=mismatches[:5],  # log first 5 for brevity
        )

        self._state = KillSwitchState.HALTED

        cancelled = 0
        if self._cancel_all:
            try:
                cancelled = await self._cancel_all()
            except Exception:
                logger.exception("kill_switch.cancel_all_failed")

        await self._event_bus.publish(
            "kill_switch",
            {
                "action": "halt",
                "trigger": KillTrigger.RECONCILIATION_MISMATCH.value,
                "mismatch_count": len(mismatches),
                "orders_cancelled": cancelled,
            },
        )

        mismatch_summary = "; ".join(
            f"{m.get('type', '?')}: {m.get('detail', '?')}" for m in mismatches[:5]
        )
        await self._send_alert(
            "RECONCILIATION MISMATCH — HALTED",
            f"{len(mismatches)} mismatch(es) detected. System halted.\n\n{mismatch_summary}",
            AlertSeverity.FATAL,
            {"mismatch_count": len(mismatches), "orders_cancelled": cancelled},
        )

    # ── Market data tracking ─────────────────────────────────────

    def record_data_update(self, market_id: str) -> None:
        """Record that fresh data was received for a market.

        Should be called by the data layer on every book/trade update.
        If the market was previously paused due to data gap, it is
        automatically un-paused.
        """
        self._last_data_timestamps[market_id] = time.monotonic()
        self._paused_markets.discard(market_id)

    def check_data_gaps(self) -> dict[str, float]:
        """Return markets with data gaps exceeding tolerance.

        Returns a dict of ``{market_id: gap_seconds}`` for markets
        that haven't received data within the tolerance window.
        """
        now = time.monotonic()
        gaps: dict[str, float] = {}
        for market_id, last_ts in self._last_data_timestamps.items():
            gap = now - last_ts
            if gap > self._data_gap_tolerance:
                gaps[market_id] = gap
        return gaps

    # ── Heartbeat tracking ───────────────────────────────────────

    def record_heartbeat(self) -> None:
        """Record that a heartbeat was received from the venue."""
        self._last_heartbeat = time.monotonic()
        # Reset consecutive restart counter on healthy heartbeat
        self._restart_consecutive = 0

    def heartbeat_age(self) -> float:
        """Return seconds since last heartbeat."""
        return time.monotonic() - self._last_heartbeat

    # ── Manual controls ──────────────────────────────────────────

    async def resume(self) -> None:
        """Manually resume from PAUSED state.

        Does NOT resume from HALTED — use ``reset()`` for that.
        """
        if self._state != KillSwitchState.PAUSED:
            logger.warning("kill_switch.resume_ignored", current_state=self._state.value)
            return

        self._state = KillSwitchState.RUNNING
        self._restart_consecutive = 0

        await self._event_bus.publish(
            "kill_switch",
            {"action": "resume", "trigger": "manual"},
        )

        logger.info("kill_switch.resumed", source="manual")

    async def reset(self) -> None:
        """Reset from any state back to RUNNING.

        Clears daily loss, trigger history, and paused markets.
        Use with caution — intended for manual intervention after HALT.
        """
        old_state = self._state
        self._state = KillSwitchState.RUNNING
        self._restart_consecutive = 0
        self._daily_loss = Decimal("0")
        self._paused_markets.clear()

        await self._event_bus.publish(
            "kill_switch",
            {"action": "reset", "previous_state": old_state.value},
        )

        logger.info("kill_switch.reset", previous_state=old_state.value)

    # ── Internals ────────────────────────────────────────────────

    async def _auto_resume(self, backoff_seconds: int) -> None:
        """Auto-resume after ENGINE_RESTART backoff."""
        try:
            await asyncio.sleep(backoff_seconds)
        except asyncio.CancelledError:
            return

        if self._state == KillSwitchState.PAUSED:
            self._state = KillSwitchState.RUNNING

            await self._event_bus.publish(
                "kill_switch",
                {"action": "resume", "trigger": KillTrigger.ENGINE_RESTART.value},
            )

            logger.info("kill_switch.auto_resumed", backoff=backoff_seconds)

    def _reset_daily_loss_if_new_day(self) -> None:
        """Reset daily loss counter if the date has changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_loss_reset_date != today:
            self._daily_loss = Decimal("0")
            self._daily_loss_reset_date = today

    async def _send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send alert via AlertManager if configured."""
        if self._alert_manager is None:
            return
        try:
            await self._alert_manager.send_alert(title, message, severity, details)
        except Exception:
            logger.exception("kill_switch.alert_failed", title=title)
