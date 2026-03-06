#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="$(cd "$SCRIPT_DIR/.." && pwd)"
LUNA_X_ENV_FILE="${LUNA_X_ENV_FILE:-/home/openclaw/.openclaw/luna-x-growth.env}"
if [ -z "${AUTH_TOKEN:-}" ] && [ -z "${CT0:-}" ] && [ -f "$LUNA_X_ENV_FILE" ]; then
  set -a
  . "$LUNA_X_ENV_FILE"
  set +a
fi
OUT_DIR="$WORKSPACE/artifacts/reports/luna-x-growth"
HEALTH_JSON="$OUT_DIR/session-health-latest.json"

mkdir -p "$OUT_DIR"

node "$SCRIPT_DIR/luna_x_growth_canary.mjs" health --output "$HEALTH_JSON" >/dev/null

session_state() {
  python3 - "$HEALTH_JSON" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], 'r', encoding='utf-8'))
print(str(payload.get('session_state') or 'unknown').strip())
PY
}

if [ "$(session_state)" = "ok" ]; then
  echo "$HEALTH_JSON"
  exit 0
fi

if [ -f "$SCRIPT_DIR/inject_x_session.mjs" ]; then
  node "$SCRIPT_DIR/inject_x_session.mjs" >/dev/null
fi

node "$SCRIPT_DIR/luna_x_growth_canary.mjs" health --output "$HEALTH_JSON" >/dev/null
[ "$(session_state)" = "ok" ] || { echo "luna x session recovery failed" >&2; exit 1; }

echo "$HEALTH_JSON"
