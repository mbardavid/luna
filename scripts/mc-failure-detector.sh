#!/usr/bin/env bash
# mc-failure-detector.sh — Detect subagent failures and notify Discord
# Runs every 5min via cron. Checks MC tasks in_progress whose sessions have ended.
# Unlike watchdog (which moves tasks to review), this NOTIFIES Luna immediately.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${MC_CLIENT:-${SCRIPT_DIR}/mc-client.sh}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
GATEWAY_URL="${MC_GATEWAY_URL:-ws://127.0.0.1:18789}"
STATE_FILE="${MC_FAILURE_STATE:-/tmp/.mc-failure-detector-state.json}"
DISCORD_CHANNEL="${MC_NOTIFY_CHANNEL:-1476255906894446644}"

exec python3 - "$MC_CLIENT" "$OPENCLAW_BIN" "$OPENCLAW_CONFIG" "$GATEWAY_URL" "$STATE_FILE" "$DISCORD_CHANNEL" "$@" <<'PY'
import json
import os
import subprocess
import sys
import time

mc_client_path = sys.argv[1]
openclaw_bin = sys.argv[2]
openclaw_config = sys.argv[3]
gateway_url = sys.argv[4]
state_file = sys.argv[5]
discord_channel = sys.argv[6]


def run(cmd):
    cp = subprocess.run(cmd, text=True, capture_output=True)
    if cp.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\n{cp.stdout}\n{cp.stderr}")
    return cp.stdout.strip()


def load_state():
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                return json.load(f)
        except Exception:
            pass
    return {"notified": {}}


def save_state(state):
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


def load_gateway_token():
    env_token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token
    with open(openclaw_config, "r", encoding="utf-8") as fp:
        data = json.load(fp)
    token = data.get("gateway", {}).get("auth", {}).get("token")
    if not token:
        raise RuntimeError(f"gateway token not found in {openclaw_config}")
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
    raw = run(cmd)
    return json.loads(raw) if raw else {}


def mc_list_tasks():
    raw = run([mc_client_path, "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return []


def send_discord(message):
    """Send notification via gateway messaging."""
    try:
        gateway_call("chat.send", {
            "channel": "discord",
            "target": discord_channel,
            "message": message,
        })
        return True
    except Exception as e:
        print(f"[failure-detector] Discord send failed: {e}", file=sys.stderr)
        return False


# Get active sessions
try:
    session_state = gateway_call("sessions.list", {})
except Exception as e:
    print(f"[failure-detector] sessions.list failed: {e}", file=sys.stderr)
    sys.exit(1)

session_candidates = []
if isinstance(session_state, dict):
    session_candidates = session_state.get("sessions", [])
elif isinstance(session_state, list):
    session_candidates = session_state
sessions_by_key = {
    item.get("key"): item
    for item in (session_candidates or [])
    if isinstance(item, dict) and item.get("key")
}

# Get MC tasks
try:
    tasks = mc_list_tasks()
except Exception as e:
    print(f"[failure-detector] MC list failed: {e}", file=sys.stderr)
    sys.exit(1)

state = load_state()
notified = state.get("notified", {})
now_ms = int(time.time() * 1000)
failures_found = []

for task in tasks:
    status = str(task.get("status", "")).lower()
    if status != "in_progress":
        continue

    task_id = task.get("id", "")
    fields = task.get("custom_field_values") or {}
    session_key = str(fields.get("mc_session_key", "") or "").strip()

    if not session_key:
        continue

    # Check if session is still active
    if session_key in sessions_by_key:
        # Session exists and is active — check if it's in error/failed state
        session = sessions_by_key[session_key]
        session_status = str(session.get("status", "")).lower()
        if session_status in ("failed", "error", "ended"):
            pass  # Fall through to notification
        else:
            continue  # Session is alive, skip

    # Session not found in active list OR in failed state = dead session
    # Check if we already notified for this task
    if task_id in notified:
        last_notified = notified[task_id].get("at", 0)
        # Don't re-notify within 30 minutes
        if now_ms - last_notified < 30 * 60 * 1000:
            continue

    title = task.get("title", "(sem título)")
    agent = str(task.get("assigned_agent_id", "unknown") or "unknown")[:8]
    risk_profile = str(fields.get("mc_risk_profile", "unknown") or "unknown")
    loop_id = str(fields.get("mc_loop_id", "") or "")
    priority = str(task.get("priority", "medium") or "medium")

    failures_found.append({
        "task_id": task_id,
        "title": title,
        "session_key": session_key,
        "agent": agent,
        "risk_profile": risk_profile,
        "loop_id": loop_id,
        "priority": priority,
    })

if failures_found:
    lines = ["⚠️ **Failure Detector** — subagent(s) falharam com tasks abertas no MC:\n"]
    for f in failures_found:
        risk_tag = f"[{f['risk_profile'].upper()}]" if f['risk_profile'] != 'unknown' else ''
        loop_tag = f" loop:`{f['loop_id'][:12]}`" if f['loop_id'] else ''
        lines.append(f"• `{f['task_id'][:8]}` — **{f['title']}** {risk_tag} (sessão morta: `{f['session_key'][-12:]}`){loop_tag}")
        notified[f["task_id"]] = {"at": now_ms, "session": f["session_key"], "risk": f["risk_profile"]}

    lines.append("\nAção necessária: investigar e re-spawnar ou marcar como failed.")
    if any(f["risk_profile"] in ("high", "critical") for f in failures_found):
        lines.append("🔴 **ATENÇÃO**: falha(s) com risco alto/critical detectada(s). Escalonar para cto-ops/Luna.")
    message = "\n".join(lines)

    if send_discord(message):
        print(f"[failure-detector] Notified {len(failures_found)} failure(s)")
    else:
        print(f"[failure-detector] Failed to notify, {len(failures_found)} failure(s) detected")

    state["notified"] = notified
    save_state(state)
else:
    print("[failure-detector] No failures detected")
PY
