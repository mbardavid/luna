# Diagnose — 996496f0-da00-4cf3-9d05-b095f7208ffe

## Root cause
The leaf task was linked into execution, but the execution path did not produce observable progress because the dispatch path is the wrong one for this class of repair work.

1. **The task was routed through `repair -> cto-ops -> direct_exec`**
   - This path links to `agent:cto-ops:main` instead of creating an isolated executor with distinct progress telemetry.
   - In practice, the task can appear linked while still showing 0% / no observable work.

2. **Linking happened without a durable isolated execution session**
   - The source symptom says: "Leaf task linked without observable progress".
   - That is consistent with a direct-dispatch/main-session style execution path where session linkage exists, but no separate lifecycle/progress stream is available.

3. **Repair flow is reusing the same broken execution path instead of repairing it**
   - The requested action is to keep the source task gated until validation.
   - Re-dispatching via the same path would only recreate the same linked-without-progress failure mode.

## Proposed repair owner
- Owner of **heartbeat-v3 / repair-lane routing policy**
- Owner of **controller-v1 / dispatch path selection**
- Secondary: owner of the **MC session-link / progress telemetry contract**

## Explicit next repair step
1. Keep the source task **gated**; do not promote/retry it through the same route.
2. Change repair routing so this class of leaf task executes in an **isolated spawned session/subagent** with a traceable `mc_session_key`, instead of `agent:cto-ops:main` direct execution.
3. Re-run only after validation that the repaired route yields:
   - isolated executor session,
   - observable progress/telemetry,
   - and durable MC state updates tied to that executor.
