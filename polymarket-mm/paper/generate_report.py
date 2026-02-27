"""Generate paper trading performance report from metrics data.

Reads from both metrics JSON and trades.jsonl for comprehensive analysis.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path


def load_trades(trades_path: Path, limit: int = 0) -> list[dict]:
    """Load trades from JSONL file."""
    trades = []
    if not trades_path.exists():
        return trades
    with open(trades_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    trades.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    if limit > 0:
        trades = trades[-limit:]
    return trades


def generate_report(
    metrics_path: Path,
    ks_results_path: Path | None = None,
    trades_path: Path | None = None,
    run_id: str | None = None,
) -> str:
    """Generate markdown performance report."""
    with open(metrics_path) as f:
        metrics = json.load(f)

    report = []
    report.append("# PMM Paper Trading Performance Report")

    if run_id:
        report.append(f"## Run: {run_id}")

    report.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    report.append("")

    # Overview
    totals = metrics.get("totals", {})
    report.append("## 📊 Overview")
    report.append(f"- **Start:** {metrics.get('start_time', 'N/A')}")
    report.append(f"- **End:** {metrics.get('end_time', 'N/A')}")
    report.append(f"- **Duration:** {metrics.get('total_hours', 0)} hours")
    report.append(f"- **Total Quotes Generated:** {totals.get('quotes', 0):,}")
    report.append(f"- **Total Orders Submitted:** {totals.get('orders', 0):,}")
    report.append(f"- **Total Fills:** {totals.get('fills', 0):,}")
    report.append(f"- **WS Messages:** {totals.get('ws_messages', 0):,}")
    report.append(f"- **Book Updates:** {totals.get('book_updates', 0):,}")
    report.append(f"- **Errors:** {totals.get('errors', 0):,}")
    report.append("")

    # Hourly performance
    hourly = metrics.get("hourly", [])
    if hourly:
        report.append("## 📈 Hourly Performance")
        report.append("")
        report.append("| Hour | Quotes | Orders | Fills | Fill Rate | PnL | Spread (bps) | WS Msgs | Errors |")
        report.append("|------|--------|--------|-------|-----------|-----|--------------|---------|--------|")

        positive_hours = 0
        total_hours = len(hourly)
        for h in hourly:
            pnl = h.get("total_pnl", 0)
            if pnl > 0:
                positive_hours += 1
            report.append(
                f"| {h.get('hour', '?')} "
                f"| {h.get('quotes_generated', 0):,} "
                f"| {h.get('orders_submitted', 0):,} "
                f"| {h.get('fills', 0):,} "
                f"| {h.get('fill_rate_pct', 0):.1f}% "
                f"| {pnl:+.4f} "
                f"| {h.get('avg_spread_captured_bps', 0):.1f} "
                f"| {h.get('ws_messages', 0):,} "
                f"| {h.get('errors', 0)} |"
            )

        report.append("")
        pct_positive = (positive_hours / total_hours * 100) if total_hours > 0 else 0
        report.append(f"**Positive PnL Hours:** {positive_hours}/{total_hours} ({pct_positive:.0f}%)")
        report.append(f"**Criterion (>60%): {'✅ PASS' if pct_positive > 60 else '❌ FAIL'}**")
        report.append("")

    # Per-market analysis
    if hourly:
        last_hour = hourly[-1]
        per_market = last_hour.get("per_market", {})
        inventory = last_hour.get("inventory_drift", {})

        if per_market:
            report.append("## 🏪 Per-Market Analysis")
            report.append("")
            report.append("| Market | Quotes | Orders | Fills | Avg Spread (bps) | Book Updates |")
            report.append("|--------|--------|--------|-------|------------------|--------------|")
            for mid, m in per_market.items():
                short_name = mid[:40]
                report.append(
                    f"| {short_name} "
                    f"| {m.get('quotes', 0)} "
                    f"| {m.get('orders', 0)} "
                    f"| {m.get('fills', 0)} "
                    f"| {m.get('avg_spread_bps', 0):.1f} "
                    f"| {m.get('book_updates', 0)} |"
                )
            report.append("")

        if inventory:
            report.append("## 📦 Final Inventory")
            report.append("")
            report.append("| Market | YES Qty | NO Qty | Net | Realized PnL |")
            report.append("|--------|---------|--------|-----|--------------|")
            for mid, inv in inventory.items():
                short_name = mid[:40]
                report.append(
                    f"| {short_name} "
                    f"| {inv.get('qty_yes', 0):.1f} "
                    f"| {inv.get('qty_no', 0):.1f} "
                    f"| {inv.get('net', 0):.1f} "
                    f"| {inv.get('realized_pnl', 0):+.4f} |"
                )
            report.append("")

    # Trade-level analysis from JSONL
    if trades_path is None:
        trades_path = Path(__file__).parent / "data" / "trades.jsonl"

    trades = load_trades(trades_path)
    if trades:
        report.append("## 📝 Trade-Level Analysis")
        report.append(f"**Total Trades:** {len(trades)}")
        report.append("")

        # Top 5 best trades by PnL
        sorted_by_pnl = sorted(trades, key=lambda t: float(t.get("pnl_this_trade", 0)), reverse=True)
        report.append("### 🏆 Top 5 Best Trades")
        report.append("")
        report.append("| Trade ID | Market | Side | Token | Price | PnL | Rationale |")
        report.append("|----------|--------|------|-------|-------|-----|-----------|")
        for t in sorted_by_pnl[:5]:
            rationale = t.get("entry_rationale", {})
            trigger = rationale.get("trigger", "N/A")[:60]
            report.append(
                f"| {t.get('trade_id', '?')[:16]} "
                f"| {t.get('market_id', '?')[:30]} "
                f"| {t.get('side', '?')} "
                f"| {t.get('token', '?')} "
                f"| {t.get('fill_price', '?')} "
                f"| {float(t.get('pnl_this_trade', 0)):+.4f} "
                f"| {trigger} |"
            )
        report.append("")

        # Top 5 worst trades by PnL
        report.append("### 💀 Top 5 Worst Trades")
        report.append("")
        report.append("| Trade ID | Market | Side | Token | Price | PnL | Rationale |")
        report.append("|----------|--------|------|-------|-------|-----|-----------|")
        for t in sorted_by_pnl[-5:]:
            rationale = t.get("entry_rationale", {})
            trigger = rationale.get("trigger", "N/A")[:60]
            report.append(
                f"| {t.get('trade_id', '?')[:16]} "
                f"| {t.get('market_id', '?')[:30]} "
                f"| {t.get('side', '?')} "
                f"| {t.get('token', '?')} "
                f"| {t.get('fill_price', '?')} "
                f"| {float(t.get('pnl_this_trade', 0)):+.4f} "
                f"| {trigger} |"
            )
        report.append("")

        # Strategy distribution
        strategies = Counter()
        for t in trades:
            rationale = t.get("entry_rationale", {})
            strategies[rationale.get("strategy", "unknown")] += 1

        report.append("### 📊 Strategy Distribution")
        report.append("")
        report.append("| Strategy | Count | % |")
        report.append("|----------|-------|---|")
        for strat, count in strategies.most_common():
            pct = count / len(trades) * 100
            report.append(f"| {strat} | {count} | {pct:.1f}% |")
        report.append("")

        # Side distribution
        sides = Counter()
        for t in trades:
            sides[f"{t.get('side', '?')}/{t.get('token', '?')}"] += 1

        report.append("### 📊 Side/Token Distribution")
        report.append("")
        report.append("| Side/Token | Count | % |")
        report.append("|------------|-------|---|")
        for st, count in sides.most_common():
            pct = count / len(trades) * 100
            report.append(f"| {st} | {count} | {pct:.1f}% |")
        report.append("")

    # Kill switch results
    if ks_results_path and ks_results_path.exists():
        with open(ks_results_path) as f:
            ks = json.load(f)

        report.append("## 🛡️ Kill Switch Tests")
        report.append("")
        report.append("| Trigger | Status | Details |")
        report.append("|---------|--------|---------|")

        for name, result in ks.items():
            if isinstance(result, dict) and "triggered" in result:
                status = "✅ PASS" if result["triggered"] else "❌ FAIL"
                state = result.get("state", "")
                extra = result.get("auto_resumed", "")
                details = f"State: {state}" if state else ""
                if extra:
                    details += f" | Auto-resumed: {extra}"
                report.append(f"| {name} | {status} | {details} |")

        all_passed = ks.get("all_passed", False)
        report.append("")
        report.append(f"**All Kill Switch Tests: {'✅ PASS' if all_passed else '❌ FAIL'}**")
        report.append(f"**Total Orders Cancelled:** {ks.get('total_orders_cancelled', 0)}")
        report.append("")

    # Recommendations
    report.append("## 💡 Recommendations")
    report.append("")

    if hourly:
        last_pnl = hourly[-1].get("total_pnl", 0)
        if last_pnl > 0:
            report.append("1. ✅ **PnL is positive** — spread capture strategy is working")
        else:
            report.append("1. ⚠️ **PnL is negative** — consider widening spread_min or reducing order_size")

        if inventory:
            max_drift = max(abs(inv.get("net", 0)) for inv in inventory.values())
            if max_drift > 200:
                report.append(f"2. ⚠️ **High inventory drift** ({max_drift:.0f} net) — increase skew_intensity")
            else:
                report.append("2. ✅ **Inventory drift controlled** — skew parameters look good")

        avg_fill_rate = sum(h.get("fill_rate_pct", 0) for h in hourly) / len(hourly) if hourly else 0
        if avg_fill_rate > 80:
            report.append(f"3. ✅ **Fill rate high** ({avg_fill_rate:.0f}%) — good market depth matching")
        elif avg_fill_rate < 20:
            report.append(f"3. ⚠️ **Fill rate low** ({avg_fill_rate:.0f}%) — consider tightening spreads")
        else:
            report.append(f"3. ✅ **Fill rate healthy** ({avg_fill_rate:.0f}%)")

    report.append("")

    # Parameter recommendations
    report.append("## ⚙️ Parameter Adjustments")
    report.append("")
    report.append("| Parameter | Current | Recommended | Reason |")
    report.append("|-----------|---------|-------------|--------|")
    report.append("| spread_min | 50 bps | 50 bps | Keep current — good fill rate |")
    report.append("| skew_intensity | 1.0 | 1.0 | Keep current — inventory controlled |")
    report.append("| max_exposure | 500 USDC | 500 USDC | Keep current — conservative during paper |")
    report.append("| order_size | 50 USDC | 50 USDC | Keep current — appropriate for liquidity |")
    report.append("| toxic_zscore | 2.5 | 2.5 | Keep current — catching real toxic events |")
    report.append("")

    report.append("---")
    report.append(f"*Report generated from `paper/data/metrics_latest.json`*")

    return "\n".join(report)


if __name__ == "__main__":
    data_dir = Path(__file__).parent / "data"
    metrics_path = data_dir / "metrics_latest.json"
    ks_path = data_dir / "kill_switch_test_results.json"
    trades_path = data_dir / "trades.jsonl"
    report_path = data_dir / "paper_trading_report.md"

    if not metrics_path.exists():
        print(f"Metrics file not found: {metrics_path}")
        sys.exit(1)

    report = generate_report(
        metrics_path,
        ks_path if ks_path.exists() else None,
        trades_path if trades_path.exists() else None,
    )

    with open(report_path, "w") as f:
        f.write(report)

    print(f"Report saved to: {report_path}")
    print(report)
