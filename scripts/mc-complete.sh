#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-complete.sh — Mark an MC task as done
#
# Updates status to done, sets mc_output_summary, mc_delivered, and optionally
# sends a Discord notification.
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"
LOG_DIR="${SCRIPT_DIR}/../logs"
AUDIT_LOG="${LOG_DIR}/mc-lifecycle-audit.log"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
DEFAULT_CHANNEL="${MC_DELIVER_CHANNEL:-1473367119377731800}"

usage() {
  cat <<'USAGE'
mc-complete.sh — Mark an MC task as done

Usage:
  mc-complete.sh --task-id <id> --summary <text> [options]

Required:
  --task-id <id>        MC task ID
  --summary <text>      Summary of what was accomplished

Optional:
  --notify              Send Discord notification
  --channel <id>        Discord channel for notification (default: main channel)
  --progress <0-100>    Final progress value (default: 100)
  --cost <decimal>      Actual cost in USD
  --no-deliver          Don't set mc_delivered (useful if delivery script handles it)
  --dry-run             Print what would happen without making changes

Example:
  mc-complete.sh --task-id abc123 --summary "Created connector with 13 tests passing"
  mc-complete.sh --task-id abc123 --summary "Done" --notify --channel 123456
USAGE
}

TASK_ID=""
SUMMARY=""
NOTIFY=0
CHANNEL="$DEFAULT_CHANNEL"
PROGRESS=100
COST=""
NO_DELIVER=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-id)    TASK_ID="${2:-}";    shift 2 ;;
    --summary)    SUMMARY="${2:-}";    shift 2 ;;
    --notify)     NOTIFY=1;           shift ;;
    --channel)    CHANNEL="${2:-}";    shift 2 ;;
    --progress)   PROGRESS="${2:-100}"; shift 2 ;;
    --cost)       COST="${2:-}";       shift 2 ;;
    --no-deliver) NO_DELIVER=1;       shift ;;
    --dry-run)    DRY_RUN=1;          shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

if [ -z "$TASK_ID" ] || [ -z "$SUMMARY" ]; then
  echo "Error: --task-id and --summary are required" >&2
  usage
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client.sh not found: $MC_CLIENT" >&2
  exit 2
fi

# --- Build custom fields ---
FIELDS_JSON=$(python3 -c "
import json, sys

fields = {
    'mc_progress': int(sys.argv[1]),
    'mc_output_summary': sys.argv[2],
}
if sys.argv[3] == '0':
    fields['mc_delivered'] = True
cost = sys.argv[4].strip()
if cost:
    try:
        fields['mc_actual_cost_usd'] = float(cost)
    except:
        pass
print(json.dumps(fields, ensure_ascii=False))
" "$PROGRESS" "$SUMMARY" "$NO_DELIVER" "$COST")

COMMENT="[mc-complete] $(date -u '+%Y-%m-%d %H:%M:%S UTC') — Task completed. Summary: ${SUMMARY}"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "[dry-run] Would update task $TASK_ID:"
  echo "  status: done"
  echo "  fields: $FIELDS_JSON"
  echo "  comment: $COMMENT"
  if [ "$NOTIFY" -eq 1 ]; then
    echo "  notification: channel=$CHANNEL"
  fi
  exit 0
fi

# --- Update task ---
bash "$MC_CLIENT" update-task "$TASK_ID" \
  --status "done" \
  --comment "$COMMENT" \
  --fields "$FIELDS_JSON" >/dev/null

echo "Task $TASK_ID marked as done"

# --- Audit log ---
mkdir -p "$LOG_DIR"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] mc-complete: task_id=$TASK_ID summary=\"${SUMMARY:0:200}\"" >> "$AUDIT_LOG" 2>/dev/null || true

# --- Discord notification ---
if [ "$NOTIFY" -eq 1 ]; then
  # Fetch task title for notification
  TASK_TITLE=$(bash "$MC_CLIENT" get-task "$TASK_ID" 2>/dev/null | python3 -c "
import sys, json
try:
    t = json.load(sys.stdin)
    print(t.get('title', 'Task $TASK_ID'))
except:
    print('Task $TASK_ID')
" 2>/dev/null) || TASK_TITLE="Task $TASK_ID"

  NOTIFY_MSG="✅ **Task Completed**
**${TASK_TITLE}**
Task ID: \`${TASK_ID}\`
Summary: ${SUMMARY}"

  "$OPENCLAW_BIN" message send \
    --channel discord \
    --target "$CHANNEL" \
    --message "$NOTIFY_MSG" 2>/dev/null && echo "Notification sent to channel $CHANNEL" || echo "Warning: notification failed (non-fatal)" >&2
fi
