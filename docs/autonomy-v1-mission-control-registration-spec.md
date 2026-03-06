# Autonomy V1 Mission Control Registration Spec

**Status:** Draft for human review  
**Date:** 2026-03-05  
**Purpose:** define how to register autonomy plans, epics, specs, and executable tasks in Mission Control **without letting heartbeat drain planning work by accident**

## 1) Problem statement

Two current cards show the classification problem clearly:

- `6e5effc1...` (`Autonomy v1: Scheduler + QA runner + Project Epics...`)
  - `status = awaiting_human`
  - `mc_dispatch_policy = auto`
- `1c6ab056...` (`Research: Incorporate Paperclip / Builderz Mission Control / OpenAI Symphony learnings...`)
  - `status = inbox`
  - `mc_dispatch_policy = auto`

The first card is protected from drain only because of status. The second is currently auto-drain eligible even though it is planning/spec work.

The control rule must be:

> **Only leaf tasks that satisfy the executability contract may use `mc_dispatch_policy=auto`.**

## 2) Canonical policy model

### 2.1 Status remains operational
Use the existing canonical status set already recognized by `heartbeat-v3`:
- `inbox`
- `in_progress`
- `review`
- `awaiting_human`
- `done`
- `failed`
- `blocked`
- `stalled`
- `retry`

Do **not** invent a separate planning status if the runtime does not understand it.

### 2.2 Dispatch policy controls drain eligibility
Use:
- `auto`
- `backlog`
- `human_hold`

Interpretation:
- `auto`: eligible for automatic dispatch if the status also permits it
- `backlog`: executable later, but **not** auto-drained now
- `human_hold`: governance/spec/planning/blocked-by-decision work; never auto-drained until cleared

## 3) Executability contract

A card may be promoted to `mc_dispatch_policy=auto` only if all conditions below are true:

1. It is a **leaf task**, not a parent goal/epic/spec/review package.
2. It has a clearly identified execution owner (`agent` / assigned agent intent).
3. It defines verification checks or acceptance criteria that can be evaluated.
4. It defines expected output or artifact paths.
5. It is not waiting for a human/governance decision.
6. It is not merely carrying a plan/spec/analysis deliverable.
7. It does not require a workflow the runtime does not yet support.

If any of these is false, the card must not be `auto`.

## 4) Policy matrix

| Card type | Recommended status | `mc_dispatch_policy` | Auto-drain? | Notes |
|---|---|---|---|---|
| Goal / Epic parent | `awaiting_human` or `inbox` | `human_hold` | No | Governance object, not executable leaf work |
| Plan / Spec / Research planning card | `inbox` | `human_hold` | No | Can move to `awaiting_human` when pending operator decision |
| Approved but parked implementation card | `inbox` | `backlog` | No | Ready in principle, intentionally not being drained yet |
| Executable leaf task | `inbox` | `auto` | Yes | Must satisfy executability contract |
| Active execution | `in_progress` | `auto` | N/A | Linked to run/session proof |
| Luna review / QA gate | `review` | `auto` | Yes | Review wake must remain eligible |
| Human decision gate | `awaiting_human` | `human_hold` | No | Explicit stop state |

## 5) Registration pattern for `Autonomy v1`

### 5.1 Parent card
Use the existing `Autonomy v1` card as the parent governance object.

Recommended posture:
- `status = awaiting_human`
- `mc_dispatch_policy = human_hold`
- comment links to:
  - `docs/autonomy-roadmap-v1.md`
  - `docs/external-learnings-2026-03-05.md`
  - `docs/autonomy-v1-adoption-plan-2026-03-05.md`
  - `docs/autonomy-v1-mission-control-registration-spec.md`

### 5.2 Child planning cards
Examples:
- `Plan: Autonomy V1 - truth boundary hardening`
- `Plan: Autonomy V1 - MC visibility`
- `Plan: Autonomy V1 - workflow contract + run lineage`
- `Plan: Autonomy V1 - goal ancestry + budget scope`

Recommended posture while still planning:
- `status = inbox`
- `mc_dispatch_policy = human_hold`
- explicit description header: `PLANNING ONLY - NOT EXECUTABLE`

### 5.3 Approved but not yet released phase cards
When a planning card becomes approved implementation backlog but should not drain yet:
- keep `status = inbox`
- set `mc_dispatch_policy = backlog`

This signals:
- the work is real
- the work is not to be auto-dispatched yet
- the card may be released later by switching to `auto`

### 5.4 Executable leaf tasks
Only create `auto` cards when the work is actually executable.

Minimum content:
- acceptance criteria
- verification commands/checks
- expected artifacts
- owner agent / intended runtime path
- risk classification

## 6) Immediate recommendation for the two current cards

### 6.1 `Autonomy v1` (`6e5effc1...`)
Recommended next MC state:
- keep `status = awaiting_human`
- change `mc_dispatch_policy` from `auto` to `human_hold`

Rationale:
- this card is a governance/approval umbrella
- it should never be interpreted as leaf executable work

### 6.2 `Research: Incorporate ...` (`1c6ab056...`)
Recommended next MC state if it remains planning/spec work:
- keep `status = inbox`
- change `mc_dispatch_policy` from `auto` to `human_hold`

Alternative:
- if the research is approved and intentionally queued for later execution, move to `backlog`

## 7) What not to do

- Do not keep plan/spec cards in `review`; `review` wakes Luna/judge logic.
- Do not keep governance cards in `auto`; status-only blocking is fragile and semantically wrong.
- Do not use `backlog` for cards that are still being debated or need human sign-off; use `human_hold`.
- Do not promote a parent epic card to `auto`.

## 8) Test protocol

### 8.1 Synthetic-card dry run
Create one card for each case:
- planning card: `inbox + human_hold`
- parked implementation card: `inbox + backlog`
- approval gate: `awaiting_human + human_hold`
- review gate: `review + auto`
- executable leaf task: `inbox + auto`

Run:
- `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`

Expected:
- planning card does not drain
- parked implementation card does not drain
- approval gate does not drain
- review gate is considered before fresh inbox work
- executable leaf task remains eligible

### 8.2 Release protocol test
Take one synthetic implementation card through:
- `human_hold` -> `backlog` -> `auto`

Expected:
- no dispatch while held or parked
- eligibility appears only after promotion to `auto`
- the card can then enter the normal execution/review path

## 9) Decision rule

Yes, it **does** make sense to register the autonomy plan in Mission Control, but only as:
- governance card(s)
- planning/spec card(s)
- explicitly held backlog

It does **not** make sense to register the whole plan as `auto` work before the plan is decomposed into leaf executable tasks.
