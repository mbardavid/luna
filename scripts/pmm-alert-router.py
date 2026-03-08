#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


WORKSPACE = Path(os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace"))
PMM_ROOT = Path(os.environ.get("PMM_ROOT", str(WORKSPACE / "polymarket-mm")))
DATA_DIR = PMM_ROOT / "paper" / "data"
LOG_PATH = PMM_ROOT / "logs" / "production.log"
SCRIPTS_DIR = WORKSPACE / "scripts"

MC_CLIENT = SCRIPTS_DIR / "mc-client.sh"
TOPOLOGY_HELPER = SCRIPTS_DIR / "agent_runtime_topology.py"
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")

RUNTIME_STATE_PATH = DATA_DIR / "pmm_runtime_state.json"
LIVE_STATE_PATH = DATA_DIR / "live_state_production.json"
LATEST_PATH = DATA_DIR / "decision_envelope_latest.json"
APPLIED_PATH = DATA_DIR / "decision_envelope_applied.json"
CANDIDATE_PATH = DATA_DIR / "decision_envelope_candidate.json"
DIAGNOSIS_PATH = DATA_DIR / "quant_diagnosis_latest.json"
CYCLE_STATE_PATH = DATA_DIR / "quant_cycle_state.json"
STATE_PATH = DATA_DIR / "pmm_alert_router_state.json"

PARENT_TITLE = "PMM Live Operations"
SNAPSHOT_CHANNEL = "discord:notifications"
UNKNOWN_RUN_ID = "unknown"
REJECT_RATE_THRESHOLD = 1.0
NO_REWARDS_THRESHOLD = timedelta(hours=2)
CANDIDATE_PROMOTION_THRESHOLD = timedelta(minutes=10)
LATEST_APPLIED_DRIFT_THRESHOLD = timedelta(minutes=5)
EXPIRING_THRESHOLD = timedelta(minutes=15)
WAKE_COOLDOWN = timedelta(minutes=30)

OWNER_MAP = {
    "reward_adjusted_pnl_negative": "quant-strategist",
    "no_rewards_eligible_2h": "quant-strategist",
    "transport_gate_conflict": "quant-strategist",
    "envelope_expiring_without_successor": "quant-strategist",
    "candidate_valid_without_promotion": "quant-strategist",
    "fill_rate_collapse": "quant-strategist",
    "reject_rate_above_threshold": "quant-strategist",
    "adverse_selection_spike": "quant-strategist",
    "economic_standby_recommendation": "quant-strategist",
    "capital_reallocation_required": "quant-strategist",
    "trading_state_halted": "quant-strategist",
    "recoverable_inventory_detected": "crypto-sage",
    "inventory_unwind_required": "crypto-sage",
    "balance_allowance_mismatch": "crypto-sage",
    "collateral_recovery_required": "crypto-sage",
    "wallet_state_mismatch": "crypto-sage",
    "execution_onchain_repair_required": "crypto-sage",
    "mc_task_creation_failed": "luna",
    "wake_delivery_failed": "luna",
    "agent_id_resolution_failed": "luna",
    "control_plane_state_conflict": "luna",
    "repeated_restart_failures": "luna",
    "repeated_promote_failures": "luna",
    "restart_failed_3x": "luna",
    "promote_failed_3x": "luna",
    "runner_down_10m": "luna",
    "runner_halted": "luna",
    "latest_applied_drift": "luna",
    "runtime_active_expired_envelope": "luna",
    "directional_live_governance_gate": "luna",
}

SEVERITY_MAP = {
    "snapshot": "snapshot",
    "incident": "incident",
    "escalation": "escalation",
}


def incident_history_key(incident: dict[str, Any]) -> str:
    task_id = str((incident or {}).get("task_id") or "").strip()
    if task_id:
        return task_id
    code = str((incident or {}).get("code") or "unknown").strip() or "unknown"
    opened_at = str((incident or {}).get("opened_at") or (incident or {}).get("resolved_at") or "").strip()
    return f"{code}:{opened_at}"


def normalize_router_state(state: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(state or {})
    open_incidents = payload.get("open_incidents")
    if not isinstance(open_incidents, dict):
        payload["open_incidents"] = {}

    resolved = payload.get("resolved_incidents")
    if isinstance(resolved, list):
        payload["resolved_incidents"] = {
            incident_history_key(item): item
            for item in resolved
            if isinstance(item, dict)
        }
    elif not isinstance(resolved, dict):
        payload["resolved_incidents"] = {}
    return payload


def _load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_topology_module():
    return _load_module(TOPOLOGY_HELPER, "agent_runtime_topology_runtime")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat()


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


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


def run_cmd(cmd: list[str], *, cwd: Path | None = None, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def file_age_seconds(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except FileNotFoundError:
        return None


def is_placeholder_task_id(task_id: Any) -> bool:
    text = str(task_id or "").strip()
    return not text or text.startswith("dry-run-")


def normalize_owner(owner: str | None) -> str:
    topology = load_topology_module()
    aliases = {
        "quant": "quant-strategist",
        "quant_strategist": "quant-strategist",
        "crypto_sage": "crypto-sage",
        "blockchain-operator": "crypto-sage",
        "luna": "main",
    }
    text = str(owner or "").strip().lower()
    if not text:
        return "main"
    normalized = aliases.get(text) or topology.normalize_agent_name(text) or text
    return str(normalized)


def owner_label(owner: str) -> str:
    canonical = normalize_owner(owner)
    if canonical == "main":
        return "luna"
    return canonical


def resolve_workspace(owner: str) -> Path:
    topology = load_topology_module()
    workspace = topology.resolve_workspace(normalize_owner(owner))
    if workspace:
        return Path(workspace)
    return WORKSPACE


def mc_call(*args: str, timeout: int = 45) -> tuple[dict[str, Any], str]:
    if not MC_CLIENT.exists():
        return {"returncode": 1, "stdout": "", "stderr": f"missing mc-client: {MC_CLIENT}"}, ""
    proc = run_cmd([str(MC_CLIENT), *args], cwd=WORKSPACE, timeout=timeout)
    payload: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    task_id = ""
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, dict):
                task_id = str(parsed.get("id") or parsed.get("task_id") or "")
                payload["response"] = parsed
        except json.JSONDecodeError:
            pass
    return payload, task_id


def wake_agent(owner: str, message: str, *, idempotency_key: str, dry_run: bool = False) -> dict[str, Any]:
    label = owner_label(owner)
    workspace = resolve_workspace(owner)
    params = {"message": message, "idempotencyKey": idempotency_key}
    if dry_run:
        return {
            "dry_run": True,
            "target": label,
            "workspace": str(workspace),
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
        cwd=workspace,
        timeout=25,
    )
    payload: dict[str, Any] = {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
        "target": label,
        "workspace": str(workspace),
    }
    if proc.stdout.strip():
        try:
            payload["response"] = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pass
    return payload


def load_snapshot() -> dict[str, Any]:
    runtime = read_json(RUNTIME_STATE_PATH, {})
    live_state = read_json(LIVE_STATE_PATH, {})
    if live_state.get("stale") and str(runtime.get("status") or "").lower() != "running":
        live_state = {}
    latest = read_json(LATEST_PATH, {})
    applied = read_json(APPLIED_PATH, {})
    candidate = read_json(CANDIDATE_PATH, {})
    diagnosis = read_json(DIAGNOSIS_PATH, {})
    cycle_state = read_json(CYCLE_STATE_PATH, {})
    return {
        "runtime": runtime,
        "live_state": live_state,
        "latest": latest,
        "applied": applied,
        "candidate": candidate,
        "diagnosis": diagnosis,
        "cycle_state": cycle_state,
        "latest_age_seconds": file_age_seconds(LATEST_PATH),
        "applied_age_seconds": file_age_seconds(APPLIED_PATH),
        "candidate_age_seconds": file_age_seconds(CANDIDATE_PATH),
    }


def current_market_label(snapshot: dict[str, Any]) -> str:
    live_markets = (snapshot["live_state"].get("markets") or {})
    if live_markets:
        first = next(iter(live_markets.values()))
        if first.get("description"):
            return str(first["description"])
    latest_markets = snapshot["latest"].get("markets") or []
    if latest_markets:
        first = latest_markets[0] or {}
        return str(first.get("description") or first.get("market_id") or "n/a")
    return "n/a"


def parent_status(runtime_status: str) -> str:
    if runtime_status == "running":
        return "in_progress"
    return "blocked"


def parent_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = snapshot["runtime"] or {}
    latest = snapshot["latest"] or {}
    run_id = runtime.get("run_id") or snapshot["live_state"].get("run_id") or UNKNOWN_RUN_ID
    return {
        "mc_origin": "pmm_live_control_plane",
        "run_id": run_id,
        "environment": "live",
        "decision_id_current": latest.get("decision_id"),
        "operation_channel": SNAPSHOT_CHANNEL,
        "pmm_status": runtime.get("status") or "unknown",
        "parent_task": True,
    }


def build_parent_description(snapshot: dict[str, Any]) -> str:
    runtime = snapshot["runtime"] or {}
    latest = snapshot["latest"] or {}
    run_id = runtime.get("run_id") or snapshot["live_state"].get("run_id") or UNKNOWN_RUN_ID
    return (
        "Persistent parent card for PMM live operations.\n\n"
        f"Run: {run_id}\n"
        f"Status: {runtime.get('status') or 'unknown'}\n"
        f"Decision: {latest.get('decision_id') or 'n/a'}\n"
        f"Market: {current_market_label(snapshot)}\n"
        f"Runtime: {RUNTIME_STATE_PATH}\n"
        f"Latest envelope: {LATEST_PATH}\n"
        f"Applied envelope: {APPLIED_PATH}\n"
        f"Diagnosis: {DIAGNOSIS_PATH}\n"
        f"Live state: {LIVE_STATE_PATH}\n"
        "Use child incidents for actionable alerts; keep snapshots in Discord notifications."
    )


def ensure_parent_task(state: dict[str, Any], snapshot: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    parent = state.setdefault("parent_task", {})
    task_id = str(parent.get("task_id") or "")
    if is_placeholder_task_id(task_id):
        task_id = ""
    fields = parent_fields(snapshot)
    runtime = snapshot["runtime"] or {}
    desired_status = parent_status(str(runtime.get("status") or ""))

    if not task_id:
        if dry_run:
            task_id = "dry-run-parent-task"
            parent["create_result"] = {"dry_run": True}
        else:
            create_result, task_id = mc_call(
                "create-task",
                PARENT_TITLE,
                build_parent_description(snapshot),
                "luna",
                "medium",
                desired_status,
            )
            parent["create_result"] = create_result
        parent.update({"task_id": task_id, "created_at": iso_now()})

    if task_id:
        parent["task_id"] = task_id
        parent["last_seen_at"] = iso_now()
        if dry_run:
            parent["update_result"] = {"dry_run": True}
        else:
            update_result, _ = mc_call(
                "update-task",
                task_id,
                "--status",
                desired_status,
            )
            parent["update_result"] = update_result

        comment_payload = {
            "run_id": fields["run_id"],
            "decision_id": fields["decision_id_current"],
            "pmm_status": fields["pmm_status"],
            "market": current_market_label(snapshot),
        }
        comment_hash = json.dumps(comment_payload, sort_keys=True)
        if comment_hash != parent.get("last_comment_hash"):
            message = (
                "PMM live state update.\n\n"
                f"run_id={fields['run_id']}\n"
                f"decision_id={fields['decision_id_current'] or 'n/a'}\n"
                f"status={fields['pmm_status']}\n"
                f"market={comment_payload['market']}"
            )
            if dry_run:
                parent["last_comment_result"] = {"dry_run": True, "message": message}
            else:
                comment_result, _ = mc_call("create-comment", task_id, message)
                parent["last_comment_result"] = comment_result
            parent["last_comment_hash"] = comment_hash
    return parent


def incident_owner(code: str, owner: str | None = None) -> str:
    if owner:
        return owner_label(owner)
    return owner_label(OWNER_MAP.get(code, "luna"))


def build_incident_message(incident: dict[str, Any], task_id: str | None, parent_task_id: str | None) -> str:
    task_line = f"Mission Control task: {task_id}\n" if task_id else ""
    parent_line = f"Parent PMM card: {parent_task_id}\n" if parent_task_id else ""
    return (
        f"{incident['title']}\n"
        f"Incident code: {incident['code']}\n"
        f"{task_line}"
        f"{parent_line}"
        f"Owner: {incident['owner']}\n"
        f"Run: {incident.get('run_id') or UNKNOWN_RUN_ID}\n"
        f"Decision: {incident.get('decision_id') or 'n/a'}\n"
        f"Reason: {incident['summary']}\n"
        f"Runtime: {RUNTIME_STATE_PATH}\n"
        f"Latest envelope: {LATEST_PATH}\n"
        f"Applied envelope: {APPLIED_PATH}\n"
        f"Diagnosis: {DIAGNOSIS_PATH}\n"
        "Act on this incident and update Mission Control when the objective closure criterion is met."
    )


def create_incident_task(
    incident: dict[str, Any],
    parent_task_id: str | None,
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], str]:
    description = (
        f"{incident['summary']}\n\n"
        f"Owner: {incident['owner']}\n"
        f"Run: {incident.get('run_id') or UNKNOWN_RUN_ID}\n"
        f"Decision: {incident.get('decision_id') or 'n/a'}\n"
        f"Paths:\n"
        f"- runtime: {RUNTIME_STATE_PATH}\n"
        f"- latest: {LATEST_PATH}\n"
        f"- applied: {APPLIED_PATH}\n"
        f"- candidate: {CANDIDATE_PATH}\n"
        f"- diagnosis: {DIAGNOSIS_PATH}\n"
        f"- log: {LOG_PATH}\n\n"
        f"Objective closure criterion:\n{incident['resolution_criteria']}"
    )
    if dry_run:
        return {"dry_run": True}, f"dry-run-{incident['code']}"
    return mc_call(
        "create-task",
        incident["title"],
        description,
        incident["owner"],
        "high",
        "inbox",
    )


def resolve_incident_task(task_id: str, code: str, *, dry_run: bool = False) -> dict[str, Any]:
    message = f"Router auto-resolved incident `{code}` because the triggering signal cleared."
    if dry_run:
        return {"dry_run": True, "task_id": task_id, "message": message}
    result, _ = mc_call("update-task", task_id, "--status", "done", "--comment", message)
    return result


def add_parent_incident_comment(parent_task_id: str | None, incident: dict[str, Any], task_id: str, *, dry_run: bool = False) -> dict[str, Any]:
    if not parent_task_id:
        return {"skipped": True, "reason": "missing_parent"}
    message = (
        f"PMM incident opened.\n\n"
        f"code={incident['code']}\n"
        f"owner={incident['owner']}\n"
        f"task_id={task_id}\n"
        f"decision_id={incident.get('decision_id') or 'n/a'}\n"
        f"run_id={incident.get('run_id') or UNKNOWN_RUN_ID}"
    )
    if dry_run:
        return {"dry_run": True, "message": message}
    result, _ = mc_call("create-comment", parent_task_id, message)
    return result


def maybe_rewake(existing: dict[str, Any], *, owner: str, message: str, dry_run: bool = False) -> dict[str, Any] | None:
    last_wake_at = parse_dt(existing.get("last_wake_at"))
    if last_wake_at and utcnow() - last_wake_at < WAKE_COOLDOWN:
        return None
    wake_result = wake_agent(owner, message, idempotency_key=f"pmm-{existing['code']}-{int(time.time())}", dry_run=dry_run)
    existing["last_wake_at"] = iso_now()
    existing["last_wake_result"] = wake_result
    return wake_result


def incident_from_explicit_args(args: argparse.Namespace, snapshot: dict[str, Any]) -> dict[str, Any]:
    runtime = snapshot["runtime"] or {}
    latest = snapshot["latest"] or {}
    owner = incident_owner(args.code, args.owner)
    return {
        "code": args.code,
        "title": args.title,
        "summary": args.description,
        "owner": owner,
        "run_id": runtime.get("run_id") or snapshot["live_state"].get("run_id") or UNKNOWN_RUN_ID,
        "decision_id": latest.get("decision_id") or snapshot["candidate"].get("decision_id"),
        "resolution_criteria": args.resolution_criteria or "The triggering condition no longer appears in runtime, envelope, or diagnosis artifacts.",
        "severity": args.severity,
        "class": SEVERITY_MAP.get(args.severity, "incident"),
    }


def as_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def build_runtime_incidents(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    runtime = snapshot["runtime"] or {}
    latest = snapshot["latest"] or {}
    applied = snapshot["applied"] or {}
    candidate = snapshot["candidate"] or {}
    diagnosis = snapshot["diagnosis"] or {}
    cycle_state = snapshot["cycle_state"] or {}
    incidents: list[dict[str, Any]] = []
    run_id = runtime.get("run_id") or snapshot["live_state"].get("run_id") or UNKNOWN_RUN_ID
    decision_id = latest.get("decision_id") or applied.get("decision_id") or candidate.get("decision_id")
    runtime_status = str(runtime.get("status") or "")
    latest_expires = parse_dt(latest.get("expires_at"))
    candidate_expires = parse_dt(candidate.get("expires_at"))
    candidate_age = snapshot.get("candidate_age_seconds")
    latest_age = snapshot.get("latest_age_seconds")
    latest_transport_gates = (latest.get("metadata") or {}).get("transport_live_gates") or {}
    diagnosis_ptd = ((diagnosis.get("analysis") or {}).get("post_trade_diagnosis") or {})
    taint_reasons = set(((diagnosis_ptd.get("taint") or {}).get("reasons") or []))
    wallet_state = diagnosis_ptd.get("wallet_state") or {}
    reward_adjusted_pnl = as_float((diagnosis_ptd.get("reward_adjusted_pnl") or {}).get("reward_adjusted_pnl_usd"))
    reject_rate_pct = as_float((diagnosis_ptd.get("execution_pnl") or {}).get("reject_rate_pct"))

    def add(code: str, title: str, summary: str, resolution: str) -> None:
        incidents.append(
            {
                "code": code,
                "title": title,
                "summary": summary,
                "resolution_criteria": resolution,
                "owner": incident_owner(code),
                "run_id": run_id,
                "decision_id": decision_id,
                "severity": "high",
                "class": "incident",
            }
        )

    if runtime_status == "running" and latest_expires and latest_expires <= utcnow():
        add(
            "runtime_active_expired_envelope",
            "PMM: runtime running with expired envelope",
            "The PMM runtime is still marked as running even though the promoted envelope is already expired.",
            "The runtime is no longer active with an expired envelope, and latest/applied/runtime agree on the current decision state.",
        )

    if runtime_status == "halted":
        candidate_ready = bool(candidate.get("decision_id")) and (candidate_expires is None or candidate_expires > utcnow())
        latest_invalid = not latest.get("decision_id") or (latest_expires and latest_expires <= utcnow())
        if candidate_ready and latest_invalid and candidate_age is not None and candidate_age >= CANDIDATE_PROMOTION_THRESHOLD.total_seconds():
            add(
                "candidate_valid_without_promotion",
                "PMM: promotable candidate waiting without promotion",
                "A valid candidate envelope has been sitting long enough while the runtime remains halted and the promoted envelope is missing or expired.",
                "A valid envelope is promoted or the candidate is explicitly rejected with a newer successor.",
            )
        elif latest.get("trading_state") == "active":
            add(
                "runner_halted",
                "PMM: runner halted while live trading was expected",
                "The runtime is halted even though the promoted envelope still requests active trading.",
                "The runner is back to running with the promoted envelope or the envelope is intentionally moved to standby/halted.",
            )

    if latest_expires and latest_expires - utcnow() <= EXPIRING_THRESHOLD:
        candidate_same = candidate.get("decision_id") == latest.get("decision_id")
        candidate_ready = bool(candidate.get("decision_id")) and (candidate_expires is None or candidate_expires > utcnow())
        if not candidate_ready or candidate_same:
            add(
                "envelope_expiring_without_successor",
                "PMM: envelope expiring without successor",
                "The promoted live envelope is close to expiry and there is no distinct valid successor candidate ready to take over.",
                "A new candidate is produced and promoted before the current envelope expires.",
            )

    if (
        latest.get("decision_id")
        and applied.get("decision_id")
        and latest.get("decision_id") != applied.get("decision_id")
        and latest.get("trading_state") == "active"
    ):
        if latest_age is not None and latest_age >= LATEST_APPLIED_DRIFT_THRESHOLD.total_seconds():
            add(
                "latest_applied_drift",
                "PMM: promoted envelope diverged from applied envelope",
                "The latest promoted envelope and the applied envelope differ for longer than the allowed drift window.",
                "Latest and applied decision IDs match again, or runtime is explicitly halted with a documented reason.",
            )

    if latest.get("trading_state") == "active" and latest_transport_gates.get("rewards_live_ok") is False:
        add(
            "transport_gate_conflict",
            "PMM: transport live gate conflict",
            "The promoted envelope wants active rewards live trading even though transport live gates say rewards live is not allowed.",
            "Transport live gates allow the promoted mode or the envelope is replaced with standby/halted.",
        )

    no_rewards_since = parse_dt(cycle_state.get("no_rewards_since"))
    if no_rewards_since and utcnow() - no_rewards_since >= NO_REWARDS_THRESHOLD:
        add(
            "no_rewards_eligible_2h",
            "PMM: no rewards-eligible markets for 2h",
            "The Quant control plane has kept the PMM without any enabled rewards market for more than two hours.",
            "At least one rewards market becomes eligible again or the system is intentionally paused with documented rationale.",
        )

    if reward_adjusted_pnl < 0:
        add(
            "reward_adjusted_pnl_negative",
            "PMM: reward-adjusted PnL turned negative",
            "The latest diagnosis shows negative reward-adjusted PnL, indicating the current market selection or quoting policy may be unprofitable.",
            "Reward-adjusted PnL returns non-negative or the envelope changes to a new market/sizing policy.",
        )

    if reject_rate_pct > REJECT_RATE_THRESHOLD:
        add(
            "reject_rate_above_threshold",
            "PMM: reject rate above threshold",
            "Execution reject rate is above the configured threshold for the current diagnosis window.",
            "Reject rate falls back under threshold or the live envelope is replaced with a safer policy.",
        )

    if {"balance_allowance_errors", "config_balance_mismatch"} & taint_reasons:
        add(
            "balance_allowance_mismatch",
            "PMM: balance or allowance mismatch",
            "The latest diagnosis found balance/allowance mismatches that can block safe execution or recovery flows.",
            "Wallet balance, allowance, and runtime accounting match again in diagnosis and live preflight.",
        )

    if runtime_status != "running" and latest.get("trading_state") != "active":
        recoverable_inventory = as_float(wallet_state.get("recoverable_inventory_usdc"))
        if recoverable_inventory > 1.0:
            add(
                "recoverable_inventory_detected",
                "PMM: recoverable inventory still trapped",
                "The system is not actively trading, but diagnosis still sees meaningful recoverable inventory that should be unwound or merged.",
                "Recoverable inventory drops below the dust threshold or a documented hold reason is attached.",
            )

    directional_live = bool((latest.get("risk_limits") or {}).get("allow_directional_live")) or bool(
        (candidate.get("risk_limits") or {}).get("allow_directional_live")
    )
    if directional_live and os.environ.get("PMM_DIRECTIONAL_LIVE_APPROVED") != "1":
        add(
            "directional_live_governance_gate",
            "PMM: directional live requested without governance gate",
            "A candidate or promoted envelope requests directional live trading without the explicit governance approval flag.",
            "Directional live approval is granted or directional live is removed from the active candidate/envelope.",
        )

    return incidents


def route_incident(
    incident: dict[str, Any],
    state: dict[str, Any],
    parent_task_id: str | None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    open_incidents = state.setdefault("open_incidents", {})
    code = incident["code"]
    existing = open_incidents.get(code)
    if existing and not is_placeholder_task_id(existing.get("task_id")):
        existing["last_seen_at"] = iso_now()
        message = build_incident_message(incident, existing.get("task_id"), parent_task_id)
        maybe_rewake(existing, owner=incident["owner"], message=message, dry_run=dry_run)
        open_incidents[code] = existing
        return existing

    create_result, task_id = create_incident_task(incident, parent_task_id, dry_run=dry_run)
    message = build_incident_message(incident, task_id or None, parent_task_id)
    wake_result = maybe_rewake({"code": code}, owner=incident["owner"], message=message, dry_run=dry_run) or {}
    payload = {
        **incident,
        "task_id": task_id,
        "parent_task_id": parent_task_id,
        "opened_at": iso_now(),
        "last_seen_at": iso_now(),
        "create_result": create_result,
        "wake_result": wake_result,
        "last_wake_at": iso_now() if wake_result else None,
    }
    open_incidents[code] = payload

    if parent_task_id:
        payload["parent_comment_result"] = add_parent_incident_comment(parent_task_id, incident, task_id, dry_run=dry_run)
    return payload


def resolve_cleared_incidents(state: dict[str, Any], active_codes: set[str], *, dry_run: bool = False) -> list[dict[str, Any]]:
    open_incidents = state.setdefault("open_incidents", {})
    resolved_store = state.setdefault("resolved_incidents", {})
    resolved: list[dict[str, Any]] = []
    for code in list(open_incidents.keys()):
        if code in active_codes:
            continue
        incident = open_incidents.pop(code)
        task_id = str(incident.get("task_id") or "")
        incident["resolved_at"] = iso_now()
        key = incident_history_key(incident)
        already_resolved = key in resolved_store
        if task_id and not already_resolved:
            incident["resolve_result"] = resolve_incident_task(task_id, code, dry_run=dry_run)
        resolved_store[key] = incident
        resolved.append(incident)
    return resolved


def cmd_route(args: argparse.Namespace) -> int:
    state = normalize_router_state(read_json(STATE_PATH, {}) or {})
    snapshot = load_snapshot()
    parent = ensure_parent_task(state, snapshot, dry_run=args.dry_run)
    parent_task_id = str(parent.get("task_id") or "")
    incidents = build_runtime_incidents(snapshot)
    routed: list[dict[str, Any]] = []
    for incident in incidents:
        routed.append(route_incident(incident, state, parent_task_id, dry_run=args.dry_run))
    resolved = resolve_cleared_incidents(state, {item["code"] for item in incidents}, dry_run=args.dry_run)
    state["last_route_at"] = iso_now()
    state["last_route_summary"] = {
        "active_codes": [item["code"] for item in incidents],
        "open_incident_count": len(state.get("open_incidents", {})),
        "resolved_count": len(resolved),
    }
    if not args.dry_run:
        write_json(STATE_PATH, state)
    payload = {
        "ok": True,
        "parent_task_id": parent_task_id,
        "active_incidents": routed,
        "resolved_incidents": resolved,
        "state_path": str(STATE_PATH),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"parent_task_id={parent_task_id or 'n/a'} incidents={len(routed)} resolved={len(resolved)}")
    return 0


def cmd_incident(args: argparse.Namespace) -> int:
    state = normalize_router_state(read_json(STATE_PATH, {}) or {})
    snapshot = load_snapshot()
    parent = ensure_parent_task(state, snapshot, dry_run=args.dry_run)
    parent_task_id = str(parent.get("task_id") or "")
    incident = incident_from_explicit_args(args, snapshot)
    routed = route_incident(incident, state, parent_task_id, dry_run=args.dry_run)
    state["last_route_at"] = iso_now()
    if not args.dry_run:
        write_json(STATE_PATH, state)
    payload = {
        "ok": True,
        "incident": routed,
        "parent_task_id": parent_task_id,
        "state_path": str(STATE_PATH),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"incident={incident['code']} task_id={routed.get('task_id') or 'n/a'} owner={routed.get('owner')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PMM alert router for Discord snapshots + Mission Control incidents.")
    sub = parser.add_subparsers(dest="command", required=True)

    route = sub.add_parser("route", help="Classify PMM runtime state and route incidents to Mission Control.")
    route.add_argument("--dry-run", action="store_true")
    route.add_argument("--json", action="store_true")

    incident = sub.add_parser("incident", help="Open or refresh one explicit PMM incident through the router.")
    incident.add_argument("--code", required=True)
    incident.add_argument("--title", required=True)
    incident.add_argument("--description", required=True)
    incident.add_argument("--owner", default=None)
    incident.add_argument("--severity", choices=["incident", "escalation"], default="incident")
    incident.add_argument("--resolution-criteria", default=None)
    incident.add_argument("--dry-run", action="store_true")
    incident.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "route":
        return cmd_route(args)
    if args.command == "incident":
        return cmd_incident(args)
    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
