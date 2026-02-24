#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/openclaw/.openclaw/workspace}"
LOGDIR="${MC_CRON_LOG_DIR:-$WORKDIR/logs}"
MAX_BYTES="${MC_LOG_MAX_BYTES:-10485760}" # 10MB
KEEP_BYTES="${MC_LOG_KEEP_BYTES:-2097152}" # 2MB

mkdir -p "$LOGDIR"

rotate_one() {
  local path="$1"
  [ -f "$path" ] || return 0
  local size
  size=$(wc -c < "$path" | tr -d ' ')
  if [ "$size" -le "$MAX_BYTES" ]; then
    return 0
  fi
  # Keep last KEEP_BYTES
  tail -c "$KEEP_BYTES" "$path" > "${path}.tmp" || true
  mv "${path}.tmp" "$path"
  echo "[mc-log-rotate] truncated $path from $size bytes" >&2
}

for f in \
  "$LOGDIR/mc-watchdog-cron.log" \
  "$LOGDIR/mc-delivery-cron.log" \
  "$LOGDIR/mc-resource-monitor-cron.log" \
  "$LOGDIR/mc-approvals-notify-cron.log" \
  "$LOGDIR/gateway-restart-notify.log"
do
  rotate_one "$f"
done
