"""ToxicFlowDetector — monitors toxic flow signals from FeatureVector.

Uses the pre-computed ``toxic_flow_score`` from FeatureVector (z-score
of book_imbalance computed by FeatureEngine) to detect when
market-taking flow is likely informed (toxic to market-makers).
Publishes ``toxic_flow`` events to EventBus when thresholds are breached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from core.event_bus import EventBus
from models.feature_vector import FeatureVector

logger = structlog.get_logger("strategy.toxic_flow_detector")


@dataclass
class ToxicFlowConfig:
    """Configuration for toxic flow detection thresholds."""

    # Z-score threshold for "toxic" classification
    toxic_zscore_threshold: float = 2.5

    # Z-score threshold for "halt" — more aggressive, triggers quote withdrawal
    halt_zscore_threshold: float = 3.5

    # Combined halt: if toxic_flow_score > this AND abs(book_imbalance) > imbalance_threshold
    imbalance_halt_threshold: float = 0.8

    # Combined halt z-score (lower when combined with extreme imbalance)
    combined_zscore_threshold: float = 3.0


class ToxicFlowDetector:
    """Detects toxic (informed) order flow from FeatureVector signals.

    Relies entirely on the ``toxic_flow_score`` field of the
    FeatureVector, which is computed by FeatureEngine as a z-score of
    book_imbalance over its own rolling window.

    Usage::

        detector = ToxicFlowDetector(event_bus=bus)
        if detector.is_toxic(feature_vector):
            # Widen spreads
        if detector.should_halt(feature_vector):
            # Withdraw all quotes
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        config: ToxicFlowConfig | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._config = config or ToxicFlowConfig()

        # Track last event publication to avoid spamming
        self._last_toxic_event: dict[str, datetime] = {}

    def get_zscore(self, fv: FeatureVector) -> float:
        """Return the toxic flow z-score from the FeatureVector.

        Simply delegates to ``fv.toxic_flow_score`` which is computed
        by the FeatureEngine.
        """
        return fv.toxic_flow_score

    def is_toxic(self, fv: FeatureVector) -> bool:
        """Return True when toxic flow is detected.

        Toxic flow is flagged when the z-score of book_imbalance exceeds
        ``toxic_zscore_threshold`` (default 2.5).
        """
        zscore = self.get_zscore(fv)
        is_toxic = zscore > self._config.toxic_zscore_threshold
        if is_toxic:
            logger.warning(
                "toxic_flow.detected",
                market_id=fv.market_id,
                zscore=round(zscore, 3),
                imbalance=round(fv.book_imbalance, 4),
            )
        return is_toxic

    def should_halt(self, fv: FeatureVector) -> bool:
        """Return True when conditions warrant halting all quotes.

        Halt is triggered by:
        1. Z-score > ``halt_zscore_threshold`` (default 3.5), OR
        2. Combined signal: z-score > ``combined_zscore_threshold`` (3.0)
           AND abs(book_imbalance) > ``imbalance_halt_threshold`` (0.8)
        """
        zscore = self.get_zscore(fv)

        # Pure z-score halt
        if zscore > self._config.halt_zscore_threshold:
            logger.critical(
                "toxic_flow.halt_triggered",
                market_id=fv.market_id,
                zscore=round(zscore, 3),
                reason="extreme_zscore",
            )
            return True

        # Combined signal halt
        if (
            zscore > self._config.combined_zscore_threshold
            and abs(fv.book_imbalance) > self._config.imbalance_halt_threshold
        ):
            logger.critical(
                "toxic_flow.halt_triggered",
                market_id=fv.market_id,
                zscore=round(zscore, 3),
                imbalance=round(fv.book_imbalance, 4),
                reason="combined_signal",
            )
            return True

        return False

    async def evaluate_and_publish(self, fv: FeatureVector) -> bool:
        """Evaluate toxicity and publish event if toxic.

        Returns True if toxic flow was detected.
        """
        toxic = self.is_toxic(fv)

        if toxic and self._event_bus is not None:
            zscore = self.get_zscore(fv)
            halt = self.should_halt(fv)

            await self._event_bus.publish(
                "toxic_flow",
                {
                    "market_id": fv.market_id,
                    "zscore": round(zscore, 4),
                    "book_imbalance": round(fv.book_imbalance, 4),
                    "toxic_flow_score": round(fv.toxic_flow_score, 4),
                    "should_halt": halt,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            self._last_toxic_event[fv.market_id] = datetime.now(timezone.utc)

        return toxic

    def reset(self, market_id: str | None = None) -> None:
        """Clear state for a market (or all)."""
        if market_id:
            self._last_toxic_event.pop(market_id, None)
        else:
            self._last_toxic_event.clear()
