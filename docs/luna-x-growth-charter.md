# Luna X Growth Canary Charter

**MC Task:** `f5e70391`  
**Status:** Draft v1 (Planning/Docs)  
**Updated:** 2026-03-08

## 1) Audit Objective

Create a conservative governance charter for **Luna X (@lunabardabot)** that enables safe, auditable autonomy while explicitly freezing growth-risking public actions until Matheus provides a manual “unfreeze” approval.

- Keep the canary operationally safe (no public social actions that could create reputation, legal, or platform-risk).
- Base decisions on available artifacts in `artifacts/reports/luna-x-growth/`.
- Make gaps explicit when data is missing, and define a minimal safe capture plan to close them.

## 2) Baseline & Current Snapshot (from artifacts)

### Canonical sources
- `artifacts/reports/luna-x-growth/baseline-latest.json`
- `artifacts/reports/luna-x-growth/profile-snapshot-latest.json`
- `artifacts/reports/luna-x-growth/scorecard-latest.json`
- `artifacts/reports/luna-x-growth/session-health-latest.json`
- `artifacts/reports/luna-x-growth/board-packet-latest.md`

### Current observed state
- Account: **@lunabardabot** (display: **Luna**)
- Baseline: **1 follower / 7 following**
- Snapshot: **1 follower / 7 following** (net delta **+0**)
- Session health: `ok`
- Suggested scorecard action: `adjust`
- Recent themes/formats/posts: **none captured**
- Guardrail flags: **none**

### Initial audit findings
1. Identity and baseline are stable, but activity signal is low (no recent posts/themes captured).
2. No guardrail violations have been detected in the latest artifacts.
3. Governance state shows active project/milestone, but multiple repair bundles remain open, including this audit task.

## 3) Freeze Growth Charter (Mandatory)

Until manually lifted by Matheus, the following actions are **frozen**:

- **No follower growth campaigns** (paid ads, follow-unfollow loops, mass follow-back actions, giveaway loops).
- **No outbound acquisition automations** (DMs for growth solicitation, unsolicited reply-chains for conversion).
- **No public positioning changes** (bio/profile identity/mission edits, tone shifts, or high-risk topic pivots).
- **No claim-driven posting** about trading profits or guaranteed financial outcomes.
- **No account actions intended solely to maximize vanity metrics**.

Allowed during freeze:
- Safe planning documents, internal analysis, and capture/monitoring.
- Passive, non-manipulative content if explicitly whitelisted in approved pillars (see section 4).

## 4) Content & Positioning Constraints (while frozen)

### Approved pillars (must apply to every allowed post/reply)
- Crypto market structure and microstructure insights.
- Build-in-public notes about OpenClaw tooling/experiments.
- Data-backed observations with clear uncertainty/final caveats.
- Reactions to live market developments only with verifiable evidence.

### Disallowed without approval
- Political partisanship, harassment, flame-war behavior.
- Financial advice/return guarantees.
- Unverified anecdotal “wins/losses” claims.
- Clickbait or manipulative framing tied only to social metrics.

## 5) Audit/Steering Gates

### Escalate immediately to Chairman on:
- Any guardrail flag triggered by safety checks.
- Negative public reaction trending beyond normal disagreement.
- Platform account issues (login/session restrictions, temporary lock, suspicious activity flags).
- Any observed identity/persona mismatch.

### Decision outcomes
- **continue:** evidence consistent, no flags, objective met and risk budget respected.
- **adjust:** minor content/process tweaks; keep within current charter and freeze constraints.
- **pause:** uncertain data quality or policy ambiguity; stop public actions, continue monitoring.
- **unfreeze only on explicit approval:** requires manual review of this charter + latest scorecard + snapshot diff.

## 6) Minimal Daily Evidence Loop

Every daily run should produce (at minimum):
1. `session-health-latest.json` (session state)
2. `profile-snapshot-latest.json` (followers/following)
3. `scorecard-latest.json` (+ action recommendation)
4. `board-packet-latest.md` (runtime + decisions context)
5. Delta summary written into audit notes

## 7) Evidence Contract (required)

All autonomous planning decisions for this account MUST cite artifacts above as primary evidence.

## 8) Missing-Artifact Fallback (safe plan)

If any required artifact is missing at runtime, do not proceed with growth actions:

- Mark charter status as `PAUSED (evidence gap)`.
- Create TODO placeholders in this document with the missing file list.
- Run minimal safe capture plan:
  1) session-state check (`session-health-latest.json`)
  2) profile baseline/snapshot refresh
  3) scorecard recompute from snapshot vs baseline
  4) board packet readback
- Resume only after artifacts are present and reviewed.

## 9) Exit Criteria to Lift Freeze

The growth freeze remains until all conditions are met:

1. At least one full evidence cycle is complete for 7 consecutive days.
2. Scorecard trend is stable or improving **and** guardrail flags remain clear.
3. Matheus explicitly approves lift with a timeboxed scope (example: `7d`/`M1`).

## 10) Audit Notes

- This is the first-draft governance document for the Luna X growth canary and should be treated as a human-review gate, not a production policy.
- Scope is intentionally narrow: no public actions requested; planning/doc updates only.
- Task-specific source: MC repair `f5e70391`.
