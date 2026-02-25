"""Entrypoint — uvloop event-loop, empty heartbeat, graceful shutdown."""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import NoReturn

import uvloop

from config.settings import settings
from core.logger import get_logger

log = get_logger(__name__)


class GracefulShutdown:
    """Tracks shutdown signal and provides a flag for the main loop."""

    def __init__(self) -> None:
        self._should_stop = asyncio.Event()

    @property
    def should_stop(self) -> bool:
        return self._should_stop.is_set()

    def trigger(self) -> None:
        self._should_stop.set()

    async def wait(self) -> None:
        await self._should_stop.wait()


async def heartbeat_loop(shutdown: GracefulShutdown) -> None:
    """Empty heartbeat loop — placeholder for future engine integration."""
    interval = settings.HEARTBEAT_INTERVAL_SECONDS
    log.info("heartbeat_started", interval_s=interval, env=settings.APP_ENV)

    while not shutdown.should_stop:
        log.debug("heartbeat_tick")
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=interval)
            break  # shutdown triggered
        except asyncio.TimeoutError:
            pass  # normal tick — loop again

    log.info("heartbeat_stopped")


async def main() -> None:
    """Top-level orchestrator."""
    log.info(
        "starting",
        app=settings.APP_NAME,
        env=settings.APP_ENV,
        heartbeat_interval=settings.HEARTBEAT_INTERVAL_SECONDS,
    )

    shutdown = GracefulShutdown()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: _handle_signal(s, shutdown))

    try:
        await heartbeat_loop(shutdown)
    except Exception:
        log.exception("fatal_error")
        sys.exit(1)

    log.info("shutdown_complete")


def _handle_signal(sig: signal.Signals, shutdown: GracefulShutdown) -> None:
    """Signal handler — sets the shutdown flag."""
    log.info("signal_received", signal=sig.name)
    shutdown.trigger()


def run() -> NoReturn:
    """CLI entry: install uvloop policy and run."""
    uvloop.install()
    asyncio.run(main())
    sys.exit(0)


if __name__ == "__main__":
    run()
