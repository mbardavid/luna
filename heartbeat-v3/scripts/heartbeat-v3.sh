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

# Run the Python heartbeat
exec python3 "$SCRIPT_DIR/heartbeat-v3.py" "$@"
