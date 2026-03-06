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
BASELINE_JSON="$OUT_DIR/baseline-latest.json"
SNAPSHOT_JSON="$OUT_DIR/profile-snapshot-latest.json"
SCORECARD_JSON="$OUT_DIR/scorecard-latest.json"
BOARD_PACKET_MD="$OUT_DIR/board-packet-latest.md"

mkdir -p "$OUT_DIR"

bash "$SCRIPT_DIR/luna_x_session_recover.sh" >/dev/null
node "$SCRIPT_DIR/luna_x_growth_canary.mjs" baseline --output "$BASELINE_JSON" >/dev/null
python3 "$WORKSPACE/heartbeat-v3/scripts/luna_x_growth_scorecard.py" \
  --baseline "$BASELINE_JSON" \
  --snapshot "$SNAPSHOT_JSON" \
  --output "$SCORECARD_JSON" >/dev/null
python3 "$WORKSPACE/heartbeat-v3/scripts/autonomy_board_packet.py" "$BOARD_PACKET_MD" >/dev/null

echo "$BASELINE_JSON"
