#!/usr/bin/env bash
# gateway-update.sh — Safe OpenClaw update (runs OUTSIDE the gateway via systemd-run)
#
# Usage (Luna fires via exec):
#   sudo systemd-run --unit=openclaw-update --description="OpenClaw Update" \
#     bash /home/openclaw/.openclaw/workspace/scripts/gateway-update.sh [target_version]
#
# If target_version is omitted, installs @latest.
#
# PREREQUISITES (Luna must do BEFORE calling this):
#   1. Save state to /tmp/.pre-update-state.json (subagents, MC tasks, context)
#   2. Notify Discord "Atualizando, brb"
#
# Flow:
#   1. Wait 5s (let Luna finish)
#   2. npm install -g openclaw@<version>
#   3. Verify binary version matches
#   4. If OK → systemctl restart openclaw-gateway
#   5. If FAIL → DO NOT restart, log error, try to notify
#   6. Post-restart: wake-sentinel reads /tmp/.pre-update-state.json and feeds Luna

set -euo pipefail

LOG="/home/openclaw/.openclaw/workspace/logs/gateway-update.log"
DISCORD_TARGET="1473367119377731800"  # #general-luna
TARGET_VERSION="${1:-latest}"
STATE_FILE="/tmp/.pre-update-state.json"

log() {
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG"
}

notify_discord() {
    # Try to notify Discord via openclaw CLI (only works if gateway is UP)
    timeout 10 openclaw message send \
        --channel discord \
        --target "$DISCORD_TARGET" \
        --message "$1" 2>/dev/null || true
}

log "=== OpenClaw Update Started ==="
log "Target version: $TARGET_VERSION"
log "Current version: $(openclaw --version 2>/dev/null || echo 'unknown')"

# Verify state file exists (Luna should have created it)
if [ -f "$STATE_FILE" ]; then
    log "Pre-update state file found: $STATE_FILE"
    log "State contents: $(cat "$STATE_FILE" | head -5)"
else
    log "WARNING: No pre-update state file at $STATE_FILE — Luna may not have saved state"
fi

# Step 1: Wait for Luna to finish Discord notification
log "Waiting 5s for Luna to finish..."
sleep 5

# Step 2: Capture pre-update version
PRE_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
log "Pre-update version: $PRE_VERSION"

# Step 3: Install
log "Running: npm install -g openclaw@${TARGET_VERSION}"
if npm install -g "openclaw@${TARGET_VERSION}" >> "$LOG" 2>&1; then
    log "npm install completed successfully"
else
    log "ERROR: npm install FAILED (exit $?)"
    notify_discord "❌ **Update falhou** — npm install retornou erro. Gateway NÃO reiniciado. Versão: $PRE_VERSION"
    exit 1
fi

# Step 4: Verify new version
POST_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
log "Post-install version: $POST_VERSION"

if [ "$POST_VERSION" = "$PRE_VERSION" ] && [ "$TARGET_VERSION" != "latest" ]; then
    log "WARNING: Version unchanged ($PRE_VERSION → $POST_VERSION). Target was $TARGET_VERSION."
    notify_discord "⚠️ **Update sem mudança** — versão continua $POST_VERSION. Gateway NÃO reiniciado."
    exit 1
fi

if [ "$POST_VERSION" = "unknown" ]; then
    log "ERROR: Cannot determine version after install. Binary may be broken."
    notify_discord "❌ **Update falhou** — binário quebrado pós-install. Gateway NÃO reiniciado."
    exit 1
fi

log "Version verified: $PRE_VERSION → $POST_VERSION"

# Step 5: Write update result to state file (for wake sentinel to pick up)
UPDATE_RESULT="/tmp/.update-result.json"
cat > "$UPDATE_RESULT" << EOFR
{
  "type": "update",
  "from_version": "$PRE_VERSION",
  "to_version": "$POST_VERSION",
  "target_version": "$TARGET_VERSION",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "status": "success",
  "pre_update_state": "$STATE_FILE"
}
EOFR
log "Update result written to $UPDATE_RESULT"

# Step 6: Restart gateway
log "Restarting gateway..."
if systemctl restart openclaw-gateway; then
    log "Gateway restart command sent successfully"
else
    log "ERROR: systemctl restart failed (exit $?)"
fi

# Step 7: Wait for gateway to come back, then notify
log "Waiting 20s for gateway to stabilize..."
sleep 20

FINAL_VERSION=$(openclaw --version 2>/dev/null || echo "unknown")
log "Final version check: $FINAL_VERSION"

notify_discord "✅ **OpenClaw atualizado:** $PRE_VERSION → $FINAL_VERSION. Wake sentinel vai retomar contexto."

log "=== OpenClaw Update Complete ==="
