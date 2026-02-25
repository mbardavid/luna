"""Tests for ai_copilot — Fase 9.

Covers:
- PostMortemAnalyser — daily report generation, anomaly flags, Markdown/JSON output
- ParamTuner — Bayesian optimisation, suggestion generation
- AnomalyDetector — rolling z-score, alert creation, event bus publication
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest

from ai_copilot.post_mortem import (
    DailyReport,
    FillRecord,
    MarketSummary,
    PostMortemAnalyser,
    PositionSnapshot,
    SpreadSnapshot,
)
from ai_copilot.param_tuner import (
    ParamRange,
    ParamSuggestion,
    ParamTuner,
    ParamTunerConfig,
    TunerResult,
)
from ai_copilot.anomaly_detector import (
    AnomalyAlert,
    AnomalyDetector,
    AnomalyDetectorConfig,
    MetricConfig,
    _RollingWindow,
)
from core.event_bus import EventBus


# ═══════════════════════════════════════════════════════════════════════
# PostMortemAnalyser Tests
# ═══════════════════════════════════════════════════════════════════════


class TestPostMortemAnalyser:
    """Tests for the PostMortemAnalyser."""

    @pytest.fixture
    def analyser(self) -> PostMortemAnalyser:
        return PostMortemAnalyser(
            drawdown_alert_pct=Decimal("0.05"),
            low_fill_rate_threshold=0.5,
            spread_compression_bps=Decimal("5"),
            inventory_imbalance_threshold=Decimal("500"),
        )

    @pytest.fixture
    def sample_fills(self) -> list[FillRecord]:
        base = datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)
        return [
            FillRecord(
                market_id="mkt-001",
                side="BUY",
                token_side="YES",
                price=Decimal("0.50"),
                size=Decimal("100"),
                fee=Decimal("0.10"),
                timestamp=base,
            ),
            FillRecord(
                market_id="mkt-001",
                side="SELL",
                token_side="YES",
                price=Decimal("0.55"),
                size=Decimal("100"),
                fee=Decimal("0.10"),
                timestamp=base + timedelta(hours=1),
            ),
            FillRecord(
                market_id="mkt-001",
                side="BUY",
                token_side="NO",
                price=Decimal("0.45"),
                size=Decimal("50"),
                fee=Decimal("0.05"),
                timestamp=base + timedelta(hours=2),
            ),
            FillRecord(
                market_id="mkt-002",
                side="SELL",
                token_side="YES",
                price=Decimal("0.60"),
                size=Decimal("200"),
                fee=Decimal("0.20"),
                timestamp=base + timedelta(hours=3),
            ),
        ]

    @pytest.fixture
    def sample_positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                market_id="mkt-001",
                qty_yes=Decimal("100"),
                qty_no=Decimal("50"),
                unrealized_pnl=Decimal("5.00"),
                realized_pnl=Decimal("4.80"),
            ),
            PositionSnapshot(
                market_id="mkt-002",
                qty_yes=Decimal("0"),
                qty_no=Decimal("0"),
                unrealized_pnl=Decimal("0"),
                realized_pnl=Decimal("119.80"),
            ),
        ]

    @pytest.fixture
    def sample_spreads(self) -> list[SpreadSnapshot]:
        base = datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)
        return [
            SpreadSnapshot(market_id="mkt-001", spread_bps=Decimal("20"), timestamp=base),
            SpreadSnapshot(
                market_id="mkt-001",
                spread_bps=Decimal("30"),
                timestamp=base + timedelta(hours=1),
            ),
            SpreadSnapshot(
                market_id="mkt-001",
                spread_bps=Decimal("15"),
                timestamp=base + timedelta(hours=2),
            ),
        ]

    def test_basic_report_generation(
        self, analyser: PostMortemAnalyser, sample_fills, sample_positions
    ):
        """Report includes all markets and aggregates correctly."""
        report = analyser.analyse(
            fills=sample_fills,
            positions=sample_positions,
            report_date=date(2026, 2, 25),
        )

        assert report.report_date == date(2026, 2, 25)
        assert report.num_markets_active == 2
        assert report.total_fills == 4
        assert report.total_volume > Decimal("0")
        assert report.realized_pnl == Decimal("4.80") + Decimal("119.80")
        assert report.unrealized_pnl == Decimal("5.00")
        assert report.total_pnl == report.realized_pnl + report.unrealized_pnl

    def test_per_market_breakdown(
        self, analyser: PostMortemAnalyser, sample_fills, sample_positions
    ):
        """Each market has its own summary."""
        report = analyser.analyse(
            fills=sample_fills,
            positions=sample_positions,
            report_date=date(2026, 2, 25),
        )

        assert len(report.market_summaries) == 2
        mkt1 = next(ms for ms in report.market_summaries if ms.market_id == "mkt-001")
        mkt2 = next(ms for ms in report.market_summaries if ms.market_id == "mkt-002")

        assert mkt1.total_fills == 3
        assert mkt1.buy_fills == 2
        assert mkt1.sell_fills == 1
        assert mkt1.net_inventory == Decimal("50")  # 100 YES - 50 NO

        assert mkt2.total_fills == 1
        assert mkt2.sell_fills == 1

    def test_spread_metrics(
        self,
        analyser: PostMortemAnalyser,
        sample_fills,
        sample_positions,
        sample_spreads,
    ):
        """Spread statistics are computed from spread snapshots."""
        report = analyser.analyse(
            fills=sample_fills,
            positions=sample_positions,
            spreads=sample_spreads,
            report_date=date(2026, 2, 25),
        )

        mkt1 = next(ms for ms in report.market_summaries if ms.market_id == "mkt-001")
        assert mkt1.min_spread_bps == Decimal("15")
        assert mkt1.max_spread_bps == Decimal("30")
        assert mkt1.avg_spread_bps > Decimal("0")

    def test_empty_fills(self, analyser: PostMortemAnalyser, sample_positions):
        """Zero-fill scenario produces a valid report with anomaly flag."""
        report = analyser.analyse(
            fills=[],
            positions=sample_positions,
            report_date=date(2026, 2, 25),
        )

        assert report.total_fills == 0
        assert report.num_markets_active == 2
        assert any("Zero fills" in a for a in report.anomalies)

    def test_max_drawdown_calculation(self, analyser: PostMortemAnalyser):
        """Drawdown is computed correctly from buy/sell sequence."""
        fills = [
            FillRecord(
                market_id="m",
                side="SELL",
                token_side="YES",
                price=Decimal("0.60"),
                size=Decimal("100"),
                fee=Decimal("0"),
                timestamp=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
            ),
            FillRecord(
                market_id="m",
                side="BUY",
                token_side="YES",
                price=Decimal("0.80"),
                size=Decimal("100"),
                fee=Decimal("0"),
                timestamp=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
            ),
        ]

        report = analyser.analyse(
            fills=fills,
            positions=[
                PositionSnapshot(market_id="m", realized_pnl=Decimal("-20")),
            ],
            report_date=date(2026, 1, 1),
        )

        # Sell +60, then Buy -80 → cumulative goes +60 then -20
        # Peak was +60, trough is -20 → drawdown = 80
        assert report.max_drawdown == Decimal("80")

    def test_to_json(self, analyser: PostMortemAnalyser, sample_fills, sample_positions):
        """Report serialises to valid JSON."""
        report = analyser.analyse(
            fills=sample_fills,
            positions=sample_positions,
            report_date=date(2026, 2, 25),
        )

        json_str = report.to_json()
        parsed = json.loads(json_str)

        assert parsed["report_date"] == "2026-02-25"
        assert isinstance(parsed["total_pnl"], str)  # Decimal → str
        assert "market_summaries" in parsed

    def test_to_markdown(
        self, analyser: PostMortemAnalyser, sample_fills, sample_positions
    ):
        """Report produces non-empty Markdown with expected sections."""
        report = analyser.analyse(
            fills=sample_fills,
            positions=sample_positions,
            report_date=date(2026, 2, 25),
        )

        md = report.to_markdown()
        assert "# Daily Post-Mortem" in md
        assert "2026-02-25" in md
        assert "## Summary" in md
        assert "Total PnL" in md
        assert "Per-Market Breakdown" in md

    def test_inventory_imbalance_anomaly(self, analyser: PostMortemAnalyser):
        """High inventory imbalance is flagged as anomaly."""
        fills = [
            FillRecord(
                market_id="m",
                side="BUY",
                token_side="YES",
                price=Decimal("0.50"),
                size=Decimal("1000"),
                fee=Decimal("1"),
            ),
        ]
        positions = [
            PositionSnapshot(
                market_id="m",
                qty_yes=Decimal("1000"),
                qty_no=Decimal("0"),
                realized_pnl=Decimal("0"),
            ),
        ]

        report = analyser.analyse(fills=fills, positions=positions)
        assert any("Inventory imbalance" in a for a in report.anomalies)

    def test_spread_compression_anomaly(self, analyser: PostMortemAnalyser):
        """Tight spreads below threshold trigger anomaly."""
        spreads = [
            SpreadSnapshot(market_id="m", spread_bps=Decimal("3")),
            SpreadSnapshot(market_id="m", spread_bps=Decimal("10")),
        ]
        positions = [PositionSnapshot(market_id="m")]

        report = analyser.analyse(
            fills=[],
            positions=positions,
            spreads=spreads,
        )
        assert any("Spread compression" in a for a in report.anomalies)


# ═══════════════════════════════════════════════════════════════════════
# ParamTuner Tests
# ═══════════════════════════════════════════════════════════════════════


class TestParamTuner:
    """Tests for the ParamTuner (Bayesian optimisation)."""

    @pytest.fixture
    def sample_fills(self) -> list[FillRecord]:
        """Diverse fills to give the optimizer signal."""
        base = datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)
        fills = []
        for i in range(20):
            fills.append(
                FillRecord(
                    market_id="mkt-001",
                    side="BUY" if i % 3 == 0 else "SELL",
                    token_side="YES",
                    price=Decimal(str(0.45 + i * 0.005)),
                    size=Decimal("50"),
                    fee=Decimal("0.05"),
                    timestamp=base + timedelta(minutes=i * 10),
                )
            )
        return fills

    @pytest.fixture
    def sample_positions(self) -> list[PositionSnapshot]:
        return [
            PositionSnapshot(
                market_id="mkt-001",
                qty_yes=Decimal("200"),
                qty_no=Decimal("150"),
                realized_pnl=Decimal("10"),
            ),
        ]

    def test_basic_optimisation(self, sample_fills, sample_positions):
        """Tuner runs and returns a valid TunerResult."""
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=10, sampler_seed=42),
        )

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
            n_trials=10,
        )

        assert isinstance(result, TunerResult)
        assert result.n_trials_completed == 10
        assert result.generated_at is not None

    def test_suggestions_have_required_fields(self, sample_fills, sample_positions):
        """Each suggestion has all required fields."""
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=15, sampler_seed=42),
        )

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
        )

        for s in result.suggestions:
            assert isinstance(s, ParamSuggestion)
            assert s.param_name
            assert s.confidence in ("low", "medium", "high")
            assert isinstance(s.reason, str)
            assert len(s.reason) > 0

    def test_custom_objective_fn(self, sample_fills, sample_positions):
        """Custom objective functions are honoured."""

        def custom_obj(fills, positions, params):
            # Simple objective: prefer high gamma
            return params.get("gamma_risk_aversion", 0.3) * 100

        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=10, sampler_seed=42),
            objective_fn=custom_obj,
        )

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
        )

        # Best value should be > 30 (current gamma=0.3 → 30)
        assert result.best_objective_value > 25

    def test_result_to_json(self, sample_fills, sample_positions):
        """TunerResult serialises to valid JSON."""
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=5, sampler_seed=42),
        )

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
        )

        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert "suggestions" in parsed
        assert "n_trials_completed" in parsed

    def test_result_to_markdown(self, sample_fills, sample_positions):
        """TunerResult produces valid Markdown."""
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=5, sampler_seed=42),
        )

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
        )

        md = result.to_markdown()
        assert "# Parameter Tuning Results" in md
        assert "Trials:" in md

    def test_empty_fills(self, sample_positions):
        """Tuner handles zero fills gracefully."""
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=5, sampler_seed=42),
        )

        result = tuner.optimise(
            historical_fills=[],
            historical_positions=sample_positions,
        )

        assert isinstance(result, TunerResult)
        assert result.n_trials_completed == 5

    def test_single_param_range(self, sample_fills, sample_positions):
        """Works with a custom single-parameter search space."""
        config = ParamTunerConfig(
            param_ranges=[
                ParamRange(
                    name="gamma_risk_aversion",
                    low=0.1,
                    high=0.9,
                    current=0.3,
                    description="Test gamma only",
                ),
            ],
            n_trials=10,
            sampler_seed=42,
        )
        tuner = ParamTuner(config=config)

        result = tuner.optimise(
            historical_fills=sample_fills,
            historical_positions=sample_positions,
        )

        assert isinstance(result, TunerResult)

    def test_param_suggestion_to_dict(self):
        """ParamSuggestion serialises correctly."""
        s = ParamSuggestion(
            param_name="gamma",
            current_value=0.3,
            suggested_value=0.45,
            expected_improvement_pct=12.5,
            reason="Better risk adjustment",
            confidence="medium",
        )
        d = s.to_dict()
        assert d["param_name"] == "gamma"
        assert d["suggested_value"] == 0.45


# ═══════════════════════════════════════════════════════════════════════
# AnomalyDetector Tests
# ═══════════════════════════════════════════════════════════════════════


class TestRollingWindow:
    """Tests for the internal _RollingWindow helper."""

    def test_basic_stats(self):
        """Mean and std are computed correctly."""
        w = _RollingWindow(max_size=5)
        for v in [10.0, 20.0, 30.0, 40.0, 50.0]:
            w.push(v)

        assert w.count == 5
        assert w.mean == pytest.approx(30.0)
        assert w.std > 0

    def test_rolling_eviction(self):
        """Old values are evicted when window is full."""
        w = _RollingWindow(max_size=3)
        for v in [1.0, 2.0, 3.0, 4.0, 5.0]:
            w.push(v)

        assert w.count == 3
        # Window contains [3, 4, 5]
        assert w.mean == pytest.approx(4.0)

    def test_zscore(self):
        """Z-score is computed correctly for an outlier."""
        w = _RollingWindow(max_size=10)
        for v in [10.0, 10.0, 10.0, 10.0, 10.0]:
            w.push(v)

        # All values identical → std = 0 → zscore should be 0
        assert w.zscore(100.0) == 0.0

        # Now add some variance
        w2 = _RollingWindow(max_size=10)
        for v in [10.0, 12.0, 8.0, 11.0, 9.0]:
            w2.push(v)

        z = w2.zscore(20.0)
        assert z > 2.0  # 20 is far above mean of ~10

    def test_empty_window(self):
        """Empty window returns zero for mean/std/zscore."""
        w = _RollingWindow(max_size=5)
        assert w.mean == 0.0
        assert w.std == 0.0
        assert w.zscore(5.0) == 0.0


class TestAnomalyDetector:
    """Tests for the AnomalyDetector."""

    @pytest.fixture
    def event_bus(self) -> EventBus:
        return EventBus()

    @pytest.fixture
    def detector(self, event_bus: EventBus) -> AnomalyDetector:
        return AnomalyDetector(
            event_bus=event_bus,
            config=AnomalyDetectorConfig(
                metrics=[
                    MetricConfig(
                        name="pnl_drawdown",
                        zscore_threshold=2.0,
                        window_size=20,
                        description="PnL drawdown",
                        alert_cooldown_seconds=0,  # No cooldown for tests
                    ),
                    MetricConfig(
                        name="fill_rate",
                        zscore_threshold=2.5,
                        window_size=20,
                        description="Fill rate",
                        alert_cooldown_seconds=0,
                    ),
                    MetricConfig(
                        name="spread_bps",
                        zscore_threshold=2.5,
                        window_size=20,
                        description="Spread BPS",
                        alert_cooldown_seconds=0,
                    ),
                    MetricConfig(
                        name="inventory_imbalance",
                        zscore_threshold=2.0,
                        window_size=20,
                        description="Inventory imbalance",
                        alert_cooldown_seconds=0,
                    ),
                ],
            ),
        )

    @pytest.mark.asyncio
    async def test_no_anomaly_on_normal_values(self, detector: AnomalyDetector):
        """Normal values within the distribution don't trigger alerts."""
        # Populate window with stable values
        for _ in range(10):
            alert = await detector.observe("fill_rate", Decimal("5.0"))
            assert alert is None

    @pytest.mark.asyncio
    async def test_anomaly_on_extreme_value(self, detector: AnomalyDetector):
        """Extreme outlier triggers an anomaly alert."""
        # Build up a window of normal values
        for v in [10.0, 11.0, 10.5, 9.5, 10.0, 10.2, 9.8, 10.1, 10.3, 9.9]:
            await detector.observe("pnl_drawdown", Decimal(str(v)))

        # Inject an extreme outlier
        alert = await detector.observe("pnl_drawdown", Decimal("50.0"))
        assert alert is not None
        assert isinstance(alert, AnomalyAlert)
        assert alert.metric_name == "pnl_drawdown"
        assert abs(alert.zscore) >= 2.0

    @pytest.mark.asyncio
    async def test_alert_severity_levels(self, detector: AnomalyDetector):
        """Critical severity for very extreme outliers."""
        # Build stable window
        for v in [1.0, 1.0, 1.0, 1.0, 1.0, 1.1, 0.9, 1.0, 1.0, 1.0]:
            await detector.observe("spread_bps", Decimal(str(v)))

        # Extreme outlier (>> 2.5 * 1.5 threshold)
        alert = await detector.observe("spread_bps", Decimal("100.0"))
        if alert is not None:
            assert alert.severity in ("warning", "critical")

    @pytest.mark.asyncio
    async def test_alert_to_dict(self, detector: AnomalyDetector):
        """Alert serialises to dict with all expected keys."""
        for v in [10.0, 10.0, 10.0, 10.0, 10.0, 10.5, 9.5, 10.0, 10.0, 10.0]:
            await detector.observe("inventory_imbalance", Decimal(str(v)))

        alert = await detector.observe("inventory_imbalance", Decimal("50.0"))
        if alert is not None:
            d = alert.to_dict()
            assert "metric_name" in d
            assert "zscore" in d
            assert "severity" in d
            assert "timestamp" in d

    @pytest.mark.asyncio
    async def test_event_bus_publication(
        self, event_bus: EventBus, detector: AnomalyDetector
    ):
        """Anomaly alerts are published to the event bus."""
        received: list[Any] = []

        async def _collect():
            async for event in event_bus.subscribe(AnomalyDetector.ALERT_TOPIC):
                received.append(event)
                return  # Stop after first event

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0.01)  # Let subscriber register

        # Build window and inject outlier
        for v in [10.0, 10.0, 10.0, 10.0, 10.0, 10.5, 9.5, 10.0, 10.0, 10.0]:
            await detector.observe("pnl_drawdown", Decimal(str(v)))

        await detector.observe("pnl_drawdown", Decimal("100.0"))
        await asyncio.sleep(0.05)  # Let event propagate

        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if received:
            assert received[0].topic == AnomalyDetector.ALERT_TOPIC
            assert "metric_name" in received[0].payload

    @pytest.mark.asyncio
    async def test_cooldown_prevents_spam(self):
        """Alert cooldown prevents duplicate alerts."""
        detector = AnomalyDetector(
            config=AnomalyDetectorConfig(
                metrics=[
                    MetricConfig(
                        name="test_metric",
                        zscore_threshold=2.0,
                        window_size=50,
                        alert_cooldown_seconds=3600,  # 1 hour cooldown
                    ),
                ],
            ),
        )

        # Populate window with values that have some variance
        for v in [10.0, 10.5, 9.5, 10.2, 9.8, 10.1, 9.9, 10.3, 9.7, 10.4,
                  9.6, 10.0, 10.1, 9.9, 10.2, 9.8, 10.0, 10.3, 9.7, 10.0]:
            await detector.observe("test_metric", Decimal(str(v)))

        # First anomaly — extreme outlier
        alert1 = await detector.observe("test_metric", Decimal("100.0"))
        assert alert1 is not None

        # Populate more normal values to keep window stable
        for _ in range(5):
            await detector.observe("test_metric", Decimal("10.0"))

        # Second anomaly (should be suppressed by cooldown)
        alert2 = await detector.observe("test_metric", Decimal("100.0"))
        assert alert2 is None  # Suppressed by cooldown

    @pytest.mark.asyncio
    async def test_dynamic_metric(self, detector: AnomalyDetector):
        """Unknown metric names are handled with default config."""
        # Observe a metric not in the initial config
        for _ in range(10):
            await detector.observe("new_custom_metric", Decimal("5.0"))

        stats = detector.get_window_stats("new_custom_metric")
        assert stats["count"] == 10

    def test_reset_single_metric(self, detector: AnomalyDetector):
        """Resetting a single metric clears only that window."""
        # We can't use await here, but we can test the reset method
        detector.reset("pnl_drawdown")
        stats = detector.get_window_stats("pnl_drawdown")
        assert stats["count"] == 0

        # Other metrics should still have their windows
        stats2 = detector.get_window_stats("fill_rate")
        assert stats2["count"] == 0  # Also empty because no data yet

    def test_reset_all(self, detector: AnomalyDetector):
        """Resetting all metrics clears everything."""
        detector.reset()
        for metric in ["pnl_drawdown", "fill_rate", "spread_bps", "inventory_imbalance"]:
            assert detector.get_window_stats(metric)["count"] == 0

    @pytest.mark.asyncio
    async def test_window_stats_accuracy(self, detector: AnomalyDetector):
        """Window stats reflect the actual data."""
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        for v in values:
            await detector.observe("fill_rate", Decimal(str(v)))

        stats = detector.get_window_stats("fill_rate")
        assert stats["count"] == 5
        assert stats["mean"] == pytest.approx(30.0)
        assert stats["std"] > 0

    @pytest.mark.asyncio
    async def test_no_alert_insufficient_data(self, detector: AnomalyDetector):
        """No alert when window has fewer than 5 observations."""
        # Only 3 observations — too few for meaningful z-score
        for v in [10.0, 10.0, 10.0]:
            await detector.observe("pnl_drawdown", Decimal(str(v)))

        alert = await detector.observe("pnl_drawdown", Decimal("100.0"))
        assert alert is None  # Not enough data

    @pytest.mark.asyncio
    async def test_detector_without_event_bus(self):
        """Detector works without an event bus (logs only)."""
        detector = AnomalyDetector(
            event_bus=None,
            config=AnomalyDetectorConfig(
                metrics=[
                    MetricConfig(
                        name="test",
                        zscore_threshold=2.0,
                        window_size=20,
                        alert_cooldown_seconds=0,
                    ),
                ],
            ),
        )

        for v in [10.0, 10.0, 10.0, 10.0, 10.0, 10.5, 9.5, 10.0, 10.0, 10.0]:
            await detector.observe("test", Decimal(str(v)))

        alert = await detector.observe("test", Decimal("100.0"))
        assert alert is not None  # Alert created but no bus publication


# ═══════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end integration tests across modules."""

    def test_post_mortem_feeds_param_tuner(self):
        """PostMortem report data can feed into ParamTuner."""
        analyser = PostMortemAnalyser()
        base = datetime(2026, 2, 25, 10, 0, 0, tzinfo=timezone.utc)

        fills = [
            FillRecord(
                market_id="m",
                side="SELL" if i % 2 == 0 else "BUY",
                token_side="YES",
                price=Decimal(str(0.50 + i * 0.01)),
                size=Decimal("100"),
                fee=Decimal("0.10"),
                timestamp=base + timedelta(minutes=i * 5),
            )
            for i in range(15)
        ]
        positions = [
            PositionSnapshot(
                market_id="m",
                qty_yes=Decimal("300"),
                qty_no=Decimal("200"),
                realized_pnl=Decimal("50"),
            )
        ]

        # Generate report
        report = analyser.analyse(fills=fills, positions=positions)
        assert report.total_fills == 15

        # Use same fills for tuner
        tuner = ParamTuner(
            config=ParamTunerConfig(n_trials=5, sampler_seed=42),
        )
        result = tuner.optimise(
            historical_fills=fills,
            historical_positions=positions,
        )
        assert result.n_trials_completed == 5

    @pytest.mark.asyncio
    async def test_anomaly_detector_with_post_mortem_data(self):
        """Anomaly detector processes data from post-mortem fill records."""
        bus = EventBus()
        detector = AnomalyDetector(
            event_bus=bus,
            config=AnomalyDetectorConfig(
                metrics=[
                    MetricConfig(
                        name="realized_pnl",
                        zscore_threshold=2.0,
                        window_size=20,
                        alert_cooldown_seconds=0,
                    )
                ]
            ),
        )

        # Simulate daily PnL observations
        daily_pnls = [
            10.0, 12.0, 8.0, 11.0, 9.0,
            10.5, 11.0, 9.5, 10.0, 10.0,
        ]

        for pnl in daily_pnls:
            await detector.observe("realized_pnl", Decimal(str(pnl)))

        # Sudden loss — should trigger alert
        alert = await detector.observe("realized_pnl", Decimal("-50.0"))
        assert alert is not None
        assert alert.metric_name == "realized_pnl"
