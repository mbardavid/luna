# Mission Control — Plano de Execução (Fases 1 → 3)

**Atualizado:** 2026-02-24

Este documento consolida o que já foi implementado + as próximas etapas para chegar até a Fase 3 (approvals + cost tracking), com critérios de validação objetivos.

## Onde estamos (status real)

Já existe um kit Fase 1 no repo (scripts + cron) que cobre:

- CRUD de tasks no Mission Control (API local auth)
- Custom fields para persistir estado mínimo (`mc_session_key`, `mc_retry_count`, `mc_progress`, `mc_delivered`, `mc_last_error`, etc.)
- Watchdog: cruza tasks ativas com sessões do gateway (`sessions.list` + `chat.history`) e marca `needs_approval`/`stalled`
- Delivery: varre tasks `done` não entregues e posta no Discord
- Resource monitor: degrade mode por pressão de RAM (com kill allowlist opcional)
- Cron installer (Fase 1) + hook pós-restart do gateway

Limitação importante: **não existe hook nativo no OpenClaw CLI para `sessions_spawn`**, então a criação de task no MC não acontece automaticamente quando alguém spawna via tool/SDK. A automação atual resolve isso com:
- disciplina operacional (spawn via fluxo que cria task primeiro)
- watchdog + delivery (garantia de não perder resultado)

---

## Fase 1.5 — Hardening (P0/P1)

### Card 1 — Secrets hygiene
**Mudança:** token não fica versionado.
- `config/mission-control-ids.json` virou template (sem token real)
- token real deve ficar em `config/mission-control-ids.local.json` (gitignored) ou `MC_AUTH_TOKEN`

**DoD**
- `git grep` não encontra o token real
- scripts rodam com `MC_AUTH_TOKEN` ou `.local.json`

### Card 2 — Watchdog anti-spam + semântica de stalled
**Mudança:**
- `mc-watchdog.sh` usa `mc_last_error` como flag idempotente para `stalled` e `needs_approval`
- `missing_session_key` vira estado explícito (sem retries irreais)

**DoD**
- duas execuções seguidas do watchdog não criam comentários duplicados

### Card 3 — Resource monitor kill off by default
**Mudança:** `MC_RESOURCE_MONITOR_KILL` default = `0`.

**DoD**
- cron instalado não mata processos a menos que env ligue explicitamente

### Card 4 — Delivery envia output real
**Mudança:**
- `mc-task-update.sh` grava `mc_output_summary` ao finalizar
- `mc-delivery.sh` prioriza `mc_output_summary` e fallback para parsing de comments

**DoD**
- mensagem no Discord contém “Resultado:” com summary do `TASK_UPDATE`

---

## Fase 2 — Orquestração ativa (melhor UX + menos manual)

### Card 5 — Fluxo recomendado de spawn
Como o CLI não tem `sessions.spawn`, o fluxo padrão para sempre aparecer no MC é:
1) Criar task no MC (`mc-client.sh create-task` ou `mc-spawn-wrapper.sh --json`)
2) Spawn via ferramenta (Luna / OpenClaw tool)
3) Linkar `sessionKey` no MC (`mc-link-task-session.sh`)
4) Sub-agent envia `TASK_UPDATE` e a Luna espelha via `mc-task-update.sh`

**DoD**
- toda task em `in_progress` tem `mc_session_key`

### Card 6 — QA ampliado (Fase 2)
Adicionar cenários S5+ ao `fase1_qa_validate.sh`:
- anti-spam stalled
- missing_session_key
- delivery com mc_output_summary

**DoD**
- `bash fase1_qa_validate.sh` retorna `pass=1` em todos

---

## Fase 3 — Approvals + cost tracking (operável)

### Card 7 — Approvals notify (Discord)
Adicionar `mc-approvals-notify.sh` + `mc-phase3-cron.sh`.
- varre approvals `pending`
- envia notificação no Discord
- marca `mc_approval_notified=true` na task para não spammar

**DoD**
- criar approval no MC → notificação aparece em até 5 min

### Card 8 — Cost fields + reporte
Bootstrap cria campos:
- `mc_estimated_cost_usd`, `mc_actual_cost_usd`

**DoD**
- campos existem no MC e aparecem no card quando setados

---

## Validação (comandos)

- Base: `bash fase1_qa_validate.sh`
- Runbook: `docs/mission-control-phase1-runbook.md`

