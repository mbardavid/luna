#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
PMM_ROOT="${PMM_ROOT:-$WORKSPACE/polymarket-mm}"
CONFIG_PATH="${PMM_LIVE_CONFIG:-$PMM_ROOT/paper/runs/prod-006.yaml}"
DECISION_ENVELOPE="${PMM_DECISION_ENVELOPE:-$PMM_ROOT/paper/data/decision_envelope_latest.json}"
LOG_FILE="${PMM_LIVE_LOG_FILE:-$PMM_ROOT/logs/production.log}"
PID_FILE="${PMM_PID_FILE:-$PMM_ROOT/paper/data/production_trading.pid}"
PMM_ENV_FILE="${PMM_ENV_FILE:-$PMM_ROOT/.env}"
SYSTEMD_ENV_FILE="${PMM_SYSTEMD_ENV_FILE:-/home/openclaw/.config/systemd/user/openclaw-gateway.service.d/polymarket-env.conf}"
FOREGROUND="0"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    --decision-envelope)
      DECISION_ENVELOPE="$2"
      shift 2
      ;;
    --log-file)
      LOG_FILE="$2"
      shift 2
      ;;
    --pid-file)
      PID_FILE="$2"
      shift 2
      ;;
    --foreground)
      FOREGROUND="1"
      shift
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$PID_FILE")"

load_systemd_env() {
  local env_file="$1"
  [[ -f "$env_file" ]] || return 0
  while IFS= read -r raw; do
    [[ "$raw" == Environment=* ]] || continue
    local item="${raw#Environment=}"
    item="${item%\"}"
    item="${item#\"}"
    export "$item"
  done <"$env_file"
}

if [[ -f "$PMM_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$PMM_ENV_FILE"
  set +a
fi

load_systemd_env "$SYSTEMD_ENV_FILE"

export POLYMARKET_API_SECRET="${POLYMARKET_API_SECRET:-${POLYMARKET_SECRET:-}}"
export POLYGON_PRIVATE_KEY="${POLYGON_PRIVATE_KEY:-${POLYMARKET_PRIVATE_KEY:-}}"
export POLYMARKET_PRIVATE_KEY="${POLYMARKET_PRIVATE_KEY:-${POLYGON_PRIVATE_KEY:-}}"
export PRIVATE_KEY="${PRIVATE_KEY:-${POLYMARKET_PRIVATE_KEY:-${POLYGON_PRIVATE_KEY:-}}}"

if [[ -f "$PID_FILE" ]]; then
  EXISTING_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$EXISTING_PID" ]] && kill -0 "$EXISTING_PID" 2>/dev/null; then
    echo "{\"ok\": false, \"error\": \"pid_file_already_running\", \"pid\": $EXISTING_PID}" >&2
    exit 1
  fi
fi

cd "$PMM_ROOT"
CMD=(python3 -m runner --mode live --config "$CONFIG_PATH" --decision-envelope "$DECISION_ENVELOPE")

if [[ "$FOREGROUND" == "1" ]]; then
  exec env PYTHONUNBUFFERED=1 PMM_DECISION_ENVELOPE="$DECISION_ENVELOPE" "${CMD[@]}"
fi

printf '\n[%s] pmm-live-runner start config=%s envelope=%s\n' "$(date -u +%FT%TZ)" "$CONFIG_PATH" "$DECISION_ENVELOPE" >>"$LOG_FILE"
nohup env PYTHONUNBUFFERED=1 PMM_DECISION_ENVELOPE="$DECISION_ENVELOPE" "${CMD[@]}" >>"$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"

python3 - "$NEW_PID" "$CONFIG_PATH" "$DECISION_ENVELOPE" "$LOG_FILE" "$PID_FILE" <<'PY'
import json
import sys

print(json.dumps({
    "ok": True,
    "pid": int(sys.argv[1]),
    "config_path": sys.argv[2],
    "decision_envelope": sys.argv[3],
    "log_file": sys.argv[4],
    "pid_file": sys.argv[5],
}))
PY
