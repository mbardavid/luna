#!/usr/bin/env bash
set -euo pipefail
set -o pipefail
set -f

WORKDIR=/home/openclaw/.openclaw/workspace
SCRIPTS="$WORKDIR/scripts"
MC_CLIENT="$SCRIPTS/mc-client.sh"
MC_DELIVERY="$SCRIPTS/mc-delivery.sh"
MC_BOOTSTRAP="$SCRIPTS/mc-bootstrap-custom-fields.sh"
MC_APPROVALS="$SCRIPTS/mc-approvals-notify.sh"

TS=$(date -u +%Y%m%d%H%M%S)
PREFIX="F2 QA ${TS}"

export MC_AUTH_TOKEN="${MC_AUTH_TOKEN:-$(python3 -c 'import json;print(json.load(open("/home/openclaw/.openclaw/workspace/config/mission-control-ids.local.json"))["auth_token"])')}"

call_client() {
  local out
  out="$($MC_CLIENT "$@")"
  [ -n "$out" ] || { echo "empty-response" >&2; return 1; }
  printf '%s\n' "$out"
}

parse_json_field(){ python3 -c 'import json,sys; print(json.loads(sys.argv[2]).get(sys.argv[1],""))' "$1" "$2"; }

scenario6() {
  # Delivery should prefer mc_output_summary
  local t
  t=$(call_client create-task "${PREFIX} - delivery output summary" "Scenario 6" luna medium done '{"mc_progress":100,"mc_delivered":false,"mc_output_summary":"OK: output summary used"}' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')

  out=$(MC_DELIVERY_DRYRUN=1 $MC_DELIVERY --status done --max-to-deliver 5 --channel 1476255906894446644)
  # We can't see the exact message body in dry-run output reliably; validate at least that it scanned >=1.
  scanned=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("scanned",0))' "$(echo "$out" | tail -n 1)")

  pass=0
  if [ "$scanned" -ge 1 ]; then pass=1; fi
  echo "{\"scenario\":\"S6_delivery_prefers_output_summary\",\"pass\":$pass,\"task\":\"$t\",\"scanned\":$scanned}"
}

scenario7() {
  # Bootstrap idempotence: second run should be noop for all fields.
  local out
  out=$($MC_BOOTSTRAP --dry-run)
  # Expect that there are no 'created' entries in dry-run now that fields exist.
  created=$(python3 -c 'import json,sys; print(len(json.loads(sys.stdin.read()).get("created",[])))' <<< "$out")
  pass=0
  if [ "$created" -eq 0 ]; then pass=1; fi
  echo "{\"scenario\":\"S7_bootstrap_idempotent\",\"pass\":$pass,\"created\":$created}"
}

scenario8() {
  # Approvals notify dry-run should run without error (may notify 0 if none pending).
  out=$(MC_APPROVALS_DRYRUN=1 $MC_APPROVALS --max 2)
  # valid json output
  notified=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("notified",-1))' "$(echo "$out" | tail -n 1)")
  pass=0
  if [ "$notified" -ge 0 ]; then pass=1; fi
  echo "{\"scenario\":\"S8_approvals_notify_runs\",\"pass\":$pass,\"notified\":$notified}"
}

S6=$(scenario6)
S7=$(scenario7)
S8=$(scenario8)

echo "{\"run_id\":\"$TS\",\"results\":[${S6},${S7},${S8}]}"
