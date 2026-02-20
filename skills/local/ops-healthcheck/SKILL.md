---
name: ops-healthcheck
description: Run local operational checks for OpenClaw setup health. Use when user asks if environment is working, versions are current, browser automation is healthy, or integrations look broken.
---

Use `/home/openclaw/.openclaw/workspace/scripts/healthcheck.sh`.

## Procedure
1. Run healthcheck script.
2. Report status in three lines: OpenClaw, browser tooling, integrations.
3. If failures exist, return exact failing command + next fix step.

## Negative triggers
- Do not use for deep security hardening audits (use healthcheck skill instead).
- Do not run destructive fixes without confirmation.

## Command
`bash scripts/healthcheck.sh`
