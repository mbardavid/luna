#!/usr/bin/env bash
set -euo pipefail
set -o pipefail
set -f

WORKDIR=/home/openclaw/.openclaw/workspace
SCRIPTS="$WORKDIR/scripts"
MC_CLIENT="$SCRIPTS/mc-client.sh"
MC_WATCHDOG="$SCRIPTS/mc-watchdog.sh"
MC_DELIVERY="$SCRIPTS/mc-delivery.sh"
MC_PHASE1_CRON="$SCRIPTS/mc-phase1-cron.sh"
MC_RESOURCE="$SCRIPTS/mc-resource-monitor.sh"

TS=$(date -u +%Y%m%d%H%M%S)
PREFIX="F1 QA ${TS}"
STATE_FILE="$WORKDIR/.mc-resource-state-f1-${TS}.json"

echo "[info] running from $WORKDIR"

call_client() {
  local out
  if ! out="$("$MC_CLIENT" "$@")"; then
    echo "client-call-failed" >&2
    return 1
  fi
  if [ -z "$out" ]; then
    echo "empty-response" >&2
    return 1
  fi
  printf '%s\n' "$out"
}

parse_json_field() {
  local json_text="$1"
  local key="$2"
  if [ -z "$json_text" ]; then
    echo ""
    return
  fi
  python3 -c 'import json, sys; print(json.loads(sys.argv[2]).get(sys.argv[1], ""))' "$key" "$json_text"
}

parse_json_nested() {
  local json_text="$1"
  local key="$2"
  if [ -z "$json_text" ]; then
    echo ""
    return
  fi
  python3 -c 'import json, sys; obj = json.loads(sys.argv[2]); values = obj.get("custom_field_values"); print(values.get(sys.argv[1], "") if isinstance(values, dict) else "")' "$key" "$json_text"
}

mc_get_task_by_id() {
  local task_id="$1"
  local tasks_json
  tasks_json="$("$MC_CLIENT" list-tasks)"

  if [ -z "$tasks_json" ]; then
    echo ""
    return
  fi

  python3 -c 'import json, sys; task_id = sys.argv[1]; payload = json.loads(sys.argv[2]); [print(__import__("json").dumps(task)) for task in payload.get("items", []) or [] if task.get("id") == task_id][:1]' "$task_id" "$tasks_json"
}

safe_int() {
  local value="$1"
  value="$(tr -d '\r\n' <<< "$value" | awk '{print $1}')"
  if [ -z "$value" ] || [ "$value" = "None" ] || [ "$value" = "null" ]; then
    echo 0
  elif printf '%s\n' "$value" | grep -Eq '^-?[0-9]+$'; then
    echo "$value"
  else
    echo 0
  fi
}

extract_last_json() {
  awk '/^\{/{line=$0} END {if (line) print line}' <<< "$1"
}

run_task() {
  local title="$1" desc="$2" status="$3" json_fields="$4"
  local task_json
  task_json="$(call_client create-task "$title" "$desc" "" medium "$status" "$json_fields")"
  echo "$(parse_json_field "$task_json" id)"
}

scenario1() {
  local fields='{"mc_session_key":"__qa_missing_1_'"${TS}"'","mc_retry_count":0,"mc_progress":10}'
  local t
  local before mid after
  local out

  t=$(run_task "${PREFIX} - missing session recovery" "Scenario 1" in_progress "$fields")
  before="$(mc_get_task_by_id "$t")"
  local before_status
  local before_retry
  before_status="$(parse_json_field "$before" status)"
  before_retry="$(parse_json_nested "$before" mc_retry_count)"

  out=$($MC_WATCHDOG --max-retries 1 --no-stall-check)
  mid="$(mc_get_task_by_id "$t")"
  local mid_status
  local mid_retry
  local mid_state
  mid_status="$(parse_json_field "$mid" status)"
  mid_retry="$(parse_json_nested "$mid" mc_retry_count)"
  mid_state="$(parse_json_nested "$mid" mc_last_error)"

  out="$out\n$($MC_WATCHDOG --max-retries 1 --no-stall-check)"
  after="$(mc_get_task_by_id "$t")"
  local after_status
  local after_retry
  local after_state
  after_status="$(parse_json_field "$after" status)"
  after_retry="$(parse_json_nested "$after" mc_retry_count)"
  after_state="$(parse_json_nested "$after" mc_last_error)"

  local pass=0
  if [ "$mid_status" = "in_progress" ] && [ "$(safe_int "$mid_retry")" -eq 1 ] && [ "$after_status" = "review" ] && [ "$after_state" = "needs_approval" ]; then
    pass=1
  fi

  cat <<JSON
{"scenario":"S1_recovery_loop","pass":$pass,"task":"$t","before_status":"$before_status","before_retry":"$before_retry","mid_status":"$mid_status","mid_retry":"$mid_retry","mid_state":"$mid_state","after_status":"$after_status","after_retry":"$after_retry","after_state":"$after_state"}
JSON
}

scenario2() {
  local fields='{"mc_session_key":"__qa_missing_2_'"${TS}"'","mc_retry_count":0,"mc_progress":20}'
  local t
  local out

  t=$(run_task "${PREFIX} - startup recovery" "Scenario 2" in_progress "$fields")
  out=$($MC_WATCHDOG --startup-recovery --no-stall-check --max-retries 0)
  local after
  after="$(mc_get_task_by_id "$t")"
  local status
  local retry
  status="$(parse_json_field "$after" status)"
  retry="$(parse_json_nested "$after" mc_retry_count)"
  local state
  state="$(parse_json_nested "$after" mc_last_error)"

  local pass=0
  if [ "$status" = "review" ] && [ "$state" = "needs_approval" ] && [ "$(safe_int "$retry")" -eq 0 ]; then
    pass=1
  fi

  cat <<JSON
{"scenario":"S2_startup_recovery","pass":$pass,"task":"$t","status":"$status","retry":"$retry","state":"$state"}
JSON
}

scenario3() {
  local fields='{"mc_progress":100,"mc_delivered":false}'
  local t
  local out
  local delivered
  local pending

  t=$(run_task "${PREFIX} - delivery dryrun" "Scenario 3" done "$fields")
  out=$(MC_DELIVERY_DRYRUN=1 $MC_DELIVERY --status done --max-to-deliver 10 --channel 1473367119377731800)
  out_json="$(extract_last_json "$out")"

  delivered="$(python3 -c 'import json, sys; print(json.loads(sys.argv[1] if sys.argv[1] else "{}").get("delivered",0))' "$out_json")"
  pending="$(python3 -c 'import json, sys; print(json.loads(sys.argv[1] if sys.argv[1] else "{}").get("pending_total",0))' "$out_json")"

  local pass=0
  if [ "$(safe_int "$delivered")" -ge 1 ]; then
    pass=1
  elif [ "$(safe_int "$pending")" -ge 1 ]; then
    pass=1
  fi

  cat <<JSON
{"scenario":"S3_delivery_dryrun","pass":$pass,"task":"$t","delivered":$delivered,"pending_total":$pending}
JSON
}

scenario4() {
  local pre
  local post
  local res1
  local res2
  local mode1
  local mode2
  local ec1
  local ec2

  pre=$(crontab -l 2>/dev/null | grep -F "OPENCLAW MC PHASE1 MONITORING BEGIN" | wc -l | tr -d ' ')
  $MC_PHASE1_CRON install >/tmp/fase1-cron-install-1.log
  $MC_PHASE1_CRON install >/tmp/fase1-cron-install-2.log
  post=$(crontab -l 2>/dev/null | grep -F "OPENCLAW MC PHASE1 MONITORING BEGIN" | wc -l | tr -d ' ')

  res1=$(MC_RESOURCE_STATE_FILE="$STATE_FILE" $MC_RESOURCE --warn-pct 90 --degrade-pct 90 --recover-pct 50 --kill-pct 95 --kill-allowlist --allowlist "no-match-path-zz/*" --test-mem-kb 100 --dry-run)
  res1_json="$(extract_last_json "$res1")"
  mode1="$(python3 -c 'import json,sys; obj=json.loads(sys.argv[1] if sys.argv[1] else "{}"); print(obj.get("mode",""))' "$res1_json")"
  ec1="$(python3 -c 'import json,sys; obj=json.loads(sys.argv[1] if sys.argv[1] else "{}"); print(obj.get("event_count",0))' "$res1_json")"

  res2=$(MC_RESOURCE_STATE_FILE="$STATE_FILE" $MC_RESOURCE --warn-pct 90 --degrade-pct 90 --recover-pct 99 --kill-pct 95 --kill-allowlist --allowlist "no-match-path-zz/*" --test-mem-kb 100 --dry-run --state-stale-ms 1000000000)
  res2_json="$(extract_last_json "$res2")"
  mode2="$(python3 -c 'import json,sys; obj=json.loads(sys.argv[1] if sys.argv[1] else "{}"); print(obj.get("mode",""))' "$res2_json")"
  ec2="$(python3 -c 'import json,sys; obj=json.loads(sys.argv[1] if sys.argv[1] else "{}"); print(obj.get("event_count",0))' "$res2_json")"

  local pass=0
  if [ "$post" -eq 1 ] && [ "$mode1" = "degrade" ] && [ "$mode2" = "normal" ]; then
    pass=1
  fi

  cat <<JSON
{"scenario":"S4_cron_resource","pass":$pass,"cron_before":$pre,"cron_after":$post,"resource1":{"mode":"$mode1","event_count":$ec1},"resource2":{"mode":"$mode2","event_count":$ec2}}
JSON
}

scenario5() {
  # Validate that missing mc_session_key is handled idempotently (no spam loops).
  local t
  local out1 out2
  local after1 after2
  local status1 status2
  local err1 err2

  t=$(run_task "${PREFIX} - missing session key" "Scenario 5" in_progress '{"mc_retry_count":0,"mc_progress":5}')

  out1=$($MC_WATCHDOG --max-retries 2 --no-stall-check)
  after1="$(mc_get_task_by_id "$t")"
  status1="$(parse_json_field "$after1" status)"
  err1="$(parse_json_nested "$after1" mc_last_error)"

  out2=$($MC_WATCHDOG --max-retries 2 --no-stall-check)
  after2="$(mc_get_task_by_id "$t")"
  status2="$(parse_json_field "$after2" status)"
  err2="$(parse_json_nested "$after2" mc_last_error)"

  local pass=0
  if [ "$status1" = "review" ] && [ "$err1" = "missing_session_key" ] && [ "$status2" = "review" ] && [ "$err2" = "missing_session_key" ]; then
    pass=1
  fi

  cat <<JSON
{"scenario":"S5_missing_session_key_idempotent","pass":$pass,"task":"$t","status1":"$status1","err1":"$err1","status2":"$status2","err2":"$err2"}
JSON
}

S1="$(scenario1)"
S2="$(scenario2)"
S3="$(scenario3)"
S4="$(scenario4)"
S5="$(scenario5)"

cat <<EOF_SUM
{"run_id":"$TS","results":[$S1,$S2,$S3,$S4,$S5]}
EOF_SUM
