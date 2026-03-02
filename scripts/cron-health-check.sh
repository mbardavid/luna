#!/usr/bin/env bash
# cron-health-check.sh — Detect failed OpenClaw crons and alert
#
# Checks `openclaw cron list` for status=error and notifies Discord.
# Designed to run from system crontab (independent of gateway).
#
# Usage: cron-health-check.sh [--notify]
#
set -euo pipefail

NOTIFY=0
STATE_FILE="/tmp/.cron-health-state.json"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
LOG_FILE="/home/openclaw/.openclaw/workspace/logs/cron-health-check.log"

[ "${1:-}" = "--notify" ] && NOTIFY=1

mkdir -p "$(dirname "$LOG_FILE")"

log() {
    echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $1" >> "$LOG_FILE"
}

# Get cron list
CRON_OUTPUT=$(timeout 15 openclaw cron list --json 2>/dev/null) || {
    log "WARN: openclaw cron list failed or timed out"
    exit 0
}

# Parse for errors
ERRORS=$(echo "$CRON_OUTPUT" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    crons = data if isinstance(data, list) else data.get('items', data.get('crons', []))
    errors = [c for c in crons if c.get('status') == 'error']
    for e in errors:
        print(f\"{e.get('name','?')} (agent: {e.get('agent','?')})\")
except:
    pass
" 2>/dev/null) || ERRORS=""

if [ -z "$ERRORS" ]; then
    log "OK: no cron errors detected"
    # Clear state
    echo '{"last_errors":[]}' > "$STATE_FILE"
    exit 0
fi

# Check if already notified
PREV_ERRORS=""
if [ -f "$STATE_FILE" ]; then
    PREV_ERRORS=$(python3 -c "
import json
with open('$STATE_FILE') as f:
    print('\n'.join(json.load(f).get('last_errors',[])))
" 2>/dev/null) || PREV_ERRORS=""
fi

# Compare
NEW_ERRORS=""
while IFS= read -r line; do
    if ! echo "$PREV_ERRORS" | grep -qF "$line"; then
        NEW_ERRORS="${NEW_ERRORS}${line}\n"
    fi
done <<< "$ERRORS"

if [ -z "$NEW_ERRORS" ]; then
    log "OK: cron errors exist but already notified"
    exit 0
fi

log "ALERT: new cron errors detected: $ERRORS"

# Update state
echo "$ERRORS" | python3 -c "
import json, sys
errors = [l.strip() for l in sys.stdin if l.strip()]
with open('$STATE_FILE', 'w') as f:
    json.dump({'last_errors': errors}, f)
" 2>/dev/null

# Notify if requested
if [ "$NOTIFY" -eq 1 ]; then
    MSG="⚠️ **Cron errors detected:**\n$(echo -e "$ERRORS" | sed 's/^/- /')\n\nCheck with \`openclaw cron list\`"
    timeout 10 openclaw message send \
        --channel discord \
        --target "$DISCORD_CHANNEL" \
        --message "$MSG" \
        --json 2>/dev/null || log "WARN: Discord notification failed"
fi
