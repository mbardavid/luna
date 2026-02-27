# Heartbeat V3 — Rollback Guide

> Use this guide if heartbeat-v3 has issues and you need to revert to v2.

## Quick Rollback (< 2 minutes)

### Step 1: Disable V3 crontab entries

```bash
crontab -e
# Comment out or remove these lines:
# */10 * * * * .../heartbeat-v3/scripts/heartbeat-v3.sh ...
# */5  * * * * .../heartbeat-v3/scripts/queue-escalation.sh ...
# */30 * * * * .../heartbeat-v3/scripts/session-gc.sh ...
```

### Step 2: Re-enable heartbeat-v2

```bash
crontab -e
# Uncomment the heartbeat-v2 line:
*/10 * * * * /home/openclaw/.openclaw/workspace/scripts/heartbeat-v2.sh >> /home/openclaw/.openclaw/workspace/logs/heartbeat-v2-cron.log 2>&1
```

### Step 3: Verify

```bash
# Check v2 is running (wait 10min, then):
tail -5 /home/openclaw/.openclaw/workspace/logs/heartbeat-v2.log

# Check v3 is NOT running:
# (no new entries should appear after rollback)
tail -5 /home/openclaw/.openclaw/workspace/logs/heartbeat-v3.log
```

## What Happens to Queue Items

- Items in `queue/pending/` will NOT be processed after rollback (v2 doesn't know about the queue)
- Items in `queue/active/` may be orphaned
- **Manual cleanup:** Move any important pending items back to MC inbox:
  ```bash
  for f in /home/openclaw/.openclaw/workspace/heartbeat-v3/queue/pending/*.json; do
    [ -f "$f" ] || continue
    task_id=$(python3 -c "import json; print(json.load(open('$f')).get('task_id',''))")
    echo "Reverting $task_id to inbox..."
    /home/openclaw/.openclaw/workspace/scripts/mc-client.sh update-task "$task_id" \
      --status inbox --comment "[rollback] heartbeat-v3 → v2 rollback"
  done
  ```

## State File Compatibility

Both v2 and v3 use the same state file: `/tmp/.heartbeat-check-state.json`

The state file is backward-compatible:
- V3 adds fields that v2 ignores (via `setdefault`)
- V2 won't lose data from v3 additions
- No manual state file cleanup needed

## What to Revert in SOUL.md

If you added the dispatch queue directive to Luna's SOUL.md, you can leave it (harmless) or remove:

```markdown
## Dispatch Queue
(remove this section)
```

## Session GC Consideration

Even after rollback, `session-gc.sh` is independently useful. Consider keeping it:

```bash
# Keep session-gc running even with v2:
*/30 * * * * /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/session-gc.sh >> /home/openclaw/.openclaw/workspace/logs/session-gc-cron.log 2>&1
```

This prevents the zombie session memory leak that affects v2 as well.

## Troubleshooting Post-Rollback

| Symptom | Fix |
|---------|-----|
| V2 not detecting tasks | Check `crontab -l`, ensure v2 line is active |
| Tasks stuck in `in_progress` | May need manual MC update to `inbox` |
| Queue items orphaned | Run manual cleanup above |
| State file corrupt | Delete `/tmp/.heartbeat-check-state.json` (fresh start) |
| Lock file stale | Delete `/tmp/.heartbeat-check.lock` |
