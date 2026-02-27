#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-fail.sh — Mark an MC task as failed with optional retry logic
#
# Increments mc_retry_count. If under max retries, moves task back to inbox
# for re-spawn. If at max retries, moves to review with error details.
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"
LOG_DIR="${SCRIPT_DIR}/../logs"
AUDIT_LOG="${LOG_DIR}/mc-lifecycle-audit.log"
DEFAULT_MAX_RETRIES="${MC_MAX_RETRIES:-2}"

usage() {
  cat <<'USAGE'
mc-fail.sh — Mark an MC task as failed with retry logic

Usage:
  mc-fail.sh --task-id <id> --error <message> [options]

Required:
  --task-id <id>        MC task ID
  --error <message>     Error description

Optional:
  --retry               Enable retry logic (re-queue if under max retries)
  --max-retries <n>     Max retry attempts (default: 2, env: MC_MAX_RETRIES)
  --notify              Send Discord notification on final failure
  --channel <id>        Discord channel for notification
  --dry-run             Print what would happen without making changes

Without --retry:
  Task is immediately marked as failed with the error.

With --retry:
  - If retry_count < max_retries: move to inbox for re-spawn
  - If retry_count >= max_retries: move to review with error

Example:
  mc-fail.sh --task-id abc123 --error "Timeout after 900s" --retry
  mc-fail.sh --task-id abc123 --error "Auth failed" --max-retries 3 --retry
USAGE
}

TASK_ID=""
ERROR_MSG=""
RETRY=0
MAX_RETRIES="$DEFAULT_MAX_RETRIES"
NOTIFY=0
CHANNEL="${MC_DELIVER_CHANNEL:-1476255906894446644}"
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-id)      TASK_ID="${2:-}";       shift 2 ;;
    --error)        ERROR_MSG="${2:-}";     shift 2 ;;
    --retry)        RETRY=1;               shift ;;
    --max-retries)  MAX_RETRIES="${2:-2}";  shift 2 ;;
    --notify)       NOTIFY=1;              shift ;;
    --channel)      CHANNEL="${2:-}";       shift 2 ;;
    --dry-run)      DRY_RUN=1;             shift ;;
    -h|--help)      usage; exit 0 ;;
    *)              echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$ERROR_MSG" ]; then
  echo "Error: --task-id and --error are required" >&2
  usage
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client.sh not found: $MC_CLIENT" >&2
  exit 2
fi

# --- Get current task state ---
# The MC API may not support GET single task, so we fetch via list and filter
TASK_JSON=$(bash "$MC_CLIENT" list-tasks 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = data.get('items', data) if isinstance(data, dict) else data
task_id = sys.argv[1]
for t in (items if isinstance(items, list) else []):
    if t.get('id') == task_id:
        print(json.dumps(t))
        sys.exit(0)
# Try each status
sys.exit(1)
" "$TASK_ID" 2>/dev/null) || {
  # Fallback: try specific statuses
  for status in in_progress inbox review blocked; do
    TASK_JSON=$(bash "$MC_CLIENT" list-tasks "$status" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
items = data.get('items', data) if isinstance(data, dict) else data
task_id = sys.argv[1]
for t in (items if isinstance(items, list) else []):
    if t.get('id') == task_id:
        print(json.dumps(t))
        sys.exit(0)
sys.exit(1)
" "$TASK_ID" 2>/dev/null) && break
  done
}

if [ -z "$TASK_JSON" ]; then
  echo "Warning: Could not fetch current task state for $TASK_ID, using defaults" >&2
  TASK_JSON='{"custom_field_values": {}}'
fi

CURRENT_STATE=$(python3 -c "
import json, sys
task = json.loads(sys.argv[1])
fields = task.get('custom_field_values') or {}
retry_count = int(fields.get('mc_retry_count', 0) or 0)
progress = int(fields.get('mc_progress', 0) or 0)
title = task.get('title', '')
print(json.dumps({
    'retry_count': retry_count,
    'progress': progress,
    'title': title,
}))
" "$TASK_JSON")

CURRENT_RETRY=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['retry_count'])" "$CURRENT_STATE")
CURRENT_PROGRESS=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['progress'])" "$CURRENT_STATE")
TASK_TITLE=$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['title'])" "$CURRENT_STATE")

NOW_UTC="$(date -u '+%Y-%m-%d %H:%M:%S UTC')"

if [ "$RETRY" -eq 0 ]; then
  # --- No retry: mark as failed directly ---
  FIELDS_JSON=$(python3 -c "
import json
print(json.dumps({
    'mc_last_error': '$ERROR_MSG'[:500],
    'mc_retry_count': $CURRENT_RETRY,
    'mc_progress': $CURRENT_PROGRESS,
}))
")
  COMMENT="[mc-fail] $NOW_UTC — Failed: ${ERROR_MSG}"

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] Would mark task $TASK_ID as failed"
    echo "  error: $ERROR_MSG"
    exit 0
  fi

  bash "$MC_CLIENT" update-task "$TASK_ID" \
    --status "failed" \
    --comment "$COMMENT" \
    --fields "$FIELDS_JSON" >/dev/null

  echo "Task $TASK_ID marked as failed: $ERROR_MSG"

else
  # --- Retry logic ---
  NEXT_RETRY=$((CURRENT_RETRY + 1))

  if [ "$NEXT_RETRY" -lt "$MAX_RETRIES" ]; then
    # Under max: move back to inbox for re-spawn
    NEW_STATUS="inbox"
    COMMENT="[mc-fail] $NOW_UTC — Retry $NEXT_RETRY/$MAX_RETRIES. Error: ${ERROR_MSG}. Moving to inbox for re-spawn."
    FIELDS_JSON=$(python3 -c "
import json
print(json.dumps({
    'mc_retry_count': $NEXT_RETRY,
    'mc_last_error': 'retry',
    'mc_progress': $CURRENT_PROGRESS,
    'mc_session_key': '',
}))
")

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] Would retry task $TASK_ID (attempt $NEXT_RETRY/$MAX_RETRIES)"
      echo "  new status: inbox"
      exit 0
    fi

    bash "$MC_CLIENT" update-task "$TASK_ID" \
      --status "$NEW_STATUS" \
      --comment "$COMMENT" \
      --fields "$FIELDS_JSON" >/dev/null

    echo "Task $TASK_ID moved to inbox for retry ($NEXT_RETRY/$MAX_RETRIES)"

  else
    # At max retries: move to review
    NEW_STATUS="review"
    COMMENT="[mc-fail] $NOW_UTC — Max retries ($MAX_RETRIES) exhausted. Error: ${ERROR_MSG}. Requires manual review."
    FIELDS_JSON=$(python3 -c "
import json
print(json.dumps({
    'mc_retry_count': $NEXT_RETRY,
    'mc_last_error': '$ERROR_MSG'[:500],
    'mc_progress': $CURRENT_PROGRESS,
}))
")

    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] Would move task $TASK_ID to review (max retries exhausted)"
      echo "  retries: $NEXT_RETRY/$MAX_RETRIES"
      exit 0
    fi

    bash "$MC_CLIENT" update-task "$TASK_ID" \
      --status "$NEW_STATUS" \
      --comment "$COMMENT" \
      --fields "$FIELDS_JSON" >/dev/null

    echo "Task $TASK_ID moved to review (max retries $MAX_RETRIES exhausted)"
  fi
fi

# --- Audit log ---
mkdir -p "$LOG_DIR"
echo "[$NOW_UTC] mc-fail: task_id=$TASK_ID retry=$RETRY retry_count=$CURRENT_RETRY->$((CURRENT_RETRY + (RETRY == 1 ? 1 : 0))) error=\"${ERROR_MSG:0:200}\"" >> "$AUDIT_LOG" 2>/dev/null || true

# --- Discord notification (only on final failure) ---
if [ "$NOTIFY" -eq 1 ] && { [ "$RETRY" -eq 0 ] || [ "$((CURRENT_RETRY + 1))" -ge "$MAX_RETRIES" ]; }; then
  OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
  NOTIFY_MSG="❌ **Task Failed**
**${TASK_TITLE}**
Task ID: \`${TASK_ID}\`
Error: ${ERROR_MSG}
Retries: ${CURRENT_RETRY}/${MAX_RETRIES}"

  "$OPENCLAW_BIN" message send \
    --channel discord \
    --target "$CHANNEL" \
    --message "$NOTIFY_MSG" 2>/dev/null || true
fi
