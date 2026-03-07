# Autonomy Architecture Validation
Generated: 2026-03-07T08:25:11Z

## Overall: `PASS`
- Passed: 13
- Warnings: 2
- Failed: 0
- Active project: `3a0d8492` Grow Luna X account by +200 followers
- Active milestone: `fed0a72b` M0 Session Recovery + Baseline + Charter

## Scheduler Snapshot
- Mode: `full` | health=`healthy` | slots=4
- Reserved slots: `{"ambient": 0, "project": 0, "repair": 1, "review": 0}`
- Running by lane: `{"ambient": 0, "project": 0, "repair": 4, "review": 0}`
- Eligible by lane: `{"ambient": 0, "project": 0, "repair": 4, "review": 0}`
- Dispatch decision: `{"lane": "", "status": "", "task_id": "", "type": "idle"}`

## Failed Checks
- None.

## Warnings
- `project_lane_coexists_with_ambient` Project lane coexists with ambient: Current snapshot does not exercise ambient+project contention at the same time.
  - {"ambient_running": 0, "project_eligible": 0}
- `project_artifacts_fresh` Project artifacts exist and are fresh: Required artifacts are healthy, but optional project artifacts are missing or stale.
  - session-health-latest.json: stale (1690 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/session-health-latest.json
  - baseline-latest.json: stale (1689 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/baseline-latest.json

## Passed Checks
- `active_project_present` Active project detected: Active project `3a0d8492` with chairman_state=`active`.
  - project=Grow Luna X account by +200 followers
  - milestone=M0 Session Recovery + Baseline + Charter
- `governance_not_in_review` Governance stays out of review: No governance card is currently in `review`.
- `governance_not_claimed` Governance is never claim-driven: No governance card has an active lease/claim.
- `review_queue_actionable_only` Review queue contains only actionable work: Every card in `review` is actionable by the judge/runtime.
- `judge_not_using_main` Judge runtime never falls back to main: No legacy judge dispatch to `main` was recorded.
- `review_sessions_not_on_main` Review sessions avoid main: No review card is linked to `main`.
- `auto_dispatch_not_on_main` Auto execution never lands on main: No auto-dispatched execution is linked to `main`.
- `scheduler_state_fresh` Scheduler state is fresh: Scheduler state updated 0 minute(s) ago.
- `scheduler_mode_live` Scheduler v2 is active: Scheduler mode is `full`.
- `repair_lane_served` Repair lane receives capacity: Repair work has live capacity via running tasks or the current dispatch decision.
  - {"dispatch": {"lane": "", "status": "", "task_id": "", "type": "idle"}, "eligible": 4, "running": 4}
- `autonomy_runtime_consistent` Autonomy runtime matches MC active scope: Runtime state matches the active project, milestone and workstream window.
  - runtime_project=3a0d8492
  - runtime_milestone=fed0a72b
  - runtime_workstreams=0a7aac3a,c2dad9a4,f71aaca0
- `repair_bundles_integrity` Repair bundles stay executable: All 26 open repair bundle(s) have executable children and live work.
- `repair_gates_valid` Repair-gated tasks point to live bundles: All 21 repair-gated task(s) reference open repair bundles.
