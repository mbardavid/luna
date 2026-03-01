# Comparison: crshdn/mission-control vs Our Mission Control

**Status:** DRAFT — awaiting Matheus review
**Created:** 2026-03-01

---

## 1. Overview

| | **crshdn/mission-control** | **Our Mission Control** |
|---|---|---|
| **Version** | 1.2.0 (2026-02-19) | Custom build (2026-02-24) |
| **Stack** | Next.js 15, SQLite, TypeScript | Next.js, PostgreSQL (via docker-compose) |
| **DB** | SQLite (single file) | PostgreSQL (more scalable) |
| **Gateway Integration** | WebSocket nativo ao OpenClaw | REST API + bash scripts |
| **Auth** | Bearer token + HMAC webhooks | Local token estático |
| **License** | MIT (open source) | Custom (private) |
| **Deployment** | Docker-ready, multi-machine | Docker-compose, single machine |

---

## 2. Feature Comparison (Detailed)

### 2.1 Task Management

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| Kanban board | ✅ 7 columns (Planning→Inbox→Assigned→In Progress→Testing→Review→Done) | ✅ 4 columns (Inbox→In Progress→Review→Done) | crshdn (mais granular) |
| Drag-and-drop | ✅ | ✅ | Tie |
| Priority levels | ✅ (low, normal, high, urgent) | ✅ (low, medium, high) | Tie |
| Due dates | ✅ | ✅ | Tie |
| Task dependencies | ❌ (roadmap) | ✅ (depends_on_task_ids) | **Ours** |
| Custom fields | ❌ | ✅ (mc_session_key, mc_retry_count, mc_progress, mc_delivered, mc_last_error, mc_output_summary) | **Ours** |
| Blocked detection | ❌ | ✅ (is_blocked, blocked_by_task_ids) | **Ours** |
| Tags | ❌ | ✅ | **Ours** |

### 2.2 AI Planning

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| AI Q&A planning flow | ✅ (interactive questions before task starts) | ❌ | **crshdn** |
| Clarifying questions | ✅ (AI asks multiple-choice + free text) | ❌ (Luna decides approach directly) | **crshdn** |
| Auto-spec generation | ✅ (from Q&A answers) | ❌ (Luna writes specs manually) | **crshdn** |
| Planning persistence | ✅ (resume interrupted planning) | ❌ | **crshdn** |

### 2.3 Agent System

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| Agent auto-creation | ✅ (creates agent per task based on requirements) | ❌ (fixed agents: Luna, Luan, Crypto-sage, QS) | **crshdn** (more flexible) |
| Gateway agent import | ✅ (discover + bulk import from OpenClaw) | ✅ (manual config in mc-agent-ids.json) | **crshdn** |
| Agent SOUL.md | ✅ (per-agent personality) | Partial (Luan has workspace but no real SOUL) | **crshdn** |
| Agent status tracking | ✅ (standby, working, idle) | ✅ (via sessions_list) | Tie |
| Agent avatars/emoji | ✅ | ❌ | crshdn |
| Agent metrics | ❌ (roadmap) | ❌ | Tie |

### 2.4 Gateway Integration

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| WebSocket connection | ✅ (real-time bidirectional) | ❌ (REST polling + bash scripts) | **crshdn** |
| Session management | ✅ (planning + agent sessions) | ✅ (via mc_session_key custom field) | crshdn (nativo) |
| Chat history sync | ✅ | ❌ (sessions_history available but not used by MC) | **crshdn** |
| Task dispatch | ✅ (auto-dispatch after planning) | ✅ (via heartbeat bash script) | crshdn (mais integrado) |
| Retry dispatch | ✅ (API endpoint) | ✅ (mc-fail.sh --retry) | Tie |
| Device identity | ✅ (Ed25519 key pairing) | ❌ | **crshdn** |

### 2.5 Real-Time Features

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| Live feed / event stream | ✅ (SSE real-time) | ❌ (static Kanban, manual refresh) | **crshdn** |
| Activity log | ✅ (timeline with icons) | ❌ | **crshdn** |
| Progress updates | ✅ (PROGRESS_UPDATE protocol) | ✅ (mc_progress custom field) | crshdn (real-time) |
| BLOCKED reporting | ✅ (agent protocol) | ✅ (is_blocked field) | Tie |

### 2.6 Deliverables & Output

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| File deliverables | ✅ (file upload, clickable URLs) | ❌ (text-only via mc_output_summary) | **crshdn** |
| Project directories | ✅ (auto-created per task) | ❌ | **crshdn** |
| Workspace browser | ✅ (per-workspace dashboard) | ❌ | **crshdn** |

### 2.7 Automation & Operations

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| Bash automation scripts | Minimal (bridge scripts) | ✅ (14 scripts: mc-spawn, mc-complete, mc-fail, watchdog, delivery, resource-monitor, etc.) | **Ours** |
| Watchdog (auto-heal) | ❌ | ✅ (every 15min, auto-complete stalled tasks) | **Ours** |
| Resource monitoring | ❌ | ✅ (RAM/CPU monitoring, auto-kill orphans) | **Ours** |
| Failure detection | ❌ | ✅ (heartbeat + watchdog + inline) | **Ours** |
| Cost tracking | ❌ | ✅ (mc_estimated_cost_usd, mc_actual_cost_usd) | **Ours** |
| Retry logic with limits | ❌ | ✅ (mc_retry_count, max 2 retries) | **Ours** |
| Heartbeat integration | ❌ | ✅ (bash deterministic, blocklist, dispatch) | **Ours** |
| Cron job orchestration | ❌ | ✅ (7 cron jobs for MC operations) | **Ours** |

### 2.8 Security

| Feature | crshdn | Ours | Winner |
|---------|--------|------|--------|
| API auth | ✅ (Bearer + HMAC) | ✅ (static token) | crshdn |
| Security headers | ✅ (X-Frame-Options, CSP, etc.) | ❌ | **crshdn** |
| Input validation (Zod) | ✅ | ❌ | **crshdn** |
| Path traversal protection | ✅ | N/A | crshdn |

---

## 3. Key Architectural Differences

### crshdn: "GUI-first, agent-per-task"
- Cada task cria um novo agente especializado
- Fluxo visual: UI → Planning Q&A → Agent created → Work → Deliverables
- Foco no UX do desenvolvedor humano
- WebSocket nativo dá real-time updates
- Mais parecido com "Cursor Cloud Agents" (Era 3)

### Ours: "Automation-first, fixed-agent-fleet"
- Agentes fixos (Luna, Luan, Crypto-sage) — não cria por task
- Luna é o orquestrador que faz spawn de sessões
- Foco na automação operacional (watchdog, delivery, resource monitoring)
- Bash scripts como backbone (zero token overhead)
- Custom fields permitem tracking rico (retry count, cost, progress)
- Mais parecido com "SRE/DevOps dashboard" que "IDE dashboard"

---

## 4. What We Should Adopt from crshdn

### 🟢 High Priority (adotar)

1. **WebSocket integration com Gateway**
   - Elimina polling e bash scripts para dispatch
   - Real-time status updates sem overhead
   - Já temos o gateway rodando, só falta conectar

2. **AI Planning flow (Q&A)**
   - Antes de spawnar um agente, fazer perguntas clarificatórias
   - Reduz retrabalho por specs ambíguos
   - Pode ser implementado como step no mc-spawn.sh

3. **Live Feed / Event Stream (SSE)**
   - Dashboard estático é inútil sem refresh manual
   - SSE é leve e dá real-time sem WebSocket no frontend

4. **Deliverables system**
   - Agentes devem produzir artifacts (diffs, test reports, screenshots)
   - mc_output_summary é insuficiente — precisa de files/URLs

5. **7 status columns** (Planning → Inbox → Assigned → In Progress → Testing → Review → Done)
   - Mais granularidade no workflow
   - "Testing" como fase explícita é valioso

### 🟡 Medium Priority (considerar)

6. **Agent per task** (dynamic creation)
   - Interessante mas conflita com nosso modelo de agentes fixos
   - Hibridar: manter fleet fixo + criar agents efêmeros para tasks específicas

7. **Gateway agent discovery**
   - Auto-import agents do OpenClaw → MC
   - Elimina config manual em mc-agent-ids.json

8. **Device identity (Ed25519)**
   - Mais seguro que token estático
   - Baixa prioridade, token funciona

### 🔴 Low Priority (ignorar por agora)

9. Security headers — bom mas não urgente (MC é interno)
10. Docker — já temos docker-compose funcionando
11. Workspace browser — nice-to-have, não essential

---

## 5. What crshdn Should Adopt from Us

1. **Task dependencies** — essencial para workflows complexos
2. **Custom fields** — session_key, retry_count, progress, cost tracking
3. **Watchdog automation** — auto-heal stalled/orphaned tasks
4. **Resource monitoring** — prevent OOM from agent storms
5. **Failure detection + retry logic** — with max retries and escalation
6. **Heartbeat system** — periodic health check + task dispatch
7. **Blocklist mechanism** — prevent auto-dispatch of human-gated tasks
8. **Cost tracking** — essential when running many agents

---

## 6. Migration Strategy

### Option A: Adopt crshdn/mission-control (replace ours)
**Pros:** More mature UI, WebSocket native, community-supported, actively developed
**Cons:** Lose our custom fields, watchdog, automation scripts, cost tracking. Would need to port all our operational scripts to their system.
**Effort:** 3-5 days migration + 2-3 days porting scripts

### Option B: Upgrade our MC with crshdn features
**Pros:** Keep all our automation infrastructure, add missing UI features
**Cons:** More dev work, maintaining custom codebase
**Effort:** 5-8 days for WebSocket + Live Feed + Planning + Deliverables

### Option C: Hybrid — Use crshdn as frontend, our backend
**Pros:** Best UI + best automation. crshdn dashboard → our PostgreSQL + scripts
**Cons:** Complex integration, two codebases to maintain
**Effort:** 8-10 days

### Recommendation: **Option A (Adopt crshdn) + port our scripts**
- crshdn has better UX, real-time, and is community-maintained
- Our scripts (watchdog, delivery, heartbeat) can work against any backend
- Port custom fields to crshdn's SQLite schema
- Keep our operational automation as external scripts
- Biggest win: WebSocket + Live Feed + Planning flow "for free"

---

## 7. Decision Points for Matheus

1. **Replace our MC with crshdn, or upgrade ours?**
2. **If replace:** keep scripts as external automation layer?
3. **Timeline:** start immediately or after PMM stabilizes?
4. **Scope:** full migration or just cherry-pick features (WebSocket, Live Feed)?
