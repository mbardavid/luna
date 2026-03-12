#!/usr/bin/env bash
# mc-drain-bridge.sh — Bridge between mc-simple-drain queue and sessions_spawn.
#
# Purpose:
#   Consumes one item from queue/pending/ and triggers an isolated Luna session
#   to perform the atomic spawn via sessions_spawn (Claude, not Codex).
#
# Flow:
#   1. Check queue/pending/ for eligible items
#   2. Claim one item (atomic move to active/)
#   3. Inject an isolated cron session with structured SPAWN_REQUEST
#   4. The isolated session calls sessions_spawn, links mc_session_key
#
# Why not mc-fast-dispatch.sh?
#   mc-fast-dispatch uses `openclaw agent --agent <codex-agent>` which breaks
#   when the openai-codex OAuth token expires. This bridge routes through Claude
#   (sessions_spawn from main session) — independent of Codex auth.
#
# Designed to run as a cron job every 3 minutes.
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
V3_DIR="${HEARTBEAT_V3_DIR:-$WORKSPACE/heartbeat-v3}"
QUEUE_DIR="$V3_DIR/queue"
PENDING="$QUEUE_DIR/pending"
ACTIVE="$QUEUE_DIR/active"
DONE="$QUEUE_DIR/done"
FAILED="$QUEUE_DIR/failed"
LOG_FILE="$WORKSPACE/logs/mc-drain-bridge.log"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
LOCK_FILE="/tmp/mc-drain-bridge.lock"
MAX_ACTIVE_AGE_MINUTES=30
DRY_RUN="${MC_DRAIN_BRIDGE_DRY_RUN:-0}"

mkdir -p "$PENDING" "$ACTIVE" "$DONE" "$FAILED" "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# ─── Global lock ─────────────────────────────────────────────────────────────
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "SKIP: already running (lock held)"
    exit 0
fi

# ─── Source env (MC_API_TOKEN, etc.) ─────────────────────────────────────────
set +euo pipefail
source "$HOME/.bashrc" 2>/dev/null || true
set -euo pipefail

# ─── Recover stale active items ───────────────────────────────────────────────
for f in "$ACTIVE"/*.json; do
    [ -f "$f" ] || continue
    age_seconds=$(( $(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo "0") ))
    age_minutes=$(( age_seconds / 60 ))
    if [ "$age_minutes" -ge "$MAX_ACTIVE_AGE_MINUTES" ]; then
        fname=$(basename "$f")
        log "RECOVER: stuck active item → pending: $fname (${age_minutes}min)"
        mv "$f" "$PENDING/$fname" 2>/dev/null || true
    fi
done

# ─── Pick one pending item ────────────────────────────────────────────────────
ITEM="$(ls -1t "$PENDING"/*.json 2>/dev/null | head -1 || true)"
if [ -z "$ITEM" ] || [ ! -f "$ITEM" ]; then
    log "IDLE: no items in queue"
    exit 0
fi

FNAME=$(basename "$ITEM")
ACTIVE_ITEM="$ACTIVE/$FNAME"
log "BRIDGE: claiming $FNAME"

mv "$ITEM" "$ACTIVE_ITEM" 2>/dev/null || {
    log "BRIDGE: claim failed for $FNAME (race)"
    exit 0
}

# ─── Read queue item fields ───────────────────────────────────────────────────
TASK_ID=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('task_id',''))" 2>/dev/null)
AGENT=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('agent','luan'))" 2>/dev/null)
TITLE=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('title',''))" 2>/dev/null)
DESCRIPTION=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('context',{}).get('description','')[:600])" 2>/dev/null)
PRIORITY=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('priority','medium'))" 2>/dev/null)
ACCEPTANCE=$(python3 -c "import json; print(json.load(open('$ACTIVE_ITEM')).get('context',{}).get('acceptance_criteria','')[:300])" 2>/dev/null)

if [ -z "$TASK_ID" ] || [ -z "$TITLE" ]; then
    log "BRIDGE: invalid item $FNAME — missing task_id or title"
    mv "$ACTIVE_ITEM" "$FAILED/$FNAME" 2>/dev/null || true
    exit 1
fi

# ─── Build isolated session message ──────────────────────────────────────────
BRIDGE_MSG=$(cat <<EOF
ATOMIC_SPAWN_REQUEST
queue_file=$FNAME
task_id=$TASK_ID
agent=$AGENT
title=$TITLE
priority=$PRIORITY

description:
$DESCRIPTION

acceptance_criteria:
$ACCEPTANCE

Instructions (follow exactly):
1. Call sessions_spawn with:
   - task: the description above as the execution brief
   - label: "$TASK_ID"
   - agentId: "$AGENT"
   - mode: "run"
   - runtime: "subagent"
   - sandbox: "inherit"
   - timeoutSeconds: 1800
2. Capture childSessionKey from the result
3. Update MC task via:
   bash /home/openclaw/.openclaw/workspace/scripts/mc-client.sh update-task $TASK_ID --status in_progress --comment "[mc-drain-bridge] spawned via sessions_spawn, session=<childSessionKey>"
4. Update mc_session_key field:
   curl -s -X PATCH "\$MC_API_URL/api/v1/boards/\$MC_BOARD_ID/tasks/$TASK_ID" -H "Authorization: Bearer \$MC_API_TOKEN" -H "Content-Type: application/json" -d '{"custom_field_values":{"mc_session_key":"<childSessionKey>","mc_delivery_state":"linked"}}'
5. Mark queue item done by writing: BRIDGE_DONE session=<childSessionKey>
6. Reply ONLY with: BRIDGE_DONE session=<childSessionKey> task=$TASK_ID
EOF
)

log "BRIDGE: triggering isolated session for $TASK_ID → $AGENT"

if [ "$DRY_RUN" = "1" ]; then
    log "DRY-RUN: would trigger isolated cron session for $TASK_ID"
    mv "$ACTIVE_ITEM" "$PENDING/$FNAME" 2>/dev/null || true
    exit 0
fi

# ─── Trigger isolated Luna session ───────────────────────────────────────────
set +e
CRON_RESULT=$("$OPENCLAW_BIN" cron add \
    --agent main \
    --session isolated \
    --message "$BRIDGE_MSG" \
    --at "2m" \
    --delete-after-run \
    --name "drain-bridge-${TASK_ID:0:8}" \
    --timeout-seconds 300 \
    --light-context \
    --json 2>&1)
CRON_RC=$?
set -e

if [ "$CRON_RC" -ne 0 ]; then
    log "BRIDGE: cron add failed for $TASK_ID (rc=$CRON_RC): $CRON_RESULT"
    mv "$ACTIVE_ITEM" "$PENDING/$FNAME" 2>/dev/null || true
    exit 1
fi

CRON_ID=$(echo "$CRON_RESULT" | python3 -c "import json,sys; d=sys.stdin.read(); i=d.find('{'); print(json.loads(d[i:]).get('id','?') if i>=0 else '?')" 2>/dev/null || echo "?")
log "BRIDGE: isolated session scheduled cron_id=$CRON_ID for task=${TASK_ID:0:8}"

# Mark active item with pending bridge info (watchdog will clean up if bridge never confirms)
python3 - "$ACTIVE_ITEM" "$CRON_ID" <<'PY'
import json, sys
path, cron_id = sys.argv[1], sys.argv[2]
try:
    with open(path) as f: data = json.load(f)
    data["bridge_cron_id"] = cron_id
    data["bridge_triggered_at"] = __import__('datetime').datetime.utcnow().isoformat()
    with open(path, "w") as f: json.dump(data, f, indent=2)
except Exception: pass
PY

exit 0
