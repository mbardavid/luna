#!/bin/bash
# Session Compact Sentinel — detecta sessoes grandes, arquiva inativas, notifica
#
# Cron recomendado: 0 6 * * * (diariamente às 6h UTC)
#   Trocar de: 0 4 * * 1 (semanal)
#   Para:      0 6 * * * (diário)
#
# NOTA: sessão ATIVA não pode ser arquivada. O script detecta, alerta e
# arquiva somente sessões INATIVAS (não abertas pelo gateway).
# Para compactar a sessão ativa, use /compact no Discord ou reinicie o gateway.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENCLAW_BIN="${OPENCLAW_BIN:-/usr/bin/openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
DISCORD_CHANNEL="${SENTINEL_DISCORD_CHANNEL:-1476255906894446644}"
SESSIONS_DIR="${SESSIONS_DIR:-/home/openclaw/.openclaw/agents/main/sessions}"
AGENTS_DIR="${AGENTS_DIR:-/home/openclaw/.openclaw/agents}"

# Thresholds
WARN_MB=5          # alerta: sessão ativa > 5MB
ALERT_MB=2         # alerta leve: sessão > 2MB (preservado para compatibilidade)
ARCHIVE_MB=1       # arquivar inativas > 1MB
ARCHIVE_INACTIVE_DAYS=1  # arquivar inativas há > 1 dia

LOG_TAG="[session-compact]"
log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $LOG_TAG $*"; }

log "Iniciando verificação de sessões..."

# ─── 1. Detectar sessão ativa via gateway (sessions.list) ───────────────────
ACTIVE_KEYS=()
active_raw=$("$OPENCLAW_BIN" --config "$OPENCLAW_CONFIG" gateway call \
    sessions.list --json 2>/dev/null) || true

if [ -n "$active_raw" ]; then
    while IFS= read -r key; do
        [ -n "$key" ] && ACTIVE_KEYS+=("$key")
    done < <(echo "$active_raw" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    sessions = data.get('sessions', []) if isinstance(data, dict) else []
    for s in sessions:
        k = s.get('key', '')
        if k:
            print(k[:8])  # primeiro 8 chars (prefixo do filename)
except: pass
" 2>/dev/null)
fi

log "Sessões ativas detectadas: ${#ACTIVE_KEYS[@]}"

# ─── 2. Verificar .jsonl de sessões ─────────────────────────────────────────
problems=()
archived_log=()
errors=()

check_jsonl() {
    local f="$1"
    local size_bytes agent_name session_id size_mb is_active

    size_bytes=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f" 2>/dev/null || echo 0)
    size_mb=$(echo "scale=1; $size_bytes / 1048576" | bc 2>/dev/null || echo "?")
    session_id=$(basename "$f" .jsonl | cut -c1-8)
    agent_name=$(basename "$(dirname "$(dirname "$f")")")

    # Checar se é sessão ativa
    is_active=false
    for key in "${ACTIVE_KEYS[@]:-}"; do
        if [[ "$session_id" == "${key:0:8}" ]]; then
            is_active=true
            break
        fi
    done

    # Alerta WARN_MB independente de estar ativa ou não
    if [ "$size_bytes" -gt $((WARN_MB * 1048576)) ]; then
        local tag="🔴"
        [ "$is_active" = true ] && tag="🔴 [ATIVA-NÃO-ARQUIVÁVEL]"
        problems+=("${tag} ${size_mb}MB  ${agent_name}/${session_id}  ($(wc -l < "$f" 2>/dev/null) linhas)")
        log "CRÍTICO: $f (${size_mb}MB) — ativa=$is_active"
    elif [ "$size_bytes" -gt $((ALERT_MB * 1048576)) ]; then
        problems+=("⚠️ ${size_mb}MB  ${agent_name}/${session_id}  ($(wc -l < "$f" 2>/dev/null) linhas)")
        log "ALERTA: $f (${size_mb}MB) — ativa=$is_active"
    fi

    # Arquivar se inativa E > ARCHIVE_MB E antiga
    if [ "$is_active" = false ] && \
       [ "$size_bytes" -gt $((ARCHIVE_MB * 1048576)) ]; then
        # Checar idade do arquivo
        age_days=$(python3 -c "
import os, time
mtime = os.path.getmtime('$f')
age = (time.time() - mtime) / 86400
print(int(age))
" 2>/dev/null || echo 0)

        if [ "$age_days" -ge "$ARCHIVE_INACTIVE_DAYS" ]; then
            local archived_dir
            archived_dir="$(dirname "$f")/archived"
            mkdir -p "$archived_dir"
            if mv "$f" "$archived_dir/$(basename "$f")" 2>/dev/null; then
                archived_log+=("✅ Arquivada: ${agent_name}/${session_id} (${size_mb}MB, ${age_days}d inativa)")
                log "ARQUIVADA: $f"
            else
                errors+=("❌ Erro ao arquivar: ${agent_name}/${session_id}")
                log "ERRO ao arquivar: $f"
            fi
        fi
    fi
}

# Iterar todos os agentes
while IFS= read -r -d '' f; do
    check_jsonl "$f"
done < <(find "$AGENTS_DIR" -maxdepth 3 -name '*.jsonl' -print0 2>/dev/null)

# ─── 3. Verificar sessions.json principal (não só .jsonl) ───────────────────
sessions_json="$SESSIONS_DIR/sessions.json"
if [ -f "$sessions_json" ]; then
    sz=$(stat -c%s "$sessions_json" 2>/dev/null || stat -f%z "$sessions_json" 2>/dev/null || echo 0)
    sz_mb=$(echo "scale=1; $sz / 1048576" | bc 2>/dev/null || echo "?")
    if [ "$sz" -gt $((WARN_MB * 1048576)) ]; then
        problems+=("🔴 [sessions.json] ${sz_mb}MB — requer restart ou /compact")
        log "CRÍTICO: sessions.json (${sz_mb}MB)"
    elif [ "$sz" -gt $((ALERT_MB * 1048576)) ]; then
        problems+=("⚠️ [sessions.json] ${sz_mb}MB — monitorar")
        log "ALERTA: sessions.json (${sz_mb}MB)"
    fi
fi

# ─── 4. Montar e enviar relatório ────────────────────────────────────────────
total_issues=${#problems[@]}
total_archived=${#archived_log[@]}
total_errors=${#errors[@]}

if [ "$total_issues" -eq 0 ] && [ "$total_archived" -eq 0 ] && [ "$total_errors" -eq 0 ]; then
    log "Nenhum problema. OK."
    exit 0
fi

report="📦 **Session Compact Report** ($(date -u +%Y-%m-%d))\n"

if [ "$total_issues" -gt 0 ]; then
    report="${report}\n**Sessões com tamanho crítico (${total_issues}):**\n\`\`\`"
    for p in "${problems[@]}"; do
        report="${report}\n${p}"
    done
    report="${report}\n\`\`\`"
fi

if [ "$total_archived" -gt 0 ]; then
    report="${report}\n\n**Arquivadas automaticamente (${total_archived}):**\n\`\`\`"
    for a in "${archived_log[@]}"; do
        report="${report}\n${a}"
    done
    report="${report}\n\`\`\`"
fi

if [ "$total_errors" -gt 0 ]; then
    report="${report}\n\n**Erros:**"
    for e in "${errors[@]}"; do
        report="${report}\n${e}"
    done
fi

# Dica de ação para sessão ativa grande
has_active_critical=false
for p in "${problems[@]}"; do
    [[ "$p" == *"ATIVA"* ]] && has_active_critical=true
done

if [ "$has_active_critical" = true ]; then
    report="${report}\n\n⚡ **Ação necessária para sessão ativa:** use \`/compact\` no Discord ou \`sudo systemctl restart openclaw-gateway\` para liberar memória."
fi

log "Enviando relatório: ${total_issues} problemas, ${total_archived} arquivadas, ${total_errors} erros"

"$OPENCLAW_BIN" --config "$OPENCLAW_CONFIG" message send \
    --channel discord --target "$DISCORD_CHANNEL" \
    --text "$report" 2>/dev/null \
    && log "Relatório enviado" \
    || log "Falha ao enviar via openclaw — tentando fallback API direta"
