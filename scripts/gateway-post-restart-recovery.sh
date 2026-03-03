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

# Load user environment (MC_API_TOKEN, etc.)
# ExecStartPost runs with systemd env — needs bashrc tokens
if [ -f "$HOME/.bashrc" ]; then
    set +euo pipefail
    source "$HOME/.bashrc" 2>/dev/null || true
    set -euo pipefail
fi

MC_API_TOKEN="${MC_API_TOKEN:-}"
MC_BOARD_ID="${MC_BOARD_ID:-0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47}"

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
    log "No pre-restart snapshot found — running snapshot-less recovery via MC API"
    
    # ─── SNAPSHOT-LESS RECOVERY ──────────────────────────────────────────
    # When gateway restarts unexpectedly (OOM, CTO-ops, systemd), there's no
    # pre-restart snapshot. Query MC API directly for orphaned tasks.
    
    if [ -n "$MC_API_TOKEN" ]; then
        ORPHAN_TASKS=$(curl -sf -H "Authorization: Bearer $MC_API_TOKEN" \
          "$MC_API_URL/api/v1/boards/$MC_BOARD_ID/tasks" | \
        python3 -c "
import json, sys, time
tasks = json.load(sys.stdin).get('items', [])
results = []
for t in tasks:
    if t['status'] in ('in_progress', 'review'):
        fields = t.get('custom_field_values') or {}
        sk = fields.get('mc_session_key', '')
        if sk:
            results.append({
                'task_id': t['id'],
                'title': t.get('title', '?')[:60],
                'session_key': sk,
                'status': t['status']
            })
for r in results:
    print(json.dumps(r))
" 2>/dev/null)

        ORPHAN_COUNT=$(echo "$ORPHAN_TASKS" | grep -c '{' 2>/dev/null || echo "0")
        
        if [ "$ORPHAN_COUNT" -gt 0 ]; then
            log "Snapshot-less recovery: found $ORPHAN_COUNT tasks with dead sessions"
            SUMMARY=""
            RECOVERED=0
            
            echo "$ORPHAN_TASKS" | while read -r task_json; do
                [ -z "$task_json" ] && continue
                
                TASK_ID=$(echo "$task_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['task_id'])")
                TITLE=$(echo "$task_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['title'])")
                SESSION_KEY=$(echo "$task_json" | python3 -c "import json,sys; print(json.load(sys.stdin)['session_key'])")
                
                # Generate respawn-with-context queue item for heartbeat-v3
                QUEUE_DIR="$WORKSPACE/heartbeat-v3/queue/pending"
                mkdir -p "$QUEUE_DIR"
                QUEUE_FILE="$QUEUE_DIR/$(date -u '+%Y%m%dT%H%M%S')-respawn-context-${TASK_ID:0:8}.json"
                
                cat > "$QUEUE_FILE" << QEOF
{
  "type": "respawn",
  "task_id": "$TASK_ID",
  "title": "$TITLE",
  "agent": "luan",
  "priority": "high",
  "context": {
    "recovery_reason": "gateway_restart_snapshotless",
    "previous_session_key": "$SESSION_KEY",
    "instruction": "CONTINUE FROM WHERE YOU LEFT OFF. Gateway restarted unexpectedly. Read session history of previous session for context. Do NOT restart from scratch."
  }
}
QEOF
                log "Generated respawn-with-context queue item for $TASK_ID: $TITLE"
                RECOVERED=$((RECOVERED + 1))
            done
            
            # Notify Discord
            NOTIFY_MSG="🔄 **Snapshot-less Recovery** (gateway restart sem snapshot)
Encontradas $ORPHAN_COUNT tasks com sessões mortas.
Queue items gerados para respawn com contexto."
            
            timeout 8 "$OPENCLAW_BIN" message send \
                --channel discord \
                --target "$DISCORD_CHANNEL" \
                --message "$NOTIFY_MSG" \
                --json 2>/dev/null || log "Discord notification failed"
        else
            log "Snapshot-less recovery: no orphaned tasks found"
        fi
    else
        log "No MC_API_TOKEN — cannot do snapshot-less recovery"
    fi
    
    # Still check PMM and trigger heartbeat even without snapshot
    # ─── PMM check (snapshot-less) ───
    PMM_DIR="$WORKSPACE/polymarket-mm"
    PMM_PID_FILE="$PMM_DIR/paper/data/production_trading.pid"
    if [ -f "$PMM_PID_FILE" ]; then
        PMM_PID=$(cat "$PMM_PID_FILE" 2>/dev/null)
        if [ -n "$PMM_PID" ] && ! kill -0 "$PMM_PID" 2>/dev/null; then
            log "PMM dead (PID $PMM_PID) — heartbeat-v3 will auto-restart"
        fi
    fi
    
    # Run heartbeat-v3 for detection, then wake Luna via agent RPC
    HEARTBEAT_SCRIPT="$WORKSPACE/heartbeat-v3/scripts/heartbeat-v3.sh"
    if [ -f "$HEARTBEAT_SCRIPT" ]; then
        log "Running heartbeat-v3 for detection (snapshot-less)..."
        bash "$HEARTBEAT_SCRIPT" >> "$WORKSPACE/logs/heartbeat-v3.log" 2>&1 || true
    fi
    
    # Wake Luna immediately
    WAKE_MSG="Gateway reiniciou (snapshot-less recovery). Verificar queue items pendentes e tasks órfãs."
    timeout 15 "$OPENCLAW_BIN" gateway call agent \
        --json \
        --params "{\"message\":\"$WAKE_MSG\",\"idempotencyKey\":\"snapshotless-$(date +%s)\"}" \
        2>/dev/null && {
        log "Luna awakened via agent RPC (snapshot-less)"
    } || {
        log "Agent RPC failed — Luna will wake on next heartbeat cycle (≤2min)"
    }
    
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
    if pgrep -f "runner.*--mode" > /dev/null 2>&1 || pgrep -f "production_runner" > /dev/null 2>&1; then
        log "PMM already running (survived restart)"
        SUMMARY="${SUMMARY}\n✅ PMM: já rodando (sobreviveu ao restart)"
    else
        log "PMM was running but died — relaunching via unified runner..."
        # Find latest prod config
        LATEST_CONFIG=$(ls -t "$WORKSPACE/polymarket-mm/paper/runs/prod-"*.yaml 2>/dev/null | head -1)
        if [ -z "$LATEST_CONFIG" ]; then
            LATEST_CONFIG="paper/runs/prod-003.yaml"
        fi
        
        cd "$WORKSPACE/polymarket-mm" 2>/dev/null || cd "$WORKSPACE/polymarket-mm"
        mkdir -p logs
        
        # Load .env and launch with unified runner
        python3 -c "
import subprocess, sys, os
from pathlib import Path

env = os.environ.copy()
dotenv = Path('.env')
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

proc = subprocess.Popen(
    [sys.executable, '-m', 'runner', '--mode', 'live', '--config', '$LATEST_CONFIG'],
    stdout=open('logs/production.log', 'a'),
    stderr=subprocess.STDOUT,
    start_new_session=True,
    cwd='$WORKSPACE/polymarket-mm',
    env=env,
)
with open('paper/data/production_trading.pid', 'w') as f:
    f.write(str(proc.pid))
print(proc.pid)
" 2>/dev/null
        
        NEW_PID=$(cat paper/data/production_trading.pid 2>/dev/null)
        sleep 3
        
        if [ -n "$NEW_PID" ] && kill -0 "$NEW_PID" 2>/dev/null; then
            log "PMM relaunched with PID $NEW_PID (unified runner)"
            SUMMARY="${SUMMARY}\n🔄 PMM: relançado (PID $NEW_PID, unified runner)"
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

# ─── 6. Wake Luna immediately via gateway agent RPC ─────────────────────────
# Instead of waiting for heartbeat cron or built-in heartbeat interval,
# directly inject a message into Luna's main session. This wakes her up
# in seconds, not minutes.

log "Waking Luna via gateway agent RPC..."

# First run bash heartbeat-v3 for detection (generates queue items)
HEARTBEAT_SCRIPT="$WORKSPACE/heartbeat-v3/scripts/heartbeat-v3.sh"
if [ -f "$HEARTBEAT_SCRIPT" ]; then
    log "Running heartbeat-v3 for detection..."
    bash "$HEARTBEAT_SCRIPT" >> "$WORKSPACE/logs/heartbeat-v3.log" 2>&1 || true
    log "Heartbeat-v3 detection complete"
fi

# Then wake Luna to process results
WAKE_MSG="Gateway reiniciou (motivo: ${RESTART_REASON:-unknown}). Recovery executado: ${RECOVERED:-0} recuperados, ${FAILED:-0} falhas. Verificar queue items pendentes e tasks órfãs."
IDEMPOTENCY_KEY="restart-recovery-$(date +%s)"

timeout 15 "$OPENCLAW_BIN" gateway call agent \
    --json \
    --params "{\"message\":\"$WAKE_MSG\",\"idempotencyKey\":\"$IDEMPOTENCY_KEY\"}" \
    2>/dev/null && {
    log "Luna awakened via agent RPC (key: $IDEMPOTENCY_KEY)"
} || {
    log "Agent RPC failed — Luna will wake on next heartbeat cycle (≤2min)"
}
