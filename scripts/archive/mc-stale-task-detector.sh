# DEPRECATED: 2026-03-03 01:07:00 UTC
# Reason: absorbed by heartbeat-v3 Phase 5.5 (detect_stale_and_completions)
# Original location: /home/openclaw/.openclaw/workspace/scripts/mc-stale-task-detector.sh
#
#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-stale-task-detector.sh — Detects tasks stuck in active states
#
# Two checks:
#   1. Completion pending QA: session_key set, session dead, status ≠ done
#   2. Orphan task: active status, no session_key at all
#
# Writes alerts to /tmp/.mc-stale-tasks.txt for heartbeat pickup.
# Runs via cron every 10 minutes.
##############################################################################

set +euo pipefail; source ~/.bashrc 2>/dev/null; set -euo pipefail
MC_API_TOKEN="${MC_API_TOKEN:-}"
MC_BOARD="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
MC_BASE="http://localhost:8000/api/v1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="/tmp/.mc-stale-task-detector-state.json"
ALERT_FILE="/tmp/.mc-stale-tasks.txt"
BLOCKLIST_FILE="${SCRIPT_DIR}/../config/heartbeat-blocklist.json"

if [ -z "$MC_API_TOKEN" ]; then
  echo "MC_API_TOKEN not set" >&2; exit 1
fi

# Fetch MC tasks
TASKS_FILE=$(mktemp)
trap "rm -f $TASKS_FILE" EXIT
curl -sf -H "Authorization: Bearer $MC_API_TOKEN" \
  "${MC_BASE}/boards/${MC_BOARD}/tasks" > "$TASKS_FILE" 2>/dev/null || echo '{"items":[]}' > "$TASKS_FILE"

# Fetch live sessions
SESSIONS_FILE=$(mktemp)
trap "rm -f $TASKS_FILE $SESSIONS_FILE" EXIT
curl -sf "http://localhost:18789/api/sessions?limit=200" > "$SESSIONS_FILE" 2>/dev/null || echo '[]' > "$SESSIONS_FILE"

# Analyze
python3 - "$TASKS_FILE" "$SESSIONS_FILE" "$STATE_FILE" "$ALERT_FILE" "$BLOCKLIST_FILE" << 'PYEOF'
import json, os, sys
from datetime import datetime

tasks_file, sessions_file, state_file, alert_file, blocklist_file = sys.argv[1:6]

with open(tasks_file) as f:
    tasks = json.load(f).get("items", [])

# Parse live sessions
live_sessions = set()
try:
    with open(sessions_file) as f:
        sess_data = json.load(f)
        if isinstance(sess_data, list):
            for s in sess_data:
                live_sessions.add(s.get("key", s.get("sessionKey", "")))
        elif isinstance(sess_data, dict):
            for s in sess_data.get("sessions", sess_data.get("items", [])):
                live_sessions.add(s.get("key", s.get("sessionKey", "")))
except:
    pass

# Load blocklist
blocklist = set()
try:
    with open(blocklist_file) as f:
        bl = json.load(f)
        blocklist = set(bl.get("blocked_task_ids", []))
except:
    pass

# Load previous state
alerted = set()
try:
    with open(state_file) as f:
        alerted = set(json.load(f).get("alerted_ids", []))
except:
    pass

ACTIVE_STATUSES = {"in_progress", "review", "needs_approval"}
alerts = []

for t in tasks:
    status = t.get("status", "")
    if status not in ACTIVE_STATUSES:
        continue
    
    tid = t.get("id", "")
    short_id = tid[:8]
    title = t.get("title", "?")[:55]
    custom = t.get("custom_field_values", {}) or {}
    session_key = custom.get("mc_session_key", "") or ""
    
    # Skip blocklisted tasks
    if tid in blocklist or short_id in blocklist:
        continue
    
    # Skip already alerted
    if tid in alerted:
        continue
    
    # Check 1: Has session but session is dead → completion pending QA
    if session_key and session_key not in live_sessions:
        alerts.append({
            "type": "completion_pending",
            "id": short_id,
            "full_id": tid,
            "title": title,
            "status": status,
            "session": session_key[:40],
            "msg": f"🔴 `{short_id}` **{title}** ({status}): session morta, QA pendente"
        })
    
    # Check 2: No session at all → orphan
    elif not session_key and status in {"in_progress", "review"}:
        alerts.append({
            "type": "orphan",
            "id": short_id,
            "full_id": tid,
            "title": title,
            "status": status,
            "session": "",
            "msg": f"🟡 `{short_id}` **{title}** ({status}): sem executor (task órfã)"
        })

# Update state
new_alerted = alerted | {a["full_id"] for a in alerts}
with open(state_file, "w") as f:
    json.dump({
        "alerted_ids": list(new_alerted),
        "last_check": datetime.utcnow().isoformat(),
        "alerts_count": len(alerts)
    }, f)

# Write alert file
if alerts:
    header = f"⚠️ MC Stale Tasks: {len(alerts)} task(s) precisam de atenção\n"
    body = "\n".join(a["msg"] for a in alerts)
    footer = "\nUse QA Review Protocol ou re-spawn conforme necessário."
    with open(alert_file, "w") as f:
        f.write(header + body + footer)
    print(header + body)
else:
    # Clear old alerts
    try:
        os.remove(alert_file)
    except:
        pass
    print("OK — no stale tasks")
PYEOF
