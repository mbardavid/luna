#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
MC_CLIENT="${MC_CLIENT:-$WORKSPACE/scripts/mc-client.sh}"
TOPOLOGY_HELPER="${TOPOLOGY_HELPER:-$WORKSPACE/scripts/agent_runtime_topology.py}"
LOG_FILE="${MC_SPAWN_ISOLATED_LOG:-$WORKSPACE/logs/mc-spawn-isolated.log}"
LOCK_FILE="${MC_SPAWN_ISOLATED_LOCK:-/tmp/mc-spawn-isolated.lock}"
WAIT_SECONDS="${MC_SPAWN_ISOLATED_WAIT_SECONDS:-25}"
CLAIM_SECONDS="${MC_SPAWN_ISOLATED_CLAIM_SECONDS:-900}"
DRY_RUN=0
TASK_ID=""

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

usage() {
  cat <<'USAGE'
mc-spawn-isolated.sh

Atomic-ish MC dispatch path:
  claim card -> trigger isolated main session -> sessions_spawn -> link mc_session_key

Usage:
  mc-spawn-isolated.sh --task-id <task_id> [--dry-run]
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-id)
      TASK_ID="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
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

[ -n "$TASK_ID" ] || { echo "--task-id is required" >&2; exit 1; }
[ -x "$MC_CLIENT" ] || { echo "mc-client not executable: $MC_CLIENT" >&2; exit 1; }

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "SKIP: already running (lock held)"
  printf '{"action":"skip","reason":"lock_held"}\n'
  exit 0
fi

set +euo pipefail
source "$HOME/.bashrc" 2>/dev/null || true
set -euo pipefail

TASK_JSON=$(python3 - "$TASK_ID" "$MC_CLIENT" <<'PY'
import json, subprocess, sys

task_id = sys.argv[1]
mc = sys.argv[2]
raw = subprocess.check_output([mc, 'list-tasks'], text=True)
for item in (json.loads(raw or '{}').get('items', [])):
    if str(item.get('id','')) == task_id:
        print(json.dumps(item, ensure_ascii=False))
        raise SystemExit(0)
print('{}')
PY
)

[ "$TASK_JSON" != "{}" ] || { echo "task not found: $TASK_ID" >&2; exit 1; }

TASK_STATE=$(TASK_JSON="$TASK_JSON" python3 - <<'PY'
import json, os

t = json.loads(os.environ['TASK_JSON'])
fields = t.get('custom_field_values') or {}
assigned_id = str(t.get('assigned_agent_id') or '').strip()
mc_assigned = str(fields.get('mc_assigned_agent') or '').strip()
agent_ref = mc_assigned or assigned_id or 'luan'
status = str(t.get('status') or '').strip().lower()
delivery = str(fields.get('mc_delivery_state') or '').strip().lower()
session_key = str(fields.get('mc_session_key') or '').strip()
phase_owner = str(fields.get('mc_phase_owner') or '').strip().lower()
validation_artifact = str(fields.get('mc_validation_artifact') or '').strip()
run_id = str(fields.get('mc_run_id') or '').strip()
print(json.dumps({
    'title': str(t.get('title') or '(sem título)'),
    'description': str(t.get('description') or ''),
    'priority': str(t.get('priority') or 'medium').lower(),
    'agent_ref': agent_ref,
    'status': status,
    'delivery_state': delivery,
    'session_key': session_key,
    'phase_owner': phase_owner,
    'validation_artifact': validation_artifact,
    'run_id': run_id,
    'acceptance': str(fields.get('mc_acceptance_criteria') or ''),
    'qa_checks': str(fields.get('mc_qa_checks') or ''),
    'expected_artifacts': str(fields.get('mc_expected_artifacts') or ''),
    'lane': str(fields.get('mc_lane') or ''),
    'workflow': str(fields.get('mc_workflow') or ''),
}))
PY
)

TITLE=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['title'])
PY
)
DESCRIPTION=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['description'])
PY
)
PRIORITY=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['priority'])
PY
)
AGENT_REF=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['agent_ref'])
PY
)
STATUS=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['status'])
PY
)
DELIVERY_STATE=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['delivery_state'])
PY
)
SESSION_KEY=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['session_key'])
PY
)
PHASE_OWNER=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['phase_owner'])
PY
)
VALIDATION_ARTIFACT=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['validation_artifact'])
PY
)
RUN_ID=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['run_id'])
PY
)
ACCEPTANCE=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['acceptance'])
PY
)
QA_CHECKS=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['qa_checks'])
PY
)
EXPECTED_ARTIFACTS=$(TASK_STATE="$TASK_STATE" python3 - <<'PY'
import json, os
print(json.loads(os.environ['TASK_STATE'])['expected_artifacts'])
PY
)

if [[ "$STATUS" =~ ^(done|review|awaiting_human|failed)$ ]]; then
  log "RECONCILE: task $TASK_ID not executable anymore (status=$STATUS)"
  printf '{"action":"reconciled","reason":"status_%s","task_id":"%s"}\n' "$STATUS" "$TASK_ID"
  exit 0
fi

if [[ "$SESSION_KEY" == *"luna-judge"* ]] || [ -n "$VALIDATION_ARTIFACT" ] || [ "$PHASE_OWNER" = "luna" ]; then
  log "RECONCILE: task $TASK_ID is judge-owned; skip spawn"
  printf '{"action":"reconciled","reason":"judge_owned","task_id":"%s"}\n' "$TASK_ID"
  exit 0
fi

if [ -n "$SESSION_KEY" ] && [[ "$DELIVERY_STATE" =~ ^(linked|running|proof_pending|review_pending|done)$ ]]; then
  log "RECONCILE: task $TASK_ID already linked to $SESSION_KEY"
  printf '{"action":"reconciled","reason":"already_linked","task_id":"%s","session_key":"%s"}\n' "$TASK_ID" "$SESSION_KEY"
  exit 0
fi

RAW_AGENT="$AGENT_REF"
if [ -f "$TOPOLOGY_HELPER" ]; then
  NAME_AGENT="$(python3 "$TOPOLOGY_HELPER" assigned-agent "$RAW_AGENT" 2>/dev/null || true)"
  [ -n "$NAME_AGENT" ] || NAME_AGENT="$RAW_AGENT"
  RESOLVED_AGENT="$(python3 "$TOPOLOGY_HELPER" normalize "$NAME_AGENT" 2>/dev/null || true)"
else
  NAME_AGENT="$RAW_AGENT"
  RESOLVED_AGENT=""
fi
AGENT_ID="${RESOLVED_AGENT:-$NAME_AGENT}"
[ -n "$AGENT_ID" ] || AGENT_ID="luan"

CLAIM_EXPIRES_AT=$(python3 - "$CLAIM_SECONDS" <<'PY'
from datetime import datetime, timezone, timedelta
import sys
print((datetime.now(timezone.utc) + timedelta(seconds=int(sys.argv[1]))).isoformat())
PY
)

RUN_STAMP=$(date -u '+%Y%m%dT%H%M%SZ')
if [ -z "$RUN_ID" ]; then
  RUN_ID="${TASK_ID:0:8}-a1-${RUN_STAMP}"
fi

CLAIM_FIELDS=$(CLAIM_EXPIRES_AT="$CLAIM_EXPIRES_AT" RUN_ID="$RUN_ID" AGENT_ID="$AGENT_ID" python3 - <<'PY'
import json, os
print(json.dumps({
  'mc_delivery_state': 'dispatching',
  'mc_claimed_by': 'mc-spawn-isolated',
  'mc_claim_expires_at': os.environ['CLAIM_EXPIRES_AT'],
  'mc_session_key': '',
  'mc_last_error': '',
  'mc_assigned_agent': os.environ['AGENT_ID'],
  'mc_run_id': os.environ['RUN_ID'],
}, ensure_ascii=False))
PY
)

if [ "$DRY_RUN" = "1" ]; then
  log "DRY-RUN: would claim and spawn task=${TASK_ID:0:8} agent=$AGENT_ID"
  printf '{"action":"dry_run","task_id":"%s","agent":"%s","run_id":"%s"}\n' "$TASK_ID" "$AGENT_ID" "$RUN_ID"
  exit 0
fi

"$MC_CLIENT" update-task "$TASK_ID" --status in_progress --comment "[mc-spawn-isolated] claiming task for atomic dispatch" --fields "$CLAIM_FIELDS" >/dev/null

SPAWN_MSG=$(cat <<EOF
ATOMIC_SPAWN_REQUEST
source=mc-spawn-isolated
task_id=$TASK_ID
agent=$AGENT_ID
run_id=$RUN_ID
title=$TITLE
priority=$PRIORITY

Description:
$DESCRIPTION

Acceptance criteria:
$ACCEPTANCE

QA checks:
$QA_CHECKS

Expected artifacts:
$EXPECTED_ARTIFACTS

Follow exactly:
1. Call sessions_spawn with:
   - task: the description above as the execution brief
   - label: "$TASK_ID"
   - agentId: "$AGENT_ID"
   - mode: "run"
   - runtime: "subagent"
   - sandbox: "inherit"
   - timeoutSeconds: 1800
2. Capture childSessionKey from the result.
3. Update MC task via:
   bash /home/openclaw/.openclaw/workspace/scripts/mc-client.sh update-task $TASK_ID --status in_progress --comment "[mc-spawn-isolated] linked session=<childSessionKey>" --fields '{"mc_session_key":"<childSessionKey>","mc_delivery_state":"linked","mc_claimed_by":null,"mc_claim_expires_at":null,"mc_last_error":"","mc_run_id":"$RUN_ID","mc_assigned_agent":"$AGENT_ID"}'
4. Reply ONLY with: SPAWN_DONE session=<childSessionKey> task=$TASK_ID run_id=$RUN_ID
EOF
)

log "SPAWN: adding isolated cron for task=${TASK_ID:0:8} agent=$AGENT_ID"
set +e
ADD_RESULT=$($OPENCLAW_BIN cron add \
  --agent main \
  --session isolated \
  --message "$SPAWN_MSG" \
  --at 1m \
  --delete-after-run \
  --name "mc-spawn-${TASK_ID:0:8}" \
  --timeout-seconds 300 \
  --light-context \
  --channel discord \
  --to 1476255906894446644 \
  --best-effort-deliver \
  --json 2>&1)
ADD_RC=$?
set -e
if [ "$ADD_RC" -ne 0 ]; then
  ERR="failed_to_add_cron"
  log "ERROR: $ERR task=${TASK_ID:0:8} output=$ADD_RESULT"
  ROLLBACK_FIELDS=$(python3 - <<'PY'
import json
print(json.dumps({'mc_delivery_state':'queued','mc_claimed_by':None,'mc_claim_expires_at':None,'mc_last_error':'failed_to_add_cron','mc_session_key':''}))
PY
)
  "$MC_CLIENT" update-task "$TASK_ID" --status inbox --comment "[mc-spawn-isolated] failed to add cron" --fields "$ROLLBACK_FIELDS" >/dev/null || true
  exit 1
fi
CRON_ID=$(ADD_RESULT="$ADD_RESULT" python3 - <<'PY'
import json, os
raw = os.environ.get('ADD_RESULT', '')
i = raw.find('{')
if i < 0:
    print('')
else:
    print((json.loads(raw[i:]).get('id') or '').strip())
PY
)

if [ -z "$CRON_ID" ]; then
  ERR="failed_to_parse_cron_id"
  log "ERROR: $ERR task=${TASK_ID:0:8} output=$ADD_RESULT"
  ROLLBACK_FIELDS=$(python3 - <<'PY'
import json
print(json.dumps({'mc_delivery_state':'queued','mc_claimed_by':None,'mc_claim_expires_at':None,'mc_last_error':'failed_to_parse_cron_id','mc_session_key':''}))
PY
)
  "$MC_CLIENT" update-task "$TASK_ID" --status inbox --comment "[mc-spawn-isolated] failed to parse cron id" --fields "$ROLLBACK_FIELDS" >/dev/null || true
  exit 1
fi

log "SPAWN: running cron immediately cron_id=$CRON_ID task=${TASK_ID:0:8}"
set +e
RUN_OUTPUT="$($OPENCLAW_BIN cron run "$CRON_ID" --expect-final --timeout 45000 2>&1)"
RUN_RC=$?
set -e
FOUND_SESSION=$(RUN_OUTPUT="$RUN_OUTPUT" python3 - <<'PY'
import os, re
m = re.search(r'SPAWN_DONE session=([^\s]+)', os.environ.get('RUN_OUTPUT', ''))
print(m.group(1) if m else '')
PY
)
if [ "$RUN_RC" -ne 0 ]; then
  log "WARN: cron run returned rc=$RUN_RC task=${TASK_ID:0:8} cron_id=$CRON_ID"
fi

if [ -n "$FOUND_SESSION" ]; then
  log "SPAWN: cron returned session=$FOUND_SESSION task=${TASK_ID:0:8}"
fi

for _ in $(seq 1 "$WAIT_SECONDS"); do
  CURRENT=$(python3 - "$TASK_ID" "$MC_CLIENT" <<'PY'
import json, subprocess, sys

task_id, mc = sys.argv[1], sys.argv[2]
raw = subprocess.check_output([mc, 'list-tasks'], text=True)
for item in (json.loads(raw or '{}').get('items', [])):
    if str(item.get('id','')) == task_id:
        f = item.get('custom_field_values') or {}
        print(json.dumps({
            'session_key': str(f.get('mc_session_key') or ''),
            'delivery_state': str(f.get('mc_delivery_state') or ''),
        }))
        raise SystemExit(0)
print('{"session_key":"","delivery_state":""}')
PY
)
  CURRENT_SESSION=$(CURRENT="$CURRENT" python3 - <<'PY'
import json, os
print(json.loads(os.environ['CURRENT'])['session_key'])
PY
)
  CURRENT_DELIVERY=$(CURRENT="$CURRENT" python3 - <<'PY'
import json, os
print(json.loads(os.environ['CURRENT'])['delivery_state'])
PY
)
  if [ -n "$CURRENT_SESSION" ] && [[ "$CURRENT_DELIVERY" =~ ^(linked|running|proof_pending|review_pending|done)$ ]]; then
    log "DONE: task=${TASK_ID:0:8} linked session=$CURRENT_SESSION"
    printf '{"action":"spawned","task_id":"%s","session_key":"%s","delivery_state":"%s","run_id":"%s","cron_id":"%s"}\n' "$TASK_ID" "$CURRENT_SESSION" "$CURRENT_DELIVERY" "$RUN_ID" "$CRON_ID"
    exit 0
  fi
  sleep 1
done

# Best-effort repair if the isolated session replied but MC link failed.
if [ -n "$FOUND_SESSION" ]; then
  log "REPAIR: linking session from cron output task=${TASK_ID:0:8} session=$FOUND_SESSION"
  LINK_FIELDS=$(FOUND_SESSION="$FOUND_SESSION" RUN_ID="$RUN_ID" AGENT_ID="$AGENT_ID" python3 - <<'PY'
import json, os
print(json.dumps({
  'mc_session_key': os.environ['FOUND_SESSION'],
  'mc_delivery_state': 'linked',
  'mc_claimed_by': None,
  'mc_claim_expires_at': None,
  'mc_last_error': '',
  'mc_run_id': os.environ['RUN_ID'],
  'mc_assigned_agent': os.environ['AGENT_ID'],
}, ensure_ascii=False))
PY
)
  "$MC_CLIENT" update-task "$TASK_ID" --status in_progress --comment "[mc-spawn-isolated] repaired link from cron output session=$FOUND_SESSION" --fields "$LINK_FIELDS" >/dev/null || true
  printf '{"action":"spawned_repaired","task_id":"%s","session_key":"%s","run_id":"%s","cron_id":"%s"}\n' "$TASK_ID" "$FOUND_SESSION" "$RUN_ID" "$CRON_ID"
  exit 0
fi

ERR_FIELDS=$(python3 - <<'PY'
import json
print(json.dumps({'mc_delivery_state':'queued','mc_claimed_by':None,'mc_claim_expires_at':None,'mc_last_error':'link_timeout','mc_session_key':''}))
PY
)
"$MC_CLIENT" update-task "$TASK_ID" --status inbox --comment "[mc-spawn-isolated] link timeout after cron run" --fields "$ERR_FIELDS" >/dev/null || true
log "ERROR: link timeout task=${TASK_ID:0:8} cron_id=$CRON_ID"
printf '{"action":"failed","reason":"link_timeout","task_id":"%s","cron_id":"%s"}\n' "$TASK_ID" "$CRON_ID"
exit 1
