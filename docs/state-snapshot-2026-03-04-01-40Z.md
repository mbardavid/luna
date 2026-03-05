# State Snapshot — 2026-03-04 01:40Z

## Context / Problematic
- We are stabilizing autonomy: MC + heartbeat-v3 dispatch + QA loop.
- Recurring failure mode: tasks marked `in_progress` without `mc_session_key` (dispatch/link failure) and tasks in `review` with `missing_session_key` (never started).
- We patched review dispatch to use wake_luna_immediate (not mc-fast-dispatch), but inbox dispatch still sometimes produces missing session_key.
- We are planning a definitive heartbeat design: health-gated dispatch, WIP caps, queue as source of truth, transacional linking (no orphan in_progress).

## Decisions in this session
- Pause Luan work on:
  - fe69b371 (health-gated dispatch plan v2) — paused with current plan draft in `docs/health-gated-dispatch-plan.md`.
  - 272b14b8 (QA handoff loop) — plan completed, ready for judge review.
- Increase heartbeat-v3 `max_dispatches_per_hour` to 10 in `heartbeat-v3/config/v3-config.json`.

## Current artifacts (canonical in main workspace)
- `docs/autonomy-roadmap-v1.md` (updated with Cursor scaling-agents ideas)
- `docs/health-gated-dispatch-plan.md` (plan draft; Luan paused)
- `docs/mc-qa-handoff-loop.md` (plan submitted)
- `docs/luan-upstream-docs-mirror-plan.md` (plan submitted)

## Mission Control status (at snapshot time)
- Key cards:
  - 6e5effc1 — awaiting_human — Autonomy v1 plan (artifact updated)
  - fe69b371 — review — health-gated dispatch plan paused
  - 272b14b8 — review — QA handoff loop plan ready

## Upgrade intent
- Matheus requested to:
  1) Pause Luan on active plans; update cards with artifacts + next steps.
  2) Save state + problematic.
  3) Upgrade OpenClaw to latest (currently 2026.3.2 available), using safe procedure.
  4) Post-upgrade: Judge review of plans integrating new OpenClaw features.

## Notes
- Previously pinned 2026.2.22-2 due to plugin incompatibility (historical).
- **Update:** system is now on OpenClaw **2026.3.2** (as of 2026-03-04).
