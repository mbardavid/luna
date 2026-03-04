#!/usr/bin/env bash
# queue-dispatch-cron.sh — Consume pending queue items via mc-fast-dispatch.sh
#
# Designed to run as a cron job every 3 minutes.
# Processes one item per run to avoid overloading the system.
#
# Flow:
#   1. Check queue/pending/ for items
#   2. Pick the oldest/highest-priority item
#   3. Dispatch via mc-fast-dispatch.sh --from-queue <file>
#   4. fast-dispatch handles: spawn → link session_key → move to done/
#
# Also recovers stuck items in queue/active/ older than 30 minutes
# by moving them back to queue/pending/ for re-processing.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE="${WORKSPACE:-$(dirname "$(dirname "$SCRIPT_DIR")")}"
QUEUE_DIR="$V3_DIR/queue"
PENDING="$QUEUE_DIR/pending"
ACTIVE="$QUEUE_DIR/active"
LOG_FILE="$WORKSPACE/logs/queue-dispatch-cron.log"
FAST_DISPATCH="$WORKSPACE/scripts/mc-fast-dispatch.sh"

MAX_ACTIVE_AGE_MINUTES=30

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

# Load environment (MC_API_TOKEN, etc.)
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

# ─── Phase 1: Recover stuck active items ─────────────────────────────────────

for f in "$ACTIVE"/*.json; do
    [ -f "$f" ] || continue
    age_seconds=$(( $(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo "0") ))
    age_minutes=$(( age_seconds / 60 ))
    if [ "$age_minutes" -ge "$MAX_ACTIVE_AGE_MINUTES" ]; then
        fname=$(basename "$f")
        log "RECOVER: moving stuck active item back to pending: $fname (${age_minutes}min old)"
        mv "$f" "$PENDING/$fname" 2>/dev/null || true
    fi
done

# ─── Phase 2: Process one pending item ───────────────────────────────────────

# Find oldest pending item (sorted by filename which includes timestamp)
ITEM=$(ls -1 "$PENDING"/*.json 2>/dev/null | head -1)

if [ -z "$ITEM" ] || [ ! -f "$ITEM" ]; then
    # Nothing to do
    exit 0
fi

FNAME=$(basename "$ITEM")
log "DISPATCH: processing $FNAME"

# Dispatch via fast-dispatch
if "$FAST_DISPATCH" --from-queue "$ITEM" >> "$LOG_FILE" 2>&1; then
    log "DISPATCH: success for $FNAME"
else
    EXIT_CODE=$?
    log "DISPATCH: failed for $FNAME (exit=$EXIT_CODE)"
    # Move to failed/ if dispatch fails
    if [ -f "$ITEM" ]; then
        mv "$ITEM" "$QUEUE_DIR/failed/$FNAME" 2>/dev/null || true
        log "DISPATCH: moved $FNAME to failed/"
    fi
fi
