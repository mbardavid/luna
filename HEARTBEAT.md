# HEARTBEAT.md
# ⚠️ Este heartbeat roda no Gemini Flash — NÃO orquestrar agentes aqui.
# Apenas checar e escalar para Luna (Opus) se houver trabalho.

## Checklist (rápido, sem orquestração)

1. **Subagents:** `subagents list` — verificar ativos E recentes falhados
2. **MC inbox:** `curl -s "http://localhost:8000/api/v1/boards/0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47/tasks" -H "Authorization: Bearer $MC_API_TOKEN" | python3 -c "import json,sys; tasks=json.loads(sys.stdin.read()).get('items',[]); inbox=[t for t in tasks if t['status']=='inbox']; print(f'{len(inbox)} inbox tasks')"`
3. **Anti-spam state:** `cat /tmp/.heartbeat-last-notify.json 2>/dev/null || echo '{"inbox":0,"failed":[]}'` — ler estado anterior

## Decisão

- Se **subagents ativos (não falhados)**: → HEARTBEAT_OK (trabalho em andamento)
- Se **subagents recentes FALHADOS** (status=failed na lista):
  - Verificar se o label do subagent falhado JÁ ESTÁ no array `failed` do state file
  - Se JÁ notificado → HEARTBEAT_OK (não repetir)
  - Se NOVO: notificar E atualizar state file (ver abaixo)
- Se **sem subagents** E **inbox > 0**:
  - Verificar se o count de inbox MUDOU em relação ao state file
  - Se **MESMO count** → HEARTBEAT_OK (já notificou, não repetir)
  - Se **count MUDOU** (novo ou diferente): notificar E atualizar state file
- Se **sem subagents** E **inbox == 0**: → HEARTBEAT_OK

## Como notificar (OBRIGATÓRIO seguir exatamente)

Usar `message` tool com estes parâmetros EXATOS:
- `action`: `send`
- `channel`: `discord`   ← OBRIGATÓRIO, sem isso dá erro
- `target`: `1473367119377731800`
- `message`: o texto da notificação

## Como atualizar state file após notificar

Usar `exec` tool para escrever:
```
echo '{"inbox": N, "failed": ["label1","label2"]}' > /tmp/.heartbeat-last-notify.json
```
Onde N = count atual de inbox, e failed = array de labels de subagents falhados já notificados.

## ⚠️ REGRAS CRÍTICAS
- Crons de manutenção (watchdog, delivery, resource-monitor, failure-detector) NÃO contam como "trabalho ativo"
- Apenas subagents do `subagents list` contam
- **NUNCA notificar duas vezes para o mesmo estado** — checar state file ANTES
- **SEMPRE especificar `channel: discord`** no message tool — sem isso o gateway crasheia
- Se state file não existe, tratar como inbox=0 e failed=[] (primeiro run)
