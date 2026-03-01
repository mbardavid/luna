# Analysis: ClawSuite (outsourc-e/clawsuite)

**Status:** DRAFT — awaiting Matheus review
**Created:** 2026-03-01
**Source:** https://github.com/outsourc-e/clawsuite

---

## 1. What Is It?

ClawSuite is an **all-in-one command center for OpenClaw agents**. Think of it as a full-featured web dashboard that connects to your OpenClaw Gateway and provides a visual UI for everything: agent management, mission orchestration, chat, cost tracking, file browsing, memory editing, cron management, and more.

**Self-described:** "Not a chat wrapper. A complete command center."

**Current version:** 3.0.0 (2026-02-25)
**License:** MIT (open source)
**Author:** [@outsourc-e](https://github.com/outsourc-e) + community
**Stars:** Growing (star-history chart on README)

---

## 2. Full Feature Breakdown

### 2.1 🤖 Mission Control / Agent Hub

- **Multi-agent orchestration**: Spawn, pause, resume, abort agent sessions
- **Isometric office view**: Visual representation of agents working in real-time (SVG-based)
- **Mission lifecycle**: Full state machine (spawn → working → paused → resumed → aborted → done)
- **Live agent status**: Real-time badges showing what each agent is doing
- **Mission reports**: Success rate, token count, artifacts produced
- **Exec approval prompts**: When an agent wants to run a dangerous command, UI shows approval modal with 30s countdown, risk badges, approve/deny

### 2.2 💬 Chat Interface

- **Real-time SSE streaming**: Token-by-token output, no polling
- **Multi-session management**: Switch between agent conversations
- **Full chat history**: Synced with gateway
- **File and image attachments**: Upload directly in chat
- **Markdown + syntax highlighting**: Code blocks rendered properly
- **Message search**: Cmd+F across conversations
- **Chat dedup**: Prevents duplicate messages on paste/attach

### 2.3 📊 Cost Analytics

- **Per-agent spend breakdown**: See how much each agent costs
- **Daily trend charts**: 30-day history
- **MTD totals**: Month-to-date spending
- **Projected EOM cost**: Forecasts end-of-month spend
- **Per-model usage table**: Breakdown by model (Opus, Sonnet, GPT, etc.)
- **Budget tracking**: Set budgets, see % utilization
- **Real SQLite data**: Not estimates — actual usage from gateway

### 2.4 🧠 Memory Browser

- **Browse agent memory**: View `MEMORY.md` + `memory/*.md` files
- **Full-text search**: Search across all memory files with line jump
- **Edit mode**: Modify memory files directly in the UI
- **Unsaved changes indicator**: Prevents accidental navigation away
- **Markdown preview toggle**: View raw or rendered
- **Grouped file list**: Organized by agent/date

### 2.5 📁 Workspace File Browser

- **Split-panel navigator**: File tree on left, content on right
- **Expandable folders**: Navigate workspace hierarchy
- **File icons by type**: Visual differentiation
- **Syntax highlighting**: TS, JS, JSON, Python, etc.
- **Image preview**: View images inline
- **Edit + save**: Modify files directly (Monaco editor)
- **Markdown preview**: Rendered markdown files

### 2.6 ⏰ Cron Manager

- **Visual cron management**: Create, edit, delete cron jobs from UI
- **Next run time display**: `nextRunAt` field
- **Full CRUD**: All operations verified and working

### 2.7 🌐 Browser Integration

- **Headed Chromium**: With stealth anti-detection
- **Agent handoff**: Share live browser pages with AI agents
- **Persistent sessions**: Cookies survive restarts
- **Browser proxy**: Gateway RPC for browser control

### 2.8 🔧 Terminal

- **Full PTY support**: Real terminal in the browser
- **Cross-platform**: Works on Linux/macOS/Windows
- **SSE streaming**: Real-time output

### 2.9 🐛 Debug Console

- **Gateway diagnostics**: Health checks, status monitoring
- **Pattern-based troubleshooter**: Common issue detection
- **System metrics footer**: CPU, RAM, disk, gateway status, uptime (toggle in settings)

### 2.10 🛒 Skills Marketplace

- **2,000+ skills**: From ClawdHub registry
- **Security scanning**: Every skill audited before install
- **One-click install**: With dependency resolution
- **Auth-protected**: GET /api/skills requires authentication

### 2.11 🎨 Themes & Mobile

- **3 themes**: Paper Light, Ops Dark, Premium Dark
- **66+ components**: Deep theme wiring
- **Theme persistence**: Across sessions
- **PWA**: Installable on iOS, Android, desktop
- **Mobile-first**: Full feature parity on mobile
- **Tailscale support**: Access from anywhere, no port forwarding
- **Safari fixes**: Frosted glass nav, safe area insets

### 2.12 🔒 Security

- **Auth middleware**: On ALL API routes (10 previously unprotected routes fixed in v3.0)
- **CORS locked**: To localhost only (wildcard removed)
- **Path traversal prevention**: On file/memory routes
- **Rate limiting**: On high-risk endpoints (exec, gateway-restart, update-check)
- **CSRF guard**: `requireJsonContentType()` on all mutating POST routes
- **Input validation**: On body parameters
- **Exec approval workflow**: For sensitive commands
- **Security audit**: Full audit pass documented in SECURITY.md

---

## 3. Technical Stack

| Component | Technology |
|-----------|------------|
| Frontend | Next.js (App Router), React, Tailwind CSS |
| Backend | Next.js API routes |
| Database | SQLite |
| Language | TypeScript |
| Streaming | SSE (Server-Sent Events) |
| Gateway comms | WebSocket + REST (RPC) |
| Terminal | PTY via SSE |
| Editor | Monaco (VS Code engine) |
| Desktop | Electron (in development) |
| Mobile | PWA (Progressive Web App) |

---

## 4. Version History

| Version | Date | Key Changes |
|---------|------|-------------|
| 1.0.0 | 2026-02-17 | Initial release: chat, kanban, dark mode, mobile optimization |
| 2.0.0 | 2026-02-19 | Live streaming, enterprise polish, mission robustness |
| 2.1.0 | 2026-02-22 | Cost analytics, health widget, 38 QA bugs fixed |
| 3.0.0 | 2026-02-25 | Agent Hub overhaul, exec approval, memory browser, file browser, 10 security fixes |

**Pace:** 4 major releases in 8 days. Very active development.

---

## 5. Comparison with Our Setup

| Capability | ClawSuite | Our Setup |
|-----------|-----------|-----------|
| Agent chat | ✅ Full UI | ✅ Discord channels |
| Mission orchestration | ✅ Visual UI | ✅ MC + bash scripts |
| Cost tracking | ✅ Per-agent/model | ✅ mc_estimated/actual_cost fields |
| Memory browser | ✅ In-app editor | ❌ Manual file editing |
| File browser | ✅ Monaco editor | ❌ CLI only |
| Cron manager | ✅ Visual UI | ❌ Manual crontab |
| Terminal | ✅ In-browser PTY | ✅ exec tool |
| Skills marketplace | ✅ 2000+ skills | ❌ Manual skill install |
| Browser control | ✅ Headed + stealth | ✅ agent-browser CLI |
| Mobile app | ✅ PWA | ❌ |
| Exec approval | ✅ In-UI modal | ❌ (trust-based) |
| Live streaming | ✅ SSE | ❌ (polling) |
| Themes | ✅ 3 themes | ❌ (fixed dark) |
| Debug console | ✅ | ❌ |

---

## 6. What ClawSuite Offers That We Don't Have

### 🟢 High Value
1. **Memory Browser** — edit MEMORY.md, lessons.md directly from browser. Huge for Matheus reviewing agent state
2. **Cost Analytics** — per-agent spend with trends. We track fields but don't visualize
3. **Cron Manager UI** — we have 7+ crons managed via CLI. Visual manager would be much better
4. **Exec Approval** — security gate before agents run destructive commands
5. **Live SSE Streaming** — real-time agent output without polling

### 🟡 Medium Value
6. **File Browser** — Monaco editor in browser for workspace files
7. **Skills Marketplace** — discover and install OpenClaw skills visually
8. **PWA/Mobile** — access from phone (Matheus could check agent status from bed)
9. **Debug Console** — gateway diagnostics in UI

### 🔴 Nice-to-Have
10. **Isometric office view** — cool visualization but not essential
11. **3 themes** — aesthetic preference
12. **Electron desktop app** — in development, PWA covers this

---

## 7. Key Differences from crshdn/mission-control

| | ClawSuite | crshdn/mission-control |
|---|---|---|
| **Focus** | All-in-one command center (chat, files, memory, terminal, costs, missions) | Task management + planning + agent dispatch |
| **Scope** | Much broader — replaces multiple tools | Focused — does one thing well (Kanban + planning) |
| **AI Planning** | ❌ No Q&A planning flow | ✅ Interactive planning with clarifying questions |
| **Agent creation** | Manual / gateway import | ✅ Auto-creates per task |
| **Cost tracking** | ✅ Full analytics | ❌ |
| **Memory/File editing** | ✅ In-app | ❌ |
| **Maturity** | v3.0 (4 releases in 8 days) | v1.2 (3 releases in 16 days) |
| **Mobile** | ✅ PWA | ❌ |

**Summary:** ClawSuite is broader (Swiss Army knife), crshdn is deeper on task planning (scalpel).

---

## 8. Recommendation

ClawSuite is the most complete OpenClaw dashboard available. It could **replace both our MC and several CLI workflows** (memory editing, cron management, file browsing, cost tracking).

### Considerations:
- **Pros of adopting:** Massive feature set for free, active community, MIT license, PWA for mobile access
- **Cons:** May not have our custom automation (watchdog, retry logic, heartbeat dispatch). Would need to verify if our bash scripts can work alongside it
- **vs crshdn:** ClawSuite is broader, crshdn has better AI planning. Could even use both for different purposes

### Options:
1. **Try ClawSuite alongside our MC** — install it, point at our gateway, evaluate
2. **Replace our MC with ClawSuite** — most features included, port scripts as needed
3. **Cherry-pick features** — just install for memory browser + cost tracking + cron manager, keep our MC for task tracking

---

## Next Action
Matheus reviews and decides which path to explore.
