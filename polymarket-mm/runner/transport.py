"""Transport probing and latency recording for Polymarket access."""

from __future__ import annotations

import json
import socket
import ssl
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger("runner.transport")

DEFAULT_PROXY_URL = "socks5://127.0.0.1:9050"
PROBE_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("clob_public", "https://clob.polymarket.com/"),
    ("gamma_markets", "https://gamma-api.polymarket.com/markets?limit=1&active=true&closed=false"),
)


@dataclass(slots=True)
class TransportProbeSample:
    endpoint: str
    url: str
    transport: str
    dns_ms: float
    connect_ms: float
    ttfb_ms: float
    ok: bool
    status_code: int | None = None
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TransportSelection:
    policy: str
    selected_transport: str
    reason: str
    direct_samples: list[TransportProbeSample]
    proxy_samples: list[TransportProbeSample]
    selected_proxy_url: str | None
    rewards_live_ok: bool
    directional_live_ok: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy,
            "selected_transport": self.selected_transport,
            "reason": self.reason,
            "selected_proxy_url": self.selected_proxy_url,
            "rewards_live_ok": self.rewards_live_ok,
            "directional_live_ok": self.directional_live_ok,
            "direct_samples": [sample.to_dict() for sample in self.direct_samples],
            "proxy_samples": [sample.to_dict() for sample in self.proxy_samples],
        }


def _socket_target(url: str, proxy_url: str | None, transport: str) -> tuple[str, int]:
    if transport == "proxy":
        if not proxy_url:
            raise ValueError("proxy transport requested without proxy_url")
        parsed = urlparse(proxy_url)
    else:
        parsed = urlparse(url)
    port = parsed.port or 443
    host = parsed.hostname or "localhost"
    return host, port


def _measure_dns(host: str, port: int) -> float:
    started = time.perf_counter()
    socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return (time.perf_counter() - started) * 1000


def _measure_connect(host: str, port: int) -> float:
    context = ssl.create_default_context()
    started = time.perf_counter()
    with socket.create_connection((host, port), timeout=10.0) as raw_sock:
        if port == 443:
            with context.wrap_socket(raw_sock, server_hostname=host):
                pass
    return (time.perf_counter() - started) * 1000


def probe_http_endpoint(
    endpoint: str,
    url: str,
    *,
    transport: str,
    proxy_url: str | None = None,
    timeout: float = 10.0,
) -> TransportProbeSample:
    host, port = _socket_target(url, proxy_url, transport)
    dns_ms = _measure_dns(host, port)
    connect_ms = _measure_connect(host, port)

    started = time.perf_counter()
    status_code: int | None = None
    error = ""
    ok = False
    try:
        with httpx.Client(
            timeout=timeout,
            proxy=(proxy_url if transport == "proxy" else None),
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            status_code = response.status_code
            ok = response.status_code < 500
    except Exception as exc:  # pragma: no cover - network variability
        error = str(exc)
        ok = False
    ttfb_ms = (time.perf_counter() - started) * 1000
    return TransportProbeSample(
        endpoint=endpoint,
        url=url,
        transport=transport,
        dns_ms=round(dns_ms, 2),
        connect_ms=round(connect_ms, 2),
        ttfb_ms=round(ttfb_ms, 2),
        ok=ok,
        status_code=status_code,
        error=error,
    )


def probe_transport_set(
    *,
    proxy_url: str | None = DEFAULT_PROXY_URL,
    endpoints: tuple[tuple[str, str], ...] = PROBE_ENDPOINTS,
) -> tuple[list[TransportProbeSample], list[TransportProbeSample]]:
    direct = [
        probe_http_endpoint(name, url, transport="direct")
        for name, url in endpoints
    ]
    proxy: list[TransportProbeSample] = []
    if proxy_url:
        proxy = [
            probe_http_endpoint(name, url, transport="proxy", proxy_url=proxy_url)
            for name, url in endpoints
        ]
    return direct, proxy


def _p90(samples: list[TransportProbeSample]) -> float:
    if not samples:
        return float("inf")
    values = [sample.ttfb_ms for sample in samples if sample.ok]
    if not values:
        return float("inf")
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=10, method="inclusive")[8]


def choose_transport(
    policy: str,
    direct_samples: list[TransportProbeSample],
    proxy_samples: list[TransportProbeSample],
    *,
    proxy_url: str | None = DEFAULT_PROXY_URL,
    rewards_direct_p90_ms: float = 350.0,
    rewards_proxy_p90_ms: float = 1300.0,
) -> TransportSelection:
    direct_ok = any(sample.ok for sample in direct_samples)
    proxy_ok = any(sample.ok for sample in proxy_samples)
    direct_p90 = _p90(direct_samples)
    proxy_p90 = _p90(proxy_samples)

    if policy == "direct_only":
        if not direct_ok:
            raise RuntimeError("transport policy direct_only failed: direct transport unavailable")
        selected_transport = "direct"
        reason = f"direct_only selected with p90_ttfb={direct_p90:.2f}ms"
    elif direct_ok:
        selected_transport = "direct"
        reason = f"direct selected with p90_ttfb={direct_p90:.2f}ms"
    elif proxy_ok and policy in {"direct_preferred", "proxy_fallback"}:
        selected_transport = "proxy"
        reason = f"proxy fallback selected with p90_ttfb={proxy_p90:.2f}ms"
    else:
        raise RuntimeError("no viable transport path to Polymarket endpoints")

    rewards_live_ok = (
        direct_p90 < rewards_direct_p90_ms if selected_transport == "direct"
        else proxy_p90 < rewards_proxy_p90_ms
    )
    directional_live_ok = selected_transport == "direct" and direct_p90 < rewards_direct_p90_ms

    return TransportSelection(
        policy=policy,
        selected_transport=selected_transport,
        reason=reason,
        direct_samples=direct_samples,
        proxy_samples=proxy_samples,
        selected_proxy_url=(proxy_url if selected_transport == "proxy" else None),
        rewards_live_ok=rewards_live_ok,
        directional_live_ok=directional_live_ok,
    )


def apply_py_clob_transport(selection: TransportSelection) -> None:
    import py_clob_client.http_helpers.helpers as clob_helpers

    client = httpx.Client(
        http2=True,
        proxy=selection.selected_proxy_url,
        timeout=30.0,
        follow_redirects=True,
    )
    clob_helpers._http_client = client
    logger.info(
        "transport.applied",
        selected_transport=selection.selected_transport,
        rewards_live_ok=selection.rewards_live_ok,
        directional_live_ok=selection.directional_live_ok,
    )


class TransportLatencyRecorder:
    """Collect and persist per-hour latency summaries."""

    def __init__(
        self,
        *,
        selection: TransportSelection,
        jsonl_path: str | Path | None = None,
        summary_path: str | Path | None = None,
        proxy_url: str | None = DEFAULT_PROXY_URL,
    ) -> None:
        root = Path(jsonl_path).parent if jsonl_path else None
        default_root = root or (Path(__file__).resolve().parent.parent / "paper" / "data")
        default_root.mkdir(parents=True, exist_ok=True)
        self.selection = selection
        self.proxy_url = proxy_url
        self.jsonl_path = Path(jsonl_path) if jsonl_path else default_root / "transport_latency.jsonl"
        self.summary_path = Path(summary_path) if summary_path else default_root / "transport_latency_latest.json"
        self._samples: list[dict[str, Any]] = []

    def record_probe(self, sample: TransportProbeSample) -> None:
        payload = sample.to_dict()
        self._samples.append(payload)
        with open(self.jsonl_path, "a") as handle:
            handle.write(json.dumps(payload) + "\n")

    def record_order_ack(
        self,
        ack_ms: float,
        *,
        market_id: str,
        decision_id: str,
        status: str,
        error_code: str = "",
    ) -> None:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "order_ack",
            "transport": self.selection.selected_transport,
            "endpoint": "order_ack",
            "ttfb_ms": round(float(ack_ms), 2),
            "market_id": market_id,
            "decision_id": decision_id,
            "status": status,
            "error_code": error_code,
        }
        self._samples.append(payload)
        with open(self.jsonl_path, "a") as handle:
            handle.write(json.dumps(payload) + "\n")

    def record_rejection(
        self,
        *,
        market_id: str,
        decision_id: str,
        rejection_reason: str,
        latency_bucket: str,
    ) -> None:
        payload = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "kind": "rejection",
            "transport": self.selection.selected_transport,
            "endpoint": "execution",
            "market_id": market_id,
            "decision_id": decision_id,
            "rejection_reason": rejection_reason,
            "latency_bucket": latency_bucket,
        }
        self._samples.append(payload)
        with open(self.jsonl_path, "a") as handle:
            handle.write(json.dumps(payload) + "\n")

    def sample_current_transport(self) -> list[TransportProbeSample]:
        samples = [
            probe_http_endpoint(
                name,
                url,
                transport=self.selection.selected_transport,
                proxy_url=self.proxy_url if self.selection.selected_transport == "proxy" else None,
            )
            for name, url in PROBE_ENDPOINTS
        ]
        for sample in samples:
            self.record_probe(sample)
        return samples

    def write_summary(self) -> dict[str, Any]:
        grouped: dict[tuple[str, str], list[float]] = {}
        for sample in self._samples:
            endpoint = sample.get("endpoint", "unknown")
            transport = sample.get("transport", self.selection.selected_transport)
            key = (endpoint, transport)
            grouped.setdefault(key, []).append(float(sample.get("ttfb_ms", 0.0)))

        summary: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "selected_transport": self.selection.selected_transport,
            "policy": self.selection.policy,
            "metrics": {},
        }
        for (endpoint, transport), values in grouped.items():
            values = [v for v in values if v >= 0]
            if not values:
                continue
            metric_key = f"{transport}:{endpoint}"
            summary["metrics"][metric_key] = {
                "count": len(values),
                "p50_ms": round(statistics.median(values), 2),
                "p90_ms": round(_percentile(values, 0.90), 2),
                "p99_ms": round(_percentile(values, 0.99), 2),
            }

        with open(self.summary_path, "w") as handle:
            json.dump(summary, handle, indent=2)
        return summary


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
