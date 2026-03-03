# HEARTBEAT.md
# ⚠️ Este heartbeat roda no Gemini Flash — NÃO orquestrar agentes aqui.
# Apenas checar e escalar para Luna (Opus) se houver trabalho.

## Checklist (rápido, sem orquestração)

1. **Subagents:** `subagents list` — verificar ativos E recentes falhados
2. **MC inbox:** `curl -s "http://localhost:8000/api/v1/boards/0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47/tasks" -H "Authorization: Bearer $MC_API_TOKEN" | python3 -c "import json,sys; tasks=json.loads(sys.stdin.read()).get('items',[]); inbox=[t for t in tasks if t['status']=='inbox']; print(f'{len(inbox)} inbox tasks')"`
3. **Anti-spam state:** `cat /tmp/.heartbeat-last-notify.json 2>/dev/null || echo '{"inbox":0,"failed":[]}'` — ler estado anterior
4. **MC review/needs_approval tasks:** Check for tasks in `review` or `needs_approval` status that need Luna's attention.

## Decisão

- Se **subagents ativos (não falhados)**: → HEARTBEAT_OK (trabalho em andamento)
- Se **subagents recentes FALHADOS** (status=failed na lista):
  - Verificar se o label do subagent falhado JÁ ESTÁ no array `failed` do state file
  - Se JÁ notificado → HEARTBEAT_OK (não repetir)
  - Se NOVO: notificar E atualizar state file
- Se **tasks em review/needs_approval**: Notificar para QA review
- Se **sem subagents** E **inbox > 0**:
  - Verificar se o count de inbox MUDOU em relação ao state file
  - Se **MESMO count** → HEARTBEAT_OK (já notificou, não repetir)
  - Se **count MUDOU** (novo ou diferente): notificar E atualizar state file
- Se **sem subagents** E **inbox == 0**: → HEARTBEAT_OK

## ⚠️ REGRAS CRÍTICAS
- Crons de manutenção (watchdog, delivery, resource-monitor) NÃO contam como "trabalho ativo"
- Apenas subagents do `subagents list` contam
- **NUNCA notificar duas vezes para o mesmo estado** — checar state file ANTES
- **SEMPRE especificar `channel: discord`** no message tool — sem isso o gateway crasheia

## 🔄 Operação Contínua (Regra Permanente)
Antes de criar um NOVO script de detecção/automação:
1. Verificar se `heartbeat-v3` já cobre o caso.
2. Se sim → evoluir a Phase correspondente no `heartbeat-v3.py`.
3. Se não → justificar a exceção e documentar em `docs/ownership-matrix.md`.

### O que heartbeat-v3 já cobre automaticamente (NÃO duplicar)
- Failure detection + classification (Phase 4)
- Description quality audit (Phase 4.8)
- Stale task + completion detection → qa-review queue (Phase 5.5)
- PMM health check + auto-restart com crash loop alert (Phase 1)
- Gateway health check (Phase 1)
