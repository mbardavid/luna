# Autonomy v1 — Scheduler + QA Runner + Projetos/Épicos (Plano)
**MC Task ID:** 6e5effc1-a6bf-4d73-9079-74ba9dc52069  
**Status:** plan_submitted  
**Escopo:** arquitetura + roadmap + especificações mínimas (não implementar agora)

## 0) Contexto e princípios
Stack atual assumida:
- OpenClaw tools + subagents (spawn/push-based)
- Mission Control (MC) como “source of truth” de tarefas e estados
- `heartbeat-v3/` como motor de automação periódica (cron + fila)
- scripts `scripts/mc-*.sh` como *única* forma suportada de mutar MC (spawn/link/update/complete/fail/reject)
- artefatos de lifecycle conforme `docs/a2a-task-lifecycle.md`
- regras A2A + gates/rollback conforme `docs/agent-orchestration-a2a.md`

Princípios de desenho:
1. **Idempotência**: toda decisão de dispatch e QA deve ter chave idempotente (dedupe) e registrar “por quê”
2. **Resiliência por reprocessamento**: scheduler pode cair e recuperar do estado (MC + fila local)
3. **WIP explícito e limitado**: nenhum agente roda sem slot; fila não “explode” por falta de backpressure
4. **Observabilidade auditável**: cada transição (dispatch, retry, QA) vira evento/artefato
5. **Separar decisão vs execução**: scheduler decide *o que* e *quando*; executores (subagents) fazem *como*

---

## 1) Arquitetura proposta (componentes, responsabilidades, interfaces)

### 1.1 Visão geral (fluxo)
1) **Ingest**: novas tarefas entram no MC (manual, webhooks, ou scripts) com metadata e prioridade
2) **Enriquecimento**: “Task Normalizer” aplica schema, tags, risk profile e sugere QA profile
3) **Capacity/WIP Gate**: “Capacity Manager” calcula slots disponíveis por agente/capability
4) **Scheduler/Dispatcher**: seleciona a próxima tarefa elegível e dispara spawn (Phase 1 planning ou Phase 2 impl)
5) **Runner de QA**: após completion (ou em checkpoints), roda validações e publica `05-qa-review.md` + update MC
6) **Projetos/Épicos**: “Epic Planner” quebra em subtasks, dependências e milestones, alimentando o scheduler

### 1.2 Componentes

#### A) Mission Control (já existe) — *source of truth*
- Armazena: tasks, status, dependencies, tags, approvals, session_key, retries
- Interface: scripts `mc-*.sh` + API (mas **mutação** somente via scripts)

#### B) Local Orchestration State (novo, leve)
- Arquivo canônico: `state/autonomy-v1.json` (proposto)
- Responsável por:
  - snapshots de decisões de scheduling (por turno/ciclo)
  - dedupe de dispatch (taskId+phase+attempt)
  - métricas agregadas (throughput, WIP, lead time) sem depender do MC
- Motivo: MC é SoT operacional, mas estado local ajuda reprocessamento rápido e auditoria de heurísticas.

#### C) Capacity Manager (novo)
- Entrada:
  - estado atual MC: tasks em `in_progress`, `review`, `blocked`
  - disponibilidade por agente (config estática + heurística: “tempo desde último heartbeat/exec”)
  - limites por tipo (dev/research/ops) e risco (MEDIUM+ exige gates)
- Saída:
  - `capacity_snapshot` com slots e WIP por agente e por lane

#### D) Scheduler/Dispatcher (novo)
- Responsável por:
  - priorização e fairness (ver seção 3)
  - “eligibility checks” (dependências, approvals, risk gates)
  - emissão de dispatch event (spawn + link MC)
- Implementação sugerida: script Python em `heartbeat-v3/scripts/` chamado por cron (ou incorporado ao `heartbeat-v3.py`).

#### E) QA Runner (novo)
- Responsável por:
  - disparar verificações automatizadas (tests/linters/checklists) para tasks finalizadas
  - capturar outputs e anexar no artefato `05-qa-review.md`
  - aplicar decisão: approve/reject (e, se reject, usar `mc-review-reject.sh` com feedback)
- Execução: geralmente local via `exec` em shell (sem side effects externos), com perfis por repo/projeto.

#### F) Epic/Project Planner (novo)
- Representa Épicos/Projetos com:
  - milestones
  - backlog de subtasks geradas
  - dependências DAG
  - “definition of done” por milestone
- Pode ser acionado:
  - manualmente (criar épico)
  - automaticamente quando um task tem tag `epic:<id>` sem subtasks

#### G) Event Log (opcional, recomendado)
- Arquivo append-only: `logs/autonomy-v1-events.jsonl` (proposto)
- Eventos: `capacity_snapshot`, `dispatch_decision`, `qa_run_started`, `qa_run_finished`, `epic_plan_generated`.

---

## 2) Interfaces e contratos

### 2.1 Contrato do Scheduler (inputs/outputs)

**Inputs mínimos**:
- Lista de tasks candidatas no MC (via scripts ou API read-only)
  - status: `inbox|in_progress|review|blocked|retry` (mapear para os status reais do MC)
  - campos: `task_id`, `title`, `description`, `priority`, `tags`, `risk`, `assigned_agent`, `retry_count`
- Capacity snapshot:
  - slots disponíveis por agente/capability
- Config:
  - pesos de prioridade
  - limites de rate (spawns por minuto)
  - WIP por lane (dev/research/ops)

**Output mínimo (dispatch decision)**:
```json
{
  "decisionId": "dec_20260303T223500Z_0001",
  "taskId": "6e5effc1-a6bf-4d73-9079-74ba9dc52069",
  "phase": "planning|implementation|qa",
  "targetAgent": "luan|crypto-sage|...",
  "reason": {
    "priorityScore": 0.82,
    "fairnessDebt": 0.10,
    "ageSeconds": 3600,
    "riskGate": "passed"
  },
  "idempotencyKey": "dispatch:task=...:phase=planning:attempt=1",
  "createdAt": "ISO8601"
}
```

**Side-effects permitidos do Scheduler**:
- `mc-spawn*.sh` + spawn tool + `mc-link-task-session.sh`
- `mc-task-update.sh` para registrar decisão (ex.: `dispatch_decisionId`, `queue_lane`, `qa_profile`)

### 2.2 Contrato do QA Runner
**Triggers**:
- Task entra em estado “pronto para QA” (ex.: status done do agente executor, ou `04-completion.md` existe)
- Evento periódico (cron) revalida tasks com QA pendente
- Manual: comando “re-run QA” (gera novo runId)

**Inputs**:
- `task_id`
- Artefatos do lifecycle (`tasks/<task_id>/04-completion.md`, lista de arquivos, comandos sugeridos)
- “QA profile” (ex.: `python-fast`, `python-full`, `ops-checks`, `docs-only`)

**Outputs**:
- Artefato `tasks/<task_id>/05-qa-review.md` (ou, neste projeto, manter também uma cópia em `artifacts/a2a/<taskPrefix>/...` se desejado)
- Atualização no MC:
  - `decision=approved|rejected`
  - se rejected: feedback obrigatório + reset para re-dispatch

**Formato mínimo do resultado QA (estrutura)**:
```yaml
qa_run:
  run_id: qa_20260303T230100Z_0001
  task_id: ...
  profile: python-fast
  started_at: ...
  finished_at: ...
  decision: approved|rejected
  verification_ran: true
  commands:
    - cmd: "pytest -q"
      exit_code: 0
      seconds: 120
  notes: |
    ...
```

---

## 3) Especificação mínima do Scheduler

### 3.1 Priorização (score)
Score recomendado (0..1) por task:
- `P`: prioridade explícita (MC field/label) — peso 0.40
- `A`: aging (tempo no inbox/retry) — peso 0.25
- `R`: risco/criticidade (ops>dev>research; MEDIUM+ pode exigir planning primeiro) — peso 0.15
- `D`: dependências (penaliza tasks bloqueadas) — peso 0.10
- `F`: fairness debt (garante round-robin entre lanes/epics) — peso 0.10

`score = 0.40P + 0.25A + 0.15R + 0.10(1-D) + 0.10F`

### 3.2 Fairness
Requisitos:
- **Fairness entre lanes**: reservar % de slots (ex.: dev 50%, ops 30%, research 20%) com possibilidade de “borrow” quando fila vazia.
- **Fairness entre épicos**: dentro de uma lane, usar “deficit round robin” por `epic_id` para não starvar épicos grandes.

Implementação simples:
- manter `fairnessDebt` por (lane, epic_id) em `state/autonomy-v1.json`
- cada dispatch consome crédito; créditos acumulam por tempo

### 3.3 Rate limit
- global: max `N` spawns / minuto (ex.: 4/min)
- por agente: max `M` spawns / 10min (evita saturar Luan)
- por task: no máximo 1 spawn ativo por phase

### 3.4 Dedup / idempotência
Chave idempotente canônica:
`dispatch:task=<taskId>:phase=<phase>:attempt=<k>`

Regras:
- se `idempotencyKey` já existe em `state/autonomy-v1.json.dispatched`, não disparar de novo
- se MC já tem `session_key` ativo para aquele task/phase, não disparar
- se a última tentativa falhou com erro “transiente”, aplicar backoff exponencial

### 3.5 Eligibility checks (resiliência)
Antes de dispatch:
- TaskSpec (A2A) válido (para handoffs entre agentes)
- Gates MEDIUM+:
  - planning obrigatório antes de implementation (salvo exceções aprovadas)
  - approvals/authorize-plan quando aplicável (`mc-authorize-plan.sh` fluxo)
- Dependências resolvidas (DAG sem bloqueios)
- WIP slot disponível

### 3.6 Estados e transições (integração com MC)
Mapear para os gates do `docs/a2a-task-lifecycle.md`:
- `planning_dispatched` → aguardando `02-plan.md`
- `plan_submitted` → aguardando `03-plan-review.md`
- `implementation_dispatched` → aguardando `04-completion.md`
- `qa_pending` → aguardando `05-qa-review.md`

Onde armazenar:
- MC custom fields (se existirem) **ou** tags padronizadas `autonomy:state=<...>`.

---

## 4) QA Runner — desenho e publish de resultados

### 4.1 Perfis de QA (mínimo viável)
- `docs-only`: markdown lint + link check (quando aplicável)
- `python-fast`: `ruff`/`pytest -q` subset
- `python-full`: `pytest` completo + coverage (se já existir)
- `node-fast`: `npm test`/`pnpm test` + lint
- `ops-checks`: `shellcheck`, validação de scripts, grep de padrões proibidos (ex.: `rm -rf` sem guard)

Cada profile define:
- comandos
- timeout por comando
- artefatos a capturar

### 4.2 Integração com completion report
O QA Runner consome o bloco estruturado de `04-completion.md` (ou o bloco final do completion report) para:
- `FILES_LIST`
- `VERIFICATION_OUTPUT`
- `VERIFICATION CHECKS` declarados

Se o completion report estiver incompleto:
- QA Runner marca `rejected` com feedback: “completion report missing structured block”

### 4.3 Publicação
- Escrever `tasks/<task_id>/05-qa-review.md`
- Atualizar `tasks/<task_id>/metadata.json.phases.qa_review` (quando adotado)
- Atualizar MC:
  - approved → `done`
  - rejected → usar `scripts/mc-review-reject.sh --feedback ...` (conforme AGENTS.md)

### 4.4 Retry e flakiness
- distinguir:
  - falha determinística (lint erro) → reject direto
  - falha flake (test intermitente) → permitir 1 re-run automático com etiqueta `qa:rerun=1`
- guardar histórico em `logs/autonomy-v1-events.jsonl`

---

## 5) Modelo de dados — Épicos/Projetos (subtasks, dependências, milestones)

### 5.1 Entidades

**Epic**
```json
{
  "epicId": "epic_20260301_polymarket_refactor",
  "title": "Refactor runner + stability",
  "owner": "main",
  "lane": "dev|ops|research",
  "priority": "P0|P1|P2",
  "createdAt": "ISO8601",
  "milestones": ["ms1", "ms2"],
  "taskIds": ["uuid", "uuid"],
  "policy": {
    "wipLimit": 2,
    "maxParallelAgents": 1
  }
}
```

**Milestone**
```json
{
  "milestoneId": "ms1",
  "epicId": "...",
  "title": "Phase 1 complete",
  "doneWhen": ["All QA approved", "No P0 bugs open"],
  "dueAt": null
}
```

**TaskLink (dependency)**
- Preferir usar dependency model do MC se disponível.
- Caso precise localmente:
```json
{
  "fromTaskId": "A",
  "toTaskId": "B",
  "type": "blocks|relates|duplicates",
  "createdAt": "ISO8601"
}
```

### 5.2 Onde persistir
Opção A (preferida): usar recursos nativos do MC
- `task_dependencies` e tags/fields para `epicId`/`milestoneId`

Opção B (MVP local):
- `state/epics/*.json` e sincronização periódica com MC via tags.

### 5.3 Geração de subtasks
Epic Planner recebe:
- objetivo do épico
- constraints
- definition of done

Ele produz:
- lista de tasks com
  - títulos curtos
  - acceptance criteria
  - verification checks
  - dependências
  - risk profile

E cria no MC via `mc-spawn.sh` (ou lote com wrapper), adicionando tag `epic:<epicId>`.

---

## 6) Roadmap por fases (done, riscos, rollback)

### Fase 0 — Alinhar dados e campos no MC (1-2 dias)
**Objetivo:** garantir que tasks tenham os campos mínimos para scheduling/QA.

Deliverables:
- Convenção de tags:
  - `lane:dev|ops|research`
  - `risk:low|medium|high|critical`
  - `epic:<id>` (quando aplicável)
  - `qa:profile=<name>`
- Definir WIP limits padrão por lane.

Done when:
- [ ] 90% das tasks novas entram com `lane` + `risk`
- [ ] existe mapeamento documentado MC status → autonomy states

Riscos:
- dados incompletos atrapalham fairness; mitigação: defaults conservadores.

Rollback:
- manter scheduler desligado; operação manual via MC.

### Fase 1 — Scheduler MVP (dispatch resiliente) (3-5 dias)
**Objetivo:** selecionar e disparar tarefas respeitando WIP e dedupe.

Deliverables:
- `heartbeat-v3/scripts/autonomy_scheduler.py` (proposto)
- `state/autonomy-v1.json` + `logs/autonomy-v1-events.jsonl`
- Implementar:
  - WIP gate
  - rate limit
  - dedupe idempotente
  - fairness simples por lane

Done when:
- [ ] scheduler roda por cron e faz no máximo 1 dispatch por ciclo
- [ ] nenhum task recebe 2 spawns simultâneos
- [ ] após restart, scheduler não “duplica” dispatch

Riscos:
- corrida com watchdogs existentes; mitigação: integrar via heartbeat-v3 (mesmo loop).

Rollback:
- feature flag `AUTONOMY_SCHEDULER_ENABLED=false`.

### Fase 2 — QA Runner MVP (3-5 dias)
**Objetivo:** automatizar checks e publicar decisão QA.

Deliverables:
- `heartbeat-v3/scripts/autonomy_qa_runner.py` (proposto)
- perfis `qa/profiles/*.yaml` (proposto)
- escrita de `tasks/<id>/05-qa-review.md` + update MC

Done when:
- [ ] para uma task de exemplo, QA roda e marca approved/rejected
- [ ] rejeição usa `mc-review-reject.sh` com feedback acionável

Riscos:
- comandos pesados (pytest) → tempo/CPU; mitigação: `python-fast` default + timeouts.

Rollback:
- QA runner em modo “report-only” (não muda MC) por 1 semana.

### Fase 3 — Projetos/Épicos (planner + dependências) (5-10 dias)
**Objetivo:** suportar backlog grande com decomposição e governança.

Deliverables:
- `state/epics/*.json` (ou integração MC) + doc `docs/autonomy-epics.md` (proposto)
- geração de subtasks com dependências
- fairness por epic (deficit round robin)

Done when:
- [ ] 1 épico real criado e executado com milestones
- [ ] scheduler respeita dependências (não dispatch task bloqueada)

Riscos:
- explosão de subtasks; mitigação: limites por épico + revisão humana na geração.

Rollback:
- desativar auto-geração; manter apenas tagging manual.

### Fase 4 — Integração completa com gates/approvals/retries (ongoing)
**Objetivo:** fechar o loop com autorizações (MEDIUM+), retries e watchdog.

Deliverables:
- integração com `mc-authorize-plan.sh` (2-phase spawn) e `mc-fail.sh --retry`
- políticas de retry por tipo de erro
- dashboards básicos (WIP, throughput)

Done when:
- [ ] retries automáticos limitados (max 2) com backoff e reason
- [ ] tasks MEDIUM+ seguem planning→review→implementation→QA sem bypass

Rollback:
- fallback para fluxo manual (scripts existentes) e desativar automações.

---

## 7) Considerações de implementação (quando for implementar)

### 7.1 Onde encaixar no heartbeat-v3
- Melhor: adicionar um “consumer” no `heartbeat-v3/scripts/heartbeat-v3.py` que:
  1) roda `capacity_snapshot`
  2) tenta 0/1 dispatch
  3) tenta 0/1 QA run
  4) registra eventos

### 7.2 Convivência com watchdogs
- `scripts/mc-watchdog.sh` e heartbeat-v3 já detectam “sessões terminadas sem update”.
- Scheduler deve:
  - nunca assumir que ausência de session_key = livre; checar status real.
  - preferir *read-only* do MC para decidir e só mutar via scripts.

### 7.3 Segurança e gates A2A
- Para tasks que envolvem `delegated-human-proxy` ou promoções `simulated->live`, respeitar `docs/agent-orchestration-a2a.md`:
  - policy+envelope+TTL+dedupe
  - rollback obrigatório
  - anti-self-test (E2E só com human/authorized-harness)

---

## 8) Checklist de aceitação (mapeamento)
- [x] Arquitetura proposta (componentes, responsabilidades, interfaces)
- [x] Roadmap por fases com critérios de done, riscos e rollback
- [x] Especificação mínima do scheduler (inputs/outputs; fairness; rate limit; dedup)
- [x] Especificação do QA runner (gatilhos; como rodar checks; como publicar resultados)
- [x] Modelo de dados para Épicos/Projetos (subtasks, dependências)
- [x] Saída em artifacts: `artifacts/a2a/6e5effc1/plan.md`
