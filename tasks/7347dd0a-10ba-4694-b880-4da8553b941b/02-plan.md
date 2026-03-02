# Quant Strategist Agent — Implementation Plan

## Key Findings from Luan's Code Review

1. **`discover_markets.py` already exists** at `polymarket-mm/scripts/discover_markets.py` — uses Gamma API, has market scoring, YAML output. Scanner should extend, not rewrite.
2. **YAML format for `load_markets()`** well-defined: requires `market_id`, `condition_id`, `token_id_yes`, `token_id_no`, `description`, `market_type`, `params`
3. **Supabase tables** (`pmm_orders`, `pmm_fills`, `pmm_exits`, `pmm_runs`) use REST API with service key auth
4. **prod-003 bug**: `initial_balance` defaults to 500 while actual USDC is 222, causing false 55% drawdown → kill switch. Health monitor should detect this
5. **Agent workspace exists** at `agents/quant-strategist/` with generic NautilusTrader SOUL.md — needs complete overhaul
6. **Gamma API** (https://gamma-api.polymarket.com) works without Tor. CLOB API needs Tor.

## Files (14 total: 6 modify, 8 new)

### Phase A: Workspace Setup (7 files)
1. Overhaul `SOUL.md` → PMM Quant Analyst identity
2. Overhaul `AGENTS.md` → scan → analyze → recommend → monitor loop
3. Update `TOOLS.md` → API endpoints, Supabase, paths
4. Update `USER.md` → Matheus context
5. Update `MEMORY.md` → Seed prod-001/002/003 lessons
6. Update `HEARTBEAT.md` → Daily checklist
7. Create `memory/strategy-journal.md` → Decision log

### Phase B: Market Scanner (`scripts/market-scanner.py`)
- Extends `discover_markets.py` (imports its functions)
- Composite scoring: `score = w_vol * norm(volume_24h) + w_spread * norm(1/spread) + w_price * norm(proximity_to_0.50) + w_liq * norm(liquidity) + w_days * norm(days_left) + w_rewards * has_rewards`
- Orderbook depth via CLOB REST (Tor proxy)
- Event risk heuristic: penalize <14 days, boost >90 days
- Output: --yaml (runner-compatible), --json, --table
- --dry-run, --validate flags
- New: `config/scoring-weights.yaml`

### Phase C: Performance Analyzer (`scripts/performance-analyzer.py`)
- Supabase REST queries for pmm_fills, pmm_exits, pmm_runs
- Per-run metrics: total_fills, fill_rate, pnl, adverse_selection_ratio
- Cross-run comparison
- Output: markdown report + JSON summary
- --dry-run, --run-id flags

### Phase D: Health Monitor (`scripts/health-monitor.py`)
- Process check: PID alive, recent logs
- Log analysis: error rate, warning patterns
- Market conditions: spread, volume changes
- Config validation: initial_balance vs actual USDC
- Exit codes: 0=healthy, 1=degraded, 2=unhealthy
- --check flag for cron use

### Phase E: Config + Docs
- `config/scoring-weights.yaml` — Market scoring weights
- `config/health-thresholds.yaml` — Health monitor thresholds
- `README.md` — Agent documentation

## Deferred
- `param-optimizer.py` — Needs multiple run data. After 3+ completed runs with Supabase logging.

## Luna Review Notes
- Plan approved ✅
- prod-003 initial_balance bug confirmed (good catch)
- Extending discover_markets.py instead of rewriting is the right call
- Deterministic scoring formula is correct approach (explainable, tunable)
