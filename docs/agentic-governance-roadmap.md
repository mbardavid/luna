# Roadmap Integral de Governança Orquestradora (OpenClaw)

**Status geral:** Em implementação
**Data de início:** 2026-02-26
**Objetivo:** transformar a operação de orquestração em um padrão de controle contínuo, audível e resistente a falhas entre Luna, Luan e CTO-ops.

## 1) Base de verdade e contratos obrigatórios

### Arquivos centrais de decisão

- `docs/agentic-governance-roadmap.md` (este documento)
- `docs/mission-control-unified-plan.md`
- `docs/a2a-mission-control-phase1to2-plan.md`
- `docs/heartbeat-v3-design-doc.md`
- `docs/heartbeat-v3-luan-analysis.md`
- `docs/mc-automation-upgrade-spec.md`

### Contratos e validação

- `docs/agent-orchestration-a2a.md`
- `docs/schemas/task-spec.schema.json`
- `docs/schemas/orchestration-state.schema.json`
- `docs/agent-standard.md`
- `docs/agentic-loop-contract.md` (novo)

### Arquivos operacionais

- `memory/orchestration-state.json`
- `memory/lessons.md`
- `config/mission-control-ids.json`
- `config/mc-agent-ids.json`
- `config/cto-risk-policy.json` (novo)

## 2) Estado atual (resumo)

- Ponto forte: existe cadeia base de MC + heartbeat + watchguards + scripts de atualização.
- Pontos de fragilidade: sem loop de revisão LUNA⇄LUAN por contrato, sem agente CTO persistente, sem rastreabilidade de review/estado de dupla passada, risco de collapse semântico de status no TaskSpec e no estado.

## 3) Matriz de pendências (ordem de execução)

Prioridade = risco operacional > risco técnico > documentação.

| ID | Tema | Risco (alto/médio/baixo) | Proprietário | Documento de origem | Estado-alvo | Dependência |
|---|---|---|---|---|---|---|
| G1 | Contrato A2A 1.1 + anti-conflation de status | ALTO | Luan/Dev | `agent-orchestration-a2a.md` | Merge | Nenhuma |
| G2 | Loop `Luna → Luan → Luna → Luan` operacional | ALTO | Luna/Luan | `agentic-loop-contract.md` | Implementado | G1 |
| G3 | `mc-spawn` atômico + link sessão tarefa | ALTO | Dev | `mc-automation-upgrade-spec.md` | Implementado | G1 |
| G4 | CTO-ops persistente com runbooks e monitoramento | ALTO | Dev/Luna | `AGENTS.md` | Implementado | G1 |
| G5 | Bridge legado subspawn → A2A | MÉDIO | Dev | `docs/migration-legacy-subspawn.md` | Implementado | G1 |
| G6 | Kill-switch e políticas de risco | ALTO | Dev | `config/cto-risk-policy.json` | Implementado | G4 |
| G7 | Smoke + stress e trilha de replay | MÉDIO | Dev | `docs/smoke-test-plan.md` | Em progresso | G2,G3 |
| G8 | Testes de revisão por estado semântico | MÉDIO | Luan | `docs/a2a-loop-debug-guide.md` | Em progresso | G1 |

## 4) Diretrizes de prioridade operacional

1. **Conter risco de perda de rastreabilidade** (status/assinatura/revisão)
2. **Estabilizar controle de risco por agente e operação** (cto-ops)
3. **Padronizar ciclo de aprovação dupla e contra-revisão**
4. **Consolidar execução e automações auxiliares sem tocar no gateway**
5. **Ativar stress e replay em ambiente controlado**

## 5) Contratos de revisão obrigatórios

Para qualquer task de produção com impacto em gateway/infra:

- `risk_profile = high|critical` no TaskSpec
- `review_depth >= 2`
- `review_feedback_required = true`
- `mc` deve registrar estado final em `needs_approval`, `review`, `blocked` sem colapso semântico
- ação de alta energia (`gateway_restart`, `kill`, `deploy`) sem assinatura explícita e trilha de autorização = bloqueio

## 6) Runbook de execução do plano (Fase 0 → Fase 8)

### Fase 0 — Normalização
- Consolidar plano no documento e mapear ownership por tarefa.
- Sincronizar docs de status com versões e responsáveis.

### Fase 1 — Contratos
- Elevar schema A2A para 1.1.
- Inserir novos campos obrigatórios.
- Ajustar status semântico e pipeline review.

### Fase 2 — Agentes persistentes
- Criar `luan-dev` e `cto-ops`.
- Registrar identidade, memória e heartbeat.

### Fase 3 — Núcleo operacional
- Ajustar scripts de spawn, update, watchdog, heartbeat e approvals.
- Criar `agent-loop-orchestrator.sh`.

### Fase 4 — Segurança de risco
- Ativar `config/cto-risk-policy.json` + kill-switch.
- Atualizar cron e monitoramento de gateway para rastrear estado de decisão.

### Fase 5 — Estresse e replay
- Criar matriz de testes e dados de replay.
- Validar casos 01 a 09 do plano.

## 7) Métricas de sucesso (aceitação mínima)

- `test_case_01`: TaskSpec sem `risk_profile` rejeitado pela validação
- `test_case_02`: `needs_approval` não colapsa em `review` no estado da MC
- `test_case_03`: decisão de alta carga aciona CTO-ops sem reboot indevido
- `test_case_04`: reboot gateway crítico com trilha de autorização
- `test_case_05`: loop Luna-Luan em revisão executado sem intervenção manual direta
- `test_case_06`: bridge legado recebe payload sem perda de contexto
- `test_case_07`: spawn em fluxo transacional completo
- `test_case_08`: stress 1000 ciclos sem perda de checkpoint/MTTR > meta
- `test_case_09`: replay de incidente real preserva estado final e não regressa

## 8) Governança contínua

- Toda revisão de schema requer atualização desta roadmap.
- Mudanças de risco (`risk_profile`, `review_depth`, políticas) exigem atualização de 2 fontes:
  1) contrato em schema
  2) runbook/LOG de auditoria (`docs/AGENT_AUDIT_LOG.md`)

## 9) Revisão MC hoje: documentos no estado `review`

Snapshot (2026-03-01, API Mission Control remota): 8 tarefas estão em `review`.

| ID | Título | Prioridade | Retry | Último erro | Docs citados |
|---|---|---:|---:|---|---|
| 727e2c14 | Analysis: ClawSuite — All-in-one OpenClaw Command Center | medium | 0 | missing_session_key | docs/analysis-clawsuite.md |
| 108b8fd4 | Analysis: ClawSuite — All-in-one OpenClaw Command Center | high | 2 | needs_approval | docs/analysis-clawsuite.md |
| 31c63789 | Review clawsuite repo (outsourc-e/clawsuite) | high | 2 | needs_approval | — |
| 07da5f9c | MC Comparison: crshdn/mission-control vs ours | high | 2 | needs_approval | docs/comparison-mission-control-crshdn-vs-ours.md |
| 52ffe746 | Era 3: Agent Fleet Architecture | high | 2 | needs_approval | docs/plan-era3-agent-architecture.md |
| 0b666cea | Crypto-sage: add PnL tracking to Polymarket positions in balance | medium | 2 | needs_approval | — |
| 9c30e2ea | Fix production runner: trade dedup, on-chain reconciliation, wallet sync | medium | 2 | needs_approval | — |
| 4b38f7a7 | PMM-PROD P5: Paper trading com dados live | high | 0 | stalled | — |

### Relação com o plano em execução

- `docs/analysis-clawsuite.md` → base comparativa útil para fase de consolidação de contratos A2A/MC e backlog de evolução de observabilidade.
- `docs/comparison-mission-control-crshdn-vs-ours.md` → alimenta decisões de risco/arquitetura da Fase 3 e ajustes no `mc-automation-upgrade-spec`.
- `docs/plan-era3-agent-architecture.md` → tangencia diretamente `G4` e `G7` (agentes persistentes/estado + anti-degeneração).
- Tarefas **sem** documento associado (Crypto/runner/paper-trading) devem ser tratadas como execução técnica fora do escopo do plano de governança e monitoradas apenas pelo novo loop de revisão (risco e semântica de status).

Observação: os três documentos acima não estão presentes no workspace atual; manter trilha de revisão ativa no MC e evitar nova movimentação em revisão sem retorno de decisão antes de executar os próximos marcos de produção.
