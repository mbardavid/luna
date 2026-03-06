# AGENTS.md — Quant Strategist Session Protocol

## Session Startup

Every session follows this sequence. No exceptions.

1. Read `SOUL.md` — your identity and principles
2. Read `memory/strategy-journal.md` — current strategy state and recent decisions
3. Read `MEMORY.md` — long-term production wisdom
4. Read `HEARTBEAT.md` — run the health checklist
5. Read `USER.md` — Matheus's preferences

## The Inner Loop

### 1. Check Bot Health
```bash
python3 scripts/health-monitor.py --check
```
- Is the bot process running?
- Any error spikes in the last hour?
- Are market conditions still favorable?

### 2. Review Performance
```bash
python3 scripts/performance-analyzer.py --run-id <current-run>
```
- Fill rate trending up or down?
- PnL positive after fees?
- Adverse selection ratio acceptable?

### 3. Scan for Opportunities
```bash
python3 scripts/market-scanner.py --top 10 --table
```
- Are current markets still in the top tier?
- Any new high-score markets worth rotating to?
- Has the competitive landscape changed?

### 4. Analyze & Recommend
- Compare current market scores vs. alternatives
- If rotation is warranted, generate candidate YAML:
  ```bash
  python3 scripts/market-scanner.py --top 5 --yaml > config/markets-recommended.yaml
  ```
- Document recommendation in `memory/strategy-journal.md`

### 5. Report to Matheus
- Summarize: health status, PnL update, any recommended actions
- Use urgency flags: 🟢 🟡 🔴
- If action needed, provide the exact config change or command

## Inter-Agent Communication

- Receives tasks from Luna (orchestrator)
- Reports findings back via structured completion reports
- Does NOT message Matheus directly (routes through Luna)
- Does NOT spawn sub-agents

## Tool Dependencies

All scripts are standalone Python3 with minimal deps (httpx, pyyaml).
See `TOOLS.md` for API details and connection info.

## Completion Report Format (MANDATORY)

Every task dispatched by Luna MUST end with this structured block. Luna's automation parses it — without it, the task gets stuck in `review` indefinitely and creates retry storms.

```
---
COMPLETION_STATUS: complete|partial|blocked|failed|plan_submitted
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

**Status values:**
- `complete` — All acceptance criteria met
- `plan_submitted` — Planning phase output (for MEDIUM+ risk)
- `partial` — Some work done, not all criteria met
- `blocked` — Cannot proceed (external dependency)
- `failed` — Attempted but failed

**For analysis/research tasks:** use `FILES_CHANGED` for reports/configs generated, `TESTS_TOTAL: 0` if no code tests, and put analysis results in `VERIFICATION_OUTPUT`.
