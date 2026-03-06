# External Learnings 2026-03-05 - Autonomy V1 + Phase 2

**Status:** Draft for human review  
**Date:** 2026-03-05  
**Scope:** `/home/openclaw/.openclaw/workspace` (server repo)  
**Related MC tasks:**
- `6e5effc1-a6bf-4d73-9079-74ba9dc52069` - `Autonomy v1: Scheduler + QA runner + Project Epics (Roadmap + Implementation Plan)`
- `1c6ab056-9cd6-484f-8eb5-96ec98579379` - `Research: Incorporate Paperclip / Builderz Mission Control / OpenAI Symphony learnings into Autonomy v1 + Phase 2`

## 1) Snapshot of the current system

The server repo currently has three distinct control surfaces:

1. `openclaw-mission-control/`
   - operational task system, approvals, comments, board-level visibility
2. `heartbeat-v3/`
   - dispatch, claims, queue lifecycle, retries, health-gated drain, QA/review wake logic
3. `memory/orchestration-state.json`
   - source of truth for A2A handoffs, routing, promotion, delegation audit, review loop metadata

This is not a bug by itself. The main design problem is not "too many components"; it is **blurred ownership between operational task state and orchestration/governance state**.

## 2) Cross-check against the current Mission Control tasks

### 2.1 `Autonomy v1` (`6e5effc1...`)

Current snapshot:
- `status = awaiting_human`
- `mc_dispatch_policy = auto`
- `mc_workflow = direct_exec`
- card intent = planning/governance, not executable leaf work

Assessment:
- Safe from automatic drain **because the status is `awaiting_human`**.
- Semantically inconsistent **because `mc_dispatch_policy=auto` says the opposite of the card's real intent**.
- This card should be treated as a **parent governance card / approval gate**, not as dispatchable work.

### 2.2 `Research: Incorporate ... into Autonomy v1 + Phase 2` (`1c6ab056...`)

Current snapshot:
- `status = inbox`
- `mc_dispatch_policy = auto`
- `mc_workflow = direct_exec`
- deliverable = planning docs / roadmap patch / design notes

Assessment:
- This card is **currently eligible for heartbeat auto-drain**.
- That is the wrong classification for planning/spec work.
- If kept in MC, it should move to `human_hold` until explicitly converted into leaf implementation work.

## 3) What to adopt from each external project

### 3.1 Paperclip

Adopt:
- explicit **goal ancestry** above the task layer
- **budget scope** by agent/workflow/project, not only per task
- planning objects that distinguish strategic work from executable work

Do not adopt now:
- full org-chart / "company OS" abstraction
- heavy business-operating-model semantics that do not improve dispatch correctness

Why:
- The biggest Paperclip gain for this repo is not org-chart UX; it is **keeping task execution anchored to goal hierarchy and budget limits**.

### 3.2 builderz-labs/mission-control

Adopt:
- real-time activity/feed/session visibility inside Mission Control
- clearer separation between board/task UI and execution telemetry
- operator-friendly view of execution lineage and review state

Do not adopt now:
- wholesale replacement of the current Mission Control
- moving all heartbeat logic into the product before the semantics are stabilized

Why:
- The main gain here is **observability inside the control plane**, not a full platform migration.

### 3.3 OpenAI Symphony

Adopt:
- per-workflow contract (`WORKFLOW.md`-like discipline)
- **run isolation / attempt lineage** (`task -> run -> session -> artifacts`)
- proof-of-work + explicit verification artifacts as first-class outputs

Do not adopt now:
- tracker-centric orchestration as the dominant source of truth
- collapsing A2A governance into a minimal runner model

Why:
- Symphony is strong where this repo is currently weakest: **clean workflow contracts, isolated runs, restart recovery, and proof-of-work discipline**.

## 4) Mapped decisions for `Autonomy v1`

### 4.1 Truth boundaries (keep them explicit)

Recommended boundary:
- Mission Control = source of truth for operational task state, approvals, comments, delivery trace, run lineage
- `memory/orchestration-state.json` = source of truth for A2A routing, delegation, promotion, review-loop audit, governance semantics
- `heartbeat-v3` = deterministic executor/reconciler of those contracts

### 4.2 Delivery state machine (adopt from builderz, adapted locally)

Do **not** replace canonical MC statuses with a new status vocabulary.
Instead, add an **orthogonal delivery substate** tracked via custom fields/comments/artifacts:

`queued -> dispatched -> linked -> in_progress -> review -> done`

Interpretation:
- `queued`: approved leaf work exists but has not been dispatched
- `dispatched`: dispatch requested, waiting for proof
- `linked`: `mc_session_key` or equivalent proof of execution has been captured
- `in_progress`: active execution confirmed
- `review`: waiting for Luna/judge validation
- `done`: accepted with proof-of-work

### 4.3 Proof-of-work / run artifacts (adopt from Symphony)

Each auto-dispatchable leaf task should produce:
- execution artifact (`plan`, `diff`, `report`, or `result brief`)
- verification artifact (`test log`, `validation artifact`, or equivalent)
- run identity (`run_id` / attempt lineage)
- link to runtime proof (`mc_session_key` or equivalent)

A task should not become `done` without proof-of-work or an explicit human override.

### 4.4 Goal ancestry and budget scope (adopt from Paperclip)

Autonomy V1 should treat project structure as:
- `goal`
- `epic`
- `milestone`
- `leaf task`

And cost governance as:
- budget by `agent`
- budget by `workflow`
- budget by `epic/project`

This should remain lightweight in v1. The immediate goal is traceability, not a new planning product.

## 5) Mapping to the existing roadmap and Phase 2

### 5.1 `Autonomy v1`

The current `docs/autonomy-roadmap-v1.md` already covers:
- scheduler/capacity
- QA loop
- epics/projects
- MC integration
- recovery
- guardrails

The missing pieces are:
- explicit goal ancestry
- orthogonal delivery substate
- proof-of-work artifact contract
- MC anti-drain policy for planning/spec cards
- clearer distinction between governance cards and executable leaf cards

### 5.2 Phase 2A / 2B / 2C

Recommended mapping:
- **Phase 2A:** transactional dispatch + delivery substate + linked session proof
- **Phase 2B:** proof-of-work + QA_HANDOFF + validation artifacts
- **Phase 2C:** health-gated dispatch + capacity governor + budget-aware throttling

## 6) Expected deliverables for adoption

1. `docs/autonomy-v1-adoption-plan-2026-03-05.md`
   - phased adoption plan with scope, expectations, validation, and test protocol
2. `docs/autonomy-v1-mission-control-registration-spec.md`
   - how to register governance/plan cards in MC without accidental heartbeat drain
3. `docs/autonomy-roadmap-v1.md`
   - updated to reference the external learnings and tighten the MC integration contract

## 7) Acceptance criteria for the planning package

This planning package is acceptable when:
- the distinction between **governance card** and **executable leaf task** is explicit
- `Autonomy v1` and the linked research work are mapped into the roadmap without semantic drift
- the anti-drain rule is documented in MC terms (`human_hold`, `backlog`, `awaiting_human`, `review`)
- every proposed adoption item includes at least one validation path
- no recommendation requires immediate infra replacement

## 8) Validation checklist

### Document validation
- `docs/autonomy-roadmap-v1.md` references the external learnings and the MC registration rule
- the registration spec includes a policy matrix and dry-run test protocol
- the adoption plan maps changes by component: `openclaw-mission-control`, `heartbeat-v3`, `memory/orchestration-state.json`

### Operational validation (before implementation)
- create synthetic cards for `human_hold`, `backlog`, `awaiting_human`, `review`, and `auto`
- run:
  - `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`
- expected:
  - `human_hold` cards do not drain
  - `backlog` cards do not drain
  - `awaiting_human` cards do not drain
  - `review` is considered before fresh `inbox`
  - only true leaf tasks marked `auto` remain eligible for dispatch
