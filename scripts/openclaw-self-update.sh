#!/usr/bin/env bash
# openclaw-self-update.sh — Wrapper que Luna chama via exec para atualizar o gateway
#
# Uso (Luna chama com exec tool):
#   bash /home/openclaw/.openclaw/workspace/scripts/openclaw-self-update.sh [version]
#   bash /home/openclaw/.openclaw/workspace/scripts/openclaw-self-update.sh latest
#   bash /home/openclaw/.openclaw/workspace/scripts/openclaw-self-update.sh 2026.3.11
#
# O que faz:
#   1. Checa versão atual vs disponível
#   2. Lança gateway-update.sh via sudo systemd-run (escapa o cgroup do gateway)
#   3. Retorna imediatamente — o update roda em background após o gateway reiniciar
#
# Saída (para Luna interpretar):
#   - Linha "QUEUED: <version>" = update agendado com sucesso
#   - Linha "ALREADY_CURRENT: <version>" = já está na versão pedida
#   - Linha "ERROR: <motivo>" = falhou, não agendado
#
set -euo pipefail

WORKSPACE="/home/openclaw/.openclaw/workspace"
LOG="$WORKSPACE/logs/gateway-update.log"
UPDATE_SCRIPT="$WORKSPACE/scripts/gateway-update.sh"
TARGET_VERSION="${1:-latest}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [self-update] $*" >> "$LOG"; }

mkdir -p "$(dirname "$LOG")"

CURRENT_VERSION=$(openclaw --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || echo "unknown")
log "Called: target=$TARGET_VERSION current=$CURRENT_VERSION"

# Resolve "latest" to actual version number
if [ "$TARGET_VERSION" = "latest" ]; then
    TARGET_VERSION=$(npm view openclaw version 2>/dev/null || echo "")
    if [ -z "$TARGET_VERSION" ]; then
        echo "ERROR: não consegui verificar versão latest no npm"
        log "ERROR: npm view openclaw version failed"
        exit 1
    fi
fi

# Check if already current
if [ "$CURRENT_VERSION" = "$TARGET_VERSION" ]; then
    echo "ALREADY_CURRENT: $CURRENT_VERSION"
    log "Already at $TARGET_VERSION, nothing to do"
    exit 0
fi

# Verify target version exists
if ! npm view "openclaw@${TARGET_VERSION}" version &>/dev/null; then
    echo "ERROR: versão $TARGET_VERSION não encontrada no npm"
    log "ERROR: version $TARGET_VERSION not found"
    exit 1
fi

# Check if update script exists
if [ ! -f "$UPDATE_SCRIPT" ]; then
    echo "ERROR: script de update não encontrado em $UPDATE_SCRIPT"
    exit 1
fi

# Launch via sudo systemd-run to escape the gateway cgroup
# This ensures the update process survives the gateway restart
log "Launching gateway-update.sh $TARGET_VERSION via systemd-run..."
sudo -n systemd-run \
    --unit="openclaw-update-$(date +%s)" \
    --description="OpenClaw self-update to $TARGET_VERSION" \
    --uid=openclaw \
    bash "$UPDATE_SCRIPT" "$TARGET_VERSION" >> "$LOG" 2>&1

echo "QUEUED: $CURRENT_VERSION → $TARGET_VERSION"
log "systemd-run launched OK: $CURRENT_VERSION → $TARGET_VERSION"
