#!/usr/bin/env bash
# mc-authorize-plan.sh — Luna approves or counter-reviews a Luan plan
#
# Flow:
#   authorize:      Sets mc_authorization_status=authorized, moves to in_progress, fast-dispatch
#   counter-review: Sets mc_authorization_status=counter_review, writes feedback, moves to in_progress
#
# Usage:
#   mc-authorize-plan.sh --task-id <id> --action authorize
#   mc-authorize-plan.sh --task-id <id> --action counter-review --feedback "changes needed"
#   mc-authorize-plan.sh --task-id <id> --action authorize --fast-dispatch
#   mc-authorize-plan.sh --task-id <id> --action authorize --dry-run
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-${SCRIPT_DIR}/../config/mission-control-ids.json}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
LOG_FILE="$WORKSPACE/logs/mc-authorize-plan.log"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
ORCH_STATE="$WORKSPACE/memory/orchestration-state.json"
MAX_COUNTER_REVIEW_CYCLES=2

mkdir -p "$(dirname "$LOG_FILE")" "$WORKSPACE/plans"
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
ACTION=""
FEEDBACK=""
FAST_DISPATCH=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --task-id)        TASK_ID="$2"; shift 2 ;;
        --action)         ACTION="$2"; shift 2 ;;
        --feedback)       FEEDBACK="$2"; shift 2 ;;
        --fast-dispatch)  FAST_DISPATCH=1; shift ;;
        --dry-run)        DRY_RUN=1; shift ;;
        *)                echo "Unknown: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$TASK_ID" ] || [ -z "$ACTION" ]; then
    echo "Usage: mc-authorize-plan.sh --task-id <id> --action authorize|counter-review [--feedback ...]" >&2
    exit 1
fi

if [ "$ACTION" != "authorize" ] && [ "$ACTION" != "counter-review" ]; then
    echo "ERROR: --action must be 'authorize' or 'counter-review'" >&2
    exit 1
fi

if [ "$ACTION" = "counter-review" ] && [ -z "$FEEDBACK" ]; then
    echo "ERROR: --feedback is required for counter-review" >&2
    exit 1
fi

log "=== Authorization action: $ACTION for task $TASK_ID ==="

# Check counter-review cycle count from orchestration-state
CURRENT_CYCLE=0
if [ -f "$ORCH_STATE" ]; then
    CURRENT_CYCLE=$(python3 -c "
import json
with open('$ORCH_STATE') as f:
    state = json.load(f)
# Find handoff by task_id match or loop_id
for lid, h in state.get('activeHandoffs', {}).items():
    if h.get('mc_task_id', '') == '$TASK_ID' or lid == '$TASK_ID':
        print(h.get('review_cycle', 1))
        break
else:
    print(1)
" 2>/dev/null) || CURRENT_CYCLE=1
fi

if [ "$ACTION" = "counter-review" ] && [ "$CURRENT_CYCLE" -ge "$MAX_COUNTER_REVIEW_CYCLES" ]; then
    log "ESCALATION: counter-review cycle $CURRENT_CYCLE >= max $MAX_COUNTER_REVIEW_CYCLES"
    log "Escalating to human review"
    NOTIFY_MSG="🚨 **Escalation** — task \`${TASK_ID:0:8}\` hit max counter-review cycles ($MAX_COUNTER_REVIEW_CYCLES). Needs human review."
    timeout 8 "$OPENCLAW_BIN" message send \
        --channel discord --target "$DISCORD_CHANNEL" --message "$NOTIFY_MSG" --json 2>/dev/null || true
    echo "{\"status\":\"escalated\",\"task_id\":\"$TASK_ID\",\"cycle\":$CURRENT_CYCLE}"
    exit 0
fi

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN: would $ACTION task $TASK_ID (cycle: $CURRENT_CYCLE)"
    [ -n "$FEEDBACK" ] && echo "  Feedback: $FEEDBACK"
    exit 0
fi

# Build PATCH payload based on action
if [ "$ACTION" = "authorize" ]; then
    PATCH_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'status': 'in_progress',
    'custom_field_values': {
        'mc_authorization_status': 'authorized',
        'mc_session_key': ''
    }
}))
")
    COMMENT="[luna-authorize] Plan AUTHORIZED. Luan may proceed to implementation."
    DISCORD_EMOJI="✅"
    DISCORD_ACTION="authorized"
else
    PATCH_PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'status': 'in_progress',
    'custom_field_values': {
        'mc_authorization_status': 'counter_review',
        'mc_rejection_feedback': $(python3 -c "import json; print(json.dumps('''$FEEDBACK'''))"),
        'mc_session_key': ''
    }
}))
")
    COMMENT="[luna-counter-review] Plan needs changes (cycle $((CURRENT_CYCLE + 1))/$MAX_COUNTER_REVIEW_CYCLES):

$FEEDBACK"
    DISCORD_EMOJI="🔄"
    DISCORD_ACTION="counter-review (cycle $((CURRENT_CYCLE + 1))/$MAX_COUNTER_REVIEW_CYCLES)"
fi

# Apply MC update
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PATCH \
    "$MC_API_URL/tasks/$TASK_ID" \
    -H "Authorization: Bearer $MC_TOKEN" \
    -H "Content-Type: application/json" \
    -d "$PATCH_PAYLOAD" 2>/dev/null)

if [ "$HTTP_CODE" -ge 200 ] && [ "$HTTP_CODE" -lt 300 ]; then
    log "MC task updated: action=$ACTION"
else
    log "ERROR: MC PATCH failed (HTTP $HTTP_CODE)"
    exit 1
fi

# Add comment
curl -s -X POST "$MC_API_URL/tasks/$TASK_ID/comments" \
    -H "Authorization: Bearer $MC_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{\"content\": $(python3 -c "import json; print(json.dumps('''$COMMENT'''))")}" > /dev/null 2>&1 || \
    log "WARN: comment post failed"

# Update orchestration-state.json
if [ -f "$ORCH_STATE" ]; then
    python3 -c "
import json
from datetime import datetime, timezone

with open('$ORCH_STATE') as f:
    state = json.load(f)

now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
action = '$ACTION'
task_id = '$TASK_ID'

# Find matching handoff
for lid, h in state.get('activeHandoffs', {}).items():
    if h.get('mc_task_id', '') == task_id or lid == task_id:
        if action == 'authorize':
            h['review_state'] = 'authorized'
        else:
            h['review_state'] = 'counter_review'
            h['review_cycle'] = h.get('review_cycle', 1) + 1
        h['lastUpdatedAt'] = now
        break

state['updatedAt'] = now

# Add to audit log
audit = state.setdefault('delegationAuditLog', [])
audit.append({
    'at': now,
    'action': action,
    'task_id': task_id,
    'actor': 'luna',
})

with open('$ORCH_STATE', 'w') as f:
    json.dump(state, f, indent=2, ensure_ascii=False)
" 2>/dev/null || log "WARN: orchestration-state update failed"
    log "Orchestration state updated"
fi

# Notify Discord
NOTIFY_MSG="$DISCORD_EMOJI **Plan ${DISCORD_ACTION}** — task \`${TASK_ID:0:8}\`"
[ -n "$FEEDBACK" ] && NOTIFY_MSG="$NOTIFY_MSG
**Feedback:** ${FEEDBACK:0:200}"

timeout 8 "$OPENCLAW_BIN" message send \
    --channel discord --target "$DISCORD_CHANNEL" --message "$NOTIFY_MSG" --json 2>/dev/null || \
    log "WARN: Discord notification failed"

# Optional fast-dispatch
if [ "$FAST_DISPATCH" -eq 1 ] && [ "$ACTION" = "authorize" ]; then
    log "Triggering fast-dispatch for authorized task"
    bash "$SCRIPT_DIR/mc-fast-dispatch.sh" --from-mc "$TASK_ID" 2>>"$LOG_FILE" || \
        log "WARN: fast-dispatch failed (will be picked up by heartbeat)"
fi

log "Authorization action complete: $ACTION"
echo "{\"status\":\"$ACTION\",\"task_id\":\"$TASK_ID\",\"cycle\":$((CURRENT_CYCLE + ([ "$ACTION" = "counter-review" ] && echo 1 || echo 0)))}"
