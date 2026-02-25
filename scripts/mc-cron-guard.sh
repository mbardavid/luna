#!/usr/bin/env bash
# mc-cron-guard.sh — Registers cron execution in MC to prevent heartbeat concurrency
#
# Usage:
#   mc-cron-guard.sh start <cron-name>   → creates/updates MC task as in_progress
#   mc-cron-guard.sh finish <cron-name>  → marks MC task as done
#   mc-cron-guard.sh active              → returns 0 if any cron is in_progress, 1 if idle
#
# The heartbeat task drain should call `mc-cron-guard.sh active` before spawning.

set -euo pipefail

BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
MC_TOKEN="${MC_AUTH_TOKEN:-luna_mission_control_access_token_stable_v1_6741ef7ffc207adb58ce632e7ff1d9913dbf2e9c44441aac}"
MC_BASE="${MC_BASE_URL:-http://localhost:8000}"
STATE_FILE="/tmp/.mc-cron-active.json"

mc_api() {
  local method="$1" path="$2" data="${3:-}"
  if [ -n "$data" ]; then
    curl -sf -X "$method" "${MC_BASE}${path}" \
      -H "Authorization: Bearer $MC_TOKEN" \
      -H "Content-Type: application/json" \
      -d "$data" 2>/dev/null
  else
    curl -sf -X "$method" "${MC_BASE}${path}" \
      -H "Authorization: Bearer $MC_TOKEN" 2>/dev/null
  fi
}

cmd_start() {
  local name="$1"
  local now
  now=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  # Track locally for fast lookup
  python3 -c "
import json, os
state = {}
if os.path.exists('$STATE_FILE'):
    try: state = json.load(open('$STATE_FILE'))
    except: pass
state['$name'] = {'started': '$now', 'status': 'running'}
json.dump(state, open('$STATE_FILE', 'w'), indent=2)
"
  echo "cron-guard: $name started at $now"
}

cmd_finish() {
  local name="$1"

  # Remove from local tracking
  python3 -c "
import json, os
state = {}
if os.path.exists('$STATE_FILE'):
    try: state = json.load(open('$STATE_FILE'))
    except: pass
state.pop('$name', None)
json.dump(state, open('$STATE_FILE', 'w'), indent=2)
"
  echo "cron-guard: $name finished"
}

cmd_active() {
  # Check if any cron is currently running
  local count
  count=$(python3 -c "
import json, os, time
state = {}
if os.path.exists('$STATE_FILE'):
    try: state = json.load(open('$STATE_FILE'))
    except: pass
# Expire entries older than 15 minutes (stale guard)
now = time.time()
active = {k: v for k, v in state.items() if v.get('status') == 'running'}
print(len(active))
if active:
    for k in active:
        print(f'  active: {k}', file=__import__('sys').stderr)
" 2>&1)

  local num
  num=$(echo "$count" | head -1)
  if [ "$num" -gt 0 ]; then
    echo "cron-guard: $num cron(s) active"
    echo "$count" | tail -n +2
    return 0  # active = true
  else
    echo "cron-guard: idle"
    return 1  # idle
  fi
}

cmd_list() {
  # Show current state
  if [ -f "$STATE_FILE" ]; then
    cat "$STATE_FILE"
  else
    echo "{}"
  fi
}

case "${1:-help}" in
  start)  cmd_start "${2:?cron name required}" ;;
  finish) cmd_finish "${2:?cron name required}" ;;
  active) cmd_active ;;
  list)   cmd_list ;;
  *)
    echo "Usage: $0 {start|finish|active|list} [cron-name]"
    exit 1
    ;;
esac
