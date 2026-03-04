#!/usr/bin/env bash
# mc-fast-dispatch.sh — Direct task dispatch without waiting for heartbeat
#
# Replaces the slow heartbeat→queue→Luna→spawn chain with:
#   bash → openclaw agent → target agent processes task directly
#
# Usage:
#   mc-fast-dispatch.sh --agent luan --task "Fix bug in X" --title "Fix X"
#   mc-fast-dispatch.sh --from-mc <task_id>     # dispatch a MC inbox task
#   mc-fast-dispatch.sh --from-queue <file>      # dispatch a pending queue file
#
# Flow:
#   1. Read task spec (from args, MC, or queue file)
#   2. Create MC card if not exists
#   3. Send task directly to target agent via `openclaw agent`
#   4. Update MC with session key
#   5. Notify Discord
#
# Cost: ~15K tokens per dispatch (target agent context load)
# Speed: <10 seconds (vs 10-20 minutes with heartbeat)
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
LOG_FILE="$WORKSPACE/logs/fast-dispatch.log"
RATE_STATE="/tmp/.fast-dispatch-rate.json"
MAX_DISPATCHES_PER_HOUR=8

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

# Parse args
AGENT=""
TASK=""
TITLE=""
MC_TASK_ID=""
QUEUE_FILE=""
TIMEOUT=600
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agent)      AGENT="$2"; shift 2 ;;
        --task)       TASK="$2"; shift 2 ;;
        --title)      TITLE="$2"; shift 2 ;;
        --from-mc)    MC_TASK_ID="$2"; shift 2 ;;
        --from-queue) QUEUE_FILE="$2"; shift 2 ;;
        --timeout)    TIMEOUT="$2"; shift 2 ;;
        --dry-run)    DRY_RUN=1; shift ;;
        *)            echo "Unknown: $1" >&2; exit 1 ;;
    esac
done

# ─── Load task from MC ───────────────────────────────────────────────────────

MC_API_TOKEN="${MC_API_TOKEN:-}"

if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    log "Loading task from MC: $MC_TASK_ID"
    # Save CLI values — MC data only fills gaps, never overwrites CLI args
    CLI_AGENT="$AGENT"
    CLI_TASK="$TASK"
    CLI_TITLE="$TITLE"
    MC_DATA=$(curl -s "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
        -H "Authorization: Bearer $MC_API_TOKEN" 2>/dev/null)

    if [ -n "$MC_DATA" ]; then
        _MC_AGENT=$(echo "$MC_DATA" | python3 -c "
import json,sys
t = json.load(sys.stdin)
# Map agent IDs to openclaw agent names
agent_map = {'ccd2e6d0': 'luan', 'ad3cf364': 'crypto-sage', '70bd8378': 'main', 'b66bda98': 'quant-strategist'}
aid = t.get('assigned_agent_id', '')
print(agent_map.get(aid, aid))
" 2>/dev/null) || _MC_AGENT=""
        _MC_TITLE=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('title',''))" 2>/dev/null) || _MC_TITLE=""
        _MC_TASK=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description',''))" 2>/dev/null) || _MC_TASK=""
        # CLI args take priority over MC data
        [ -z "$CLI_AGENT" ] && AGENT="${_MC_AGENT}"
        [ -z "$CLI_TITLE" ] && TITLE="${_MC_TITLE}"
        [ -z "$CLI_TASK" ]  && TASK="${_MC_TASK}"

        # Extract rejection feedback and authorization status
        MC_REJECTION_FEEDBACK=$(echo "$MC_DATA" | python3 -c "
import json,sys
t = json.load(sys.stdin)
fields = t.get('custom_field_values') or {}
print(fields.get('mc_rejection_feedback', ''))
" 2>/dev/null) || MC_REJECTION_FEEDBACK=""

        MC_AUTH_STATUS=$(echo "$MC_DATA" | python3 -c "
import json,sys
t = json.load(sys.stdin)
fields = t.get('custom_field_values') or {}
print(fields.get('mc_authorization_status', ''))
" 2>/dev/null) || MC_AUTH_STATUS=""

        # Prepend feedback/authorization context to TASK
        EXTRA_CONTEXT=""
        if [ -n "$MC_REJECTION_FEEDBACK" ]; then
            EXTRA_CONTEXT="## ⚠️ PREVIOUS REVIEW FEEDBACK (MUST ADDRESS)
${MC_REJECTION_FEEDBACK}

**You MUST address all points above before reporting done.**

"
        fi

        if [ "$MC_AUTH_STATUS" = "authorized" ]; then
            PLAN_FILE="$WORKSPACE/plans/${MC_TASK_ID}.md"
            PLAN_CONTENT=""
            if [ -f "$PLAN_FILE" ]; then
                PLAN_CONTENT=$(cat "$PLAN_FILE")
            fi
            EXTRA_CONTEXT="${EXTRA_CONTEXT}## ✅ AUTHORIZED — Proceed to Implementation
This task plan was reviewed and authorized by Luna. Skip Steps 1-3, start at Step 4.

### Approved Plan
${PLAN_CONTENT:-No plan file found.}

"
        elif [ "$MC_AUTH_STATUS" = "counter_review" ]; then
            EXTRA_CONTEXT="${EXTRA_CONTEXT}## 🔄 COUNTER-REVIEW — Revise Plan
Luna reviewed your plan and requests changes. See feedback above.
Revise your plan and re-submit for authorization (max 2 cycles).

"
        fi

        if [ -n "$EXTRA_CONTEXT" ]; then
            TASK="${EXTRA_CONTEXT}${TASK}"
        fi
    fi
fi

# ─── Load task from queue file ───────────────────────────────────────────────

if [ -n "$QUEUE_FILE" ] && [ -f "$QUEUE_FILE" ]; then
    log "Loading task from queue file: $QUEUE_FILE"
    AGENT=$(python3 -c "import json; print(json.load(open('$QUEUE_FILE')).get('agent','main'))" 2>/dev/null)
    TITLE=$(python3 -c "import json; print(json.load(open('$QUEUE_FILE')).get('title',''))" 2>/dev/null)
    TASK=$(python3 -c "import json; print(json.load(open('$QUEUE_FILE')).get('context',{}).get('description',''))" 2>/dev/null)
    MC_TASK_ID=$(python3 -c "import json; print(json.load(open('$QUEUE_FILE')).get('task_id',''))" 2>/dev/null)
fi

# ─── Validate ────────────────────────────────────────────────────────────────

if [ -z "$AGENT" ] || [ -z "$TASK" ]; then
    echo "ERROR: --agent and --task are required (or --from-mc/--from-queue)" >&2
    exit 1
fi

[ -z "$TITLE" ] && TITLE="Fast dispatch: $(echo "$TASK" | head -c 50)"

log "Dispatching to $AGENT: $TITLE"

# ─── Rate limit ──────────────────────────────────────────────────────────────

DISPATCH_COUNT=0
if [ -f "$RATE_STATE" ]; then
    DISPATCH_COUNT=$(python3 -c "
import json, time
with open('$RATE_STATE') as f:
    state = json.load(f)
cutoff = time.time() - 3600
recent = [t for t in state.get('timestamps', []) if t > cutoff]
print(len(recent))
" 2>/dev/null) || DISPATCH_COUNT=0
fi

if [ "$DISPATCH_COUNT" -ge "$MAX_DISPATCHES_PER_HOUR" ]; then
    log "RATE LIMITED: $DISPATCH_COUNT/$MAX_DISPATCHES_PER_HOUR per hour"
    echo "Rate limited" >&2
    exit 1
fi

# ─── Dry run ─────────────────────────────────────────────────────────────────

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN: would dispatch to $AGENT"
    echo "  Title: $TITLE"
    echo "  Task: $(echo "$TASK" | head -c 200)"
    echo "  MC Task ID: ${MC_TASK_ID:-none}"
    exit 0
fi

# ─── Update MC status to in_progress (only for non-review tasks) ─────────────
# Review tasks must stay in "review" status — QA is handled by Luna's main session.
# Moving reviews to in_progress creates orphan tasks with no executor.

if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    # Check current task status before changing it
    _CURRENT_STATUS=$(curl -s "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
        -H "Authorization: Bearer $MC_API_TOKEN" 2>/dev/null \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null) || _CURRENT_STATUS=""

    if [ "$_CURRENT_STATUS" = "review" ]; then
        log "MC task $MC_TASK_ID stays in review (review tasks are not moved to in_progress)"
    else
        curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
            -H "Authorization: Bearer $MC_API_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"status":"in_progress"}' > /dev/null 2>&1 || true
        log "MC task $MC_TASK_ID → in_progress"
    fi
fi

# ─── Dispatch via openclaw agent ─────────────────────────────────────────────

# Use dispatcher agent (flash) to spawn the target agent
DISPATCH_MSG="DISPATCH agent=$AGENT
---
$TASK"

log "Sending to dispatcher → $AGENT (timeout: ${TIMEOUT}s)..."

RESULT=$($OPENCLAW_BIN agent \
    --agent "dispatcher" \
    --message "$DISPATCH_MSG" \
    --timeout "$TIMEOUT" \
    --json 2>&1) || {
    log "DISPATCH FAILED: $RESULT"
    # Move MC task back to inbox on failure
    if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
        curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
            -H "Authorization: Bearer $MC_API_TOKEN" \
            -H "Content-Type: application/json" \
            -d '{"status":"inbox"}' > /dev/null 2>&1 || true
    fi
    exit 1
}

# Parse result
STATUS=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
SESSION_ID=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result',{}).get('meta',{}).get('agentMeta',{}).get('sessionId',''))" 2>/dev/null)
DURATION=$(echo "$RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result',{}).get('meta',{}).get('durationMs',0))" 2>/dev/null)

log "Dispatch complete: status=$STATUS session=$SESSION_ID duration=${DURATION}ms"

# ─── Update MC with session key ──────────────────────────────────────────────

if [ -n "$MC_TASK_ID" ] && [ -n "$SESSION_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
        -H "Authorization: Bearer $MC_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"custom_field_values\":{\"mc_session_key\":\"$SESSION_ID\"}}" > /dev/null 2>&1 || true
    log "MC task $MC_TASK_ID linked session_key=$SESSION_ID"
fi

# ─── Update rate limit state ─────────────────────────────────────────────────

python3 -c "
import json, time
state = {'timestamps': []}
try:
    with open('$RATE_STATE') as f:
        state = json.load(f)
except: pass
cutoff = time.time() - 3600
state['timestamps'] = [t for t in state.get('timestamps', []) if t > cutoff]
state['timestamps'].append(time.time())
with open('$RATE_STATE', 'w') as f:
    json.dump(state, f)
" 2>/dev/null || true

# ─── Move queue file to done ─────────────────────────────────────────────────

if [ -n "$QUEUE_FILE" ] && [ -f "$QUEUE_FILE" ]; then
    DONE_DIR="$(dirname "$(dirname "$QUEUE_FILE")")/done"
    mkdir -p "$DONE_DIR"
    mv "$QUEUE_FILE" "$DONE_DIR/" 2>/dev/null || true
fi

# ─── Output ──────────────────────────────────────────────────────────────────

echo "{\"status\":\"$STATUS\",\"agent\":\"$AGENT\",\"session_id\":\"$SESSION_ID\",\"duration_ms\":$DURATION,\"mc_task_id\":\"${MC_TASK_ID:-}\"}"
