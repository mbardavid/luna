# AGENT_AUDIT_LOG


## 2026-03-05 | Ressonância de auditoria persistente (audit-agents-autonomy-5)

- Continuidade da auditoria de agentes persistentes com foco em autonomia/recorrência.
- Aplicado: registro formal de `cto-ops` em `openclaw.json` + `allowAgents` interno (main/dispatcher).
- Aplicado: suporte a `cto-ops-agent-01` no mapeamento de IDs do `mc-fast-dispatch.sh`.
- Aplicado: fallback de `PMM_DEFAULT_CONFIG` em `heartbeat-v3.py` para `prod-002.yaml`/`prod-001.yaml` quando `default_config` não existe.
- Observação: manter validação de rollout em 24h (sessões + smoke cto-ops).

## 2026-03-05 | Audit de Alinhamento de Agentes Persistentes

- Auditados: `main/Luna`, `Luan`, `crypto-sage`, `quant-strategist`, `dispatcher`, `cto-ops` (existente em workspace).
- Achados críticos:
  - plugin obsoleto em `openclaw.json` gerando warning recorrente (`google-antigravity-auth`)
  - PMM config padrão em heartbeat apontava para `prod-003.yaml` inexistente
  - `cto-ops` tem runbooks/estado, mas **não está em `openclaw.json`** (`agents.list`).
- Melhorias aplicadas:
  - removido plugin obsoleto em `openclaw.json`
  - alterado `workspace/heartbeat-v3/config/v3-config.json` para `prod-002.yaml`
- Documento completo: `docs/persistent-agents-autonomy-audit-2026-03-05.md`


## 2026-03-01

- Iniciada implementação do plano de governança integral A2A.
- Novos arquivos de governança e contrato de loop criados.
- Estrutura inicial dos agentes persistentes `luan-dev` e `cto-ops` criada.

## 2026-03-01 | Mudanças de arquitetura

- Preparado contrato de revisão `Luna ⇄ Luan` com 4 passes.
- Atualizados contratos A2A e de estado para preservar semântica de `needs_approval`.
- Habilitado pipeline de risco de `cto-ops` com política JSON.

## 2026-03-01 | Consolidação (sessão 2)

### Schemas
- `task-spec.schema.json` atualizado para v1.1 (backward-compatible com v1.0)
- Novos campos: `loop_id`, `proposed_by`, `risk_profile`, `review_depth`, `review_feedback_required`, `auto_approve_window`, `review_reason`
- `orchestration-state.schema.json` atualizado para v1.1 com estados de review cycle: `proposed`, `critique`, `replan`, `authorize`, `needs_approval`, `stalled`, `retry`, `review`

### Agentes persistentes
- `agents/luan-dev/` — criados todos os arquivos de identidade (SOUL, AGENTS, USER, MEMORY, HEARTBEAT, lessons, orchestration-state.json)
- `agents/cto-ops/` — criados todos os arquivos de identidade com escopo operacional de saúde/gateway/recursos

### Scripts atualizados
- `mc-spawn.sh` — novas flags `--loop-id`, `--risk-profile`, `--review-depth`, `--no-signature`
- `mc-task-update.sh` — anti-collapse de status semântico + suporte a `review_reason` e `loop_id`
- `mc-failure-detector.sh` — eventos ricos com `risk_profile`, `loop_id` e alerta de risco alto/critical
- `mc-approvals-notify.sh` — inclusão de `review_reason`, `loop_id` e `risk_profile` nas notificações
- `mc-cron-guard.sh` — novo comando `kill-switch` com verificação contra `cto-risk-policy.json`

### Documentação
- `docs/agent-orchestration-a2a.md` — seções 3.7 (review loop v1.1) e 3.8 (anti-impersonation)
- `docs/agent-standard.md` — checklist atualizado com governança v1.1 e review loop
- `AGENTS.md` — tabela de agentes persistentes registrados

## 2026-03-05 | Cutover workspace-only de agentes persistentes

- Runtime canônico do `cto-ops` migrado para `workspace-cto-ops`.
- Helper compartilhado de topologia/aliases criado em `scripts/agent_runtime_topology.py`.
- `mission-control-ids.local.json` e `mc-agent-ids.json` passaram a usar UUIDs reais (`cto-ops` = `717382de-e289-4bcd-a4b8-e850a808fe68`, `quant-strategist` = `b66bda58-1962-4759-88b9-7836dbac9c63`).
- `heartbeat-v3.py`, `mc-client.sh`, `mc-spawn.sh` e `mc-fast-dispatch.sh` passaram a resolver nomes via helper único.
- `workspace/agents/cto-ops` foi congelado como stub legado.
