# Autonomy V1 Adoption Plan 2026-03-05

**Status:** Draft for human review  
**Purpose:** adopt the recommended ideas from Paperclip, builderz-labs/mission-control, and OpenAI Symphony **without replacing the current stack**  
**Server repo:** `/home/openclaw/.openclaw/workspace`  
**Related MC tasks:**
- `6e5effc1-a6bf-4d73-9079-74ba9dc52069`
- `1c6ab056-9cd6-484f-8eb5-96ec98579379`

## 1) Assumptions

- We are keeping the current split between:
  - `openclaw-mission-control/`
  - `heartbeat-v3/`
  - `memory/orchestration-state.json`
- This plan is about **contract tightening and operational evolution**, not replatforming.
- Planning/governance cards must remain non-executable until explicitly released.

## 2) Target architecture after adoption

### 2.1 Control-plane boundary
- Mission Control owns operational task state, approvals, comments, delivery lineage, and operator visibility.
- `heartbeat-v3` owns deterministic drain, retry, QA wake, and queue/reconciliation logic.
- `memory/orchestration-state.json` owns A2A routing/governance semantics and delegation audit.

### 2.2 New concepts to introduce
- goal ancestry: `goal -> epic -> milestone -> leaf task`
- delivery substate: `queued -> dispatched -> linked -> in_progress -> review -> done`
- proof-of-work artifacts: plan/report/validation/test evidence per run
- run lineage: `task -> attempt/run -> session -> artifacts`
- explicit MC registration policy to separate planning cards from executable work

## 3) Workstreams

### W1 - Truth boundary hardening
**Objective:** formalize ownership between MC, heartbeat, and orchestration-state.

**Expected outputs:**
- ownership matrix for state fields/events
- documented writer/reader responsibilities
- no ambiguous field that can be written by multiple loops without reconciliation

**Validation:**
- every field/event in the ownership matrix has one canonical writer
- recovery path after restart references the same canonical object every time

**How to test:**
- tabletop review over the matrix
- incident replay using an orphaned task and a review task

### W2 - MC real-time observability uplift
**Objective:** pull more execution visibility into Mission Control.

**Expected outputs:**
- activity feed events for dispatch, claim, retry, QA, completion, escalation
- session/run lineage visible from the card context
- fewer operator steps that require reading raw heartbeat logs

**Validation:**
- a single card timeline explains how the task moved from queued to done
- dispatch/review/retry decisions are reconstructible from MC alone

**How to test:**
- simulate a leaf task lifecycle and verify the event timeline in MC
- compare MC timeline vs heartbeat logs for the same run

### W3 - Workflow contract + run isolation
**Objective:** adopt Symphony-style workflow discipline without abandoning the current stack.

**Expected outputs:**
- explicit workflow contract version for `direct_exec` and `dev_loop_v1`
- run/attempt identity and proof-of-work requirements
- restart/retry behavior tied to run lineage

**Validation:**
- retries create or reference a distinct attempt lineage
- no task reaches `done` without proof-of-work or an explicit human override

**How to test:**
- simulate dispatch success, lost session link, retry, and final review
- verify artifact paths and lineage fields remain consistent

### W4 - Goal ancestry + budget scope
**Objective:** bring strategic structure to the autonomy backlog without building a new planning system.

**Expected outputs:**
- explicit parent/child semantics for autonomy work
- budget scope by epic/project/agent/workflow
- governance cards separated from executable cards

**Validation:**
- leaf cards can be traced back to an epic/goal
- budget checks can be evaluated at more than one level

**How to test:**
- create one synthetic epic with two milestones and three leaf tasks
- verify linkage, dependencies, and budget metadata

### W5 - MC anti-drain registration policy
**Objective:** stop planning/spec/governance work from entering the auto-dispatch path.

**Expected outputs:**
- policy matrix for `human_hold` / `backlog` / `auto` / `awaiting_human`
- readiness checklist for a card to become executable
- examples for `epic`, `phase card`, `spec`, `leaf task`, `review`

**Validation:**
- a planning card cannot be auto-drained by heartbeat
- only cards that satisfy the execution contract can be promoted to `auto`

**How to test:**
- create synthetic cards across all policy states
- run heartbeat dry-run and verify expected eligibility

## 4) Phased rollout

### Phase A - Planning and contract freeze
**Scope:** docs only; no runtime behavior changes.

**Deliverables:**
- `docs/external-learnings-2026-03-05.md`
- `docs/autonomy-v1-mission-control-registration-spec.md`
- updated `docs/autonomy-roadmap-v1.md`

**Acceptance criteria:**
- current MC tasks are cross-referenced explicitly
- truth boundaries and anti-drain rules are documented
- roadmap reflects goal ancestry, delivery substate, and proof-of-work

**Validation:**
- human review of all three docs
- no contradiction with `heartbeat-v3/docs/ARCHITECTURE.md`

### Phase B - Mission Control visibility
**Scope:** Mission Control API/backend/frontend changes only.

**Expected changes:**
- add activity events / timeline improvements
- expose run/session linkage and delivery substate
- keep existing heartbeat semantics intact

**Acceptance criteria:**
- the operator can inspect dispatch/review/retry lineage from MC
- event model remains compatible with current crons/scripts

**Validation:**
- MC integration tests for event ingestion and timeline rendering
- manual trace on one synthetic task lifecycle

### Phase C - Heartbeat workflow contract and run lineage
**Scope:** `heartbeat-v3` + related tests.

**Expected changes:**
- workflow contract normalization
- attempt/run identity
- proof-of-work enforcement hooks
- stronger recovery/retry semantics

**Acceptance criteria:**
- retry path is deterministic
- `linked` proof is recorded before task is considered active
- QA/review handoff points are explicit

**Validation:**
- unit tests for workflow state transitions
- dry-run scenarios for orphan/retry/review-first ordering

### Phase D - Goal ancestry and budget-aware planning
**Scope:** planner-side docs/fields/MC metadata first, then optional UI/runtime support.

**Expected changes:**
- epic/milestone/leaf conventions
- budget scope metadata
- weekly governance review model

**Acceptance criteria:**
- project work is decomposed without flattening everything into inbox cards
- budget scope is observable at epic and workflow level

**Validation:**
- synthetic epic decomposition exercise
- review against current `Autonomy v1` task and Phase 2 cards

### Phase E - Consolidation and incident replay
**Scope:** end-to-end validation.

**Expected changes:**
- final ownership matrix
- incident replay checklist
- canonical operator runbook for autonomy rollout

**Acceptance criteria:**
- recurring failure modes are covered by replay/test cases
- operator can explain the lifecycle of a task using docs + MC + heartbeat artifacts

**Validation:**
- replay: orphaned session, stalled review, duplicate dispatch prevention
- dry-run + canary validation before any full re-enable of aggressive auto-drain

## 5) Component-by-component change map

### 5.1 `openclaw-mission-control/`
Adopt from builderz:
- better real-time feed and operator-facing visibility
- clearer card timeline for execution/review/approval

Do not do yet:
- replace the product
- move heartbeat semantics into ad hoc UI-only logic

### 5.2 `heartbeat-v3/`
Adopt from Symphony:
- workflow-contract discipline
- run lineage and proof-of-work
- deterministic recovery boundaries

Do not do yet:
- introduce a third source of truth for execution state
- bypass Mission Control as the operational task ledger

### 5.3 `memory/orchestration-state.json`
Adopt from Paperclip only where it improves structure:
- clear ancestry/governance linkage
- budget/governance metadata where useful

Do not do yet:
- overload orchestration-state with operational task state that belongs in MC

## 6) Explicit non-goals

- replacing the stack with Paperclip, builderz MC, or Symphony
- creating a single monolithic source of truth for every domain
- building a full company-operating abstraction in v1
- expanding auto-dispatch volume before card executability rules are tightened

## 7) Recommended immediate follow-up in Mission Control

Without changing runtime behavior yet, the safest immediate MC posture is:
- keep `Autonomy v1` as a governance card (`awaiting_human` + `human_hold` once updated)
- move the linked research card out of `auto` if it remains planning/spec work
- only create `auto` cards for leaf implementation work with explicit checks, artifacts, and agent ownership

## 8) Minimum validation pack

### Planning package validation
- review the three planning docs
- confirm no conflict with `heartbeat-v3/docs/ARCHITECTURE.md`
- confirm the MC registration rule matches `mc_control.py`

### Dry-run validation
- `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`

Expected:
- `review` has priority over `inbox`
- `awaiting_human` never drains
- `human_hold` never drains
- `backlog` never drains
- `auto` leaf work remains eligible
