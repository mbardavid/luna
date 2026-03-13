#!/usr/bin/env bash
# gateway-safe-restart.sh — CTO-ops controlled gateway restart
#
# Follows the documented safe restart protocol:
#   1. Pre-flight checks (is restart actually needed?)
#   2. Session cleanup (prune dead cron runs from sessions.json)
#   3. Notify Discord that restart is coming
#   4. Wait for notification to deliver
#   5. Execute restart via systemd
#   6. Post-restart notification handled by ExecStartPost hook
#
# Usage:
#   gateway-safe-restart.sh                    # interactive (asks confirmation)
#   gateway-safe-restart.sh --auto             # automated (CTO-ops, skips confirmation)
#   gateway-safe-restart.sh --auto --reason "memory pressure 85%"
#   gateway-safe-restart.sh --dry-run          # show what would happen
#
# Safety:
#   - Rate-limited: max 3 restarts per hour (checked via state file)
#   - Requires memory > 70% of high watermark OR explicit --force
#   - Notifies Discord before AND after (via ExecStartPost hook)
#   - Prunes dead sessions before restart to maximize memory recovery
#   - Logs everything to audit trail
#
set -euo pipefail

WORKSPACE="/home/openclaw/.openclaw/workspace"
STATE_FILE="/tmp/.gateway-restart-state.json"
LOG_FILE="$WORKSPACE/logs/gateway-safe-restart.log"
AUDIT_LOG="$WORKSPACE/logs/cto-ops-audit.log"
DISCORD_CHANNEL="${DISCORD_CHANNEL:-1476255906894446644}"  # notifications
NOTIF_CHANNEL="${NOTIF_CHANNEL:-1476255906894446644}"      # notifications
SESSIONS_JSON="/home/openclaw/.openclaw/agents/main/sessions/sessions.json"
OPENCLAW_BIN="${OPENCLAW_BIN:-openclaw}"

# Thresholds
MEMORY_HIGH_WATERMARK_MB=900
MEMORY_RESTART_THRESHOLD_PCT=70  # restart allowed when > 70% of high watermark
MAX_RESTARTS_PER_HOUR=3
NOTIFY_WAIT_SECONDS=3

# Flags
AUTO_MODE=0
DRY_RUN=0
FORCE=0
REASON=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)     AUTO_MODE=1; shift ;;
        --dry-run)  DRY_RUN=1; shift ;;
        --force)    FORCE=1; shift ;;
        --reason)   REASON="$2"; shift 2 ;;
        --reason=*) REASON="${1#*=}"; shift ;;
        *)          echo "Unknown flag: $1" >&2; exit 1 ;;
    esac
done

mkdir -p "$(dirname "$LOG_FILE")" "$(dirname "$AUDIT_LOG")"

log() {
    local ts
    ts="$(date -u '+%Y-%m-%d %H:%M:%S')"
    echo "[$ts] $1" | tee -a "$LOG_FILE"
}

audit() {
    local ts
    ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "[$ts] CTO-OPS RESTART: $1" >> "$AUDIT_LOG"
}

notify_discord() {
    local channel="$1"
    local msg="$2"
    timeout 8 "$OPENCLAW_BIN" message send \
        --channel discord \
        --target "$channel" \
        --message "$msg" \
        --json 2>/dev/null || log "WARN: Discord notification failed"
}

# ─── Phase 1: Rate Limit Check ───────────────────────────────────────────────

log "=== Gateway Safe Restart initiated ==="
[ -n "$REASON" ] && log "Reason: $REASON"

# Load state
RESTART_COUNT=0
RESTART_TIMESTAMPS="[]"
if [ -f "$STATE_FILE" ]; then
    RESTART_TIMESTAMPS="$(python3 -c "
import json, time
with open('$STATE_FILE') as f:
    state = json.load(f)
timestamps = state.get('restart_timestamps', [])
# Keep only last hour
cutoff = time.time() - 3600
recent = [t for t in timestamps if t > cutoff]
print(json.dumps(recent))
" 2>/dev/null)" || RESTART_TIMESTAMPS="[]"
    RESTART_COUNT="$(echo "$RESTART_TIMESTAMPS" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))")"
fi

if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS_PER_HOUR" ]; then
    log "BLOCKED: Rate limit exceeded ($RESTART_COUNT/$MAX_RESTARTS_PER_HOUR restarts in last hour)"
    audit "BLOCKED by rate limit ($RESTART_COUNT/$MAX_RESTARTS_PER_HOUR)"
    exit 1
fi

log "Rate limit OK ($RESTART_COUNT/$MAX_RESTARTS_PER_HOUR)"

# ─── Phase 2: Memory Check ───────────────────────────────────────────────────

MEMORY_CURRENT_BYTES="$(cat /sys/fs/cgroup/system.slice/openclaw-gateway.service/memory.current 2>/dev/null || echo 0)"
MEMORY_CURRENT_MB=$((MEMORY_CURRENT_BYTES / 1024 / 1024))
MEMORY_THRESHOLD_MB=$((MEMORY_HIGH_WATERMARK_MB * MEMORY_RESTART_THRESHOLD_PCT / 100))

log "Memory: ${MEMORY_CURRENT_MB}MB / ${MEMORY_HIGH_WATERMARK_MB}MB (threshold: ${MEMORY_THRESHOLD_MB}MB)"

if [ "$FORCE" -eq 0 ] && [ "$MEMORY_CURRENT_MB" -lt "$MEMORY_THRESHOLD_MB" ]; then
    log "SKIPPED: Memory below threshold (${MEMORY_CURRENT_MB}MB < ${MEMORY_THRESHOLD_MB}MB). Use --force to override."
    audit "SKIPPED: memory OK (${MEMORY_CURRENT_MB}MB)"
    exit 0
fi

# ─── Phase 3: Session Cleanup ────────────────────────────────────────────────

log "Phase 3: Cleaning dead sessions from sessions.json..."

if [ -f "$SESSIONS_JSON" ]; then
    CLEANUP_RESULT="$(python3 << 'PYEOF'
import json, shutil, os, time
from datetime import datetime

path = '/home/openclaw/.openclaw/agents/main/sessions/sessions.json'
backup = path + f'.pre-restart-{datetime.utcnow().strftime("%Y%m%dT%H%M%S")}'
shutil.copy2(path, backup)

with open(path) as f:
    data = json.load(f)

before = len(data)

# Group cron runs by definition, keep last 2
cron_runs = {}
for k in list(data.keys()):
    if ':cron:' in k and ':run:' in k:
        cron_def = k.split(':run:')[0]
        if cron_def not in cron_runs:
            cron_runs[cron_def] = []
        updated = data[k].get('updatedAt', 0)
        cron_runs[cron_def].append((k, updated))

removed = 0
for cron_def, runs in cron_runs.items():
    runs.sort(key=lambda x: x[1], reverse=True)
    for k, _ in runs[2:]:
        del data[k]
        removed += 1

after = len(data)

with open(path, 'w') as f:
    json.dump(data, f)

print(f'{before}|{removed}|{after}')
PYEOF
    )" || CLEANUP_RESULT="0|0|0"

    IFS='|' read -r BEFORE REMOVED AFTER <<< "$CLEANUP_RESULT"
    log "Sessions: ${BEFORE} → ${AFTER} (removed ${REMOVED} dead cron runs)"
else
    log "WARN: sessions.json not found, skipping cleanup"
fi

# ─── Phase 3.5: Session Model Normalization ───────────────────────────────────
# Ensures all sessions use the model configured in openclaw.json, not stale overrides.
# Prevents cross-provider fallback contamination across restarts.

log "Phase 3.5: Normalizing session models against openclaw.json..."

NORMALIZE_RESULT="$(python3 << 'PYEOF'
import json, os

OPENCLAW_JSON = '/home/openclaw/.openclaw/openclaw.json'
AGENTS_DIR = '/home/openclaw/.openclaw/agents'

with open(OPENCLAW_JSON) as f:
    cfg = json.load(f)

# Build agent model map from openclaw.json
agent_models = {}
default_primary = cfg.get('agents', {}).get('defaults', {}).get('model', {}).get('primary', '')
for agent in cfg.get('agents', {}).get('list', []):
    agent_id = agent.get('id')
    raw = agent.get('model', default_primary)
    if '/' in raw:
        provider, model = raw.split('/', 1)
    else:
        provider, model = default_primary.split('/', 1) if '/' in default_primary else ('', raw)
        model = raw
    agent_models[agent_id] = {'modelProvider': provider, 'model': model}

normalized = 0
errors = 0

for agent_id, expected in agent_models.items():
    sf = os.path.join(AGENTS_DIR, agent_id, 'sessions', 'sessions.json')
    if not os.path.exists(sf):
        continue
    try:
        with open(sf) as f:
            data = json.load(f)
        changed = False
        for key, session in data.items():
            cur_provider = session.get('modelProvider', '')
            cur_override = session.get('authProfileOverride', '')
            # If session model doesn't match agent config AND override is from a broken provider
            if cur_provider and cur_provider != expected['modelProvider']:
                # Reset to agent-configured model
                session['modelProvider'] = expected['modelProvider']
                session['model'] = expected['model']
                # Remove stale override if it references the wrong provider
                if cur_override and not cur_override.startswith(expected['modelProvider']):
                    session.pop('authProfileOverride', None)
                    session.pop('authProfileOverrideSource', None)
                    session.pop('authProfileOverrideCompactionCount', None)
                changed = True
                normalized += 1
        if changed:
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(sf), suffix='.tmp')
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, sf)
    except Exception as e:
        errors += 1

print(f'{normalized}|{errors}')
PYEOF
    )" || NORMALIZE_RESULT="0|1"

IFS='|' read -r NORM_FIXED NORM_ERRORS <<< "$NORMALIZE_RESULT"
log "Model normalization: fixed=${NORM_FIXED} errors=${NORM_ERRORS}"

# Also clean .deleted and .corrupt files
DISK_CLEANED=0
while IFS= read -r -d '' f; do
    rm -f "$f" && DISK_CLEANED=$((DISK_CLEANED + 1))
done < <(find /home/openclaw/.openclaw/agents/ \( -name "*.deleted.*" -o -name "*.corrupt" \) -print0 2>/dev/null) || true
[ "$DISK_CLEANED" -gt 0 ] && log "Disk: removed $DISK_CLEANED orphan files"

# ─── Phase 3.6: Stale Cross-Agent Session Cleanup ───────────────────────────
# Remove sessions where an agent is handling a Discord channel that belongs to another
# agent (via bindings). These cause dual responses.

log "Phase 3.6: Cleaning stale cross-agent Discord channel sessions..."

CROSSAGENT_RESULT="$(python3 << 'PYEOF'
import json, os

OPENCLAW_JSON = '/home/openclaw/.openclaw/openclaw.json'
AGENTS_DIR = '/home/openclaw/.openclaw/agents'

with open(OPENCLAW_JSON) as f:
    cfg = json.load(f)

# Build channel->agent binding map
channel_to_agent = {}
for binding in cfg.get('bindings', []):
    channel_id = binding.get('match', {}).get('peer', {}).get('id', '')
    agent_id = binding.get('agentId', '')
    if channel_id and agent_id:
        channel_to_agent[channel_id] = agent_id

removed = 0
for agent_dir in os.listdir(AGENTS_DIR):
    sf = os.path.join(AGENTS_DIR, agent_dir, 'sessions', 'sessions.json')
    if not os.path.exists(sf):
        continue
    try:
        with open(sf) as f:
            data = json.load(f)
        to_remove = []
        for key in list(data.keys()):
            if ':discord:channel:' in key:
                channel_id = key.split(':discord:channel:')[1]
                correct_agent = channel_to_agent.get(channel_id)
                # Remove if a binding exists AND this isn't the correct agent
                if correct_agent and agent_dir != correct_agent:
                    to_remove.append(key)
        if to_remove:
            for key in to_remove:
                del data[key]
                removed += 1
            import tempfile
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(sf), suffix='.tmp')
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, sf)
    except Exception:
        pass

print(f'{removed}')
PYEOF
    )" || CROSSAGENT_RESULT="0"

log "Cross-agent cleanup: removed ${CROSSAGENT_RESULT} stale sessions"

# ─── Phase 4: Dry Run Exit ───────────────────────────────────────────────────

if [ "$DRY_RUN" -eq 1 ]; then
    log "DRY RUN: would restart gateway now"
    log "  Sessions cleaned: ${REMOVED:-0}"
    log "  Disk files cleaned: $DISK_CLEANED"
    log "  Memory: ${MEMORY_CURRENT_MB}MB"
    audit "DRY RUN (memory: ${MEMORY_CURRENT_MB}MB, sessions cleaned: ${REMOVED:-0})"
    exit 0
fi

# ─── Phase 5: Pre-restart Notification ────────────────────────────────────────

REASON_TEXT="${REASON:-memory ${MEMORY_CURRENT_MB}MB/${MEMORY_HIGH_WATERMARK_MB}MB}"
RESTART_MSG="⚠️ **Gateway restart em ${NOTIFY_WAIT_SECONDS}s** — CTO-ops automático
Motivo: ${REASON_TEXT}
Sessões limpas: ${REMOVED:-0} cron runs mortos
Memória: ${MEMORY_CURRENT_MB}MB → esperado ~400MB após restart"

log "Phase 5: Notifying Discord..."
notify_discord "$DISCORD_CHANNEL" "$RESTART_MSG"

audit "EXECUTING restart (reason: $REASON_TEXT, memory: ${MEMORY_CURRENT_MB}MB, sessions_cleaned: ${REMOVED:-0})"

# ─── Phase 6: Wait & Restart ─────────────────────────────────────────────────

# ─── Phase 5.5: Pre-restart State Snapshot ────────────────────────────────

SNAPSHOT_SCRIPT="$WORKSPACE/scripts/gateway-state-snapshot.sh"
if [ -x "$SNAPSHOT_SCRIPT" ]; then
    log "Phase 5.5: Capturing pre-restart state snapshot..."
    RESTART_REASON="$REASON_TEXT" bash "$SNAPSHOT_SCRIPT" 2>>"$LOG_FILE" || log "WARN: Snapshot failed (non-fatal)"
fi

log "Waiting ${NOTIFY_WAIT_SECONDS}s for notification delivery..."
sleep "$NOTIFY_WAIT_SECONDS"

# Update state file with new timestamp
python3 -c "
import json, time
timestamps = $RESTART_TIMESTAMPS
timestamps.append(time.time())
with open('$STATE_FILE', 'w') as f:
    json.dump({'restart_timestamps': timestamps, 'last_reason': '$REASON_TEXT'}, f)
" 2>/dev/null || true

log "Executing: sudo systemctl restart openclaw-gateway"
sudo systemctl restart openclaw-gateway

# This line may not execute (process gets killed with gateway)
log "Restart command sent"
