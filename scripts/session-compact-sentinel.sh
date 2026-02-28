#!/bin/bash
# Session Compact Sentinel — detecta sessoes grandes e notifica Matheus
# Cron: 0 4 * * 1 (toda segunda-feira as 4h UTC)

OPENCLAW_BIN="${OPENCLAW_BIN:-/usr/bin/openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
DISCORD_CHANNEL="${SENTINEL_DISCORD_CHANNEL:-1476255906894446644}"
SESSIONS_DIR="${SESSIONS_DIR:-/home/openclaw/.openclaw/agents/main/sessions}"
MAX_SIZE_MB=2
LOG_TAG="[session-compact]"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $LOG_TAG $*"; }

log "Verificando sessoes > ${MAX_SIZE_MB}MB..."

large_sessions=$(find "$SESSIONS_DIR" -name "*.jsonl" -size +"${MAX_SIZE_MB}M" 2>/dev/null)

if [ -z "$large_sessions" ]; then
    log "Nenhuma sessao grande encontrada. OK."
    exit 0
fi

report="📦 **Session Compact Report**\nSessões > ${MAX_SIZE_MB}MB detectadas:\n\`\`\`"
count=0
while IFS= read -r f; do
    size=$(du -sh "$f" 2>/dev/null | cut -f1)
    name=$(basename "$f" .jsonl | cut -c1-8)
    lines=$(wc -l < "$f" 2>/dev/null || echo "?")
    report="${report}\n${size}  ${name}  (${lines} linhas)"
    log "Grande: $f ($size, $lines linhas)"
    count=$((count + 1))
done <<< "$large_sessions"
report="${report}\n\`\`\`\nRecomendo usar \`/compact\` na sessão afetada para evitar lentidão no Discord listener."

log "$count sessão(ões) grande(s) reportada(s)"
"$OPENCLAW_BIN" --config "$OPENCLAW_CONFIG" message send \
    --channel discord --target "$DISCORD_CHANNEL" \
    --text "$report" 2>/dev/null && log "Relatorio enviado" || log "Falha ao enviar"
