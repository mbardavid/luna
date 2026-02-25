"""Health-check endpoints for liveness and readiness probes.

Provides an ``aiohttp``-free lightweight HTTP server (built on
``asyncio.start_server``) exposing:

- ``GET /health``  — liveness: always 200 while the process is alive
- ``GET /ready``   — readiness: 200 only when WS connected, orderbook
  synced, and kill switch not tripped
- ``GET /metrics`` — Prometheus text exposition
- ``GET /status``  — detailed JSON status blob

The server is intentionally minimal (stdlib only) so we don't drag in
a web framework dependency for three endpoints.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

import structlog

from monitoring.metrics import MetricsRegistry

logger = structlog.get_logger("monitoring.health")

__all__ = ["HealthCheck", "HealthStatus"]

_VERSION = "0.1.0"


class HealthStatus(str, Enum):
    """Readiness status values."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ComponentStatus:
    """Status of an individual sub-system."""

    name: str
    healthy: bool
    detail: str = ""
    last_check: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class HealthCheck:
    """Lightweight health-check manager and HTTP server.

    Parameters
    ----------
    metrics:
        Shared ``MetricsRegistry`` for ``/metrics`` endpoint.
    port:
        TCP port for the health server.
    host:
        Bind address.
    """

    def __init__(
        self,
        metrics: MetricsRegistry | None = None,
        port: int = 8080,
        host: str = "0.0.0.0",
    ) -> None:
        self._metrics = metrics
        self._port = port
        self._host = host
        self._start_time = time.monotonic()
        self._start_utc = datetime.now(timezone.utc)
        self._components: dict[str, ComponentStatus] = {}
        self._server: asyncio.Server | None = None
        self._extra_status: dict[str, Any] = {}

    # ── Component registration ──────────────────────────────────

    def register_component(self, name: str, healthy: bool = True, detail: str = "") -> None:
        """Register or update a component's health status."""
        self._components[name] = ComponentStatus(
            name=name,
            healthy=healthy,
            detail=detail,
        )

    def set_component_healthy(self, name: str, detail: str = "") -> None:
        """Mark a component as healthy."""
        self.register_component(name, healthy=True, detail=detail)

    def set_component_unhealthy(self, name: str, detail: str = "") -> None:
        """Mark a component as unhealthy."""
        self.register_component(name, healthy=False, detail=detail)

    def set_extra_status(self, key: str, value: Any) -> None:
        """Set an arbitrary key in the /status response."""
        self._extra_status[key] = value

    # ── Readiness evaluation ────────────────────────────────────

    @property
    def status(self) -> HealthStatus:
        """Aggregate health status across all registered components."""
        if not self._components:
            return HealthStatus.HEALTHY

        unhealthy = [c for c in self._components.values() if not c.healthy]
        if not unhealthy:
            return HealthStatus.HEALTHY
        if len(unhealthy) == len(self._components):
            return HealthStatus.UNHEALTHY
        return HealthStatus.DEGRADED

    @property
    def is_ready(self) -> bool:
        """True if all components are healthy."""
        return self.status == HealthStatus.HEALTHY

    @property
    def uptime_seconds(self) -> float:
        """Seconds since health-check manager was created."""
        return time.monotonic() - self._start_time

    # ── HTTP server lifecycle ───────────────────────────────────

    async def start_server(self) -> None:
        """Start the health-check HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_connection,
            self._host,
            self._port,
        )
        logger.info(
            "health.server_started",
            host=self._host,
            port=self._port,
        )

    async def stop_server(self) -> None:
        """Stop the health-check HTTP server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            logger.info("health.server_stopped")

    # ── HTTP handler ────────────────────────────────────────────

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            request_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not request_line:
                return

            line = request_line.decode("utf-8", errors="replace").strip()
            parts = line.split()
            if len(parts) < 2:
                await self._send_response(writer, 400, b"Bad Request")
                return

            method, path = parts[0], parts[1]

            # Drain remaining headers
            while True:
                header_line = await asyncio.wait_for(reader.readline(), timeout=5.0)
                if header_line in (b"\r\n", b"\n", b""):
                    break

            if method != "GET":
                await self._send_response(writer, 405, b"Method Not Allowed")
                return

            if path == "/health":
                await self._handle_health(writer)
            elif path == "/ready":
                await self._handle_ready(writer)
            elif path == "/metrics":
                await self._handle_metrics(writer)
            elif path == "/status":
                await self._handle_status(writer)
            else:
                await self._send_response(writer, 404, b"Not Found")

        except Exception:
            logger.exception("health.handler_error")
            try:
                await self._send_response(writer, 500, b"Internal Server Error")
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_health(self, writer: asyncio.StreamWriter) -> None:
        """Liveness probe — always 200 if process is running."""
        body = json.dumps({"status": "alive", "uptime_seconds": round(self.uptime_seconds, 1)})
        await self._send_response(writer, 200, body.encode(), content_type="application/json")

    async def _handle_ready(self, writer: asyncio.StreamWriter) -> None:
        """Readiness probe — 200 only when all components healthy."""
        ready = self.is_ready
        code = 200 if ready else 503
        body = json.dumps({
            "ready": ready,
            "status": self.status.value,
            "components": {
                name: {"healthy": comp.healthy, "detail": comp.detail}
                for name, comp in self._components.items()
            },
        })
        await self._send_response(writer, code, body.encode(), content_type="application/json")

    async def _handle_metrics(self, writer: asyncio.StreamWriter) -> None:
        """Prometheus /metrics endpoint."""
        if self._metrics:
            body = self._metrics.exposition()
            await self._send_response(
                writer, 200, body,
                content_type="text/plain; version=0.0.4; charset=utf-8",
            )
        else:
            await self._send_response(writer, 503, b"Metrics not configured")

    async def _handle_status(self, writer: asyncio.StreamWriter) -> None:
        """Detailed status JSON blob."""
        body = json.dumps({
            "version": _VERSION,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "started_at": self._start_utc.isoformat(),
            "status": self.status.value,
            "components": {
                name: {
                    "healthy": comp.healthy,
                    "detail": comp.detail,
                    "last_check": comp.last_check.isoformat(),
                }
                for name, comp in self._components.items()
            },
            **self._extra_status,
        })
        await self._send_response(writer, 200, body.encode(), content_type="application/json")

    @staticmethod
    async def _send_response(
        writer: asyncio.StreamWriter,
        status_code: int,
        body: bytes,
        content_type: str = "text/plain",
    ) -> None:
        """Write a minimal HTTP/1.0 response."""
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found",
                  405: "Method Not Allowed", 500: "Internal Server Error",
                  503: "Service Unavailable"}.get(status_code, "Unknown")
        header = (
            f"HTTP/1.0 {status_code} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        writer.write(header.encode() + body)
        await writer.drain()
