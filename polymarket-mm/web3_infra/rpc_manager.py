"""RPCManager — resilient RPC connection manager with fallback and health checks.

Manages multiple Polygon RPC endpoints with:
- Automatic failover when the primary endpoint fails
- Periodic health checks with latency tracking
- Exponential backoff on consecutive failures
- Per-endpoint latency metrics for smart routing
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

import structlog
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

logger = structlog.get_logger("web3_infra.rpc_manager")

T = TypeVar("T")


class EndpointStatus(str, Enum):
    """Health status of an RPC endpoint."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"


@dataclass
class EndpointMetrics:
    """Latency and reliability metrics for a single RPC endpoint."""

    url: str
    status: EndpointStatus = EndpointStatus.HEALTHY
    consecutive_failures: int = 0
    total_requests: int = 0
    total_failures: int = 0
    avg_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    last_check_time: float = 0.0
    last_error: str | None = None

    @property
    def failure_rate(self) -> float:
        """Return failure rate as a fraction [0.0, 1.0]."""
        if self.total_requests == 0:
            return 0.0
        return self.total_failures / self.total_requests

    def record_success(self, latency_ms: float) -> None:
        """Record a successful request."""
        self.total_requests += 1
        self.consecutive_failures = 0
        self.last_latency_ms = latency_ms
        self.last_check_time = time.monotonic()
        self.status = EndpointStatus.HEALTHY
        self.last_error = None
        # Exponential moving average for latency
        if self.avg_latency_ms == 0.0:
            self.avg_latency_ms = latency_ms
        else:
            alpha = 0.3
            self.avg_latency_ms = alpha * latency_ms + (1 - alpha) * self.avg_latency_ms

    def record_failure(self, error: str) -> None:
        """Record a failed request."""
        self.total_requests += 1
        self.total_failures += 1
        self.consecutive_failures += 1
        self.last_check_time = time.monotonic()
        self.last_error = error
        if self.consecutive_failures >= 5:
            self.status = EndpointStatus.DOWN
        elif self.consecutive_failures >= 2:
            self.status = EndpointStatus.DEGRADED


@dataclass
class RPCManagerConfig:
    """Configuration for the RPC manager."""

    # Health check interval in seconds
    health_check_interval_s: float = 30.0

    # Request timeout in seconds
    request_timeout_s: float = 10.0

    # Maximum consecutive failures before marking endpoint as DOWN
    max_consecutive_failures: int = 5

    # Backoff base in seconds for retries
    backoff_base_s: float = 1.0

    # Maximum backoff in seconds
    backoff_max_s: float = 30.0

    # Minimum latency improvement to prefer a non-primary endpoint (ms)
    latency_preference_threshold_ms: float = 100.0


class RPCManager:
    """Resilient RPC connection manager with multi-endpoint fallback.

    Maintains a pool of RPC endpoints (Polygon mainnet) and automatically
    selects the best available endpoint based on health and latency.

    Usage::

        manager = RPCManager(
            endpoints=["https://polygon-rpc.com", "https://rpc.ankr.com/polygon"],
        )
        await manager.start()

        # Get the best available Web3 instance
        w3 = manager.get_web3()

        # Execute with automatic failover
        block = await manager.execute(lambda w3: w3.eth.get_block("latest"))

        await manager.stop()
    """

    def __init__(
        self,
        endpoints: list[str],
        config: RPCManagerConfig | None = None,
    ) -> None:
        if not endpoints:
            raise ValueError("At least one RPC endpoint is required")

        self._config = config or RPCManagerConfig()
        self._endpoints = endpoints
        self._metrics: dict[str, EndpointMetrics] = {
            url: EndpointMetrics(url=url) for url in endpoints
        }
        self._web3_instances: dict[str, AsyncWeb3] = {}
        self._health_check_task: asyncio.Task[None] | None = None
        self._started = False

    @property
    def config(self) -> RPCManagerConfig:
        """Return current configuration (read-only)."""
        return self._config

    @property
    def metrics(self) -> dict[str, EndpointMetrics]:
        """Return per-endpoint metrics (read-only snapshot)."""
        return dict(self._metrics)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Initialize Web3 instances and start health check loop. Idempotent."""
        if self._started:
            return

        for url in self._endpoints:
            provider = AsyncHTTPProvider(
                url,
                request_kwargs={"timeout": self._config.request_timeout_s},
            )
            self._web3_instances[url] = AsyncWeb3(provider)

        self._started = True
        self._health_check_task = asyncio.create_task(
            self._health_check_loop(), name="rpc_health_check"
        )
        logger.info(
            "rpc_manager.started",
            num_endpoints=len(self._endpoints),
            endpoints=[self._redact_url(u) for u in self._endpoints],
        )

    async def stop(self) -> None:
        """Stop health checks and clean up. Idempotent."""
        if not self._started:
            return

        if self._health_check_task is not None:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        self._web3_instances.clear()
        self._started = False
        logger.info("rpc_manager.stopped")

    # ── Public API ───────────────────────────────────────────────

    def get_web3(self) -> AsyncWeb3:
        """Return the Web3 instance for the best available endpoint.

        Raises
        ------
        RuntimeError
            If the manager has not been started or all endpoints are down.
        """
        if not self._started:
            raise RuntimeError("RPCManager not started — call start() first")

        url = self._select_best_endpoint()
        return self._web3_instances[url]

    async def execute(self, fn: Callable[[AsyncWeb3], Any]) -> Any:
        """Execute a Web3 call with automatic failover across endpoints.

        Parameters
        ----------
        fn:
            An async callable that takes an ``AsyncWeb3`` instance and
            returns a result. Will be retried on different endpoints
            upon failure.

        Returns
        -------
        Any
            The result of the Web3 call.

        Raises
        ------
        RPCError
            If all endpoints fail.
        """
        if not self._started:
            raise RuntimeError("RPCManager not started — call start() first")

        ordered = self._endpoints_by_priority()
        last_error: Exception | None = None

        for url in ordered:
            metrics = self._metrics[url]
            if metrics.status == EndpointStatus.DOWN:
                continue

            w3 = self._web3_instances[url]
            start = time.monotonic()
            try:
                result = await fn(w3)
                latency_ms = (time.monotonic() - start) * 1000
                metrics.record_success(latency_ms)
                return result
            except Exception as exc:
                latency_ms = (time.monotonic() - start) * 1000
                metrics.record_failure(str(exc))
                last_error = exc
                logger.warning(
                    "rpc_manager.endpoint_failed",
                    url=self._redact_url(url),
                    error=str(exc),
                    consecutive_failures=metrics.consecutive_failures,
                    latency_ms=round(latency_ms, 1),
                )

        # All endpoints failed — try DOWN endpoints as last resort
        for url in ordered:
            metrics = self._metrics[url]
            if metrics.status != EndpointStatus.DOWN:
                continue

            w3 = self._web3_instances[url]
            start = time.monotonic()
            try:
                result = await fn(w3)
                latency_ms = (time.monotonic() - start) * 1000
                metrics.record_success(latency_ms)
                logger.info(
                    "rpc_manager.endpoint_recovered",
                    url=self._redact_url(url),
                )
                return result
            except Exception as exc:
                metrics.record_failure(str(exc))
                last_error = exc

        raise RPCError(
            f"All {len(self._endpoints)} RPC endpoints failed",
            last_error=last_error,
        )

    def get_endpoint_status(self) -> list[dict[str, Any]]:
        """Return a summary of all endpoint statuses for monitoring."""
        return [
            {
                "url": self._redact_url(m.url),
                "status": m.status.value,
                "avg_latency_ms": round(m.avg_latency_ms, 1),
                "failure_rate": round(m.failure_rate, 4),
                "consecutive_failures": m.consecutive_failures,
                "total_requests": m.total_requests,
                "last_error": m.last_error,
            }
            for m in self._metrics.values()
        ]

    # ── Health Check Loop ────────────────────────────────────────

    async def _health_check_loop(self) -> None:
        """Periodically check all endpoints health."""
        while True:
            try:
                await asyncio.sleep(self._config.health_check_interval_s)
                await self._check_all_endpoints()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("rpc_manager.health_check_error", error=str(exc))

    async def _check_all_endpoints(self) -> None:
        """Run health check on all endpoints concurrently."""
        tasks = [
            self._check_endpoint(url) for url in self._endpoints
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_endpoint(self, url: str) -> None:
        """Health-check a single endpoint by calling eth_blockNumber."""
        metrics = self._metrics[url]
        w3 = self._web3_instances.get(url)
        if w3 is None:
            return

        start = time.monotonic()
        try:
            await w3.eth.block_number
            latency_ms = (time.monotonic() - start) * 1000
            metrics.record_success(latency_ms)
            logger.debug(
                "rpc_manager.health_check_ok",
                url=self._redact_url(url),
                latency_ms=round(latency_ms, 1),
            )
        except Exception as exc:
            metrics.record_failure(str(exc))
            logger.warning(
                "rpc_manager.health_check_failed",
                url=self._redact_url(url),
                error=str(exc),
                consecutive_failures=metrics.consecutive_failures,
            )

    # ── Endpoint Selection ───────────────────────────────────────

    def _select_best_endpoint(self) -> str:
        """Select the best available endpoint based on health and latency.

        Priority:
        1. HEALTHY endpoints sorted by average latency
        2. DEGRADED endpoints sorted by average latency
        3. First endpoint as last resort

        Raises
        ------
        RuntimeError
            If no endpoints are configured.
        """
        healthy = [
            (url, m) for url, m in self._metrics.items()
            if m.status == EndpointStatus.HEALTHY
        ]
        if healthy:
            healthy.sort(key=lambda x: x[1].avg_latency_ms)
            return healthy[0][0]

        degraded = [
            (url, m) for url, m in self._metrics.items()
            if m.status == EndpointStatus.DEGRADED
        ]
        if degraded:
            degraded.sort(key=lambda x: x[1].avg_latency_ms)
            return degraded[0][0]

        # Fallback to first endpoint even if DOWN
        return self._endpoints[0]

    def _endpoints_by_priority(self) -> list[str]:
        """Return endpoints ordered by priority (healthy first, then degraded, then down)."""
        categorized: dict[EndpointStatus, list[tuple[str, EndpointMetrics]]] = {
            EndpointStatus.HEALTHY: [],
            EndpointStatus.DEGRADED: [],
            EndpointStatus.DOWN: [],
        }
        for url, m in self._metrics.items():
            categorized[m.status].append((url, m))

        result: list[str] = []
        for status in (EndpointStatus.HEALTHY, EndpointStatus.DEGRADED, EndpointStatus.DOWN):
            items = categorized[status]
            items.sort(key=lambda x: x[1].avg_latency_ms)
            result.extend(url for url, _ in items)

        return result

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _redact_url(url: str) -> str:
        """Redact API keys from URL for logging."""
        # Simple redaction: show host only
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            return f"{parsed.scheme}://{parsed.hostname}:***"
        except Exception:
            return url[:30] + "..."

    # ── Context manager ──────────────────────────────────────────

    async def __aenter__(self) -> RPCManager:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()


class RPCError(Exception):
    """Raised when all RPC endpoints fail."""

    def __init__(self, message: str, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.last_error = last_error
