# Migração de subspawns legados para A2A

## Objetivo

Permitir continuidade operacional para bots/subspawns antigos enquanto migram para contratos A2A oficiais.

## Estratégia

1. Receber payload legada (`agent`, `task`, `priority`, etc.)
2. Normalizar para `TaskSpec`
3. Guardar assinatura e metadados em:
   - `memory/orchestration-state.json`
   - `docs/AGENT_AUDIT_LOG.md`
4. Enviar via fluxo A2A quando possível (`mc-task-update`, `mc-spawn`)

## Modo bridge

`legacy-subspawn-bridge.sh` mapeia:

- `agent` legado → `target.agentId`
- `task` legado → `intent.input`
- `title` legado → `intent.operation`
- `priority` legado → `risk_profile`

## Regras anti-queda

- Sem `handoffId`: falha explícita e rejeição
- Sem `proposal_id/loop_id`: gera novo `loop_id`
- Sem `review_feedback_required`: default `true` para operação notificada em `legacy`

## Fase de migração

- Fase 1: bridge só para observabilidade (sem execução automática)
- Fase 2: bridge com modo `warn-only`
- Fase 3: execução automática com assinatura e trilha de risco
