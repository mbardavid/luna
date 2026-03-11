# Autonomy Architecture Validation
Generated: 2026-03-10T20:20:56Z

## Overall: `FAIL`
- Passed: 13
- Warnings: 1
- Failed: 1
- Active project: `3a0d8492` Grow Luna X account by +200 followers
- Active milestone: `` (none)

## Scheduler Snapshot
- Mode: `full` | health=`healthy` | slots=4
- Reserved slots: `{"ambient": 0, "project": 0, "repair": 0, "review": 0}`
- Running by lane: `{"ambient": 0, "project": 0, "repair": 0, "review": 0}`
- Eligible by lane: `{"ambient": 0, "project": 0, "repair": 0, "review": 0}`
- Dispatch decision: `{"lane": "", "status": "", "task_id": "", "type": "idle"}`

## Failed Checks
- `project_artifacts_fresh` Project artifacts exist and are fresh: 2 required artifact problem(s) detected.
  - board_packet: stale (3971 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/autonomy-board-packet-latest.md
  - outcome: stale (2571 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/scorecard-latest.json
  - session-health-latest.json: stale (2571 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/session-health-latest.json
  - baseline-latest.json: stale (2571 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/baseline-latest.json
  - board-packet-latest.md: stale (2571 min) at /home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth/board-packet-latest.md

## Warnings
- `project_lane_coexists_with_ambient` Project lane coexists with ambient: Current snapshot does not exercise ambient+project contention at the same time.
  - {"ambient_running": 0, "project_eligible": 0}

## Passed Checks
- `active_project_present` Active project detected: Active project `3a0d8492` with chairman_state=`active`.
  - project=Grow Luna X account by +200 followers
  - milestone=(none)
- `governance_not_in_review` Governance stays out of review: No governance card is currently in `review`.
- `governance_not_claimed` Governance is never claim-driven: No governance card has an active lease/claim.
- `review_queue_actionable_only` Review queue contains only actionable work: Every card in `review` is actionable by the judge/runtime.
- `judge_not_using_main` Judge runtime never falls back to main: No legacy judge dispatch to `main` was recorded.
- `review_sessions_not_on_main` Review sessions avoid main: No review card is linked to `main`.
- `auto_dispatch_not_on_main` Auto execution never lands on main: No auto-dispatched execution is linked to `main`.
- `scheduler_state_fresh` Scheduler state is fresh: Scheduler state updated 0 minute(s) ago.
- `scheduler_mode_live` Scheduler v2 is active: Scheduler mode is `full`.
- `repair_lane_served` Repair lane receives capacity: No repair demand is currently waiting.
- `autonomy_runtime_consistent` Autonomy runtime matches MC active scope: Runtime state matches the active project, milestone and workstream window.
  - runtime_project=3a0d8492
  - runtime_milestone=
  - runtime_workstreams=(none)
- `repair_bundles_integrity` Repair bundles stay executable: All 0 open repair bundle(s) have executable children and live work.
- `repair_gates_valid` Repair-gated tasks point to live bundles: All 0 repair-gated task(s) reference open repair bundles.
