# Spawning Rules (OpenClaw) — Repo Memory

**Purpose:** avoid repeating failures when orchestrating sub-agents (A2A) and tracking work in Mission Control.

## Golden rules

1) **Spawn sub-agents only via the OpenClaw tool `sessions_spawn`** (or the higher-level assistant API that calls it).
   - Do **not** attempt to spawn via `openclaw gateway call sessions.spawn` — this RPC method is not exposed (returns `unknown method`).

2) **Do not rely on “I saved a file” unless the file exists**.
   - Any critical plan/output must be persisted via `write` (or `git commit`) and then validated with a `read`/`ls` check.

3) **Discord thread-bound subagent spawns may be disabled**.
   - If `mode=session` requires `thread=true` but thread-bound spawns are disabled, use `mode=run`.

4) **Mission Control visibility is not automatic unless you use the MC flow**.
   - A spawn done directly with `sessions_spawn` will not appear in MC unless we create/link the MC task separately.
   - Prefer the operational flow: create MC task → spawn → link `sessionKey` → mirror `TASK_UPDATE` into MC.

5) **When patching files, avoid blind `edit` operations**.
   - Always `read` the exact target region first, then apply a precise `edit` match.

## Recommended approach (hybrid)

- **1) Rule-based (mandatory):** treat tracking as an atomic part of the orchestration turn:
  create MC task → `sessions_spawn` (label=taskId) → link `sessionKey` → mirror `TASK_UPDATE`.

- **2) Helper script (accelerator):** prefer `scripts/a2a-mc-track.sh --json` to generate the MC task + spawn payload + TASK_UPDATE contract.

**DoD (objective):** any A2A spawn appears in MC within 5s with `mc_session_key` set; finishes with `mc_output_summary`.

## Operational checklist

- Before spawning:
  - confirm allowlist contains the target agent (`agents_list`)
  - define TaskSpec/acceptance criteria
  - decide if it must be tracked in Mission Control (most tasks: yes)

- After spawning:
  - capture `childSessionKey`
  - ensure progress reporting contract (`TASK_UPDATE`) is included
  - schedule watchdog/delivery where relevant
