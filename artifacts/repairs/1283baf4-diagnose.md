# Diagnose — 83be878a-8d3a-4d92-aecc-06a787154557

## Root cause
The repair leaf is stalled due to orchestration-layer gating rather than task content.

1. **heartbeat-v3 backlog policy keeps the task in inbox**
   - Repeated scheduler behavior for this family of repair tasks is to hold them in inbox under backlog policy instead of promoting them to an observable execution path.
   - Symptom pattern: "sem atividade observável" with repeated redispatch pressure but no durable progress signal.

2. **controller-v1 uses adapter-only dispatch with direct_exec to cto-ops**
   - This path reduces observability because it may not perform direct Mission Control mutation for session/progress state.
   - The result is a task that can be "sent" operationally but still appear stalled from MC’s perspective.

3. **Repeated redispatch causes churn instead of remediation**
   - Without fixing the gating and MC update path, repeated redispatch of the same repair leaf does not resolve the root issue.

## Proposed repair owner
- heartbeat-v3 backlog policy owner
- controller-v1 dispatch adapter / MC mutation contract owner

## Explicit next repair step
1. Adjust backlog policy so `lane=repair` tasks with a valid Execution Contract can bypass inbox retention once they are stalled beyond SLA.
2. Route execution through an isolated subagent session that yields a traceable `mc_session_key`, instead of `agent:cto-ops:main` via `direct_exec`.
3. Remove or patch `adapter-only dispatch` behavior for this flow so Mission Control receives deterministic updates for session key, delivery state, and last error.
4. Gate this task from further redispatch until steps 1–3 are applied.
