"""ChaosInjector — injects realistic failure scenarios into PaperVenue.

Configurable chaos events:
- ``tick_size_change``: changes tick size when price > 0.96 or < 0.04
- ``engine_restart``: pauses matching for ~90s simulated (HTTP 425)
- ``ws_disconnect``: simulates WebSocket disconnection
- ``latency_spike``: adds extra latency to fills

All events are published to the EventBus on the ``chaos`` topic.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog

from core.event_bus import EventBus

if TYPE_CHECKING:
    from paper.paper_venue import PaperVenue

logger = structlog.get_logger("paper.chaos_injector")


@dataclass
class ChaosConfig:
    """Probability and toggle configuration for each chaos event type."""

    enabled: bool = True

    # Per-cycle probability of each event type (0.0 – 1.0)
    tick_size_change_prob: float = 0.02
    engine_restart_prob: float = 0.005
    ws_disconnect_prob: float = 0.01
    latency_spike_prob: float = 0.05

    # ENGINE_RESTART simulated pause duration in seconds
    engine_restart_duration_s: float = 5.0  # real seconds (scaled down for testing)

    # Latency spike magnitude (seconds added)
    latency_spike_extra_s: float = 2.0

    # Cycle interval in seconds
    cycle_interval_s: float = 2.0


class ChaosInjector:
    """Periodically injects chaos events into a ``PaperVenue``.

    Usage::

        injector = ChaosInjector(venue, event_bus, config=ChaosConfig(enabled=True))
        await injector.start()
        # ... later
        await injector.stop()
    """

    def __init__(
        self,
        venue: PaperVenue,
        event_bus: EventBus,
        config: ChaosConfig | None = None,
        seed: int | None = None,
    ) -> None:
        self._venue = venue
        self._event_bus = event_bus
        self._config = config or ChaosConfig()
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

        import random
        self._rng = random.Random(seed)

    @property
    def config(self) -> ChaosConfig:
        return self._config

    @config.setter
    def config(self, value: ChaosConfig) -> None:
        self._config = value

    async def start(self) -> None:
        """Start the chaos injection loop."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("chaos_injector.started", enabled=self._config.enabled)

    async def stop(self) -> None:
        """Stop the chaos injection loop."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("chaos_injector.stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.cycle_interval_s)
            if not self._config.enabled:
                continue

            # Check each chaos event type
            await self._maybe_tick_size_change()
            await self._maybe_engine_restart()
            await self._maybe_ws_disconnect()
            await self._maybe_latency_spike()

    async def _maybe_tick_size_change(self) -> None:
        if self._rng.random() >= self._config.tick_size_change_prob:
            return

        # Find markets with extreme prices
        markets = await self._venue.get_active_markets()
        for state in markets:
            mid = state.mid_price
            if mid > Decimal("0.96") or (
                mid > Decimal("0") and mid < Decimal("0.04")
            ):
                old_tick = state.tick_size
                new_tick = Decimal("0.001")
                if old_tick == new_tick:
                    new_tick = Decimal("0.01")  # toggle back

                self._venue.change_tick_size(state.market_id, new_tick)
                await self._event_bus.publish(
                    "chaos",
                    {
                        "type": "tick_size_change",
                        "market_id": state.market_id,
                        "old_tick_size": str(old_tick),
                        "new_tick_size": str(new_tick),
                    },
                )
                logger.info(
                    "chaos.tick_size_change",
                    market_id=state.market_id,
                    old=str(old_tick),
                    new=str(new_tick),
                )

    async def _maybe_engine_restart(self) -> None:
        if self._rng.random() >= self._config.engine_restart_prob:
            return

        self._venue.pause_matching()
        await self._event_bus.publish(
            "chaos",
            {
                "type": "engine_restart",
                "duration_s": self._config.engine_restart_duration_s,
                "status": "paused",
            },
        )
        logger.warning(
            "chaos.engine_restart",
            duration_s=self._config.engine_restart_duration_s,
        )

        await asyncio.sleep(self._config.engine_restart_duration_s)

        self._venue.resume_matching()
        await self._event_bus.publish(
            "chaos",
            {
                "type": "engine_restart",
                "status": "resumed",
            },
        )

    async def _maybe_ws_disconnect(self) -> None:
        if self._rng.random() >= self._config.ws_disconnect_prob:
            return

        await self._event_bus.publish(
            "chaos",
            {
                "type": "ws_disconnect",
                "status": "disconnected",
            },
        )
        logger.warning("chaos.ws_disconnect")

        # Brief simulated disconnect
        await asyncio.sleep(0.5)

        await self._event_bus.publish(
            "chaos",
            {
                "type": "ws_disconnect",
                "status": "reconnected",
            },
        )

    async def _maybe_latency_spike(self) -> None:
        if self._rng.random() >= self._config.latency_spike_prob:
            return

        await self._event_bus.publish(
            "chaos",
            {
                "type": "latency_spike",
                "extra_ms": int(self._config.latency_spike_extra_s * 1000),
            },
        )
        logger.info(
            "chaos.latency_spike",
            extra_ms=int(self._config.latency_spike_extra_s * 1000),
        )
