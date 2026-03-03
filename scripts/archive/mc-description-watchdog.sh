# DEPRECATED: 2026-03-03 01:07:00 UTC
# Reason: absorbed by heartbeat-v3 Phase 4.8 (check_description_quality)
# Original location: /home/openclaw/.openclaw/workspace/scripts/mc-description-watchdog.sh
#
#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-description-watchdog.sh — Detects MC tasks with poor descriptions
#
# Defense-in-depth: catches tasks created via ANY path that have
# descriptions too short or unstructured.
# Runs via cron. Writes violations to marker file for heartbeat to pick up.
##############################################################################

# Source env
set +euo pipefail; source ~/.bashrc 2>/dev/null; set -euo pipefail
MC_API_TOKEN="${MC_API_TOKEN:-}"
MC_BOARD="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
MC_BASE="http://localhost:8000/api/v1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ -z "$MC_API_TOKEN" ]; then
  echo "MC_API_TOKEN not set" >&2; exit 1
fi

# Fetch tasks to temp file
TASKS_FILE=$(mktemp)
trap "rm -f $TASKS_FILE" EXIT
curl -sf -H "Authorization: Bearer $MC_API_TOKEN" \
  "${MC_BASE}/boards/${MC_BOARD}/tasks" > "$TASKS_FILE" 2>/dev/null || echo '{"items":[]}' > "$TASKS_FILE"

# Run Python analysis
VIOLATIONS=$(python3 <<'PYEOF'
import json, os, sys
from datetime import datetime

TASKS_FILE = os.environ.get("TASKS_FILE", "/dev/null")
STATE_FILE = "/tmp/.mc-description-watchdog-state.json"
MIN_LENGTH = 200

with open(TASKS_FILE) as f:
    data = json.load(f)

alerted = set()
if os.path.exists(STATE_FILE):
    try:
        with open(STATE_FILE) as f:
            alerted = set(json.load(f).get("alerted_ids", []))
    except: pass

tasks = data.get("items", [])
active = {"inbox", "in_progress", "review", "needs_approval"}
markers = ["## ", "Objective", "Objetivo", "Context", "Contexto", "Criteria", "Problem", "Approach", "Plano"]
violations = []

for t in tasks:
    if t.get("status", "") not in active: continue
    tid = t.get("id", "")
    if tid in alerted: continue
    desc = t.get("description", "") or ""
    issues = []
    if len(desc) < MIN_LENGTH:
        issues.append(f"short ({len(desc)} chars)")
    if not any(m in desc for m in markers) and len(desc) < 500:
        issues.append("no structure")
    if issues:
        violations.append({"id": tid[:8], "full_id": tid, "title": t.get("title","?")[:50], "status": t.get("status","?"), "issues": ", ".join(issues)})

new_alerted = alerted | {v["full_id"] for v in violations}
with open(STATE_FILE, "w") as f:
    json.dump({"alerted_ids": list(new_alerted), "last_check": datetime.utcnow().isoformat(), "violations": len(violations)}, f)

print(json.dumps(violations))
PYEOF
)

if [ "$VIOLATIONS" != "[]" ] && [ -n "$VIOLATIONS" ]; then
  COUNT=$(echo "$VIOLATIONS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")
  ALERT=$(echo "$VIOLATIONS" | python3 -c "
import json,sys
vs = json.load(sys.stdin)
msg = f'⚠️ MC Description Quality: {len(vs)} task(s) with poor descriptions\n'
for v in vs:
    msg += f'• \`{v[\"id\"]}\` {v[\"title\"]} ({v[\"status\"]}): {v[\"issues\"]}\n'
msg += 'Fix: update with objective + approach + criteria (min 200 chars)'
print(msg)")
  echo "$ALERT" > /tmp/.mc-description-violations.txt
  mkdir -p "${SCRIPT_DIR}/../logs"
  echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $ALERT" >> "${SCRIPT_DIR}/../logs/mc-description-watchdog.log"
  echo "VIOLATIONS: $COUNT"
else
  rm -f /tmp/.mc-description-violations.txt
  echo "OK"
fi
