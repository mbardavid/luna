# Plano de Implementação: Control Loop Autônomo v2

**Status:** Em execução  
**Início:** 2026-03-02  
**Autor:** Luna  
**MC Tasks:** `9f66397f` (F0), `e5692112` (F1), `fc8c360e` (F2), `2fa3d4df` (F3), `a2557f3a` (F4)  
**Relatório base:** `docs/report-control-loop-autonomo-v2.md`

---

## Índice

1. [Visão Geral](#1-visão-geral)
2. [Estado Atual (pré-migração)](#2-estado-atual)
3. [Estado Alvo (pós-migração)](#3-estado-alvo)
4. [Fase 0: Baseline, SLOs e Ownership Matrix](#4-fase-0)
5. [Fase 1: Absorver Detectores no heartbeat-v3](#5-fase-1)
6. [Fase 2: Redução de Latência](#6-fase-2)
7. [Fase 3: Validação Dual-Run e Depreciação](#7-fase-3)
8. [Fase 4: Cleanup, Docs e Operação Contínua](#8-fase-4)
9. [Rollback Plan](#9-rollback)
10. [Riscos e Mitigações](#10-riscos)
11. [Cronograma](#11-cronograma)
12. [Apêndices](#12-apêndices)

---

## 1. Visão Geral

### Problema
O sistema tem 23 cron jobs e 45 scripts bash. Detectores se sobrepõem, state files são fragmentados, e completions de subagentes ficam sem processamento quando Luna não está em sessão ativa. Matheus precisa intervir manualmente para rotina operacional.

### Solução
Evoluir o heartbeat-v3 (engine existente de 1185 LOC) para absorver detectores redundantes, adicionar processamento de completions, e reduzir latência de resposta de 30min para 5min.

### Resultado esperado
- 23 crons → 14
- 7+ state files → 2
- Latência detecção→ação: P95 < 6 min (antes: 15-∞ min)
- Zero tasks órfãs ou completions sem QA por mais de 30min
- PMM auto-restart sem intervenção humana

### Princípio operacional
**Não substituir. Não adicionar. Consolidar e acelerar.**

---

## 2. Estado Atual (pré-migração)

### 2.1 Cron Jobs Ativos (23)

```
FREQ    SCRIPT                              CATEGORIA           AÇÃO
────────────────────────────────────────────────────────────────────────────
*/1     gateway-wake-sentinel.sh            Gateway             Restart se down
*/2     gateway-health-sentinel.sh          Gateway             Health check profundo
*/5     mc-failure-detector.sh              MC/Detection        Detecta sessões falhadas → notifica
*/5     queue-escalation.sh                 Heartbeat           Escala queue stuck > 15min
*/5     mc-approvals-notify.sh              MC/Tasks            Notifica tasks pendentes aprovação
*/10    heartbeat-v3.sh                     Heartbeat           Engine principal (9 fases)
*/10    mc-stale-task-detector.sh           MC/Detection        Detecta tasks órfãs → notifica
*/15    mc-watchdog.sh                      MC/Tasks            Retry/completion tracking → age
*/15    mc-delivery.sh                      MC/Tasks            Entrega resultados → Discord POST
*/15    mc-resource-monitor.sh              Gateway             Memory monitoring → GC/restart
*/15    mc-description-watchdog.sh          MC/Detection        Audita descrições → notifica
*/15    pmm-status-updater.sh               PMM                 Atualiza status do bot no MC
*/30    session-gc.sh                       Sessions            Limpa sessões zombie
*/30    escalation-recovery.sh              Heartbeat           Recupera escalations travadas
*/30    smoke-test-orchestrator.sh          PMM                 Smoke test do pipeline
*/30    cron-health-check.sh                Manutenção          Verifica saúde dos crons
6h      session-compact-sentinel.sh         Sessions            Compacta sessões grandes
daily   session-smart-compact.py            Sessions            Compactação inteligente
daily   lessons-sync.sh                     Manutenção          Sync lessons Luna↔Luan
daily   mc-log-rotate.sh                    Manutenção          Rotaciona logs
weekly  mc-cost-report.sh                   Manutenção          Relatório de custos
—       gateway-restart-notify.sh           Gateway             Hook systemd (ExecStartPost)
—       gateway-post-restart-recovery.sh    Gateway             Hook systemd (ExecStartPost)
```

### 2.2 State Files (7+)

```
/tmp/.heartbeat-check-state.json            heartbeat-v3 (principal)
/tmp/.mc-stale-task-detector-state.json     stale task detector
/tmp/.mc-description-watchdog-state.json    description watchdog
/tmp/.mc-failure-detector-state.json        failure detector
/tmp/.heartbeat-escalation-state.json       escalation recovery
/tmp/.heartbeat-last-notify.json            heartbeat notify dedup
/tmp/.gateway-sentinel-state.json           gateway sentinel
/tmp/.mc-description-violations.txt         marker file (violations)
/tmp/.mc-stale-tasks.txt                    marker file (stale tasks)
```

### 2.3 heartbeat-v3 — Capabilities Existentes

```
Arquivo:   heartbeat-v3/scripts/heartbeat-v3.py
Linhas:    1185
Testes:    57 (heartbeat-v3/tests/)
Queue:     heartbeat-v3/queue/{pending,active,done,failed,escalated}/
Consumer:  heartbeat-v3/scripts/queue-consumer.py (512 LOC)
Config:    heartbeat-v3/config/v3-config.json
Lock:      /tmp/.heartbeat-check.lock (fcntl)

FASES EXISTENTES:
  Phase 1    Gateway health check (curl localhost:18789)
  Phase 2    Active hours filter (8h-0h São Paulo)
  Phase 3    Fetch sessions (gateway API) + MC tasks (MC API)
  Phase 4    Failure detection → classify → queue + Discord + nudge
  Phase 4.5  Circuit breaker (3 failures → open → cooldown 30min)
  Phase 4.6  Resource check (skip dispatch se degradado)
  Phase 4.7  Rate limit (max 3 dispatches/hora, 5min entre dispatches)
  Phase 5    Subagent concurrency check (max 2 ativos)
  Phase 5.5  Stale dispatch detection (in_progress sem session_key > 15min)
  Phase 6    Review task dispatch → Luna
  Phase 7    Inbox pull (FIFO + blocklist + dependency chain)
  Phase 8    Dedup
  Phase 9    Dispatch → queue file + system-event nudge

QUEUE TYPES EXISTENTES:
  dispatch   — task do inbox para spawnar
  respawn    — task falhada para re-spawnar

CONSTANTES CHAVE:
  MAX_DISPATCHES_PER_HOUR = 3
  MIN_DISPATCH_INTERVAL_MS = 5 * 60 * 1000  (5min)
  DISPATCH_STALE_MS = 15 * 60 * 1000        (15min)
  CB_FAILURE_THRESHOLD = 3
  FAILURE_COOLDOWN_MS = 30 * 60 * 1000      (30min)
```

### 2.4 Sobreposições Identificadas

```
TIPO DE EVENTO                          QUEM DETECTA HOJE              QUEM DEVERIA DETECTAR
─────────────────────────────────────────────────────────────────────────────────────────────
Subagent falhou (error/timeout)         heartbeat-v3 Phase 4           heartbeat-v3 Phase 4
                                        mc-failure-detector.sh ← REDUNDANTE

Task stale (session morta + ativa)      heartbeat-v3 Phase 5.5         heartbeat-v3 Phase 5.5
                                        mc-stale-task-detector.sh ← REDUNDANTE

Descrição ruim no MC                    mc-description-watchdog.sh     heartbeat-v3 Phase 4.8 (NOVO)

Gateway health                          heartbeat-v3 Phase 1           heartbeat-v3 Phase 1
                                        gateway-health-sentinel.sh ← REDUNDANTE
                                        gateway-wake-sentinel.sh ← MANTER (crítico, isolado)

Cron health                             cron-health-check.sh           Desnecessário com 14 crons

PMM status                              pmm-status-updater.sh          heartbeat-v3 Phase 1 (NOVO)

Escalation recovery                     escalation-recovery.sh         queue-escalation.sh (já cobre)
```

---

## 3. Estado Alvo (pós-migração)

### 3.1 Cron Jobs Alvo (14)

```
FREQ    SCRIPT                              CATEGORIA           AÇÃO
────────────────────────────────────────────────────────────────────────────
*/1     gateway-wake-sentinel.sh            Gateway             Restart se down (crítico, isolado)
*/5     heartbeat-v3.sh                     Heartbeat           Engine principal (12 fases, enhanced)
*/5     queue-escalation.sh                 Heartbeat           Backup se nudge falha
*/5     mc-approvals-notify.sh              MC/Tasks            Notifica tasks pendentes aprovação
*/15    mc-watchdog.sh                      MC/Tasks            Retry/completion tracking
*/15    mc-delivery.sh                      MC/Tasks            Entrega resultados Discord
*/15    mc-resource-monitor.sh              Gateway             Memory/resource management
*/30    session-gc.sh                       Sessions            Cleanup de sessões zombie
6h      session-compact-sentinel.sh         Sessions            Compactação de sessões grandes
daily   session-smart-compact.py            Sessions            Compactação inteligente
daily   lessons-sync.sh                     Manutenção          Sync lessons entre agentes
daily   mc-log-rotate.sh                    Manutenção          Rotação de logs
weekly  mc-cost-report.sh                   Manutenção          Relatório semanal de custos
—       gateway-restart-notify.sh           Gateway             Hook systemd (ExecStartPost)
```

### 3.2 State Files Alvo (2)

```
/tmp/.heartbeat-check-state.json              heartbeat-v3 (expandido com campos absorvidos)
workspace/state/control-loop-metrics.json     Métricas operacionais (novo)
```

### 3.3 heartbeat-v3 Enhanced — Novas Capabilities

```
FASES NOVAS/MODIFICADAS:
  Phase 1    (ENHANCED) Gateway health + PMM PID check + auto-restart
  Phase 4    (ENHANCED) Failure detection + classificação (LOOP, INCOMPLETE, GENERIC)
  Phase 4.8  (NOVO) Description quality audit
  Phase 5.5  (ENHANCED) Stale detection + completion pending QA → queue qa-review

NOVO QUEUE TYPE:
  qa-review  — completion pendente de QA, payload com contexto completo:
    {
      "type": "qa-review",
      "task_id": "...",
      "task_title": "...",
      "agent": "luan",
      "session_key": "...",
      "completion_status": "complete|partial|blocked",
      "priority": "P1",
      "action": "QA review: ler completion report, inspecionar 2+ arquivos, rodar verification checks"
    }
```

### 3.4 Ownership Matrix Alvo

```
TIPO DE EVENTO                  OWNER                       AÇÃO
────────────────────────────────────────────────────────────────────────
Subagent falhou                 heartbeat-v3 Phase 4        Queue respawn + nudge
Task completion pendente QA     heartbeat-v3 Phase 5.5      Queue qa-review + nudge
Task órfã (sem session)         heartbeat-v3 Phase 5.5      Alert + nudge
Inbox com tasks elegíveis       heartbeat-v3 Phase 7-9      Queue dispatch + nudge
Review tasks (Luan→Luna)        heartbeat-v3 Phase 6        Queue review-dispatch
Descrição ruim                  heartbeat-v3 Phase 4.8      Log + alert
Gateway down                    gateway-wake-sentinel       systemctl restart
Gateway memory > threshold      mc-resource-monitor         Session GC / safe restart
PMM morto                       heartbeat-v3 Phase 1        Auto-restart + log
Task retry/completion           mc-watchdog                 Auto-retry (max 2)
Delivery de resultados          mc-delivery                 POST Discord
Queue stuck > 15min             queue-escalation            Escalation alert
Session cleanup                 session-gc                  Limpa zombies
Tasks pendentes aprovação       mc-approvals-notify         Discord notify
```

---

## 4. Fase 0: Baseline, SLOs e Ownership Matrix

**MC Task:** `9f66397f`  
**Executor:** Luna (diretamente)  
**Risco:** LOW  
**Estimativa:** ~2h  

### 4.1 Entregáveis

| # | Arquivo | Descrição |
|---|---------|-----------|
| 1 | `docs/cron-inventory.md` | Mapa oficial: cada cron ativo com frequência, script, categoria, path completo, status (ativo/legado/candidato-depreciar) |
| 2 | `docs/ownership-matrix.md` | Tabela: tipo de evento → script owner → backup → ação → estado atual vs alvo |
| 3 | `docs/slos.md` | SLOs com método de medição e baseline atual |
| 4 | `state/control-loop-metrics.json` | Schema inicial do arquivo de métricas |

### 4.2 SLOs Definidos

| Métrica | Baseline Atual | SLO Alvo | Medição |
|---------|---------------|----------|---------|
| Latência detecção→ação | 15-∞ min | P95 < 6 min | control-loop-metrics.json |
| Alertas duplicados/incidente | 3-4 | ≤ 1 | Contagem Discord manual (1 semana) |
| Tasks órfãs/semana | 2-3 | 0 | MC query semanal |
| Completions sem QA > 30min | Ilimitado | 0 | heartbeat-v3 metrics |
| Gateway uptime | ~95% | > 99% | systemd journal |
| PMM uptime | ~80% (restarts manuais) | > 95% (auto-restart) | PID check log |

### 4.3 Acceptance Criteria

- [ ] `cron-inventory.md` lista todos os 23 crons com status verificado (`crontab -l` vs filesystem)
- [ ] `ownership-matrix.md` cobre todos os tipos de evento da seção 3.4
- [ ] SLOs documentados com método de medição e baseline
- [ ] `state/control-loop-metrics.json` schema criado e validado
- [ ] Git commit com Fase 0 entregue

### 4.4 Validação

```bash
# Confirmar que inventário bate com crontab real
diff <(grep -c "^\*/" docs/cron-inventory.md) <(crontab -l | grep -v "^#" | grep -v "^$" | grep -v "^MC_" | wc -l)

# Confirmar que ownership matrix cobre os tipos definidos
grep -c "│" docs/ownership-matrix.md  # deve ter >= 14 linhas de dados
```

---

## 5. Fase 1: Absorver Detectores no heartbeat-v3

**MC Task:** `e5692112`  
**Executor:** Luan (Two-Phase: planning → implementation)  
**Risco:** MEDIUM  
**Estimativa:** ~4h (1h planning + 3h implementation)  
**Depende de:** Fase 0 concluída

### 5.1 Escopo de Mudanças

#### 5.1.1 heartbeat-v3.py — Modificações

```python
# PHASE 1 ENHANCED: Gateway health + PMM check
# Linha ~300-350 (Phase 1 existente)
# ADICIONAR após gateway health check:

def check_pmm_health(state: dict) -> dict:
    """
    Check if PMM bot is alive. If dead, auto-restart.
    
    Returns:
        {"alive": bool, "pid": int|None, "restarted": bool, "error": str|None}
    """
    PID_FILE = Path(WORKSPACE) / "polymarket-mm" / "paper" / "data" / "production_trading.pid"
    PMM_DIR = Path(WORKSPACE) / "polymarket-mm"
    
    # 1. Read PID file
    # 2. Check if process alive (kill -0)
    # 3. If dead:
    #    a. Load .env from PMM_DIR/.env
    #    b. Find latest prod-*.yaml config
    #    c. subprocess.Popen([python3, -m, runner, --mode, live, --config, ...])
    #    d. Write new PID
    #    e. Log restart event
    #    f. Send Discord alert (informational, not error)
    # 4. Update state with PMM info
```

```python
# PHASE 4 ENHANCED: Failure classification
# Linha ~400-500 (Phase 4 existente)
# MODIFICAR classificação de erro:

def classify_failure(session: dict) -> tuple[str, str]:
    """
    Classify session failure for better diagnostics.
    
    Absorbs: mc-failure-detector.sh logic
    
    Returns:
        (failure_type, recommended_adjustment)
    
    Types:
        LOOP_DEGENERATIVO  — same tool called 5+ times in last messages
        INCOMPLETE         — stopReason=stop but no COMPLETION_STATUS
        THINKING_SIGNATURE — "thinking.signature: Field required" error
        PROVIDER_ERROR     — API/provider level error (400, 429, 500)
        TIMEOUT            — session exceeded runTimeoutSeconds
        GENERIC_ERROR      — unclassifiable
    """
```

```python
# PHASE 4.8 NEW: Description quality audit
# ADICIONAR entre Phase 4.7 e Phase 5

def check_description_quality(tasks: list[dict], state: dict) -> list[dict]:
    """
    Audit active task descriptions for quality.
    
    Absorbs: mc-description-watchdog.sh logic
    
    Checks:
        - Length >= 200 chars
        - Has structural markers (##, Objective, Context, Criteria)
        - No placeholder text
    
    Returns list of violations (task_id, title, issues).
    Dedup via state["absorbed"]["alerted_description_violations"].
    """
    ACTIVE_STATUSES = {"inbox", "in_progress", "review"}
    MIN_LENGTH = 200
    MARKERS = ["## ", "Objective", "Objetivo", "Context", "Criteria", "Problem", "Approach"]
    
    # Filter active tasks
    # Check length and structure
    # Dedup against state
    # Return new violations only
```

```python
# PHASE 5.5 ENHANCED: Completion pending QA
# Linha ~700-750 (Phase 5.5 existente)
# MODIFICAR para detectar completions:

def detect_stale_and_completions(tasks: list[dict], sessions: list[dict], state: dict) -> list[dict]:
    """
    Enhanced stale detection that also identifies completions pending QA.
    
    Absorbs: mc-stale-task-detector.sh logic
    
    Detects:
        1. ORPHAN: task in_progress/review with NO session_key
        2. STALE: task with session_key but session not in active list (existing)
        3. COMPLETION_PENDING: task with dead session that had COMPLETION_STATUS: complete
           → generates qa-review queue item instead of respawn
    
    For case 3, attempts to read last messages from session history
    to extract COMPLETION_STATUS block.
    """
    live_session_keys = {s.get("key", "") for s in sessions}
    
    for task in tasks:
        if task["status"] not in {"in_progress", "review"}:
            continue
        
        session_key = (task.get("custom_field_values") or {}).get("mc_session_key", "")
        
        if not session_key:
            # ORPHAN — task sem executor
            yield {"type": "orphan", ...}
        
        elif session_key not in live_session_keys:
            # Session morta — completion ou failure?
            # Try to determine if completion happened
            completion_status = check_session_completion(session_key)
            
            if completion_status in ("complete", "partial"):
                # COMPLETION PENDING QA
                yield {
                    "type": "qa-review",
                    "task_id": task["id"],
                    "task_title": task["title"],
                    "session_key": session_key,
                    "completion_status": completion_status,
                    "priority": "P1",
                }
            else:
                # STALE/FAILED — existing behavior
                yield {"type": "respawn", ...}
```

#### 5.1.2 queue-consumer.py — Modificações

```python
# ADICIONAR suporte ao queue type "qa-review"
# O consumer deve gerar output diferente para qa-review:
# Em vez de spawn params, gera um "action brief" para Luna

def process_qa_review(item: dict) -> dict:
    """
    Process a qa-review queue item.
    
    Returns action brief that Luna can process directly:
    {
        "action": "qa-review",
        "task_id": "...",
        "task_title": "...",
        "steps": [
            "1. Read completion report from session history",
            "2. Inspect 2+ changed files",
            "3. Run verification checks",
            "4. Mark done or reject via mc-review-reject.sh"
        ],
        "session_key": "...",
        "mc_task_id": "..."
    }
    """
```

#### 5.1.3 v3-config.json — Adições

```json
{
  "existing_config": "...",
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
    "required_markers": ["## ", "Objective", "Objetivo", "Context", "Criteria"],
    "check_statuses": ["inbox", "in_progress", "review"]
  },
  "failure_classification": {
    "loop_threshold": 5,
    "known_provider_errors": ["thinking.signature", "RESOURCE_EXHAUSTED", "capacity"]
  }
}
```

### 5.2 Testes Novos (~15)

```python
# heartbeat-v3/tests/test_heartbeat_v3.py (adicionar)

# PMM Health Check (3 testes)
def test_pmm_alive_returns_healthy():
    """PID exists, process running → alive=True, restarted=False"""

def test_pmm_dead_triggers_restart():
    """PID exists, process dead → alive=False, restarted=True, new PID written"""

def test_pmm_no_pid_file_returns_none():
    """No PID file → alive=None, no restart attempted"""

# Failure Classification (4 testes)
def test_classify_loop_degenerativo():
    """5+ identical tool calls → LOOP_DEGENERATIVO"""

def test_classify_thinking_signature():
    """Error contains 'thinking.signature' → THINKING_SIGNATURE"""

def test_classify_incomplete():
    """stopReason=stop, no COMPLETION_STATUS → INCOMPLETE"""

def test_classify_timeout():
    """Session exceeded timeout → TIMEOUT"""

# Description Quality (3 testes)
def test_description_too_short_flagged():
    """Description < 200 chars → violation"""

def test_description_no_structure_flagged():
    """No ## headers → violation"""

def test_description_good_not_flagged():
    """200+ chars with structure → no violation"""

# Completion Detection (3 testes)
def test_completion_pending_generates_qa_review():
    """Dead session with COMPLETION_STATUS: complete → qa-review queue item"""

def test_orphan_task_detected():
    """Task in_progress with no session_key → orphan alert"""

def test_stale_without_completion_generates_respawn():
    """Dead session without COMPLETION_STATUS → respawn (existing behavior)"""

# Queue Consumer qa-review (2 testes)
def test_queue_consumer_accepts_qa_review():
    """qa-review item is processed without error"""

def test_qa_review_generates_action_brief():
    """qa-review produces structured action brief for Luna"""
```

### 5.3 Acceptance Criteria

- [ ] Phase 1 enhanced: PMM PID check + auto-restart funcional
- [ ] Phase 4 enhanced: classifica erros em 6 categorias
- [ ] Phase 4.8: audita descrições de tasks ativas
- [ ] Phase 5.5 enhanced: detecta completion pendente → queue `qa-review`
- [ ] queue-consumer.py aceita e processa `qa-review`
- [ ] v3-config.json com seções `pmm`, `description_quality`, `failure_classification`
- [ ] Todos os 57+ testes existentes passam
- [ ] 15+ testes novos passam
- [ ] Crons antigos MANTIDOS (dual-run)
- [ ] State file expandido com `absorbed{}` section

### 5.4 Verification Checks

```bash
cd /home/openclaw/.openclaw/workspace/heartbeat-v3
python3 -m pytest tests/ -x --tb=short -q

# Teste funcional (dry-run)
python3 scripts/heartbeat-v3.py --dry-run --verbose 2>&1 | grep -E "Phase|PMM|description|qa-review|classify"

# Verificar que queue-consumer aceita qa-review
echo '{"type":"qa-review","task_id":"test","task_title":"Test","priority":"P1"}' > queue/pending/test-qa.json
python3 scripts/queue-consumer.py --dry-run --one
```

### 5.5 Constraints

- NÃO remover crons antigos nesta fase
- NÃO alterar frequência do cron
- Manter backward compatibility com queue items `dispatch` e `respawn`
- PMM restart deve carregar `.env` via Python `dotenv` (não depender de bashrc)
- Não usar `subprocess.run` para restart — usar `Popen(start_new_session=True)`

### 5.6 Rollback

Se testes falharem ou heartbeat-v3 enhanced causar problemas:
```bash
# Reverter heartbeat-v3.py para versão anterior
cd /home/openclaw/.openclaw/workspace
git checkout HEAD~1 -- heartbeat-v3/scripts/heartbeat-v3.py
git checkout HEAD~1 -- heartbeat-v3/scripts/queue-consumer.py
git checkout HEAD~1 -- heartbeat-v3/config/v3-config.json
# Crons antigos continuam rodando (dual-run), cobertura mantida
```

---

## 6. Fase 2: Redução de Latência

**MC Task:** `fc8c360e`  
**Executor:** Luna (diretamente)  
**Risco:** LOW  
**Estimativa:** ~1h + 24h de monitoramento  
**Depende de:** Fase 1 concluída e validada

### 6.1 Mudanças

#### 6.1.1 Crontab

```bash
# ANTES
*/10 * * * * ... heartbeat-v3.sh ...

# DEPOIS
*/5 * * * * ... heartbeat-v3.sh ...
```

#### 6.1.2 OpenClaw Heartbeat

```bash
# Verificar frequência atual
openclaw status | grep heartbeat

# Alterar para */5
# Método depende da versão do OpenClaw:
# Opção A: openclaw configure (se suportar)
# Opção B: editar openclaw.json (CUIDADO — regra de IDENTITY.md)
# Opção C: openclaw cron (se suportar heartbeat interval)
```

### 6.2 Monitoramento 24h

```bash
# Verificar a cada 6h durante as primeiras 24h:

# 1. Gateway memory
ps -o rss= -p $(pgrep -f openclaw-gateway) | awk '{printf "%.0f MB\n", $1/1024}'
# SLO: < 600 MB

# 2. Circuit breaker trips
grep "circuit_breaker" /home/openclaw/.openclaw/workspace/logs/heartbeat-v3.log | tail -5
# SLO: 0 trips em 24h

# 3. Rate limit hits
grep "rate_limit" /home/openclaw/.openclaw/workspace/logs/heartbeat-v3.log | tail -5
# SLO: < 5 hits em 24h (hits = working as intended, just verify not excessive)

# 4. Alertas duplicados
# Verificar Discord: mesmo incidente notificado mais de 1x?
```

### 6.3 Acceptance Criteria

- [ ] heartbeat-v3 roda a cada 5 min (`crontab -l` confirma)
- [ ] OpenClaw heartbeat roda a cada 5 min
- [ ] 24h sem OOM
- [ ] 24h sem retry storm
- [ ] Gateway RSS < 600 MB durante o período
- [ ] Circuit breaker: 0 trips
- [ ] Doc de validação com métricas antes/depois

### 6.4 Rollback

```bash
# Reverter para frequência anterior
# Editar crontab: */5 → */10
crontab -e  # mudar linha do heartbeat-v3
# OpenClaw heartbeat: reverter para */30
```

---

## 7. Fase 3: Validação Dual-Run e Depreciação

**MC Task:** `2fa3d4df`  
**Executor:** Luna (monitoramento) + Luan (script de validação)  
**Risco:** MEDIUM  
**Estimativa:** 3 dias de dual-run + 3 dias de depreciação gradual  
**Depende de:** Fase 2 concluída

### 7.1 Script de Comparação

```bash
# scripts/dual-run-validator.sh
# Compara outputs dos detectores antigos vs heartbeat-v3 enhanced

# Input:
#   - Logs do heartbeat-v3 (heartbeat-v3.log)
#   - Logs dos detectores antigos (mc-failure-detector.log, mc-stale-task-detector.log, etc.)
#
# Output:
#   - Relatório de divergências:
#     - heartbeat-v3 detectou, antigo não detectou (OK, enhanced é melhor)
#     - Antigo detectou, heartbeat-v3 NÃO detectou (BUG, corrigir antes de depreciar)
#     - Ambos detectaram (OK, equivalência)
#     - Nenhum detectou (OK, nada aconteceu)
```

### 7.2 Cronograma de Depreciação

```
DIA 1-3:  Dual-run (ambos ativos, comparar outputs)
DIA 4:    Depreciar mc-failure-detector.sh (*/5)
          Depreciar gateway-health-sentinel.sh (*/2)
DIA 5:    Depreciar mc-stale-task-detector.sh (*/10)
          Depreciar escalation-recovery.sh (*/30)
          Depreciar cron-health-check.sh (*/30)
DIA 6:    Depreciar mc-description-watchdog.sh (*/15)
          Depreciar pmm-status-updater.sh (*/15)
          Depreciar smoke-test-orchestrator.sh (*/30)
```

### 7.3 Procedimento de Depreciação por Script

```bash
# Para cada script:
# 1. Verificar log de dual-run: 0 divergências para este detector
# 2. Comentar a linha no crontab (não deletar)
# 3. Mover script para scripts/archive/
# 4. Remover state file de /tmp/
# 5. Registrar em docs/cron-inventory.md (status: deprecated, data, razão)
# 6. Monitorar 24h: heartbeat-v3 continua cobrindo os casos?
# 7. Se problema: descomentar crontab + restaurar script (< 5 min)
```

### 7.4 Acceptance Criteria

- [ ] 3 dias de dual-run sem divergência crítica
- [ ] Cada script depreciado tem entrada no log de validação
- [ ] Scripts arquivados em `scripts/archive/` com data e razão
- [ ] Crontab final tem 14 entries ativas
- [ ] State files obsoletos removidos de `/tmp/`
- [ ] `docs/cron-inventory.md` atualizado com status final

### 7.5 Critério de STOP

**Se durante o dual-run um detector antigo flagrar algo que heartbeat-v3 enhanced missed:**
1. PARAR depreciação
2. Investigar: o que heartbeat-v3 deveria ter detectado e não detectou?
3. Corrigir heartbeat-v3.py
4. Adicionar teste para o caso
5. Reiniciar contagem de 3 dias de dual-run

---

## 8. Fase 4: Cleanup, Docs e Operação Contínua

**MC Task:** `a2557f3a`  
**Executor:** Luna (diretamente)  
**Risco:** LOW  
**Estimativa:** ~2h  
**Depende de:** Fase 3 concluída

### 8.1 Documentação a Atualizar

| Arquivo | Mudanças |
|---------|----------|
| `AGENTS.md` | Remover refs a scripts depreciados, atualizar seção de heartbeat |
| `HEARTBEAT.md` | Simplificar checklist (5.5 itens → 3), remover marker files obsoletos |
| `TOOLS.md` | Remover refs a scripts antigos |
| `docs/cron-inventory.md` | Estado final (14 crons) |
| `docs/ownership-matrix.md` | Revisão pós-migração |
| `docs/slos.md` | Atualizar baselines com dados reais |

### 8.2 Rotina de Operação Contínua

```markdown
## Rotina Semanal (Luna, durante heartbeat)
1. Verificar control-loop-metrics.json — SLOs dentro do alvo?
2. Verificar logs heartbeat-v3 — algum pattern novo?
3. Revisar ownership-matrix — algum tipo de evento sem owner?

## Regra Permanente
Antes de criar um NOVO script de detecção:
1. Verificar se heartbeat-v3 já cobre o caso
2. Se sim → adicionar ao heartbeat-v3 (Phase nova ou enhanced)
3. Se não → justificar por que precisa ser script separado
4. Documentar em ownership-matrix
```

### 8.3 Acceptance Criteria

- [ ] Zero referência a scripts depreciados em AGENTS.md, HEARTBEAT.md, TOOLS.md
- [ ] `cron-inventory.md` = crontab real (14 entries)
- [ ] `ownership-matrix.md` validado contra crons ativos
- [ ] Rotina semanal documentada
- [ ] Git push com estado final
- [ ] Todas as SLOs sendo medidas

---

## 8.5 Cenário Crítico: Gateway Restart Mid-Task

### Problema
Quando o gateway reinicia (manual, CTO-ops, update, OOM), todas as sessões de subagents morrem. Tasks ficam `in_progress` no MC com `session_key` apontando para sessões que não existem mais. Sem intervenção, essas tasks ficam órfãs indefinidamente.

### O que existe hoje
`gateway-post-restart-recovery.sh` (hook systemd ExecStartPost):
- Espera gateway ficar ready
- Relança PMM se estava rodando
- Move tasks `in_progress` → `inbox` (limpa session_key)
- Notifica Discord
- Dispara heartbeat imediato

### Gaps identificados

**Gap 1 — Sem snapshot, sem recovery.**
O script depende de `/tmp/.gateway-pre-restart-state.json` (escrito pelo `gateway-safe-restart.sh`). Restarts via systemd direto, CTO-ops, ou OOM kill não geram snapshot → recovery pula tudo → tasks órfãs.

**Gap 2 — Move pra inbox, não continua de onde parou.**
Mover task de `in_progress` → `inbox` perde contexto. Luan é re-spawnado do zero, sem saber que já tinha feito 80% na sessão anterior. Desperdício de tokens e tempo.

**Gap 3 — PMM restart usa comando antigo.**
Script referencia `production_runner` e `prod-002.yaml`, não o runner unificado com `prod-003.yaml`.

### Solução (incluída na Fase 1)

#### 1. Recovery SEM snapshot (snapshot-less recovery)

Adicionar ao `gateway-post-restart-recovery.sh` um fallback que consulta MC API diretamente quando snapshot não existe:

```bash
if [ ! -f "$STATE_FILE" ]; then
    log "No snapshot — running snapshot-less recovery via MC API"
    
    # Query MC for in_progress tasks with session_key
    ORPHAN_TASKS=$(curl -s -H "Authorization: Bearer $MC_API_TOKEN" \
      "$MC_API_URL/api/v1/boards/$MC_BOARD_ID/tasks" | \
    python3 -c "
    import json, sys
    tasks = json.load(sys.stdin).get('items', [])
    for t in tasks:
        if t['status'] in ('in_progress', 'review'):
            sk = (t.get('custom_field_values') or {}).get('mc_session_key', '')
            if sk:  # Has session_key but session is dead (gateway just restarted)
                print(json.dumps({
                    'task_id': t['id'],
                    'title': t['title'],
                    'session_key': sk,
                    'status': t['status']
                }))
    ")
    
    # Process each orphaned task
    echo "$ORPHAN_TASKS" | while read -r task_json; do
        # Generate queue item for heartbeat-v3 to process
        # (see point 2 below)
    done
fi
```

#### 2. Continuação com contexto (respawn-with-context)

Em vez de mover para `inbox` (perde contexto), gerar um queue item estruturado:

```json
{
  "type": "respawn-with-context",
  "task_id": "e5692112-...",
  "task_title": "Control Loop Fase 1: Absorver detectores",
  "previous_session_key": "agent:luan:subagent:abc123",
  "previous_status": "in_progress",
  "recovery_reason": "gateway_restart",
  "context_instruction": "CONTINUE FROM WHERE YOU LEFT OFF. Read session history of previous session for context. Do NOT restart from scratch.",
  "timestamp": "2026-03-02T22:00:00Z"
}
```

Quando Luna processa este queue item:
1. Lê `sessions_history` da sessão anterior (se ainda existir no storage)
2. Extrai progresso: quais arquivos foram modificados, quais testes passaram, onde parou
3. Re-spawna Luan com prompt que inclui: "Sessão anterior morreu por gateway restart. Progresso até aqui: [resumo]. Continue de onde parou."

Se a sessão anterior não tiver history acessível, fallback para re-spawn completo (comportamento atual).

#### 3. PMM restart atualizado

```bash
# ANTES (hardcoded):
nohup python3 -m paper.production_runner --config "paper/runs/prod-002.yaml" ...

# DEPOIS (dinâmico):
# Encontrar o config mais recente
LATEST_CONFIG=$(ls -t "$PMM_DIR/paper/runs/prod-*.yaml" 2>/dev/null | head -1)
if [ -z "$LATEST_CONFIG" ]; then
    LATEST_CONFIG="paper/runs/prod-003.yaml"
fi

# Usar runner unificado com .env loading
python3 -c "
import subprocess, sys, os
from pathlib import Path

env = os.environ.copy()
dotenv = Path('$PMM_DIR/.env')
if dotenv.exists():
    for line in dotenv.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()

proc = subprocess.Popen(
    [sys.executable, '-m', 'runner', '--mode', 'live', '--config', '$LATEST_CONFIG'],
    stdout=open('logs/production.log', 'a'),
    stderr=subprocess.STDOUT,
    start_new_session=True,
    cwd='$PMM_DIR',
    env=env,
)
print(proc.pid)
with open('paper/data/production_trading.pid', 'w') as f:
    f.write(str(proc.pid))
"
```

### Fluxo Completo Pós-Restart

```
Gateway reinicia (t=0)
  → systemd ExecStartPost → gateway-post-restart-recovery.sh (t=5s)
    → Snapshot exists?
      → YES: recovery normal (existente)
      → NO: snapshot-less recovery via MC API
    → Para cada task órfã:
      → Gera queue item "respawn-with-context" em heartbeat-v3/queue/pending/
    → PMM alive?
      → NO: restart com runner unificado + .env
    → Notifica Discord
    → Dispara heartbeat imediato (t=15s)
  → heartbeat-v3 acorda (t=15-20s)
    → Processa queue items (respawn-with-context)
    → Nudge Luna via system-event
  → Luna acorda (t=20-30s)
    → Lê queue items
    → Lê session history da sessão morta
    → Re-spawna com contexto
  → Luan retoma trabalho com contexto (t=30-60s)

Latência total: 30-60 segundos (antes: 30min+)
```

---

## 9. Rollback Plan

### 9.1 Rollback por Fase

| Fase | Trigger | Procedimento | Tempo |
|------|---------|------------|-------|
| Fase 0 | N/A (docs only) | N/A | N/A |
| Fase 1 | Testes falhando ou heartbeat-v3 instável | `git checkout HEAD~1 -- heartbeat-v3/` | < 2 min |
| Fase 2 | OOM, retry storm, ou RSS > 700MB | Reverter crontab `*/5 → */10` | < 1 min |
| Fase 3 | Divergência crítica no dual-run | Parar depreciação, crons antigos continuam | < 1 min |
| Fase 4 | N/A (docs only) | N/A | N/A |

### 9.2 Rollback Total

```bash
# Se precisar reverter TUDO para pré-migração:
cd /home/openclaw/.openclaw/workspace

# 1. Restaurar heartbeat-v3 original
git checkout <commit-pre-fase1> -- heartbeat-v3/

# 2. Restaurar crons depreciados
for script in scripts/archive/*.sh; do
    cp "$script" scripts/
done

# 3. Restaurar crontab original
# (manter backup em docs/crontab-pre-migration.txt)
crontab docs/crontab-pre-migration.txt

# 4. Verificar
crontab -l | wc -l  # deve ser 23
```

---

## 10. Riscos e Mitigações

| # | Risco | Prob | Impacto | Mitigação |
|---|-------|------|---------|-----------|
| R1 | heartbeat-v3 fica complexo (>1500 LOC) | Médio | Médio | Modularizar fases em arquivos separados se > 1500 |
| R2 | */5 causa load no gateway | Baixo | Alto | Circuit breaker + monitoramento 24h |
| R3 | Regressão de cobertura | Médio | Alto | Dual-run 3 dias + critério de STOP |
| R4 | qa-review loop (re-dispatch mesma task) | Baixo | Médio | Dedup por task_id + cooldown 30min |
| R5 | PMM auto-restart loop (crash→restart→crash) | Baixo | Alto | max_restarts_per_hour = 3 + cooldown 5min |
| R6 | State file corrupto | Baixo | Alto | Auto-reset se JSON parse falha (já existe) |
| R7 | Dual-run gera alertas duplicados | Médio | Baixo | Esperado e temporário (3 dias) |

---

## 11. Cronograma

```
DIA 0 (hoje)     Fase 0: Baseline + SLOs + Ownership Matrix
                  Luna executa diretamente (~2h)

DIA 1            Fase 1 Planning: Luan planeja absorção
                  Luna revisa plano, aprova ou ajusta

DIA 1-2          Fase 1 Implementation: Luan implementa
                  Luna faz QA review (inspeciona código, roda testes)
                  Luna valida: heartbeat-v3 --dry-run com todas as fases novas

DIA 2            Fase 2: Reduzir latência
                  Luna altera crontab (*/10 → */5)
                  Início do monitoramento 24h

DIA 3            Fase 2 Validação: 24h sem problemas?
                  Se OK → iniciar dual-run (Fase 3)
                  Se não → rollback, investigar

DIA 3-5          Fase 3 Dual-Run: ambos sistemas ativos
                  Luna compara outputs diariamente

DIA 6-8          Fase 3 Depreciação: desativar scripts validados
                  1 por dia, com monitoramento

DIA 9            Fase 4: Cleanup + docs + push final
                  Luna atualiza toda documentação

DIA 10+          Operação contínua
                  Revisão semanal de SLOs
```

---

## 12. Apêndices

### A. Backup do Crontab Atual

```bash
# Salvar antes de qualquer mudança
crontab -l > docs/crontab-pre-migration.txt
```

### B. Comandos de Verificação Rápida

```bash
# Status geral pós-migração
echo "=== Crons ===" && crontab -l | grep -v "^#" | grep -v "^$" | grep -v "^MC_" | wc -l
echo "=== State files ===" && ls /tmp/.heartbeat* /tmp/.mc-* /tmp/.gateway* 2>/dev/null | wc -l
echo "=== heartbeat-v3 ===" && wc -l heartbeat-v3/scripts/heartbeat-v3.py
echo "=== Tests ===" && cd heartbeat-v3 && python3 -m pytest tests/ -q 2>&1 | tail -1
echo "=== PMM ===" && kill -0 $(cat polymarket-mm/paper/data/production_trading.pid 2>/dev/null) 2>/dev/null && echo "ALIVE" || echo "DEAD"
echo "=== Gateway ===" && curl -sf localhost:18789/health | head -1
```

### C. Mapa de Absorção (script antigo → fase do heartbeat-v3)

```
mc-failure-detector.sh        → Phase 4 (failure classification)
mc-stale-task-detector.sh     → Phase 5.5 (completion detection + orphan detection)
mc-description-watchdog.sh    → Phase 4.8 (description quality)
gateway-health-sentinel.sh    → Phase 1 (já faz gateway health check)
pmm-status-updater.sh         → Phase 1 (PMM PID check)
escalation-recovery.sh        → queue-escalation.sh (já cobre)
cron-health-check.sh          → Desnecessário com 14 crons
smoke-test-orchestrator.sh    → Eliminado (valor questionável em produção)
heartbeat-v2.sh               → heartbeat-v3 (já substitui)
```

---

*Plano vivo — atualizado conforme cada fase é executada. Última atualização: 2026-03-02.*
