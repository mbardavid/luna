#!/usr/bin/env bash
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
QUANT_WORKSPACE="${QUANT_WORKSPACE:-/home/openclaw/.openclaw/workspace-quant-strategist}"
LOG_FILE="${QUANT_REWARDS_RESEARCH_LOG:-$WORKSPACE/logs/quant-rewards-research.log}"

mkdir -p "$(dirname "$LOG_FILE")"
cd "$QUANT_WORKSPACE"
exec python3 "$QUANT_WORKSPACE/scripts/rewards_research.py" "$@" >>"$LOG_FILE" 2>&1
