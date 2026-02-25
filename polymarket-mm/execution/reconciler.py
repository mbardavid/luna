"""Reconciler — order/position/balance reconciliation against venue state.

Periodically compares local state (OrderManager) with venue state
(ExecutionProvider) to detect mismatches such as:
- Ghost orders: locally tracked but not on venue
- Orphan orders: on venue but not locally tracked
- Fill mismatches: filled_qty divergence
- Balance divergences: (placeholder for on-chain balance checks)

Emits ``RECONCILIATION_MISMATCH`` events via the KillSwitch when
divergences are detected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from config.settings import settings
from core.event_bus import EventBus
from execution.execution_provider import ExecutionProvider
from execution.order_manager import OrderManager
from models.order import Order

logger = structlog.get_logger("execution.reconciler")


@dataclass(frozen=True, slots=True)
class Mismatch:
    """Represents a single reconciliation mismatch."""

    type: str
    detail: str
    local_order: Order | None = None
    venue_order: Order | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for event payloads."""
        d: dict[str, Any] = {"type": self.type, "detail": self.detail}
        if self.local_order:
            d["local_client_order_id"] = str(self.local_order.client_order_id)
        if self.venue_order:
            d["venue_client_order_id"] = str(self.venue_order.client_order_id)
        d.update(self.extra)
        return d


class Reconciler:
    """Periodic reconciliation of local vs venue order/position state.

    Parameters
    ----------
    event_bus:
        EventBus for publishing reconciliation events.
    order_manager:
        Local order tracker.
    execution_provider:
        Venue execution backend for fetching real order state.
    interval_seconds:
        Seconds between reconciliation cycles.
    mismatch_callback:
        Async callable invoked when mismatches are found.  Signature:
        ``async (mismatches: list[dict]) -> None``.
        Typically wired to ``KillSwitch.trigger_reconciliation_mismatch``.
    """

    def __init__(
        self,
        event_bus: EventBus,
        order_manager: OrderManager,
        execution_provider: ExecutionProvider,
        interval_seconds: int | None = None,
        mismatch_callback: Any = None,
    ) -> None:
        self._event_bus = event_bus
        self._order_manager = order_manager
        self._provider = execution_provider
        self._interval = interval_seconds or settings.RECONCILIATION_INTERVAL_SECONDS
        self._mismatch_callback = mismatch_callback

        # State
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_run: datetime | None = None
        self._total_runs: int = 0
        self._total_mismatches: int = 0

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the periodic reconciliation loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("reconciler.started", interval=self._interval)

    async def stop(self) -> None:
        """Stop the reconciliation loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "reconciler.stopped",
            total_runs=self._total_runs,
            total_mismatches=self._total_mismatches,
        )

    # ── Single reconciliation cycle ──────────────────────────────

    async def reconcile(self) -> list[Mismatch]:
        """Run a single reconciliation cycle.

        Returns the list of detected mismatches (empty if clean).
        """
        mismatches: list[Mismatch] = []

        try:
            # Fetch venue orders
            venue_orders = await self._provider.get_open_orders()
        except Exception:
            logger.exception("reconciler.venue_fetch_failed")
            mismatches.append(
                Mismatch(
                    type="venue_fetch_error",
                    detail="Failed to fetch open orders from venue",
                )
            )
            await self._handle_mismatches(mismatches)
            return mismatches

        # Build lookup maps
        local_active = self._order_manager.get_active_orders()
        local_by_id: dict[UUID, Order] = {o.client_order_id: o for o in local_active}
        venue_by_id: dict[UUID, Order] = {o.client_order_id: o for o in venue_orders}

        # 1. Ghost orders: in local but NOT on venue
        for cid, local_order in local_by_id.items():
            if cid not in venue_by_id:
                mismatches.append(
                    Mismatch(
                        type="ghost_order",
                        detail=f"Order {cid} tracked locally but not found on venue",
                        local_order=local_order,
                    )
                )

        # 2. Orphan orders: on venue but NOT locally tracked
        for cid, venue_order in venue_by_id.items():
            if cid not in local_by_id:
                mismatches.append(
                    Mismatch(
                        type="orphan_order",
                        detail=f"Order {cid} exists on venue but not tracked locally",
                        venue_order=venue_order,
                    )
                )

        # 3. Fill mismatches: both exist but filled_qty differs
        for cid in local_by_id.keys() & venue_by_id.keys():
            local_order = local_by_id[cid]
            venue_order = venue_by_id[cid]
            if local_order.filled_qty != venue_order.filled_qty:
                mismatches.append(
                    Mismatch(
                        type="fill_mismatch",
                        detail=(
                            f"Order {cid} filled_qty mismatch: "
                            f"local={local_order.filled_qty} venue={venue_order.filled_qty}"
                        ),
                        local_order=local_order,
                        venue_order=venue_order,
                        extra={
                            "local_filled": str(local_order.filled_qty),
                            "venue_filled": str(venue_order.filled_qty),
                        },
                    )
                )

        # Update stats
        self._total_runs += 1
        self._last_run = datetime.now(timezone.utc)

        if mismatches:
            self._total_mismatches += len(mismatches)
            logger.warning(
                "reconciler.mismatches_detected",
                count=len(mismatches),
                types=[m.type for m in mismatches],
            )
            await self._handle_mismatches(mismatches)
        else:
            logger.debug("reconciler.clean", run=self._total_runs)

        # Publish reconciliation result event
        await self._event_bus.publish(
            "reconciliation",
            {
                "status": "mismatch" if mismatches else "clean",
                "mismatch_count": len(mismatches),
                "local_active": len(local_active),
                "venue_open": len(venue_orders),
                "run_number": self._total_runs,
            },
        )

        return mismatches

    # ── Stats ────────────────────────────────────────────────────

    @property
    def stats(self) -> dict[str, Any]:
        """Return reconciler statistics."""
        return {
            "total_runs": self._total_runs,
            "total_mismatches": self._total_mismatches,
            "last_run": self._last_run.isoformat() if self._last_run else None,
            "interval_seconds": self._interval,
            "running": self._running,
        }

    # ── Internals ────────────────────────────────────────────────

    async def _loop(self) -> None:
        """Periodic reconciliation loop."""
        while self._running:
            try:
                await asyncio.sleep(self._interval)
                await self.reconcile()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("reconciler.loop_error")
                # Continue running — don't crash the loop on transient errors
                await asyncio.sleep(self._interval)

    async def _handle_mismatches(self, mismatches: list[Mismatch]) -> None:
        """Invoke the mismatch callback (typically KillSwitch)."""
        if self._mismatch_callback is None:
            return

        mismatch_dicts = [m.to_dict() for m in mismatches]
        try:
            await self._mismatch_callback(mismatch_dicts)
        except Exception:
            logger.exception("reconciler.callback_failed")
