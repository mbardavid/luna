---
name: browser-task
description: Run repeatable browser collection tasks with stable outputs. Use when the user asks to open a page, capture title/text/snapshot, or store browser evidence in workspace artifacts. Prefer script flow for deterministic results.
---

Use `/home/openclaw/.openclaw/workspace/scripts/browser_snapshot.sh`.

## Procedure
1. Accept URL and output file path.
2. Run script to collect title + accessibility snapshot + optional screenshot.
3. Save outputs under `artifacts/reports/`.
4. Return location of files and a 1-2 line summary.

## Negative triggers
- Do not use for authenticated sessions requiring human login unless user confirms.
- Do not use when task is better handled by OpenClaw `browser` tool interactive flow.

## Command
`bash scripts/browser_snapshot.sh "https://example.com" "artifacts/reports/example"`
