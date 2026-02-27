#!/bin/bash
# Paper Trading Runner - Background Launch Script
# Runs: kill switch tests + 4h paper trading with real Polymarket WS data

set -e

cd /home/openclaw/.openclaw/workspace/polymarket-mm

# Create data/logs directories
mkdir -p paper/data paper/logs

# Log file with timestamp
LOG_FILE="paper/logs/paper_trading_$(date +%Y%m%d_%H%M%S).log"

echo "=== PMM Paper Trading Runner ===" | tee "$LOG_FILE"
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG_FILE"
echo "Duration: 4 hours" | tee -a "$LOG_FILE"
echo "Log: $LOG_FILE" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"

# Run with INFO logging level (not DEBUG - too verbose for 4h)
export LOG_LEVEL=INFO

python3 -m paper.paper_runner \
    --duration-hours 4 \
    --quote-interval 3.0 \
    --flush-interval 3600 \
    --test-kill-switch \
    --run-pipeline \
    2>&1 | tee -a "$LOG_FILE"

echo ""
echo "=== Run Complete ==="
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Metrics: paper/data/metrics_latest.json"
