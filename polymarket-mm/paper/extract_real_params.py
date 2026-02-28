"""extract_real_params — Extracts real trading parameters from production JSONL.

Reads trades_production.jsonl and computes real-world parameters to
calibrate the PaperVenue simulation for more realistic paper trading.

Usage:
    python3 -m paper.extract_real_params
    python3 -m paper.extract_real_params --input paper/data/trades_production.jsonl
    python3 -m paper.extract_real_params --output paper/data/real_params.json

Also importable:
    from paper.extract_real_params import extract_params
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_trades(path: Path) -> list[dict]:
    """Load trades from JSONL file."""
    if not path.exists():
        return []

    trades = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return trades


def extract_params(
    trades: list[dict],
    all_events: list[dict] | None = None,
) -> dict[str, Any]:
    """Extract real trading parameters from production trade records.

    Parameters
    ----------
    trades : list[dict]
        Parsed JSONL trade records (from trades_production.jsonl).
    all_events : list[dict] | None
        If provided, includes both fills and rejections for rejection rate.

    Returns
    -------
    dict
        Extracted parameters including fill rate, adverse selection,
        latency, fees, and suggested PaperVenue config.
    """
    now = datetime.now(timezone.utc).isoformat()

    if not trades:
        return {
            "extracted_at": now,
            "sample_size": 0,
            "error": "No trades found in input data",
            "real_fill_rate": 0.0,
            "real_adverse_selection_bps": 0.0,
            "real_fee_bps": 0.0,
            "real_latency_ms": 0.0,
            "real_rejection_rate": 0.0,
            "real_spread_bps": 0.0,
            "real_gas_cost_per_tx_usd": 0.0,
            "real_max_price_jump_pct": 0.0,
            "suggested_paper_config": {
                "fill_probability": 0.1,
                "adverse_selection_bps": 30,
                "maker_fee_bps": 30,
                "fill_distance_decay": True,
            },
        }

    # Separate fills from rejections
    fills = [t for t in trades if not t.get("rejection_reason")]
    rejections = [t for t in trades if t.get("rejection_reason")]

    # Use all_events if provided for total order count, otherwise estimate
    if all_events is not None:
        total_orders = len(all_events)
        total_rejections = len([e for e in all_events if e.get("rejection_reason")])
    else:
        total_orders = len(trades)
        total_rejections = len(rejections)

    # ── 1. Real fill rate ────────────────────────────────────────
    real_fill_rate = len(fills) / total_orders if total_orders > 0 else 0.0

    # ── 2. Real adverse selection (bps) ──────────────────────────
    # mid_after_fill vs mid_before_fill
    adverse_selections = []
    for t in fills:
        ctx = t.get("market_context", {})
        mid_before = _to_float(ctx.get("mid_price", 0))
        # If we have mid_after, use it; otherwise estimate from next trade
        mid_after = _to_float(t.get("mid_after_fill", 0))

        if mid_before > 0 and mid_after > 0:
            adv_sel = abs(mid_after - mid_before) / mid_before * 10000
            adverse_selections.append(adv_sel)
        elif mid_before > 0:
            # Estimate from fill_price vs mid
            fill_price = _to_float(t.get("fill_price", 0))
            if fill_price > 0:
                side = t.get("side", "")
                if side == "BUY":
                    # If we bought, adverse selection = mid dropped after
                    adv_est = abs(fill_price - mid_before) / mid_before * 10000
                else:
                    adv_est = abs(fill_price - mid_before) / mid_before * 10000
                adverse_selections.append(adv_est)

    # Compute adverse selection from sequential fills
    if not adverse_selections and len(fills) > 1:
        for i in range(1, len(fills)):
            ctx_prev = fills[i - 1].get("market_context", {})
            ctx_curr = fills[i].get("market_context", {})
            mid_prev = _to_float(ctx_prev.get("mid_price", 0))
            mid_curr = _to_float(ctx_curr.get("mid_price", 0))
            if mid_prev > 0 and mid_curr > 0:
                adv_sel = abs(mid_curr - mid_prev) / mid_prev * 10000
                adverse_selections.append(adv_sel)

    real_adverse_selection_bps = (
        statistics.mean(adverse_selections) if adverse_selections else 0.0
    )

    # ── 3. Real fee (bps) ────────────────────────────────────────
    fee_bps_list = []
    for t in fills:
        fee_bps = _to_float(t.get("real_fee_bps", 0))
        if fee_bps > 0:
            fee_bps_list.append(fee_bps)
        else:
            # Compute from wallet_after
            wallet = t.get("wallet_after", {})
            fee = _to_float(wallet.get("fee", 0))
            fill_price = _to_float(t.get("fill_price", 0))
            fill_qty = _to_float(t.get("fill_qty", 0))
            notional = fill_price * fill_qty
            if notional > 0 and fee > 0:
                fee_bps_list.append(fee / notional * 10000)

    real_fee_bps = statistics.mean(fee_bps_list) if fee_bps_list else 30.0

    # ── 4. Real latency (ms) ────────────────────────────────────
    latencies = []
    for t in fills:
        lat = _to_float(t.get("latency_ms", 0))
        if lat > 0:
            latencies.append(lat)

    real_latency_ms = statistics.mean(latencies) if latencies else 0.0

    # ── 5. Rejection rate ────────────────────────────────────────
    real_rejection_rate = total_rejections / total_orders if total_orders > 0 else 0.0

    # ── 6. Real spread (bps) ─────────────────────────────────────
    spreads = []
    for t in fills:
        ctx = t.get("market_context", {})
        best_bid = _to_float(ctx.get("best_bid", 0))
        best_ask = _to_float(ctx.get("best_ask", 0))
        mid = _to_float(ctx.get("mid_price", 0))

        if best_bid > 0 and best_ask > 0 and mid > 0:
            spread_bps = (best_ask - best_bid) / mid * 10000
            spreads.append(spread_bps)
        elif "spread_bps" in ctx:
            spread_val = _to_float(ctx.get("spread_bps", 0))
            if spread_val > 0:
                spreads.append(spread_val)

    real_spread_bps = statistics.mean(spreads) if spreads else 0.0

    # ── 7. Gas cost per tx (USD) ─────────────────────────────────
    gas_costs = []
    for t in trades:
        gas = _to_float(t.get("gas_cost_usd", 0))
        if gas > 0:
            gas_costs.append(gas)

    real_gas_cost_per_tx = statistics.mean(gas_costs) if gas_costs else 0.0

    # ── 8. Max price jump (%) ────────────────────────────────────
    price_jumps = []
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", ""))
    for i in range(1, len(sorted_trades)):
        ctx_prev = sorted_trades[i - 1].get("market_context", {})
        ctx_curr = sorted_trades[i].get("market_context", {})
        mid_prev = _to_float(ctx_prev.get("mid_price", 0))
        mid_curr = _to_float(ctx_curr.get("mid_price", 0))
        if mid_prev > 0 and mid_curr > 0:
            jump_pct = abs(mid_curr - mid_prev) / mid_prev * 100
            price_jumps.append(jump_pct)

    real_max_price_jump_pct = max(price_jumps) if price_jumps else 0.0

    # ── Suggested paper config ───────────────────────────────────
    suggested = {
        "fill_probability": round(max(0.01, real_fill_rate), 4),
        "adverse_selection_bps": round(real_adverse_selection_bps, 1),
        "maker_fee_bps": round(real_fee_bps, 1),
        "fill_distance_decay": True,
    }

    return {
        "extracted_at": now,
        "sample_size": len(trades),
        "fills_count": len(fills),
        "rejections_count": total_rejections,
        "real_fill_rate": round(real_fill_rate, 4),
        "real_adverse_selection_bps": round(real_adverse_selection_bps, 2),
        "real_fee_bps": round(real_fee_bps, 2),
        "real_latency_ms": round(real_latency_ms, 2),
        "real_rejection_rate": round(real_rejection_rate, 4),
        "real_spread_bps": round(real_spread_bps, 2),
        "real_gas_cost_per_tx_usd": round(real_gas_cost_per_tx, 6),
        "real_max_price_jump_pct": round(real_max_price_jump_pct, 4),
        "suggested_paper_config": suggested,
    }


def _to_float(val: Any) -> float:
    """Safely convert a value to float."""
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def main():
    parser = argparse.ArgumentParser(
        description="Extract real trading parameters from production JSONL"
    )
    parser.add_argument(
        "--input", "-i",
        type=str,
        default=str(PROJECT_ROOT / "paper" / "data" / "trades_production.jsonl"),
        help="Path to production trades JSONL file",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=str(PROJECT_ROOT / "paper" / "data" / "real_params.json"),
        help="Path to output JSON file",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    print(f"Loading trades from: {input_path}")
    trades = load_trades(input_path)
    print(f"Loaded {len(trades)} trade records")

    params = extract_params(trades)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    indent = 2 if args.pretty else None
    with open(output_path, "w") as f:
        json.dump(params, f, indent=indent, default=str)

    print(f"\nExtracted parameters saved to: {output_path}")
    print(f"\n{'='*60}")
    print(f"  Sample size:           {params['sample_size']}")
    print(f"  Fill rate:             {params['real_fill_rate']:.2%}")
    print(f"  Adverse selection:     {params['real_adverse_selection_bps']:.1f} bps")
    print(f"  Fee:                   {params['real_fee_bps']:.1f} bps")
    print(f"  Latency:               {params['real_latency_ms']:.0f} ms")
    print(f"  Rejection rate:        {params['real_rejection_rate']:.2%}")
    print(f"  Spread:                {params['real_spread_bps']:.0f} bps")
    print(f"  Gas cost/tx:           ${params['real_gas_cost_per_tx_usd']:.4f}")
    print(f"  Max price jump:        {params['real_max_price_jump_pct']:.2f}%")
    print(f"{'='*60}")
    print(f"\nSuggested PaperVenue config:")
    for k, v in params['suggested_paper_config'].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
