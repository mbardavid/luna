#!/usr/bin/env python3
"""V1 control plane for Quant + PMM.

This module provides the deterministic layer of the Quant/PMM loop:
- Quant cycle trigger + material diff
- Post-trade diagnosis snapshots
- Agent wake-up wrapper via `gateway call agent`
- DecisionEnvelope promotion guardrails
- PMM supervisor with controlled restart + rollback
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - available on server
    yaml = None


WORKSPACE = Path(os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace"))
QUANT_WORKSPACE = Path(os.environ.get("QUANT_WORKSPACE", "/home/openclaw/.openclaw/workspace-quant-strategist"))
PMM_ROOT = Path(os.environ.get("PMM_ROOT", str(WORKSPACE / "polymarket-mm")))
DATA_DIR = PMM_ROOT / "paper" / "data"
LOG_DIR = WORKSPACE / "logs"
CONFIG_DIR = WORKSPACE / "config"
SCRIPTS_DIR = WORKSPACE / "scripts"

DEFAULT_LIVE_CONFIG = Path(os.environ.get("PMM_LIVE_CONFIG", str(PMM_ROOT / "paper" / "runs" / "prod-004.yaml")))

DECISION_INPUTS_PATH = DATA_DIR / "decision_inputs_latest.json"
DECISION_CANDIDATE_PATH = DATA_DIR / "decision_envelope_candidate.json"
DECISION_LATEST_PATH = DATA_DIR / "decision_envelope_latest.json"
DECISION_APPLIED_PATH = DATA_DIR / "decision_envelope_applied.json"
MATERIAL_CHANGE_PATH = DATA_DIR / "material_change_report.json"
QUANT_DIAGNOSIS_PATH = DATA_DIR / "quant_diagnosis_latest.json"
QUANT_CYCLE_STATE_PATH = DATA_DIR / "quant_cycle_state.json"
PMM_RUNTIME_STATE_PATH = DATA_DIR / "pmm_runtime_state.json"
LIVE_STATE_PATH = DATA_DIR / "live_state_production.json"
PID_FILE = DATA_DIR / "production_trading.pid"
TRANSPORT_SUMMARY_PATH = DATA_DIR / "transport_latency_latest.json"

QUANT_DECISION_ENGINE = QUANT_WORKSPACE / "scripts" / "decision_engine.py"
QUANT_PERF_ANALYZER = QUANT_WORKSPACE / "scripts" / "performance-analyzer.py"
QUANT_HEALTH_MONITOR = QUANT_WORKSPACE / "scripts" / "health-monitor.py"
LIVE_RUNNER_WRAPPER = SCRIPTS_DIR / "pmm-live-runner.sh"
MC_CLIENT = SCRIPTS_DIR / "mc-client.sh"
MC_AGENT_IDS = CONFIG_DIR / "mc-agent-ids.json"
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")

LOCK_ROOT = Path("/tmp")
QUANT_CYCLE_LOCK = LOCK_ROOT / ".quant-cycle.lock"
QUANT_DIAGNOSE_LOCK = LOCK_ROOT / ".quant-diagnose.lock"
QUANT_PROMOTE_LOCK = LOCK_ROOT / ".quant-promote.lock"
PMM_SUPERVISOR_LOCK = LOCK_ROOT / ".pmm-supervisor.lock"

HOUR_SECONDS = 3600
CRITICAL_WAKE_REASONS = {
    "reject_rate_above_threshold",
    "cancel_unknown_order_detected",
    "balance_allowance_mismatch",
    "reward_adjusted_pnl_negative",
    "runner_unhealthy",
    "runner_down",
    "restart_failed",
    "direct_latency_gate_failed",
    "proxy_latency_gate_failed",
}


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_decision_envelope_class():
    sys.path.insert(0, str(PMM_ROOT))
    from runner.decision_envelope import DecisionEnvelope  # type: ignore

    return DecisionEnvelope


def load_perf_module():
    return _load_module(QUANT_PERF_ANALYZER, "quant_performance_analyzer_runtime")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path, default: Any | None = None) -> Any:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return copy.deepcopy(default)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def file_mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None


def file_age_seconds(path: Path) -> float | None:
    mtime = file_mtime(path)
    if mtime is None:
        return None
    return max(0.0, time.time() - mtime)


def read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except Exception:
        return None


def is_pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def remove_stale_pid_file() -> None:
    pid = read_pid(PID_FILE)
    if pid and not is_pid_alive(pid):
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass


def hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )
    return proc


def load_run_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text()
    if yaml is not None:
        try:
            return yaml.safe_load(raw) or {}
        except Exception:
            return {}
    return {}


def current_live_config(path_arg: str | None = None) -> Path:
    raw = path_arg or os.environ.get("PMM_LIVE_CONFIG") or str(DEFAULT_LIVE_CONFIG)
    path = Path(raw)
    if not path.is_absolute():
        path = PMM_ROOT / path
    return path


def resolve_current_run_id(config_path: Path, runtime_state: dict[str, Any] | None = None) -> str:
    runtime_state = runtime_state or read_json(PMM_RUNTIME_STATE_PATH, {})
    if runtime_state.get("run_id"):
        return str(runtime_state["run_id"])
    live_state = read_json(LIVE_STATE_PATH, {})
    if live_state.get("run_id"):
        return str(live_state["run_id"])
    config = load_run_config(config_path)
    if config.get("run_id"):
        return str(config["run_id"])
    return "prod-004"


def load_envelope(path: Path):
    DecisionEnvelope = load_decision_envelope_class()
    return DecisionEnvelope.load(path)


def load_envelope_safe(path: Path):
    if not path.exists():
        return None, f"missing:{path}"
    try:
        envelope = load_envelope(path)
        return envelope, None
    except Exception as exc:
        return None, str(exc)


def market_signature(market: Any) -> dict[str, Any]:
    base = {
        "mode": market.mode,
        "market_id": getattr(market, "market_id", ""),
        "condition_id": getattr(market, "condition_id", ""),
        "disable_reason": getattr(market, "disable_reason", ""),
    }
    if market.mode == "rewards_farming":
        base.update(
            {
                "order_size": str(getattr(market, "order_size", "0")),
                "half_spread_bps": int(getattr(market, "half_spread_bps", 0)),
                "max_inventory_per_side": str(getattr(market, "max_inventory_per_side", "0")),
                "min_quote_lifetime_s": float(getattr(market, "min_quote_lifetime_s", 0)),
                "max_requote_rate_per_min": float(getattr(market, "max_requote_rate_per_min", 0)),
            }
        )
    else:
        base.update(
            {
                "side": getattr(market, "side", ""),
                "entry_price_limit": str(getattr(market, "entry_price_limit", "0")),
                "stake_usdc": str(getattr(market, "stake_usdc", "0")),
                "ttl_seconds": int(getattr(market, "ttl_seconds", 0)),
            }
        )
    return base


def envelope_summary(envelope: Any | None) -> dict[str, Any]:
    if envelope is None:
        return {}
    enabled_rewards = sorted(m.market_id for m in envelope.enabled_markets("rewards_farming"))
    enabled_directional = sorted(m.market_id for m in envelope.enabled_markets("event_driven"))
    rewards_signature = sorted(
        [market_signature(m) for m in envelope.markets if getattr(m, "mode", "") == "rewards_farming"],
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    directional_signature = sorted(
        [market_signature(m) for m in envelope.markets if getattr(m, "mode", "") == "event_driven"],
        key=lambda item: json.dumps(item, sort_keys=True),
    )
    transport_summary = (envelope.metadata or {}).get("transport_probe_summary", {})
    direct_summary = transport_summary.get("direct", {})
    proxy_summary = transport_summary.get("proxy", {})
    return {
        "decision_id": envelope.decision_id,
        "generated_at": envelope.generated_at.isoformat(),
        "expires_at": envelope.expires_at.isoformat(),
        "trading_state": envelope.trading_state,
        "decision_scope": envelope.decision_scope,
        "decision_reason": envelope.decision_reason,
        "transport_policy": envelope.transport_policy,
        "selected_transport": (envelope.metadata or {}).get("selected_transport"),
        "direct_p90_ttfb_ms": direct_summary.get("p90_ttfb_ms"),
        "proxy_p90_ttfb_ms": proxy_summary.get("p90_ttfb_ms"),
        "capital_policy": envelope.capital_policy.to_dict(),
        "mode_allocations": envelope.mode_allocations.to_dict(),
        "risk_limits": envelope.risk_limits.to_dict(),
        "enabled_rewards_markets": enabled_rewards,
        "enabled_directional_markets": enabled_directional,
        "rewards_signature_hash": hash_payload(rewards_signature),
        "directional_signature_hash": hash_payload(directional_signature),
    }


def health_snapshot() -> dict[str, Any]:
    proc = run_cmd(
        ["python3", str(QUANT_HEALTH_MONITOR), "--check", "--json"],
        cwd=QUANT_WORKSPACE,
        timeout=60,
        check=False,
    )
    report: dict[str, Any] = {}
    if proc.stdout.strip():
        stdout = proc.stdout.strip()
        try:
            report = json.loads(stdout)
        except json.JSONDecodeError:
            for idx, char in enumerate(stdout):
                if char != "{":
                    continue
                try:
                    report = json.loads(stdout[idx:])
                    break
                except json.JSONDecodeError:
                    continue
            if not report:
                report = {"status": "unhealthy", "stdout": stdout}
    if not report:
        report = {"status": "unhealthy", "stderr": proc.stderr.strip()}
    report["exit_code"] = proc.returncode
    report["captured_at"] = iso_now()
    return report


def fallback_analysis(run_id: str, perf_module: Any) -> dict[str, Any]:
    live_state = read_json(LIVE_STATE_PATH, {}) or {}
    trades = perf_module.load_trade_records(run_id)
    total_orders = int((live_state.get("totals") or {}).get("orders_submitted", len(trades)))
    total_fills = int((live_state.get("totals") or {}).get("fills", len(trades)))
    total_volume = 0.0
    for trade in trades:
        fill_qty = float(trade.get("fill_qty", trade.get("size", 0)) or 0)
        fill_price = float(trade.get("fill_price", trade.get("price", 0)) or 0)
        total_volume += fill_qty * fill_price
    gross_pnl = float((live_state.get("pnl") or {}).get("realized", 0.0) or 0.0)
    total_fees = float((live_state.get("wallet") or {}).get("total_fees", 0.0) or 0.0)
    analysis = {
        "run_id": run_id,
        "status": str(live_state.get("status", "unknown")),
        "started_at": str(live_state.get("timestamp", "")),
        "ended_at": None,
        "initial_balance": float((live_state.get("wallet") or {}).get("initial_balance", 0.0) or 0.0),
        "total_orders": total_orders,
        "total_fills": total_fills,
        "fill_rate_pct": round((total_fills / total_orders * 100.0), 2) if total_orders else 0.0,
        "buy_fills": 0,
        "sell_fills": 0,
        "total_volume_usd": round(total_volume, 2),
        "total_fees_usd": round(total_fees, 6),
        "total_exits": 0,
        "gross_pnl_usd": round(gross_pnl, 6),
        "net_pnl_usd": round(gross_pnl - total_fees, 6),
        "adverse_selection_ratio": 0.0,
        "adverse_exits": 0,
        "per_market": {},
    }
    analysis["post_trade_diagnosis"] = perf_module.build_post_trade_diagnosis(run_id, analysis, dry_run=False)
    analysis["taint"] = analysis["post_trade_diagnosis"]["taint"]
    return analysis


def diagnosis_snapshot(run_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    perf_module = load_perf_module()
    try:
        analysis = perf_module.analyze_run(run_id, dry_run=dry_run)
    except Exception as exc:
        analysis = {"error": str(exc)}
    if "error" in analysis:
        analysis = fallback_analysis(run_id, perf_module)
    elif "post_trade_diagnosis" not in analysis:
        analysis["post_trade_diagnosis"] = perf_module.build_post_trade_diagnosis(run_id, analysis, dry_run=dry_run)
        analysis["taint"] = analysis["post_trade_diagnosis"]["taint"]
    return {
        "generated_at": iso_now(),
        "run_id": run_id,
        "analysis": analysis,
        "live_state_age_seconds": file_age_seconds(LIVE_STATE_PATH),
        "live_state_path": str(LIVE_STATE_PATH),
    }


def should_alert_runner_unhealthy(health_payload: dict[str, Any]) -> bool:
    if not health_payload:
        return False
    checks = {
        str(check.get("name")): check
        for check in health_payload.get("checks", [])
        if isinstance(check, dict)
    }
    process_check = checks.get("process", {})
    if process_check.get("status") == "unhealthy":
        return True

    for name, check in checks.items():
        if name == "logs":
            continue
        if check.get("status") == "unhealthy":
            return True

    logs_check = checks.get("logs", {})
    log_details = logs_check.get("details", {}) if isinstance(logs_check.get("details"), dict) else {}
    if logs_check.get("status") == "unhealthy":
        structural_errors = int(log_details.get("structural_errors", log_details.get("errors", 0)) or 0)
        return structural_errors > 0
    return False


def build_material_change_report(
    *,
    current_envelope: Any | None,
    candidate_envelope: Any,
    diagnosis_payload: dict[str, Any],
    health: dict[str, Any],
    cycle_state: dict[str, Any],
) -> dict[str, Any]:
    now = utcnow()
    current_summary = envelope_summary(current_envelope)
    candidate_summary = envelope_summary(candidate_envelope)
    reasons: list[str] = []

    if not current_envelope:
        reasons.append("no_promoted_envelope")
    else:
        expires_at = parse_dt(current_summary.get("expires_at"))
        if expires_at and expires_at - now <= timedelta(minutes=15):
            reasons.append("envelope_expiring_soon")
        if current_summary.get("trading_state") != candidate_summary.get("trading_state"):
            reasons.append("trading_state_changed")
        if current_summary.get("decision_scope") != candidate_summary.get("decision_scope"):
            reasons.append("decision_scope_changed")
        if current_summary.get("selected_transport") != candidate_summary.get("selected_transport"):
            reasons.append("selected_transport_changed")
        if current_summary.get("enabled_rewards_markets") != candidate_summary.get("enabled_rewards_markets"):
            reasons.append("rewards_market_set_changed")
        if current_summary.get("enabled_directional_markets") != candidate_summary.get("enabled_directional_markets"):
            reasons.append("directional_market_set_changed")
        if current_summary.get("rewards_signature_hash") != candidate_summary.get("rewards_signature_hash"):
            reasons.append("rewards_market_params_changed")
        if current_summary.get("directional_signature_hash") != candidate_summary.get("directional_signature_hash"):
            reasons.append("directional_market_params_changed")
        if current_summary.get("capital_policy") != candidate_summary.get("capital_policy"):
            reasons.append("capital_policy_changed")

    direct_p90 = candidate_summary.get("direct_p90_ttfb_ms")
    proxy_p90 = candidate_summary.get("proxy_p90_ttfb_ms")
    if isinstance(direct_p90, (int, float)) and direct_p90 >= 350:
        reasons.append("direct_latency_gate_failed")
    if isinstance(proxy_p90, (int, float)) and proxy_p90 >= 1300:
        reasons.append("proxy_latency_gate_failed")

    diagnosis = diagnosis_payload.get("analysis", {}).get("post_trade_diagnosis", {})
    execution_pnl = diagnosis.get("execution_pnl", {})
    reward_adjusted = diagnosis.get("reward_adjusted_pnl", {})
    taint = diagnosis.get("taint", {})
    reject_rate_pct = float(execution_pnl.get("reject_rate_pct", 0.0) or 0.0)
    reward_adjusted_pnl = float(reward_adjusted.get("reward_adjusted_pnl_usd", 0.0) or 0.0)
    taint_reasons = set(taint.get("reasons", []))
    if reject_rate_pct > 1.0:
        reasons.append("reject_rate_above_threshold")
    if reward_adjusted_pnl < 0:
        reasons.append("reward_adjusted_pnl_negative")
    if "cancel_unknown_order" in taint_reasons:
        reasons.append("cancel_unknown_order_detected")
    if {"balance_allowance_errors", "config_balance_mismatch"} & taint_reasons:
        reasons.append("balance_allowance_mismatch")

    health_status = str(health.get("status", "unknown"))
    if health_status != "healthy":
        reasons.append("runner_unhealthy")

    last_wake_at = parse_dt(cycle_state.get("last_wake_at"))
    forced_review = last_wake_at is None or (now - last_wake_at) >= timedelta(hours=1)
    if forced_review:
        reasons.append("forced_hourly_review")

    reason_hash = hash_payload(sorted(set(reasons)))
    last_reason_hash = str(cycle_state.get("last_wake_reason_hash", ""))
    suppress_duplicate_wake = False
    if reason_hash == last_reason_hash and last_wake_at and (now - last_wake_at) < timedelta(minutes=15):
        suppress_duplicate_wake = True
    critical = any(reason in CRITICAL_WAKE_REASONS for reason in reasons)
    wake_required = (bool(reasons) and (forced_review or not suppress_duplicate_wake or critical))
    if suppress_duplicate_wake and not forced_review and not critical:
        wake_required = False

    return {
        "generated_at": now.isoformat(),
        "material_change": bool(reasons),
        "forced_review": forced_review,
        "wake_required": wake_required,
        "critical": critical,
        "reasons": sorted(set(reasons)),
        "reason_hash": reason_hash,
        "current_summary": current_summary,
        "candidate_summary": candidate_summary,
        "metrics": {
            "reject_rate_pct": reject_rate_pct,
            "reward_adjusted_pnl_usd": reward_adjusted_pnl,
            "health_status": health_status,
            "direct_p90_ttfb_ms": direct_p90,
            "proxy_p90_ttfb_ms": proxy_p90,
        },
    }


def build_quant_message(report: dict[str, Any]) -> str:
    reasons = ", ".join(report.get("reasons", [])) or "forced_hourly_review"
    candidate = report.get("candidate_summary", {})
    current = report.get("current_summary", {})
    return (
        "Quant review required for PMM control plane.\n\n"
        f"Reasons: {reasons}\n"
        f"Current promoted decision: {current.get('decision_id') or 'none'}\n"
        f"Candidate decision: {candidate.get('decision_id') or 'unknown'}\n"
        f"Trading state candidate: {candidate.get('trading_state')}\n"
        f"Selected transport candidate: {candidate.get('selected_transport')}\n\n"
        f"Read: {DECISION_INPUTS_PATH}\n"
        f"Diff: {MATERIAL_CHANGE_PATH}\n"
        f"Candidate: {DECISION_CANDIDATE_PATH}\n"
        f"Latest promoted: {DECISION_LATEST_PATH}\n"
        f"Diagnosis: {QUANT_DIAGNOSIS_PATH}\n\n"
        "If the candidate is correct, promote it with:\n"
        f"{SCRIPTS_DIR / 'quant-promote-envelope.sh'}\n"
        "If not, revise the candidate first, then promote. Rewards remain first; directional stays paper-first."
    )


def build_luna_message(title: str, task_id: str | None, code: str) -> str:
    task_line = f"Mission Control task: {task_id}\n" if task_id else ""
    return (
        f"{title}\n"
        f"Incident code: {code}\n"
        f"{task_line}"
        f"Runtime: {PMM_RUNTIME_STATE_PATH}\n"
        f"Latest envelope: {DECISION_LATEST_PATH}\n"
        f"Applied envelope: {DECISION_APPLIED_PATH}\n"
        f"Diagnosis: {QUANT_DIAGNOSIS_PATH}\n"
        "This came from the PMM V1 supervisor/control plane."
    )


def wake_agent(target: str, message: str, *, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
    cwd = QUANT_WORKSPACE if target == "quant" else WORKSPACE
    params = {
        "message": message,
        "idempotencyKey": idempotency_key,
    }
    if dry_run:
        return {
            "dry_run": True,
            "target": target,
            "cwd": str(cwd),
            "params": params,
        }
    proc = run_cmd(
        [
            OPENCLAW_BIN,
            "gateway",
            "call",
            "--json",
            "--timeout",
            "20000",
            "--params",
            json.dumps(params),
            "agent",
        ],
        cwd=cwd,
        timeout=25,
        check=False,
    )
    payload: dict[str, Any] = {
        "target": target,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    if proc.stdout.strip():
        try:
            payload["response"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return payload


def load_agent_id(agent_name: str) -> str:
    raw = read_json(MC_AGENT_IDS, {})
    return str(raw.get(agent_name, "") or "")


def maybe_open_incident(
    *,
    code: str,
    title: str,
    description: str,
    cycle_state: dict[str, Any],
    dry_run: bool = False,
) -> dict[str, Any]:
    incidents = cycle_state.setdefault("open_incidents", {})
    if code in incidents:
        incidents[code]["last_seen_at"] = iso_now()
        return incidents[code]

    task_id = ""
    create_result: dict[str, Any] = {}
    assignee = load_agent_id("main")
    if dry_run:
        create_result = {"dry_run": True}
    elif MC_CLIENT.exists():
        fields = {
            "mc_origin": "pmm_v1_control_plane",
            "incident_code": code,
        }
        cmd = [
            str(MC_CLIENT),
            "create-task",
            title,
            description,
            assignee,
            "high",
            "inbox",
            json.dumps(fields),
        ]
        proc = run_cmd(cmd, cwd=WORKSPACE, timeout=30, check=False)
        create_result = {
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
        }
        if proc.stdout.strip():
            try:
                payload = json.loads(proc.stdout)
                task_id = str(payload.get("id") or payload.get("task_id") or "")
            except json.JSONDecodeError:
                task_id = ""

    wake_result = wake_agent(
        "luna",
        build_luna_message(title, task_id or None, code),
        idempotency_key=f"pmm-incident-{code}-{int(time.time())}",
        dry_run=dry_run,
    )
    incident = {
        "code": code,
        "task_id": task_id,
        "opened_at": iso_now(),
        "last_seen_at": iso_now(),
        "create_result": create_result,
        "wake_result": wake_result,
    }
    incidents[code] = incident
    return incident


def update_cycle_state(updates: dict[str, Any]) -> dict[str, Any]:
    current = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
    current.update(updates)
    write_json(QUANT_CYCLE_STATE_PATH, current)
    return current


def run_decision_engine(
    *,
    capital_usdc: Decimal,
    top_n: int,
    current_run_id: str,
    dry_run: bool,
    expires_hours: int,
    directional_live: bool,
    output_path: Path,
    directional_signals_path: Path | None,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        "python3",
        str(QUANT_DECISION_ENGINE),
        "--capital-usdc",
        str(capital_usdc),
        "--top",
        str(top_n),
        "--current-run-id",
        current_run_id,
        "--expires-hours",
        str(expires_hours),
        "--output",
        str(output_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    if directional_live:
        cmd.append("--directional-live")
    if directional_signals_path:
        cmd.extend(["--directional-signals", str(directional_signals_path)])
    return run_cmd(cmd, cwd=QUANT_WORKSPACE, timeout=120, check=False)


def discover_capital(config_path: Path, cli_value: str | None = None) -> Decimal:
    if cli_value:
        return Decimal(str(cli_value))
    env_value = os.environ.get("PMM_TOTAL_CAPITAL_USDC")
    if env_value:
        return Decimal(env_value)
    live_state = read_json(LIVE_STATE_PATH, {}) or {}
    wallet = live_state.get("wallet") or {}
    if wallet.get("initial_balance") is not None:
        return Decimal(str(wallet["initial_balance"]))
    config = load_run_config(config_path)
    if config.get("initial_balance") is not None:
        return Decimal(str(config["initial_balance"]))
    return Decimal("220")


def cmd_cycle(args: argparse.Namespace) -> int:
    with file_lock(QUANT_CYCLE_LOCK):
        config_path = current_live_config(args.config)
        runtime_state = read_json(PMM_RUNTIME_STATE_PATH, {}) or {}
        cycle_state = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
        run_id = resolve_current_run_id(config_path, runtime_state)
        capital_usdc = discover_capital(config_path, args.capital_usdc)
        health = health_snapshot()
        diagnosis = read_json(QUANT_DIAGNOSIS_PATH, {}) or {}
        if not diagnosis:
            diagnosis = diagnosis_snapshot(run_id, dry_run=args.dry_run)
            write_json(QUANT_DIAGNOSIS_PATH, diagnosis)

        engine_proc = run_decision_engine(
            capital_usdc=capital_usdc,
            top_n=args.top,
            current_run_id=run_id,
            dry_run=args.dry_run,
            expires_hours=args.expires_hours,
            directional_live=args.directional_live,
            output_path=DECISION_CANDIDATE_PATH,
            directional_signals_path=Path(args.directional_signals) if args.directional_signals else None,
        )
        if engine_proc.returncode != 0:
            error_payload = {
                "generated_at": iso_now(),
                "error": "decision_engine_failed",
                "stdout": engine_proc.stdout.strip(),
                "stderr": engine_proc.stderr.strip(),
            }
            write_json(MATERIAL_CHANGE_PATH, error_payload)
            update_cycle_state(
                {
                    "last_cycle_at": iso_now(),
                    "last_cycle_error": error_payload,
                }
            )
            if args.json:
                print(json.dumps(error_payload, indent=2))
            else:
                print("decision_engine_failed")
            return 1

        candidate_envelope, candidate_error = load_envelope_safe(DECISION_CANDIDATE_PATH)
        if candidate_error or candidate_envelope is None:
            payload = {
                "generated_at": iso_now(),
                "error": "candidate_envelope_invalid",
                "details": candidate_error,
            }
            write_json(MATERIAL_CHANGE_PATH, payload)
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print("candidate_envelope_invalid")
            return 1

        current_envelope, _ = load_envelope_safe(DECISION_LATEST_PATH)
        report = build_material_change_report(
            current_envelope=current_envelope,
            candidate_envelope=candidate_envelope,
            diagnosis_payload=diagnosis,
            health=health,
            cycle_state=cycle_state,
        )

        candidate_summary = report["candidate_summary"]
        no_rewards_since = cycle_state.get("no_rewards_since")
        if not candidate_summary.get("enabled_rewards_markets"):
            no_rewards_since = no_rewards_since or iso_now()
        else:
            no_rewards_since = None
        report["no_rewards_since"] = no_rewards_since
        if no_rewards_since:
            no_rewards_at = parse_dt(no_rewards_since)
            if no_rewards_at and utcnow() - no_rewards_at >= timedelta(hours=2):
                maybe_open_incident(
                    code="no_rewards_eligible_2h",
                    title="PMM: no rewards-eligible markets for 2h",
                    description=(
                        "The Quant control plane has kept the PMM in standby because no rewards market stayed "
                        "eligible for more than 2 hours.\n\n"
                        f"Inputs: {DECISION_INPUTS_PATH}\n"
                        f"Candidate: {DECISION_CANDIDATE_PATH}\n"
                        f"Latest promoted: {DECISION_LATEST_PATH}"
                    ),
                    cycle_state=cycle_state,
                    dry_run=args.dry_run,
                )

        decision_inputs = {
            "generated_at": iso_now(),
            "current_run_id": run_id,
            "config_path": str(config_path),
            "paths": {
                "candidate": str(DECISION_CANDIDATE_PATH),
                "latest": str(DECISION_LATEST_PATH),
                "applied": str(DECISION_APPLIED_PATH),
                "runtime_state": str(PMM_RUNTIME_STATE_PATH),
                "diagnosis": str(QUANT_DIAGNOSIS_PATH),
                "material_change": str(MATERIAL_CHANGE_PATH),
            },
            "runtime_state": runtime_state,
            "health": health,
            "diagnosis": diagnosis,
            "current_summary": report["current_summary"],
            "candidate_summary": report["candidate_summary"],
            "material_change_report": report,
        }

        wake_result = None
        if report.get("wake_required"):
            wake_result = wake_agent(
                "quant",
                build_quant_message(report),
                idempotency_key=f"quant-cycle-{report['reason_hash'][:16]}-{int(time.time())}",
                dry_run=args.dry_run,
            )

        updated_state = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
        updated_state.update(
            {
                "last_cycle_at": iso_now(),
                "last_cycle_run_id": run_id,
                "last_candidate_decision_id": candidate_envelope.decision_id,
                "last_material_change_reasons": report["reasons"],
                "last_material_change_hash": report["reason_hash"],
                "last_material_change_at": iso_now() if report["material_change"] else updated_state.get("last_material_change_at"),
                "last_health_status": health.get("status"),
                "last_candidate_trading_state": candidate_envelope.trading_state,
                "last_candidate_scope": candidate_envelope.decision_scope,
                "no_rewards_since": no_rewards_since,
            }
        )
        if wake_result:
            updated_state["last_wake_at"] = iso_now()
            updated_state["last_wake_target"] = "quant"
            updated_state["last_wake_reason_hash"] = report["reason_hash"]
            updated_state["last_wake_candidate_decision_id"] = candidate_envelope.decision_id
            updated_state["last_wake_result"] = wake_result

        write_json(MATERIAL_CHANGE_PATH, report)
        write_json(DECISION_INPUTS_PATH, decision_inputs)
        write_json(QUANT_CYCLE_STATE_PATH, updated_state)

        if args.json:
            print(json.dumps({"report": report, "wake_result": wake_result}, indent=2))
        else:
            print(f"candidate={candidate_envelope.decision_id} wake_required={report['wake_required']} reasons={','.join(report['reasons'])}")
        return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    with file_lock(QUANT_DIAGNOSE_LOCK):
        config_path = current_live_config(args.config)
        runtime_state = read_json(PMM_RUNTIME_STATE_PATH, {}) or {}
        run_id = args.run_id or resolve_current_run_id(config_path, runtime_state)
        payload = diagnosis_snapshot(run_id, dry_run=args.dry_run)
        payload["health"] = health_snapshot()
        payload["runtime_state"] = runtime_state
        write_json(QUANT_DIAGNOSIS_PATH, payload)

        cycle_state = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
        diagnosis = payload.get("analysis", {}).get("post_trade_diagnosis", {})
        reject_rate_pct = float((diagnosis.get("execution_pnl") or {}).get("reject_rate_pct", 0.0) or 0.0)
        reward_adjusted_pnl = float((diagnosis.get("reward_adjusted_pnl") or {}).get("reward_adjusted_pnl_usd", 0.0) or 0.0)
        taint_reasons = set((diagnosis.get("taint") or {}).get("reasons", []))
        alerts: list[str] = []
        if reject_rate_pct > 1.0:
            alerts.append("reject_rate_above_threshold")
        if reward_adjusted_pnl < 0:
            alerts.append("reward_adjusted_pnl_negative")
        if "cancel_unknown_order" in taint_reasons:
            alerts.append("cancel_unknown_order_detected")
        if {"balance_allowance_errors", "config_balance_mismatch"} & taint_reasons:
            alerts.append("balance_allowance_mismatch")
        if should_alert_runner_unhealthy(payload.get("health", {}) or {}):
            alerts.append("runner_unhealthy")

        wake_result = None
        if alerts:
            reason_hash = hash_payload(sorted(alerts))
            last_hash = str(cycle_state.get("last_diagnosis_wake_reason_hash", ""))
            last_at = parse_dt(cycle_state.get("last_diagnosis_wake_at"))
            if last_hash != reason_hash or not last_at or (utcnow() - last_at) >= timedelta(minutes=15):
                message = (
                    "Quant diagnosis alert for PMM.\n\n"
                    f"Run: {run_id}\n"
                    f"Alerts: {', '.join(alerts)}\n"
                    f"Diagnosis: {QUANT_DIAGNOSIS_PATH}\n"
                    f"Runtime: {PMM_RUNTIME_STATE_PATH}\n"
                    f"Latest envelope: {DECISION_LATEST_PATH}"
                )
                wake_result = wake_agent(
                    "quant",
                    message,
                    idempotency_key=f"quant-diagnose-{reason_hash[:16]}-{int(time.time())}",
                    dry_run=args.dry_run,
                )
                cycle_state["last_diagnosis_wake_at"] = iso_now()
                cycle_state["last_diagnosis_wake_reason_hash"] = reason_hash

        cycle_state["last_diagnosis_at"] = iso_now()
        cycle_state["last_diagnosis_run_id"] = run_id
        cycle_state["last_diagnosis_alerts"] = alerts
        if wake_result:
            cycle_state["last_diagnosis_wake_result"] = wake_result
        write_json(QUANT_CYCLE_STATE_PATH, cycle_state)

        if args.json:
            print(json.dumps({"diagnosis": payload, "alerts": alerts, "wake_result": wake_result}, indent=2))
        else:
            print(f"run_id={run_id} alerts={','.join(alerts) or 'none'}")
        return 0


def validate_candidate_envelope(envelope: Any) -> list[str]:
    errors: list[str] = []
    try:
        if envelope.trading_state == "active":
            envelope.require_live_ready()
    except Exception as exc:
        errors.append(str(exc))
    if envelope.mode_allocations.priority_order != ["rewards_farming", "event_driven"]:
        errors.append("priority_order_invalid")
    if envelope.risk_limits.allow_directional_live:
        if os.environ.get("PMM_DIRECTIONAL_LIVE_APPROVED") != "1":
            errors.append("directional_live_requires_governance")
        if str((envelope.metadata or {}).get("selected_transport", "")) != "direct":
            errors.append("directional_live_requires_direct_transport")
    if envelope.decision_scope == "rewards_only" and envelope.mode_allocations.directional_enabled:
        errors.append("directional_enabled_but_scope_rewards_only")
    return errors


def cmd_promote(args: argparse.Namespace) -> int:
    with file_lock(QUANT_PROMOTE_LOCK):
        cycle_state = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
        candidate_path = Path(args.candidate) if args.candidate else DECISION_CANDIDATE_PATH
        envelope, error = load_envelope_safe(candidate_path)
        if error or envelope is None:
            cycle_state["consecutive_promote_failures"] = int(cycle_state.get("consecutive_promote_failures", 0)) + 1
            cycle_state["last_promote_error"] = error or "candidate_missing"
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps({"ok": False, "error": error or "candidate_missing"}, indent=2))
            else:
                print(error or "candidate_missing")
            return 1

        errors = validate_candidate_envelope(envelope)
        if errors:
            cycle_state["consecutive_promote_failures"] = int(cycle_state.get("consecutive_promote_failures", 0)) + 1
            cycle_state["last_promote_error"] = errors
            if cycle_state["consecutive_promote_failures"] >= 3:
                maybe_open_incident(
                    code="promote_failed_3x",
                    title="PMM: candidate promotion failed 3 times",
                    description=(
                        "The Quant control plane rejected candidate promotion 3 times in a row.\n\n"
                        f"Candidate: {candidate_path}\n"
                        f"Errors: {', '.join(errors)}"
                    ),
                    cycle_state=cycle_state,
                    dry_run=args.dry_run,
                )
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps({"ok": False, "errors": errors}, indent=2))
            else:
                print(",".join(errors))
            return 1

        if not args.dry_run:
            payload = read_json(candidate_path, {})
            write_json(DECISION_LATEST_PATH, payload)
        cycle_state["consecutive_promote_failures"] = 0
        cycle_state["last_promoted_at"] = iso_now()
        cycle_state["last_promoted_decision_id"] = envelope.decision_id
        cycle_state["last_promote_error"] = None
        write_json(QUANT_CYCLE_STATE_PATH, cycle_state)

        if envelope.trading_state == "halted":
            maybe_open_incident(
                code="trading_state_halted",
                title="PMM: promoted envelope halted trading",
                description=(
                    "A promoted DecisionEnvelope explicitly halted live trading.\n\n"
                    f"Envelope: {DECISION_LATEST_PATH}\n"
                    f"Decision reason: {envelope.decision_reason or '(none)'}"
                ),
                cycle_state=cycle_state,
                dry_run=args.dry_run,
            )

        payload = {
            "ok": True,
            "decision_id": envelope.decision_id,
            "trading_state": envelope.trading_state,
            "decision_scope": envelope.decision_scope,
            "latest_path": str(DECISION_LATEST_PATH),
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"promoted={envelope.decision_id} trading_state={envelope.trading_state}")
        return 0


def stop_runner(pid: int, *, timeout_seconds: int = 45) -> dict[str, Any]:
    result = {"pid": pid, "stopped": False, "signal": "TERM"}
    if not is_pid_alive(pid):
        result["stopped"] = True
        return result
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        result["error"] = str(exc)
        return result

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if not is_pid_alive(pid):
            result["stopped"] = True
            break
        time.sleep(1)

    if not result["stopped"] and is_pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
            result["signal"] = "KILL"
        except OSError as exc:
            result["error"] = str(exc)
            return result
        time.sleep(1)
        result["stopped"] = not is_pid_alive(pid)

    if result["stopped"]:
        try:
            PID_FILE.unlink()
        except FileNotFoundError:
            pass
    return result


def start_runner(config_path: Path, envelope_path: Path) -> dict[str, Any]:
    returncode = 1
    stdout = ""
    stderr = ""
    try:
        proc = run_cmd(
            [
                str(LIVE_RUNNER_WRAPPER),
                "--config",
                str(config_path),
                "--decision-envelope",
                str(envelope_path),
            ],
            cwd=WORKSPACE,
            timeout=30,
            check=False,
        )
        returncode = proc.returncode
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
    except Exception as exc:
        stderr = str(exc)

    pid = read_pid(PID_FILE)
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "pid": pid,
    }


def wait_for_start(
    pid: int | None,
    *,
    baseline_mtime: float | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if pid and not is_pid_alive(pid):
            return {"ok": False, "reason": "process_exited_early"}
        current_mtime = file_mtime(LIVE_STATE_PATH)
        if current_mtime and (baseline_mtime is None or current_mtime > baseline_mtime):
            return {"ok": True, "reason": "live_state_updated", "live_state_mtime": current_mtime}
        time.sleep(5)
    if pid and is_pid_alive(pid):
        return {"ok": True, "reason": "process_alive_timeout_no_live_state"}
    return {"ok": False, "reason": "start_timeout"}


def runtime_payload(
    *,
    status: str,
    config_path: Path,
    desired_envelope: Any | None,
    applied_envelope: Any | None,
    action: str,
    action_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pid = read_pid(PID_FILE)
    live_state = read_json(LIVE_STATE_PATH, {}) or {}
    return {
        "timestamp": iso_now(),
        "status": status,
        "pid": pid,
        "run_id": live_state.get("run_id") or resolve_current_run_id(config_path),
        "config_path": str(config_path),
        "desired_decision_id": getattr(desired_envelope, "decision_id", None),
        "applied_decision_id": getattr(applied_envelope, "decision_id", None),
        "trading_state": getattr(desired_envelope, "trading_state", "halted") if desired_envelope else "halted",
        "selected_transport": (getattr(desired_envelope, "metadata", {}) or {}).get("selected_transport") if desired_envelope else None,
        "live_state_path": str(LIVE_STATE_PATH),
        "live_state_age_seconds": file_age_seconds(LIVE_STATE_PATH),
        "last_supervisor_action": action,
        "last_supervisor_result": action_result or {},
        "process_alive": is_pid_alive(pid),
    }


def cmd_supervisor(args: argparse.Namespace) -> int:
    with file_lock(PMM_SUPERVISOR_LOCK):
        cycle_state = read_json(QUANT_CYCLE_STATE_PATH, {}) or {}
        config_path = current_live_config(args.config)
        remove_stale_pid_file()

        latest_envelope, latest_error = load_envelope_safe(DECISION_LATEST_PATH)
        applied_envelope, _ = load_envelope_safe(DECISION_APPLIED_PATH)

        if latest_envelope and latest_envelope.is_expired():
            latest_error = f"decision envelope {latest_envelope.decision_id} expired"
            latest_envelope = None
        elif latest_envelope and latest_envelope.trading_state == "active":
            try:
                latest_envelope.require_live_ready()
            except Exception as exc:
                latest_error = str(exc)
                latest_envelope = None

        pid = read_pid(PID_FILE)
        running = is_pid_alive(pid)
        live_state_age = file_age_seconds(LIVE_STATE_PATH)
        live_state_fresh = live_state_age is not None and live_state_age <= args.live_state_max_age

        if latest_error or latest_envelope is None:
            if args.dry_run:
                stop_result = {"dry_run": True, "would_stop_pid": pid}
            elif running and pid:
                stop_result = stop_runner(pid, timeout_seconds=args.stop_timeout)
            else:
                stop_result = {"stopped": True, "reason": "already_stopped"}
            payload = runtime_payload(
                status="halted",
                config_path=config_path,
                desired_envelope=None,
                applied_envelope=applied_envelope,
                action="halt_missing_or_invalid_latest",
                action_result={"error": latest_error, **stop_result},
            )
            write_json(PMM_RUNTIME_STATE_PATH, payload)
            cycle_state["runner_down_since"] = cycle_state.get("runner_down_since") or iso_now()
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print("halted:no_valid_latest_envelope")
            return 0

        if latest_envelope.trading_state != "active":
            stop_result = {"stopped": True, "reason": "standby"}
            if args.dry_run:
                stop_result = {"dry_run": True, "would_stop_pid": pid}
            elif running and pid:
                stop_result = stop_runner(pid, timeout_seconds=args.stop_timeout)
            payload = runtime_payload(
                status=latest_envelope.trading_state,
                config_path=config_path,
                desired_envelope=latest_envelope,
                applied_envelope=applied_envelope,
                action="enforce_non_active_trading_state",
                action_result=stop_result,
            )
            write_json(PMM_RUNTIME_STATE_PATH, payload)
            if latest_envelope.trading_state == "halted":
                maybe_open_incident(
                    code="trading_state_halted",
                    title="PMM: supervisor entered halted state",
                    description=(
                        "The promoted envelope requested halted trading.\n\n"
                        f"Envelope: {DECISION_LATEST_PATH}\n"
                        f"Reason: {latest_envelope.decision_reason or '(none)'}"
                    ),
                    cycle_state=cycle_state,
                    dry_run=args.dry_run,
                )
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"{latest_envelope.trading_state}:supervisor")
            return 0

        if running and applied_envelope and applied_envelope.decision_id == latest_envelope.decision_id and live_state_fresh:
            cycle_state["consecutive_restart_failures"] = 0
            cycle_state.pop("runner_down_since", None)
            payload = runtime_payload(
                status="running",
                config_path=config_path,
                desired_envelope=latest_envelope,
                applied_envelope=applied_envelope,
                action="noop_current_decision_healthy",
                action_result={"live_state_fresh": True},
            )
            write_json(PMM_RUNTIME_STATE_PATH, payload)
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"running:{latest_envelope.decision_id}")
            return 0

        stop_result = {"stopped": True, "reason": "no_running_process"}
        if args.dry_run:
            stop_result = {"dry_run": True, "would_stop_pid": pid}
        elif running and pid:
            stop_result = stop_runner(pid, timeout_seconds=args.stop_timeout)

        if args.dry_run:
            start_result = {
                "dry_run": True,
                "config_path": str(config_path),
                "envelope_path": str(DECISION_LATEST_PATH),
                "pid": pid,
            }
            wait_result = {"ok": True, "reason": "dry_run"}
        else:
            baseline_mtime = file_mtime(LIVE_STATE_PATH)
            start_result = start_runner(config_path, DECISION_LATEST_PATH)
            wait_result = wait_for_start(
                start_result.get("pid"),
                baseline_mtime=baseline_mtime,
                timeout_seconds=args.start_timeout,
            )
        if (args.dry_run or start_result.get("returncode") == 0) and wait_result.get("ok"):
            if not args.dry_run:
                write_json(DECISION_APPLIED_PATH, read_json(DECISION_LATEST_PATH, {}))
            cycle_state["consecutive_restart_failures"] = 0
            cycle_state.pop("runner_down_since", None)
            cycle_state["last_applied_decision_id"] = latest_envelope.decision_id
            cycle_state["last_applied_at"] = iso_now()
            payload = runtime_payload(
                status="running",
                config_path=config_path,
                desired_envelope=latest_envelope,
                applied_envelope=latest_envelope,
                action="apply_latest_envelope",
                action_result={"stop": stop_result, "start": start_result, "wait": wait_result},
            )
            write_json(PMM_RUNTIME_STATE_PATH, payload)
            write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
            if args.json:
                print(json.dumps(payload, indent=2))
            else:
                print(f"applied:{latest_envelope.decision_id}")
            return 0

        cycle_state["consecutive_restart_failures"] = int(cycle_state.get("consecutive_restart_failures", 0)) + 1
        cycle_state["runner_down_since"] = cycle_state.get("runner_down_since") or iso_now()

        rollback_result: dict[str, Any] | None = None
        if applied_envelope and applied_envelope.decision_id != latest_envelope.decision_id and not applied_envelope.is_expired():
            try:
                applied_envelope.require_live_ready()
                if args.dry_run:
                    rollback_start = {"dry_run": True, "pid": pid}
                    rollback_wait = {"ok": True, "reason": "dry_run"}
                else:
                    rollback_start = start_runner(config_path, DECISION_APPLIED_PATH)
                    rollback_wait = wait_for_start(
                        rollback_start.get("pid"),
                        baseline_mtime=file_mtime(LIVE_STATE_PATH),
                        timeout_seconds=args.start_timeout,
                    )
                if (args.dry_run or rollback_start.get("returncode") == 0) and rollback_wait.get("ok"):
                    cycle_state["consecutive_restart_failures"] = 0
                    cycle_state.pop("runner_down_since", None)
                    rollback_result = {"start": rollback_start, "wait": rollback_wait, "ok": True}
                    payload = runtime_payload(
                        status="running",
                        config_path=config_path,
                        desired_envelope=latest_envelope,
                        applied_envelope=applied_envelope,
                        action="rollback_to_previous_envelope",
                        action_result={"stop": stop_result, "start": start_result, "wait": wait_result, "rollback": rollback_result},
                    )
                    write_json(PMM_RUNTIME_STATE_PATH, payload)
                    write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
                    if args.json:
                        print(json.dumps(payload, indent=2))
                    else:
                        print(f"rollback:{applied_envelope.decision_id}")
                    return 0
            except Exception as exc:
                rollback_result = {"ok": False, "error": str(exc)}

        down_since = parse_dt(cycle_state.get("runner_down_since"))
        if cycle_state["consecutive_restart_failures"] >= 3:
            maybe_open_incident(
                code="restart_failed_3x",
                title="PMM: supervisor restart failed 3 times",
                description=(
                    "The PMM supervisor failed to apply the promoted envelope 3 times in a row.\n\n"
                    f"Latest: {DECISION_LATEST_PATH}\n"
                    f"Applied: {DECISION_APPLIED_PATH}\n"
                    f"Runtime: {PMM_RUNTIME_STATE_PATH}"
                ),
                cycle_state=cycle_state,
                dry_run=args.dry_run,
            )
        if down_since and utcnow() - down_since >= timedelta(minutes=10):
            maybe_open_incident(
                code="runner_down_10m",
                title="PMM: runner down for more than 10 minutes",
                description=(
                    "The PMM runner stayed down for more than 10 minutes while the latest envelope wanted live trading.\n\n"
                    f"Latest: {DECISION_LATEST_PATH}\n"
                    f"Runtime: {PMM_RUNTIME_STATE_PATH}"
                ),
                cycle_state=cycle_state,
                dry_run=args.dry_run,
            )

        payload = runtime_payload(
            status="halted",
            config_path=config_path,
            desired_envelope=latest_envelope,
            applied_envelope=applied_envelope,
            action="apply_failed",
            action_result={"stop": stop_result, "start": start_result, "wait": wait_result, "rollback": rollback_result},
        )
        write_json(PMM_RUNTIME_STATE_PATH, payload)
        write_json(QUANT_CYCLE_STATE_PATH, cycle_state)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print("halted:apply_failed")
        return 1


def cmd_wake(args: argparse.Namespace) -> int:
    message = args.message
    if not message:
        reason = args.reason or "manual_review"
        target = args.target
        message = (
            f"Manual wake from control plane.\n"
            f"Reason: {reason}\n"
            f"Candidate: {DECISION_CANDIDATE_PATH}\n"
            f"Latest: {DECISION_LATEST_PATH}\n"
            f"Diagnosis: {QUANT_DIAGNOSIS_PATH}\n"
            f"Runtime: {PMM_RUNTIME_STATE_PATH}"
        )
        if target == "luna":
            message = build_luna_message(f"PMM incident wake: {reason}", None, reason)

    result = wake_agent(
        args.target,
        message,
        idempotency_key=args.idempotency_key or f"{args.target}-{int(time.time())}",
        dry_run=args.dry_run,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"target={args.target} returncode={result.get('returncode', 0)}")
    return 0 if result.get("returncode", 0) == 0 or result.get("dry_run") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quant + PMM V1 control plane")
    sub = parser.add_subparsers(dest="command", required=True)

    cycle = sub.add_parser("cycle", help="Run deterministic Quant cycle")
    cycle.add_argument("--config", type=str, default=None)
    cycle.add_argument("--capital-usdc", type=str, default=None)
    cycle.add_argument("--top", type=int, default=3)
    cycle.add_argument("--expires-hours", type=int, default=6)
    cycle.add_argument("--directional-live", action="store_true")
    cycle.add_argument("--directional-signals", type=str, default=None)
    cycle.add_argument("--dry-run", action="store_true")
    cycle.add_argument("--json", action="store_true")

    diagnose = sub.add_parser("diagnose", help="Generate post-trade diagnosis snapshot")
    diagnose.add_argument("--config", type=str, default=None)
    diagnose.add_argument("--run-id", type=str, default=None)
    diagnose.add_argument("--dry-run", action="store_true")
    diagnose.add_argument("--json", action="store_true")

    promote = sub.add_parser("promote", help="Validate and promote candidate envelope")
    promote.add_argument("--candidate", type=str, default=None)
    promote.add_argument("--dry-run", action="store_true")
    promote.add_argument("--json", action="store_true")

    supervisor = sub.add_parser("supervisor", help="Apply promoted envelope via controlled restart")
    supervisor.add_argument("--config", type=str, default=None)
    supervisor.add_argument("--start-timeout", type=int, default=60)
    supervisor.add_argument("--stop-timeout", type=int, default=45)
    supervisor.add_argument("--live-state-max-age", type=int, default=180)
    supervisor.add_argument("--dry-run", action="store_true")
    supervisor.add_argument("--json", action="store_true")

    wake = sub.add_parser("wake", help="Wake Quant or Luna via gateway call agent")
    wake.add_argument("--target", choices=["quant", "luna"], default="quant")
    wake.add_argument("--reason", type=str, default=None)
    wake.add_argument("--message", type=str, default=None)
    wake.add_argument("--idempotency-key", type=str, default=None)
    wake.add_argument("--dry-run", action="store_true")
    wake.add_argument("--json", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command == "cycle":
            return cmd_cycle(args)
        if args.command == "diagnose":
            return cmd_diagnose(args)
        if args.command == "promote":
            return cmd_promote(args)
        if args.command == "supervisor":
            return cmd_supervisor(args)
        if args.command == "wake":
            return cmd_wake(args)
    except BlockingIOError:
        print(f"lock_busy:{args.command}")
        return 0
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
