#!/bin/bash
# Start PMM Dashboard server in background
# Usage: ./start.sh [port]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DATA_DIR="$PROJECT_ROOT/paper/data"
PID_FILE="$DATA_DIR/dashboard.pid"
PORT="${1:-8501}"

mkdir -p "$DATA_DIR"

# Check if already running
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Dashboard already running (PID $OLD_PID)"
        exit 0
    else
        echo "Removing stale PID file"
        rm -f "$PID_FILE"
    fi
fi

echo "Starting PMM Dashboard on port $PORT..."
cd "$PROJECT_ROOT"

DASHBOARD_PORT="$PORT" nohup python3 paper/dashboard/server.py \
    > "$DATA_DIR/dashboard.log" 2>&1 &

DASH_PID=$!
echo "$DASH_PID" > "$PID_FILE"

echo "Dashboard started (PID $DASH_PID)"
echo "URL: http://0.0.0.0:$PORT/"
echo "PID file: $PID_FILE"
echo "Log: $DATA_DIR/dashboard.log"
