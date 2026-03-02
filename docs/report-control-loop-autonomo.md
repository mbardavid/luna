# Relatório: Control Loop Autônomo para OpenClaw Agent System

**Autor:** Luna (AI Agent)  
**Data:** 2026-03-02  
**Versão:** 1.0  
**Público-alvo:** Matheus Vidinha + consultores técnicos  

---

## 1. Sumário Executivo

O sistema de agentes OpenClaw evoluiu organicamente de um assistente pessoal para uma plataforma multi-agente com orquestração A2A (Agent-to-Agent), trading automatizado, e infra de monitoramento. Ao longo de 15 dias de operação, acumulou **23 cron jobs**, **45 scripts bash**, e **133 tasks processadas** via Mission Control.

O sistema funciona, mas tem um problema estrutural: **é reativo, não autônomo**. Cada falha identificada gerou um novo script de detecção, criando uma arquitetura de "alarmes empilhados" que detecta problemas mas não os resolve sem intervenção humana. Este relatório propõe consolidar essa infra fragmentada em um **Control Loop unificado** que fecha o gap entre detecção e ação.

---

## 2. Diagnóstico: Estado Atual do Sistema

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
│                   Mission Control                        │
│            (Kanban API, port 8000/3000)                   │
│            133 tasks | 129 done | 45 scripts             │
└─────────────────────────────────────────────────────────┘
```

### 2.2 Fluxo Atual de Trabalho (A2A Pipeline)

```
1. Luna recebe task (humano ou inbox MC)
2. Luna escreve spec completa via mc-spawn.sh
3. mc-spawn.sh valida qualidade → cria MC task → gera spawn params
4. Luna chama sessions_spawn(Luan) → Two-Phase (plan → review → implement)
5. Luan auto-announces completion
6. Luna faz QA Review (inspeciona código, roda tests)
7. Luna marca done no MC
8. Luna checa inbox → spawna próxima task
```

**Este pipeline funciona bem quando Luna está ativa.** O problema é o passo 5→6: quando o auto-announce chega e Luna não está em sessão, o resultado morre silenciosamente.

### 2.3 Cron Jobs — Inventário Completo

| Freq | Script | Categoria | Função |
|------|--------|-----------|--------|
| */1 | gateway-wake-sentinel.sh | Gateway | Verifica se gateway está respondendo |
| */2 | gateway-health-sentinel.sh | Gateway | Health check mais profundo |
| */5 | mc-failure-detector.sh | MC/Tasks | Detecta subagents com sessão falhada |
| */5 | queue-escalation.sh | Heartbeat | Escala tasks em queue > 15min |
| */5 | mc-approvals-notify.sh | MC/Tasks | Notifica tasks pendentes de aprovação |
| */10 | heartbeat-v3.sh | Heartbeat | Loop principal de detecção |
| */10 | mc-stale-task-detector.sh | MC/Tasks | Detecta tasks órfãs/completions pendentes |
| */15 | mc-watchdog.sh | MC/Tasks | Detecta tasks sem completion + auto-retry |
| */15 | mc-delivery.sh | MC/Tasks | Entrega resultados de tasks done |
| */15 | mc-resource-monitor.sh | Gateway | Monitora memory, auto-restart se > 80% |
| */15 | mc-description-watchdog.sh | MC/Tasks | Audita qualidade de descrições |
| */15 | pmm-status-updater.sh | PMM | Atualiza status do bot no MC |
| */30 | session-gc.sh | Sessions | Limpa sessões mortas |
| */30 | escalation-recovery.sh | Heartbeat | Recupera escalations travadas |
| */30 | smoke-test-orchestrator.sh | PMM | Smoke test do pipeline |
| */30 | cron-health-check.sh | Manutenção | Verifica saúde dos próprios crons |
| 6h | session-compact-sentinel.sh | Sessions | Compacta sessões grandes |
| daily | session-smart-compact.py | Sessions | Compactação inteligente |
| daily | lessons-sync.sh | Manutenção | Sincroniza lessons Luna↔Luan |
| daily | mc-log-rotate.sh | Manutenção | Rotaciona logs |
| weekly | mc-cost-report.sh | Manutenção | Relatório de custos |

**Total: 23 cron jobs em 6 categorias.**

### 2.4 O Pattern de Falha: Alarmes Empilhados

Cada incidente nos últimos 15 dias gerou um novo script de detecção:

| Data | Incidente | Script Criado | Resolvia? |
|------|-----------|---------------|-----------|
| Feb 26 | Gateway OOM por heartbeat AI | heartbeat-v2.sh (bash puro) | ✅ Preveniu |
| Feb 26 | Gateway não reinicia sozinho | gateway-wake-sentinel.sh | ✅ Detecta |
| Feb 27 | Subagent falha silenciosa | mc-failure-detector.sh | 🟡 Detecta, não age |
| Feb 28 | Task sem completion | mc-watchdog.sh | 🟡 Auto-retry, mas perigoso |
| Mar 01 | Queue travada | queue-escalation.sh | 🟡 Escala, não resolve |
| Mar 02 | Task órfã/completion pendente | mc-stale-task-detector.sh | 🟡 Detecta, não age |
| Mar 02 | Descrição ruim no MC | mc-description-watchdog.sh | 🟡 Detecta, não age |

**Pattern:** Detecção → Marker file → Heartbeat lê → Notifica Luna → Luna age (talvez).

**O gap fundamental:** 6 dos 7 scripts detectam mas não agem. A ação depende de Luna estar ativa, o que depende de um trigger externo.

---

## 3. Análise do Problema

### 3.1 Latência de Resposta

```
Evento ocorre (t=0)
  → Cron detecta (t=5-15 min, depende da frequência)
    → Marker file escrito (t+0s)
      → Heartbeat lê marker (t=10-30 min, depende do ciclo)
        → Heartbeat notifica Discord (t+0s)
          → Luna é acionada pela mensagem? (t=??? depende de trigger)
            → Luna age (t+2-5 min)
```

**Melhor caso:** ~15 minutos entre evento e ação.  
**Pior caso:** Infinito (se nenhum trigger acorda Luna — exatamente o que aconteceu hoje).

### 3.2 Complexidade vs. Confiabilidade

- **23 cron jobs** competem por resources e podem interferir entre si
- Cada script precisa de `source ~/.bashrc` (bug sistêmico: bashrc exits early para non-interactive shells)
- Cada script mantém seu próprio state file em `/tmp/` (não sobrevive reboot)
- Nenhum script sabe o que os outros estão fazendo (sem coordenação)
- Adicionar um novo detector é fácil; remover um existente é arriscado (medo de perder cobertura)

### 3.3 Custo Operacional

- **Token burn:** Heartbeat v3 no Flash é barato (~$0.001/ciclo), mas quando escala pra Opus consome $0.05-0.50/turno
- **CPU:** 23 crons a cada 1-30 min geram ~100 processos bash/hora
- **Memory:** Gateway em 469MB (watermark 900MB) — cada sessão de cron que sobrevive consome memória
- **Cognitive load:** Matheus precisa entender 23 crons + 45 scripts para diagnosticar problemas

### 3.4 Failure Modes Não Cobertos

Mesmo com 23 crons, estes cenários passam despercebidos:

1. **Subagent completa com sucesso mas Luna não processa** — Detectado hoje, fix implementado (stale-task-detector), mas o fix só detecta, não age
2. **Múltiplos crons tentam agir no mesmo recurso** — Sem locking, race conditions possíveis
3. **Cron falha silenciosamente** — Se um cron não roda (PATH errado, env missing), ninguém sabe até o cron-health-check (*/30) detectar
4. **Cascata de alertas** — Quando gateway reinicia, 5+ crons detectam anomalias simultâneas e geram alertas duplicados
5. **State files inconsistentes** — Cada script mantém estado próprio em /tmp, sem validação cruzada

---

## 4. Proposta: Control Loop Unificado

### 4.1 Visão

Substituir a arquitetura de "23 alarmes independentes" por um **único processo de controle** que consolida detecção + decisão + ação em um loop determinístico.

### 4.2 Arquitetura Proposta (Opção D — Hybrid)

```
┌──────────────────────────────────────────────────────────────┐
│                    CONTROL LOOP (bash)                        │
│                   Processo único, loop 60s                    │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ Phase 1  │→ │ Phase 2  │→ │ Phase 3  │→ │ Phase 4  │    │
│  │ COLLECT  │  │ ANALYZE  │  │ DECIDE   │  │ OUTPUT   │    │
│  │          │  │          │  │          │  │          │    │
│  │• MC API  │  │• Stale?  │  │• Act now │  │• JSON    │    │
│  │• Sessions│  │• Orphan? │  │• Notify? │  │  workload│    │
│  │• Gateway │  │• Failed? │  │• Wait?   │  │• Metrics │    │
│  │• PMM PID │  │• Memory? │  │• Ignore? │  │• Alerts  │    │
│  │• Supabase│  │• Inbox?  │  │          │  │          │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
│                                                              │
│  Se workload.json existe → aciona Opus                       │
└──────────────────────────────────────────────────────────────┘
         │                                    ▲
         ▼                                    │
┌──────────────────────┐          ┌───────────────────────┐
│   workload.json      │          │   Luna (Opus)          │
│                      │──────────│                        │
│ {                    │  trigger │ • Processa workload    │
│   "completions": [], │          │ • Spawna tasks         │
│   "failures": [],    │          │ • Faz QA review        │
│   "inbox": [],       │          │ • Marca done/retry     │
│   "alerts": [],      │          │ • Notifica humano      │
│   "pmm_health": {},  │          │   (só quando precisa)  │
│   "gateway": {}      │          │                        │
│ }                    │          └───────────────────────┘
└──────────────────────┘
```

### 4.3 Fases do Control Loop

**Phase 1 — COLLECT (puro I/O, sem lógica)**
```bash
# Uma única chamada por recurso, resultados em variáveis
MC_TASKS=$(curl -s MC_API/tasks)
SESSIONS=$(curl -s gateway/sessions)
GATEWAY_MEM=$(ps rss gateway)
PMM_PID=$(cat production_trading.pid)
PMM_ALIVE=$(kill -0 $PMM_PID)
```

**Phase 2 — ANALYZE (lógica determinística)**
```bash
# Python script que recebe TODOS os dados e produz análise unificada
python3 control-loop-analyzer.py \
  --tasks "$MC_TASKS" \
  --sessions "$SESSIONS" \
  --gateway-mem "$GATEWAY_MEM" \
  --pmm-status "$PMM_ALIVE" \
  --state control-loop-state.json \
  > analysis.json
```

O analyzer cruza dados que hoje estão em scripts separados:
- Task tem session_key + session morta → completion pendente
- Task ativa sem session_key → órfã
- Task falhada com retry < max → retriable
- Gateway memory > threshold → precisa GC
- PMM PID morto → precisa restart
- Inbox > 0 + sem subagents ativos → precisa dispatch

**Phase 3 — DECIDE (regras de prioridade)**

| Prioridade | Condição | Ação |
|------------|----------|------|
| P0 CRÍTICO | Gateway down | Restart via script seguro |
| P0 CRÍTICO | PMM morto | Alertar humano (não auto-restart sem aprovação) |
| P1 URGENTE | Completion pendente de QA | Acionar Opus |
| P1 URGENTE | Subagent falhou | Acionar Opus (investigar + retry) |
| P2 NORMAL | Inbox com tasks | Acionar Opus (dispatch) |
| P2 NORMAL | Memory > 70% | Session GC |
| P3 LOW | Descrição ruim no MC | Log + alert próximo ciclo |
| P4 INFO | Tudo ok | Noop |

**Phase 4 — OUTPUT**

Duas saídas possíveis:
1. **workload.json** — Se P0-P2 detectado, escreve JSON estruturado com exatamente o que Opus precisa fazer. Um cron de */5 verifica se o arquivo existe e aciona Opus.
2. **metrics.json** — Sempre atualizado. Dashboard consumível por MC ou Supabase.

### 4.4 O que é eliminado

| Cron Atual | Substituído por | Razão |
|------------|----------------|-------|
| mc-failure-detector.sh | Phase 2 (analyze) | Cruza sessions + tasks |
| mc-stale-task-detector.sh | Phase 2 (analyze) | Mesma análise |
| mc-watchdog.sh | Phase 2 (analyze) | Mesma análise |
| mc-description-watchdog.sh | Phase 2 (analyze) | Incluso na varredura |
| mc-delivery.sh | Phase 2+3 (pode executar direto) | Simple POST |
| mc-approvals-notify.sh | Phase 2 (analyze) | Incluso na varredura |
| queue-escalation.sh | Eliminado | Não precisa de queue files se loop é direto |
| pmm-status-updater.sh | Phase 1 (collect) | Leitura de PID |
| heartbeat-v3.sh | Substituído pelo loop | O loop É o heartbeat |
| escalation-recovery.sh | Eliminado | Não precisa de escalation se loop age direto |
| gateway-wake-sentinel.sh | Phase 1 (collect) | Health check incluso |
| gateway-health-sentinel.sh | Phase 1 (collect) | Redundante com o anterior |
| cron-health-check.sh | Eliminado | Só 1 cron pra monitorar |

**De 23 crons para ~5:**
1. `*/1` — Control Loop principal
2. `*/5` — Opus trigger (verifica workload.json)
3. `0 6` — Session compaction (diário)
4. `0 3` — Log rotation (diário)
5. `0 9 * * 1` — Cost report (semanal)

### 4.5 State Management

**Hoje:** 7+ state files em `/tmp/` (não sobrevive reboot, sem coordenação):
```
/tmp/.heartbeat-last-notify.json
/tmp/.mc-stale-task-detector-state.json
/tmp/.mc-description-watchdog-state.json
/tmp/.mc-failure-detector-state.json
/tmp/.heartbeat-escalation-state.json
/tmp/.gateway-sentinel-state.json
/tmp/.pmm-status-state.json
```

**Proposto:** Um único arquivo de estado persistente:
```json
// workspace/state/control-loop-state.json
{
  "last_run": "2026-03-02T21:00:00Z",
  "last_opus_trigger": "2026-03-02T20:45:00Z",
  "alerted_task_ids": ["5fb3bb4e", "017469bb"],
  "failed_sessions": ["agent:luan:subagent:abc123"],
  "gateway": {
    "last_restart": "2026-03-02T18:30:00Z",
    "restart_count_1h": 0,
    "memory_mb": 469
  },
  "pmm": {
    "pid": 2735539,
    "alive": true,
    "last_check": "2026-03-02T21:00:00Z"
  },
  "metrics": {
    "loops_total": 1247,
    "opus_triggers_today": 3,
    "tasks_completed_today": 2,
    "alerts_sent_today": 1
  }
}
```

---

## 5. Análise de Riscos

### 5.1 Riscos da Implementação

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Loop crashea e ninguém detecta | Médio | Alto | Keepalive via systemd + watchdog externo mínimo |
| Regressão: caso coberto por cron antigo não coberto pelo loop | Médio | Médio | Migração incremental com dual-run period |
| Loop muito complexo, vira o mesmo problema | Baixo | Alto | Manter < 500 linhas, lógica em Python testável |
| Opus trigger storm (loop detecta work a cada 60s) | Médio | Médio | Cooldown de 5min entre triggers, dedup |

### 5.2 Riscos de NÃO Implementar

| Risco | Probabilidade | Impacto |
|-------|--------------|---------|
| Tasks completadas ficam sem QA indefinidamente | **Já aconteceu** | Alto — trabalho desperdiçado |
| Novo incidente → novo script → mais complexidade | Certo | Médio — entropia crescente |
| Matheus precisa supervisionar manualmente | **Já acontece** | Alto — sistema deveria ser autônomo |
| Cron interference (race conditions) | Médio | Médio — resultados imprevisíveis |

---

## 6. Plano de Implementação

### Fase 1 — Core Loop (1-2 tasks Luan, ~2h)
- Criar `scripts/control-loop.sh` com Phase 1-4
- Criar `scripts/control-loop-analyzer.py` (lógica de análise)
- State file unificado em `state/control-loop-state.json`
- Testar com dry-run (não aciona Opus, só loga)

### Fase 2 — Opus Trigger (1 task, ~1h)
- Criar `scripts/control-loop-trigger.sh` — lê workload.json, manda mensagem pro canal Luna
- Cron */5 para o trigger
- Testar end-to-end: loop detecta → trigger aciona → Luna age

### Fase 3 — Migração Gradual (2-3 dias)
- Dual-run: loop novo + crons antigos em paralelo
- Comparar outputs (loop deveria detectar tudo que os crons detectam)
- Desativar crons um por um conforme validação

### Fase 4 — Cleanup
- Remover crons substituídos
- Arquivar scripts obsoletos em `scripts/archive/`
- Documentar nova arquitetura

---

## 7. Métricas de Sucesso

| Métrica | Antes | Alvo |
|---------|-------|------|
| Latência detecção→ação | 15-∞ min | < 6 min |
| Cron jobs | 23 | 5 |
| Scripts bash | 45 | ~30 (15 arquivados) |
| State files | 7+ em /tmp | 1 persistente |
| Tasks órfãs (por semana) | 2-3 | 0 |
| Intervenção humana necessária para dispatch | Frequente | Raro (só decisões) |

---

## 8. Perguntas para Discussão

1. **Frequência do loop:** 60s é agressivo demais? 120s seria suficiente?
2. **Escopo do Opus trigger:** Opus deveria processar TODO o workload de uma vez, ou um item por trigger?
3. **PMM auto-restart:** Se prod-003 morrer, o loop deveria reiniciar automaticamente ou alertar?
4. **Persistência do state:** JSON file é suficiente ou vale usar SQLite/Supabase para auditoria?
5. **Systemd vs cron:** O loop deveria ser um serviço systemd próprio (restartável) em vez de cron?
6. **Observabilidade:** Vale exportar métricas do loop para Supabase/dashboard?

---

## Apêndice A — Glossário

- **MC:** Mission Control — dashboard Kanban para tracking de tasks
- **A2A:** Agent-to-Agent — delegação de trabalho entre agentes AI
- **Opus:** Claude claude-opus-4-6-thinking — modelo principal (caro, capaz)
- **Flash:** Gemini 3 Flash — modelo leve para detecção (barato, limitado)
- **Control Loop:** Processo contínuo que detecta, decide e age
- **Heartbeat:** Polling periódico para detectar trabalho pendente
- **Stale task:** Task em estado ativo mas sem progresso real

## Apêndice B — Incidentes que Motivaram Este Relatório

1. **2026-02-26:** Heartbeat AI (Flash) entrou em retry storm de notificações → OOM → gateway crash chain (5 restarts). Fix: heartbeat-v2 em bash puro.
2. **2026-02-27:** Subagent do Luan falhou por timeout. Luna não investigou nem re-spawnou. Matheus teve que perguntar "por que falhou?". Fix: regra em AGENTS.md + failure-detector.
3. **2026-03-02 (hoje):** 3 tasks em in_progress/review, nenhuma sendo trabalhada de fato. Quant Agent completou mas Luna não processou. Market Rotation nunca foi spawnada. Matheus teve que perguntar "as tasks estão ativas?". Fix: stale-task-detector + este relatório.

---

*Relatório gerado por Luna. Disponível em `docs/report-control-loop-autonomo.md`.*
