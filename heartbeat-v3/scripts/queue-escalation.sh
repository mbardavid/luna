#!/usr/bin/env bash
# queue-escalation.sh — Deterministic escalation for stale queue items
#
# Runs via crontab every 5 minutes. Pure bash, zero AI, zero sessions.
#
# Logic:
#   - Scans queue/pending/ for .json files
#   - >15min old → nudge Discord #notifications
#   - >30min old → alert #general-luna + move to escalated/
#   - Uses `openclaw message send` (stateless, no session creation)
#
# This is the safety net: if Luna doesn't consume the queue, humans are notified.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$V3_DIR/config/v3-config.json"

# Load config
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config not found: $CONFIG_FILE" >&2
    exit 1
fi

# Parse config with Python (stdlib only)
read -r QUEUE_DIR WARN_MIN CRIT_MIN DISCORD_CH NOTIF_CH <<< "$(python3 -c "
import json, sys
with open('$CONFIG_FILE') as f:
    c = json.load(f)
print(
    c.get('queue_dir', '$V3_DIR/queue'),
    c.get('escalation_warn_minutes', 15),
    c.get('escalation_critical_minutes', 30),
    c.get('discord_channel', '1473367119377731800'),
    c.get('notifications_channel', '1476255906894446644')
)
")"

PENDING="$QUEUE_DIR/pending"
ESCALATED="$QUEUE_DIR/escalated"
LOG_FILE="${V3_DIR%/heartbeat-v3}/logs/queue-escalation.log"

# Ensure dirs exist
mkdir -p "$PENDING" "$ESCALATED" "$(dirname "$LOG_FILE")"

log() {
    local ts
    ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" >> "$LOG_FILE" 2>/dev/null || true
}

now=$(date +%s)
escalated_count=0
warned_count=0

for f in "$PENDING"/*.json; do
    [ -f "$f" ] || continue

    file_age=$(( now - $(stat -c %Y "$f") ))
    file_age_min=$(( file_age / 60 ))

    # Extract task info (best-effort)
    task_title="$(python3 -c "import json; print(json.load(open('$f')).get('title','?'))" 2>/dev/null || echo "?")"
    task_id="$(python3 -c "import json; print(json.load(open('$f')).get('task_id','?')[:8])" 2>/dev/null || echo "?")"
    item_type="$(python3 -c "import json; print(json.load(open('$f')).get('type','?'))" 2>/dev/null || echo "?")"

    if [ "$file_age_min" -ge "$CRIT_MIN" ]; then
        # >30min: critical — escalate to #general-luna + move to escalated/
        log "CRITICAL: $task_id ($item_type) — $task_title — ${file_age_min}min in pending"

        openclaw message send --channel discord --target "$DISCORD_CH" \
            --message "🚨 **Queue Escalation** — task pendente >${CRIT_MIN}min sem processamento:
\`$task_id\` — **$task_title** (tipo: $item_type)
Luna não consumiu da queue. Requer atenção." 2>/dev/null || true

        mv "$f" "$ESCALATED/" 2>/dev/null || true
        escalated_count=$((escalated_count + 1))

    elif [ "$file_age_min" -ge "$WARN_MIN" ]; then
        # >15min: warning — nudge Discord #notifications
        log "WARN: $task_id ($item_type) — $task_title — ${file_age_min}min in pending"

        openclaw message send --channel discord --target "$NOTIF_CH" \
            --message "⏰ **Queue Warning** — task pendente >${WARN_MIN}min na queue:
\`$task_id\` — **$task_title** (tipo: $item_type)
Luna, verifique workspace/heartbeat-v3/queue/pending/." 2>/dev/null || true

        warned_count=$((warned_count + 1))
    fi
done

if [ "$escalated_count" -gt 0 ] || [ "$warned_count" -gt 0 ]; then
    log "Summary: $escalated_count escalated, $warned_count warned"
fi
