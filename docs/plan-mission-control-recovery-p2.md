# Mission Control Recovery - P2 Hardening and Observability

**Status:** Draft - 2026-03-05  
**Goal:** Make the loop measurable, replayable, and resilient under real production load.

---

## Why P2 exists

P0 makes the system safe.
P1 makes the loop enforceable.
P2 makes it reliable over time.

Without P2, the same classes of failure will come back through:
- silent drift
- session bloat
- queue corruption that is only visible after hours
- regressions that only appear in live cron behavior

---

## Workstreams

### P2.1 - Replay harness for known incidents

Create deterministic replay cases for:
- repeated inbox dispatch (`226588ab`)
- repeated queue dispatch (`8cc49d51` pattern)
- review listed but not closed in the main session
- queue item moved to `done/` without dispatch result
- `review -> awaiting_human` semantic mismatch

Targets:
- new integration tests under `tests/` or `heartbeat-v3/tests/`
- fixture snapshots in `state/` or `artifacts/`

Acceptance:
- every known Mar 5 incident has a replayable fixture and expected outcome

---

### P2.2 - Queue and transition observability

Add metrics and periodic audits for:
- queue items written
- queue items deduped
- queue items completed by dispatcher
- queue items invalidly completed
- phase transitions per task
- average time in `review`
- average time in `awaiting_human`
- duplicate dispatch attempts

Suggested outputs:
- log summaries
- daily report in `docs/` or `artifacts/`
- optional dashboard extension later

Acceptance:
- duplicate dispatch rate and invalid queue completion count are measurable

---

### P2.3 - Main session hygiene

Use our lessons and OpenClaw session model constraints to prevent the main Luna session from becoming the universal automation sink.

Required changes:
- enforce session-size alerts earlier
- rotate/archive aggressively when thresholds are crossed
- keep automation summaries compact
- avoid dumping queue processing into the main chat session

Targets:
- `scripts/session-compact-sentinel.sh`
- `scripts/session-smart-compact.py`
- `docs/slos.md`

Acceptance:
- main session does not exceed agreed size/age thresholds unnoticed
- cron-originated summaries are compact and auditable

---

### P2.4 - Consolidate orchestration logic into testable modules

Current problem:
- critical control logic is spread across bash, Python-in-bash, reminders, and direct chat behavior.

Required change:
- move control-loop logic behind one testable module boundary
- prefer Python for transition logic, dedup, leasing, and queue result validation
- keep shell as thin entrypoints only

Targets:
- `heartbeat-v3/scripts/heartbeat-v3.py`
- `heartbeat-v3/scripts/queue-consumer.py`
- `scripts/mc-watchdog.sh`
- `scripts/auto-qa-reviewer.sh`
- possibly a new `scripts/mc_lib/` or equivalent package

Acceptance:
- transition logic is unit-testable without replaying full shell flows

---

### P2.5 - Operational SLOs and runbooks

Define SLOs for:
- review closure latency
- duplicate queue write rate
- inbox redispatch regression rate
- main session size
- stale claim recovery time

Required docs:
- update `docs/slos.md`
- add runbook for queue corruption / judge-loop stuck / claim timeout

Acceptance:
- on-call style diagnosis can be done from runbooks and metrics, not only from transcript archaeology

---

### P2.6 - Optional event bridge improvements

Constraint:
- do not assume OpenClaw post-session-end hooks will solve this problem.

Allowed improvements:
- Mission Control webhooks if available on the MC side
- more precise `system event` usage for wakeups
- tighter integration between queue writes and worker claims

Not allowed as a design dependency:
- unsupported gateway hooks
- direct session-end hook assumptions

Acceptance:
- polling remains a valid fallback path even if eventing is improved

---

## Acceptance criteria

P2 is complete when all are true:

- every high-severity failure mode from Mar 5 has a replay test
- duplicate dispatch is observable and alertable
- main session growth is bounded and monitored
- transition logic is testable outside chat transcripts
- runbooks exist for queue corruption, claim timeout, review stall, and session bloat

---

## Suggested milestones

### P2a
- replay harness
- queue audit metrics
- session hygiene thresholds

### P2b
- orchestration logic consolidation
- SLO reporting
- runbooks

### P2c
- optional MC webhook bridge and richer observability

---

## Definition of done

Mission Control recovery is truly complete only when:
- P0 safety rails are in place
- P1 loop is enforced by code
- P2 can prove, measure, and replay the behavior
