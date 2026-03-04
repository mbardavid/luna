# Pós-upgrade OpenClaw — Next Steps (Judge Loop)

## Objetivo
Após o upgrade do OpenClaw, retomar exatamente de onde estávamos, sem repetir gaps conhecidos.

## 0) Não-negociáveis (guardrails)
- Restart do gateway **somente** via `scripts/gateway-safe-restart.sh`.
- Não criar estados falsos no MC:
  - `in_progress` **só** com `mc_session_key` linkada.
  - `review` sem session_key ⇒ voltar para `inbox` com comentário de triage.
- Timeline auditável: **nunca apagar histórico**; usar comentários + fields.

## 1) Verificações imediatas (após upgrade + restart)
1. `openclaw --version`
2. `openclaw status` (gateway reachable; channels OK)
3. `openclaw doctor` (read-only) e capturar warnings relevantes
4. Smoke do heartbeat-v3:
   - `heartbeat-v3/scripts/heartbeat-v3.sh --dry-run`
   - checar `logs/heartbeat-v3.log` por 422/dispatch loops

## 2) Cards a revisar como Judge (ordem)
1) `6e5effc1` — Autonomy v1 (`docs/autonomy-roadmap-v1.md`)
   - Validar se novas features do OpenClaw ajudam (cron concurrency, gateway lock fixes, etc.).
   - Atualizar comentário com decisões.

2) `fe69b371` — Heartbeat definitivo (health-gated dispatch)
   - Conferir se o upgrade trouxe features úteis (cron parallelism, restart-loop fixes, queue quarantine, auth/lock fixes).
   - Exigir Acceptance Criteria: "no orphan in_progress".

3) `272b14b8` — QA handoff loop (`docs/mc-qa-handoff-loop.md`)
   - Decidir se implementa via heartbeat-v3 ou via scripts MC.

## 3) Próxima fase de implementação (depois do Judge)
- Criar tasks de **implementation** (Phase 2) com:
  - Approved Plan
  - Acceptance Criteria + Verification Checks
  - Rollback plan

## Referência
Snapshot do estado antes do upgrade:
- `docs/state-snapshot-2026-03-04-01-40Z.md`
