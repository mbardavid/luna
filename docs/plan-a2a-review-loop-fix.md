# Plano: Corrigir Fluxo A2A Luna ↔ Luan (Review Loop)

## Diagnóstico

### O que deveria acontecer (fluxo 5 fases):
```
1. Luna → task spec → Luan
2. Luan → plano técnico → Luna
3. Luna → review + go-ahead → Luan
4. Luan → implementação + completion report → Luna
5. Luna → validação (lessons, tests, criteria) → done/reject
```

### O que está acontecendo (fire-and-forget):
```
1. Luna → task spec completa → Luan (mode="run")
2. Luan faz tudo sozinho (5min) → completion report
3. Luna recebe report → fecha card
```

### Root Causes:

**RC1 — Luna usa `mode="run"` (one-shot)**
- `mode="run"` não permite comunicação bidirecional
- Luan recebe a task, executa, e morre
- Não há como Luna enviar "go ahead" mid-flight

**RC2 — Task spec já inclui Execution Plan detalhado**
- Luna manda o plano PRONTO pra Luan
- Luan vê o plano e pula Phase 3a (já tem plano, pra que planejar?)
- O Execution Plan deveria ser REMOVIDO da task spec — Luan é quem cria

**RC3 — Risk profile não está no formato que Luan parseia**
- Luan procura `risk_profile: high` em YAML
- Luna manda `## Risk Profile\nHIGH` em markdown
- O trigger não é ativado

**RC4 — Luan não tem incentivo pra parar e esperar**
- `mode="run"` tem timeout — Luan quer terminar antes do timeout
- Parar pra "esperar review" em mode="run" = morrer sem completar

## Solução: Two-Phase Spawn (já previsto, nunca implementado)

### Mudanças necessárias:

### 1. Luna: Mudar task spec structure

**ANTES (Luna decide tudo):**
```
## Execution Plan
### Phase 1: Fazer X
### Phase 2: Fazer Y
```

**DEPOIS (Luna define O QUÊ, Luan define COMO):**
```
## Objective
Implementar startup reconciliation...

## Context
- prod-002 teve 7390 rejections...

## Acceptance Criteria
- [ ] Ordens stale canceladas antes de nova submissão
- [ ] Position tracker com balances on-chain

## Constraints
- Não alterar QuoteEngine
- Proxy Tor obrigatório

## QA Guidance for Luna
- Reconciler usa proxy Tor?
- Testes mockam API calls?

## Risk Profile: HIGH
## Review Required: true
```

**SEM Execution Plan** — isso é trabalho do Luan.

### 2. Luna: Two-phase spawn flow

**Phase 1 — Planning (mode="run", ~2min)**
```
Task: {spec sem execution plan}
Instructions: Create implementation plan only. Save to plans/<task_id>.md. 
Do NOT implement. Do NOT write code. Report plan in completion report.
```

Luna recebe o plano, revisa contra:
- lessons.md do Luan
- Context técnico que ela tem
- QA Guidance

**Phase 2 — Implementation (mode="run", ~10min)**
```
AUTHORIZED — Proceed to Implementation.

## Approved Plan
{plano do Luan, possivelmente com ajustes da Luna}

## Task Spec
{spec original}

Instructions: Follow the approved plan. Implement, test, report.
```

### 3. Luan: Simplificar AGENTS.md

O "Review Loop Protocol" atual é complexo demais (save plan to file, update MC, end session, wait for re-spawn). Simplificar:

```
## Step 3: Plan
- Output plan in 5-10 bullets
- If task has `Review Required: true`:
  - Output ONLY the plan as completion report
  - Status: "plan_submitted" (not "complete")
  - Do NOT proceed to Step 4
- If task has `Review Required: false` or absent:
  - Proceed to Step 4
```

### 4. Luna: QA Review no Step 5

Luna já tem o "QA Review Protocol" em AGENTS.md. Mas não está sendo executado rigorosamente. Checklist:

```
On receiving completion report:
1. Read target agent's memory/lessons.md
2. Cross-reference each lesson with changes
3. Answer each QA Guidance question
4. Verify all Acceptance Criteria are [x]
5. Verify Verification Checks output is present
6. If ALL pass → mc-complete
7. If ANY fail → mc-review-reject with specific feedback
```

### 5. mc-spawn.sh: Remover --plan flag

O `--plan` flag encoraja Luna a mandar o plano pronto. Remover e substituir por `--review-required` flag que adiciona:
```
## Review Required: true
## Risk Profile: {value}

IMPORTANT: Do NOT implement. Create implementation plan only.
Save plan to plans/<task_id>.md and report as status: plan_submitted.
```

## Implementation Checklist

### Luna side (AGENTS.md + scripts):
- [ ] Adicionar seção "Two-Phase Spawn Protocol" em AGENTS.md
- [ ] Criar template de task spec SEM execution plan
- [ ] mc-spawn.sh: adicionar --phase planning|implementation flag
- [ ] mc-spawn.sh: --phase planning → injeta "Do NOT implement, plan only"
- [ ] mc-spawn.sh: --phase implementation → injeta "AUTHORIZED + approved plan"
- [ ] QA Review: tornar obrigatório (não opcional) para tasks HIGH/CRITICAL

### Luan side (AGENTS.md):
- [ ] Simplificar Review Loop Protocol
- [ ] Step 3: se Review Required → output plan only, status plan_submitted
- [ ] Step 3: se NOT Review Required → proceed normally (low risk tasks)
- [ ] Completion Report: adicionar status "plan_submitted" como opção válida

### MC side:
- [ ] Adicionar status "needs_approval" ou usar "review" com flag
- [ ] mc-authorize-plan.sh já existe — integrar no fluxo

## Impacto

### Tasks LOW risk (majority):
- Zero mudança. Luna spawna, Luan implementa, Luna valida. Mesma coisa de hoje.

### Tasks MEDIUM/HIGH/CRITICAL risk:
- 2 spawns ao invés de 1
- ~$0.30 extra por task (1 planning run + 1 implementation run)
- Mas: Luna catch design errors ANTES de gastar 10min de implementação
- Net positive: menos rejeições, menos re-spawns, melhor qualidade

## Timeline
- Estimativa: 1 task do Luan (~30min) para atualizar ambos os AGENTS.md + scripts
- Validação: spawnar uma task HIGH risk e verificar que o fluxo 5-fases funciona
