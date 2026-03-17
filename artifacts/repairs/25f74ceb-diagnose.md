# Diagnose — 25f74ceb

## Root cause
The failure is caused by a **routing/ownership mismatch in the execution path**:

1. **Auto-dispatch for `lane=ambient` resolved to `main`**
   - The task context explicitly says: `Auto-dispatch for lane 'ambient' resolved to 'main'.`
   - That already puts execution on the chairman/main path instead of a dedicated execution worker.

2. **Execution agents are required to be independent of the chairman session, but the actual run is chairman-originated**
   - The task context explicitly says: `Execution agents must not depend on the chairman session.`
   - Direct evidence from the current runtime session registry shows this diagnostic subagent was spawned by:
     - `spawnedBy: agent:main:cron:a8bf3fd6-71ae-4c01-b399-5a429c7484ea`
   - Source: `/home/openclaw/.openclaw/agents/cto-ops/sessions/sessions.json`
   - This is evidence that the repair/execution flow is still anchored to the `main` control path instead of a lane-owned executor contract.

3. **Resulting failure mode**
   - `ambient -> main` makes the task dependent on chairman/main session semantics for dispatch and continuity.
   - That violates the stated execution contract and creates a brittle path where execution availability/ownership is tied to the chairman session rather than an isolated worker/session.

## Evidence
### Task/context evidence
- Provided context:
  - `Auto-dispatch for lane 'ambient' resolved to 'main'`
  - `Execution agents must not depend on the chairman session`

### Runtime evidence
From `/home/openclaw/.openclaw/agents/cto-ops/sessions/sessions.json`:
- current subagent session key: `agent:cto-ops:subagent:f15f7c47-2c5d-4756-bc45-b704c2fa9a6d`
- `spawnedBy: agent:main:cron:a8bf3fd6-71ae-4c01-b399-5a429c7484ea`

This shows the active execution/repair chain is still rooted in `main`, which is the exact dependency the context says must not exist.

## Proposed repair owner
- **Owner of lane-resolution / dispatch policy** for `ambient`
- **Owner of chairman/main-session isolation rules** in Mission Control / dispatch orchestration

Operationally, this is likely the controller/dispatcher policy owner rather than the task content owner.

## Explicit next repair step
1. **Change lane policy so `ambient` does not resolve to `main` for execution.**
   - `ambient` should resolve to a dedicated execution agent/session class, not the chairman/main session.

2. **Add a dispatch guardrail:**
   - If task metadata or contract says execution must not depend on chairman, block any route where:
     - resolved agent = `main`, or
     - `spawnedBy` / execution root is `agent:main:*`.

3. **Re-dispatch only after validation** that the new route creates an isolated executor with its own ownership/telemetry, instead of chairman-rooted execution.

## Recommended validation after repair
- Queue/lane resolution for `ambient` no longer points to `main`
- New executor session is not rooted at `agent:main:*`
- Execution has its own durable session identity and observable progress path
