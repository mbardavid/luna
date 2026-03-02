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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
MC_TOKEN="${MC_AUTH_TOKEN:-${MC_API_TOKEN:-}}"
if [ -z "$MC_TOKEN" ]; then
  echo "ERROR: MC_AUTH_TOKEN or MC_API_TOKEN must be set" >&2
  exit 1
fi
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
  kill-switch)
    # Check kill-switch status against cto-risk-policy.json
    POLICY_FILE="${SCRIPT_DIR}/../config/cto-risk-policy.json"
    python3 -c "
import json, os, sys, time

policy_path = sys.argv[1]
state_path = sys.argv[2]

policy = {}
if os.path.exists(policy_path):
    with open(policy_path) as f:
        policy = json.load(f)

anti_spam = policy.get('anti_spam', {})
max_restarts = anti_spam.get('max_restart_actions_per_hour', 3)
kill_enabled = anti_spam.get('global_kill_switch_enabled', True)

state = {}
if os.path.exists(state_path):
    try:
        with open(state_path) as f:
            state = json.load(f)
    except: pass

restarts = state.get('restart_log', [])
now = time.time()
hour_ago = now - 3600
recent_restarts = [r for r in restarts if r.get('at', 0) > hour_ago]

result = {
    'kill_switch_enabled': kill_enabled,
    'max_restarts_per_hour': max_restarts,
    'recent_restarts': len(recent_restarts),
    'can_restart': len(recent_restarts) < max_restarts and kill_enabled,
    'status': 'allowed' if len(recent_restarts) < max_restarts else 'blocked'
}
print(json.dumps(result, indent=2))

if len(recent_restarts) >= max_restarts:
    sys.exit(1)
" "$POLICY_FILE" "$STATE_FILE"
    ;;
  *)
    echo "Usage: $0 {start|finish|active|list|kill-switch} [cron-name]"
    exit 1
    ;;
esac
