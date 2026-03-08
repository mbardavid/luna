#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/openclaw/.openclaw/workspace")
DATA_DIR = WORKSPACE / "polymarket-mm" / "paper" / "data"
DEFAULT_CHANNEL = "1476255906894446644"
DEFAULT_OPENCLAW_BIN = "openclaw"
STATE_PATH = DATA_DIR / "pmm_snapshot_state.json"
ALERT_ROUTER_STATE_PATH = DATA_DIR / "pmm_alert_router_state.json"
STACK_CAPITAL_PATH = DATA_DIR / "stack_capital_latest.json"
DEFAULT_STACK_REFRESH_SECONDS = 20 * 60


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def collect_stack_capital(max_age_seconds: int = DEFAULT_STACK_REFRESH_SECONDS) -> dict[str, Any]:
    current = read_json(STACK_CAPITAL_PATH, {})
    generated_at = int(current.get("generated_at_ts", 0) or 0)
    if current and int(time.time()) - generated_at <= max_age_seconds:
        return current
    script = WORKSPACE / "scripts" / "stack-capital-snapshot.py"
    proc = subprocess.run(["python3", str(script)], text=True, capture_output=True)
    if proc.returncode != 0:
        return current
    try:
        payload = json.loads(proc.stdout)
    except Exception:
        return current
    return payload if isinstance(payload, dict) else current


def fmt_money(value) -> str:
    try:
        return f"{float(value):.4f}"
    except Exception:
        return "0.0000"


def fmt_num(value) -> str:
    try:
        return str(int(value))
    except Exception:
        return "0"


def latest_error_line(diagnosis: dict) -> str:
    checks = diagnosis.get("health", {}).get("checks", [])
    for check in checks:
        if str(check.get("name")) != "logs":
            continue
        details = check.get("details") or {}
        recent = details.get("recent_errors") or []
        if recent:
            return str(recent[-1])[:180]
    return ""


def stack_capital_lines(stack_capital: dict[str, Any]) -> list[str]:
    if not stack_capital:
        return []
    pmm = stack_capital.get("pmm") or {}
    stack = stack_capital.get("stack") or {}
    chain_totals = stack.get("chain_totals") or {}
    lines = [
        f"capital_pmm={fmt_money(pmm.get('total_usd'))} stack_total={fmt_money(stack.get('total_usd'))} delta={fmt_money(stack_capital.get('delta_vs_pmm_usd'))}"
    ]
    ordered = []
    for key in ["solana", "polygon", "arbitrum", "base", "hyperliquid"]:
        if key in chain_totals:
            ordered.append(f"{key}={fmt_money(chain_totals.get(key))}")
    if ordered:
        lines.append("stack_by_chain=" + " ".join(ordered))
    return lines


def render_snapshot(runtime: dict, live_state: dict, latest: dict, diagnosis: dict, alert_state: dict, stack_capital: dict[str, Any]) -> str:
    if live_state.get("stale") and str(runtime.get("status") or "").lower() != "running":
        live_state = {}
    totals = live_state.get("totals") or {}
    wallet = live_state.get("wallet") or {}
    diagnosis_wallet = ((((diagnosis.get("analysis") or {}).get("post_trade_diagnosis") or {}).get("wallet_state")) or {})
    pnl = live_state.get("pnl") or {}
    markets = live_state.get("markets") or {}
    first_market = next(iter(markets.values()), {})
    latest_market = next(iter(latest.get("markets") or []), {})
    incidents = alert_state.get("open_incidents") or {}
    first_incident = next(iter(incidents.values()), {})
    health = diagnosis.get("health") or {}
    health_status = str(health.get("status", "unknown")).upper()
    runtime_status = str(runtime.get("status", "unknown")).upper()
    market_label = (
        first_market.get("description")
        or latest_market.get("description")
        or latest_market.get("market_id")
        or "n/a"
    )
    if len(market_label) > 90:
        market_label = market_label[:87] + "..."
    err = latest_error_line(diagnosis)
    wallet_free = wallet.get("available_balance")
    equity = wallet.get("total_equity")
    if wallet_free in (None, 0, 0.0, "0", "0.0", "0.0000"):
        wallet_free = diagnosis_wallet.get("free_collateral_usdc")
    if equity in (None, 0, 0.0, "0", "0.0", "0.0000"):
        equity = diagnosis_wallet.get("total_wallet_equity_usdc")
    lines = [
        "PMM Snapshot",
        f"status: {runtime_status} | health: {health_status}",
        f"run_id: {runtime.get('run_id', live_state.get('run_id', 'n/a'))}",
        f"decision: {latest.get('decision_id', 'n/a')}",
        f"market: {market_label}",
        (
            f"quotes={fmt_num(totals.get('quotes_generated'))} "
            f"orders={fmt_num(totals.get('orders_submitted'))} "
            f"fills={fmt_num(totals.get('fills'))} "
            f"fill_rate={totals.get('fill_rate_pct', 0)}%"
        ),
        (
            f"pnl_realized={fmt_money(pnl.get('realized'))} "
            f"pnl_unrealized={fmt_money(pnl.get('unrealized'))} "
            f"pnl_total={fmt_money(pnl.get('cumulative'))}"
        ),
        (
            f"wallet_free={fmt_money(wallet_free)} "
            f"equity={fmt_money(equity)}"
        ),
    ]
    lines.extend(stack_capital_lines(stack_capital))
    if first_market:
        lines.append(
            f"position_net={fmt_money(first_market.get('position_net'))} spread_bps={first_market.get('spread_bps', 'n/a')}"
        )
    lines.append(f"incident_open={'true' if incidents else 'false'}")
    if first_incident:
        lines.append(
            f"incident_owner={first_incident.get('owner', 'n/a')} incident_code={first_incident.get('code', 'n/a')}"
        )
    if err:
        lines.append(f"last_error: {err}")
    return "\n".join(lines)


def send_message(openclaw_bin: str, channel: str, message: str, dry_run: bool) -> int:
    if dry_run:
        print(message)
        return 0
    proc = subprocess.run(
        [
            openclaw_bin,
            "message",
            "send",
            "--channel",
            "discord",
            "--target",
            channel,
            "--message",
            message,
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--openclaw-bin", default=DEFAULT_OPENCLAW_BIN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    runtime = read_json(DATA_DIR / "pmm_runtime_state.json", {})
    live_state = read_json(DATA_DIR / "live_state_production.json", {})
    latest = read_json(DATA_DIR / "decision_envelope_latest.json", {})
    diagnosis = read_json(DATA_DIR / "quant_diagnosis_latest.json", {})
    alert_state = read_json(ALERT_ROUTER_STATE_PATH, {})
    stack_capital = collect_stack_capital()

    message = render_snapshot(runtime, live_state, latest, diagnosis, alert_state, stack_capital)
    new_hash = str(hash(message))
    state = read_json(STATE_PATH, {})
    previous_hash = state.get("last_hash")

    if previous_hash == new_hash and not args.force:
        result = {
            "ok": True,
            "sent": False,
            "reason": "unchanged",
            "channel": args.channel,
        }
        print(json.dumps(result, indent=2))
        return 0

    rc = send_message(args.openclaw_bin, args.channel, message, args.dry_run)
    if rc == 0:
        write_json(
            STATE_PATH,
            {
                "last_hash": new_hash,
                "last_sent_at": int(time.time()),
                "last_channel": args.channel,
            },
        )

    result = {
        "ok": rc == 0,
        "sent": rc == 0,
        "channel": args.channel,
        "dry_run": args.dry_run,
        "message": message,
    }
    print(json.dumps(result, indent=2))
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
