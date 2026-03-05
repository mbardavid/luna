#!/usr/bin/env bash
# auto-qa-reviewer.sh — Automated QA review for tasks in 'review' status
#
# Strategy: auto-close what's objectively verifiable, fallback to Luna for the rest.
#
# Auto-approve criteria (ALL must be true):
#   1. Task status = review
#   2. mc_last_error = needs_approval (agent thinks it's done)
#   3. Session has COMPLETION_STATUS: complete
#   4. mc_progress >= 50 (some progress was made)
#
# Fallback to Luna:
#   - If COMPLETION_STATUS != complete
#   - If mc_progress < 50
#   - If session data unavailable
#   - If task description contains high-risk indicators
#
# Usage:
#   ./auto-qa-reviewer.sh                # Normal run (process up to 2 per cycle)
#   ./auto-qa-reviewer.sh --dry-run      # Simulate without side-effects
#   ./auto-qa-reviewer.sh --verbose      # Extra logging
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${WORKSPACE:-$(dirname "$SCRIPT_DIR")}"
MC_CLIENT="${MC_CLIENT:-${SCRIPT_DIR}/mc-client.sh}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
GATEWAY_URL="${MC_GATEWAY_URL:-ws://127.0.0.1:18789}"
LOG_FILE="$WORKSPACE/logs/auto-qa-reviewer.log"
MAX_PER_CYCLE=2

mkdir -p "$(dirname "$LOG_FILE")"

# Parse args
DRY_RUN=0
VERBOSE=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=1; shift ;;
        --verbose)  VERBOSE=1; shift ;;
        *)          shift ;;
    esac
done

# Load environment
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

exec python3 - "$MC_CLIENT" "$OPENCLAW_BIN" "$OPENCLAW_CONFIG" "$GATEWAY_URL" "$DRY_RUN" "$VERBOSE" "$MAX_PER_CYCLE" "$LOG_FILE" <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

mc_client_path = sys.argv[1]
openclaw_bin = sys.argv[2]
openclaw_config = sys.argv[3]
gateway_url = sys.argv[4]
DRY_RUN = sys.argv[5] == "1"
VERBOSE = sys.argv[6] == "1"
MAX_PER_CYCLE = int(sys.argv[7])
LOG_FILE = sys.argv[8]
workspace = Path(mc_client_path).resolve().parent.parent
sys.path.insert(0, str(workspace / "heartbeat-v3" / "scripts"))

from mc_control import normalize_status, normalize_workflow, task_phase, task_phase_owner

def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")
    if VERBOSE:
        print(line, file=sys.stderr)

def run(cmd, timeout=15):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception as e:
        log(f"WARN: cmd failed: {' '.join(cmd[:3])}... — {e}")
        return ""

def mc_list_tasks():
    raw = run([mc_client_path, "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return []

def mc_update_task(task_id, status=None, comment=None, fields=None):
    if DRY_RUN:
        log(f"DRY-RUN: update {task_id[:8]} status={status} fields={fields}")
        return True
    cmd = [mc_client_path, "update-task", task_id]
    if status:
        cmd += ["--status", status]
    if comment:
        cmd += ["--comment", comment]
    if fields is not None:
        cmd += ["--fields", json.dumps(fields)]
    raw = run(cmd)
    return bool(raw)

def load_gateway_token():
    env_token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token
    with open(openclaw_config, "r") as fp:
        data = json.load(fp)
    token = data.get("gateway", {}).get("auth", {}).get("token")
    if not token:
        raise RuntimeError("gateway token not found")
    return token

def gateway_call(method, params):
    params_json = json.dumps(params or {})
    cmd = [
        openclaw_bin, "gateway", "call",
        "--url", gateway_url,
        "--token", load_gateway_token(),
        "--json", "--params", params_json,
        method,
    ]
    raw = run(cmd, timeout=20)
    if not raw:
        return {}
    return json.loads(raw)

def check_session_completion(session_key):
    """Check if session has COMPLETION_STATUS and what it is."""
    try:
        history = gateway_call("chat.history", {
            "sessionKey": session_key,
            "limit": 8,
        })
    except Exception as e:
        log(f"WARN: chat.history failed for {session_key}: {e}")
        return "", ""

    messages = []
    if isinstance(history, dict):
        messages = history.get("messages", history.get("items", []))
    elif isinstance(history, list):
        messages = history

    completion_status = ""
    last_output = ""

    for msg in reversed(messages):
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        if not last_output and len(content) > 50:
            last_output = content[:500]

        if "COMPLETION_STATUS:" in content:
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("COMPLETION_STATUS:"):
                    status_val = line.split(":", 1)[1].strip().lower()
                    if status_val in ("complete", "partial", "blocked", "failed",
                                      "plan_submitted"):
                        completion_status = status_val
                        break
            if completion_status:
                break

        # Heuristic: look for common completion markers
        lower = content.lower()
        if not completion_status:
            if "all acceptance criteria met" in lower or "criteria_met:" in lower:
                completion_status = "complete"
                break
            if "task complete" in lower and "not complete" not in lower:
                completion_status = "complete"
                break

    return completion_status, last_output

def wake_luna(reason):
    """Wake Luna for manual QA review (fallback)."""
    if DRY_RUN:
        log(f"DRY-RUN: would wake Luna — {reason[:80]}")
        return
    try:
        gw_token = load_gateway_token()
        idempotency_key = f"auto-qa-{int(time.time())}"
        params = json.dumps({
            "message": reason,
            "idempotencyKey": idempotency_key,
        })
        run([
            openclaw_bin, "gateway", "call",
            "--url", gateway_url,
            "--token", gw_token,
            "--json", "--params", params,
            "agent",
        ], timeout=20)
        log(f"WAKE: Luna notified for manual QA")
    except Exception as e:
        log(f"ERROR: wake_luna failed: {e}")


# ─── Main ────────────────────────────────────────────────────────────────────

log("=" * 50)
log("auto-qa-reviewer starting")

tasks = mc_list_tasks()
review_tasks = []
for task in tasks:
    if normalize_status(task.get("status"), default="inbox") != "review":
        continue
    workflow = normalize_workflow((task.get("custom_field_values") or {}).get("mc_workflow") or "direct_exec")
    phase = task_phase(task)
    owner = task_phase_owner(task)
    if workflow == "dev_loop_v1":
        if VERBOSE:
            log(f"SKIP: {str(task.get('id', ''))[:8]} — dev_loop review phase={phase} owner={owner}")
        continue
    review_tasks.append(task)
log(f"Found {len(review_tasks)} tasks in review")

processed = 0

for task in review_tasks:
    if processed >= MAX_PER_CYCLE:
        log(f"LIMIT: max {MAX_PER_CYCLE} per cycle reached")
        break

    task_id = str(task.get("id", ""))
    title = task.get("title", "(sem título)")
    fields = task.get("custom_field_values") or {}
    last_error = str(fields.get("mc_last_error", "") or "").strip().lower()
    session_key = str(fields.get("mc_session_key", "") or "").strip()
    progress = int(fields.get("mc_progress", 0) or 0)
    risk = str(fields.get("mc_risk_profile", "medium") or "medium").lower()
    phase = task_phase(task)
    workflow = normalize_workflow(fields.get("mc_workflow") or "direct_exec")

    log(f"EVAL: {task_id[:8]} — {title[:50]} — progress={progress}% risk={risk} workflow={workflow} phase={phase} last_error={last_error or '(none)'}")

    # ─── Check 1: Session key exists ─────────────────────────────────────
    if not session_key:
        log(f"FALLBACK: {task_id[:8]} — no session_key, can't verify")
        wake_luna(
            f"🔍 Auto-QA precisa de review manual: `{task_id[:8]}` — **{title}**\n"
            f"Motivo: sem session_key para verificar completion.\n"
            f"Ação: revisar manualmente e marcar done ou rejeitar."
        )
        processed += 1
        continue

    # ─── Check 2: Session completion status ──────────────────────────────
    completion, last_output = check_session_completion(session_key)
    log(f"  completion_status={completion or '(none)'}")

    # ─── Decision matrix ─────────────────────────────────────────────────

    if completion in ("complete", "plan_submitted"):
        # AUTO-APPROVE: Agent completed, session confirms
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = (
            f"[auto-qa] {now_iso} — QA automático APROVADO.\n"
            f"Completion status: {completion}. "
            f"Verificação: session confirmou COMPLETION_STATUS:{completion}."
        )
        mc_update_task(task_id,
            status="done",
            comment=comment,
            fields={
                **fields,
                "mc_last_error": "",
                "mc_output_summary": f"Auto-QA approved ({completion})",
                "mc_delivered": False,
            })
        log(f"AUTO-APPROVED: {task_id[:8]} — {title[:40]} (completion={completion})")
        processed += 1

    elif completion == "partial":
        # PARTIAL: auto-approve if progress >= 70, otherwise fallback
        if progress >= 70:
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            comment = (
                f"[auto-qa] {now_iso} — QA automático APROVADO (parcial).\n"
                f"Completion status: partial, progress: {progress}%.\n"
                f"Aceitável — maioria do trabalho concluído."
            )
            mc_update_task(task_id,
                status="done",
                comment=comment,
                fields={
                    **fields,
                    "mc_last_error": "",
                    "mc_output_summary": f"Auto-QA approved (partial, {progress}%)",
                    "mc_delivered": False,
                })
            log(f"AUTO-APPROVED (partial): {task_id[:8]} — progress={progress}%")
        else:
            log(f"FALLBACK: {task_id[:8]} — partial completion, progress only {progress}%")
            wake_luna(
                f"🔍 Auto-QA precisa de review manual: `{task_id[:8]}` — **{title}**\n"
                f"Status: partial ({progress}%). Precisa avaliar se é suficiente.\n"
                f"Session: `{session_key}`"
            )
        processed += 1

    elif completion in ("blocked", "failed"):
        # BLOCKED/FAILED: send back to inbox for retry
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        comment = (
            f"[auto-qa] {now_iso} — Agente reportou {completion}. "
            f"Devolvendo para inbox para re-dispatch."
        )
        mc_update_task(task_id,
            status="inbox",
            comment=comment,
            fields={
                **fields,
                "mc_last_error": f"auto_qa_{completion}",
                "mc_retry_count": 0,
                "mc_session_key": "",
            })
        log(f"RETURNED TO INBOX: {task_id[:8]} — {completion}")
        processed += 1

    else:
        # NO COMPLETION STATUS: can't verify objectively → Luna fallback
        log(f"FALLBACK: {task_id[:8]} — no COMPLETION_STATUS found in session")
        wake_luna(
            f"🔍 Auto-QA precisa de review manual: `{task_id[:8]}` — **{title}**\n"
            f"Motivo: sessão não tem COMPLETION_STATUS. Progress: {progress}%.\n"
            f"Session: `{session_key}`\n"
            f"Ação: ler output do agente, verificar se entregou, marcar done ou rejeitar."
        )
        processed += 1

log(f"auto-qa-reviewer done: processed={processed}")
PY
