#!/usr/bin/env bash
set -euo pipefail

mkdir -p artifacts/logs
LOG="artifacts/logs/healthcheck-$(date -u +%Y%m%dT%H%M%SZ).log"

{
  echo "# OpenClaw"
  openclaw --version

  echo
  echo "# Tooling"
  python3 --version
  node -v
  npm -v
  (/home/openclaw/.local/bin/agent-browser --version || agent-browser --version)

  echo
  echo "# Gmail token check"
  test -f /home/openclaw/.config/gogcli/token.json && echo "token.json: OK" || echo "token.json: MISSING"

  echo
  echo "# Quick browser check"
  bash scripts/browser_snapshot.sh "https://example.com" "artifacts/reports/healthcheck-example"
} | tee "$LOG"

echo "LOG: $LOG"
