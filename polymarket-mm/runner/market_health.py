"""runner.market_health — Market health monitoring for rotation decisions.

Tracks per-market health metrics using rolling windows:
- Spread quality (what fraction of observed spreads are within threshold)
- Fill rate (fills / orders over a configurable window)
- Inventory skew (directional exposure as percentage of total position)

Produces a composite health_score = avg(spread_score, fill_score, skew_score).
Market is UNHEALTHY when score < threshold OR any single metric trips.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

import structlog

from models.position import Position
from runner.config import RotationConfig

logger = structlog.get_logger("runner.market_health")


class MarketHealthStatus(str, Enum):
    """Market health classification."""

    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"


@dataclass
class MarketHealthSnapshot:
    """Point-in-time health assessment for a single market."""

    market_id: str
    status: MarketHealthStatus
    health_score: float
    spread_score: float
    fill_score: float
    skew_score: float
    spread_bps: float
    fill_rate_pct: float
    inventory_skew_pct: float
    timestamp: float = field(default_factory=time.monotonic)

    @property
    def is_unhealthy(self) -> bool:
        return self.status == MarketHealthStatus.UNHEALTHY


@dataclass
class _SpreadSample:
    """Single spread observation."""

    spread_bps: float
    timestamp: float


@dataclass
class _FillOrderSample:
    """Fill or order event."""

    is_fill: bool  # True = fill, False = order
    timestamp: float


class MarketHealthMonitor:
    """Rolling-window health monitor for a set of markets.

    Designed to be fed events from the pipeline's quote and fill loops.
    Evaluates health on demand via ``evaluate()``.
    """

    def __init__(self, config: RotationConfig) -> None:
        self._config = config
        self._window_seconds = config.fill_rate_window_hours * 3600

        # Per-market rolling windows
        self._spread_samples: dict[str, deque[_SpreadSample]] = {}
        self._fill_order_samples: dict[str, deque[_FillOrderSample]] = {}

    def record_spread(self, market_id: str, spread_bps: float) -> None:
        """Record an observed bid-ask spread for a market."""
        now = time.monotonic()
        if market_id not in self._spread_samples:
            self._spread_samples[market_id] = deque(maxlen=10_000)
        self._spread_samples[market_id].append(_SpreadSample(
            spread_bps=spread_bps,
            timestamp=now,
        ))

    def record_order(self, market_id: str) -> None:
        """Record an order submitted for a market."""
        now = time.monotonic()
        if market_id not in self._fill_order_samples:
            self._fill_order_samples[market_id] = deque(maxlen=50_000)
        self._fill_order_samples[market_id].append(_FillOrderSample(
            is_fill=False,
            timestamp=now,
        ))

    def record_fill(self, market_id: str) -> None:
        """Record a fill received for a market."""
        now = time.monotonic()
        if market_id not in self._fill_order_samples:
            self._fill_order_samples[market_id] = deque(maxlen=50_000)
        self._fill_order_samples[market_id].append(_FillOrderSample(
            is_fill=True,
            timestamp=now,
        ))

    def evaluate(
        self,
        market_id: str,
        position: Position | None = None,
    ) -> MarketHealthSnapshot:
        """Evaluate health for a single market.

        Returns a snapshot with scores and status.
        """
        now = time.monotonic()
        cutoff = now - self._window_seconds

        # ── Spread score ──
        spread_bps = self._avg_spread(market_id, cutoff)
        if spread_bps <= 0:
            # No data → assume neutral
            spread_score = 0.5
        elif spread_bps <= self._config.max_spread_bps:
            # Linear scale: 0 bps → 1.0, max_spread_bps → 0.0
            spread_score = max(0.0, 1.0 - spread_bps / self._config.max_spread_bps)
        else:
            spread_score = 0.0

        # ── Fill rate score ──
        fill_rate_pct = self._fill_rate(market_id, cutoff)
        if fill_rate_pct < 0:
            # No orders → neutral
            fill_score = 0.5
        elif fill_rate_pct >= self._config.min_fill_rate_pct:
            # Cap at 1.0
            fill_score = min(1.0, fill_rate_pct / max(self._config.min_fill_rate_pct * 5, 1.0))
        else:
            fill_score = fill_rate_pct / max(self._config.min_fill_rate_pct, 0.01)

        # ── Inventory skew score ──
        skew_pct = self._inventory_skew(position)
        if skew_pct <= self._config.max_inventory_skew_pct:
            skew_score = max(0.0, 1.0 - skew_pct / 100.0)
        else:
            skew_score = 0.0

        # ── Composite score ──
        health_score = (spread_score + fill_score + skew_score) / 3.0

        # ── Status classification ──
        # UNHEALTHY if composite below threshold OR any single metric trips hard
        is_unhealthy = False
        if health_score < self._config.min_market_health_score:
            is_unhealthy = True
        if spread_bps > self._config.max_spread_bps and spread_bps > 0:
            is_unhealthy = True
        if 0 <= fill_rate_pct < self._config.min_fill_rate_pct:
            is_unhealthy = True
        if skew_pct > self._config.max_inventory_skew_pct:
            is_unhealthy = True

        if is_unhealthy:
            status = MarketHealthStatus.UNHEALTHY
        elif health_score < self._config.min_market_health_score * 1.5:
            status = MarketHealthStatus.DEGRADED
        else:
            status = MarketHealthStatus.HEALTHY

        snapshot = MarketHealthSnapshot(
            market_id=market_id,
            status=status,
            health_score=health_score,
            spread_score=spread_score,
            fill_score=fill_score,
            skew_score=skew_score,
            spread_bps=spread_bps,
            fill_rate_pct=fill_rate_pct,
            inventory_skew_pct=skew_pct,
            timestamp=now,
        )

        if is_unhealthy:
            logger.warning(
                "market_health.unhealthy",
                market_id=market_id,
                health_score=round(health_score, 3),
                spread_bps=round(spread_bps, 1),
                fill_rate_pct=round(fill_rate_pct, 2),
                skew_pct=round(skew_pct, 1),
            )

        return snapshot

    def _avg_spread(self, market_id: str, cutoff: float) -> float:
        """Average spread in bps over the window. Returns -1 if no data."""
        samples = self._spread_samples.get(market_id)
        if not samples:
            return -1.0

        # Prune old samples
        while samples and samples[0].timestamp < cutoff:
            samples.popleft()

        if not samples:
            return -1.0

        total = sum(s.spread_bps for s in samples)
        return total / len(samples)

    def _fill_rate(self, market_id: str, cutoff: float) -> float:
        """Fill rate as percentage over the window. Returns -1 if no orders."""
        samples = self._fill_order_samples.get(market_id)
        if not samples:
            return -1.0

        # Prune old samples
        while samples and samples[0].timestamp < cutoff:
            samples.popleft()

        if not samples:
            return -1.0

        fills = sum(1 for s in samples if s.is_fill)
        orders = sum(1 for s in samples if not s.is_fill)

        if orders == 0:
            return -1.0  # No orders → can't compute fill rate

        return (fills / orders) * 100.0

    def _inventory_skew(self, position: Position | None) -> float:
        """Inventory skew as percentage (0 = balanced, 100 = fully one-sided)."""
        if position is None:
            return 0.0

        total = position.qty_yes + position.qty_no
        if total == 0:
            return 0.0

        net = abs(position.qty_yes - position.qty_no)
        return float(net / total) * 100.0

    def prune_market(self, market_id: str) -> None:
        """Remove all tracking data for a market (after rotation)."""
        self._spread_samples.pop(market_id, None)
        self._fill_order_samples.pop(market_id, None)
