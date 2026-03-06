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
#   4. Update MC with target session key (never dispatcher session)
#   5. Notify Discord
#
# Cost: ~15K tokens per dispatch (target agent context load)
# Speed: <10 seconds (vs 10-20 minutes with heartbeat)

set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
TOPOLOGY_HELPER="$WORKSPACE/scripts/agent_runtime_topology.py"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
LOG_FILE="$WORKSPACE/logs/fast-dispatch.log"
RATE_STATE="/tmp/.fast-dispatch-rate.json"
METRICS_FILE="$WORKSPACE/state/control-loop-metrics.json"
MAX_DISPATCHES_PER_HOUR=8

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

resolve_agent_name() {
    local ref="${1:-}"
    if [ -z "$ref" ]; then
        echo ""
        return
    fi
    if [ -f "$TOPOLOGY_HELPER" ]; then
        local resolved=""
        resolved="$(python3 "$TOPOLOGY_HELPER" assigned-agent "$ref" 2>/dev/null || true)"
        if [ -n "$resolved" ]; then
            echo "$resolved"
            return
        fi
    fi
    echo "$ref"
}

canonicalize_agent() {
    local ref="${1:-}"
    if [ -z "$ref" ]; then
        echo ""
        return
    fi
    if [ -f "$TOPOLOGY_HELPER" ]; then
        local resolved=""
        resolved="$(python3 "$TOPOLOGY_HELPER" normalize "$ref" 2>/dev/null || true)"
        if [ -n "$resolved" ]; then
            echo "$resolved"
            return
        fi
    fi
    echo "$ref"
}

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

MC_API_TOKEN="${MC_API_TOKEN:-}"

rollback_mc_task() {
    local task_id="$1"
    local message="$2"
    if [ -z "$task_id" ] || [ -z "$MC_API_TOKEN" ]; then
        return 0
    fi
    local payload
    payload=$(python3 - "$task_id" "$message" <<'PY'
import json
import sys
_ = sys.argv[1]
message = sys.argv[2]
print(json.dumps({
    "status": "inbox",
    "comment": f"[heartbeat-v3] queue dispatch failed: {message}",
    "fields": {
        "mc_last_error": message,
        "mc_session_key": "",
    },
}))
PY
)
    curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$task_id" \
        -H "Authorization: Bearer $MC_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$payload" > /dev/null 2>&1 || true
}

# ─── Load task from MC ───────────────────────────────────────────────────────

if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    log "Loading task from MC: $MC_TASK_ID"
    # Save CLI values — MC data only fills gaps, never overwrites CLI args
    CLI_AGENT="$AGENT"
    CLI_TASK="$TASK"
    CLI_TITLE="$TITLE"
    MC_DATA=$(curl -s "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
        -H "Authorization: Bearer $MC_API_TOKEN" 2>/dev/null)

    if [ -n "$MC_DATA" ]; then
        _MC_ASSIGNED_ID=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('assigned_agent_id',''))" 2>/dev/null) || _MC_ASSIGNED_ID=""
        _MC_AGENT="$(resolve_agent_name "$_MC_ASSIGNED_ID")"
        _MC_TITLE=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('title',''))" 2>/dev/null) || _MC_TITLE=""
        _MC_TASK=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('description',''))" 2>/dev/null) || _MC_TASK=""
        _MC_STATUS=$(echo "$MC_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null) || _MC_STATUS=""

        # CLI args take priority over MC data
        [ -z "$CLI_AGENT" ] && AGENT="${_MC_AGENT}"
        [ -z "$CLI_TITLE" ] && TITLE="${_MC_TITLE}"
        [ -z "$CLI_TASK" ]  && TASK="${_MC_TASK}"

        # Extract rejection feedback and authorization status
        MC_REJECTION_FEEDBACK=$(echo "$MC_DATA" | python3 -c "import json,sys; t=json.load(sys.stdin); f=t.get('custom_field_values') or {}; print(f.get('mc_rejection_feedback',''))" 2>/dev/null) || MC_REJECTION_FEEDBACK=""
        MC_AUTH_STATUS=$(echo "$MC_DATA" | python3 -c "import json,sys; t=json.load(sys.stdin); f=t.get('custom_field_values') or {}; print(f.get('mc_authorization_status',''))" 2>/dev/null) || MC_AUTH_STATUS=""

        EXTRA_CONTEXT=""
        if [ -n "$MC_REJECTION_FEEDBACK" ]; then
            EXTRA_CONTEXT="## ⚠️ PREVIOUS REVIEW FEEDBACK (MUST ADDRESS)\n${MC_REJECTION_FEEDBACK}\n\n**You MUST address all points above before reporting done.**\n\n"
        fi

        if [ "$MC_AUTH_STATUS" = "authorized" ]; then
            PLAN_FILE="$WORKSPACE/plans/${MC_TASK_ID}.md"
            PLAN_CONTENT=""
            if [ -f "$PLAN_FILE" ]; then
                PLAN_CONTENT=$(cat "$PLAN_FILE")
            fi
            EXTRA_CONTEXT="${EXTRA_CONTEXT}## ✅ AUTHORIZED — Proceed to Implementation\nThis task plan was reviewed and authorized by Luna. Skip Steps 1-3, start at Step 4.\n\n### Approved Plan\n${PLAN_CONTENT:-No plan file found.}\n\n"
        elif [ "$MC_AUTH_STATUS" = "counter_review" ]; then
            EXTRA_CONTEXT="${EXTRA_CONTEXT}## 🔄 COUNTER-REVIEW — Revise Plan\nLuna reviewed your plan and requests changes. See feedback above.\nRevise your plan and re-submit for authorization (max 2 cycles).\n\n"
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
AGENT="$(canonicalize_agent "$AGENT")"
if [ -z "$AGENT" ] || [ -z "$TASK" ]; then
    echo "ERROR: --agent and --task are required (or --from-mc/--from-queue)" >&2
    exit 1
fi

[ -z "$TITLE" ] && TITLE="Fast dispatch: $(echo "$TASK" | head -c 50)"
log "Dispatching to $AGENT: $TITLE"

# ─── Rate limit ──────────────────────────────────────────────────────────────

DISPATCH_COUNT=0
if [ -f "$RATE_STATE" ]; then
    DISPATCH_COUNT=$(python3 -c "import json, time;\nwith open('$RATE_STATE') as f:\n    state=json.load(f)\ncutoff=time.time()-3600\nrecent=[t for t in state.get('timestamps',[]) if t>cutoff]\nprint(len(recent))" 2>/dev/null) || DISPATCH_COUNT=0
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

# ─── Update MC status to in_progress (except reviews) ──────────────────────
if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    _CURRENT_STATUS="${_MC_STATUS:-}"
    if [ -z "$_CURRENT_STATUS" ]; then
        _CURRENT_STATUS=$(curl -s "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
            -H "Authorization: Bearer $MC_API_TOKEN" 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',''))" 2>/dev/null) || _CURRENT_STATUS=""
    fi

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

# ─── Dispatch via openclaw agent ────────────────────────────────────────────
DISPATCH_MSG="DISPATCH agent=$AGENT\n---\n$TASK"
log "Sending to dispatcher → $AGENT (timeout: ${TIMEOUT}s)..."

set +e
RESULT=$($OPENCLAW_BIN agent --agent "dispatcher" --message "$DISPATCH_MSG" --timeout "$TIMEOUT" --json 2>&1)
OPENCLAW_RC=$?
set -e

if [ "$OPENCLAW_RC" -ne 0 ]; then
    log "DISPATCH FAILED: openclaw returned rc=$OPENCLAW_RC"
    rollback_mc_task "$MC_TASK_ID" "openclaw dispatch command failed (${OPENCLAW_RC})"
    echo "Dispatch failed" >&2
    exit 1
fi

# Some OpenClaw CLIs may prepend config warnings before the JSON output.
# Normalize by extracting the first JSON object from the combined stdout/stderr.
RESULT_JSON=$(python3 - "$RESULT" <<'PY'
import sys
s = sys.argv[1]
# Find the first '{' which starts the JSON payload.
i = s.find('{')
if i == -1:
    print("")
else:
    print(s[i:])
PY
)

ACTION_STATUS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null)
if [ -z "$ACTION_STATUS" ]; then
    ACTION_STATUS="unknown"
fi

SESSION_ID=$(python3 - "$RESULT_JSON" <<'PY'
import json
import re
import sys

def first_match(value):
    if not isinstance(value, str):
        return ""
    # Prefer explicit dispatch payload marker first (agent:session format).
    for pat in (
        r"DISPATCHED\\s+session=([a-zA-Z0-9:_-]+)",
        r"session=([a-zA-Z0-9:_-]+)",
        r"session[:=]{1}([a-zA-Z0-9:_-]+)",
    ):
        m = re.search(pat, value)
        if m:
            return m.group(1)
    return ""

payload_text = sys.argv[1]
try:
    payload = json.loads(payload_text)
except Exception:
    print("")
    sys.exit(0)

result = payload.get("result") if isinstance(payload, dict) else {}

# 1) Prefer explicit dispatch marker from payload first.
def first_payload_match(items):
    for item in items:
        for field in ("text", "message"):
            value = item.get(field)
            m = re.search(r"DISPATCHED\s+session=([a-zA-Z0-9:_-]+)", str(value))
            if m:
                print(m.group(1))
                sys.exit(0)

first_payload_match(result.get("payloads", []) or [])
first_payload_match(payload.get("payloads", []) if isinstance(payload, dict) else [])

# 2) Structured output (session field)
for key in ("sessionKey", "session_id", "sessionId", "session", "targetSession"):
    value = result.get(key)
    if isinstance(value, str) and value:
        print(value)
        sys.exit(0)

# 3) Fallback: generic extraction from any payload text
for item in result.get("payloads", []) or []:
    for field in ("text", "message"):
        value = item.get(field)
        extracted = first_match(str(value))
        if extracted:
            print(extracted)
            sys.exit(0)

for item in payload.get("payloads", []) if isinstance(payload, dict) else []:
    for field in ("text", "message"):
        value = item.get(field)
        extracted = first_match(str(value))
        if extracted:
            print(extracted)
            sys.exit(0)

print("")
PY
)
if [ -z "$SESSION_ID" ]; then
    log "ERROR: unable to extract target session_key from dispatcher output"
    rollback_mc_task "$MC_TASK_ID" "dispatch response lacked target session_key"
    echo "Dispatch failed" >&2
    exit 1
fi

DURATION=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('result',{}).get('meta',{}).get('durationMs',0))" 2>/dev/null)
if [ -z "$DURATION" ]; then
    DURATION=0
fi

# Update session key and clear prior errors on success
if [ -n "$MC_TASK_ID" ] && [ -n "$MC_API_TOKEN" ]; then
    _IS_REVIEW_TARGET="0"
    if [ "${_CURRENT_STATUS:-}" = "review" ]; then
        _IS_REVIEW_TARGET="1"
    fi

    PAYLOAD=$(python3 - "$SESSION_ID" "$_IS_REVIEW_TARGET" <<'PY'
import json
import sys
session = sys.argv[1]
is_review = sys.argv[2] == "1"
payload = {
    "custom_field_values": {
        "mc_session_key": session,
        "mc_last_error": ""
    },
    "comment": "[heartbeat-v3] dispatcher linked target session",
}
if not is_review:
    payload["status"] = "in_progress"
print(json.dumps(payload))
PY
)
    if ! curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$MC_TASK_ID" \
        -H "Authorization: Bearer $MC_API_TOKEN" \
        -H "Content-Type: application/json" \
        -d "$PAYLOAD" > /dev/null 2>&1; then
        log "ERROR: failed to persist session_link for $MC_TASK_ID, rolling back"
        rollback_mc_task "$MC_TASK_ID" "failed to persist mc_session_key on task card"
        echo "Dispatch failed" >&2
        exit 1
    fi
    log "MC task $MC_TASK_ID linked session_key=$SESSION_ID and set in_progress"
fi

# ─── Update rate limit state ────────────────────────────────────────────────
python3 -c "import json, time;\nstate={'timestamps': []};\n\ntry:\n    with open('$RATE_STATE') as f:\n        state = json.load(f)\nexcept: pass\n\ncutoff = time.time() - 3600\nstate['timestamps'] = [t for t in state.get('timestamps', []) if t > cutoff]\nstate['timestamps'].append(time.time())\nwith open('$RATE_STATE', 'w') as f:\n    json.dump(state, f)\n" 2>/dev/null || true

# ─── Move queue file to done with audit metadata ──────────────────────────
if [ -n "$QUEUE_FILE" ] && [ -f "$QUEUE_FILE" ]; then
    DONE_DIR="$(dirname "$(dirname "$QUEUE_FILE")")/done"
    mkdir -p "$DONE_DIR"
    python3 - "$QUEUE_FILE" "$DONE_DIR" "$SESSION_ID" "$AGENT" "$METRICS_FILE" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

queue_file, done_dir, session_id, agent, metrics_file = sys.argv[1:6]
filename = os.path.basename(queue_file)
target = os.path.join(done_dir, filename)

try:
    with open(queue_file, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
except Exception:
    payload = {}

payload["completed_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
payload["completed_by"] = "mc-fast-dispatch"
payload["success"] = True
payload["result"] = {
    "action": "dispatch",
    "agent": agent,
    "session_id": session_id,
}

tmp_path = f"{target}.tmp"
with open(tmp_path, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp_path, target)
try:
    os.unlink(queue_file)
except FileNotFoundError:
    pass

metrics = {
    "schema_version": 2,
    "last_updated": payload["completed_at"],
    "counters_today": {},
    "cron_health": {},
    "phase_transitions": {},
}
if os.path.exists(metrics_file):
    try:
        with open(metrics_file, "r", encoding="utf-8") as fh:
            metrics = json.load(fh)
    except Exception:
        pass
metrics.setdefault("counters_today", {})
metrics["counters_today"]["queue_items_completed"] = int(metrics["counters_today"].get("queue_items_completed", 0) or 0) + 1
metrics["last_updated"] = payload["completed_at"]
os.makedirs(os.path.dirname(metrics_file), exist_ok=True)
with open(metrics_file, "w", encoding="utf-8") as fh:
    json.dump(metrics, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
PY
fi

log "Dispatch complete: status=$ACTION_STATUS session=$SESSION_ID duration=${DURATION}ms"

echo "{\"status\":\"$ACTION_STATUS\",\"agent\":\"$AGENT\",\"session_id\":\"$SESSION_ID\",\"duration_ms\":$DURATION,\"mc_task_id\":\"${MC_TASK_ID:-}\"}"
