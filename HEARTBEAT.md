# HEARTBEAT.md
# ⚠️ Este heartbeat roda no Gemini Flash — NÃO orquestrar agentes aqui.
# Apenas checar e escalar para Luna (Opus) se houver trabalho.

## Checklist (rápido, sem orquestração)

1. **Cron guard:** `bash scripts/mc-cron-guard.sh active` — se retornar 0 (ativo), → HEARTBEAT_OK
2. **Subagents:** `subagents list` — se houver subagents ativos, → HEARTBEAT_OK
3. **MC inbox:** `curl -s "http://localhost:8000/api/v1/boards/0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47/tasks" -H "Authorization: Bearer luna_mission_control_access_token_stable_v1_6741ef7ffc207adb58ce632e7ff1d9913dbf2e9c44441aac" | python3 -c "import json,sys; tasks=json.loads(sys.stdin.read()).get('items',[]); inbox=[t for t in tasks if t['status']=='inbox']; print(f'{len(inbox)} inbox tasks')"`

## Decisão

- Se **tudo limpo** (sem crons, sem subagents, sem in_progress) E **inbox > 0**:
  - Enviar mensagem para o canal `#general-luna` (1473367119377731800) usando `message` tool:
    `"🔔 Heartbeat: {N} task(s) inbox no MC aguardando execução."`
  - NÃO spawnar agentes. NÃO orquestrar. Apenas notificar.
- Se **tudo limpo** e **inbox == 0**: → HEARTBEAT_OK
- Se **algo ativo**: → HEARTBEAT_OK
