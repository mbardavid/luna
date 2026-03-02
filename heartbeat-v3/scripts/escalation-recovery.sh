#!/usr/bin/env bash
# escalation-recovery.sh — Recover tasks stuck in escalated/ queue
#
# Processes escalated items:
#   - inbox/in_progress tasks without session → move to pending/ (re-enters pipeline)
#   - done/failed tasks → move to done/ (already resolved)
#   - Items >48h in escalated → critical Discord alert
#
# Usage:
#   escalation-recovery.sh             # process all
#   escalation-recovery.sh --dry-run   # show what would happen
#
# Cron: */30 * * * *
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
QUEUE_DIR="$WORKSPACE/heartbeat-v3/queue"
ESCALATED_DIR="$QUEUE_DIR/escalated"
PENDING_DIR="$QUEUE_DIR/pending"
DONE_DIR="$QUEUE_DIR/done"
LOG_FILE="$WORKSPACE/logs/escalation-recovery.log"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1476255906894446644}"
STALE_THRESHOLD_HOURS=48

mkdir -p "$(dirname "$LOG_FILE")" "$PENDING_DIR" "$DONE_DIR"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ ! -d "$ESCALATED_DIR" ]; then
    exit 0
fi

ESCALATED_FILES=($(find "$ESCALATED_DIR" -name "*.json" -type f 2>/dev/null || true))
TOTAL=${#ESCALATED_FILES[@]}

if [ "$TOTAL" -eq 0 ]; then
    exit 0
fi

log "=== Escalation recovery: $TOTAL items ==="

RECOVERED=0
ARCHIVED=0
STALE_ALERTS=0

for FILE in "${ESCALATED_FILES[@]}"; do
    BASENAME=$(basename "$FILE")
    ITEM=$(cat "$FILE" 2>/dev/null) || continue

    TASK_ID=$(echo "$ITEM" | python3 -c "import json,sys; print(json.load(sys.stdin).get('task_id',''))" 2>/dev/null) || TASK_ID=""
    ITEM_TYPE=$(echo "$ITEM" | python3 -c "import json,sys; print(json.load(sys.stdin).get('type','dispatch'))" 2>/dev/null) || ITEM_TYPE="dispatch"
    TITLE=$(echo "$ITEM" | python3 -c "import json,sys; print(json.load(sys.stdin).get('title','?')[:50])" 2>/dev/null) || TITLE="?"

    # Check age
    FILE_AGE_HOURS=$(python3 -c "
import os, time
mtime = os.path.getmtime('$FILE')
print(int((time.time() - mtime) / 3600))
" 2>/dev/null) || FILE_AGE_HOURS=0

    if [ "$FILE_AGE_HOURS" -ge "$STALE_THRESHOLD_HOURS" ]; then
        log "STALE ALERT: $BASENAME (${FILE_AGE_HOURS}h old): $TITLE"
        STALE_ALERTS=$((STALE_ALERTS + 1))
        if [ "$DRY_RUN" -eq 0 ]; then
            timeout 8 "$OPENCLAW_BIN" message send \
                --channel discord --target "$DISCORD_CHANNEL" \
                --message "🚨 **Escalation Stale** — \`${TASK_ID:0:8}\` stuck for ${FILE_AGE_HOURS}h: $TITLE" \
                --json 2>/dev/null || true
        fi
    fi

    # Decide action based on item type
    if [ "$ITEM_TYPE" = "dispatch" ] || [ "$ITEM_TYPE" = "respawn" ]; then
        # Re-enter pipeline
        log "RECOVER: $BASENAME → pending/ ($ITEM_TYPE, ${FILE_AGE_HOURS}h old)"
        if [ "$DRY_RUN" -eq 0 ]; then
            mv "$FILE" "$PENDING_DIR/$BASENAME"
        fi
        RECOVERED=$((RECOVERED + 1))
    else
        # Alert or review items that escalated — archive
        log "ARCHIVE: $BASENAME → done/ ($ITEM_TYPE, ${FILE_AGE_HOURS}h old)"
        if [ "$DRY_RUN" -eq 0 ]; then
            mv "$FILE" "$DONE_DIR/$BASENAME"
        fi
        ARCHIVED=$((ARCHIVED + 1))
    fi
done

log "Recovery complete: recovered=$RECOVERED, archived=$ARCHIVED, stale_alerts=$STALE_ALERTS"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "DRY RUN: would recover $RECOVERED, archive $ARCHIVED, alert $STALE_ALERTS"
fi
