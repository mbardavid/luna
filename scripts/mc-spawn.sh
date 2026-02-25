#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-spawn.sh — Atomic prep for subagent spawn via Mission Control
#
# Creates an MC task (status: in_progress) with the correct assigned_agent_id,
# then outputs a ready-to-use JSON payload for sessions_spawn.
#
# The caller (Luna) invokes sessions_spawn with the returned params, then
# optionally passes the session_key back to link via --auto-link or
# mc-link-task-session.sh.
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"
MC_LINK="${SCRIPT_DIR}/mc-link-task-session.sh"
AGENT_IDS_FILE="${SCRIPT_DIR}/../config/mc-agent-ids.json"
LOG_DIR="${SCRIPT_DIR}/../logs"
AUDIT_LOG="${LOG_DIR}/mc-spawn-audit.log"

usage() {
  cat <<'USAGE'
mc-spawn.sh — Atomic prep for MC task + spawn payload

Usage:
  mc-spawn.sh --agent <name> --title <string> --task <string> [options]

Required:
  --agent <name>        Agent name (luan, crypto-sage, main, quant-strategist)
  --title <string>      Task title
  --task <string>       Full task description / instructions for the subagent

Optional:
  --timeout <seconds>   Run timeout (default: 900)
  --priority <level>    Priority: low|medium|high (default: medium)
  --mode <mode>         Session mode: run|chat (default: run)
  --json                Output only JSON (machine-readable)
  --auto-link           After output, read session_key from stdin and link it
  --estimated-cost <n>  Estimated cost in USD
  --mc-task-id <id>     Use existing MC task instead of creating new one

Environment:
  MC_AUTH_TOKEN         Override auth token from config
  MC_CONFIG_PATH        Override mission-control-ids.json path

Example:
  mc-spawn.sh \
    --agent luan \
    --title "Fix auth module" \
    --task "Investigate and fix the auth..." \
    --timeout 900 \
    --priority high \
    --json
USAGE
}

# --- Parse arguments ---
AGENT=""
TITLE=""
TASK=""
TIMEOUT=900
PRIORITY="medium"
MODE="run"
OUTPUT_JSON=0
AUTO_LINK=0
ESTIMATED_COST=""
EXISTING_TASK_ID=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent)      AGENT="${2:-}";          shift 2 ;;
    --title)      TITLE="${2:-}";          shift 2 ;;
    --task)       TASK="${2:-}";           shift 2 ;;
    --timeout)    TIMEOUT="${2:-900}";     shift 2 ;;
    --priority)   PRIORITY="${2:-medium}"; shift 2 ;;
    --mode)       MODE="${2:-run}";        shift 2 ;;
    --json)       OUTPUT_JSON=1;          shift ;;
    --auto-link)  AUTO_LINK=1;            shift ;;
    --estimated-cost) ESTIMATED_COST="${2:-}"; shift 2 ;;
    --mc-task-id) EXISTING_TASK_ID="${2:-}"; shift 2 ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

# --- Validate ---
if [ -z "$AGENT" ] || [ -z "$TITLE" ] || [ -z "$TASK" ]; then
  echo "Error: --agent, --title, and --task are required" >&2
  usage
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client.sh not found or not executable: $MC_CLIENT" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not available" >&2
  exit 2
fi

# --- Resolve agent ID from lookup table ---
resolve_agent_short_id() {
  local agent_name="$1"
  if [ -f "$AGENT_IDS_FILE" ]; then
    python3 -c "
import json, sys
with open('$AGENT_IDS_FILE') as f:
    ids = json.load(f)
name = sys.argv[1].lower().replace('_', '-')
if name in ids:
    print(ids[name])
elif name.replace('-', '_') in ids:
    print(ids[name.replace('-', '_')])
else:
    sys.exit(1)
" "$agent_name" 2>/dev/null
  else
    return 1
  fi
}

# Resolve full agent UUID via mc-client (for MC API)
resolve_agent_full_id() {
  local agent_name="$1"
  # mc-client already has the full UUID resolution via config
  source <(grep -A999 '^mc_cfg()' "$MC_CLIENT" | head -0) 2>/dev/null || true
  # Use mc_resolve_agent_id from mc-client
  bash -c "
    source '$MC_CLIENT' 2>/dev/null
    mc_resolve_agent_id '$agent_name'
  " 2>/dev/null || true
}

# We need the full UUID for the MC API
AGENT_FULL_ID=""
# First try resolving through mc-client's config (has full UUIDs)
AGENT_FULL_ID=$(python3 -c "
import json, sys
with open('${SCRIPT_DIR}/../config/mission-control-ids.json') as f:
    cfg = json.load(f)
agents = cfg.get('agents', {})
name = sys.argv[1].lower().replace('-', '_')
if name in agents:
    print(agents[name])
else:
    # Try with dashes
    name2 = sys.argv[1].lower().replace('_', '-')
    for k, v in agents.items():
        if k.lower().replace('-', '_') == name or k.lower() == name2:
            print(v)
            sys.exit(0)
    sys.exit(1)
" "$AGENT" 2>/dev/null) || true

if [ -z "$AGENT_FULL_ID" ]; then
  echo "Warning: Could not resolve agent '$AGENT' to UUID. Task will be created without assigned_agent_id." >&2
fi

# --- Slugify title for label ---
LABEL_SLUG=$(python3 -c "
import re, sys
title = sys.argv[1].lower().strip()
slug = re.sub(r'[^a-z0-9]+', '-', title).strip('-')
if len(slug) > 40:
    slug = slug[:40].rstrip('-')
print(slug)
" "$TITLE")

# --- Create MC task (or use existing) ---
TASK_ID=""
CREATED_TASK_JSON=""

if [ -n "$EXISTING_TASK_ID" ]; then
  TASK_ID="$EXISTING_TASK_ID"
  # Update the existing task to in_progress with assignment
  FIELDS_JSON="{}"
  if [ -n "$ESTIMATED_COST" ]; then
    FIELDS_JSON=$(python3 -c "import json; print(json.dumps({'mc_estimated_cost_usd': float('$ESTIMATED_COST')}))")
  fi
  
  UPDATE_ARGS=("$TASK_ID" --status "in_progress")
  if [ -n "$AGENT_FULL_ID" ]; then
    UPDATE_ARGS+=(--fields "$(python3 -c "
import json
fields = json.loads('$FIELDS_JSON')
print(json.dumps(fields))
")")
  fi
  CREATED_TASK_JSON=$(bash "$MC_CLIENT" update-task "${UPDATE_ARGS[@]}" 2>/dev/null) || true
else
  # Build custom fields
  FIELDS_JSON=$(python3 -c "
import json
fields = {'mc_progress': 0}
cost = '$ESTIMATED_COST'.strip()
if cost:
    try:
        fields['mc_estimated_cost_usd'] = float(cost)
    except:
        pass
print(json.dumps(fields))
")

  CREATED_TASK_JSON=$(bash "$MC_CLIENT" create-task "$TITLE" "$TASK" "${AGENT:-}" "$PRIORITY" "in_progress" "$FIELDS_JSON")
  TASK_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" <<< "$CREATED_TASK_JSON")
fi

if [ -z "$TASK_ID" ]; then
  echo "Error: Failed to create/resolve MC task" >&2
  echo "$CREATED_TASK_JSON" >&2
  exit 1
fi

# Short task ID for label (first 8 chars)
TASK_ID_SHORT="${TASK_ID:0:8}"
SESSION_LABEL="${LABEL_SLUG}-${TASK_ID_SHORT}"

# --- Build spawn params ---
# The task text sent to the subagent includes the MC task reference
SPAWN_TASK_TEXT="${TASK}

MC Task: ${TASK_ID}"

SPAWN_PARAMS=$(python3 -c "
import json, sys

params = {
    'agentId': sys.argv[1],
    'label': sys.argv[2],
    'mode': sys.argv[3],
    'runTimeoutSeconds': int(sys.argv[4]),
    'task': sys.argv[5],
}
print(json.dumps(params, ensure_ascii=False))
" "$AGENT" "$SESSION_LABEL" "$MODE" "$TIMEOUT" "$SPAWN_TASK_TEXT")

# --- Build output ---
OUTPUT=$(python3 -c "
import json, sys

result = {
    'mc_task_id': sys.argv[1],
    'spawn_params': json.loads(sys.argv[2]),
}
print(json.dumps(result, ensure_ascii=False, indent=2))
" "$TASK_ID" "$SPAWN_PARAMS")

# --- Audit log ---
mkdir -p "$LOG_DIR"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] mc-spawn: agent=$AGENT task_id=$TASK_ID title=\"$TITLE\" timeout=$TIMEOUT priority=$PRIORITY" >> "$AUDIT_LOG" 2>/dev/null || true

# --- Output ---
if [ "$OUTPUT_JSON" -eq 1 ]; then
  echo "$OUTPUT"
else
  echo "═══════════════════════════════════════════════"
  echo "  MC Task Created + Spawn Payload Ready"
  echo "═══════════════════════════════════════════════"
  echo ""
  echo "  Task ID:    $TASK_ID"
  echo "  Agent:      $AGENT"
  echo "  Label:      $SESSION_LABEL"
  echo "  Timeout:    ${TIMEOUT}s"
  echo "  Priority:   $PRIORITY"
  echo ""
  echo "  Spawn Params (pass to sessions_spawn):"
  echo "$SPAWN_PARAMS" | python3 -m json.tool 2>/dev/null || echo "$SPAWN_PARAMS"
  echo ""
  echo "  Next steps:"
  echo "  1. Call sessions_spawn with the params above"
  echo "  2. Run: bash scripts/mc-link-task-session.sh $TASK_ID <session_key>"
  echo "═══════════════════════════════════════════════"
fi

# --- Auto-link mode ---
if [ "$AUTO_LINK" -eq 1 ]; then
  echo "" >&2
  echo "Waiting for session_key on stdin..." >&2
  read -r SESSION_KEY
  if [ -n "$SESSION_KEY" ]; then
    bash "$MC_LINK" "$TASK_ID" "$SESSION_KEY"
    echo "[mc-spawn] Linked task $TASK_ID to session $SESSION_KEY" >&2
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] mc-spawn: linked task_id=$TASK_ID session_key=$SESSION_KEY" >> "$AUDIT_LOG" 2>/dev/null || true
  else
    echo "Error: Empty session_key received" >&2
    exit 1
  fi
fi
