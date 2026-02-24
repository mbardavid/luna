#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/openclaw/.openclaw/workspace}"
SCRIPT_DIR="${SCRIPT_DIR:-$WORKDIR/scripts}"
CRON_EVERY_MINUTES="${MC_CRON_EVERY_MINUTES:-5}"
CRON_LOG_DIR="${MC_CRON_LOG_DIR:-$WORKDIR/logs}"
MC_CHANNEL="${MC_APPROVALS_CHANNEL:-1473367119377731800}"

ACTION="${1:-install}"
MARKER_BEGIN="# OPENCLAW MC PHASE3 APPROVALS BEGIN"
MARKER_END="# OPENCLAW MC PHASE3 APPROVALS END"

ensure_scripts() {
  [ -x "$SCRIPT_DIR/mc-approvals-notify.sh" ] || { echo "missing: mc-approvals-notify.sh" >&2; exit 1; }
  [ -x "$SCRIPT_DIR/mc-cost-report.sh" ] || { echo "missing: mc-cost-report.sh" >&2; exit 1; }
}

build_block() {
  cat <<EOF
$MARKER_BEGIN
*/$CRON_EVERY_MINUTES * * * * PATH=/usr/local/bin:/usr/bin:/bin $SCRIPT_DIR/mc-approvals-notify.sh --channel "$MC_CHANNEL" >> "$CRON_LOG_DIR/mc-approvals-notify-cron.log" 2>&1
5 9 * * 1 PATH=/usr/local/bin:/usr/bin:/bin $SCRIPT_DIR/mc-cost-report.sh --channel "$MC_CHANNEL" >> "$CRON_LOG_DIR/mc-cost-report-cron.log" 2>&1
$MARKER_END
EOF
}

read_existing_crontab() { crontab -l 2>/dev/null || true; }

strip_existing_block() {
  awk '
BEGIN {drop=0}
$0 == MARKER_BEGIN {drop=1; next}
$0 == MARKER_END {drop=0; next}
drop == 0 {print}
' "MARKER_BEGIN=$MARKER_BEGIN" "MARKER_END=$MARKER_END"
}

install_cron() {
  mkdir -p "$CRON_LOG_DIR"
  ensure_scripts
  current="$(read_existing_crontab)"
  cleaned="$(printf '%s\n' "$current" | strip_existing_block)"
  block="$(build_block)"
  {
    echo "$cleaned";
    echo;
    echo "$block";
  } | sed '/^$/N;/^$/D' | crontab -
  echo "[mc-phase3-cron] approvals cron instalado (intervalo ${CRON_EVERY_MINUTES}min)."
}

remove_cron() {
  current="$(read_existing_crontab)"
  printf '%s\n' "$current" | strip_existing_block | crontab -
  echo "[mc-phase3-cron] bloco removido."
}

status_cron() {
  current="$(read_existing_crontab)"
  if echo "$current" | grep -Fq "$MARKER_BEGIN"; then
    echo "[mc-phase3-cron] bloco presente:";
    echo "$current" | sed -n "/$MARKER_BEGIN/,/$MARKER_END/p";
  else
    echo "[mc-phase3-cron] bloco ausente.";
  fi
}

case "$ACTION" in
  install|upsert) install_cron ;;
  remove|uninstall) remove_cron ;;
  status) status_cron ;;
  *) echo "Usage: $0 [install|status|remove]" >&2; exit 1;;
 esac
