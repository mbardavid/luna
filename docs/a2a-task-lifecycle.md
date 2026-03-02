# A2A Task Lifecycle — Artifact Standard

## Directory Structure

Every task tracked in Mission Control gets a directory:

```
tasks/<task_id>/
├── 01-specs.md          # Luna's original task specs (objective, criteria, constraints)
├── 02-plan.md           # Luan's execution plan (Phase 1 output)
├── 03-plan-review.md    # Luna's review notes on the plan
├── 04-completion.md     # Luan's structured completion report
├── 05-qa-review.md      # Luna's QA review (lessons cross-ref, verification)
├── metadata.json        # Machine-readable lifecycle data
```

## metadata.json Schema

```json
{
  "task_id": "uuid",
  "title": "string",
  "type": "bugfix|feature|refactor|research|review",
  "risk": "low|medium|high|critical",
  "agent": "luan|crypto-sage|quant-strategist",
  "phases": {
    "specs": {
      "timestamp": "ISO8601",
      "session_key": null,
      "files_targeted": ["path1", "path2"]
    },
    "plan": {
      "timestamp": "ISO8601",
      "session_key": "agent:luan:subagent:...",
      "status": "plan_submitted|skipped",
      "estimated_files": 5,
      "estimated_new_tests": 10
    },
    "plan_review": {
      "timestamp": "ISO8601",
      "decision": "approved|revision_requested",
      "lessons_checked": ["L1", "L2", "L11"],
      "revision_count": 0,
      "notes": "summary of review"
    },
    "implementation": {
      "timestamp": "ISO8601",
      "session_key": "agent:luan:subagent:...",
      "status": "complete|partial|blocked|failed",
      "files_changed": 5,
      "tests_total": 717,
      "tests_new": 16,
      "tests_passing": 717,
      "criteria_met": "9/9",
      "runtime_seconds": 320
    },
    "qa_review": {
      "timestamp": "ISO8601",
      "decision": "approved|rejected",
      "lessons_violated": [],
      "verification_ran": true,
      "files_inspected": ["path1", "path2"],
      "rejection_feedback": null,
      "notes": "summary"
    }
  },
  "outcome": "done|rejected|failed",
  "total_spawns": 2,
  "total_cost_estimate_usd": 0.60
}
```

## Lifecycle Gates (Mandatory)

### Gate 1: Specs → Plan
- `01-specs.md` must exist before spawning Luan
- MC card description updated with specs summary

### Gate 2: Plan → Plan Review
- `02-plan.md` must exist (saved from Luan's completion report)
- Luna must read at minimum:
  - The plan itself
  - Target agent's `memory/lessons.md`
  - At least 1 file that will be modified
- `03-plan-review.md` must be written before spawning Phase 2

### Gate 3: Plan Review → Implementation
- `03-plan-review.md` must exist with `decision: approved`
- If `revision_requested`: re-spawn Phase 1 with feedback (max 2 iterations)

### Gate 4: Implementation → QA Review
- `04-completion.md` must exist (saved from Luan's completion report)
- Structured format required (see below)

### Gate 5: QA Review → Done
- `05-qa-review.md` must exist
- Luna must:
  - Read target agent's `memory/lessons.md`
  - Run or verify the Verification Checks output
  - Inspect at least 2 changed files
  - Check all Acceptance Criteria
- MC card updated with final summary

## Completion Report Format (Luan — Mandatory)

Luan must end every completion report with this structured block:

```
---
COMPLETION_STATUS: plan_submitted|complete|partial|blocked|failed
FILES_CHANGED: <number>
FILES_LIST: <comma-separated paths>
TESTS_TOTAL: <number>
TESTS_NEW: <number>
TESTS_PASSING: <number>
CRITERIA_MET: <met>/<total>
VERIFICATION_OUTPUT: |
  <paste actual command output>
BLOCKERS: <none|description>
---
```

## LOW Risk — Simplified Flow

For LOW risk tasks, the flow collapses:
1. Luna sends specs (01-specs.md) — Luan plans AND implements in one spawn
2. No separate plan review (Gate 2-3 skipped)
3. QA Review still mandatory (Gate 4-5)
4. Artifacts: 01-specs.md, 04-completion.md, 05-qa-review.md

## MEDIUM+ Risk — Full Flow

1. Luna sends specs → 01-specs.md
2. Luan plans (PHASE: planning) → 02-plan.md
3. Luna reviews → 03-plan-review.md
4. Luan implements (PHASE: implementation) → 04-completion.md
5. Luna QA reviews → 05-qa-review.md

## Why This Matters

- **Learning**: Historical artifacts show what worked vs. didn't
- **Lessons**: QA review notes feed into `memory/lessons.md`
- **Debugging**: When a task fails, we can trace exactly where the breakdown happened
- **Metrics**: metadata.json enables analysis of spawn success rate, cost per task, etc.
