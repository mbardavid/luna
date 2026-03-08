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
STATE_PATH = DATA_DIR / "rewards_research_report_state.json"
LATEST_PATH = DATA_DIR / "rewards_research_latest.json"
WINDOW_PATH = DATA_DIR / "rewards_research_window_latest.json"
STACK_CAPITAL_PATH = DATA_DIR / "stack_capital_latest.json"
DEFAULT_STACK_REFRESH_SECONDS = 20 * 60


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


def capital_lines(stack_capital: dict[str, Any]) -> list[str]:
    if not stack_capital:
        return []
    pmm = stack_capital.get("pmm") or {}
    stack = stack_capital.get("stack") or {}
    chain_totals = stack.get("chain_totals") or {}
    lines = [
        f"capital_pmm={float(pmm.get('total_usd', 0) or 0):.2f} stack_total={float(stack.get('total_usd', 0) or 0):.2f} delta={float(stack_capital.get('delta_vs_pmm_usd', 0) or 0):.2f}"
    ]
    ordered = []
    for key in ["solana", "polygon", "arbitrum", "base", "hyperliquid"]:
        if key in chain_totals:
            ordered.append(f"{key}={float(chain_totals.get(key) or 0):.2f}")
    if ordered:
        lines.append("stack_by_chain=" + " ".join(ordered))
    return lines


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


def top_market_line(latest: dict[str, Any]) -> str:
    top = (latest.get("top_positive_ev_markets") or [])[:1]
    if not top:
        return "top_market: none"
    market = top[0]
    question = str(market.get("question") or market.get("market_id") or "n/a")
    if len(question) > 90:
        question = question[:87] + "..."
    return (
        f"top_market: {question} | "
        f"net_ev_bps_day={market.get('net_reward_ev_bps_day', 'n/a')} | "
        f"reward_day_usd={market.get('expected_reward_day_usdc', 'n/a')} | "
        f"midpoint={market.get('midpoint', 'n/a')}"
    )


def render_message(latest: dict[str, Any], window: dict[str, Any], stack_capital: dict[str, Any], *, reason: str) -> str:
    blockers = latest.get("blockers") or []
    gates = latest.get("transport_live_gates") or {}
    lines = [
        "Quant Rewards Research",
        f"reason: {reason}",
        f"run_id: {latest.get('run_id', 'n/a')}",
        f"decision: {latest.get('decision_id', 'n/a')}",
        f"trading_state: {latest.get('trading_state', 'n/a')}",
        f"decision_reason: {latest.get('decision_reason', 'n/a')}",
        (
            f"markets_considered={latest.get('markets_considered', 0)} "
            f"positive_ev={latest.get('markets_with_positive_ev', 0)} "
            f"live_eligible={latest.get('enabled_rewards_markets', 0)}"
        ),
        (
            f"public_quote_direct_ok={gates.get('public_quote_direct_ok')} "
            f"private_post_proxy_ok={gates.get('private_post_proxy_ok')} "
            f"rewards_live_ok={gates.get('rewards_live_ok')}"
        ),
        f"blockers: {', '.join(blockers) if blockers else 'none'}",
        (
            f"window_cycles={window.get('decision_cycles', 0)} "
            f"window_positive_ev={window.get('cycles_with_positive_ev_market', 0)} "
            f"window_live_eligible={window.get('cycles_with_live_eligible_market', 0)}"
        ),
        f"window_recommendation: {window.get('recommendation', 'n/a')}",
        top_market_line(latest),
    ]
    lines.extend(capital_lines(stack_capital))
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


def material_signature(latest: dict[str, Any], window: dict[str, Any]) -> str:
    payload = {
        "decision_reason": latest.get("decision_reason"),
        "trading_state": latest.get("trading_state"),
        "blockers": latest.get("blockers") or [],
        "markets_with_positive_ev": latest.get("markets_with_positive_ev"),
        "enabled_rewards_markets": latest.get("enabled_rewards_markets"),
        "recommendation": window.get("recommendation"),
        "cycles_with_positive_ev_market": window.get("cycles_with_positive_ev_market"),
        "cycles_with_live_eligible_market": window.get("cycles_with_live_eligible_market"),
        "top_positive_ev_markets": (latest.get("top_positive_ev_markets") or [])[:3],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def should_send(latest: dict[str, Any], window: dict[str, Any], state: dict[str, Any], *, force: bool) -> tuple[bool, str]:
    if force:
        return True, "forced"

    now = int(time.time())
    last_sent_at = int(state.get("last_sent_at", 0) or 0)
    last_signature = str(state.get("last_signature", "") or "")
    signature = material_signature(latest, window)

    if latest.get("enabled_rewards_markets", 0):
        if state.get("last_reason") != "live_candidate_detected" or last_signature != signature:
            return True, "live_candidate_detected"

    if last_signature != signature:
        return True, "material_change"

    # Periodic heartbeat while research remains in standby.
    if now - last_sent_at >= 6 * 3600:
        return True, "periodic_heartbeat"

    return False, "unchanged"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--openclaw-bin", default=DEFAULT_OPENCLAW_BIN)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    latest = read_json(LATEST_PATH, {})
    window = read_json(WINDOW_PATH, {})
    if not latest:
        print(json.dumps({"ok": False, "error": "latest_missing"}, indent=2))
        return 1

    state = read_json(STATE_PATH, {})
    send, reason = should_send(latest, window, state, force=args.force)
    stack_capital = collect_stack_capital()
    message = render_message(latest, window, stack_capital, reason=reason)

    if not send:
        print(json.dumps({"ok": True, "sent": False, "reason": reason}, indent=2))
        return 0

    rc = send_message(args.openclaw_bin, args.channel, message, args.dry_run)
    if rc == 0:
        write_json(
            STATE_PATH,
            {
                "last_sent_at": int(time.time()),
                "last_signature": material_signature(latest, window),
                "last_reason": reason,
                "last_decision_id": latest.get("decision_id"),
                "last_channel": args.channel,
            },
        )

    print(
        json.dumps(
            {
                "ok": rc == 0,
                "sent": rc == 0,
                "reason": reason,
                "channel": args.channel,
                "message": message,
            },
            indent=2,
        )
    )
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
