#!/usr/bin/env bash
# heartbeat-v3.sh — Wrapper bash for heartbeat-v3.py
#
# Replaces: heartbeat-v2.sh (heredoc Python)
# This is a thin wrapper that invokes the separated Python script.
#
# Key improvements over v2:
#   - Python code is a standalone .py file (not heredoc)
#   - Syntax errors are caught at import time with clear messages
#   - Dispatch uses filesystem queue (not cron one-shot)
#   - System-event nudge replaces cron isolated sessions
#
# Usage:
#   ./heartbeat-v3.sh                    # Normal run
#   ./heartbeat-v3.sh --dry-run          # Simulate without side-effects
#   ./heartbeat-v3.sh --verbose          # Verbose logging
#   ./heartbeat-v3.sh --reset-circuit-breaker  # Reset CB to closed
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
V3_DIR="$(dirname "$SCRIPT_DIR")"
WORKSPACE_DIR="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Export workspace for the Python script
export WORKSPACE="${WORKSPACE:-$WORKSPACE_DIR}"
export HEARTBEAT_V3_DIR="${HEARTBEAT_V3_DIR:-$V3_DIR}"

# Load user environment (MC_API_TOKEN, SUPABASE_*, etc.)
# Cron runs with minimal env — bashrc has the tokens we need
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail  # bashrc may have unguarded expansions
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

# Ensure queue directories exist
mkdir -p "$V3_DIR/queue"/{pending,active,done,failed,escalated}

# Check Python availability
if ! command -v python3 &>/dev/null; then
    echo "FATAL: python3 not found in PATH" >&2
    exit 1
fi

# Check Python version (need 3.10+)
py_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
py_major=$(echo "$py_version" | cut -d. -f1)
py_minor=$(echo "$py_version" | cut -d. -f2)
if [ "$py_major" -lt 3 ] || { [ "$py_major" -eq 3 ] && [ "$py_minor" -lt 10 ]; }; then
    echo "FATAL: Python 3.10+ required, found $py_version" >&2
    exit 1
fi

RUN_VALIDATION_MONITOR=1
RUN_CONTROLLER_V1=1
for arg in "$@"; do
    if [ "$arg" = "--dry-run" ]; then
        RUN_VALIDATION_MONITOR=0
    fi
done
if [ "${HEARTBEAT_AUTONOMY_MONITOR:-1}" = "0" ]; then
    RUN_VALIDATION_MONITOR=0
fi
if [ "${HEARTBEAT_CONTROLLER_V1:-1}" = "0" ]; then
    RUN_CONTROLLER_V1=0
fi

# ─── controller-v1 (non-critical, runs in background with timeout) ────────────
# Failures here must NOT affect the heartbeat exit code.
# controller-v1 is a supplementary component; rate-limit/network errors (429/503)
# should never contaminate the main heartbeat path.
CONTROLLER_SCRIPT="$SCRIPT_DIR/controller-v1.py"
if [ "$RUN_CONTROLLER_V1" -eq 1 ] && [ -f "$CONTROLLER_SCRIPT" ]; then
    CONTROLLER_LOG="$WORKSPACE_DIR/logs/controller-v1.log"
    mkdir -p "$(dirname "$CONTROLLER_LOG")"
    set +e
    timeout 60 python3 "$CONTROLLER_SCRIPT" "$@" >> "$CONTROLLER_LOG" 2>&1
    CONTROLLER_RC=$?
    set -e
    if [ "$CONTROLLER_RC" -ne 0 ]; then
        echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] WARN: controller-v1 exited rc=$CONTROLLER_RC (non-fatal, heartbeat continues)" \
            >> "$CONTROLLER_LOG"
    fi
fi

# ─── heartbeat-v3.py (critical path) ─────────────────────────────────────────
set +e
python3 "$SCRIPT_DIR/heartbeat-v3.py" "$@"
HEARTBEAT_RC=$?
set -e

# ─── autonomy validation monitor (optional) ───────────────────────────────────
MONITOR_SCRIPT="$WORKSPACE_DIR/scripts/autonomy-validation-monitor.sh"
MONITOR_LOG="$WORKSPACE_DIR/logs/autonomy-validation-monitor.log"
if [ "$RUN_VALIDATION_MONITOR" -eq 1 ] && [ -x "$MONITOR_SCRIPT" ]; then
    mkdir -p "$(dirname "$MONITOR_LOG")"
    "$MONITOR_SCRIPT" --source heartbeat-v3 >> "$MONITOR_LOG" 2>&1 || true
fi

# Always exit with heartbeat-v3.py's code — controller-v1 failures are non-fatal.
exit "$HEARTBEAT_RC"
