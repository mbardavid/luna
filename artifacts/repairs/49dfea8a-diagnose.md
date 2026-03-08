# Diagnose — 5f481af1-f7ef-4b33-ad0e-e4b75b09f40f

## Summary
The task is not progressing due to **control-plane policy gates** rather than the task content itself.

## Root cause
### 1) Heartbeat backlog policy kept the task in inbox (no scheduling)
`heartbeat-v3.log` repeatedly applied:

- `FILTER: 5f481af1 backlog policy — staying in inbox`

This gate prevented the task from being picked up and dispatched for long periods, producing the “sem atividade observável” symptom.

### 2) Misaligned execution mode: controller-v1 adapter-only dispatch + direct_exec → cto-ops
The current dispatch item is **controller-owned** and uses:

- `created_by: controller-v1`
- `lane: repair`, `phase: intake`
- `agent: cto-ops`
- `queue_key: ...|direct_exec|...`

Additionally, `fast-dispatch.log` shows:

- `Controller-owned task 5f481af1... uses adapter-only dispatch (no direct MC mutation)`

So even when dispatch returns `status=ok`, the controller may not be writing/confirming progress/state into Mission Control, which increases perceived stalling and can lead to requeue loops.

## Proposed repair owner
- **Owner of heartbeat-v3 scheduling policy** (backlog policy that keeps items in inbox).
- **Owner of controller-v1 dispatch adapter / MC mutation contract** (adapter-only dispatch behavior and routing to `direct_exec` + `cto-ops`).

## Explicit next repair step
1) **Backlog policy fix**: update the heartbeat-v3 backlog filter so that `lane=repair` tasks with explicit Execution Contract (acceptance criteria + expected artifacts) are **eligible for dispatch**, or provide a deterministic promotion mechanism (e.g., priority override / SLA-based bypass when stalled > X minutes).

2) **Dispatch model fix**: for repair tasks, route to an **isolated executor session** (subagent) instead of `agent:cto-ops:main` direct-exec when the requirement is observability; ensure a real `mc_session_key` is written.

3) **MC state update fix**: remove/adjust the controller-v1 `adapter-only dispatch (no direct MC mutation)` mode for these cards, or add a follow-up MC PATCH step that sets `mc_session_key`, delivery_state, and last_error deterministically.

4) Re-dispatch and validate success criteria:
   - Queue item shows correct lane/agent/workflow.
   - Mission Control shows a non-empty `mc_session_key` and observable progress.
   - Artifact produced at: `artifacts/repairs/49dfea8a-diagnose.md` (this document).
