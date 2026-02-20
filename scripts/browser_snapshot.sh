#!/usr/bin/env bash
set -euo pipefail

URL="${1:-https://example.com}"
OUT_BASE="${2:-artifacts/reports/snapshot}"

mkdir -p "$(dirname "$OUT_BASE")"

if command -v agent-browser >/dev/null 2>&1; then
  AB="agent-browser"
elif [[ -x /home/openclaw/.local/bin/agent-browser ]]; then
  AB="/home/openclaw/.local/bin/agent-browser"
else
  echo "agent-browser not found" >&2
  exit 1
fi

$AB open "$URL" >/dev/null
$AB get title page > "${OUT_BASE}.title.txt"
$AB snapshot > "${OUT_BASE}.snapshot.txt"
$AB screenshot "${OUT_BASE}.png" >/dev/null || true
$AB close >/dev/null || true

echo "WROTE: ${OUT_BASE}.title.txt"
echo "WROTE: ${OUT_BASE}.snapshot.txt"
echo "WROTE: ${OUT_BASE}.png"
