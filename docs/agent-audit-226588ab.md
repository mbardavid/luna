# Agent Audit — Task 226588ab-690e-4349-9cf9-67b31cac64b3

**Date:** 2026-03-05  
**Scope:** main/Luna, Luan, crypto-sage, quant-strategist, dispatcher  
**Status:** plan_submitted

---

## 1) Per-agent summary

### main (Luna) — role: orchestration + autonomy
- **Strengths**
  - Mature operational baseline in `AGENTS.md` (A2A lifecycle, MC lifecycle gates, QA protocol, anti-footgun for gateway/model changes, trusted channels in `IDENTITY.md`).
  - Stronger safety culture (`TOOLS.md`, `HEARTBEAT.md`, `MEMORY.md`) with explicit anti-restart and message rules.
  - Extensive incident capture and lessons; active task logging exists.
- **Current gaps**
  - `active-tasks.md` contains stale/compact entries (`auto-compact` markers + duplicated backlog notes) indicating weak maintenance discipline.
  - A2A/QA lifecycle is documented, but adoption depends on consistent execution by delegated agents (gaps observed downstream).
  - Some assumptions still rely on shared runtime defaults (e.g., `openclaw.json` trust/binding context) without lightweight agent-local summaries.

### Luan — role: code execution + reviews
- **Strengths**
  - Very complete identity/memory stack (`SOUL.md`, `IDENTITY.md`, `USER.md`, `AGENTS.md`, `TOOLS.md`, `memory/*`).
  - Explicit two-phase protocol for medium-risk tasks, strict completion format, and metrics/metrics.jsonl requirement in AGENTS.
  - Good failure-aware conventions (read lessons, evidence-first, no code without tests).
- **Current gaps**
  - No immediate visible execution-cycle issue, but heavy operational burden from inconsistent cross-agent inputs (e.g., stale parent tasks, noisy task specs) still impacts speed.
  - No explicit rule forcing periodic sync of “trusted channels” assumptions from main into Luan context.

### crypto-sage — role: blockchain executor
- **Strengths**
  - Domain-specific dual-plane control/execution model and strict completion protocol in `AGENTS.md`.
  - Good operational safety guardrails (default dry-run, policy/risk checks, schema validation, post-tx terminal-state requirement).\n  - Memory conventions are present (`active-tasks`, `lessons`, `workflow-registry`, daily logs).
- **Current gaps**
  - `USER.md` is generic; not fully tailored to persistent human preferences.
  - `Memory cadence` misses today/yesterday on 2026-03-05 (latest at 2026-03-02), likely reducing “today+yesterday” startup context quality.
  - Cross-agent protocol propagation exists but depends on manual diligence; no explicit check that “channel trust assumptions” from main are loaded in every run.

### quant-strategist — role: market strategy/decision support
- **Strengths**
  - Runtime checklists and strategy-focused heartbeat process are robust (`HEARTBEAT.md`, `AGENTS.md`, `TOOLS.md`, `memory/strategy-journal.md`, `MEMORY.md`).
  - Clear recommendation/report style already in strategy outputs.
- **Current gaps**
  - `IDENTITY.md` is still placeholder text — no actual role/persona/emotional tone set, which weakens consistency and accountability.
  - Missing `memory/active-tasks.md`, `memory/lessons.md` only. This breaks main startup protocol and continuity requirements shared in other agents.
  - Missing `workflow-registry.md` and daily logs (except no `MM/DD` recent files), so no long-lived workflow catalog.

### dispatcher — role: routing helper
- **Strengths**
  - Very narrow mission and deterministic handoff behavior (`AGENTS.md`: parse input and call `sessions_spawn`).
  - Minimal, low-noise `HEARTBEAT.md`.
- **Current gaps**
  - Identity documents incomplete (`SOUL`/`USER` are minimal placeholders; no concrete persona or human metadata).
  - No `MEMORY.md`, no `memory/` directory, and no `workflow-registry`/`lessons` persistence.
  - Missing safety/operational conventions present in other agents (anti-footgun, A2A docs, escalation rules, completion status expectations).
  - This creates blind spots for auditability and reduces autonomy when dispatcher errors recur.

---

## 2) Top 3 recurring failure modes observed + mitigations

1. **Cross-agent context drift (high frequency)**
   - Evidence: only some agents keep strict startup memory cycle; others lack required files (quant/dispatcher) or stale `active-tasks.md` in main.
   - Mitigation:
     - Enforce a lightweight onboarding checklist template shared by all persistent agents.
     - Add a startup self-check that logs a **readiness warning** if any required file missing.
     - Require dispatcher and quant to adopt the same memory/continuity minimum before non-trivial tasks.

2. **Governance propagation lag between docs and runtime behavior**
   - Evidence: several lessons/tasks capture protocol changes, but only some agents explicitly include propagation to their local docs.
   - Mitigation:
     - Make “propagation to all persistent agents” a checklist item in `A2A` pre-task start and post-completion.
     - Add one central “change log digest” file each workspace uses to copy relevant protocol deltas quickly.

3. **Identity/documentation incompleteness reducing predictability/autonomy**
   - Evidence: quant `IDENTITY.md` and dispatcher docs are placeholders/defaults.
   - Mitigation:
     - Require complete identity files at creation (no placeholder tokens allowed).
     - Add a periodic validation script/check that blocks high-risk dispatch to placeholders.
     - Add default values (role, channel confidence, safety constraints) so autonomy is bounded and explicit.

---

## 3) Concrete doc changes (diff-style proposals)

### main
- **`docs/agent-standard.md`**: add a short “Persistent Agent Minimum Identity Contract” section:
  - `- [ ] SOUL`, `IDENTITY`, `USER`, `AGENTS`, `TOOLS` present and non-placeholder.
  - `- [ ] memory/active-tasks.md` + `memory/lessons.md` + today/yesterday exists.
  - `- [ ] startup self-check log` for missing files.

### main (for Luna)
- **`AGENTS.md`**: add a one-liner under startup checklist:
  - `If memory files are stale/compressed >N days, append audit note and flag for cleanup.`
  - Add explicit “cross-agent file presence check” before `Two-Phase spawn`.

### Luan
- **`AGENTS.md`**: add a mandatory pre-task step:
  - `If task spec relies on trust bindings or channel routing changes, import `main` `IDENTITY.md` trust matrix first (no assumptions).`

### crypto-sage
- **`USER.md`**: replace generic template with concrete profile values (tone, authority, response constraints), aligning to Matheus' shared preferences.
- **`AGENTS.md`**: add explicit startup check: verify presence of `main` trust/binding assumptions and `delegated-human-proxy` policy before accepting sensitive tasks.

### quant-strategist
- **`IDENTITY.md`**: replace placeholders with real identity metadata:
  - Name/creature/vibe/emoji + concise decision boundaries.
- **`AGENTS.md`**:
  - add required `memory/active-tasks.md` read/write.
  - add `memory/workflow-registry.md` requirement (if missing, create stub and populate from strategy workflow changes).
- **`workspace root`**: create `memory/active-tasks.md` + `memory/lessons.md` + today/yesterday daily logs.

### dispatcher
- **`AGENTS.md`**: keep narrow mission but append minimal operational contract:
  - startup read: `SOUL/IDENTITY/USER/AGENTS` + `memory/*` if present.
  - if missing protocol files -> emit concise structured warning in response.
  - explicitly include expected output contract for spawn (`DISPATCHED ...` only) and failure escalation path.
- **`IDENTITY.md`**: replace minimalist text with actual role/persona/limits/what-not-to-do.
- **`USER.md`**: populate Matheus metadata minimally (or link to main/USER.md intent) to keep consistent authority assumptions.
- **`HEARTBEAT.md`**: add optional check for broken `openclaw`/MC dispatch path and fallback hint.
- **`memory/`**: create at least `memory/active-tasks.md` + `memory/lessons.md` + `active-tasks` lifecycle skeleton for traceability.

---

## 4) Cross-agent protocol changes needed

### A2A
- Standardize a **Required A2A bootstrap** across all persistent agents: identity check, memory-read set, and explicit trust assumptions snapshot.
- Add a **“protocol version” key** in each completion artifact (e.g., `protocol_version: a2a_v1`) to detect protocol drift in audits.

### Mission Control tracking
- Enforce non-placeholder identity completion as part of `mc-spawn` precondition (warn/block if target agent files unresolved).
- For dispatcher-run tasks, add MC comment annotation of “dispatch method used” + “fallback path if provider/dispatch dependency fails” for recovery audit.

### QA review
- Extend QA checklist for any `complete`/`partial` output to include:
  - continuity check (`active-tasks` updated + startup-required files present),
  - protocol-propagation check (lessons/guideline updates applied in affected agent docs),
  - and at least one identity-file sanity spot-check (no placeholders).

---

## 5) What I found about optional doc-only improvements

- No in-place doc edits were applied in this run (report-only mode).
- The above items are intentionally minimal and safe (markdown-only, no config/code changes).
- High-leverage next step: implement only the required-file bootstrap checks first (minimal edits, high impact).
