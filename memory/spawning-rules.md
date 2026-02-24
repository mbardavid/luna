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

## Operational checklist

- Before spawning:
  - confirm allowlist contains the target agent (`agents_list`)
  - define TaskSpec/acceptance criteria
  - decide if it must be tracked in Mission Control (most tasks: yes)

- After spawning:
  - capture `childSessionKey`
  - ensure progress reporting contract (`TASK_UPDATE`) is included
  - schedule watchdog/delivery where relevant
