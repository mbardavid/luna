"""Prometheus metrics registry for the Polymarket Market Maker.

Exposes fills/s, cumulative PnL, p99 latency, inventory exposure,
spread width, and order-level counters — all labelled by market_id
and side where relevant.

Uses a dedicated ``CollectorRegistry`` so tests can instantiate
isolated registries without polluting the global default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

if TYPE_CHECKING:
    from decimal import Decimal

__all__ = ["MetricsRegistry"]


class MetricsRegistry:
    """Central Prometheus metrics registry.

    Parameters
    ----------
    registry:
        A ``CollectorRegistry`` to register metrics in.  When *None*,
        a fresh registry is created (useful for tests).  Pass
        ``prometheus_client.REGISTRY`` for the global default when you
        want ``/metrics`` to work automatically.

    Usage::

        metrics = MetricsRegistry()
        metrics.record_fill("0xabc", "BUY", 10.0, 0.012)
        metrics.set_pnl(42.5)
        print(metrics.exposition())
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self._registry = registry or CollectorRegistry()

        # ── App info ────────────────────────────────────────────
        self.app_info = Info(
            "pmm",
            "Polymarket Market Maker build info",
            registry=self._registry,
        )

        # ── Fills ───────────────────────────────────────────────
        self.fills_total = Counter(
            "pmm_fills_total",
            "Total number of fills",
            labelnames=["market_id", "side"],
            registry=self._registry,
        )

        self.fill_value_usd = Counter(
            "pmm_fill_value_usd_total",
            "Cumulative fill notional (USD)",
            labelnames=["market_id", "side"],
            registry=self._registry,
        )

        # ── PnL ─────────────────────────────────────────────────
        self.pnl_cumulative = Gauge(
            "pmm_pnl_cumulative_usd",
            "Cumulative realised PnL (USD)",
            registry=self._registry,
        )

        self.pnl_daily = Gauge(
            "pmm_pnl_daily_usd",
            "Daily realised PnL (USD), reset at midnight UTC",
            registry=self._registry,
        )

        # ── Latency ─────────────────────────────────────────────
        self.order_latency = Histogram(
            "pmm_order_latency_seconds",
            "Round-trip order submission latency",
            labelnames=["market_id"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self._registry,
        )

        self.quote_cycle_latency = Histogram(
            "pmm_quote_cycle_seconds",
            "Full quote cycle (feature calc + quoting + submission)",
            buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5),
            registry=self._registry,
        )

        # ── Inventory ──────────────────────────────────────────
        self.inventory_exposure = Gauge(
            "pmm_inventory_exposure_usd",
            "Net inventory exposure in USD",
            labelnames=["market_id"],
            registry=self._registry,
        )

        self.total_exposure = Gauge(
            "pmm_total_exposure_usd",
            "Total absolute exposure across all markets (USD)",
            registry=self._registry,
        )

        # ── Spread ──────────────────────────────────────────────
        self.quoted_spread_bps = Gauge(
            "pmm_quoted_spread_bps",
            "Current quoted half-spread in basis points",
            labelnames=["market_id"],
            registry=self._registry,
        )

        # ── Orders ──────────────────────────────────────────────
        self.orders_submitted = Counter(
            "pmm_orders_submitted_total",
            "Orders submitted to the venue",
            labelnames=["market_id", "side"],
            registry=self._registry,
        )

        self.orders_cancelled = Counter(
            "pmm_orders_cancelled_total",
            "Orders cancelled",
            labelnames=["market_id"],
            registry=self._registry,
        )

        self.orders_rejected = Counter(
            "pmm_orders_rejected_total",
            "Orders rejected by venue",
            labelnames=["market_id"],
            registry=self._registry,
        )

        # ── Kill switch ─────────────────────────────────────────
        self.kill_switch_trips = Counter(
            "pmm_kill_switch_trips_total",
            "Number of kill switch activations",
            labelnames=["trigger"],
            registry=self._registry,
        )

        self.kill_switch_state = Gauge(
            "pmm_kill_switch_state",
            "Kill switch state: 0=RUNNING, 1=PAUSED, 2=HALTED",
            registry=self._registry,
        )

        # ── WebSocket ──────────────────────────────────────────
        self.ws_messages_received = Counter(
            "pmm_ws_messages_total",
            "WebSocket messages received",
            registry=self._registry,
        )

        self.ws_reconnects = Counter(
            "pmm_ws_reconnects_total",
            "WebSocket reconnection count",
            registry=self._registry,
        )

    # ── Convenience recording methods ───────────────────────────

    @property
    def registry(self) -> CollectorRegistry:
        """Return the underlying ``CollectorRegistry``."""
        return self._registry

    def record_fill(
        self,
        market_id: str,
        side: str,
        notional_usd: float,
        latency_seconds: float | None = None,
    ) -> None:
        """Record a single fill event.

        Parameters
        ----------
        market_id:
            Condition/token id of the market.
        side:
            ``"BUY"`` or ``"SELL"``.
        notional_usd:
            Dollar value of the fill.
        latency_seconds:
            Round-trip latency for the order (optional).
        """
        self.fills_total.labels(market_id=market_id, side=side).inc()
        self.fill_value_usd.labels(market_id=market_id, side=side).inc(notional_usd)
        if latency_seconds is not None:
            self.order_latency.labels(market_id=market_id).observe(latency_seconds)

    def set_pnl(self, cumulative: float, daily: float | None = None) -> None:
        """Update PnL gauges."""
        self.pnl_cumulative.set(cumulative)
        if daily is not None:
            self.pnl_daily.set(daily)

    def set_inventory(self, market_id: str, exposure_usd: float) -> None:
        """Update inventory exposure for a single market."""
        self.inventory_exposure.labels(market_id=market_id).set(exposure_usd)

    def set_total_exposure(self, total_usd: float) -> None:
        """Update total portfolio exposure."""
        self.total_exposure.set(total_usd)

    def set_spread(self, market_id: str, half_spread_bps: float) -> None:
        """Update quoted spread for a market."""
        self.quoted_spread_bps.labels(market_id=market_id).set(half_spread_bps)

    def record_order_submit(self, market_id: str, side: str) -> None:
        """Record an order submission."""
        self.orders_submitted.labels(market_id=market_id, side=side).inc()

    def record_order_cancel(self, market_id: str) -> None:
        """Record an order cancellation."""
        self.orders_cancelled.labels(market_id=market_id).inc()

    def record_order_reject(self, market_id: str) -> None:
        """Record a venue rejection."""
        self.orders_rejected.labels(market_id=market_id).inc()

    def record_kill_switch(self, trigger: str) -> None:
        """Record a kill switch trip."""
        self.kill_switch_trips.labels(trigger=trigger).inc()

    def set_kill_switch_state(self, state: int) -> None:
        """Set kill switch state gauge (0=RUNNING, 1=PAUSED, 2=HALTED)."""
        self.kill_switch_state.set(state)

    def exposition(self) -> bytes:
        """Return Prometheus text exposition format."""
        return generate_latest(self._registry)
