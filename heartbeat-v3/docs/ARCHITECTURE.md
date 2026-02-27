# Heartbeat V3 — Architecture

## Principle

> **Bash detecta. Filesystem enfileira. Luna consome. Bash escala.**

No AI in the critical path of detection, queuing, or escalation. AI only enters at the CONSUMPTION phase, where it already has a channel, context, and can fail without systemic consequence.

## Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    CRONTAB NATIVO (kernel)                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  */10 * * * *  heartbeat-v3.sh                               │
│  */5  * * * *  queue-escalation.sh                           │
│  */30 * * * *  session-gc.sh                                 │
│                                                              │
└──────┬───────────────┬───────────────┬───────────────────────┘
       │               │               │
       ▼               │               ▼
┌──────────────┐       │        ┌──────────────┐
│ heartbeat    │       │        │ escalation   │
│ (detection)  │       │        │ (bash)       │
│              │       │        │              │
│ • failures   │       │        │ • pending>15m│
│ • inbox      │       │        │   → Discord  │
│ • stale      │       │        │ • pending>30m│
│              │       │        │   → escalate │
└──────┬───────┘       │        └──────┬───────┘
       │               │               │
       ▼               ▼               ▼
┌──────────────────────────────────────────────────────────────┐
│                   FILESYSTEM QUEUE                            │
│                                                              │
│  queue/pending/   ← heartbeat writes here                    │
│  queue/active/    ← Luna moves here during work              │
│  queue/done/      ← Luna moves here when complete            │
│  queue/failed/    ← Luna moves here on failure               │
│  queue/escalated/ ← escalation.sh moves here                 │
│                                                              │
│  Format: {timestamp}-{type}-{task_id_short}.json             │
│  Atomic write: write to .tmp → os.replace()                  │
│                                                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               │ Luna reads on:
                               │  • system-event nudge
                               │  • SOUL.md directive
                               │  • user interaction idle
                               │
                               ▼
                    ┌──────────────────┐
                    │  LUNA (main)     │
                    │                  │
                    │  • Has channel ✅│
                    │  • Has context ✅│
                    │  • Can spawn   ✅│
                    │  • Can notify  ✅│
                    └──────────────────┘
```

## Components

### heartbeat-v3.sh / heartbeat-v3.py
- **What:** Main detection engine. Replaces heartbeat-v2.sh.
- **Change from v2:** Python extracted from heredoc → standalone `.py` file.
- **Phase 4:** On failure detection, writes `respawn` queue file + system-event nudge (instead of cron one-shot).
- **Phase 9:** On dispatch, writes `dispatch` queue file + system-event nudge (instead of cron isolated session).
- **Same guardrails:** circuit breaker, rate limit, flock, state file.

### queue-consumer.py
- **What:** Module that Luna uses to consume the queue.
- **Operations:** `peek()` → `claim()` → process → `complete()`.
- **Atomicity:** Uses `os.replace()` (atomic on Linux) for all moves.
- **Idempotency:** If called 2x on the same item, second call is a no-op.

### queue-escalation.sh
- **What:** Deterministic escalation for stale queue items.
- **Logic:** >15min → Discord nudge, >30min → critical alert + move to `escalated/`.
- **Pure bash:** Zero AI, zero sessions.

### session-gc.sh
- **What:** Garbage collection of zombie cron sessions.
- **Logic:** Lists gateway sessions, destroys cron sessions >2h old.
- **Why:** Cron one-shot sessions persist in the gateway after job completion. This is a slow memory leak.

## Queue File Format

```json
{
  "version": 1,
  "type": "dispatch",          // dispatch | respawn | alert
  "task_id": "cd0c8e37-...",
  "title": "Implementar feature X",
  "agent": "luan",
  "priority": "high",
  "created_at": "2026-02-26T22:00:00Z",
  "created_by": "heartbeat-v3",
  "context": {
    "description": "...",
    "failure_type": "TIMEOUT",   // only for respawn
    "retry_count": 1,            // only for respawn
    "adjustments": "..."         // only for respawn
  },
  "constraints": {
    "max_age_minutes": 30,
    "timeout_seconds": 600
  },
  "spawn_params": {              // only for dispatch
    "agent": "luan",
    "description": "...",
  }
}
```

## How Luna Consumes the Queue

### System-Event Nudge (primary)
After writing to the queue, heartbeat-v3 sends a lightweight system-event to Luna's main session:
```bash
openclaw cron add --at "+10s" --agent main \
  --system-event "📋 Nova tarefa na dispatch queue..." \
  --delete-after-run --name "queue-nudge-{id}"
```
This injects a system message WITHOUT creating a new session. Luna sees it on next interaction.

### SOUL.md Directive (fallback)
Luna's SOUL.md includes:
```
## Dispatch Queue
Ao iniciar qualquer interação, verifique workspace/heartbeat-v3/queue/pending/.
Se houver arquivos, processe-os ANTES de qualquer outra ação.
```

### Escalation (safety net)
If Luna doesn't consume within 15min, `queue-escalation.sh` nudges Discord.
If still pending after 30min, it alerts #general-luna and moves to `escalated/`.

## Key Differences from V2

| Aspect | V2 | V3 |
|--------|----|----|
| Python code | Heredoc in bash | Standalone `.py` file |
| Dispatch | Cron one-shot (creates session) | Queue file + system-event nudge |
| Failure respawn | Disabled (caused OOM) | Queue file (safe) |
| AI in critical path | Yes (cron → agent) | No (bash → filesystem → agent reads) |
| Session leak | Every dispatch creates a session | Zero new sessions |
| Escalation | None | Deterministic bash (15min/30min) |
| Session cleanup | None | session-gc.sh every 30min |

## Failure Modes

| Failure | Impact | Mitigation |
|---------|--------|------------|
| Luna never checks queue | Delay ≤30min | Escalation to Discord/Matheus |
| Queue file corruption | One item lost | Atomic writes (.tmp → replace) |
| System-event not delivered | Delay until next check | SOUL.md directive + escalation |
| Queue fills up | Backlog | Rate limit (3/hour) + escalation |
| heartbeat-v3 crash | No detection | Existing log mtime monitoring |
