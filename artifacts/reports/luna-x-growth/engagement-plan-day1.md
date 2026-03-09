# Luna X Canary — Day-1 Engagement Plan

**Status:** Draft v1 (docs/planning only)  
**Date:** 2026-03-09  
**Scope:** Internal operator plan for the first controlled engagement day of the Luna X canary. **No public actions on X are authorized by this document.**

## 1) Purpose

Define a conservative, auditable day-1 operating plan for Luna X (`@lunabardabot`) under the current growth freeze.

This plan converts the charter, baseline, profile snapshot, content pillars, and draft distribution map into a step-by-step operator runbook that can be executed **as documentation/capture/planning only**.

## 2) Source artifacts used

Primary evidence set reviewed:
- `docs/luna-x-growth-charter.md`
- `artifacts/reports/luna-x-growth/content-pillars.md`
- `artifacts/reports/luna-x-growth/distribution-map.md`
- `artifacts/reports/luna-x-growth/baseline-latest.md`
- `artifacts/reports/luna-x-growth/baseline-latest.json`
- `artifacts/reports/luna-x-growth/profile-snapshot-latest.md`
- `artifacts/reports/luna-x-growth/profile-snapshot-latest.json`

## 3) Current operating context

### Baseline and snapshot
- Handle: `@lunabardabot`
- Display name: `Luna`
- Session state: `ok`
- Followers: `1`
- Following: `7`
- Net follower delta vs baseline: `0`

### Evidence quality
- `recent_posts`: empty
- `recent_themes`: empty
- `recent_formats`: empty
- Distribution map exists, but remains **draft-level** and does **not** yet include a validated target-account list.

### Planning implication
Day 1 should be treated as a **readiness + evidence-shaping day**, not a growth or public-engagement day. Because post history and audience mapping are incomplete, the operator should prepare the system for safe future engagement while preserving the freeze.

## 4) Day-1 objective

By the end of day 1, the operator should have:
1. Confirmed that the latest evidence artifacts are internally consistent.
2. Converted broad pillars into a small set of approved candidate engagement themes.
3. Prepared a vetted watchlist/template structure for future engagement targets.
4. Logged go/no-go findings for later public execution review.

**Non-objective for day 1:** posting, replying, liking, following, profile edits, or any other public X action.

## 5) Allowed action classes

Allowed on day 1:
- Artifact review and consistency checks
- Internal planning notes
- Candidate theme selection from approved pillars
- Draft watchlist construction using placeholders or pre-vetted categories
- Risk review and stop/go documentation
- Evidence capture gap logging
- Internal checklist completion

Allowed only if explicitly documented as non-public/internal:
- Preparing sample reply angles in a doc
- Preparing candidate account tables without contacting them
- Preparing decision thresholds for day 2+

## 6) Forbidden actions

Forbidden on day 1:
- Any public post on X
- Any public reply, quote-post, repost, or like
- Any follow/unfollow action
- Any DM or outreach
- Any bio, display name, avatar, banner, pinned-post, or positioning edit
- Any financial performance claim drafting for publication
- Any mass target-list building from vanity metrics alone
- Any action designed to increase follower count while the freeze remains active

## 7) Preconditions

Before continuing with any day-1 step, all of the following must be true:
- Charter still indicates growth freeze is in effect.
- Latest baseline and profile snapshot are present.
- Session state is `ok` in latest artifacts.
- No guardrail flags are known.

### Distribution-map precondition
The current distribution map is usable only as a **draft directional reference**. Until it contains validated account examples and risk-ranked targets, treat all segment references as placeholders for planning only.

## 8) Step-by-step day-1 sequence

### Step 1 — Confirm artifact integrity
Review the latest baseline and snapshot artifacts side by side.

**Operator action**
- Verify handle, display name, followers, following, and session state match across markdown/json artifacts.
- Note any mismatch in a day-1 notes section before proceeding.

**Expected evidence**
- Confirmation that baseline and snapshot both show `@lunabardabot`, `1 follower`, `7 following`, `session ok`.
- Short note stating whether artifacts are consistent.

### Step 2 — Reconfirm freeze and scope
Re-read the charter and restate the day-1 operating boundary.

**Operator action**
- Record that day 1 is planning-only.
- Restate that no public X actions are authorized.

**Expected evidence**
- One written sentence in operator notes confirming freeze status.
- One written sentence confirming no public actions will be taken.

### Step 3 — Select provisional engagement themes
Translate the content pillars into a small day-1 planning shortlist.

**Operator action**
- Select 2-3 pillar-aligned themes for future engagement planning, for example:
  - product/protocol signal & experimentation
  - practical AI-agent workflows / engineering process
  - trading/automation transparency updates (strictly non-promissory)
- Reject any theme that would require unverifiable claims or off-topic personality posting.

**Expected evidence**
- A list of 2-3 provisional themes with one sentence each explaining why they are aligned with the charter.

### Step 4 — Build a draft watchlist structure
Use the draft distribution map to create an internal watchlist template.

**Operator action**
- Create or update an internal table with columns such as:
  - segment
  - candidate account
  - why relevant
  - risk notes
  - proof link/post link
  - status (`placeholder`, `needs validation`, `approved for review`)
- If no validated candidate accounts are available, leave `candidate account` rows as placeholders by segment.

**Expected evidence**
- Internal watchlist/table draft exists.
- Placeholder rows are clearly marked if no concrete accounts have been validated.

**Explicit precondition**
Do not mark any account as approved unless there is a concrete evidence link and a reason it matches both the pillars and the low-risk segment criteria.

### Step 5 — Define candidate action inventory for future days
Prepare the allowed future engagement motions without executing them.

**Operator action**
- Draft examples of future allowed engagement classes, such as:
  - evidence-backed reply
  - clarifying technical question
  - quote/restatement with added technical insight
- Add one sample structure for each action class, but keep it unpublished.

**Expected evidence**
- A short internal list of sample action structures tied to approved pillars.
- Each sample labeled `draft only / not approved for posting`.

### Step 6 — Run risk screen on candidate themes and targets
Check whether planned themes or placeholder targets violate the charter.

**Operator action**
- Remove anything that looks promotional, manipulative, generic-growth oriented, or claim-heavy.
- Remove segments/accounts associated with drama, partisan discourse, or financial-advice signaling.

**Expected evidence**
- A yes/no risk screen result for each provisional theme or placeholder segment.
- A short reason for any rejected item.

### Step 7 — Produce a day-1 go/no-go note
Write the final day-1 outcome for operator review.

**Operator action**
- Summarize what is ready, what remains blocked, and what evidence is still missing.
- Assign one of these outcomes for next-day planning: `continue planning`, `adjust`, or `pause`.

**Expected evidence**
- One short closing note with:
  - readiness status
  - blockers
  - recommended next action

## 9) Evidence requirements per action

| Action | Minimum evidence required | Block if missing? |
|---|---|---|
| Confirm artifact integrity | Matching handle/session/follower counts across baseline + snapshot markdown/json | Yes |
| Reconfirm freeze and scope | Charter review note explicitly stating planning-only status | Yes |
| Select provisional themes | Themes mapped back to approved pillars from `content-pillars.md` | Yes |
| Build draft watchlist structure | Segment rationale from `distribution-map.md`; placeholders clearly marked if unvalidated | No, but must remain placeholder-only |
| Define future action inventory | Each action class labeled draft-only and tied to a safe pillar | Yes |
| Risk screen | Written pass/fail reasoning for each planned theme/segment | Yes |
| Produce day-1 go/no-go note | Summary of readiness, missing evidence, and recommendation | Yes |

## 10) Stop conditions

Stop day-1 work immediately if any of the following occurs:
- Baseline and snapshot no longer match on identity or account counts without explanation.
- Session state is not `ok` in the latest artifacts.
- A new guardrail flag appears.
- The operator is about to take any public X action.
- The only way to proceed would require guessing target accounts, inventing evidence, or inferring posting history that is not captured.
- Distribution-map assumptions start being treated as validated targets without proof links.

## 11) Lightweight operator checklist

Use this as the minimum completion checklist for day 1.

- [ ] Reviewed charter and reconfirmed growth freeze.
- [ ] Verified baseline-latest and profile-snapshot-latest are internally consistent.
- [ ] Recorded current state: `1 follower / 7 following / session ok`.
- [ ] Selected 2-3 provisional themes from approved pillars.
- [ ] Built or updated a draft watchlist structure.
- [ ] Marked all unvalidated targets as placeholders.
- [ ] Drafted future action classes without publishing anything.
- [ ] Ran risk screen on themes/segments.
- [ ] Wrote a go/no-go day-1 note.
- [ ] Confirmed no public X actions were taken.

## 12) Recommended day-1 outcome

**Recommended outcome:** `continue planning`

Reason:
- Identity/session evidence is stable.
- No guardrail issues are visible in the reviewed artifacts.
- However, recent post history and validated target-account evidence remain incomplete, so the safe next move is to continue planning/capture preparation rather than perform public engagement.

## 13) Operator handoff note

If day 2 planning is requested, the next useful artifacts would be:
1. A validated target-account list per distribution segment.
2. Safe capture of recent post history for Luna.
3. Examples of pillar-matching third-party posts suitable for future evidence-backed replies.

Until then, this plan remains a **non-public day-1 readiness runbook** under the active freeze.