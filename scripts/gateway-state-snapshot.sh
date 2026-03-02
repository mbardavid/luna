#!/usr/bin/env bash
# gateway-state-snapshot.sh — Captures running state before restart
#
# Saves a JSON snapshot of everything that needs recovery:
#   - Active subagent sessions
#   - MC in_progress tasks
#   - Running background processes (PMM, dashboard, etc.)
#   - Active cron jobs state
#
# Called by gateway-safe-restart.sh BEFORE restart.
# Output: /tmp/.gateway-pre-restart-state.json
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
STATE_FILE="/tmp/.gateway-pre-restart-state.json"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
LOG_FILE="$WORKSPACE/logs/gateway-recovery.log"

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] SNAPSHOT: $1" >> "$LOG_FILE"; }

log "Starting pre-restart snapshot..."

python3 << 'PYEOF'
import json, subprocess, os, time

state = {
    "timestamp": time.time(),
    "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "reason": os.environ.get("RESTART_REASON", "unknown"),
    "processes": [],
    "mc_in_progress": [],
    "subagent_sessions": [],
}

# 1. Capture running background processes
pmm_patterns = [
    ("pmm", "production_runner"),
    ("dashboard", "dashboard.server"),
]
import re
try:
    ps_out = subprocess.check_output(
        ["ps", "aux"], text=True, timeout=5
    )
    for line in ps_out.strip().split("\n"):
        for name, pattern in pmm_patterns:
            if pattern in line and "grep" not in line:
                parts = line.split()
                pid = int(parts[1])
                # Extract the command
                cmd = " ".join(parts[10:])
                state["processes"].append({
                    "name": name,
                    "pid": pid,
                    "cmd": cmd,
                    "cwd": os.readlink(f"/proc/{pid}/cwd") if os.path.exists(f"/proc/{pid}/cwd") else None,
                })
except Exception as e:
    state["process_error"] = str(e)

# 2. Capture MC in_progress tasks
try:
    mc_url = os.environ.get("MC_API_URL", "http://localhost:8000")
    board_id = os.environ.get("MC_BOARD_ID", "0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47")
    token = os.environ.get("MC_API_TOKEN", "")
    if token:
        import urllib.request
        req = urllib.request.Request(
            f"{mc_url}/api/v1/boards/{board_id}/tasks",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            tasks = json.loads(resp.read()).get("items", [])
            for t in tasks:
                if t["status"] == "in_progress":
                    state["mc_in_progress"].append({
                        "task_id": t["id"],
                        "title": t.get("title", ""),
                        "agent": t.get("assigned_agent_id", ""),
                        "session_key": t.get("mc_session_key", ""),
                    })
except Exception as e:
    state["mc_error"] = str(e)

# 3. Capture active subagent sessions
try:
    result = subprocess.check_output(
        ["openclaw", "gateway", "call", "sessions.list", "--json", "--params", "{}"],
        text=True, timeout=10
    )
    sessions = json.loads(result)
    if isinstance(sessions, dict):
        sessions = sessions.get("sessions", [])
    for s in sessions:
        key = s.get("key", "")
        if "subagent" in key:
            state["subagent_sessions"].append({
                "key": key,
                "label": s.get("label", s.get("displayName", "")),
                "updated_at": s.get("updatedAt", 0),
                "model": s.get("model", ""),
            })
except Exception as e:
    state["sessions_error"] = str(e)

# 4. Capture PID files
pid_files = {}
pid_dir = os.path.expanduser("~/.openclaw/workspace/polymarket-mm/paper/data")
for f in os.listdir(pid_dir) if os.path.isdir(pid_dir) else []:
    if f.endswith(".pid"):
        try:
            with open(os.path.join(pid_dir, f)) as pf:
                pid_files[f] = int(pf.read().strip())
        except:
            pass
state["pid_files"] = pid_files

# Write snapshot
with open("/tmp/.gateway-pre-restart-state.json", "w") as f:
    json.dump(state, f, indent=2)

print(json.dumps({
    "processes": len(state["processes"]),
    "mc_in_progress": len(state["mc_in_progress"]),
    "subagent_sessions": len(state["subagent_sessions"]),
    "pid_files": len(state["pid_files"]),
}))
PYEOF

log "Snapshot saved to $STATE_FILE"
