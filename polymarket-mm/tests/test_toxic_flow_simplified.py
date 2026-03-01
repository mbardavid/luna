"""Tests for TASK 1 — ToxicFlowDetector simplified (no rolling window).

Verifies that ToxicFlowDetector uses ONLY the toxic_flow_score from
the FeatureVector, without maintaining any internal rolling window.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from models.feature_vector import FeatureVector
from strategy.toxic_flow_detector import ToxicFlowDetector, ToxicFlowConfig


def _make_fv(
    toxic_flow_score: float = 0.0,
    book_imbalance: float = 0.0,
    market_id: str = "test-mkt",
) -> FeatureVector:
    return FeatureVector(
        market_id=market_id,
        spread_bps=Decimal("80"),
        book_imbalance=book_imbalance,
        volatility_1m=0.008,
        liquidity_score=0.6,
        toxic_flow_score=toxic_flow_score,
        expected_fee_bps=Decimal("2"),
        data_quality_score=0.9,
    )


class TestToxicFlowDetectorSimplified:
    """ToxicFlowDetector should rely solely on FeatureVector.toxic_flow_score."""

    def test_no_rolling_window_attribute(self) -> None:
        """Detector should not have _imbalances (rolling window removed)."""
        detector = ToxicFlowDetector()
        assert not hasattr(detector, "_imbalances"), (
            "ToxicFlowDetector should not maintain a rolling window"
        )

    def test_no_update_method(self) -> None:
        """Detector should not have an update() method."""
        detector = ToxicFlowDetector()
        assert not hasattr(detector, "update"), (
            "ToxicFlowDetector should not have update() — "
            "FeatureEngine computes the z-score"
        )

    def test_get_zscore_returns_fv_score(self) -> None:
        """get_zscore should return toxic_flow_score from FeatureVector."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=2.7)
        assert detector.get_zscore(fv) == 2.7

    def test_get_zscore_returns_zero_when_fv_zero(self) -> None:
        """get_zscore returns 0 when FeatureVector has toxic_flow_score=0."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=0.0)
        assert detector.get_zscore(fv) == 0.0

    def test_is_toxic_below_threshold(self) -> None:
        """Score below threshold → not toxic."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=2.0)
        assert not detector.is_toxic(fv)

    def test_is_toxic_above_threshold(self) -> None:
        """Score above threshold → toxic."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=3.0)
        assert detector.is_toxic(fv)

    def test_is_toxic_at_threshold_not_toxic(self) -> None:
        """Score exactly at threshold → not toxic (strictly greater)."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=2.5)
        assert not detector.is_toxic(fv)

    def test_should_halt_extreme_zscore(self) -> None:
        """z-score > halt_zscore_threshold → halt."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=4.0)
        assert detector.should_halt(fv)

    def test_should_halt_below_halt_threshold(self) -> None:
        """z-score below halt threshold → no halt."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=3.0)
        assert not detector.should_halt(fv)

    def test_should_halt_combined_signal(self) -> None:
        """z-score > combined threshold AND extreme imbalance → halt."""
        config = ToxicFlowConfig(
            combined_zscore_threshold=3.0,
            imbalance_halt_threshold=0.8,
        )
        detector = ToxicFlowDetector(config=config)
        fv = _make_fv(toxic_flow_score=3.2, book_imbalance=0.9)
        assert detector.should_halt(fv)

    def test_should_halt_combined_signal_imbalance_too_low(self) -> None:
        """z-score > combined threshold but imbalance below threshold → no halt."""
        config = ToxicFlowConfig(
            combined_zscore_threshold=3.0,
            imbalance_halt_threshold=0.8,
        )
        detector = ToxicFlowDetector(config=config)
        fv = _make_fv(toxic_flow_score=3.2, book_imbalance=0.5)
        assert not detector.should_halt(fv)

    def test_custom_thresholds(self) -> None:
        """Custom config thresholds are respected."""
        config = ToxicFlowConfig(
            toxic_zscore_threshold=1.0,
            halt_zscore_threshold=2.0,
        )
        detector = ToxicFlowDetector(config=config)

        fv_mild = _make_fv(toxic_flow_score=1.5)
        assert detector.is_toxic(fv_mild)
        assert not detector.should_halt(fv_mild)

        fv_extreme = _make_fv(toxic_flow_score=2.5)
        assert detector.is_toxic(fv_extreme)
        assert detector.should_halt(fv_extreme)

    def test_different_markets_independent(self) -> None:
        """Detector doesn't carry state between markets."""
        detector = ToxicFlowDetector()

        fv_a = _make_fv(toxic_flow_score=3.0, market_id="market-A")
        fv_b = _make_fv(toxic_flow_score=1.0, market_id="market-B")

        assert detector.is_toxic(fv_a)
        assert not detector.is_toxic(fv_b)

    def test_reset_clears_last_event_tracking(self) -> None:
        """reset() clears the _last_toxic_event dict."""
        detector = ToxicFlowDetector()
        from datetime import datetime, timezone
        detector._last_toxic_event["market-A"] = datetime.now(timezone.utc)

        detector.reset("market-A")
        assert "market-A" not in detector._last_toxic_event

        detector._last_toxic_event["market-B"] = datetime.now(timezone.utc)
        detector.reset()
        assert len(detector._last_toxic_event) == 0

    @pytest.mark.asyncio
    async def test_evaluate_and_publish_no_update_call(self) -> None:
        """evaluate_and_publish should work without calling update()."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=3.0)
        result = await detector.evaluate_and_publish(fv)
        assert result is True  # toxic detected

    @pytest.mark.asyncio
    async def test_evaluate_and_publish_not_toxic(self) -> None:
        """evaluate_and_publish returns False when not toxic."""
        detector = ToxicFlowDetector()
        fv = _make_fv(toxic_flow_score=1.0)
        result = await detector.evaluate_and_publish(fv)
        assert result is False
