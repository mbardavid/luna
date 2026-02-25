"""AnomalyDetector — rolling z-score anomaly detection with event_bus alerts.

Monitors key trading metrics in real-time using a rolling window z-score
approach.  When a metric's z-score exceeds the configured threshold,
an alert is emitted via the ``EventBus``.

Monitored metrics:
- **PnL drawdown** — sudden drops in cumulative PnL.
- **Fill rate deviation** — abnormal fill frequency (too high or too low).
- **Spread compression** — spreads tightening to unprofitable levels.
- **Inventory imbalance** — net inventory drifting excessively.

Usage::

    bus = EventBus()
    detector = AnomalyDetector(event_bus=bus)

    # Feed observations as they arrive:
    await detector.observe("pnl_drawdown", Decimal("-150.00"))
    await detector.observe("fill_rate", Decimal("2.3"))
    await detector.observe("spread_bps", Decimal("8"))
    await detector.observe("inventory_imbalance", Decimal("450"))

    # Anomalies are automatically published to the event bus
    # under the "anomaly.alert" topic.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from math import sqrt
from typing import Any

import structlog

from core.event_bus import EventBus

logger = structlog.get_logger("ai_copilot.anomaly_detector")

_ZERO = Decimal("0")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class MetricConfig:
    """Configuration for a single monitored metric."""

    name: str
    zscore_threshold: float = 2.5
    window_size: int = 100
    description: str = ""
    alert_cooldown_seconds: float = 300.0  # 5 minute cooldown between alerts


@dataclass
class AnomalyDetectorConfig:
    """Configuration for the anomaly detector.

    Attributes
    ----------
    metrics:
        List of metric configurations.  Defaults cover the four core metrics.
    default_zscore_threshold:
        Default z-score threshold for anomaly flagging.
    default_window_size:
        Default rolling window size (number of observations).
    """

    metrics: list[MetricConfig] = field(default_factory=lambda: [
        MetricConfig(
            name="pnl_drawdown",
            zscore_threshold=2.0,
            window_size=50,
            description="PnL drawdown from peak (negative values indicate loss)",
        ),
        MetricConfig(
            name="fill_rate",
            zscore_threshold=2.5,
            window_size=100,
            description="Fill rate (fills per interval)",
        ),
        MetricConfig(
            name="spread_bps",
            zscore_threshold=2.5,
            window_size=100,
            description="Observed spread in basis points",
        ),
        MetricConfig(
            name="inventory_imbalance",
            zscore_threshold=2.0,
            window_size=50,
            description="Net inventory imbalance |YES - NO|",
        ),
    ])
    default_zscore_threshold: float = 2.5
    default_window_size: int = 100

    def get_metric(self, name: str) -> MetricConfig | None:
        """Find a metric config by name."""
        for m in self.metrics:
            if m.name == name:
                return m
        return None


# ── Anomaly Alert ────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AnomalyAlert:
    """Structured anomaly alert emitted via event bus."""

    metric_name: str
    current_value: float
    zscore: float
    threshold: float
    window_mean: float
    window_std: float
    severity: str  # "warning" or "critical"
    description: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict."""
        return {
            "metric_name": self.metric_name,
            "current_value": self.current_value,
            "zscore": round(self.zscore, 4),
            "threshold": self.threshold,
            "window_mean": round(self.window_mean, 4),
            "window_std": round(self.window_std, 4),
            "severity": self.severity,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
        }


# ── Rolling window ───────────────────────────────────────────────────


class _RollingWindow:
    """Efficient rolling statistics using Welford's online algorithm."""

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max(max_size, 2)  # need at least 2 for std
        self._values: deque[float] = deque(maxlen=self._max_size)
        self._sum: float = 0.0
        self._sum_sq: float = 0.0

    def push(self, value: float) -> None:
        """Add a value to the window."""
        if len(self._values) == self._max_size:
            # Remove oldest value from running sums
            old = self._values[0]
            self._sum -= old
            self._sum_sq -= old * old

        self._values.append(value)
        self._sum += value
        self._sum_sq += value * value

    @property
    def count(self) -> int:
        """Number of values in the window."""
        return len(self._values)

    @property
    def mean(self) -> float:
        """Rolling mean."""
        if self.count == 0:
            return 0.0
        return self._sum / self.count

    @property
    def std(self) -> float:
        """Rolling standard deviation (population)."""
        if self.count < 2:
            return 0.0
        variance = (self._sum_sq / self.count) - (self.mean ** 2)
        # Guard against negative variance from floating-point errors
        return sqrt(max(variance, 0.0))

    def zscore(self, value: float) -> float:
        """Compute z-score of a value against the window distribution."""
        if self.count < 2 or self.std == 0.0:
            return 0.0
        return (value - self.mean) / self.std


# ── AnomalyDetector ─────────────────────────────────────────────────


class AnomalyDetector:
    """Rolling z-score anomaly detector with event_bus alerts.

    Parameters
    ----------
    event_bus:
        EventBus instance for publishing anomaly alerts.
        If ``None``, alerts are logged but not published.
    config:
        Detector configuration with metric definitions.
    """

    ALERT_TOPIC = "anomaly.alert"

    def __init__(
        self,
        event_bus: EventBus | None = None,
        config: AnomalyDetectorConfig | None = None,
    ) -> None:
        self._bus = event_bus
        self._config = config or AnomalyDetectorConfig()

        # Initialise rolling windows for each configured metric
        self._windows: dict[str, _RollingWindow] = {}
        self._metric_configs: dict[str, MetricConfig] = {}
        self._last_alert_time: dict[str, datetime] = {}

        for mc in self._config.metrics:
            self._windows[mc.name] = _RollingWindow(max_size=mc.window_size)
            self._metric_configs[mc.name] = mc

    @property
    def config(self) -> AnomalyDetectorConfig:
        """Return current configuration."""
        return self._config

    async def observe(
        self,
        metric_name: str,
        value: Decimal | float,
        market_id: str = "",
    ) -> AnomalyAlert | None:
        """Record a metric observation and check for anomalies.

        Parameters
        ----------
        metric_name:
            Name of the metric (must match a configured metric).
        value:
            Current observation value.
        market_id:
            Optional market ID for contextual logging.

        Returns
        -------
        AnomalyAlert or None
            If an anomaly is detected and the cooldown has elapsed,
            returns the alert (also published to event bus).
            Otherwise returns ``None``.
        """
        float_value = float(value)

        # Get or create window
        mc = self._metric_configs.get(metric_name)
        if mc is None:
            # Dynamic metric: create with defaults
            mc = MetricConfig(
                name=metric_name,
                zscore_threshold=self._config.default_zscore_threshold,
                window_size=self._config.default_window_size,
            )
            self._metric_configs[metric_name] = mc
            self._windows[metric_name] = _RollingWindow(
                max_size=mc.window_size
            )

        window = self._windows[metric_name]

        # Check for anomaly before pushing (so the new value doesn't
        # dilute the window statistics used for comparison)
        alert: AnomalyAlert | None = None
        if window.count >= 5:  # Need minimum data for meaningful z-score
            z = window.zscore(float_value)
            if abs(z) >= mc.zscore_threshold:
                alert = self._maybe_create_alert(
                    mc, float_value, z, window, market_id
                )

        # Push value into window (after anomaly check)
        window.push(float_value)

        return alert

    def get_window_stats(self, metric_name: str) -> dict[str, float]:
        """Return current window statistics for a metric.

        Returns
        -------
        dict
            Keys: ``count``, ``mean``, ``std``.
        """
        window = self._windows.get(metric_name)
        if window is None:
            return {"count": 0, "mean": 0.0, "std": 0.0}
        return {
            "count": window.count,
            "mean": round(window.mean, 6),
            "std": round(window.std, 6),
        }

    def reset(self, metric_name: str | None = None) -> None:
        """Reset rolling windows.

        Parameters
        ----------
        metric_name:
            If provided, reset only that metric. Otherwise reset all.
        """
        if metric_name:
            mc = self._metric_configs.get(metric_name)
            if mc:
                self._windows[metric_name] = _RollingWindow(
                    max_size=mc.window_size
                )
                self._last_alert_time.pop(metric_name, None)
        else:
            for name, mc in self._metric_configs.items():
                self._windows[name] = _RollingWindow(max_size=mc.window_size)
            self._last_alert_time.clear()

    # ── Internal ─────────────────────────────────────────────────

    def _maybe_create_alert(
        self,
        mc: MetricConfig,
        value: float,
        zscore: float,
        window: _RollingWindow,
        market_id: str,
    ) -> AnomalyAlert | None:
        """Create an alert if cooldown has elapsed, and publish it."""
        now = datetime.now(timezone.utc)

        # Check cooldown
        last = self._last_alert_time.get(mc.name)
        if last is not None:
            elapsed = (now - last).total_seconds()
            if elapsed < mc.alert_cooldown_seconds:
                return None

        # Determine severity
        severity = "critical" if abs(zscore) >= mc.zscore_threshold * 1.5 else "warning"

        description = (
            f"{mc.description or mc.name}: value={value:.4f}, "
            f"z-score={zscore:+.2f} (threshold=±{mc.zscore_threshold}), "
            f"window μ={window.mean:.4f}, σ={window.std:.4f}"
        )
        if market_id:
            description = f"[{market_id}] {description}"

        alert = AnomalyAlert(
            metric_name=mc.name,
            current_value=value,
            zscore=zscore,
            threshold=mc.zscore_threshold,
            window_mean=window.mean,
            window_std=window.std,
            severity=severity,
            description=description,
            timestamp=now,
        )

        self._last_alert_time[mc.name] = now

        logger.warning(
            "anomaly_detector.alert",
            metric=mc.name,
            value=value,
            zscore=round(zscore, 4),
            severity=severity,
            market_id=market_id or "global",
        )

        # Publish to event bus (fire-and-forget for sync callers)
        if self._bus is not None:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._bus.publish(
                        self.ALERT_TOPIC,
                        alert.to_dict(),
                    )
                )
            except RuntimeError:
                # No running loop — just log
                logger.debug("anomaly_detector.no_event_loop")

        return alert
