#!/bin/bash
# Gateway Health Sentinel — detecta problemas ANTES do crash e alerta Matheus
# Cron: */2 * * * * /home/openclaw/.openclaw/workspace/scripts/gateway-health-sentinel.sh

OPENCLAW_BIN="${OPENCLAW_BIN:-/usr/bin/openclaw}"
OPENCLAW_CONFIG="${OPENCLAW_CONFIG:-/home/openclaw/.openclaw/openclaw.json}"
DISCORD_CHANNEL="${SENTINEL_DISCORD_CHANNEL:-1476255906894446644}"
STATE_FILE="/tmp/.gateway-sentinel-state.json"
COOLDOWN_SECONDS=300
LOG_TAG="[gateway-sentinel]"

# Token lido de arquivo de configuracao local (fora do git, nao versionado)
# Para configurar: echo 'DISCORD_BOT_TOKEN=seu_token' > /home/openclaw/.openclaw/sentinel.env
SENTINEL_ENV_FILE="${SENTINEL_ENV_FILE:-/home/openclaw/.openclaw/sentinel.env}"
[ -f "$SENTINEL_ENV_FILE" ] && source "$SENTINEL_ENV_FILE"

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $LOG_TAG $*"; }

in_cooldown() {
    local key="$1"
    python3 -c "
import json, os, time
f = '$STATE_FILE'
if not os.path.exists(f):
    print('no'); exit()
with open(f) as fh:
    try: d = json.load(fh)
    except: d = {}
last = d.get('$key', 0)
print('yes' if (time.time() - last) < $COOLDOWN_SECONDS else 'no')
" 2>/dev/null || echo "no"
}

set_alert() {
    local key="$1"
    python3 -c "
import json, os, time
f = '$STATE_FILE'
d = {}
if os.path.exists(f):
    with open(f) as fh:
        try: d = json.load(fh)
        except: d = {}
d['$key'] = int(time.time())
with open(f, 'w') as fh:
    json.dump(d, fh)
" 2>/dev/null
}

# Token do bot Discord lido do sentinel.env (ver cabecalho para configurar)
# DISCORD_BOT_TOKEN deve estar definido em /home/openclaw/.openclaw/sentinel.env

send_via_discord_api() {
    local msg="$1"
    local json_msg
    json_msg=$(python3 -c "import json,sys; print(json.dumps(sys.argv[1]))" "$msg" 2>/dev/null)
    curl -sf --max-time 8 \
        -H "Authorization: Bot $DISCORD_BOT_TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"content\": $json_msg}" \
        "https://discord.com/api/v10/channels/$DISCORD_CHANNEL/messages" \
        -o /dev/null -w "%{http_code}" 2>/dev/null
}

send_alert() {
    local msg="$1"
    local key="$2"
    [ "$(in_cooldown "$key")" = "yes" ] && { log "cooldown: $key"; return; }

    # Tenta primeiro via gateway (caminho normal)
    if "$OPENCLAW_BIN" --config "$OPENCLAW_CONFIG" message send \
        --channel discord --target "$DISCORD_CHANNEL" --text "$msg" 2>/dev/null; then
        set_alert "$key"
        log "Alerta enviado via gateway: $key"
        return
    fi

    # Fallback: Discord REST API direta — funciona mesmo com gateway OOM ou zumbi
    log "Gateway indisponivel — usando Discord API direta: $key"
    local http_code
    http_code=$(send_via_discord_api "$msg")
    if [ "$http_code" = "200" ] || [ "$http_code" = "201" ]; then
        set_alert "$key"
        log "Alerta enviado via Discord API direta: $key (HTTP $http_code)"
    else
        log "FALHA ao enviar alerta $key (gateway + API falhou): HTTP $http_code"
    fi
}

# CHECK 1: Processo orfao real (porta com PID que NAO e filho do MainPID)
port_pid=$(ss -tlnp 2>/dev/null | grep '18789 ' | grep -oP 'pid=\K[0-9]+' | head -1)
main_pid=$(systemctl show openclaw-gateway --property=MainPID 2>/dev/null | grep -oP '\d+$')
if [ -n "$port_pid" ] && [ -n "$main_pid" ] && [ "$main_pid" != "0" ]; then
    port_ppid=$(ps -o ppid= -p "$port_pid" 2>/dev/null | tr -d ' ')
    if [ "$port_pid" != "$main_pid" ] && [ "$port_ppid" != "$main_pid" ]; then
        log "ORFAO REAL: porta=$port_pid main=$main_pid ppid=$port_ppid"
        send_alert "⚠️ **Gateway: processo órfão na porta 18789**
PID na porta: \`$port_pid\` | MainPID systemd: \`$main_pid\`
Execute: \`sudo kill $port_pid\` — systemd reinicia automaticamente." "orphan_port"
    fi
fi

# CHECK 2: Retry storm (Channel is required)
error_count=$(journalctl -u openclaw-gateway --since "2 minutes ago" --no-pager 2>/dev/null \
    | grep -c "Channel is required" 2>/dev/null || echo 0)
if [ "${error_count:-0}" -gt 20 ] 2>/dev/null; then
    log "RETRY STORM: $error_count erros em 2min"
    send_alert "⚠️ **Gateway: retry storm detectado**
\`$error_count\` erros \`Channel is required\` em 2 minutos — OOM iminente.
Considere reiniciar o gateway." "retry_storm"
fi

# CHECK 3: Memoria critica (>85% do MemoryHigh)
# Usa python para lidar com 'infinity' e parse numerico seguro
python3 - << 'PYEOF' 2>/dev/null
import subprocess, os, json, time

def get_prop(prop):
    try:
        r = subprocess.run(['systemctl', 'show', 'openclaw-gateway', f'--property={prop}'],
                           capture_output=True, text=True)
        val = r.stdout.strip().split('=', 1)[-1]
        return int(val) if val.isdigit() else None
    except: return None

mem_cur = get_prop('MemoryCurrent')
mem_high = get_prop('MemoryHigh')

if mem_cur and mem_high and mem_high > 0:
    pct = mem_cur * 100 // mem_high
    if pct > 85:
        cur_mb = mem_cur // 1024 // 1024
        high_mb = mem_high // 1024 // 1024
        print(f"MEMORY_CRITICAL:{pct}:{cur_mb}:{high_mb}")
PYEOF
mem_result=$?

# Lê resultado do python e envia alerta se necessário
if python3 -c "
import subprocess, json, os, time
def get_prop(prop):
    try:
        r = subprocess.run(['systemctl','show','openclaw-gateway',f'--property={prop}'], capture_output=True, text=True)
        val = r.stdout.strip().split('=',1)[-1]
        return int(val) if val.isdigit() else None
    except: return None
cur = get_prop('MemoryCurrent'); high = get_prop('MemoryHigh')
if cur and high and high > 0:
    pct = cur*100//high
    if pct > 85: print(f'{pct} {cur//1024//1024} {high//1024//1024}'); exit(0)
exit(1)
" 2>/dev/null; then
    read pct cur_mb high_mb <<< $(python3 -c "
import subprocess
def get_prop(prop):
    r = subprocess.run(['systemctl','show','openclaw-gateway',f'--property={prop}'], capture_output=True, text=True)
    val = r.stdout.strip().split('=',1)[-1]
    return int(val) if val.isdigit() else 0
cur = get_prop('MemoryCurrent'); high = get_prop('MemoryHigh')
if cur and high and high > 0: print(cur*100//high, cur//1024//1024, high//1024//1024)
" 2>/dev/null)
    if [ -n "$pct" ]; then
        log "MEMORIA CRITICA: ${cur_mb}MB/${high_mb}MB (${pct}%)"
        send_alert "⚠️ **Gateway: memória crítica — ${pct}%**
Uso: \`${cur_mb}MB / ${high_mb}MB\`. Risco de OOM." "memory_critical"
    fi
fi

# CHECK 4: Gateway zumbi (porta aberta sem resposta HTTP)
if ss -tlnp 2>/dev/null | grep -q "18789 "; then
    http_code=$(curl -sf --max-time 3 http://127.0.0.1:18789 -o /dev/null -w "%{http_code}" 2>/dev/null || echo "0")
    if [ "$http_code" = "0" ] || [ "$http_code" = "000" ]; then
        log "ZUMBI DETECTADO"
        send_alert "⚠️ **Gateway: possível zumbi detectado**
Porta 18789 aberta sem resposta HTTP (code: $http_code).
Verifique: \`sudo journalctl -u openclaw-gateway --since '5 minutes ago'\`" "zombie_gateway"
    fi
fi

log "Done"
