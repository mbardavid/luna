#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"
MC_RESOURCE_STATE_FILE="${MC_RESOURCE_STATE_FILE:-/home/openclaw/.openclaw/workspace/.mc-resource-state.json}"

usage() {
  cat <<'USAGE'
mc-spawn-wrapper.sh

Prepare a Mission Control task and build a sessions_spawn payload template.
NOTE: This script does NOT spawn a subagent session. It only creates the MC
task and outputs the payload so the caller can invoke sessions_spawn separately.

Required:
  --assignee <agent_or_uuid>
  --title <string>
  --description <string>

Optional:
  --task-message <string>
    Extra instruction appended to subagent message.
  --force-spawn
    Ignore resource-degrade guard and create the task immediately.
  --priority <medium|high|low>
    Default: medium
  --status <status>
    Default: inbox
  --initial-progress <0-100>
    Default: 0
  --estimated-cost <decimal>
    Sets mc_estimated_cost_usd custom field on task creation.
  --json
    Print machine-readable JSON payload and exit.

Output:
  Prints TASK_ID, SESSION_LABEL, and the SPAWN_PAYLOAD template.
  The caller is responsible for actually invoking sessions_spawn with the payload.
USAGE
}

ASSIGNEE=""
TITLE=""
DESCRIPTION=""
TASK_MESSAGE=""
PRIORITY="medium"
STATUS="inbox"
INITIAL_PROGRESS="0"
ESTIMATED_COST=""
OUTPUT_JSON=0
FORCE_SPAWN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --assignee)
      ASSIGNEE="${2:-}"
      shift 2
      ;;
    --title)
      TITLE="${2:-}"
      shift 2
      ;;
    --description)
      DESCRIPTION="${2:-}"
      shift 2
      ;;
    --task-message)
      TASK_MESSAGE="${2:-}"
      shift 2
      ;;
    --priority)
      PRIORITY="${2:-medium}"
      shift 2
      ;;
    --status)
      STATUS="${2:-inbox}"
      shift 2
      ;;
    --initial-progress)
      INITIAL_PROGRESS="${2:-0}"
      shift 2
      ;;
    --estimated-cost)
      ESTIMATED_COST="${2:-}"
      shift 2
      ;;
    --force-spawn)
      FORCE_SPAWN=1
      shift
      ;;
    --json)
      OUTPUT_JSON=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ -z "$ASSIGNEE" ] || [ -z "$TITLE" ] || [ -z "$DESCRIPTION" ]; then
  echo "assignee, title and description are required" >&2
  usage
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client script not found or not executable: $MC_CLIENT" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not available" >&2
  exit 1
fi

task_fields=$(INITIAL_PROGRESS="$INITIAL_PROGRESS" ESTIMATED_COST="$ESTIMATED_COST" python3 - <<'PY'
import json
import os

value = int(os.environ.get("INITIAL_PROGRESS", "0") or 0)
value = max(0, min(100, value))
fields={"mc_progress": value}
raw_cost=(os.environ.get("ESTIMATED_COST", "") or "").strip()
if raw_cost:
    try:
        fields["mc_estimated_cost_usd"]=float(raw_cost)
    except Exception:
        pass
print(json.dumps(fields))
PY
)

created_task=$(bash "$MC_CLIENT" create-task "$TITLE" "$DESCRIPTION" "$ASSIGNEE" "$PRIORITY" "$STATUS" "$task_fields")
task_id=$(python3 -c 'import sys, json; print(json.load(sys.stdin).get("id", ""))' <<< "$created_task")

if [ -z "$task_id" ]; then
  echo "failed to parse task id" >&2
  echo "$created_task" >&2
  exit 1
fi

if [ "$FORCE_SPAWN" -eq 0 ] && [ -f "$MC_RESOURCE_STATE_FILE" ]; then
  RESOURCE_DEGRADE=$(python3 - "$MC_RESOURCE_STATE_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
  with open(path, "r", encoding="utf-8") as fp:
    state = json.load(fp)
  print("1" if str(state.get("mode", "")).lower() == "degrade" else "0")
except Exception:
  print("0")
PY
  )

  if [ "$RESOURCE_DEGRADE" = "1" ]; then
    BLOCK_COMMENT="[mc-spawn-wrapper] Resource guard active: skip automatic spawn while mode=degrade. Task created, status set to blocked."
    UPDATED_TASK_STATUS="${STATUS}"
    if [ -n "$UPDATED_TASK_STATUS" ] && [ "$UPDATED_TASK_STATUS" != "blocked" ]; then
      UPDATED_TASK_STATUS="blocked"
    fi
    "$MC_CLIENT" update-task "$task_id" --status "$UPDATED_TASK_STATUS" --comment "$BLOCK_COMMENT" --fields "$task_fields"
    printf 'TASK_ID=%s\n' "$task_id"
    printf 'STATUS=%s\n' "$UPDATED_TASK_STATUS"
    printf 'SESSION_LABEL=%s\n' "$task_id"
    printf 'SPAWN_BLOCKED=1\n'
    printf '%s\n' "$BLOCK_COMMENT"
    printf 'Next step: rerun with --force-spawn when resource mode is recovered.\n'
    exit 3
  fi
fi

spawn_message=$(TASK_ID="$task_id" TITLE="$TITLE" DESCRIPTION="$DESCRIPTION" TASK_MESSAGE="$TASK_MESSAGE" PRIORITY="$PRIORITY" python3 - <<'PY'
import os

parts = [
    "Objetivo principal (task do Mission Control):",
    f"taskId={os.environ['TASK_ID']}",
    f"Prioridade: {os.environ['PRIORITY']}",
    "",
    "Instruções de operação:",
    f"Title: {os.environ['TITLE']}",
    f"Description: {os.environ['DESCRIPTION']}",
    "Execute o objetivo abaixo e, ao final de cada etapa relevante, inclua:",
    'TASK_UPDATE {"taskId":"<taskId>","status":"in_progress|done|failed|blocked","progress":<0-100>,"summary":"texto curto","error":null,"artifacts":["path/para/arquivo"]}',
    "",
]

extra = os.environ.get("TASK_MESSAGE", "").strip()
if extra:
    parts.append("Instruções extras:")
    parts.append(extra)

print("\\n".join(parts))
PY
)

spawn_payload=$(TASK_ID="$task_id" MESSAGE="$spawn_message" python3 - <<'PY'
import json
import os

print(json.dumps(
    {
        "label": os.environ["TASK_ID"],
        "message": os.environ["MESSAGE"],
    },
    ensure_ascii=False,
))
PY
)

if [ "$OUTPUT_JSON" -eq 1 ]; then
  OUTPUT_JSON_PAYLOAD=$(TASK_ID="$task_id" LABEL="$task_id" MESSAGE="$spawn_message" CLIENT_TASK="$created_task" PAYLOAD="$spawn_payload" python3 - <<'PY'
import json
import os

print(json.dumps(
    {
        "task_id": os.environ["TASK_ID"],
        "session_label": os.environ["LABEL"],
        "task_json": json.loads(os.environ["CLIENT_TASK"]),
        "spawn_payload": json.loads(os.environ["PAYLOAD"]),
        "spawn_message": os.environ["MESSAGE"],
    },
    ensure_ascii=False,
))
PY
)
  echo "$OUTPUT_JSON_PAYLOAD"
  exit 0
fi

printf 'TASK_ID=%s\n' "$task_id"
printf 'SESSION_LABEL=%s\n' "$task_id"
printf 'SPAWN_PAYLOAD=%s\n' "$spawn_payload"
printf 'SPAWN_MESSAGE=%s\n' "$spawn_message"
printf 'CREATED_TASK=%s\n' "$task_id"
printf 'NOTE: No session was spawned. Use the SPAWN_PAYLOAD above to call sessions_spawn manually.\n'
printf 'Next step: invoke sessions_spawn with SPAWN_PAYLOAD, then run: mc-link-task-session.sh %s <session_key>\n' "$task_id"
