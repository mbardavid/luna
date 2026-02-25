#!/usr/bin/env bash
# a2a-mc-track.sh
# Create an MC task for an A2A run and output a ready-to-use sessions_spawn payload.
# Note: sessions_spawn must still be invoked by the caller (agent tool).
# After spawn, link sessionKey -> MC via mc-link-task-session.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="$SCRIPT_DIR/mc-client.sh"

usage(){
  cat <<'USAGE'
Usage:
  a2a-mc-track.sh --agent <agentId> --title <title> [--description <desc>] [--json]

Creates an MC task and prints:
- taskId
- a sessions_spawn payload (JSON)
- TASK_UPDATE contract

Flow:
1) run this script (creates MC task)
2) call sessions_spawn using the printed payload (set label=taskId)
3) link sessionKey back to MC:
   scripts/mc-link-task-session.sh <taskId> <sessionKey>
USAGE
}

AGENT=""; TITLE=""; DESC=""; AS_JSON=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent) AGENT="${2:-}"; shift 2;;
    --title) TITLE="${2:-}"; shift 2;;
    --description) DESC="${2:-}"; shift 2;;
    --json) AS_JSON=1; shift;;
    -h|--help) usage; exit 0;;
    *) echo "unknown arg: $1" >&2; usage; exit 1;;
  esac
done

[ -n "$AGENT" ] && [ -n "$TITLE" ] || { usage; exit 2; }

TASK_JSON=$($MC_CLIENT create-task "$TITLE" "$DESC" "$AGENT" high in_progress '{"mc_progress":0}')
TASK_ID=$(python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])' <<< "$TASK_JSON")

SUBAGENT_MESSAGE=$(TASK_ID="$TASK_ID" python3 - <<'PY'
import os

task_id = os.environ["TASK_ID"]
msg = (
  "You are working on a Mission Control tracked task. "
  "You MUST report progress using TASK_UPDATE JSON blocks.\n\n"
  "Format:\n"
  "TASK_UPDATE {\"taskId\":\"%s\",\"status\":\"in_progress\",\"progress\":0,\"summary\":\"...\",\"artifacts\":[]}\n\n"
  "When done, send:\n"
  "TASK_UPDATE {\"taskId\":\"%s\",\"status\":\"done\",\"progress\":100,\"summary\":\"...\",\"artifacts\":[]}\n"
) % (task_id, task_id)
print(msg)
PY
)

SPAWN_PAYLOAD=$(AGENT="$AGENT" TASK_ID="$TASK_ID" TITLE="$TITLE" DESC="$DESC" SUBAGENT_MESSAGE="$SUBAGENT_MESSAGE" python3 - <<'PY'
import json
import os

agent = os.environ["AGENT"]
task_id = os.environ["TASK_ID"]
title = os.environ.get("TITLE", "")
desc = os.environ.get("DESC", "")
sub_msg = os.environ.get("SUBAGENT_MESSAGE", "")
message = f"Task: {title}\n\n{desc}\n\n" + sub_msg
print(json.dumps({
  "agentId": agent,
  "mode": "run",
  "cleanup": "keep",
  "label": task_id,
  "task": message,
}, ensure_ascii=False))
PY
)

if [ "$AS_JSON" -eq 1 ]; then
  TASK_ID="$TASK_ID" SPAWN_PAYLOAD="$SPAWN_PAYLOAD" python3 - <<'PY'
import json
import os
print(json.dumps({
  "taskId": os.environ["TASK_ID"],
  "spawnPayload": json.loads(os.environ["SPAWN_PAYLOAD"]),
}, ensure_ascii=False))
PY
  exit 0
fi

cat <<EOF
MC task created: $TASK_ID

sessions_spawn payload (JSON):
$SPAWN_PAYLOAD

After sessions_spawn returns sessionKey, link it:
  $SCRIPT_DIR/mc-link-task-session.sh $TASK_ID <sessionKey>

TASK_UPDATE contract:
TASK_UPDATE {"taskId":"$TASK_ID","status":"in_progress","progress":0,"summary":"...","artifacts":[]}
EOF
