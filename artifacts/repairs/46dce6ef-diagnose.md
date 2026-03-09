# Diagnose — d5e5ffb1-3481-45a5-8b9b-fa7f7309e4c7

## Root cause
The failure is caused by **routing/policy mismatch in the execution path**, not by the content-analysis task itself.

1. **The task intent says `lane=project` should resolve to `main`**
   - Context explicitly states: auto-dispatch for lane `project` resolved to `main`.
   - It also explicitly states execution agents must **not depend on the chairman session**.

2. **Actual repair/dispatch path was reclassified into a controller-owned repair flow**
   - The task was routed through a repair-style path instead of a clean project/main isolated execution path.
   - Prior evidence for this task family showed controller-owned queue items being created with `lane=repair`, `agent=cto-ops`, and a `direct_exec`-style dispatch path.

3. **The resulting dispatch path depends on a non-isolated / chairman-like execution model**
   - This violates the stated requirement that execution must not depend on the chairman session.
   - When the routing lands on `cto-ops` / direct execution semantics instead of an isolated `main` subagent, Mission Control loses the intended observability and ownership model.

## Proposed repair owner
- Owner of **controller-v1 / lane-resolution policy**
- Owner of **heartbeat-v3 / dispatch routing policy**
- Secondary: owner of the **MC adapter / direct-dispatch session-link contract**

## Explicit next repair step
1. Fix policy so a task whose effective intent is `lane=project -> agent=main` is dispatched to an **isolated main subagent/session**, not reclassified into a repair/direct-exec path.
2. Add a guardrail: if task context says "must not depend on the chairman session", block any route that resolves to chairman-like/direct main-session execution.
3. Re-dispatch only after validation that the queue item resolves to:
   - correct project/main routing,
   - isolated session semantics,
   - and a traceable `mc_session_key` for that isolated executor.
