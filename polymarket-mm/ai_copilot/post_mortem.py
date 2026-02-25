"""PostMortemAnalyser — daily analysis of PnL, fills, spreads, and anomalies.

Generates a structured report (JSON + human-readable Markdown) summarising
a trading day.  This is a **read-only** analytical tool — it never places
orders or modifies strategy parameters.

Usage::

    analyser = PostMortemAnalyser()
    report = analyser.analyse(
        fills=fill_records,
        positions=position_snapshots,
        market_states=market_state_snapshots,
        date=datetime.date.today(),
    )
    print(report.to_markdown())
    print(report.to_json())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from statistics import mean, stdev
from typing import Any, Sequence

import structlog

logger = structlog.get_logger("ai_copilot.post_mortem")

_ZERO = Decimal("0")


# ── Data classes for report input ────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FillRecord:
    """A single fill (partial or complete) that occurred during the day."""

    market_id: str
    side: str  # "BUY" or "SELL"
    token_side: str  # "YES" or "NO"
    price: Decimal
    size: Decimal
    fee: Decimal = _ZERO
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """End-of-day position snapshot for a single market."""

    market_id: str
    qty_yes: Decimal = _ZERO
    qty_no: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO


@dataclass(frozen=True, slots=True)
class SpreadSnapshot:
    """Spread observation at a point in time."""

    market_id: str
    spread_bps: Decimal
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ── Report output ────────────────────────────────────────────────────


@dataclass
class MarketSummary:
    """Per-market summary within a daily report."""

    market_id: str
    total_fills: int = 0
    buy_fills: int = 0
    sell_fills: int = 0
    total_volume: Decimal = _ZERO
    total_fees: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO
    avg_fill_price: Decimal = _ZERO
    avg_spread_bps: Decimal = _ZERO
    min_spread_bps: Decimal = _ZERO
    max_spread_bps: Decimal = _ZERO
    net_inventory: Decimal = _ZERO
    fill_rate: float = 0.0  # fills per hour
    anomalies: list[str] = field(default_factory=list)


@dataclass
class DailyReport:
    """Complete daily post-mortem report."""

    report_date: date
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Aggregate metrics
    total_pnl: Decimal = _ZERO
    realized_pnl: Decimal = _ZERO
    unrealized_pnl: Decimal = _ZERO
    total_fills: int = 0
    total_volume: Decimal = _ZERO
    total_fees: Decimal = _ZERO
    num_markets_active: int = 0
    max_drawdown: Decimal = _ZERO

    # Per-market breakdowns
    market_summaries: list[MarketSummary] = field(default_factory=list)

    # Detected anomalies (global)
    anomalies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise report to a JSON-safe dict (Decimals → strings)."""

        def _convert(obj: Any) -> Any:
            if isinstance(obj, Decimal):
                return str(obj)
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, dict):
                return {k: _convert(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_convert(i) for i in obj]
            return obj

        return _convert(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        """Serialise report to a pretty JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_markdown(self) -> str:
        """Generate a human-readable Markdown summary."""
        lines: list[str] = [
            f"# Daily Post-Mortem — {self.report_date.isoformat()}",
            "",
            f"**Generated:** {self.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total PnL | {self.total_pnl} |",
            f"| Realized PnL | {self.realized_pnl} |",
            f"| Unrealized PnL | {self.unrealized_pnl} |",
            f"| Max Drawdown | {self.max_drawdown} |",
            f"| Total Fills | {self.total_fills} |",
            f"| Total Volume | {self.total_volume} |",
            f"| Total Fees | {self.total_fees} |",
            f"| Active Markets | {self.num_markets_active} |",
            "",
        ]

        if self.anomalies:
            lines.append("## ⚠️ Anomalies")
            lines.append("")
            for a in self.anomalies:
                lines.append(f"- {a}")
            lines.append("")

        if self.market_summaries:
            lines.append("## Per-Market Breakdown")
            lines.append("")
            for ms in self.market_summaries:
                lines.append(f"### {ms.market_id}")
                lines.append("")
                lines.append(
                    f"- Fills: {ms.total_fills} "
                    f"(buy={ms.buy_fills}, sell={ms.sell_fills})"
                )
                lines.append(f"- Volume: {ms.total_volume}")
                lines.append(f"- Realized PnL: {ms.realized_pnl}")
                lines.append(
                    f"- Spread BPS: avg={ms.avg_spread_bps}, "
                    f"min={ms.min_spread_bps}, max={ms.max_spread_bps}"
                )
                lines.append(f"- Net Inventory: {ms.net_inventory}")
                lines.append(f"- Fill Rate: {ms.fill_rate:.2f} fills/hr")
                if ms.anomalies:
                    lines.append(f"- Anomalies: {', '.join(ms.anomalies)}")
                lines.append("")

        return "\n".join(lines)


# ── Analyser ─────────────────────────────────────────────────────────


class PostMortemAnalyser:
    """Generates daily post-mortem reports from trading data.

    This is a pure analytical component — it reads fill records,
    position snapshots, and spread observations, then produces a
    structured ``DailyReport``.

    Parameters
    ----------
    drawdown_alert_pct:
        PnL drawdown percentage threshold (as Decimal, e.g. ``Decimal("0.05")``
        for 5%) that triggers an anomaly flag.
    low_fill_rate_threshold:
        Minimum fills/hour below which a market is flagged as
        having anomalously low fill rate.
    spread_compression_bps:
        Spread below this level (in bps) flags possible adverse selection.
    inventory_imbalance_threshold:
        Net inventory (|YES − NO|) above this triggers an imbalance flag.
    """

    def __init__(
        self,
        drawdown_alert_pct: Decimal = Decimal("0.05"),
        low_fill_rate_threshold: float = 0.5,
        spread_compression_bps: Decimal = Decimal("5"),
        inventory_imbalance_threshold: Decimal = Decimal("500"),
    ) -> None:
        self._drawdown_pct = drawdown_alert_pct
        self._low_fill_rate = low_fill_rate_threshold
        self._spread_compression = spread_compression_bps
        self._inventory_imbalance = inventory_imbalance_threshold

    def analyse(
        self,
        fills: Sequence[FillRecord],
        positions: Sequence[PositionSnapshot],
        spreads: Sequence[SpreadSnapshot] | None = None,
        report_date: date | None = None,
        trading_hours: float = 24.0,
    ) -> DailyReport:
        """Run post-mortem analysis and return a ``DailyReport``.

        Parameters
        ----------
        fills:
            All fill records for the day.
        positions:
            End-of-day position snapshots (one per market).
        spreads:
            Spread observations over the day (optional).
        report_date:
            Date of the report. Defaults to today (UTC).
        trading_hours:
            Number of hours the bot was active (for fill-rate calculation).

        Returns
        -------
        DailyReport
            Structured report with per-market and aggregate metrics.
        """
        if report_date is None:
            report_date = datetime.now(timezone.utc).date()

        report = DailyReport(report_date=report_date)

        # Group fills by market
        fills_by_market: dict[str, list[FillRecord]] = {}
        for f in fills:
            fills_by_market.setdefault(f.market_id, []).append(f)

        # Group positions by market
        pos_by_market: dict[str, PositionSnapshot] = {}
        for p in positions:
            pos_by_market[p.market_id] = p

        # Group spreads by market
        spreads_by_market: dict[str, list[SpreadSnapshot]] = {}
        if spreads:
            for s in spreads:
                spreads_by_market.setdefault(s.market_id, []).append(s)

        # All market IDs
        all_markets = set(fills_by_market) | set(pos_by_market)
        report.num_markets_active = len(all_markets)

        # Compute drawdown from fills
        report.max_drawdown = self._compute_max_drawdown(fills)

        # Per-market analysis
        for mkt_id in sorted(all_markets):
            mkt_fills = fills_by_market.get(mkt_id, [])
            mkt_pos = pos_by_market.get(mkt_id)
            mkt_spreads = spreads_by_market.get(mkt_id, [])

            summary = self._analyse_market(
                mkt_id, mkt_fills, mkt_pos, mkt_spreads, trading_hours
            )
            report.market_summaries.append(summary)

            # Aggregate
            report.total_fills += summary.total_fills
            report.total_volume += summary.total_volume
            report.total_fees += summary.total_fees
            report.realized_pnl += summary.realized_pnl
            report.unrealized_pnl += summary.unrealized_pnl

        report.total_pnl = report.realized_pnl + report.unrealized_pnl

        # Global anomaly checks
        if report.max_drawdown > _ZERO:
            if report.total_volume > _ZERO:
                drawdown_pct = report.max_drawdown / report.total_volume
                if drawdown_pct > self._drawdown_pct:
                    report.anomalies.append(
                        f"High drawdown: {report.max_drawdown} "
                        f"({drawdown_pct:.2%} of volume)"
                    )

        if report.total_fills == 0 and len(all_markets) > 0:
            report.anomalies.append("Zero fills across all active markets")

        # Collect per-market anomalies at global level too
        for ms in report.market_summaries:
            for a in ms.anomalies:
                report.anomalies.append(f"[{ms.market_id}] {a}")

        logger.info(
            "post_mortem.report_generated",
            date=report_date.isoformat(),
            total_pnl=str(report.total_pnl),
            total_fills=report.total_fills,
            anomalies=len(report.anomalies),
        )

        return report

    # ── Internal helpers ─────────────────────────────────────────

    def _analyse_market(
        self,
        market_id: str,
        fills: list[FillRecord],
        position: PositionSnapshot | None,
        spreads: list[SpreadSnapshot],
        trading_hours: float,
    ) -> MarketSummary:
        """Analyse a single market and return its summary."""
        summary = MarketSummary(market_id=market_id)

        # Fill metrics
        summary.total_fills = len(fills)
        summary.buy_fills = sum(1 for f in fills if f.side == "BUY")
        summary.sell_fills = sum(1 for f in fills if f.side == "SELL")
        summary.total_volume = sum((f.price * f.size for f in fills), _ZERO)
        summary.total_fees = sum((f.fee for f in fills), _ZERO)

        if fills:
            total_size = sum((f.size for f in fills), _ZERO)
            if total_size > _ZERO:
                summary.avg_fill_price = summary.total_volume / total_size

        # Fill rate
        if trading_hours > 0:
            summary.fill_rate = summary.total_fills / trading_hours

        # Position metrics
        if position is not None:
            summary.realized_pnl = position.realized_pnl
            summary.unrealized_pnl = position.unrealized_pnl
            summary.net_inventory = position.qty_yes - position.qty_no

        # Spread metrics
        if spreads:
            spread_vals = [float(s.spread_bps) for s in spreads]
            summary.avg_spread_bps = Decimal(str(round(mean(spread_vals), 2)))
            summary.min_spread_bps = min(s.spread_bps for s in spreads)
            summary.max_spread_bps = max(s.spread_bps for s in spreads)

        # Anomaly detection at market level
        if summary.fill_rate < self._low_fill_rate and summary.total_fills > 0:
            summary.anomalies.append(
                f"Low fill rate: {summary.fill_rate:.2f} fills/hr"
            )

        if spreads:
            if summary.min_spread_bps < self._spread_compression:
                summary.anomalies.append(
                    f"Spread compression: min={summary.min_spread_bps} bps"
                )

        if position is not None:
            abs_inventory = abs(summary.net_inventory)
            if abs_inventory > self._inventory_imbalance:
                summary.anomalies.append(
                    f"Inventory imbalance: net={summary.net_inventory}"
                )

        return summary

    @staticmethod
    def _compute_max_drawdown(fills: Sequence[FillRecord]) -> Decimal:
        """Compute max drawdown from a sequence of fills.

        Tracks cumulative PnL and finds the largest peak-to-trough decline.
        Buys subtract from PnL; sells add.
        """
        if not fills:
            return _ZERO

        # Sort by timestamp
        sorted_fills = sorted(fills, key=lambda f: f.timestamp)

        cumulative_pnl = _ZERO
        peak = _ZERO
        max_dd = _ZERO

        for f in sorted_fills:
            notional = f.price * f.size
            if f.side == "SELL":
                cumulative_pnl += notional - f.fee
            else:  # BUY
                cumulative_pnl -= notional + f.fee

            if cumulative_pnl > peak:
                peak = cumulative_pnl

            dd = peak - cumulative_pnl
            if dd > max_dd:
                max_dd = dd

        return max_dd
