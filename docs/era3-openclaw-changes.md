# Era 3: openclaw.json Changes Needed

**Date:** 2026-03-01
**Phase:** Phase 1 — Luan Identity

## Current State

In `openclaw.json` → `agents.list`, Luan's entry is:

```json
{
  "id": "luan",
  "name": "Luan",
  "model": "google-antigravity/claude-opus-4-6-thinking"
}
```

**Problem:** No `workspace` field. This means Luan falls back to the default workspace (`/home/openclaw/.openclaw/workspace`), which is Luna's workspace. Luan should have its own workspace to:
- Read its own SOUL.md, AGENTS.md, lessons.md at session start
- Maintain separate memory/context from Luna
- Have its own .agent-config.json respected

## Required Change

Update the Luan agent entry in `openclaw.json` → `agents.list` to:

```json
{
  "id": "luan",
  "name": "Luan",
  "model": "google-antigravity/claude-opus-4-6-thinking",
  "workspace": "/home/openclaw/.openclaw/workspace-luan"
}
```

## How to Apply

This is a single-field addition to an existing JSON object. Safe to apply:

```bash
# Using jq (safest):
jq '(.agents.list[] | select(.id == "luan")).workspace = "/home/openclaw/.openclaw/workspace-luan"' \
  /home/openclaw/.openclaw/openclaw.json > /tmp/openclaw-patched.json && \
  cp /home/openclaw/.openclaw/openclaw.json /home/openclaw/.openclaw/openclaw.json.bak && \
  mv /tmp/openclaw-patched.json /home/openclaw/.openclaw/openclaw.json
```

## Risk Assessment

- **Risk:** LOW — adding a new field to an existing object, no fields removed or renamed
- **Rollback:** Restore from `.bak` copy
- **Impact:** Luan sessions will read workspace-luan context files instead of workspace (Luna's)
- **Note:** This change requires a gateway restart to take effect for new sessions. Existing sessions are unaffected.

## Verification

After applying, verify with:
```bash
jq '.agents.list[] | select(.id == "luan")' /home/openclaw/.openclaw/openclaw.json
```

Expected output should include `"workspace": "/home/openclaw/.openclaw/workspace-luan"`.
