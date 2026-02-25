"""FeatureEngine — computes FeatureVector from market data snapshots.

Takes a MarketState + orderbook snapshot + oracle data and produces a
fully-populated FeatureVector.  Maintains rolling windows internally
for momentum, volatility, and toxic-flow z-score calculations.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

from models.feature_vector import FeatureVector

logger = structlog.get_logger("strategy.feature_engine")

# ── Configuration ────────────────────────────────────────────────────


@dataclass
class FeatureEngineConfig:
    """Tunable parameters for the feature engine."""

    # Rolling window sizes (number of ticks)
    momentum_window: int = 20
    volatility_window: int = 60  # ~1 min at 1-tick-per-second
    imbalance_window: int = 30  # for z-score of imbalance
    liquidity_window: int = 10

    # Polymarket standard fee
    default_fee_bps: Decimal = Decimal("2")

    # Depth normalisation reference (max expected depth in shares)
    max_expected_depth: Decimal = Decimal("5000")

    # Minimum data points before features are considered valid
    min_data_points: int = 3


# ── Feature Engine ───────────────────────────────────────────────────


class FeatureEngine:
    """Stateful feature computation pipeline.

    Call ``compute()`` on each tick to get an updated ``FeatureVector``.
    The engine keeps internal rolling windows keyed by *market_id*.
    """

    def __init__(self, config: FeatureEngineConfig | None = None) -> None:
        self._config = config or FeatureEngineConfig()

        # Per-market rolling windows
        self._mid_prices: dict[str, deque[float]] = {}
        self._imbalances: dict[str, deque[float]] = {}
        self._depths: dict[str, deque[float]] = {}

    # ── Public API ───────────────────────────────────────────────

    async def compute(
        self,
        market_state: "MarketState",
        orderbook: dict[str, Any],
        oracle_price: float | None = None,
    ) -> FeatureVector:
        """Compute a full FeatureVector from current market snapshot.

        Parameters
        ----------
        market_state:
            Current MarketState snapshot (with bid/ask/depth).
        orderbook:
            Raw orderbook dict with ``bids`` and ``asks`` lists
            (each item has ``price`` and ``size`` keys).
        oracle_price:
            External oracle price for the YES token (0–1 range).
            ``None`` means no oracle is available.
        """
        mid = market_state.mid_price
        mid_f = float(mid) if mid > 0 else 0.0

        mkt = market_state.market_id

        # Ensure rolling deques exist for this market
        self._ensure_windows(mkt)

        # ── 1. Spread (bps) ──────────────────────────────────────
        spread_bps = self._compute_spread_bps(market_state)

        # ── 2. Book imbalance [-1, 1] ────────────────────────────
        book_imbalance = self._compute_book_imbalance(orderbook)
        self._imbalances[mkt].append(book_imbalance)

        # ── 3. Mid-price rolling window ──────────────────────────
        if mid_f > 0:
            self._mid_prices[mkt].append(mid_f)

        # ── 4. Micro-momentum ────────────────────────────────────
        micro_momentum = self._compute_micro_momentum(mkt)

        # ── 5. Volatility (1 min) ────────────────────────────────
        volatility_1m = self._compute_volatility(mkt)

        # ── 6. Liquidity score [0, 1] ────────────────────────────
        liquidity_score = self._compute_liquidity_score(orderbook, mkt)

        # ── 7. Toxic flow z-score ────────────────────────────────
        toxic_flow_score = self._compute_toxic_flow_zscore(mkt)

        # ── 8. Oracle delta ──────────────────────────────────────
        oracle_delta = 0.0
        if oracle_price is not None and mid_f > 0:
            oracle_delta = mid_f - oracle_price

        # ── 9. Expected fee ──────────────────────────────────────
        expected_fee_bps = self._config.default_fee_bps

        # ── 10. Queue position estimate (stub) ───────────────────
        queue_position_estimate = self._estimate_queue_position(orderbook)

        # ── 11. Data quality score ───────────────────────────────
        data_quality_score = self._compute_data_quality(market_state, orderbook, mkt)

        fv = FeatureVector(
            market_id=mkt,
            timestamp=datetime.now(timezone.utc),
            spread_bps=spread_bps,
            book_imbalance=book_imbalance,
            micro_momentum=micro_momentum,
            volatility_1m=volatility_1m,
            liquidity_score=liquidity_score,
            toxic_flow_score=toxic_flow_score,
            oracle_delta=oracle_delta,
            expected_fee_bps=expected_fee_bps,
            queue_position_estimate=queue_position_estimate,
            data_quality_score=data_quality_score,
        )

        logger.debug(
            "feature_engine.computed",
            market_id=mkt,
            spread_bps=str(spread_bps),
            imbalance=round(book_imbalance, 4),
            momentum=round(micro_momentum, 6),
            vol=round(volatility_1m, 6),
        )

        return fv

    def reset(self, market_id: str | None = None) -> None:
        """Clear rolling windows for a market (or all markets)."""
        if market_id:
            self._mid_prices.pop(market_id, None)
            self._imbalances.pop(market_id, None)
            self._depths.pop(market_id, None)
        else:
            self._mid_prices.clear()
            self._imbalances.clear()
            self._depths.clear()

    # ── Internal computations ────────────────────────────────────

    def _ensure_windows(self, mkt: str) -> None:
        if mkt not in self._mid_prices:
            self._mid_prices[mkt] = deque(maxlen=self._config.volatility_window)
        if mkt not in self._imbalances:
            self._imbalances[mkt] = deque(maxlen=self._config.imbalance_window)
        if mkt not in self._depths:
            self._depths[mkt] = deque(maxlen=self._config.liquidity_window)

    @staticmethod
    def _compute_spread_bps(ms: "MarketState") -> Decimal:
        """Spread in basis points relative to mid price."""
        if ms.yes_bid <= 0 or ms.yes_ask <= 0:
            return Decimal("0")
        mid = ms.mid_price
        if mid <= 0:
            return Decimal("0")
        spread = ms.yes_ask - ms.yes_bid
        bps = (spread / mid) * Decimal("10000")
        return bps.quantize(Decimal("0.01"))

    @staticmethod
    def _compute_book_imbalance(orderbook: dict[str, Any]) -> float:
        """Compute bid/ask size imbalance normalised to [-1, 1].

        Positive means more bid-side weight (bullish).
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        bid_size = sum(float(lvl.get("size", 0)) for lvl in bids)
        ask_size = sum(float(lvl.get("size", 0)) for lvl in asks)

        total = bid_size + ask_size
        if total == 0:
            return 0.0

        imbalance = (bid_size - ask_size) / total
        return max(-1.0, min(1.0, imbalance))

    def _compute_micro_momentum(self, mkt: str) -> float:
        """Rolling average of price changes (momentum indicator)."""
        prices = self._mid_prices[mkt]
        window = min(self._config.momentum_window, len(prices))
        if window < 2:
            return 0.0

        recent = list(prices)[-window:]
        changes = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
        return sum(changes) / len(changes)

    def _compute_volatility(self, mkt: str) -> float:
        """Standard deviation of mid-price changes over the volatility window."""
        prices = self._mid_prices[mkt]
        if len(prices) < 2:
            return 0.0

        changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
        if len(changes) < 2:
            return abs(changes[0]) if changes else 0.0

        return statistics.stdev(changes)

    def _compute_liquidity_score(self, orderbook: dict[str, Any], mkt: str) -> float:
        """Normalised liquidity score [0, 1] based on total depth."""
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        total_depth = sum(float(lvl.get("size", 0)) for lvl in bids) + \
                      sum(float(lvl.get("size", 0)) for lvl in asks)

        self._depths[mkt].append(total_depth)

        max_depth = float(self._config.max_expected_depth)
        if max_depth <= 0:
            return 0.0

        score = min(1.0, total_depth / max_depth)
        return score

    def _compute_toxic_flow_zscore(self, mkt: str) -> float:
        """Z-score of the latest book_imbalance relative to rolling history."""
        imbalances = self._imbalances[mkt]
        if len(imbalances) < self._config.min_data_points:
            return 0.0

        vals = list(imbalances)
        mean = statistics.mean(vals)
        if len(vals) < 2:
            return 0.0
        stdev = statistics.stdev(vals)
        if stdev == 0:
            return 0.0

        latest = vals[-1]
        z = abs(latest - mean) / stdev
        return z

    @staticmethod
    def _estimate_queue_position(orderbook: dict[str, Any]) -> float:
        """Stub: estimate queue position based on top-of-book depth.

        Returns a number >= 0 representing estimated shares ahead.
        Real implementation would track actual queue position.
        """
        bids = orderbook.get("bids", [])
        if bids:
            # Assume we're at the back of the top level
            return float(bids[0].get("size", 0))
        return 0.0

    def _compute_data_quality(
        self,
        ms: "MarketState",
        orderbook: dict[str, Any],
        mkt: str,
    ) -> float:
        """Score from 0 to 1 reflecting completeness and freshness of data."""
        score = 1.0
        penalties = 0.0

        # Penalty: no valid bid/ask
        if ms.yes_bid <= 0 or ms.yes_ask <= 0:
            penalties += 0.4

        # Penalty: empty orderbook
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        if not bids or not asks:
            penalties += 0.3

        # Penalty: insufficient rolling data
        data_points = len(self._mid_prices.get(mkt, []))
        if data_points < self._config.min_data_points:
            penalties += 0.2

        # Penalty: crossed or zero-width book (data issue)
        if ms.yes_bid > 0 and ms.yes_ask > 0 and ms.yes_bid >= ms.yes_ask:
            penalties += 0.3

        score = max(0.0, score - penalties)
        return round(score, 4)


# Re-import for type checking (deferred to avoid circular imports)
from models.market_state import MarketState  # noqa: E402
