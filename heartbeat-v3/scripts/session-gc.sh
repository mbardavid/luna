#!/usr/bin/env bash
# session-gc.sh — Garbage collection of zombie cron sessions
#
# Runs via crontab every 30 minutes. Pure bash, zero AI.
#
# Logic:
#   - Lists all gateway sessions via `openclaw gateway call sessions.list`
#   - Finds sessions with "cron" in the key that are older than max_age
#   - Destroys them via `openclaw gateway call sessions.delete`
#   - Logs + notifies Discord about cleanup
#
# This prevents the memory leak caused by cron one-shot sessions
# that persist in the gateway after the job completes.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"
CONFIG_FILE="$V3_DIR/config/v3-config.json"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
GATEWAY_URL="${MC_GATEWAY_URL:-ws://127.0.0.1:18789}"

# Load config
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config not found: $CONFIG_FILE" >&2
    exit 1
fi

read -r MAX_AGE_HOURS NOTIF_CH <<< "$(python3 -c "
import json
with open('$CONFIG_FILE') as f:
    c = json.load(f)
print(
    c.get('session_gc_max_age_hours', 2),
    c.get('notifications_channel', '1476255906894446644')
)
")"

LOG_FILE="$WORKSPACE_DIR/logs/session-gc.log"
mkdir -p "$(dirname "$LOG_FILE")"

log() {
    local ts
    ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" >> "$LOG_FILE" 2>/dev/null || true
}

log "session-gc starting (max_age=${MAX_AGE_HOURS}h)"

# Load gateway token
GW_TOKEN="${MC_GATEWAY_TOKEN:-}"
if [ -z "$GW_TOKEN" ]; then
    GW_TOKEN="$(python3 -c "
import json
with open('$OPENCLAW_CONFIG') as f:
    print(json.load(f)['gateway']['auth']['token'])
" 2>/dev/null)" || {
        log "ERROR: cannot load gateway token"
        exit 1
    }
fi

# Get session list
sessions_raw="$($OPENCLAW_BIN gateway call \
    --url "$GATEWAY_URL" \
    --token "$GW_TOKEN" \
    --json --params '{}' \
    sessions.list 2>/dev/null)" || {
    log "ERROR: sessions.list failed"
    exit 1
}

# Find stale cron sessions
stale_sessions="$(echo "$sessions_raw" | python3 -c "
import json, sys, time

data = json.load(sys.stdin)
sessions = data if isinstance(data, list) else data.get('sessions', [])
now = time.time() * 1000
max_age_ms = $MAX_AGE_HOURS * 3600 * 1000

for s in sessions:
    key = s.get('key', '')
    if 'cron' not in key:
        continue
    updated = s.get('updatedAt', 0) or s.get('createdAt', 0) or 0
    if updated == 0:
        continue
    age_ms = now - updated
    if age_ms > max_age_ms:
        age_h = age_ms / 3600000
        print(f'{key}|{age_h:.1f}')
" 2>/dev/null)" || true

if [ -z "$stale_sessions" ]; then
    log "No stale cron sessions found"
    exit 0
fi

count=0
destroyed_keys=""

while IFS='|' read -r key age_h; do
    [ -z "$key" ] && continue

    # Try to destroy the session
    if $OPENCLAW_BIN gateway call \
        --url "$GATEWAY_URL" \
        --token "$GW_TOKEN" \
        --json --params "{\"key\":\"$key\"}" \
        sessions.delete 2>/dev/null; then
        log "Destroyed: $key (age: ${age_h}h)"
        count=$((count + 1))
        destroyed_keys="${destroyed_keys}\n• \`${key}\` (${age_h}h)"
    else
        log "WARN: failed to destroy $key"
    fi
done <<< "$stale_sessions"

if [ "$count" -gt 0 ]; then
    log "Cleaned up $count stale cron session(s)"

    openclaw message send --channel discord --target "$NOTIF_CH" \
        --message "🧹 **Session GC**: limpou **$count** sessão(ões) cron zombie.${destroyed_keys}" \
        2>/dev/null || true
fi

log "session-gc complete (cleaned: $count)"
