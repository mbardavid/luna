#!/usr/bin/env bash
# gateway-post-restart-recovery.sh — Resumes work after gateway restart
#
# Reads the pre-restart snapshot and recovers:
#   1. PMM bot (re-launch if it was running)
#   2. MC tasks stuck in_progress → move to inbox for re-dispatch
#   3. Stale subagent sessions → clean up
#   4. Notify Discord with recovery summary
#
# Called by gateway-safe-restart.sh ExecStartPost hook OR manually.
# Reads: /tmp/.gateway-pre-restart-state.json
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"
STATE_FILE="/tmp/.gateway-pre-restart-state.json"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1473367119377731800}"
LOG_FILE="$WORKSPACE/logs/gateway-recovery.log"
MC_API_URL="${MC_API_URL:-http://localhost:8000}"

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] RECOVERY: $1" >> "$LOG_FILE"; }

log "=== Post-restart recovery starting ==="

# Wait for gateway to be fully ready
MAX_WAIT=30
for i in $(seq 1 $MAX_WAIT); do
    if $OPENCLAW_BIN gateway call health --json --params '{}' 2>/dev/null | grep -q '"ok"'; then
        log "Gateway ready after ${i}s"
        break
    fi
    sleep 1
done

if [ ! -f "$STATE_FILE" ]; then
    log "No pre-restart snapshot found — nothing to recover"
    exit 0
fi

# Parse snapshot
SNAPSHOT=$(cat "$STATE_FILE")
RECOVERED=0
FAILED=0
SUMMARY=""

# ─── 1. Recover PMM bot ─────────────────────────────────────────────────────

PMM_WAS_RUNNING=$(echo "$SNAPSHOT" | python3 -c "
import json,sys
state = json.load(sys.stdin)
pmm = [p for p in state.get('processes', []) if p['name'] == 'pmm']
print('yes' if pmm else 'no')
" 2>/dev/null)

if [ "$PMM_WAS_RUNNING" = "yes" ]; then
    PMM_CMD=$(echo "$SNAPSHOT" | python3 -c "
import json,sys
state = json.load(sys.stdin)
pmm = [p for p in state.get('processes', []) if p['name'] == 'pmm']
if pmm:
    print(pmm[0].get('cmd', ''))
" 2>/dev/null)

    PMM_CWD=$(echo "$SNAPSHOT" | python3 -c "
import json,sys
state = json.load(sys.stdin)
pmm = [p for p in state.get('processes', []) if p['name'] == 'pmm']
if pmm:
    print(pmm[0].get('cwd', '/home/openclaw/.openclaw/workspace/polymarket-mm'))
" 2>/dev/null)

    # Check if PMM is already running (survived restart since it's nohup)
    if pgrep -f "production_runner" > /dev/null 2>&1; then
        log "PMM already running (survived restart)"
        SUMMARY="${SUMMARY}\n✅ PMM: já rodando (sobreviveu ao restart)"
    else
        log "PMM was running but died — relaunching..."
        # Find the config file from the command
        CONFIG_FILE=$(echo "$PMM_CMD" | grep -oP '(?<=--config )\S+' || echo "paper/runs/prod-002.yaml")
        
        cd "$PMM_CWD" 2>/dev/null || cd "$WORKSPACE/polymarket-mm"
        mkdir -p logs
        
        nohup python3 -m paper.production_runner --config "$CONFIG_FILE" \
            >> logs/prod-002.log 2>&1 &
        
        NEW_PID=$!
        echo "$NEW_PID" > paper/data/production_trading.pid
        sleep 3
        
        if kill -0 "$NEW_PID" 2>/dev/null; then
            log "PMM relaunched with PID $NEW_PID"
            SUMMARY="${SUMMARY}\n🔄 PMM: relançado (PID $NEW_PID)"
            RECOVERED=$((RECOVERED + 1))
        else
            log "PMM relaunch FAILED"
            SUMMARY="${SUMMARY}\n❌ PMM: falha no relaunch"
            FAILED=$((FAILED + 1))
        fi
    fi
else
    log "PMM was not running before restart"
fi

# ─── 2. Recover MC in_progress tasks ────────────────────────────────────────

MC_IN_PROGRESS=$(echo "$SNAPSHOT" | python3 -c "
import json,sys
state = json.load(sys.stdin)
tasks = state.get('mc_in_progress', [])
print(len(tasks))
for t in tasks:
    print(f'{t[\"task_id\"]}|{t.get(\"title\",\"?\")[:50]}|{t.get(\"agent\",\"?\")}')
" 2>/dev/null)

MC_COUNT=$(echo "$MC_IN_PROGRESS" | head -1)
if [ "$MC_COUNT" -gt 0 ] 2>/dev/null; then
    log "Found $MC_COUNT MC tasks that were in_progress"
    
    # Move them back to inbox for re-dispatch
    echo "$MC_IN_PROGRESS" | tail -n +2 | while IFS='|' read -r task_id title agent; do
        [ -z "$task_id" ] && continue
        
        # Check if task's subagent session still exists
        SESSION_EXISTS=$(openclaw gateway call sessions.list --json --params '{}' 2>/dev/null | \
            python3 -c "
import json,sys
data = json.load(sys.stdin)
sessions = data.get('sessions', data) if isinstance(data, dict) else data
keys = [s.get('key','') for s in sessions]
# Check if any session matches this task
print('yes' if any('$task_id' in k or '$agent' in k for k in keys) else 'no')
" 2>/dev/null)

        if [ "$SESSION_EXISTS" = "no" ]; then
            # Move task back to inbox via MC API
            if [ -n "$MC_API_TOKEN" ]; then
                curl -s -X PATCH "$MC_API_URL/api/v1/tasks/$task_id" \
                    -H "Authorization: Bearer $MC_API_TOKEN" \
                    -H "Content-Type: application/json" \
                    -d '{"status":"inbox","mc_session_key":null}' > /dev/null 2>&1 && {
                    log "Moved task $task_id back to inbox: $title"
                    SUMMARY="${SUMMARY}\n📥 MC task → inbox: $title"
                    RECOVERED=$((RECOVERED + 1))
                } || {
                    log "Failed to move task $task_id"
                    FAILED=$((FAILED + 1))
                }
            fi
        else
            log "Task $task_id still has active session — skipping"
            SUMMARY="${SUMMARY}\n⏳ MC task ativa: $title"
        fi
    done
else
    log "No MC in_progress tasks to recover"
fi

# ─── 3. Clean stale subagent sessions ────────────────────────────────────────

STALE_SUBAGENTS=$(echo "$SNAPSHOT" | python3 -c "
import json,sys,time
state = json.load(sys.stdin)
now_ms = time.time() * 1000
stale = []
for s in state.get('subagent_sessions', []):
    age_hours = (now_ms - s.get('updated_at', 0)) / 3600000
    if age_hours > 24:
        stale.append(s['key'])
print(len(stale))
for k in stale:
    print(k)
" 2>/dev/null)

STALE_COUNT=$(echo "$STALE_SUBAGENTS" | head -1)
if [ "$STALE_COUNT" -gt 0 ] 2>/dev/null; then
    log "Found $STALE_COUNT stale subagent sessions to clean"
    echo "$STALE_SUBAGENTS" | tail -n +2 | while read -r key; do
        [ -z "$key" ] && continue
        openclaw gateway call sessions.delete --json --params "{\"key\":\"$key\"}" 2>/dev/null && {
            log "Cleaned stale session: $key"
            RECOVERED=$((RECOVERED + 1))
        } || true
    done
    SUMMARY="${SUMMARY}\n🧹 Limpou $STALE_COUNT sessões subagent stale"
fi

# ─── 4. Notify Discord ──────────────────────────────────────────────────────

RESTART_REASON=$(echo "$SNAPSHOT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reason','unknown'))" 2>/dev/null)

if [ -n "$SUMMARY" ]; then
    NOTIFY_MSG="🔄 **Recovery pós-restart** (motivo: ${RESTART_REASON})
Recuperados: ${RECOVERED} | Falhas: ${FAILED}
$(echo -e "$SUMMARY")"

    timeout 8 "$OPENCLAW_BIN" message send \
        --channel discord \
        --target "$DISCORD_CHANNEL" \
        --message "$NOTIFY_MSG" \
        --json 2>/dev/null || log "Discord notification failed"
fi

# ─── 5. Archive snapshot ─────────────────────────────────────────────────────

mkdir -p "$WORKSPACE/logs/recovery-snapshots"
cp "$STATE_FILE" "$WORKSPACE/logs/recovery-snapshots/$(date -u '+%Y%m%dT%H%M%S').json"
rm -f "$STATE_FILE"

log "Recovery complete: recovered=$RECOVERED failed=$FAILED"
