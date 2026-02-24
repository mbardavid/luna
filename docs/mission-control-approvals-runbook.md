# Mission Control — Approvals Runbook (Fase 3)

**Goal:** Matheus only gets paged for risk-bearing tasks. Approvals are tracked in Mission Control and surfaced in Discord.

## What exists now

- `scripts/mc-approvals-notify.sh`
  - polls pending approvals: `GET /boards/{board_id}/approvals?status=pending`
  - posts a notification to Discord
  - sets `mc_approval_notified=true` on the linked task to avoid spam

- `scripts/mc-phase3-cron.sh`
  - installs a cron (default 5 min) that runs `mc-approvals-notify.sh`

## Install

```bash
cd /home/openclaw/.openclaw/workspace
./scripts/mc-phase3-cron.sh install
./scripts/mc-phase3-cron.sh status
```

## Validate (objective)

### 1) Dry-run poll

```bash
export MC_AUTH_TOKEN="$(python3 -c 'import json;print(json.load(open("config/mission-control-ids.local.json"))["auth_token"])')"
MC_APPROVALS_DRYRUN=1 ./scripts/mc-approvals-notify.sh --verbose --max 5
# Expect: JSON output with notified>=0 and dry_run=true
```

### 2) Real notification

1. Create a test task in MC.
2. Create a pending approval linked to it:

```bash
TASK_ID=<uuid>
./scripts/mc-client.sh create-approval "$TASK_ID" "teste approval" manual_override 80
```

3. Run notify script:

```bash
./scripts/mc-approvals-notify.sh --verbose --max 5
```

**Expected:** a message appears in Discord channel `MC_APPROVALS_CHANNEL` (default: #general-luna).

### 3) Anti-spam guard

Re-run the script. The same task should not be re-notified because `mc_approval_notified=true`.

## About interactive buttons

Discord buttons require handling component interactions (callbacks). At the moment we only implement notify-only because the current runtime does not route button click events back into this agent.

If/when interaction callbacks are available, we can extend the notify message to include buttons and auto-update the approval status.
