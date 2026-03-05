# Mission Control Recovery - P0 Stabilization Plan

**Status:** Draft - 2026-03-05  
**Goal:** Make the current control loop safe, consistent, and debuggable before adding more autonomy.

---

## Scope

P0 is not the final autonomous loop.
P0 stops the current failures:
- duplicate dispatch
- queue corruption by secondary consumers
- status semantic mismatch
- backlog cards being re-enqueued
- auto-QA looking at the wrong states

---

## Problems P0 addresses

1. `HEARTBEAT.md` on the main session is a notifier, not an executor.
2. `needs_approval`, `review` and `awaiting_human` are interpreted inconsistently.
3. There is more than one queue consumer in practice.
4. Backlog and human-hold intent are not machine-readable.
5. Dedup happens too late.
6. Channel split between `#general-luna` and `#notifications` hides the real state.

---

## Workstreams

### P0.1 - Freeze the production source of truth

**Objective:** Stop debugging a stale mirror.

Changes:
- Treat `/home/openclaw/.openclaw/workspace` as the only active source.
- Snapshot and commit the current production code before any behavior change.
- Stop using the local mirror as implementation reference.

Target files:
- whole server repo

Validation:
- `git status` on the server is understood before edits
- every later fix is made against the same runtime that cron is executing

---

### P0.2 - Reduce to one queue consumer per responsibility

**Objective:** Remove the current ambiguity where queue files are processed by both code and session reminders.

Changes:
- Keep filesystem queue consumption in code only.
- Disable or remove any reminder/automation that asks the main Luna session to "check queue/pending and process".
- Forbid manual `pending -> done` moves as a valid processing path.
- Add audit metadata requirement for queue completion:
  - `completed_by`
  - `completed_at`
  - `result.action`
  - `result.session_id` when applicable

Target files and surfaces:
- `heartbeat-v3/scripts/queue-dispatch-cron.sh`
- `heartbeat-v3/scripts/queue-consumer.py`
- external reminder/automation config that currently injects queue reminders into the main session

Validation:
- no new `queue/done/*.json` file appears without dispatcher metadata
- no queue item is "processed" by direct file move from the main session
- `226588ab` no longer accumulates new done files without corresponding MC transition

---

### P0.3 - Canonicalize status semantics

**Objective:** Make every consumer agree on what the card means.

Recommendation:
- Keep coarse runtime statuses as:
  - `inbox`
  - `in_progress`
  - `review`
  - `awaiting_human`
  - `done`
  - `failed`
  - `blocked`
  - `stalled`
  - `retry`
- Move approval reason out of status and into explicit fields:
  - `mc_gate_reason`
  - `mc_last_error`

Rationale:
- Production already partly uses `awaiting_human`.
- The real problem is semantic collapse, not the exact label.
- P0 must make all consumers read the same label set.

Changes:
- `scripts/mc-task-update.sh`: stop implicit semantic remaps that other consumers do not understand.
- `scripts/mc-client.sh`: make allowlist and normalization match the canonical set.
- `scripts/auto-qa-reviewer.sh`: stop assuming `review + mc_last_error=needs_approval` is the only QA funnel.
- `heartbeat-v3/scripts/heartbeat-v3.py`: read the canonical state set in Phase 5.5, 5.7, 6 and 7.

Validation:
- `awaiting_human` cards are never auto-dispatched
- `review` cards remain review cards until closed or handed back
- no consumer depends on legacy `needs_approval` as a standalone status

---

### P0.4 - Add machine-readable dispatch policy

**Objective:** Stop using comments as policy.

New field:
- `mc_dispatch_policy = auto | backlog | human_hold`

Behavior:
- `auto`: eligible for normal drain
- `backlog`: stay in inbox, never auto-enqueue
- `human_hold`: stay out of auto-drain regardless of status until cleared

Changes:
- `heartbeat-v3/scripts/heartbeat-v3.py` Phase 7 must read this field before enqueueing inbox work.
- `scripts/mc-client.sh` should support easy updates to this field.
- `config/heartbeat-blocklist.json` stays only for static global gates and dependency chains, not as the primary per-card hold mechanism.

Validation:
- cards like `226588ab` can remain in inbox without being redispatched
- comments like "leave in inbox" are no longer required for automation correctness

---

### P0.5 - Deduplicate at queue-write time

**Objective:** Prevent repeated queue items for the same card/phase.

New rule:
- queue key = `task_id + dispatch_type + status + phase`

Changes:
- Before writing a queue item, check:
  - matching pending item exists
  - matching active item exists
  - live `mc_session_key` exists for the same task/phase
  - recent done file exists within cooldown for same queue key
- Active recovery should only requeue items that have no live proof of progress.

Target files:
- `heartbeat-v3/scripts/heartbeat-v3.py`
- `heartbeat-v3/scripts/queue-dispatch-cron.sh`

Validation:
- no new 20+ duplicate queue files for one inbox task over a few hours
- `8cc49d51` / `226588ab` style loops stop

---

### P0.6 - Make review truly higher priority than inbox

**Objective:** Preserve the intended drain order with explicit guards.

Changes:
- Keep `review` draining ahead of `inbox`.
- Block inbox dispatch whenever there is active `review` work that is not already claimed.
- Ignore `awaiting_human` in automatic drain.
- Ensure in-progress implementation still blocks new inbox intake when WIP limit is reached.

Target file:
- `heartbeat-v3/scripts/heartbeat-v3.py`

Validation:
- if `review > 0`, no fresh inbox dispatch occurs unless the review item is already leased/claimed and WIP rules allow intake

---

### P0.7 - Unify operational channel semantics

**Objective:** Make one channel authoritative for operating state.

Changes:
- `#general-luna` should be the authoritative operational channel.
- `#notifications` can remain a mirror/feed, not the primary control loop.
- Review and approval wakeups should not be split across channels without explicit reason.

Targets:
- cron configuration
- scripts that send delivery/approval notifications
- `heartbeat-v3/config/v3-config.json`

Validation:
- operators can reconstruct state from one primary channel
- the system no longer "acts" in one channel and "reports" in another without linkage

---

## Acceptance criteria

P0 is complete when all are true:

- `review` drains before `inbox`
- `awaiting_human` is never auto-drained
- `backlog` and `human_hold` cards are never auto-drained
- no queue item is marked done without dispatcher result metadata
- no task produces repeated queue items for the same phase without live proof of progress
- main Luna reminder flow is no longer a queue consumer

---

## Rollout order

1. Snapshot current server repo state.
2. Remove secondary queue consumers/reminders.
3. Canonicalize status handling.
4. Add `mc_dispatch_policy` and enforce it in Phase 7.
5. Add queue-key dedup.
6. Run smoke tests on known bad cases.

---

## Smoke tests

Use these cards or equivalents:
- `226588ab` backlog/inbox hold case
- `1c6ab056` normal inbox candidate
- `6e5effc1` awaiting-human case
- `f5d6003b` awaiting-human case

Expected outcomes:
- held inbox card is skipped
- normal inbox card gets one queue item only
- awaiting-human cards are ignored by auto-drain
- no `pending -> done` fake processing path remains

---

## Rollback

If P0 introduces regressions:
- keep queue consumer enabled but revert dispatch-policy gating first
- keep canonical status normalization even if other changes roll back
- preserve the queue audit metadata checks because they are safety rails, not optional features
