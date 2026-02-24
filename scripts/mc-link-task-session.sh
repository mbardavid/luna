#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"

usage() {
  cat <<'USAGE'
mc-link-task-session.sh

Link a Mission Control task to a live sessionKey.

Usage:
  mc-link-task-session.sh <task_id> <session_key> [--status <status>]

Defaults:
  status = in_progress
USAGE
}

if [ "$#" -lt 2 ]; then
  usage
  exit 1
fi

TASK_ID="$1"
SESSION_KEY="$2"
STATUS="in_progress"

shift 2
while [ "$#" -gt 0 ]; do
  case "$1" in
    --status)
      STATUS="${2:-in_progress}"
      shift 2
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

if [ -z "${TASK_ID}" ] || [ -z "${SESSION_KEY}" ]; then
  echo "task_id and session_key are required" >&2
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client script not found or not executable: $MC_CLIENT" >&2
  exit 1
fi

fields_json=$(SESSION_KEY="$SESSION_KEY" python3 - <<'PY'
import json
import os
print(json.dumps({"mc_session_key": os.environ["SESSION_KEY"]}, ensure_ascii=False))
PY
)

comment="sessionKey=${SESSION_KEY}"
"$MC_CLIENT" update-task "$TASK_ID" --status "$STATUS" --comment "$comment" --fields "$fields_json"
echo "Task ${TASK_ID} linked to session ${SESSION_KEY}"
