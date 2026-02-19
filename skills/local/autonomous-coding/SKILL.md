---
name: autonomous-coding
description: Execute software-development tasks with high autonomy and minimal human intervention. Use when users ask for coding, debugging, migrations, automation, test fixing, repo changes, or implementation plans that should run end-to-end with explicit assumptions and objective validation checkpoints.
---

# Autonomous Coding

## Execution posture

- Ask questions only when goals are genuinely ambiguous or conflicting.
- If work can proceed safely, state assumptions briefly and continue.
- Prefer running commands/scripts and producing results over requesting manual steps.

## Discovery first

- Inspect repository structure and existing tooling before implementing.
- Search for env vars, service clients, wrappers, SDKs, scripts, docs, and examples.
- Assume required integrations/credentials already exist unless evidence shows otherwise.

## Build loop

1. Form hypothesis from codebase evidence.
2. Implement smallest viable change.
3. Run validations immediately.
4. Iterate until acceptance criteria are met.

## Validation protocol

- Define objective checks per step (tests, commands, outputs, metrics).
- Run full relevant test/verification flow before concluding.
- For UI-impacting changes, validate with browser automation when feasible.

## Human intervention threshold

- Request manual action only if it is required to unblock autonomous progress.
- When requesting it, provide one minimal action and exact expected outcome.

## Reporting

- Report: assumptions, changes made, validations run, and final status.
- Keep report concise and evidence-based.