# Heartbeat V3 — Installation Guide

> **⚠️ DO NOT install until Matheus gives explicit go-ahead.**
> The current heartbeat-v2.sh continues running until then.

## Prerequisites

- Python 3.10+ (check: `python3 --version`)
- `openclaw` CLI in PATH
- Gateway running (`openclaw gateway status`)
- All tests passing (`cd heartbeat-v3 && python3 -m pytest tests/ -v`)

## Step-by-Step Installation

### Step 1: Run tests

```bash
cd /home/openclaw/.openclaw/workspace/heartbeat-v3
python3 -m pytest tests/ -v
```

All tests must pass. If any fail, do NOT proceed.

### Step 2: Dry-run heartbeat-v3

```bash
cd /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts
bash heartbeat-v3.sh --dry-run --verbose
```

Check output:
- All phases should complete without errors
- Queue files should NOT be created (dry-run)
- Discord messages should NOT be sent

### Step 3: Disable heartbeat-v2 in crontab

```bash
crontab -e
# Comment out the heartbeat-v2 line:
# */10 * * * * /home/openclaw/.openclaw/workspace/scripts/heartbeat-v2.sh >> ...
```

### Step 4: Make scripts executable

```bash
chmod +x /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/*.sh
chmod +x /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/*.py
```

### Step 5: Install new crontab entries

```bash
crontab -e
# Add the entries from heartbeat-v3/crontab.txt:

# Heartbeat V3 — detection + queue dispatch (every 10min)
*/10 * * * * /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/heartbeat-v3.sh >> /home/openclaw/.openclaw/workspace/logs/heartbeat-v3-cron.log 2>&1

# Queue escalation — deterministic escalation (every 5min)
*/5 * * * * /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/queue-escalation.sh >> /home/openclaw/.openclaw/workspace/logs/queue-escalation-cron.log 2>&1

# Session GC — zombie session cleanup (every 30min)
*/30 * * * * /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/session-gc.sh >> /home/openclaw/.openclaw/workspace/logs/session-gc-cron.log 2>&1
```

### Step 6: Update HEARTBEAT.md

Edit `/home/openclaw/.openclaw/workspace/HEARTBEAT.md`:
- Change references from heartbeat-v2 to heartbeat-v3
- Add queue architecture explanation
- Update troubleshooting section

### Step 7: Update Luna's SOUL.md

Add to Luna's SOUL.md:

```markdown
## Dispatch Queue
Ao iniciar qualquer interação, verifique `workspace/heartbeat-v3/queue/pending/`.
Se houver arquivos, processe-os ANTES de qualquer outra ação.

Use `queue-consumer.py --peek` para listar e `queue-consumer.py --one --json` para consumir.
```

### Step 8: First live run

```bash
# Run heartbeat-v3 manually (not dry-run)
bash /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/heartbeat-v3.sh --verbose
```

Check:
- Log output in `logs/heartbeat-v3.log`
- Queue files in `heartbeat-v3/queue/pending/` (if tasks are pending)
- Discord notifications sent correctly

### Step 9: Monitor first 24h

- Check `logs/heartbeat-v3.log` for errors
- Check `logs/queue-escalation.log` for escalation activity
- Check `logs/session-gc.log` for GC activity
- Monitor gateway memory: `systemctl status openclaw-gateway`

## Post-Installation Verification

```bash
# Check heartbeat-v3 is running
tail -20 /home/openclaw/.openclaw/workspace/logs/heartbeat-v3.log

# Check queue status
python3 /home/openclaw/.openclaw/workspace/heartbeat-v3/scripts/queue-consumer.py --peek

# Check escalation is running
tail -5 /home/openclaw/.openclaw/workspace/logs/queue-escalation.log

# Check session GC is running
tail -5 /home/openclaw/.openclaw/workspace/logs/session-gc.log

# Verify heartbeat-v2 is NOT running
grep heartbeat-v2 <(crontab -l)  # should return nothing
```
