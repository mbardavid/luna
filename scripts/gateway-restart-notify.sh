#!/usr/bin/env bash
set -euo pipefail

# Notifies #general-luna on Discord after gateway restart.
# Used by systemd (ExecStartPost). Includes lightweight recovery probes
# to re-run MC watchdog and delivery sweep after gateway comes back.

WORKDIR="${WORKDIR:-/home/openclaw/.openclaw/workspace}"
MC_WATCHDOG_SCRIPT="${WORKDIR}/scripts/mc-watchdog.sh"
MC_DELIVERY_SCRIPT="${WORKDIR}/scripts/mc-delivery.sh"
MC_RESOURCE_SCRIPT="${WORKDIR}/scripts/mc-resource-monitor.sh"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
LOG_FILE="${WORKDIR}/logs/gateway-restart-notify.log"
STARTUP_HOOKS="${OPENCLAW_GATEWAY_STARTUP_HOOKS:-off}"
RESOURCE_ALLOWLIST="${MC_RESOURCE_ALLOWLIST:-node_modules/.bin/*,/tmp/puppeteer*}"
RESOURCE_MONITOR_KILL="${MC_RESOURCE_MONITOR_KILL_ON_RESTART:-0}"

SLEEP_BEFORE_NOTIFY="${SLEEP_BEFORE_NOTIFY:-2}"
MESSAGE_TIMEOUT="${MESSAGE_TIMEOUT:-8}"
HOOK_TIMEOUT="${HOOK_TIMEOUT:-45}"
MESSAGE_TIMEOUT_FALLBACK="${MESSAGE_TIMEOUT_FALLBACK:-4}"
STARTUP_NOTIFY_ASYNC="${STARTUP_NOTIFY_ASYNC:-1}"

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() {
  printf '[%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >> "$LOG_FILE"
}

if [ "$STARTUP_HOOKS" != "on" ]; then
  log "gateway_restart_notify: startup hooks and notification disabled (OPENCLAW_GATEWAY_STARTUP_HOOKS!=on)"
  exit 0
fi

run_with_timeout() {
  local timeout_seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout --preserve-status --kill-after=3 "$timeout_seconds" "$@" >> "$LOG_FILE" 2>&1
  else
    "$@" >> "$LOG_FILE" 2>&1
  fi
}

run_version_with_timeout() {
  local timeout_seconds="$1"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout --preserve-status --kill-after=3 "$timeout_seconds" "$@"
  else
    "$@"
  fi
}

run_hook_async() {
  local name="$1"
  local script="$2"
  shift 2
  (
    set +e
    log "hook ${name}: started"
    run_with_timeout "$HOOK_TIMEOUT" "$script" "$@"
    log "hook ${name}: finished (rc=$?)"
  ) &
}

run_post_startup_notify_async() {
  local version="$1"
  (
    set +e
    log "notify startup: started"
    if [ "${STARTUP_NOTIFY_ASYNC:-1}" -eq 1 ]; then
      run_with_timeout "$MESSAGE_TIMEOUT" openclaw message send \
        --channel discord \
        --target "${DISCORD_CHANNEL}" \
        --message "🔄 Gateway reiniciado e online — v${version} ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))" \
        --json
      local first_send_rc=$?
      if [ $first_send_rc -ne 0 ] && [ "$MESSAGE_TIMEOUT_FALLBACK" -gt 0 ]; then
        run_with_timeout "$MESSAGE_TIMEOUT_FALLBACK" openclaw message send \
          --channel discord \
          --target "${DISCORD_CHANNEL}" \
          --message "⚠️ Gateway reiniciado, sem validação de presença em Discord (v${version})" \
          --json
      fi
    else
      if run_with_timeout "$MESSAGE_TIMEOUT" openclaw message send \
        --channel discord \
        --target "${DISCORD_CHANNEL}" \
        --message "🔄 Gateway reiniciado e online — v${version} ($(date -u '+%Y-%m-%d %H:%M:%S UTC'))" \
        --json; then
        :
      else
        :
      fi
    fi
    log "notify startup: finished (rc=$?)"
  ) &
}

sleep "$SLEEP_BEFORE_NOTIFY"

if command -v openclaw >/dev/null 2>&1; then
  VERSION="unknown"
  if VERSION_OUTPUT=$(run_version_with_timeout "$MESSAGE_TIMEOUT" openclaw --version 2>/dev/null); then
    VERSION="${VERSION_OUTPUT}"
  fi

  run_post_startup_notify_async "$VERSION"
  log "gateway_restart_notify: notification fire-and-forget launched"
else
  log "gateway_restart_notify: openclaw not found in PATH"
fi

if [ -x "$MC_WATCHDOG_SCRIPT" ]; then
  run_hook_async "mc-watchdog" "$MC_WATCHDOG_SCRIPT" --verbose --startup-recovery --no-stall-check
fi
if [ -x "$MC_DELIVERY_SCRIPT" ]; then
  run_hook_async "mc-delivery" "$MC_DELIVERY_SCRIPT" --status done --max-to-deliver 50
fi
if [ -x "$MC_RESOURCE_SCRIPT" ]; then
  local_args=(
    --warn-pct 80
    --degrade-pct 90
    --recover-pct 85
    --kill-pct 95
  )
  if [ "$RESOURCE_MONITOR_KILL" = "1" ]; then
    local_args+=(--allowlist "$RESOURCE_ALLOWLIST" --kill-allowlist)
  fi
  run_hook_async "mc-resource-monitor" "$MC_RESOURCE_SCRIPT" "${local_args[@]}"
fi

exit 0
