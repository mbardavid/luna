# Relatório de Validação — Control Loop Autônomo v2

**Data:** 2026-03-03
**Auditor:** Matheus (via Claude Code)
**Plano:** `docs/plan-control-loop-v2.md`
**Commits:** `d677198` (F0), `f11d300` (F1), `eff178e` (F2), `6eea66e` (F3/F4)

---

## Resumo Executivo

| Fase | Status | Score | Blocker |
|------|--------|-------|---------|
| **Fase 0** — Baseline, SLOs, Ownership | ✅ COMPLETA | 5/5 criteria | — |
| **Fase 1** — Absorver Detectores | ✅ COMPLETA | 10/10 criteria | — |
| **Fase 2** — Redução de Latência | ✅ COMPLETA | 7/7 criteria | — |
| **Fase 3** — Dual-Run e Depreciação | ⚠️ INCOMPLETA | 2/6 criteria | GAP gateway + sem archive |
| **Fase 4** — Cleanup e Docs | ✅ COMPLETA (condicionada à F3) | 6/7 criteria | Depende da F3 |

**Resultado global:** ~85% do plano executado com sucesso. Fases 0, 1, 2 e 4 sólidas. Fase 3 tem gap de validação.

---

## Fase 0 — Baseline, SLOs e Ownership Matrix

**Commit:** `d677198` (2026-03-02 22:41)
**Status:** ✅ COMPLETA (5/5)

### Entregáveis

| # | Entregável | Status | Detalhes |
|---|-----------|--------|----------|
| 1 | `docs/cron-inventory.md` | ✅ | 13 crons ativos + 8 depreciados documentados. Verificado contra `crontab -l`. |
| 2 | `docs/ownership-matrix.md` | ✅ | 17 tipos de evento mapeados (plano pedia ~14). Owners e ações definidos. |
| 3 | `docs/slos.md` | ✅ | 7 SLOs com baseline, método de medição e plano de acompanhamento. |
| 4 | `state/control-loop-metrics.json` | ✅ | Schema v1, JSON válido, medição ativa (S1, S2, S7). |
| 5 | `docs/crontab-pre-migration.txt` | ✅ | Backup do crontab original (16 entries). |

### Acceptance Criteria

- [x] `cron-inventory.md` lista todos os crons com status verificado
- [x] `ownership-matrix.md` cobre todos os tipos de evento da seção 3.4
- [x] SLOs documentados com método de medição e baseline
- [x] `state/control-loop-metrics.json` schema criado e validado
- [x] Git commit com Fase 0 entregue

---

## Fase 1 — Absorver Detectores no heartbeat-v3

**Commit:** `f11d300` (2026-03-02 23:59)
**Status:** ✅ COMPLETA (10/10)
**LOC:** 1185 → 1758 (+573 linhas)

### 1.1 heartbeat-v3.py — Enhancements

#### Phase 1 Enhanced: PMM PID Check + Auto-Restart
- **Localização:** `check_pmm_health()` linhas 667-801
- **Funcionalidades:**
  - Lê PID file em `polymarket-mm/paper/data/production_trading.pid`
  - Verifica processo vivo via `os.kill(pid, 0)`
  - Auto-restart com `subprocess.Popen(start_new_session=True)`
  - Carrega `.env` via `parse_env_file()`
  - Cooldown de 5 minutos entre restarts
  - Max 3 restarts/hora com crash loop detection
  - Alerta Discord quando rate limit é atingido
- **Status:** ✅ PASS

#### Phase 4 Enhanced: Failure Classification (6 categorias)
- **Localização:** `classify_failure()` linhas 804-913
- **Categorias implementadas:**
  1. `LOOP_DEGENERATIVO` — mesmo tool chamado 5+ vezes em sequência
  2. `INCOMPLETE` — stopReason=stop mas sem COMPLETION_STATUS
  3. `THINKING_SIGNATURE` — erro "thinking.signature: Field required"
  4. `PROVIDER_ERROR` — erros API (400, 429, 500, RESOURCE_EXHAUSTED)
  5. `TIMEOUT` — sessão excedeu runTimeoutSeconds
  6. `GENERIC_ERROR` — não classificável (fallback)
- **Config:** `failure_classification.loop_threshold = 5`, `known_provider_errors` configurável
- **Status:** ✅ PASS

#### Phase 4.8 NEW: Description Quality Audit
- **Localização:** `check_description_quality()` linhas 916-965
- **Checks:**
  - Comprimento mínimo: 200 caracteres
  - Markers estruturais: `## `, `Objective`, `Objetivo`, `Context`, `Criteria`, `Problem`, `Approach`, `Plano`
  - Status verificados: `inbox`, `in_progress`, `review`
  - Dedup via `state["absorbed"]["alerted_description_violations"]`
- **Status:** ✅ PASS

#### Phase 5.5 Enhanced: Completion Pending QA
- **Localização:** `detect_stale_and_completions()` linhas 1009-1095
- **Detecções:**
  1. **ORPHAN:** task in_progress/review sem session_key
  2. **COMPLETION_PENDING:** sessão morta com COMPLETION_STATUS → gera `qa-review` queue item
  3. **STALE:** sessão morta sem completion (comportamento existente)
- **Cooldown:** 30 minutos por task via `completion_pending_notified`
- **Status:** ✅ PASS

### 1.2 queue-consumer.py

- **Localização:** `_build_qa_review_result()` linhas 387-439
- **Funcionalidade:** Aceita queue type `qa-review`, gera action brief com checklist de 6 pontos para Luna
- **Spawn label:** `hb-qa-review-{task_id[:8]}`
- **Status:** ✅ PASS

### 1.3 v3-config.json — Novas Seções

```json
"pmm": {
  "auto_restart": true,
  "pid_file": "polymarket-mm/paper/data/production_trading.pid",
  "restart_cooldown_minutes": 5,
  "max_restarts_per_hour": 3,
  "env_file": "polymarket-mm/.env",
  "default_config": "polymarket-mm/paper/runs/prod-003.yaml"
},
"description_quality": {
  "min_length": 200,
  "required_markers": ["## ", "Objective", "Objetivo", "Context", "Contexto", "Criteria", "Problem", "Approach", "Plano"],
  "check_statuses": ["inbox", "in_progress", "review"]
},
"failure_classification": {
  "loop_threshold": 5,
  "known_provider_errors": ["thinking.signature", "RESOURCE_EXHAUSTED", "capacity"]
}
```

**Status:** ✅ PASS (todas as 3 seções presentes com todos os campos)

### 1.4 Testes

| Métrica | Valor |
|---------|-------|
| Testes totais | **86** |
| Testes novos | **29** (em `test_absorbed_detectors.py`, 675 LOC) |
| Taxa de sucesso | **100%** |
| Tempo de execução | **0.40s** |

**Cobertura por feature:**
- PMM Health Check: 4 testes
- Failure Classification: 8 testes
- Description Quality: 5 testes
- Completion Detection: 4 testes
- QA-Review Queue: 3 testes
- Config Sections: 3 testes
- State Schema: 2 testes

**Status:** ✅ PASS

### 1.5 State File

Seção `absorbed{}` adicionada com:
- `pmm_restarts`: `[{at: ms, pid: int}, ...]`
- `alerted_description_violations`: `{task_id: {at: ms}}`
- `completion_pending_notified`: `{task_id: {at: ms}}`

**Status:** ✅ PASS

### 1.6 Dry-Run

```
Phase 1: Gateway OK
Phase 1: PMM alive (PID 2905224)           ✅
Phase 4: 0 failure(s) detected
Phase 4.5: Circuit breaker closed
Phase 4.8: Description quality OK           ✅
Phase 5: 1 in_progress, 1 active subagents
Phase 5.5: 0 qa-review, 0 orphan, 0 stale  ✅
```

**Status:** ✅ PASS — todas as fases novas executam corretamente

### Acceptance Criteria Fase 1

- [x] Phase 1 enhanced: PMM PID check + auto-restart funcional
- [x] Phase 4 enhanced: classifica erros em 6 categorias
- [x] Phase 4.8: audita descrições de tasks ativas
- [x] Phase 5.5 enhanced: detecta completion pendente → queue `qa-review`
- [x] queue-consumer.py aceita e processa `qa-review`
- [x] v3-config.json com seções `pmm`, `description_quality`, `failure_classification`
- [x] Todos os 57+ testes existentes passam
- [x] 15+ testes novos passam (29 novos)
- [x] State file expandido com `absorbed{}` section
- [x] Dry-run funcional com todas as fases novas

---

## Fase 2 — Redução de Latência

**Commit:** `eff178e` (2026-03-03 00:03)
**Status:** ✅ COMPLETA (7/7)

### Métricas Operacionais

| Métrica | SLO | Valor Real | Status |
|---------|-----|-----------|--------|
| Frequência heartbeat-v3 | */5 min | */5 min | ✅ |
| Gateway RSS | < 600 MB | **417 MB** | ✅ (margem 183 MB) |
| Circuit breaker trips (24h) | 0 | 0 | ✅ |
| Rate limit hits | < 5/dia | 1/5 usado | ✅ |
| OOM events (24h) | 0 | 0 | ✅ |
| Retry storms | 0 | 0 | ✅ |

### Mudanças Adicionais
- Gateway-post-restart-recovery.sh aprimorado com snapshot-less recovery via MC API
- PMM auto-restart com unified runner + .env loading
- Baseline de métricas capturado em `control-loop-metrics.json`

### Acceptance Criteria Fase 2

- [x] heartbeat-v3 roda a cada 5 min (`crontab -l` confirma)
- [x] 24h sem OOM
- [x] 24h sem retry storm
- [x] Gateway RSS < 600 MB (417 MB)
- [x] Circuit breaker: 0 trips
- [x] Rate limit dentro do esperado
- [x] Métricas baseline capturadas

---

## Fase 3 — Validação Dual-Run e Depreciação

**Status:** ⚠️ INCOMPLETA (2/6)

### O que foi feito
- Script `dual-run-validator.sh` criado (442 linhas)
- Dual-run executado — `state/dual-run-report.json` gerado
- `cron-inventory.md` atualizado com tabela de depreciação

### O que NÃO foi feito
- **Dual-run com GAP não resolvido:** O relatório mostra `"safe_to_deprecate": false`
- **Diretório `scripts/archive/` não criado:** Scripts depreciados não foram movidos
- **State files obsoletos não removidos:** `/tmp/.gateway-sentinel-state.json` persiste
- **Crontab com 13 entries** (target era 14)

### Dual-Run Report

```json
{
  "safe_to_deprecate": false,
  "summary": {
    "match": 0,
    "enhanced": 4,
    "gap": 1,
    "none": 0
  }
}
```

| Categoria | Resultado | Detalhe |
|-----------|-----------|---------|
| failure | ENHANCED ✅ | heartbeat-v3 detecta mais que mc-failure-detector |
| stale | ENHANCED ✅ | heartbeat-v3 detecta mais que mc-stale-task-detector |
| description | ENHANCED ✅ | heartbeat-v3 detecta mais que mc-description-watchdog |
| **gateway** | **GAP ❌** | **Old script detectou 2 eventos, heartbeat-v3 detectou 1** |
| pmm | ENHANCED ✅ | heartbeat-v3 com 26 detecções vs 4 do antigo |

### Violação do Protocolo

O plano (seção 7.5) especifica:
> *"Se durante o dual-run um detector antigo flagrar algo que heartbeat-v3 enhanced missed: PARAR depreciação, investigar, corrigir, reiniciar contagem de 3 dias."*

**Porém:** Os crons antigos foram removidos do crontab mesmo com o GAP não resolvido. O risco prático é baixo (gateway-wake-sentinel continua ativo para o caso crítico de gateway down), mas o protocolo definido no plano não foi seguido.

### Scripts Depreciados (documentados, NÃO arquivados)

| Script | Absorvido por | Data | Status |
|--------|--------------|------|--------|
| mc-failure-detector.sh | Phase 4 | 2026-03-03 | Removido do crontab |
| mc-stale-task-detector.sh | Phase 5.5 | 2026-03-03 | Removido do crontab |
| mc-description-watchdog.sh | Phase 4.8 | 2026-03-03 | Removido do crontab |
| gateway-health-sentinel.sh | Phase 1 | 2026-03-03 | Removido do crontab |
| pmm-status-updater.sh | Phase 1 | 2026-03-03 | Removido do crontab |
| escalation-recovery.sh | queue-escalation.sh | 2026-03-03 | Removido do crontab |
| cron-health-check.sh | Desnecessário | 2026-03-03 | Removido do crontab |
| smoke-test-orchestrator.sh | Eliminado | 2026-03-03 | Removido do crontab |

### Acceptance Criteria Fase 3

- [ ] 3 dias de dual-run sem divergência crítica — **FAIL (GAP gateway)**
- [x] Script de validação dual-run existe
- [ ] Scripts arquivados em `scripts/archive/` — **FAIL (dir não existe)**
- [ ] Crontab final com 14 entries — **FAIL (13 entries)**
- [ ] State files obsoletos removidos — **FAIL (1 residual)**
- [x] `cron-inventory.md` atualizado com status final

---

## Fase 4 — Cleanup, Docs e Operação Contínua

**Commit:** `6eea66e` (2026-03-03 00:07)
**Status:** ✅ COMPLETA condicionada à Fase 3 (6/7)

### Documentação Atualizada

| Arquivo | Mudanças | Status |
|---------|----------|--------|
| `AGENTS.md` | Nota "absorbed by heartbeat-v3", refs a scripts antigos limpas | ✅ |
| `HEARTBEAT.md` | Regras operacionais atualizadas, crons obsoletos removidos | ✅ |
| `TOOLS.md` | Refs a scripts antigos removidas | ✅ |
| `docs/cron-inventory.md` | Consolidado (13 ativos + 8 depreciados) | ⚠️ off by 1 vs target |
| `docs/ownership-matrix.md` | 17 tipos de evento com owners | ✅ |
| `docs/slos.md` | 7 SLOs com baseline definido | ✅ |

### Rotina de Operação Contínua
- Documentada em `HEARTBEAT.md` como "Operação Contínua (Regra Permanente)"
- Inclui: revisão semanal de SLOs, verificação de logs, regra anti-proliferação

### Acceptance Criteria Fase 4

- [x] Zero referência a scripts depreciados em AGENTS.md, HEARTBEAT.md, TOOLS.md
- [ ] `cron-inventory.md` = crontab real (14 entries) — **PARTIAL (13 entries)**
- [x] `ownership-matrix.md` validado contra crons ativos
- [x] Rotina semanal documentada
- [x] Git commit com estado final
- [x] SLOs sendo medidos (S1, S2, S7 ativos)

---

## Métricas Globais vs Plano

| Métrica | Target do Plano | Resultado Real | Status |
|---------|----------------|----------------|--------|
| Cron jobs | 23 → 14 | 23 → 13 | ⚠️ off by 1 |
| State files | 7+ → 2 | 7+ → 2-3 | ⚠️ 1 residual em /tmp |
| Latência P95 | < 6 min | ~5 min (*/5 cron) | ✅ teórico, em medição |
| heartbeat-v3 LOC | 1185 | 1758 (+573) | ⚠️ acima de 1500 (risco R1) |
| Testes | 57 → 72+ | 57 → 86 | ✅ superou (+29 novos) |
| Gateway RSS | < 600 MB | 417 MB | ✅ |
| Circuit breaker trips | 0 | 0 | ✅ |
| Alertas duplicados | ≤ 1/incidente | Em medição | 🔄 |
| Tasks órfãs/semana | 0 | Em medição | 🔄 |
| Completions sem QA > 30min | 0 | Em medição | 🔄 |

---

## Incidente Descoberto Durante Auditoria

### PMM Kill Switch Crash Loop (8+ horas)

**Descoberto:** Durante esta auditoria, identificamos que o PMM estava num crash loop por 8+ horas.

**Causa raiz:** `prod-003.yaml` não tinha `initial_balance` → código usava default de $500 → com wallet real de $222, drawdown = 55.57% → kill switch disparava instantaneamente → `pmm-status-updater.sh` reiniciava a cada 15min → loop infinito.

**Agravante:** O recovery usava `ls -t *.yaml | head -1` que pegava `p5-001.yaml` (paper trading, mercados errados) em 7 de 10 restarts.

**Fixes aplicados:**
1. `prod-003.yaml`: Adicionado `initial_balance: 222`
2. `pmm-status-updater.sh`: Config fixo `prod-003.yaml`, runner corrigido para `runner --mode live`, pgrep atualizado
3. PMM reiniciado com sucesso — PID 2905224, quoting ativamente

**Lessons registradas em:**
- `memory/lessons.md` (Luna)
- `agents/quant-strategist/memory/lessons.md` (Quant — incidente + 7 lessons cross-pollinated)

---

## Incidente Relacionado: Luna Context Overflow

**Descoberto:** Sessão Discord da Luna morreu às 23:50 UTC por Context Overflow.

```
23:50:09  timed out during compaction
23:50:09  Slow listener: DiscordMessageListener took 601.6s
23:50:49  agent end: isError=true — Context overflow: prompt too large for the model
```

**Causa:** Sessão acumulou mensagens demais, compactação falhou por timeout, prompt excedeu limite do modelo.

**Agravante:** `AGENTS.md` tem 23975 chars vs limite de 20000 — truncado em toda sessão nova.

**Status:** Gateway ativo, mas Luna não responde no Discord até reset de sessão.

---

## Issues Abertos (por prioridade)

### P1 — Gateway Detection GAP
O dual-run identificou que heartbeat-v3 perde 1 evento de gateway que o script antigo detectava. Precisa investigar e corrigir antes de considerar Fase 3 como completa.

**Ação:** Investigar `dual-run-report.json`, identificar o evento perdido, corrigir Phase 1.

### P2 — `scripts/archive/` não criado
Scripts depreciados deveriam ter sido movidos para archive com data e razão. Estão apenas documentados.

**Ação:** Criar diretório, mover scripts com metadata.

### P3 — State file residual
`/tmp/.gateway-sentinel-state.json` deveria ter sido removido.

**Ação:** Remover após confirmar que heartbeat-v3 cobre o caso.

### P4 — heartbeat-v3.py com 1758 LOC
Excede o threshold de 1500 LOC identificado como risco R1 no plano.

**Ação:** Considerar modularização — extrair fases para arquivos separados.

### P5 — Contagem de crons off by 1
Documentação diz 14, crontab real tem 13.

**Ação:** Reconciliar — identificar o cron faltante ou corrigir a documentação.

### P6 — Luna Context Overflow
Sessão Discord morta. Luna não responde.

**Ação:** Restart do gateway para criar sessão limpa. Considerar reduzir AGENTS.md (23975 → <20000 chars).

---

## Veredicto Final

O plano Control Loop v2 foi **executado com qualidade alta nas fases técnicas** (0, 1, 2) e com **documentação sólida** (Fase 4). A Fase 1 em particular é impressionante — 86 testes, todas as funcionalidades implementadas, dry-run funcional.

**O gap principal está na Fase 3:** o protocolo de dual-run/depreciação não foi seguido conforme definido. O dual-run detectou uma divergência na detecção de gateway, mas os crons foram removidos mesmo assim. O risco operacional é baixo (gateway-wake-sentinel ativo), mas o processo precisa ser fechado formalmente.

**Recomendação:** Investigar o GAP de gateway, corrigir no heartbeat-v3, e rodar um ciclo curto de validação (24-48h) para fechar a Fase 3.

---

*Relatório gerado em 2026-03-03 por auditoria via Claude Code.*
