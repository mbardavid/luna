# Relatório v2: Control Loop Autônomo para OpenClaw Agent System

**Autor:** Luna (AI Agent)  
**Data:** 2026-03-02  
**Versão:** 2.0 (incorpora revisão Codex + análise cruzada)  
**Público-alvo:** Matheus Vidinha + consultores técnicos  

---

## 1. Sumário Executivo

O sistema de agentes OpenClaw opera com 5 agentes AI, 23 cron jobs, 45 scripts bash e 133 tasks processadas via Mission Control em 15 dias. O sistema produz resultados — 801 testes, PMM live, portfolio tracking — mas tem um problema estrutural: **a cadeia detecção → ação depende de Luna estar em sessão ativa**, e quando não está, trabalho concluído por subagentes fica sem processamento indefinidamente.

Este relatório propõe **evoluir a infraestrutura existente** (heartbeat-v3, queue filesystem, scripts de ação) em vez de reescrevê-la, consolidando detectores redundantes, reduzindo latência de resposta, e fechando o gap de "completion sem QA review".

**Mudança principal da v1:** Abandonada a abordagem de "loop novo do zero". A base é o heartbeat-v3 (1185 linhas Python, 9 fases, filesystem queue, 57 testes) que já resolve ~70% do problema. Os 30% restantes são: latência alta (30min), detectores duplicados, e completions que morrem sem processamento.

---

## 2. Diagnóstico: Estado Atual

### 2.1 Arquitetura de Agentes

```
┌─────────────────────────────────────────────────────────┐
│                    OpenClaw Gateway                      │
│              (Node.js, systemd, port 18789)              │
│                   RSS: ~469 MB                           │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Luna (main)          Luan (dev)        Crypto-Sage      │
│  claude-opus-4-6      claude-opus-4-6   gemini-flash     │
│  Orquestração         Codificação       Portfolio/DeFi   │
│  QA Review            PMM dev           Balances         │
│  Decisões             Tests             On-chain ops     │
│                                                          │
│  Quant Strategist     Dispatcher                         │
│  claude-opus-4-6      gemini-flash                       │
│  Market research      Task relay                         │
│  Performance analysis (lightweight)                      │
│                                                          │
├─────────────────────────────────────────────────────────┤
│          Mission Control (Kanban, port 8000/3000)        │
│          133 tasks | 129 done | 45 scripts               │
│                                                          │
│          heartbeat-v3 (Python, 1185 LOC, 9 fases)        │
│          Filesystem queue (pending/active/done/failed)    │
│          queue-consumer.py (512 LOC)                      │
└─────────────────────────────────────────────────────────┘
```

### 2.2 O que já funciona bem (subestimado no relatório v1)

O heartbeat-v3 **não é "mais um cron"**. É um engine autônomo com:

| Fase | Função | Status |
|------|--------|--------|
| Phase 1 | Gateway health check | ✅ Funcional |
| Phase 2 | Active hours filter (8h-0h São Paulo) | ✅ Funcional |
| Phase 3 | Fetch sessions + MC tasks | ✅ Funcional |
| Phase 4 | Failure detection → queue + nudge | ✅ Funcional |
| Phase 4.5 | Circuit breaker | ✅ Funcional |
| Phase 4.6 | Resource check (skip se degradado) | ✅ Funcional |
| Phase 4.7 | Rate limit (max 3 dispatches/hora) | ✅ Funcional |
| Phase 5 | Active subagents check (max 2) | ✅ Funcional |
| Phase 5.5 | Stale dispatch detection | ✅ Funcional |
| Phase 6 | Review task dispatch | ✅ Funcional |
| Phase 7 | Inbox pull (FIFO + blocklist + deps) | ✅ Funcional |
| Phase 8 | Dedup | ✅ Funcional |
| Phase 9 | Dispatch → queue + nudge | ✅ Funcional |

Complementos com ação real (não apenas detecção):

| Script | Ação que toma |
|--------|---------------|
| mc-watchdog.sh | Retry automático, move tasks pra review |
| mc-delivery.sh | POST de resultados para Discord |
| mc-resource-monitor.sh | Session GC, gateway restart se > 80% watermark |
| gateway-wake-sentinel.sh | Restart se gateway down |
| session-gc.sh | Limpa sessões zombie |

### 2.3 O que NÃO funciona (problemas reais e persistentes)

**Problema 1 — Latência de resposta: 10-30 minutos (até infinito)**

```
Subagent completa (t=0)
  → auto-announce chega na sessão Luna
    → Se Luna está ativa: processado imediatamente ✅
    → Se Luna NÃO está ativa: fica pendente ❌
      → heartbeat-v3 detecta stale (t=10-30min)
        → Escreve queue file + nudge
          → Nudge acorda Luna? (depende de cron OpenClaw, */30)
            → Luna processa (t=30-60min do evento original)
```

**Caso real (hoje, 2026-03-02):** Luan completou o Quant Strategist Agent. Auto-announce chegou. Luna não processou. Matheus perguntou horas depois "as tasks estão sendo realizadas?" — só aí Luna verificou.

**Problema 2 — Detectores redundantes e sobrepostos**

Existem 4 mecanismos que detectam "task com problema":

| Detector | Frequência | O que detecta | Age? |
|----------|------------|---------------|------|
| heartbeat-v3 Phase 4 | */10 | Subagent falhou | Sim (queue) |
| heartbeat-v3 Phase 5.5 | */10 | Stale dispatch (sem session_key) | Sim (alerta) |
| mc-failure-detector.sh | */5 | Sessão com stopReason=error | Notifica |
| mc-stale-task-detector.sh | */10 | Session morta + task ativa | Notifica |
| mc-watchdog.sh | */15 | Task sem completion + retry | Sim (retry) |
| mc-description-watchdog.sh | */15 | Descrição ruim | Notifica |

Resultado: O mesmo incidente pode gerar 3-4 alertas de fontes diferentes, em momentos diferentes, com formatos diferentes. Ruído operacional.

**Problema 3 — State fragmentation**

7+ state files independentes em `/tmp/`:

```
/tmp/.heartbeat-check-state.json          (heartbeat-v3)
/tmp/.mc-stale-task-detector-state.json   (stale detector)
/tmp/.mc-description-watchdog-state.json  (description watchdog)
/tmp/.mc-failure-detector-state.json      (failure detector)
/tmp/.heartbeat-escalation-state.json     (escalation)
/tmp/.heartbeat-last-notify.json          (heartbeat notify)
/tmp/.gateway-sentinel-state.json         (gateway sentinel)
```

Nenhum script sabe o que os outros já alertaram. Dedup é por script, não global.

**Problema 4 — Nenhum mecanismo trata "completion pendente de QA"**

Quando Luan completa e a sessão morre com `COMPLETION_STATUS: complete`:
- heartbeat-v3 detecta "task sem session_key" → trata como stale dispatch (re-dispatch)
- mc-watchdog detecta "task sem completion" → tenta retry
- **Ninguém detecta "task COMPLETOU, precisa de QA review da Luna"**

Este é o gap mais crítico. A task está **done do ponto de vista do executor**, mas **pendente do ponto de vista do orquestrador**.

---

## 3. Análise Comparativa: Plano v1 (Luna) vs Review (Codex)

| Aspecto | Plano v1 (Luna) | Review (Codex) | Avaliação |
|---------|-----------------|----------------|-----------|
| Abordagem | Rewrite: loop novo substituindo tudo | Evolução: refinar heartbeat-v3 | **Codex está certo.** Rewrite é arriscado com 1185 LOC + 57 testes existentes |
| Redução de crons | 23 → 5 (agressivo) | Depreciação gradual com dual-run | **Codex é mais seguro.** Mas 23 crons por 9 dias é conservador demais |
| Timeline | 2-3 dias | 9 dias úteis | **Compromisso: 5-6 dias** com fases compactadas |
| Ownership matrix | Não incluído | Sim, por tipo de evento | **Excelente adição do Codex** |
| Contratos de payload | Implícito (workload.json) | Explícito (YAML/JSON + docs) | **Codex mais rigoroso** |
| Gap de completion | Identificado mas não resolvido especificamente | Não abordado | **Nenhum plano resolve.** Precisa ser adicionado |
| Latência | Resolver via loop contínuo | Não propõe redução específica | **Luna identifica melhor o problema** |
| Precisão técnica | Subestimou heartbeat-v3 | Referenciou paths que não existem | **Ambos erraram em verificação** |

### O que cada plano acerta e o outro erra

**Codex acerta, Luna errava:**
- Heartbeat-v3 já é o motor de controle, não precisa de substituto
- mc-watchdog e mc-delivery JÁ tomam ação, não são "só detecção"
- Rollback plan e dual-run são necessários
- Ownership matrix resolve sobreposição

**Luna acerta, Codex não aborda:**
- O gap fundamental é latência + completion sem QA, não organização de scripts
- Reduzir heartbeat de */30 pra */5 é a melhoria de maior impacto com menor risco
- Os detectores que criei hoje (`mc-stale-task-detector`, `mc-description-watchdog`) são redundantes com heartbeat-v3 e devem ser absorvidos, não mantidos

**Codex erra factualmente:**
- Referencia `heartbeat-v3.py` e `queue-consumer.py` na raiz do diretório — estão em `scripts/`
- Lista `mc-phase1-cron.sh` e `mc-phase3-cron.sh` como componentes ativos — são instaladores, não estão no crontab
- Superestima `mc-cron-guard` como coordenador — é um lock file simples
- Assume "estado real do repositório" mas analisa filesystem, não runtime

---

## 4. Proposta v2: Evolução Dirigida do heartbeat-v3

### 4.1 Princípio

**Não substituir. Não adicionar. Consolidar e acelerar.**

### 4.2 Três mudanças de alto impacto

#### Mudança 1: Reduzir intervalo do heartbeat OpenClaw de */30 para */5

**Impacto:** Latência máxima cai de 30min para 5min.  
**Custo:** ~$0.001/ciclo Flash para detecção. Opus acionado só quando há trabalho.  
**Risco:** Baixo — heartbeat-v3 já tem circuit breaker e rate limiting.  
**Implementação:** Uma linha no crontab.

#### Mudança 2: Absorver detectores redundantes no heartbeat-v3.py

Consolidar 3 scripts externos no engine existente:

| Script a absorver | Onde no heartbeat-v3 | Como |
|-------------------|---------------------|------|
| mc-failure-detector.sh (*/5) | Phase 4 (já faz failure detection) | Adicionar classificação de erro (LOOP, INCOMPLETE, GENERIC) |
| mc-stale-task-detector.sh (*/10) | Phase 5.5 (já faz stale detection) | Adicionar check "session_key + session morta + status ≠ done" |
| mc-description-watchdog.sh (*/15) | Nova Phase 4.8 | Varrer tasks ativas por qualidade de descrição |

**Resultado:** -3 crons, -3 state files, cobertura idêntica ou melhor.

#### Mudança 3: Novo tipo de queue item — `qa-review`

Quando heartbeat-v3 detecta "task com session morta + status ∈ {in_progress, review} + session teve COMPLETION_STATUS":

```json
{
  "type": "qa-review",
  "task_id": "7347dd0a-...",
  "task_title": "Quant Strategist Agent",
  "agent": "luan",
  "session_key": "agent:luan:subagent:0757d100-...",
  "completion_status": "complete",
  "priority": "P1",
  "action": "Luna deve fazer QA review: ler completion report, inspecionar 2+ arquivos, rodar verification checks, marcar done ou rejeitar"
}
```

Luna, ao acordar e processar a queue, encontra este item e **sabe exatamente o que fazer** — não precisa investigar, o contexto já está pré-processado.

### 4.3 Ownership Matrix (adotada do Codex)

| Tipo de Evento | Owner | Backup | Ação |
|----------------|-------|--------|------|
| Subagent falhou (error/timeout) | heartbeat-v3 Phase 4 | — | Queue `respawn` + nudge |
| Task stale (in_progress sem session) | heartbeat-v3 Phase 5.5 | — | Queue `qa-review` ou `alert` |
| Task completion pendente QA | heartbeat-v3 Phase 5.5 (enhanced) | — | Queue `qa-review` + nudge |
| Inbox com tasks elegíveis | heartbeat-v3 Phase 7-9 | — | Queue `dispatch` + nudge |
| Review tasks (Luan → Luna) | heartbeat-v3 Phase 6 | — | Queue `review-dispatch` |
| Gateway memory > threshold | mc-resource-monitor.sh | — | Session GC / safe restart |
| Gateway down | gateway-wake-sentinel.sh | — | systemctl restart |
| Task retry/completion tracking | mc-watchdog.sh | — | Auto-retry (max 2) |
| Delivery de resultados | mc-delivery.sh | — | POST Discord |
| Queue stuck > 15min | queue-escalation.sh | — | Escalation alert |
| Session cleanup | session-gc.sh | — | Limpa zombies |
| Descrição ruim | heartbeat-v3 Phase 4.8 (novo) | — | Log + alert |
| Log rotation | mc-log-rotate.sh | — | Rotaciona logs |
| Cost report | mc-cost-report.sh | — | Relatório semanal |
| Lessons sync | lessons-sync.sh | — | Sync Luna↔Luan |

### 4.4 Cron jobs: de 23 para 14

**Removidos (absorvidos pelo heartbeat-v3):**

| Cron | Absorvido por |
|------|---------------|
| mc-failure-detector.sh (*/5) | heartbeat-v3 Phase 4 |
| mc-stale-task-detector.sh (*/10) | heartbeat-v3 Phase 5.5 |
| mc-description-watchdog.sh (*/15) | heartbeat-v3 Phase 4.8 |
| heartbeat-v2.sh (legado) | heartbeat-v3 (já substitui) |
| gateway-health-sentinel.sh (*/2) | heartbeat-v3 Phase 1 (já faz) |
| cron-health-check.sh (*/30) | Simplificado: watchdog do próprio loop |
| escalation-recovery.sh (*/30) | queue-escalation.sh (já cobre) |
| pmm-status-updater.sh (*/15) | heartbeat-v3 Phase 1 (PMM check) |
| smoke-test-orchestrator.sh (*/30) | Eliminado (valor questionável em produção) |

**Mantidos (14 crons):**

| Freq | Script | Razão |
|------|--------|-------|
| */5 | heartbeat-v3.sh | Core engine (absorve 4 detectores) |
| */5 | queue-escalation.sh | Backup se nudge falha |
| */5 | mc-approvals-notify.sh | Notifica tasks pendentes aprovação |
| */15 | mc-watchdog.sh | Retry + completion tracking (ownership clara) |
| */15 | mc-delivery.sh | Entrega resultados Discord |
| */15 | mc-resource-monitor.sh | Memory/resource management |
| */1 | gateway-wake-sentinel.sh | Gateway liveness (crítico, manter isolado) |
| */30 | session-gc.sh | Cleanup de sessões zombie |
| 6h | session-compact-sentinel.sh | Compactação de sessões grandes |
| daily | session-smart-compact.py | Compactação inteligente |
| daily | lessons-sync.sh | Sync lessons entre agentes |
| daily | mc-log-rotate.sh | Rotação de logs |
| weekly | mc-cost-report.sh | Relatório semanal de custos |
| — | gateway-restart-notify.sh | Hook systemd (não é cron, ExecStartPost) |

### 4.5 State Consolidation

**De 7+ state files para 2:**

1. **heartbeat-v3 state** (`/tmp/.heartbeat-check-state.json`) — já existe, expandir com campos dos detectores absorvidos:
```json
{
  "existing_fields": "...",
  "absorbed": {
    "alerted_stale_tasks": ["task_id_1"],
    "alerted_description_violations": ["task_id_2"],
    "failure_classifications": {"session_key": "GENERIC_ERROR"},
    "last_description_check": 1709406000
  }
}
```

2. **Control loop metrics** (`workspace/state/control-loop-metrics.json`) — novo, para observabilidade:
```json
{
  "last_run": "2026-03-02T21:00:00Z",
  "runs_today": 288,
  "opus_triggers_today": 4,
  "tasks_completed_today": 3,
  "failures_detected_today": 1,
  "qa_reviews_dispatched": 2,
  "avg_latency_detect_to_action_min": 4.2,
  "cron_health": {
    "heartbeat-v3": "ok",
    "mc-watchdog": "ok",
    "gateway-sentinel": "ok"
  }
}
```

---

## 5. Plano de Implementação

### Fase 0 — Baseline e SLOs (D0, ~2h)

1. **Congelar inventário** — mapa oficial de crons ativos vs scripts existentes
2. **Definir SLOs:**
   - Latência detecção→ação: P95 < 6 min
   - Alertas duplicados por incidente: ≤ 1
   - Tasks órfãs por semana: 0
   - Completions sem QA > 30min: 0
3. **Documentar ownership matrix** (seção 4.3 deste doc → `docs/ownership-matrix.md`)

### Fase 1 — Absorção de detectores (D1-D2, ~1 task Luan)

1. Adicionar ao heartbeat-v3.py:
   - Phase 4 enhanced: classificação de erro (absorve mc-failure-detector)
   - Phase 5.5 enhanced: "session morta + task ativa" check (absorve mc-stale-task-detector)
   - Phase 4.8 nova: description quality check (absorve mc-description-watchdog)
   - Novo queue type: `qa-review`
2. Expandir state file com campos absorvidos
3. Manter 57+ testes passando, adicionar ~15 novos
4. **Dual-run:** Manter crons antigos rodando em paralelo por 3 dias

### Fase 2 — Redução de latência (D2, ~1h, config only)

1. Alterar crontab: heartbeat-v3 de */10 para */5
2. Alterar cron OpenClaw: heartbeat de */30 para */5
3. Validar: circuit breaker + rate limiting comportam a frequência dobrada
4. Monitorar por 24h: sem OOM, sem retry storm, sem alerta duplicado

### Fase 3 — Validação e depreciação (D3-D5)

1. Comparar outputs: heartbeat-v3 enhanced vs detectores antigos
   - Divergência = bug no enhanced, corrigir antes de depreciar
   - 3 dias sem divergência crítica = critério para desativar
2. Desativar crons absorvidos um por um:
   - D3: mc-failure-detector (mais simples, já coberto por Phase 4)
   - D4: mc-stale-task-detector (coberto por Phase 5.5 enhanced)
   - D5: mc-description-watchdog, gateway-health-sentinel, cron-health-check
3. Arquivar scripts em `scripts/archive/` (não deletar, recuperação em 90 dias)

### Fase 4 — Limpeza e docs (D6)

1. Remover state files obsoletos de `/tmp/`
2. Atualizar AGENTS.md, HEARTBEAT.md, TOOLS.md
3. Atualizar ownership matrix com estado final
4. Criar `docs/cron-inventory.md` — fonte de verdade para crons ativos

### Fase 5 — Operação contínua

1. Review semanal de métricas (control-loop-metrics.json)
2. Teste mensal de falha de nudge (simular "Luna não responde")
3. Revisão de ownership matrix quando novo detector for necessário
4. **Regra:** Antes de criar novo script de detecção → verificar se heartbeat-v3 já cobre o caso

---

## 6. Análise de Riscos

### 6.1 Riscos da Implementação

| Risco | Prob. | Impacto | Mitigação |
|-------|-------|---------|-----------|
| heartbeat-v3 fica complexo demais (>1500 LOC) | Médio | Médio | Modularizar: extrair fases pra arquivos separados |
| Frequência */5 causa load no gateway | Baixo | Alto | Circuit breaker já existe; monitorar RSS 24h |
| Regressão: caso coberto por cron antigo perdido | Médio | Alto | Dual-run 3 dias com comparação de outputs |
| Queue `qa-review` gera loop (Luna re-dispatcha mesma task) | Baixo | Médio | Dedup por task_id + cooldown de 30min |
| State file corrupto bloqueia detecção | Baixo | Alto | State reset automático se parse falha (já existe) |

### 6.2 Riscos de NÃO Implementar

| Risco | Prob. | Impacto | Evidência |
|-------|-------|---------|-----------|
| Completions ficam sem QA indefinidamente | **Já aconteceu** | Alto | Incidente de 2026-03-02 |
| Cada novo bug → novo script → mais crons | Certo | Médio | 7 scripts em 15 dias |
| Alertas duplicados confundem Matheus | Já acontece | Baixo | 3-4 alertas por incidente |
| Matheus precisa supervisionar rotina | **Já acontece** | Alto | "As tasks estão ativas?" |

---

## 7. Métricas de Sucesso

| Métrica | Antes (23 crons) | Alvo (14 crons) | Como medir |
|---------|-------------------|------------------|------------|
| Latência detecção→ação | 15-∞ min | P95 < 6 min | control-loop-metrics.json |
| Cron jobs | 23 | 14 | crontab -l |
| State files | 7+ em /tmp | 2 | ls /tmp/.heartbeat* + workspace/state/ |
| Alertas duplicados/incidente | 3-4 | ≤ 1 | Contagem Discord |
| Tasks órfãs/semana | 2-3 | 0 | MC query |
| Completions sem QA > 30min | Ilimitado | 0 | heartbeat-v3 metrics |
| Scripts bash | 45 | ~36 (9 arquivados) | ls scripts/ |

---

## 8. Perguntas para Decisão

1. **Frequência do heartbeat OpenClaw:** */5 é aceitável em custo? (estimativa: ~$0.30/dia em Flash para detecção, Opus adicional ~$1-3/dia quando há trabalho)

2. **PMM auto-restart:** Se prod-003 morrer, o heartbeat deveria reiniciar automaticamente ou apenas alertar Matheus?

3. **State persistence:** JSON em `workspace/state/` é suficiente, ou vale Supabase para auditoria histórica?

4. **Systemd service:** O heartbeat-v3 deveria virar um serviço systemd com restart automático em vez de cron? (mais robusto, mas mais complexo de manter)

5. **Ownership matrix enforcement:** Implementar como documentação apenas, ou como código (script que valida "quem criou este alerta tinha ownership")?

---

## Apêndice A — Cronologia de Incidentes

| Data | Incidente | Detecção | Gap | Script criado |
|------|-----------|----------|-----|---------------|
| Feb 26 | Heartbeat AI OOM → gateway crash chain | Manual (Matheus) | Horas | heartbeat-v2 (bash) |
| Feb 27 | Subagent timeout silencioso | Manual (Matheus) | Horas | mc-failure-detector |
| Feb 28 | Task sem completion, watchdog auto-complete errado | Manual (log review) | Dias | Safeguard no watchdog |
| Mar 01 | Gateway memory pressure por sessões acumuladas | mc-resource-monitor | 15min | (já existia) |
| Mar 02 | 3 tasks "ativas" sem ninguém trabalhando | Manual (Matheus) | Horas | mc-stale-task-detector |
| Mar 02 | Descrição genérica no MC | Manual (Matheus) | Horas | mc-description-watchdog |

**Pattern:** 5 de 6 incidentes foram detectados por Matheus, não pelo sistema. Os scripts criados em resposta são reactivos — detectam o problema DEPOIS que já aconteceu, não previnem.

## Apêndice B — heartbeat-v3 Capabilities (verificação runtime)

```
Arquivo: heartbeat-v3/scripts/heartbeat-v3.py
Linhas: 1185
Testes: 57 (heartbeat-v3/tests/)

Fases confirmadas no código:
  Phase 1:   Gateway health (curl localhost:18789)
  Phase 2:   Active hours (8h-0h São Paulo via zoneinfo)
  Phase 3:   Fetch sessions (gateway API) + MC tasks (MC API)
  Phase 4:   Failure detection → classify → queue file + Discord + nudge
  Phase 4.5: Circuit breaker (3 failures → open → cooldown 30min)
  Phase 4.6: Resource check (skip dispatch se gateway degradado)
  Phase 4.7: Rate limit (max 3 dispatches/hora, 5min entre dispatches)
  Phase 5:   Subagent concurrency (max 2 ativos)
  Phase 5.5: Stale dispatch (in_progress sem session_key > 15min)
  Phase 6:   Review tasks → dispatch pra Luna (QA review)
  Phase 7:   Inbox pull (FIFO + blocklist + dependency chain)
  Phase 8:   Dedup
  Phase 9:   Dispatch → queue file + system-event nudge

Queue types existentes: dispatch, respawn
Queue types propostos (v2): qa-review

State: /tmp/.heartbeat-check-state.json
Lock: /tmp/.heartbeat-check.lock (fcntl, previne execução simultânea)

System-event nudge: openclaw cron add --at +10s --system-event
  → Injeta mensagem na sessão Luna sem criar sessão nova
  → Limitação: se Luna não está em sessão, nudge fica pendente
```

## Apêndice C — Diferenças entre v1 e v2 deste relatório

| Aspecto | v1 | v2 |
|---------|----|----|
| Abordagem | Rewrite (loop novo) | Evolução (heartbeat-v3 enhanced) |
| Crons target | 23 → 5 | 23 → 14 |
| Timeline | 2-3 dias | 6 dias (com dual-run) |
| heartbeat-v3 | Tratado como "mais um cron" | Reconhecido como motor principal |
| Ownership matrix | Ausente | Incluída (adotada do Codex) |
| Dual-run | Não previsto | 3 dias de validação (adotado do Codex) |
| Contratos de payload | Implícito | Explícito (queue item types) |
| Gap de completion | Identificado | Solução proposta (queue type qa-review) |
| Rollback plan | Ausente | Incluído (scripts arquivados 90 dias) |

---

*Relatório v2 gerado por Luna, incorporando feedback do Codex e verificação runtime dos componentes existentes. Disponível em `docs/report-control-loop-autonomo-v2.md`.*
