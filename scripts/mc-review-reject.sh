#!/usr/bin/env bash
# mc-review-reject.sh — Luna rejects a task with structured feedback
#
# Flow:
#   1. Writes mc_rejection_feedback to MC card
#   2. Clears mc_session_key (heartbeat treats as fresh dispatch)
#   3. Moves task to in_progress (Phase 5.5 detects stale → inbox → re-dispatch with feedback)
#   4. Adds structured comment to MC card
#   5. Notifies Discord
#
# Usage:
#   mc-review-reject.sh --task-id <id> --feedback "Motivo detalhado da rejeição"
#   mc-review-reject.sh --task-id <id> --feedback "..." --dry-run
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-${SCRIPT_DIR}/../config/mission-control-ids.json}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
LOG_FILE="$WORKSPACE/logs/mc-review-reject.log"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

# Load MC config
mc_cfg() {
    python3 -c "
import json, sys
with open('$MC_CONFIG_PATH') as f:
    cfg = json.load(f)
keys = '$1'.split('.')
v = cfg
for k in keys:
    v = v[k]
print(v)
" 2>/dev/null
}

MC_API_URL="$(mc_cfg api_url)"
MC_TOKEN="${MC_AUTH_TOKEN:-$(mc_cfg auth_token)}"

# Parse args
TASK_ID=""
FEEDBACK=""
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task-id)    TASK_ID="$2"; shift 2 ;;
        --feedback)   FEEDBACK="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        *)            echo "Unknown: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$TASK_ID" ] || [ -z "$FEEDBACK" ]; then
    echo "Usage: mc-review-reject.sh --task-id <id> --feedback \"reason\"" >&2
    exit 1
fi

log "=== Rejecting task $TASK_ID ==="
log "Feedback: $FEEDBACK"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN: would reject task $TASK_ID with feedback:"
    echo "  $FEEDBACK"
    exit 0
fi

# Step 1: Write rejection feedback + clear session key + move to in_progress
PATCH_PAYLOAD=$(python3 -c "
import json
payload = {
    'status': 'in_progress',
    'custom_field_values': {
        'mc_rejection_feedback': $(python3 -c "import json; print(json.dumps('$FEEDBACK'))"),
        'mc_session_key': ''
    }
}
print(json.dumps(payload))
" 2>/dev/null)

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
    "$MC_API_URL/tasks/$TASK_ID" \
    -H "Authorization: Bearer $MC_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PATCH_PAYLOAD" 2>/dev/null)

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    log "MC task updated: status=in_progress, feedback written, session_key cleared"
else
    log "ERROR: MC PATCH failed (HTTP $HTTP_CODE)"
    exit 1
fi

# Step 2: Add structured comment
COMMENT="[luna-review-reject] Task rejected with feedback:

$FEEDBACK

Action: session_key cleared, task moved to in_progress for re-dispatch with feedback context."

curl -s -X POST "$MC_API_URL/tasks/$TASK_ID/comments" \
    -H "Authorization: Bearer $MC_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\": $(python3 -c "import json; print(json.dumps('''$COMMENT'''))")}" > /dev/null 2>&1 || \
    log "WARN: comment post failed (non-fatal)"

# Step 3: Notify Discord
NOTIFY_MSG="❌ **Review Reject** — task \`${TASK_ID:0:8}\` rejeitada por Luna
**Feedback:** ${FEEDBACK:0:200}"

timeout 8 "$OPENCLAW_BIN" message send \
    --channel discord \
    --target "$DISCORD_CHANNEL" \
    --message "$NOTIFY_MSG" \
    --json 2>/dev/null || log "WARN: Discord notification failed"

log "Task $TASK_ID rejected successfully"
echo "{\"status\":\"rejected\",\"task_id\":\"$TASK_ID\"}"
