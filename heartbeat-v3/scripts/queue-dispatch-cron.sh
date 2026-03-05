#!/usr/bin/env bash
# queue-dispatch-cron.sh — Consume pending queue items via mc-fast-dispatch.sh
#
# Designed to run as a cron job every 3 minutes.
# Processes one item per run to avoid overloading the system.
#
# Flow:
#   1. Check queue/pending/ and queue/active/
#   2. Recover stale active items (>=30min) back to pending/
#   3. Move oldest pending item to active/
#   4. Dispatch via mc-fast-dispatch.sh --from-queue <file>
#   5. On success mc-fast-dispatch moves item to done/
#   6. On failure we rollback MC in the dispatcher and remove active item (no litter)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE="${WORKSPACE:-$(dirname "$(dirname "$SCRIPT_DIR")")}"  # workspace/scripts parent of heartbeat-v3
QUEUE_DIR="$V3_DIR/queue"
PENDING="$QUEUE_DIR/pending"
ACTIVE="$QUEUE_DIR/active"
DONE="$QUEUE_DIR/done"
FAILED="$QUEUE_DIR/failed"
LOG_FILE="$WORKSPACE/logs/queue-dispatch-cron.log"
FAST_DISPATCH="$WORKSPACE/scripts/mc-fast-dispatch.sh"

# Optional dry-run from caller: QUEUE_DISPATCH_DRY_RUN=1
DRY_RUN="${QUEUE_DISPATCH_DRY_RUN:-0}"

MAX_ACTIVE_AGE_MINUTES=30

mkdir -p "$PENDING" "$ACTIVE" "$DONE" "$FAILED" "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

mark_failed() {
    local fname="$1"
    local reason="$2"
    local src="$ACTIVE/$fname"
    local dst="$FAILED/$fname"

    if [ -f "$src" ]; then
        mkdir -p "$FAILED"
        if ! mv "$src" "$dst" 2>/dev/null; then
            rm -f "$src"
        fi
        log "DISPATCH: moved failed item $fname to failed (${reason})"
        if [ -n "$reason" ]; then
            python3 - "$dst" "$reason" <<'PY'
import json
import sys
path, reason = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        payload = json.load(f)
    payload["mc_last_error"] = reason
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)
except Exception:
    pass
PY
        fi
    fi
}

# Load environment (MC_API_TOKEN, etc.)
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

# ─── Phase 1: Recover stuck active items ──────────────────────────────────
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

# ─── Phase 2: Process one pending item ────────────────────────────────────
ITEM="$(ls -1 "$PENDING"/*.json 2>/dev/null | head -1 || true)"

if [ -z "$ITEM" ] || [ ! -f "$ITEM" ]; then
    # Nothing to do
    exit 0
fi

FNAME=$(basename "$ITEM")
ACTIVE_ITEM="$ACTIVE/$FNAME"
log "DISPATCH: claiming $FNAME"

mv "$ITEM" "$ACTIVE_ITEM" 2>/dev/null || {
    log "DISPATCH: failed to move $FNAME to active"
    exit 1
}

if [ "$DRY_RUN" = "1" ]; then
    log "DISPATCH: dry-run for $FNAME"
    if [ -x "$FAST_DISPATCH" ] && "$FAST_DISPATCH" --from-queue "$ACTIVE_ITEM" --dry-run >> "$LOG_FILE" 2>&1; then
        mv "$ACTIVE_ITEM" "$ITEM"
        log "DISPATCH: dry-run complete for $FNAME (returned to pending)"
        exit 0
    else
        EXIT_CODE=$?
        log "DISPATCH: dry-run failed for $FNAME (exit=$EXIT_CODE)"
        mark_failed "$FNAME" "dry-run failure"
        exit 0
    fi
fi

if [ ! -x "$FAST_DISPATCH" ]; then
    log "DISPATCH ERROR: fast-dispatch script missing or not executable: $FAST_DISPATCH"
    mark_failed "$FNAME" "fast-dispatch unavailable"
    exit 1
fi

if timeout 700 "$FAST_DISPATCH" --from-queue "$ACTIVE_ITEM" >> "$LOG_FILE" 2>&1; then
    log "DISPATCH: success for $FNAME"
    # fast-dispatch already moves processed file to done/ on success
else
    EXIT_CODE=$?
    log "DISPATCH: failed for $FNAME (exit=$EXIT_CODE)"
    # fast-dispatch should rollback MC task on failure; keep failed copy for observability
    mark_failed "$FNAME" "dispatch script failed (exit $EXIT_CODE)"
fi
