#!/usr/bin/env bash
# pmm-status-updater.sh — Updates PMM service card in Mission Control
#
# Runs every 15min via cron. Reads PMM logs and updates the MC card
# with current stats (PnL, fills, rejections, uptime, positions).
#
# Also restarts PMM if it crashed (auto-recovery).
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
PMM_DIR="$WORKSPACE/polymarket-mm"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"
MC_TOKEN="${MC_API_TOKEN:-luna_mission_control_access_token_stable_v1_6741ef7ffc207adb58ce632e7ff1d9913dbf2e9c44441aac}"
LOG_FILE="$WORKSPACE/logs/pmm-status-updater.log"

# PMM Service card ID
PMM_TASK_ID="${PMM_TASK_ID:-5fb3bb4e-159d-4b95-949c-c84b319a3554}"

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"; }

# ─── 1. Check if PMM is running ─────────────────────────────────────────────

PMM_PID=$(pgrep -f "production_runner\|paper_runner\|paper.production_runner" 2>/dev/null | head -1 || true)
PMM_RUNNING=false
PMM_CONFIG=""
UPTIME_HOURS="0"

if [ -n "$PMM_PID" ]; then
    PMM_RUNNING=true
    PMM_CONFIG=$(ps -p "$PMM_PID" -o args= 2>/dev/null | grep -oP '(?<=--config )\S+' || echo "unknown")
    UPTIME_SECS=$(ps -p "$PMM_PID" -o etimes= 2>/dev/null | tr -d ' ' || echo "0")
    UPTIME_HOURS=$(echo "scale=1; ${UPTIME_SECS:-0} / 3600" | bc 2>/dev/null || echo "?")
fi

# ─── 2. Parse latest log stats ──────────────────────────────────────────────

STATS=$(python3 << 'PYEOF'
import os, json, glob

pmm_dir = os.environ.get("PMM_DIR", "")
log_files = sorted(glob.glob(os.path.join(pmm_dir, "logs", "*.log")))

if not log_files:
    print(json.dumps({"error": "no logs"}))
    exit()

# Use the most recent log
log_file = log_files[-1]
log_name = os.path.basename(log_file)

# Count stats from last 15 min of log (~900 lines at 1/sec)
accepted = rejected = fills = complement = balance_err = 0
last_mid = "?"
last_spread = "?"

with open(log_file) as f:
    lines = f.readlines()
    # Analyze last 1000 lines
    for line in lines[-1000:]:
        if "order.accepted" in line: accepted += 1
        if "order.rejected" in line: rejected += 1
        if "order.filled" in line or "fill_received" in line: fills += 1
        if "complement_routing" in line: complement += 1
        if "not enough balance" in line: balance_err += 1
        if "mid=" in line:
            try:
                mid_val = line.split("mid=")[1].split()[0].rstrip(",")
                last_mid = mid_val
            except: pass
        if "spread_bps=" in line:
            try:
                spread_val = line.split("spread_bps=")[1].split()[0].rstrip(",")
                last_spread = spread_val
            except: pass

    total_lines = len(lines)
    total_accepted = sum(1 for l in lines if "order.accepted" in l)
    total_rejected = sum(1 for l in lines if "order.rejected" in l)
    total_fills = sum(1 for l in lines if "order.filled" in l or "fill_received" in l)
    total_balance_err = sum(1 for l in lines if "not enough balance" in l)

print(json.dumps({
    "log_file": log_name,
    "total_lines": total_lines,
    "recent_accepted": accepted,
    "recent_rejected": rejected,
    "recent_fills": fills,
    "recent_complement": complement,
    "recent_balance_err": balance_err,
    "total_accepted": total_accepted,
    "total_rejected": total_rejected,
    "total_fills": total_fills,
    "total_balance_err": total_balance_err,
    "last_mid": last_mid,
    "last_spread": last_spread,
}))
PYEOF
)

# ─── 3. Build status summary ────────────────────────────────────────────────

STATUS_TEXT=$(python3 -c "
import json, os
stats = json.loads('''$STATS''')
running = '$PMM_RUNNING' == 'true'
pid = '${PMM_PID:-none}'
config = '${PMM_CONFIG:-none}'
uptime = '${UPTIME_HOURS:-0}'

if running:
    icon = '🟢'
    status = f'RUNNING (PID {pid}, {uptime}h uptime)'
else:
    icon = '🔴'
    status = 'STOPPED'

summary = f'''{icon} **PMM Status**: {status}
📋 Config: \`{config}\`
📊 Last 15min: {stats.get('recent_accepted',0)} accepted, {stats.get('recent_rejected',0)} rejected, {stats.get('recent_fills',0)} fills
📈 All-time: {stats.get('total_accepted',0)} accepted, {stats.get('total_fills',0)} fills, {stats.get('total_rejected',0)} rejected
💰 Mid: {stats.get('last_mid','?')} | Spread: {stats.get('last_spread','?')}bps
⚠️ Balance errors: {stats.get('total_balance_err',0)}
📁 Log: {stats.get('log_file','?')} ({stats.get('total_lines',0)} lines)'''

print(summary)
" 2>/dev/null)

# ─── 4. Update MC card ──────────────────────────────────────────────────────

PROGRESS=50  # Running = 50%, would be 100% only if we decide to stop

if [ "$PMM_RUNNING" = "true" ]; then
    MC_STATUS="in_progress"
else
    MC_STATUS="inbox"  # Needs attention
    PROGRESS=0
fi

# Escape for JSON
STATUS_JSON=$(python3 -c "
import json
desc = '''$STATUS_TEXT'''
print(json.dumps(desc))
" 2>/dev/null)

curl -s -X PATCH "$MC_API_URL/api/v1/boards//tasks/$PMM_TASK_ID" \
    -H "Authorization: Bearer $MC_TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
        \"status\": \"$MC_STATUS\",
        \"description\": $STATUS_JSON,
        \"mc_progress\": $PROGRESS
    }" > /dev/null 2>&1

log "Updated MC card: $MC_STATUS, progress=$PROGRESS"

# ─── 5. Auto-recovery: restart PMM if crashed ───────────────────────────────

if [ "$PMM_RUNNING" = "false" ]; then
    # Check if there's a PID file indicating it SHOULD be running
    PID_FILE="$PMM_DIR/paper/data/production_trading.pid"
    if [ -f "$PID_FILE" ]; then
        EXPECTED_PID=$(cat "$PID_FILE")
        if ! kill -0 "$EXPECTED_PID" 2>/dev/null; then
            log "PMM crashed! PID $EXPECTED_PID not running. Attempting recovery..."
            
            # Find the config from PID file's associated run
            LAST_CONFIG=$(ls -t "$PMM_DIR/paper/runs/"*.yaml 2>/dev/null | head -1)
            
            if [ -n "$LAST_CONFIG" ]; then
                cd "$PMM_DIR"
                nohup python3 -m paper.production_runner --config "$LAST_CONFIG" \
                    >> "logs/$(basename "$LAST_CONFIG" .yaml).log" 2>&1 &
                NEW_PID=$!
                echo "$NEW_PID" > "$PID_FILE"
                
                sleep 3
                if kill -0 "$NEW_PID" 2>/dev/null; then
                    log "PMM recovered! New PID: $NEW_PID, config: $LAST_CONFIG"
                    
                    # Update MC card
                    curl -s -X PATCH "$MC_API_URL/api/v1/boards//tasks/$PMM_TASK_ID" \
                        -H "Authorization: Bearer $MC_TOKEN" \
                        -H "Content-Type: application/json" \
                        -d "{\"status\":\"in_progress\",\"description\":\"🔄 PMM auto-recovered at $(date -u '+%H:%M:%S UTC'). New PID: $NEW_PID\"}" \
                        > /dev/null 2>&1
                else
                    log "PMM recovery FAILED"
                fi
            fi
        fi
    fi
fi

log "PMM status update complete"
