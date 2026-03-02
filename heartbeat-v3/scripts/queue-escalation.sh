#!/usr/bin/env bash
# queue-escalation.sh — Deterministic escalation for stale queue items
#
# Runs via crontab every 5 minutes. Pure bash, zero AI, zero sessions.
#
# Logic:
#   - Scans queue/pending/ for .json files
#   - Checks MC API: if task is done/review → auto-clean (not stale)
#   - >15min old → nudge Discord #notifications (ONCE per task)
#   - >30min old → alert #general-luna + move to escalated/
#   - Uses `openclaw message send` (stateless, no session creation)
#   - Dedup: tracks notified task_ids in state file
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
CONFIG_FILE="$V3_DIR/config/v3-config.json"

# Load bashrc for MC_API_TOKEN
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

# Load config
if [ ! -f "$CONFIG_FILE" ]; then
    echo "ERROR: Config not found: $CONFIG_FILE" >&2
    exit 1
fi

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
DONE="$QUEUE_DIR/done"
LOG_FILE="${V3_DIR%/heartbeat-v3}/logs/queue-escalation.log"
STATE_FILE="/tmp/.queue-escalation-state.json"

mkdir -p "$PENDING" "$ESCALATED" "$DONE" "$(dirname "$LOG_FILE")"

log() {
    local ts
    ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" >> "$LOG_FILE" 2>/dev/null || true
}

MC_API_TOKEN="${MC_API_TOKEN:-}"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"

# Load dedup state
notified_tasks=""
if [ -f "$STATE_FILE" ]; then
    notified_tasks=$(cat "$STATE_FILE" 2>/dev/null || echo "")
fi

now=$(date +%s)
escalated_count=0
warned_count=0
cleaned_count=0

for f in "$PENDING"/*.json; do
    [ -f "$f" ] || continue

    file_age=$(( now - $(stat -c %Y "$f") ))
    file_age_min=$(( file_age / 60 ))

    task_title="$(python3 -c "import json; print(json.load(open('$f')).get('title','?'))" 2>/dev/null || echo "?")"
    task_id="$(python3 -c "import json; print(json.load(open('$f')).get('task_id','?'))" 2>/dev/null || echo "?")"
    task_short="${task_id:0:8}"

    # Check MC: is this task already resolved?
    if [ -n "$MC_API_TOKEN" ] && [ "$task_id" != "?" ]; then
        mc_status=$(python3 -c "
import json, urllib.request
try:
    url = '$MC_API_URL/api/v1/boards/$MC_BOARD_ID/tasks'
    req = urllib.request.Request(url, headers={'Authorization': 'Bearer $MC_API_TOKEN'})
    resp = urllib.request.urlopen(req, timeout=5)
    tasks = json.loads(resp.read()).get('items', [])
    t = next((t for t in tasks if t['id'] == '$task_id'), None)
    if t:
        cf = t.get('custom_field_values') or {}
        has_session = bool(cf.get('mc_session_key', ''))
        print(f'{t[\"status\"]}|{has_session}')
    else:
        print('not_found|false')
except:
    print('error|false')
" 2>/dev/null || echo "error|false")

        status=$(echo "$mc_status" | cut -d'|' -f1)
        has_session=$(echo "$mc_status" | cut -d'|' -f2)

        # Auto-clean: task already done, or in_progress with session, or in review
        if [ "$status" = "done" ] || [ "$status" = "review" ] || { [ "$status" = "in_progress" ] && [ "$has_session" = "True" ]; }; then
            log "AUTO-CLEAN: $task_short ($status) — $task_title"
            mv "$f" "$DONE/" 2>/dev/null || rm -f "$f"
            cleaned_count=$((cleaned_count + 1))
            continue
        fi
    fi

    # Dedup: skip if already notified for this task_id
    if echo "$notified_tasks" | grep -q "$task_id"; then
        log "DEDUP: $task_short already notified — skipping"
        # Still move to escalated if old enough
        if [ "$file_age_min" -ge "$CRIT_MIN" ]; then
            mv "$f" "$ESCALATED/" 2>/dev/null || true
        fi
        continue
    fi

    if [ "$file_age_min" -ge "$CRIT_MIN" ]; then
        log "CRITICAL: $task_short — $task_title — ${file_age_min}min"

        openclaw message send --channel discord --target "$DISCORD_CH" \
            --message "🚨 **Queue Escalation** — task pendente >${CRIT_MIN}min:
\`$task_short\` — **$task_title**
Luna não consumiu. Requer atenção." 2>/dev/null || true

        mv "$f" "$ESCALATED/" 2>/dev/null || true
        escalated_count=$((escalated_count + 1))
        # Record as notified
        notified_tasks="$notified_tasks $task_id"

    elif [ "$file_age_min" -ge "$WARN_MIN" ]; then
        log "WARN: $task_short — $task_title — ${file_age_min}min"

        openclaw message send --channel discord --target "$NOTIF_CH" \
            --message "⏰ **Queue Warning** — task pendente >${WARN_MIN}min:
\`$task_short\` — **$task_title**" 2>/dev/null || true

        warned_count=$((warned_count + 1))
        notified_tasks="$notified_tasks $task_id"
    fi
done

# Save dedup state (trim to last 50 task IDs)
echo "$notified_tasks" | tr ' ' '\n' | tail -50 | tr '\n' ' ' > "$STATE_FILE" 2>/dev/null || true

if [ "$escalated_count" -gt 0 ] || [ "$warned_count" -gt 0 ] || [ "$cleaned_count" -gt 0 ]; then
    log "Summary: $escalated_count escalated, $warned_count warned, $cleaned_count auto-cleaned"
fi
