#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Prefer local, unversioned config when present.
DEFAULT_CFG_LOCAL="${SCRIPT_DIR}/../config/mission-control-ids.local.json"
DEFAULT_CFG_VERSIONED="${SCRIPT_DIR}/../config/mission-control-ids.json"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-$DEFAULT_CFG_LOCAL}"
if [ ! -f "$MC_CONFIG_PATH" ] && [ -f "$DEFAULT_CFG_VERSIONED" ]; then
  MC_CONFIG_PATH="$DEFAULT_CFG_VERSIONED"
fi

if [ ! -f "$MC_CONFIG_PATH" ]; then
  echo "config not found: $MC_CONFIG_PATH" >&2
  exit 2
fi

mc_cfg() {
  local key="$1"
  python3 - "$MC_CONFIG_PATH" "$key" <<'PY'
import json
import sys

config_path = sys.argv[1]
key = sys.argv[2]

with open(config_path, "r", encoding="utf-8") as fp:
    cfg = json.load(fp)

value = cfg
for token in key.split("."):
    if not isinstance(value, dict) or token not in value:
        raise SystemExit(1)
    value = value[token]

if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

MC_API_URL="$(mc_cfg api_url)"
MC_BOARD_ID="$(mc_cfg board_id)"
# Token can also be provided via env (preferred for CI/cron): MC_AUTH_TOKEN
MC_TOKEN="${MC_AUTH_TOKEN:-}"
if [ -z "$MC_TOKEN" ]; then
  MC_TOKEN="$(mc_cfg auth_token)"
fi
MC_STATUS_ALLOWLIST="${MC_STATUS_ALLOWLIST:-inbox,in_progress,review,blocked,stalled,retry,needs_approval,done,failed}"
MC_STATUS_ALLOWLIST="${MC_STATUS_ALLOWLIST,,}"
MC_STATUS_ALLOWLIST="${MC_STATUS_ALLOWLIST//[[:space:]]/}"

mc_resolve_agent_id() {
  local ref="$1"
  if [ -z "$ref" ]; then
    echo ""
    return
  fi

  # Already UUID-like => keep as-is when valid.
  if python3 -c "from uuid import UUID; import sys; UUID(sys.argv[1]); print(sys.argv[1])" "$ref" >/dev/null 2>&1; then
    echo "$ref"
    return
  fi

  local agents_json
  agents_json="$(mc_cfg agents)"
  python3 - "$agents_json" "$ref" <<'PY'
import json
import sys

agents = json.loads(sys.argv[1])
ref = sys.argv[2].strip().lower().replace("-", "_")

if ref in agents:
    print(agents[ref])
    raise SystemExit(0)

for alias, value in agents.items():
    if alias.lower() == ref:
        print(value)
        raise SystemExit(0)

sys.exit(1)
PY
}

mc_normalize_status() {
  local status="${1,,}"
  local normalized=""

  case "$status" in
    inbox|todo|new|created)
      normalized="inbox" ;;
    in_progress|inprogress|running|running_task|active)
      normalized="in_progress" ;;
    blocked|blocked_waiting|dependency_waiting)
      normalized="blocked" ;;
    stalled|idle_too_long|stalled_review)
      normalized="stalled" ;;
    retry|retrying)
      normalized="retry" ;;
    needs_approval|needs-approval|requires_approval|requires-approval|needs_review|awaiting_approval)
      normalized="needs_approval" ;;
    completed|done)
      normalized="done" ;;
    failed|error)
      normalized="failed" ;;
    waiting|review)
      normalized="review" ;;
    *)
      normalized="$status" ;;
  esac

  if [ -z "$normalized" ]; then
    return
  fi

  if [[ ",$MC_STATUS_ALLOWLIST," == *",${normalized},"* ]]; then
    echo "$normalized"
    return
  fi

  # If MC status allowlist doesn't include this status, do NOT collapse semantics.
  # Instead, keep the normalized status so the caller notices misconfiguration.
  echo "$normalized"
}

mc_request() {
  local method="$1"
  local path="$2"
  local body="${3:-}"

  local url="$MC_API_URL${path}"
  local tmp_body
  tmp_body="$(mktemp)"
  local status

  if [ -n "$body" ]; then
    status=$(curl -sS -o "$tmp_body" -w "%{http_code}" \
      -X "$method" \
      -H "Authorization: Bearer $MC_TOKEN" \
      -H "Accept: application/json" \
      -H "Content-Type: application/json" \
      -d "$body" \
      "$url")
  else
    status=$(curl -sS -o "$tmp_body" -w "%{http_code}" \
      -X "$method" \
      -H "Authorization: Bearer $MC_TOKEN" \
      -H "Accept: application/json" \
      "$url")
  fi

  if (( status < 200 || status >= 300 )); then
    echo "HTTP $status for $method $url" >&2
    cat "$tmp_body" >&2
    rm -f "$tmp_body"
    return 1
  fi

  cat "$tmp_body"
  rm -f "$tmp_body"
}

mc_create_task() {
  local title="$1"
  local description="$2"
  local assignee="${3:-}"
  local priority="${4:-medium}"
  local status="${5:-inbox}"
  local fields_json="${6:-}"

  local assignee_id=""
  if [ -n "$assignee" ]; then
    if ! assignee_id="$(mc_resolve_agent_id "$assignee")"; then
      echo "agent not found: $assignee" >&2
      return 1
    fi
  fi

  local normalized_status
  normalized_status="$(mc_normalize_status "$status")"

  local payload
  payload=$(TITLE="$title" DESCRIPTION="$description" ASSIGNEE_ID="$assignee_id" STATUS="$normalized_status" PRIORITY="$priority" FIELDS_JSON="$fields_json" python3 - <<'PY'
import json
import os

payload = {
    "title": os.environ["TITLE"],
    "description": os.environ.get("DESCRIPTION", ""),
    "status": os.environ["STATUS"],
    "priority": os.environ["PRIORITY"],
}
assignee_id = os.environ.get("ASSIGNEE_ID")
if assignee_id:
    payload["assigned_agent_id"] = assignee_id
fields = os.environ.get("FIELDS_JSON", "").strip()
if fields:
    payload["custom_field_values"] = json.loads(fields)
print(json.dumps(payload, ensure_ascii=False))
PY
  )

  mc_request POST "/boards/$MC_BOARD_ID/tasks" "$payload"
}

mc_list_tasks() {
  local status="${1:-}"
  local endpoint="/boards/$MC_BOARD_ID/tasks"
  if [ -n "$status" ]; then
    endpoint="$endpoint?status=$(mc_normalize_status "$status")"
  fi
  mc_request GET "$endpoint"
}

mc_get_task() {
  local task_id="$1"
  mc_request GET "/boards/$MC_BOARD_ID/tasks/$task_id"
}

mc_update_task() {
  local task_id="$1"
  shift

  local status=""
  local comment=""
  local fields_json=""

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --status)
        status="$(mc_normalize_status "$2")"
        shift 2
        ;;
      --comment)
        comment="$2"
        shift 2
        ;;
      --fields)
        fields_json="$2"
        shift 2
        ;;
      *)
        echo "unknown argument: $1" >&2
        return 1
        ;;
    esac
  done

  if [ -z "$status" ] && [ -z "$comment" ] && [ -z "$fields_json" ]; then
    echo "mc_update_task requires at least one of --status, --comment, --fields" >&2
    return 1
  fi

  local payload
  payload=$(STATUS="$status" COMMENT="$comment" FIELDS_JSON="$fields_json" python3 - <<'PY'
import json
import os

payload = {}
if os.environ.get("STATUS"):
    payload["status"] = os.environ["STATUS"]
if os.environ.get("COMMENT"):
    payload["comment"] = os.environ["COMMENT"]
fields = os.environ.get("FIELDS_JSON", "").strip()
if fields:
    payload["custom_field_values"] = json.loads(fields)
print(json.dumps(payload, ensure_ascii=False))
PY
  )

  mc_request PATCH "/boards/$MC_BOARD_ID/tasks/$task_id" "$payload"
}

mc_create_comment() {
  local task_id="$1"
  local message="$2"
  local payload
  payload=$(MESSAGE="$message" python3 - <<'PY'
import json
import os
print(json.dumps({"message": os.environ["MESSAGE"]}, ensure_ascii=False))
PY
  )
  mc_request POST "/boards/$MC_BOARD_ID/tasks/$task_id/comments" "$payload"
}

mc_create_approval() {
  local task_id="$1"
  local reason="$2"
  local action_type="${3:-manual_override}"
  local confidence="${4:-80}"

  local payload
  payload=$(TASK_ID="$task_id" ACTION_TYPE="$action_type" CONFIDENCE="$confidence" REASON="$reason" python3 - <<'PY'
import json
import os

payload = {
    "action_type": os.environ["ACTION_TYPE"],
    "confidence": float(os.environ["CONFIDENCE"]),
    "task_id": os.environ["TASK_ID"],
    "lead_reasoning": os.environ["REASON"],
}
print(json.dumps(payload, ensure_ascii=False))
PY
  )

  mc_request POST "/boards/$MC_BOARD_ID/approvals" "$payload"
}

mc_add_comment() {
  mc_create_comment "$@"
}

mc_health() {
  local status
  local candidate
  local tmp
  local candidates=("${MC_API_URL%/api/v1}/healthz" "${MC_API_URL%/api/v1}/health")

  tmp="$(mktemp)"

  for candidate in "${candidates[@]}"; do
    status=$(curl -sS -o "$tmp" -w "%{http_code}" "$candidate")

    if [ "$status" -eq 200 ]; then
      cat "$tmp"
      rm -f "$tmp"
      return 0
    fi

  done

  echo "HTTP $status for health probes: ${candidates[*]}" >&2
  echo "Last response body:" >&2
  cat "$tmp"
  rm -f "$tmp"
  return 1

}

usage() {
  cat <<'USAGE'
mc-client.sh <command>

Commands:
  health
    Check Mission Control API health.

  list-tasks [status]
    List tasks (optional status filter).

  get-task <task_id>
    Fetch one task.

  create-task <title> <description> [assignee|agent_uuid] [priority=medium] [status=inbox] [fields_json]
    Create task and optionally set custom_field_values.

  update-task <task_id> [--status <status>] [--comment <msg>] [--fields <json>]
    Update task.

  create-comment <task_id> <message>
    Add task comment.

  add-comment <task_id> <message>
    Alias for create-comment.

  create-approval <task_id> <reason> [action_type] [confidence=80]
    Create approval request.

  create-task-id
    Backward-compatible alias for create-task.
USAGE
}

command="${1:-}"
case "$command" in
  health)
    mc_health
    ;;
  list-tasks|list_tasks)
    mc_list_tasks "${2:-}"
    ;;
  get-task|get_task)
    [ -n "${2:-}" ] || { echo "task_id required" >&2; exit 1; }
    mc_get_task "$2"
    ;;
  create-task|create_task|create-task-id)
    [ $# -ge 3 ] || { echo "usage: create-task <title> <description> [assignee] [priority] [status] [fields]" >&2; exit 1; }
    mc_create_task "$2" "$3" "${4:-}" "${5:-medium}" "${6:-inbox}" "${7:-}"
    ;;
  update-task|update_task)
    [ $# -ge 2 ] || { echo "usage: update-task <task_id> [--status ...]" >&2; exit 1; }
    shift
    task_id="$1"
    shift
    mc_update_task "$task_id" "$@"
    ;;
  create-comment|create_comment)
    [ $# -ge 3 ] || { echo "usage: create-comment <task_id> <message>" >&2; exit 1; }
    mc_create_comment "$2" "$3"
    ;;
  add-comment|add_comment)
    shift
    mc_add_comment "$@"
    ;;
  create-approval|create_approval)
    [ $# -ge 3 ] || { echo "usage: create-approval <task_id> <reason> [action_type] [confidence]" >&2; exit 1; }
    mc_create_approval "$2" "$3" "${4:-manual_override}" "${5:-80}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    usage
    exit 1
    ;;
esac
