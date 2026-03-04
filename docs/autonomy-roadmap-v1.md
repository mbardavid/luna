# Autonomy Roadmap v1 — Heartbeat v3 + Mission Control + Queue (Plano)

**Task:** MC 6e5effc1-a6bf-4d73-9079-74ba9dc52069  
**Scope:** Pesquisa/arquitetura (sem implementação)  
**Objetivo de alto nível:** evoluir o sistema de execução contínua para manter alto volume de tasks (research/dev/ops), com autonomia orientada por capacidade, QA automático e criação de projetos/épicos.

---

## 0) Princípios de design

### Leituras incorporadas (2026-03-04)
- Cursor — “Scaling long-running autonomous coding”: https://cursor.com/blog/scaling-agents
  - Principais ideias incorporadas neste plano: limites de single-agent; falhas de coordenação flat/locks; pipeline **planners → workers → judge**; *fresh starts*; simplicidade > complexidade; modelos distintos por papel.
  - Links citados no post (exemplos de projetos long-running):
    - https://github.com/wilsonzlin/fastrender
    - https://github.com/wilson-anysphere/indonesia
    - https://github.com/wilsonzlin/aero
    - https://github.com/wilson-anysphere/formula


1. **Primeiro garantir controle, depois escala.** Sem guardrails, aumentar volume só acelera falha.
2. **Todo dispatch é transacional e observável** (task_state + artifacts + dedup + sessão).
3. **Feedback rápido, retry seguro:** falha é esperada; perda de contexto não.
4. **Sem deadlock entre revisão e fila ativa:** review sempre tem prioridade operacional.
5. **Sem auto-execução sem verificação e custo/segurança explícitos.**

---

## 1) Escopo atual vs estado-alvo

### Estado-alvo (v1)

- **Scheduler contínuo** com 2 prioridades (review + inbox) e WIP caps por tipo.
- **MC + queue** com identidade idempotente por task para evitar respawn duplicado.
- **QA loop** rodando por ciclo, fechando 1–2 reviews com checks obrigatórios e evidências.
- **Projetos/épicos** com geração determinística de milestones → subtasks, com cadência semanal de governança.
- **Recuperação ativa** em restart/gateway recovery com wake-up instantâneo e fallback queue.
- **Segurança by default:** custo, ações externas e kill-switch.

### Não está no escopo da F1

- Mudanças de produto/UX de produto final (somente motor operacional).
- Migração massiva de linguagem (manter bash+python incremental).
- Deploy em produção sem validação F2/F3 concluídas.

---

## 2) Arquitetura proposta (v1)

### 2.0) Ajuste de arquitetura após Cursor “Scaling Agents”

O nosso desenho v1 deve se aproximar explicitamente do padrão **Planners → Workers → Judge**:

- **Planner (Luna/main)**
  - cria/deriva tarefas (MC inbox) de forma contínua;
  - mantém visão do épico/projeto;
  - não executa mudanças grandes de código; delega.

- **Workers (Luan dev, Quant research, Crypto-sage executor)**
  - executam tarefas isoladas;
  - não tentam auto-coordenar com outros workers via locking;
  - entregam artifacts + verification checks.

- **Judge (Luna/main)**
  - faz QA e decide “continuar/ajustar/parar”; fecha MC cards;
  - aplica “fresh start” quando notar drift/stall.

Isso reduz risco de:
- deadlock por locks/coordenação;
- aversão a risco em estrutura flat;
- churn sem progresso.


```
heartbeat loop (TICK every 10s/30s)
  ├─ Poll queues (inbox, review, project queues)
  ├─ Compute capacity/ratelimit/circuit-breaker state
  ├─ Select at most N tasks by policy (review-first)
  ├─ Dispatch via gateway call agent / session link / mc_session_key
  ├─ Emit run artifacts (briefing, trace ids, pre/post state)
  └─ Persist checkpoint

QA loop (separate tick, 1–2 items/cycle)
  ├─ Pick review candidates with evidence pointers
  ├─ Run mandatory verification checks
  ├─ Post review outcome + close task(s)
  └─ Return deterministic summary for governance

Project/epic orchestrator (weekly cycle)
  ├─ Build parent card + milestones
  ├─ Expand deterministically milestones->subtasks
  ├─ Enforce acceptance criteria contracts
  └─ Attach governance tags + owners
```

---

## 3) Cobertura obrigatória solicitada

### 3.1 Scheduler/Capacity

**Meta:** alto throughput com contenção ativa.

#### 3.1.1 WIP caps por tipo
- Definir caps por categoria:
  - `research`: WIP 8 (ou dinâmico)
  - `dev`: WIP 6
  - `ops`: WIP 4
- Regra de cálculo global: `active_wip_total <= Σcap_type` e `active_by_type <= cap_type`.
- Se um tipo atingir cap, apenas reclassificar novos itens para fila de espera (não estourar memória/processo).

#### 3.1.2 Prioridade `review > inbox` sem deadlock

Incorporação direta das falhas relatadas pela Cursor:
- **Evitar coordenação via lock** (brittle, cria gargalo e esquecimento de lock).
- Preferir **papéis e filas**: review/QA é papel do Judge; workers só executam.
- Implementar dedup/optimistic checks no MC/queue (idempotencyKey + session_key linking), não locking manual.
- Ordenação global por prioridade: `review` primeiro, depois `inbox`.
- Em janela de review saudável, executar **máximo 60%** do lote disponível em review antes de liberar inbox.
- Deadlock guard:
  - Se revisão travada > N ciclos sem ação (ex.: 3), reduzir lote de entrada e forçar inspeção de 1 review por ciclo por ciclo de emergência.
  - `review_blocker` só abre por revisão explícita ou decisão humana, não por falta de capacidade.

#### 3.1.3 Rate-limit dinâmico

A Cursor destaca que muitos ganhos vêm de **remover complexidade** e escolher o modelo certo por papel.
No nosso caso, isso vira:
- rate-limit por **capacidade/saúde** (health-gated dispatch) em vez de teto cego por hora;
- limites por papel (WIP caps) e *fresh starts* quando houver drift;
- manter o worker “grinding” em uma tarefa por vez, em vez de auto-coordenação ampla.
- Inputs por ciclo:
  - backlog_depth (nº de itens por fila)
  - recent_failure_rate (últimos 10 ciclos)
  - resource_pressure (memória/cpu/session count)
- Fórmula base:
  - `base_rate = 2` dispatches/ciclo
  - `adj = clamp(1 - backlog_factor - failure_factor - resource_factor, 0.2, 1.0)`
  - `dispatch_limit = floor(base_rate * adj * num_queues_active)`
- Exemplo de guard:
  - backlog alto + resource alto + falhas => reduz para 1 dispatch/ciclo.
  - recurso saudável + backlog estável => sobe até cap por tipo.

#### 3.1.4 Circuit breaker + retry/backoff
- **CB por tipo + global:** estados `closed/open/half_open`.
- Falha sequencial > threshold abre `open` e bloqueia dispatch desse fluxo por janela curta.
- Retry com exponencial `2^n + jitter`, com cap de tentativas por task.
- Half-open testa com 1 task de menor risco antes de reabrir fluxo.

---

### 3.2 QA Runner

**Meta:** `review -> done` com revisão automática consistente.

#### 3.2.1 DoD (checks obrigatórios)
Cada review executa checklist mínimo:
1. **Conformidade de contrato:** task possui `definition_of_done`, critérios verificáveis e artefato de evidência.
2. **Validação técnica obrigatória:** testes indicados no card rodam e retornam sucesso.
3. **Consistência de artefatos:** saída de artifacts anexada + resumo de risco.
4. **Sem regressão de recursos:** não violou custo/circuit e não gerou side effects críticos.

#### 3.2.2 QA loop por ciclo (1–2 revisões)
- Selecionar até 2 reviews por ciclo (ordem: revisão mais antiga + maior risco).
- Cada ciclo executa no máximo 1 novo ciclo de revisão + uma reavaliação de `retry`.
- Atualiza estado: `review -> done|retry|blocked|needs_approval`.
- Em falha, manter context snapshot de 1 ciclo anterior para prevenir repetição e **não duplicar memória/explicação**.

#### 3.2.3 Safeguards de memória
- Store de memória curta no estado do loop: apenas as últimas 3 execuções (hash, verdict, diff-resumo).
- Artefatos pesados referenciados por path/id, não embutidos no estado.
- Requisição de contexto completo só em re-review manual (evita inflação de contexto contínuo).

---

### 3.3 Project/Epics

**Meta:** criação autônoma de trabalho escalável por projeto.

1. **Modelo:** Card pai (`epic`) com milestones determinísticas (`m1..mN`).
2. **Subtask generation:** função determinística pura:
   - entrada: tema + objetivo + prazo + critérios
   - saída: subtasks ordenadas e versionáveis (mesma entrada = mesmo roadmap).
3. **Vínculo de dependências:** subtasks com dependência entre marcos (`depends_on`).
4. **Governança semanal (`weekly_loop`):**
   - report de progresso por épico
   - reclassificação de risco
   - aprovação de novas metas
   - decisão de avanço de autonomia.

---

### 3.4 MC Integration

**Meta:** rastreabilidade total de execução.

#### 3.4.1 Status model
- Estados mínimos:
  - `inbox`, `review`, `in_progress`, `review_retry`, `needs_approval`, `blocked`, `done`, `failed`.
- O estado `review` é único para controle humano e não conflita com `needs_approval`.

#### 3.4.2 Comments, artifacts, dedup
- Cada atualização grava:
  - `brief_comment` (texto curto)
  - `artifacts` (lista de paths: logs, diff, tests, reports)
  - `evidence_hash`
- Dedup de updates por `idempotency_key = task_id + phase + run_id`.

#### 3.4.3 session_key linking + respawn seguro
- Em dispatch bem-sucedido: registrar `mc_session_key` imediatamente.
- Antes de respawn: verificar `mc_session_key + heartbeat_liveness + task signature`.
- Prevenir respawn duplicado: se `mc_session_key` ativo, não criar novo; se morto, reusar estratégia de `resume` com novo `idempotencyKey`.

---

### 3.5 Failure & Recovery

#### 3.5.1 Gateway restarts
- On restart: wakeup imediato via `openclaw gateway call agent --json --params '{"message":"...","idempotencyKey":"..."}'`.
- Envio **idempotente** por evento (id gerado por task+timestamp).

#### 3.5.2 Fallback queue e provider dead
- `dispatcher` mantém `fallback_queue` persistente no disco (file/SQLite).
- Se provider de dispatch falhar (p.ex. cron/provider indisponível), enfileirar local e executar no próximo ciclo saudável.
- Health check do provider + alarme no heartbeat.

#### 3.5.3 Fast-dispatch pitfalls
- Não despachar tarefa sem:
  1. capacity permit
  2. idempotency key único
  3. assignee resolvido
  4. budget gate OK
- Evitar cascata: se o loop detectar 3 falhas seguidas iguais, entrar em degradação (`half-open` em 1 ciclo).

---

### 3.6 Safety / Guardrails

1. **Limites de custo:** teto diário/semanal por board e por agente.
2. **Gates explícitos para ações externas:** qualquer operação `write` exige
   - pre-check do estado
   - classificação do risco
   - justificativa curta no comentário.
3. **Kill-switches:**
   - memória/cpu crítico
   - alta taxa de falha por tipo
   - policy violation detectada
   - execução manual da missão de bloqueio (`blocked`).

---

### 3.7 Rollout incremental — F0..F3

## F0 — Baseline e instrumentação
- Objetivo: mapear estado real (sem alterar fluxo crítico).
- Arquivos:
  - `docs/autonomy-roadmap-v1.md` (este plano)
  - `scripts/heartbeat-v3/observability.sh` *(novo)*: métricas de fila, WIP, retry, falhas.
  - `scripts/heartbeat-v3/config.yaml` *(novo)*: caps, thresholds, guardrails.

**Verificações:**
- `python3 scripts/heartbeat-v3/observability.py --dry-run`
- `bash scripts/heartbeat-v3/f0-smoke.sh` (sem dispatch real)

## F1 — Scheduler + Capacity + Recovery base
- Objetivos: WIP caps, review priority, rate-limit dinâmico, CB+retry.
- Arquivos:
  - `scripts/heartbeat-v3/dispatcher.py` *(novo)*
  - `scripts/heartbeat-v3/capacity.py` *(novo)*
  - `scripts/heartbeat-v3/circuit_breaker.py` *(novo)*
  - `scripts/heartbeat-v3/state_store.py` *(novo)*
  - `scripts/heartbeat-v3/run_loop.py` *(novo/editar de wrapper atual se existir)*
  - `tests/autonomy/test_v3_scheduler.py` *(novo)*

**Verificações:**
- `pytest tests/autonomy/test_v3_scheduler.py`
- Cenários S1: backlog alto respeitando caps por tipo
- Cenários S2: review prioritized sem starvation
- Cenários S3: CB abre e fecha automaticamente

## F2 — QA Loop e determinismo de revisão
- Objetivos: DoD, fechamento de 1–2 reviews por ciclo, memória curta.
- Arquivos:
  - `scripts/heartbeat-v3/qa_runner.py` *(novo)*
  - `scripts/heartbeat-v3/evidence_store.py` *(novo)*
  - `scripts/heartbeat-v3/qa_checks.yaml` *(novo)*
  - `scripts/heartbeat-v3/run_loop.py` *(editar)*
  - `tests/autonomy/test_v3_qa_loop.py` *(novo)*

**Verificações:**
- `pytest tests/autonomy/test_v3_qa_loop.py`
- Cenário S4: 2 reviews no máximo por ciclo
- Cenário S5: retry não repete contexto completo

## F3 — Projects/Epics + MC hardening + Recovery total
- Objetivos: card pai/milestones/subtasks determinísticos, governance loop, MC integration madura.
- Arquivos:
  - `scripts/heartbeat-v3/epic_planner.py` *(novo)*
  - `scripts/heartbeat-v3/mc_bridge.py` *(novo)*
  - `scripts/heartbeat-v3/recovery.py` *(novo)*
  - `scripts/heartbeat-v3/run_loop.py` *(editar)*
  - `tests/autonomy/test_v3_epics.py` *(novo)*
  - `tests/autonomy/test_v3_mc_bridge.py` *(novo)*
  - `tests/autonomy/test_v3_recovery.py` *(novo)*
  - `docs/ops/autonomy-v1-runbook.md` *(novo)*

**Verificações:**
- `pytest tests/autonomy/test_v3_epics.py tests/autonomy/test_v3_mc_bridge.py`
- `bash scripts/heartbeat-v3/f3-canary.sh --simulate-restart`
- `bash scripts/heartbeat-v3/f3-weekly-governance.sh --dry-run`

---

## 4) Checklist de implementação (ordem recomendada)

1. Criar estrutura de configuração e contratos (`config + schemas`) para capacidades, status, limites e critérios.
2. Implementar camada de estado persistente (`state_store`) e deduplicação por `idempotency_key`.
3. Refatorar dispatcher para política de seleção de tarefa e caps.
4. Adicionar/ativar CB + backoff por tipo + circuito global.
5. Acoplar QA runner com evidência e limite de 1–2 revisões/ciclo.
6. Implementar gerador determinístico de épicos/subtasks.
7. Integrar estado MC completo: status model, artifacts, dedup, session_key.
8. Implementar falha/recuperação: fallback queue e wakeup via gateway idempotente.
9. Ativar kill-switches e gates de custo/ação externa.
10. Rodar rollout incremental F0→F3 com checklist de verificação de fase.

---

## 5) Riscos, mitigação e métricas de sucesso

### Rollback / Safe rollback (obrigatório)

Como este roadmap mexe com dispatch/heartbeat/MC, todo rollout deve ser **reversível**.

Regras:
- Qualquer mudança de scheduler/dispatch deve ficar atrás de **feature flag** (ex.: `v3-config.json`) com default conservador.
- Se ocorrer loop de retry/orphans ou crash loop do gateway:
  1) **desabilitar** o novo comportamento (feature flag off)
  2) voltar para “dispatch mínimo” (rate-limit baixo + review-only)
  3) executar `scripts/gateway-safe-restart.sh` **somente** se thresholds indicarem necessidade
  4) registrar incidente como comentário no card + snapshot em `docs/state-snapshot-*.md`

Checklist de rollback por fase:
- F0: somente instrumentação → rollback = remover logs/flags
- F1: scheduler básico → rollback = voltar para rate-limit fixo
- F2: QA loop → rollback = desabilitar auto-close e manter QA manual
- F3: epics/projects → rollback = parar geração automática e manter apenas cards já existentes


### Riscos principais
- **Super-automatização sem validação humana** em tarefas de risco alto.
  - Mitigação: `needs_approval` obrigatório por categoria e `blocked` para ações críticas.
- **Deadlock entre review e execução.**
  - Mitigação: janela mínima de atendimento review por ciclo + watchdog de estagnação.
- **Respawn duplicado / ghost tasks.**
  - Mitigação: `session_key` + heartbeat session liveness + idempotencyKey de dispatch.
- **Falha de provider de scheduling.**
  - Mitigação: fallback queue local + reconciliação de estado pós-restart.

### Métricas de sucesso (meta inicial)
- Throughput: `+60%` de tarefas concluídas/dia após F3.
- Tempo p95 review->done reduzido.
- Taxa de respawn duplicado < `0.5%`.
- Falha de dispatch sem artefato/estado persistido = `0`.
- Custo por dia respeitando budget com alertas automáticos.

---

## 6) Entregáveis esperados (pós implementação)

- `docs/autonomy-roadmap-v1.md` (este documento)
- Scripts/artefatos de v3 modularizados
- Testes unitários/integrados por fase
- Runbook de operação semanal e política de governança
- Painel mínimo de KPIs (fila, WIP, taxa de revisão, falhas, custo)

---

### Estado do plano

- **Plano sequencial:** sim
- **Cobertura de arquivos/scrpits:** listada por seção F0..F3
- **Verificação por fase:** definida por bloco de testes/comandos
- **Recomendações acionáveis + riscos:** sim, no item 5
