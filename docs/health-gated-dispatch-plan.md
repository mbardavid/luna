# Health-Gated Dispatch Plan (Heartbeat-v3) — PHASE: planning

**MC Task ID:** `fe69b371-09f7-48ee-9c56-c3e6e72dfbd5`
**Date:** 2026-03-04
**Status:** Plan-only (no implementation)

## 1) Objetivo
Substituir o rate-limit cego atual do heartbeat-v3 por um **despacho condicionado à saúde**:

- Cada `N` dispatches executa um **checkpoint CTO-ops**.
- Só libera mais dispatches quando as métricas de saúde estão acima do limiar aceitável.
- Em falha crítica, só executar recovery via `scripts/gateway-safe-restart.sh` com limitação de frequência (≤3/h).
- Não alterar `openclaw.json`.

---

## 2) Arquitetura alvo (alto nível)

### 2.1 Estado + orçamento
- Introduzir estado persistente de dispatch em **estado local idempotente** (arquivo JSON em `/tmp` ou `~/.cache/openclaw/heartbeat-v3/state.json`).
- Controlar dois fluxos com **orçamentos separados**:
  - `inbox_dispatch` (fluxo normal)
  - `review_wake` (fluxo de reprocessamento de `review`/`needs_approval`)
- Cada fluxo tem:
  - `dispatch_tokens_total`
  - `dispatches_since_checkpoint`
  - `checkpoint_status`
  - `wip_by_type`
  - `next_retry_at`

### 2.2 Checkpoint “gate”
- A cada `checkpoint_interval` dispatches de cada fluxo, heartbeat-v3 entra em modo **gate lock** e:
  1. roda coletor CTO-ops; se OK libera o token pool e continua;
  2. se degradado aplica remediação leve + pausa curta + retry;
  3. se crítico aciona restart controlado (rate-limited) e bloqueia novos dispatches até cooldown/retomada.

### 2.3 Integração com crons existentes
- **Não substituir** `resource-monitor`, `watchdog`, `session-gc`.
- Heartbeat-v3 passa a **ler sinais/artefatos** desses crons e usa para:
  - confirmar estado de degradação persistente;
  - evitar decisões isoladas (sourcing truth).
- Crons devem publicar estado de forma idempotente para leitura (`checkpoint_file`, last-seen timestamps, flags).

---

## 3) Policy — dispatch tokens e WIP caps

### 3.1 Sugestão de parâmetros iniciais (ajustáveis por env/CFG)
- `N` (checkpoint interval): **32 dispatches** (config inicial)
- `max_dispatch_tokens_per_minute`: **45** (hard gate de vazão)
- `min_health_probe_interval_s`: **20s**

### 3.2 Orçamento por fluxo

| Fluxo | Nome do budget | Base | Notas |
|---|---|---|---|
| normal | `inbox_dispatch` | 200 tokens/h | Governar fila normal; mais conservador |
| especial | `review_wake` | 80 tokens/h | Deve priorizar `review`/`needs_approval` com janela menor |

- `review_wake` tem prioridade no consumo quando houver incerteza de saúde (ex.: task orphan/rollback).

### 3.3 WIP cap por tipo (dev/research/ops)

- Definir contadores ativos por tipo (janela deslizante em estado persistente):
  - `dev`: **6** WIP máximo
  - `research`: **4** WIP máximo
  - `ops`: **2** WIP máximo (controle de risco)
- Sem preempção automática de WIP no mesmo tipo; manter fila com backoff exponencial e anti-retry-storm.

---

## 4) CTO-ops checkpoint (requisito de saúde)

Cada checkpoint deve coletar pelo menos:

1. **Gateway RSS/heap proxy** (container/processos principais)
2. **#sessions ativas** (gateway)
3. **RPC latency p95 e falha** (probe curto 1s/5s)
4. **Queue active stuck** (tarefas em estado ativo há > `N` min)
5. **MC orphans** (tarefas em `in_progress` sem `session_key`/sem sessão viva)
6. **Disk** (`/` e dados da sessão)

### 4.1 Classes de saúde

| Métrica | OK | Degraded | Critical |
|---|---|---|---|
| Gateway RSS (MB) | ≤ `2800` | `2800..4200` | `>4200` por 2 leituras |
| Heap growth (`/proc/<pid>/status` ou proxy equivalente) | < `12%` em 5 min | `12%..25%` | `>25%`/5min ou GC falha |
| #sessions | `<= 55` | `56..90` | `>90` ou crescimento >`+20%` em 15 min |
| RPC p95 (ms) | `< 900` | `900..1800` | `>1800` ou p95 fail rate `>2%` |
| queue stuck (contagem) | `0..4` | `5..12` | `>12` 
| MC orphans | `0..2` | `3..6` | `>6` ou sem reconciliação >10m |
| Disco usado | `< 75%` | `75..85%` | `>85%` 

### 4.2 Ações por estado
- **Healthy**: `checkpoint_status=ok`, `dispatch_gate=open`.
- **Degraded**:
  - executar remediação leve ordenada: 
    1. `session-gc`/limpeza de sessão leve;
    2. `gc`/compact/flush de estado local (sem restart);
    3. `pause_sec=30..120` + recheck imediato.
  - se duas leituras degradadas seguidas: reduzir burst e congelar dispatch (`cooldown` curto, ex. 3–5 min).
- **Critical**:
  - registrar evento de criticidade e bloquear `inbox_dispatch` e `review_wake` além do mínimo operacional;
  - disparar **`bash scripts/gateway-safe-restart.sh`** por comando único;
  - aplicar **rate limit de restart: 3 por hora** por mecanismo de leaky bucket no estado persistente;
  - só liberar novo dispatch após restart OK + 2 health checks consecutivos OK.

---

## 5) Implementação no heartbeat-v3 (sem código nesta fase)

### 5.1 Fases recomendadas

1. **Phase 0 — Medição**
   - Ler estado atual, validar lock/idempotência, carregar orçamento por fluxo.
   - Abort se heartbeat já em execução (flock/sentinel).

2. **Phase 1 — Seleção de candidato**
   - Selecionar tarefa elegível por fila e anti-fallback.
   - Conferir WIP por tipo e por fluxo antes de gastar token.

3. **Phase 2 — Pré-dispatch (token debit)**
   - Debitar 1 token em memória persistente (transação idempotente por `candidate_id + cycle_id`).
   - Se contador de checkpoint alcança N, entrar fase de checkpoint.

4. **Phase 3 — Dispatch**
   - Executar pipeline de dispatch normal.
   - Atualizar estado: `last_dispatch_at`, `inflight_id`, `resume_token`.

5. **Phase 4 — Pós-dispatch/Reconciliação**
   - Validar criação de sessão/cron/estado.
   - Em falha parcial: manter `dispatch_tokens` não consumido, `retry_count` incremental e voltar para inbox com razão.

6. **Phase 5 — Checkpoint obrigatório**
   - Rodar CTO probes.
   - Aplicar matriz OK/degraded/critical.
   - Se healthy: liberar tokens remanescentes e seguir.
   - Se degraded/critical: executar mitigação e atualizar estado.

### 5.2 Estado persistente (idempotente)
- Chave por janela + fluxo.
- Atualizações atômicas (`tempfile + mv`).
- Campos mínimos:
  - `version`, `cycle_id`, `flow`, `tokens_left`, `dispatches_since_checkpoint`,
    `checkpoint_status`, `cooldown_until`, `restart_count_1h`, `last_event_id`, `last_checkpoint_metrics`.
- Reexecução do heartbeat deve continuar estado sem duplicar dispatch.

### 5.3 Anti-loop
- Limite de loop por ciclo:
  - no máximo 1 ação de restart por ciclo;
  - no máximo 1 checkpoint de health por execução de heartbeat.
- Gate anti-loop em memória + estado: se `last_action=critical` e sem progresso de saúde por >`T`, marcar `blocked_until` e sair com alerta.

---

## 6) Checklist de mudanças (paths)

### A. Configuração (novo)
- `config/heartbeat-v3-health-gated.yaml` (novo)
  - parâmetros de threshold, `checkpoint_interval`, token caps, restart rate-limit.

### B. Estado e probes
- `scripts/heartbeat-v3/state_store.py` (novo) OU equivalente no runner atual
- `scripts/heartbeat-v3/health_probe.py` (novo)
  - funções: `collect_gateway`, `collect_rpc`, `collect_queue`, `collect_mc_orphans`, `collect_disk`.

### C. Dispatcher / fluxo
- `scripts/heartbeat-v3/run_loop.py` (ou `heartbeat-v3.sh` no caso de shell existente)
  - aplicar budgets por fluxo/tipo + chamada de checkpoint após `N`.

### D. Integração com crons
- `scripts/resource-monitor.sh` (ou equivalente existente)
- `scripts/watchdog.sh` (ou equivalente)
- `scripts/session-gc.sh` (ou equivalente)
- ajustar apenas leitura de artefatos/telemetria;
- não modificar comportamento principal desses crons.

### E. Segurança operacional
- `scripts/gateway-safe-restart.sh` (somente leitura/invocação)
  - remover qualquer chamada direta de `openclaw gateway restart`.

### F. Verificação e observabilidade
- `scripts/heartbeat-v3/health-gated-smoke.sh` (novo)
- `scripts/heartbeat-v3/storm-sim.sh` (novo)
- `scripts/heartbeat-v3/telemetry.sh` (novo) para registrar:
  - uptime, dispatch/min, rate-limit hits, restarts, checkpoints por estado.

---

## 7) Verificação e simulação

### 7.1 Smoke
1. `bash scripts/heartbeat-v3/health-gated-smoke.sh --phase dry-run --max-iterations 10`
2. `bash scripts/heartbeat-v3/health-gated-smoke.sh --force-checkpoint`
3. `bash scripts/heartbeat-v3/telemetry.sh --dump-json`

### 7.2 Simulação de storm
1. `bash scripts/heartbeat-v3/storm-sim.sh --rate 2x --duration 120s`
2. `bash scripts/heartbeat-v3/storm-sim.sh --type mixed --review-wake-ratio 0.35`
3. Validar: sem violar limiar de restart, sem starvation entre `inbox` e `review_wake`.

### 7.3 Métricas-alvo pós-teste
- **Uptime** do gateway: sem oscilação excessiva; restarts somente com `critical`.
- **dispatch/min** estável dentro da banda definida.
- **Restarts**: máximo 3 por hora no período de teste.
- **Checkpoint ratio**: healthy/degraded/critical observável e alinhado com thresholds.

---

## 8) Rollback / Fallback
- Modo de segurança manual: `HEARTBEAT_HEALTH_GATE_DISABLED=1` (para execução imediata em emergência).
- Em emergência real, reset de estado para `safe_mode` e retorno ao rate-limit cego legado por janela curta (2–3 min), com nota técnica no ticket de operação.
- Como não altera `openclaw.json`, rollback é 100% por arquivos de operação/scripts.

---

## 9) Critérios de aceite (pré-revisão humana)
- ✅ Fluxo com orçamento separado `inbox` e `review_wake` implementado no plano.
- ✅ Política de WIP por tipo definida e documentada.
- ✅ Checkpoint CTO-ops com 6 métricas + matriz OK/degraded/critical definida.
- ✅ Restart condicionado a `scripts/gateway-safe-restart.sh` + rate limit.
- ✅ Verificações (smoke, storm-sim, métricas) listadas com comandos concretos.
- ✅ Plano não exige mudança em `openclaw.json`.
