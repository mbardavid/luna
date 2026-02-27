#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/openclaw/.openclaw/workspace}"
SCRIPT_DIR="${SCRIPT_DIR:-$WORKDIR/scripts}"
CRON_EVERY_MINUTES="${MC_CRON_EVERY_MINUTES:-15}"
CRON_LOG_DIR="${MC_CRON_LOG_DIR:-$WORKDIR/logs}"
MAX_RETRIES="${MC_MAX_RETRIES:-2}"
W_NO_STALL="${MC_WATCHDOG_NO_STALL_CHECK:-0}"
WARN_PCT="${MC_RESOURCE_WARN_PCT:-80}"
DEGRADE_PCT="${MC_RESOURCE_DEGRADE_PCT:-90}"
RECOVER_PCT="${MC_RESOURCE_RECOVER_PCT:-85}"
KILL_PCT="${MC_RESOURCE_KILL_PCT:-95}"
KILL_ALLOWLIST="${MC_RESOURCE_KILL_ALLOWLIST:-node_modules/.bin/*,/tmp/puppeteer*}"
RESOURCE_MONITOR_KILL="${MC_RESOURCE_MONITOR_KILL:-0}"
MC_CHANNEL="${MC_DELIVERY_CHANNEL:-1476255906894446644}"
MC_MAX_DELIVERY="${MC_DELIVERY_MAX_TO_DELIVER:-50}"

ACTION="${1:-install}"
MARKER_BEGIN="# OPENCLAW MC PHASE1 MONITORING BEGIN"
MARKER_END="# OPENCLAW MC PHASE1 MONITORING END"

ensure_scripts() {
  local missing=0
  for script in \
    "$SCRIPT_DIR/mc-watchdog.sh" \
    "$SCRIPT_DIR/mc-delivery.sh" \
    "$SCRIPT_DIR/mc-resource-monitor.sh"
  do
    if [ ! -x "$script" ]; then
      echo "[mc-phase1-cron] script ausente ou não executável: $script" >&2
      missing=1
    fi
  done
  if [ "$missing" -ne 0 ]; then
    exit 1
  fi
}

build_block() {
  local kill_flag=()
  if [ "$RESOURCE_MONITOR_KILL" = "1" ]; then
    kill_flag=(--kill-allowlist --allowlist "$KILL_ALLOWLIST")
  fi
  local watchdog_args=(
    --verbose
    --max-retries
    "$MAX_RETRIES"
  )
  if [ "$W_NO_STALL" = "1" ]; then
    watchdog_args+=(--no-stall-check)
  fi
  local watchdog_args_str=""
  for arg in "${watchdog_args[@]}"; do
    watchdog_args_str+=" $(printf '%q' "$arg")"
  done

  cat <<EOF
$MARKER_BEGIN
*/$CRON_EVERY_MINUTES * * * * PATH=/usr/local/bin:/usr/bin:/bin $SCRIPT_DIR/mc-watchdog.sh$watchdog_args_str >> "$CRON_LOG_DIR/mc-watchdog-cron.log" 2>&1
*/$CRON_EVERY_MINUTES * * * * PATH=/usr/local/bin:/usr/bin:/bin $SCRIPT_DIR/mc-delivery.sh --status done --max-to-deliver "$MC_MAX_DELIVERY" --channel "$MC_CHANNEL" >> "$CRON_LOG_DIR/mc-delivery-cron.log" 2>&1
*/$CRON_EVERY_MINUTES * * * * PATH=/usr/local/bin:/usr/bin:/bin $SCRIPT_DIR/mc-resource-monitor.sh --warn-pct "$WARN_PCT" --degrade-pct "$DEGRADE_PCT" --recover-pct "$RECOVER_PCT" --kill-pct "$KILL_PCT" ${kill_flag[*]:-} >> "$CRON_LOG_DIR/mc-resource-monitor-cron.log" 2>&1
$MARKER_END
EOF
}

read_existing_crontab() {
  crontab -l 2>/dev/null || true
}

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

  local current
  current="$(read_existing_crontab)"
  local cleaned
  cleaned="$(printf '%s\n' "$current" | strip_existing_block)"
  local block
  block="$(build_block)"

  {
    echo "$cleaned"
    echo
    echo "$block"
  } | sed '/^$/N;/^$/D' | crontab -

  echo "[mc-phase1-cron] Cron instalado para /$WORKDIR/scripts (intervalo ${CRON_EVERY_MINUTES}min)."
}

remove_cron() {
  local current
  current="$(read_existing_crontab)"
  printf '%s\n' "$current" | strip_existing_block | crontab -
  echo "[mc-phase1-cron] Bloco de cron da Fase 1 removido (se existente)."
}

status_cron() {
  local current
  current="$(read_existing_crontab)"
  if echo "$current" | grep -Fq "$MARKER_BEGIN"; then
    echo "[mc-phase1-cron] Bloco Fase 1 presente:"
    echo "$current" | sed -n "/$MARKER_BEGIN/,/$MARKER_END/p"
  else
    echo "[mc-phase1-cron] Nenhum bloco de cron da Fase 1 encontrado."
  fi
}

case "${ACTION}" in
  install|upsert)
    install_cron
    ;;
  remove|uninstall)
    remove_cron
    ;;
  status)
    status_cron
    ;;
  *)
    echo "Usage: $0 [install|status|remove]" >&2
    exit 1
    ;;
esac
