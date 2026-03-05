# Mission Control Recovery - P1 Enforced Development Loop

**Status:** Draft - 2026-03-05  
**Goal:** Encode the required development workflow as a state machine instead of relying on chat discipline.

---

## Required workflow

Every development task must follow this loop:

1. Luna plans the task
2. Luan elaborates the implementation plan
3. Luna validates the plan
4. Luan executes and tests
5. Luna performs final validation

No development task may skip these steps.

---

## P1 design principles

1. Keep coarse `status` and add explicit `mc_phase`.
2. Make ownership explicit with `mc_phase_owner`.
3. Every Luna-owned phase must be claimable and time-bound.
4. `review` is reserved for Luna-owned phases.
5. `in_progress` is reserved for Luan-owned phases.
6. `awaiting_human` is terminal until cleared.
7. `inbox` is only intake, not direct execution.

---

## New fields

Required custom fields:
- `mc_workflow = dev_loop_v1 | direct_exec`
- `mc_phase`
- `mc_phase_owner = luna | luan | human`
- `mc_phase_state = pending | claimed | completed | rejected`
- `mc_loop_id`
- `mc_plan_artifact`
- `mc_validation_artifact`
- `mc_test_report_artifact`
- `mc_gate_reason`
- `mc_claimed_by`
- `mc_claim_expires_at`
- `mc_phase_retry_count`

Optional but useful:
- `mc_plan_version`
- `mc_phase_started_at`
- `mc_phase_completed_at`

---

## State machine

| Step | status | mc_phase | owner | Meaning |
| --- | --- | --- | --- | --- |
| Intake | `inbox` | `intake` | none | Card exists but has not entered the loop |
| Step 1 | `review` | `luna_task_planning` | Luna | Luna creates task spec and execution framing |
| Step 2 | `in_progress` | `luan_plan_elaboration` | Luan | Luan writes implementation plan |
| Step 3 | `review` | `luna_plan_validation` | Luna | Luna validates/rejects/asks for changes |
| Step 4 | `in_progress` | `luan_execution_and_tests` | Luan | Luan implements and runs tests |
| Step 5 | `review` | `luna_final_validation` | Luna | Luna performs QA and final decision |
| Human gate | `awaiting_human` | `awaiting_human_decision` | human | Human decision required |
| End | `done` | `done` | none | Work is complete |

---

## Transition rules

### Entry into the loop
- A development card starts as `inbox + intake`.
- Heartbeat must route it to Luna planning first, not to Luan execution.

### Step 1 -> Step 2
- Luna writes task spec.
- Luna updates:
  - `status = in_progress`
  - `mc_phase = luan_plan_elaboration`
  - `mc_phase_owner = luan`
  - `mc_plan_artifact` points to the task spec or brief

### Step 2 -> Step 3
- Luan submits implementation plan.
- Luan updates:
  - `status = review`
  - `mc_phase = luna_plan_validation`
  - `mc_phase_owner = luna`
  - `mc_plan_artifact` points to the generated plan

### Step 3 outcomes
- If approved:
  - `status = in_progress`
  - `mc_phase = luan_execution_and_tests`
- If revision needed:
  - back to `in_progress + luan_plan_elaboration`
- If human decision needed:
  - `status = awaiting_human`
  - `mc_phase = awaiting_human_decision`

### Step 4 -> Step 5
- Luan finishes implementation and tests.
- Luan updates:
  - `status = review`
  - `mc_phase = luna_final_validation`
  - `mc_test_report_artifact` points to the test report

### Step 5 outcomes
- If approved:
  - `status = done`
  - `mc_phase = done`
- If rejected but retryable:
  - `status = in_progress`
  - `mc_phase = luan_execution_and_tests`
  - `mc_phase_retry_count += 1`
- If human decision needed:
  - `status = awaiting_human`
  - `mc_phase = awaiting_human_decision`

---

## Runtime changes required

### P1.1 - Route development intake to Luna first

Current problem:
- inbox work is routed toward Luan dispatch directly.

Required change:
- `heartbeat-v3.py` must inspect `mc_workflow`.
- `dev_loop_v1` cards route to Luna planning first.
- only `direct_exec` cards may go straight to executor logic.

---

### P1.2 - Add a dedicated judge-loop worker

Current problem:
- review execution is still tied to the overloaded main session.

Required change:
- Introduce one dedicated worker for Luna-owned review phases.
- The worker may still wake Luna, but it must own:
  - claim/lease
  - timeout handling
  - MC transition writing
  - rejection/handoff semantics

Suggested targets:
- new `scripts/mc-judge-loop.sh` or `heartbeat-v3/scripts/judge-loop-worker.py`
- integrate with `heartbeat-v3/scripts/queue-consumer.py`

---

### P1.3 - Enforce claim/lease on review phases

Current problem:
- wakeups are fire-and-forget and can be ignored by the active session.

Required change:
- a Luna-owned phase cannot be redispatched if it is actively claimed.
- every claim writes:
  - `mc_claimed_by`
  - `mc_claim_expires_at`
  - `mc_phase_state = claimed`
- if claim expires, the worker can redispatch or escalate.

---

### P1.4 - Make artifacts first-class

Current problem:
- plans and QA decisions are sometimes visible only in chat history.

Required change:
- every phase writes an artifact path into the card.
- minimum artifacts:
  - task spec / planning brief
  - implementation plan
  - test report
  - final QA note

Possible storage:
- `plans/`
- `docs/`
- `artifacts/`

---

### P1.5 - Make review rejection deterministic

Current problem:
- review rejection exists, but the next transition is not always phase-aware.

Required change:
- rejection from `luna_plan_validation` returns to `luan_plan_elaboration`
- rejection from `luna_final_validation` returns to `luan_execution_and_tests`
- both preserve artifact references and increment the correct retry counter

---

### P1.6 - Update contracts and docs to match runtime

Required docs to align:
- `docs/agentic-loop-contract.md`
- `docs/agentic-governance-roadmap.md`
- `HEARTBEAT.md`
- `docs/a2a-loop-debug-guide.md`
- `memory/orchestration-state.json` conventions

Goal:
- runtime fields, docs and MC semantics must describe the same loop

---

## Acceptance criteria

P1 is complete when all are true:

- every `dev_loop_v1` task enters Luna planning before any Luan execution
- no development card moves directly from `inbox` to Luan implementation
- every review phase has explicit claim metadata
- every review phase either closes, requeues to the correct previous phase, or escalates to `awaiting_human`
- artifacts exist for plan, implementation validation, and final QA
- queue drain order is: `review` first, then `inbox` only when no active implementation is running

---

## Test matrix

1. New dev card in inbox.
   - Expected: routed to `review + luna_task_planning`
2. Luan submits plan.
   - Expected: card moves to `review + luna_plan_validation`
3. Luna rejects plan.
   - Expected: card returns to `in_progress + luan_plan_elaboration`
4. Luna approves plan.
   - Expected: card moves to `in_progress + luan_execution_and_tests`
5. Luan completes implementation.
   - Expected: card moves to `review + luna_final_validation`
6. Luna rejects final QA.
   - Expected: card returns to `in_progress + luan_execution_and_tests`
7. Human-gate case.
   - Expected: card moves to `awaiting_human + awaiting_human_decision` and stays there

---

## Rollout order

1. Add fields and transition guardrails.
2. Route intake by `mc_workflow`.
3. Add judge-loop worker and claim/lease.
4. Update rejection paths.
5. Update docs and operational runbooks.
6. Run replay tests on real cards.
