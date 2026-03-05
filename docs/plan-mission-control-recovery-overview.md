# Mission Control Recovery Roadmap (P0/P1/P2)

**Status:** Draft - 2026-03-05  
**Source of truth:** `/home/openclaw/.openclaw/workspace` on `clawdia`  
**Objective:** Recover deterministic task draining and implement the required Luna -> Luan -> Luna -> Luan -> Luna development loop without depending on manual nudges from Matheus.

---

## Inputs Used

### Production runtime and repo state
- Runtime code, cron, queue files and logs on `clawdia`
- `memory/lessons.md`
- `HEARTBEAT.md`
- `docs/agentic-loop-contract.md`
- `docs/agentic-governance-roadmap.md`

### OpenClaw docs and CLI behavior
- [OpenClaw Session Model](https://docs.openclaw.ai/concepts/session-model)
- [OpenClaw Queue Systems](https://docs.openclaw.ai/concepts/queue-systems)
- [OpenClaw Agent Loop](https://docs.openclaw.ai/concepts/agent-loop)
- [OpenClaw System Events](https://docs.openclaw.ai/cli/system)
- [OpenClaw Agent CLI](https://docs.openclaw.ai/cli/agent)
- `openclaw system event --help`
- `openclaw cron add --help`
- `openclaw agent --help`

---

## What the OpenClaw docs change in the diagnosis

1. `system event` and queue wakeups are nudges, not guaranteed execution.
   - The docs and CLI show that `openclaw system event` only enqueues a system event and optionally triggers a heartbeat.
   - This matches production: the main Luna session was repeatedly awakened, but the wake only produced a status summary, not actual review closure.

2. The agent loop is serialized around inbound turns.
   - OpenClaw's agent-loop/session docs imply inbound work is queued into a session, not preemptively forced through as a separate deterministic worker.
   - This explains why review wakeups can lose priority when the same main session is busy with other chats, reminders, or cron-originated prompts.

3. `isolated` jobs are the wrong primitive for flows that need channel delivery or QA follow-through.
   - `openclaw cron add --help` still exposes `--session main|isolated`.
   - Our own lessons already document the failure mode: isolated jobs that try to talk back create `Channel is required` storms and can destabilize the gateway.

4. There are no trustworthy post-session-end hooks in the current stack.
   - Our lessons already record that hooks are not viable for this problem.
   - Therefore the recovery design must stay polling-based or use inline processing on the same turn that receives completion.

5. `sessions_spawn` / agent delegation remains the correct spawn path.
   - OpenClaw behavior plus our lessons both confirm that gateway RPC is not the spawn path.
   - This reinforces that Mission Control must own the task card before agent work starts.

---

## Root problems to solve

1. The main heartbeat path is summary-only.
   - `HEARTBEAT.md` says not to orchestrate there.
   - In practice the main Luna session just listed pending reviews instead of closing them.

2. Status semantics are split across `review`, `needs_approval` and `awaiting_human`.
   - Different scripts interpret the same card differently.

3. The current queue architecture has multiple competing control planes.
   - `heartbeat-v3`
   - `queue-dispatch-cron.sh`
   - reminder-driven queue handling in the main Luna session
   - direct wakeups to the main session

4. Development tasks still bypass the intended 5-step loop.
   - Inbox tasks are dispatched toward execution instead of first entering Luna planning.

5. Human/backlog intent is not machine-readable.
   - Comments like "leave in inbox" do not prevent redispatch.

6. The main Luna session is overloaded.
   - It mixes Discord chat, cron summaries, queue reminders, QA, and orchestration.
   - Our lessons already show this causes context bloat and memory pressure.

---

## Phase order

### P0 - Stabilize and make the system safe
Goal: stop duplicate dispatch, stop silent queue corruption, unify status semantics, and make the current system debuggable.

Exit criteria:
- no queue item reaches `done/` without dispatcher result metadata
- no inbox card with backlog/human hold is re-enqueued
- no consumer depends on comment text to understand hold/human state
- `review` remains higher priority than `inbox`
- `awaiting_human` is terminal and ignored by auto-drainers

### P1 - Encode the mandatory development loop
Goal: implement an enforceable state machine for:
1. Luna planning
2. Luan plan elaboration
3. Luna plan validation
4. Luan implementation and tests
5. Luna final validation

Exit criteria:
- no development task goes directly from `inbox` to Luan execution
- every development task has `mc_phase` and `mc_workflow`
- every Luna-owned review phase is claimed, timed, and either closed or escalated
- queue draining follows review first, then inbox only when no active implementation exists

### P2 - Harden, observe, and replay
Goal: make the loop measurable, replayable, and operationally resilient.

Exit criteria:
- replay harness covers the known failure cases from Mar 5
- session hygiene and queue audits are automated
- SLOs exist for dispatch, review closure, duplicate suppression, and session size
- changes are testable without relying on chat transcripts as the only source of truth

---

## Non-negotiable constraints

- Do not add new isolated cron jobs for review or QA.
- Do not route queue processing through reminders inside the main Luna session.
- Do not use comments as the primary machine-readable hold mechanism.
- Do not treat `system event` as a job runner.
- Do not rely on unsupported hooks to detect task completion.
- Do not keep expanding the main Discord session as the execution engine for every automation.

---

## Deliverables

- [P0 Plan](./plan-mission-control-recovery-p0.md)
- [P1 Plan](./plan-mission-control-recovery-p1.md)
- [P2 Plan](./plan-mission-control-recovery-p2.md)
