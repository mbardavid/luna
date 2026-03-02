# Quant Strategist Agent — PMM Intelligence Layer

**MC Task ID:** 7347dd0a-10ba-4694-b880-4da8553b941b
**Type:** feature
**Risk:** MEDIUM
**Phase:** planning

## Objective

Design and implement the Quant Strategist agent as the intelligence/decision-making layer for the Polymarket Market Maker (PMM). The agent operates as the "brain" while the runner script is the "hands".

## Architecture Context

```
Quant Agent (IA) ──configures──→ PMM Runner (script)
     ↑                              ↓ logs
     └──── analyzes ←──── Supabase (data)
```

**Existing Infrastructure:**
- PMM runner: `polymarket-mm/runner/` — unified engine with paper/live modes
- Supabase tables: `pmm_orders`, `pmm_fills`, `pmm_exits`, `pmm_runs` (trade data)
- Supabase tables: `portfolio_snapshots`, `positions`, `pnl_daily` (portfolio data)
- Agent registered in openclaw.json as `quant-strategist` (model: claude-opus-4-6-thinking)
- Discord channel: `#quant-strategist` (id: 1475989470883872860)
- Polymarket CLOB API: `https://clob.polymarket.com` (via Tor proxy `socks5://127.0.0.1:9050`)
- Market Rotation module (being built by Luan in task 017469bb): provides the infra, quant decides WHEN

**Key Files:**
- `polymarket-mm/runner/__main__.py` — runner entry point
- `polymarket-mm/runner/config.py` — UnifiedMarketConfig, auto_select_markets()
- `polymarket-mm/runner/pipeline.py` — UnifiedTradingPipeline
- `polymarket-mm/paper/runs/*.yaml` — run configs
- `polymarket-mm/config/markets-*.yaml` — market definitions

## Agent Responsibilities

### 1. Market Research & Selection
- Periodically scan Polymarket for optimal MM markets
- Criteria: volume, spread tightness, proximity to 0.50, liquidity depth, event risk
- Score and rank markets
- Output: `config/markets-recommended.yaml` with ranked market configs

### 2. Parameter Optimization
- Analyze historical PnL from Supabase (`pmm_fills`, `pmm_exits`, `pmm_runs`)
- Tune: spread_bps, gamma, max_position, order_size, quote_interval
- A/B test parameter sets across runs
- Output: `paper/runs/next-run.yaml` with optimized params

### 3. Performance Analysis
- Post-run analysis: PnL breakdown, fill rate, adverse selection, inventory curves
- Compare across runs (prod-001 vs prod-002 vs prod-003)
- Identify patterns: time-of-day effects, market-specific behaviors
- Output: analysis reports in Discord + `analysis/` directory

### 4. Risk Monitoring (periodic cron)
- Monitor live bot status (PID alive, last log timestamp, error rate)
- Check market conditions (spread widening, volume drop, event approaching)
- Decide when to trigger market rotation (provides the WHEN; runner provides the HOW)
- Alert Luna/Matheus on anomalies

### 5. Strategy Evolution
- Maintain `memory/strategy-journal.md` with decisions and rationale
- Track what works and what doesn't
- Propose new strategies (e.g., multi-market, cross-market hedging)

## Agent Architecture (for Luan to design)

### Workspace Structure
```
workspace-quant-strategist/
├── AGENTS.md          # Agent bootstrap + protocol
├── SOUL.md            # Personality: analytical, data-driven
├── TOOLS.md           # API endpoints, Supabase connection
├── memory/
│   ├── lessons.md
│   ├── strategy-journal.md
│   └── YYYY-MM-DD.md
├── scripts/
│   ├── market-scanner.py    # Polymarket market discovery + scoring
│   ├── performance-analyzer.py  # Post-run Supabase analysis
│   ├── param-optimizer.py   # Parameter tuning from historical data
│   └── health-monitor.py    # Live bot monitoring
├── analysis/
│   └── run-xxx-report.md    # Per-run analysis reports
└── config/
    └── scoring-weights.yaml # Market scoring parameters
```

### Integration Points
1. **Supabase (read)**: Query `pmm_*` tables for historical trade data
2. **Polymarket API (read)**: `sampling-markets` endpoint for market discovery
3. **Runner config (write)**: Generate YAML configs for next runs
4. **Discord (write)**: Post analysis reports and alerts to `#quant-strategist`
5. **Luna (A2A)**: Receive tasks, report findings, request actions

### Cron Schedule (proposed)
- Every 6h: Market scan + scoring
- Every 1h: Health monitor (bot alive, metrics)
- Post-run: Automatic performance analysis (triggered by run completion)
- Weekly: Parameter optimization review

## Acceptance Criteria
- [ ] Quant agent workspace created with proper AGENTS.md, SOUL.md, TOOLS.md
- [ ] market-scanner.py: connects to Polymarket API, scores markets, outputs ranked YAML
- [ ] performance-analyzer.py: reads Supabase pmm_fills/exits, generates PnL report
- [ ] health-monitor.py: checks bot PID, last log, error rate, market conditions
- [ ] All scripts work standalone (testable without agent context)
- [ ] Integration test: scanner finds markets → generates config → validated by runner's load_markets()
- [ ] Documentation: README with agent purpose, scripts, cron setup

## Verification Checks
```bash
# Scripts run without error
cd /home/openclaw/.openclaw/workspace-quant-strategist
python3 scripts/market-scanner.py --dry-run
python3 scripts/performance-analyzer.py --run-id prod-003 --dry-run
python3 scripts/health-monitor.py --check

# Generated config is valid for runner
python3 -c "
import sys; sys.path.insert(0, '/home/openclaw/.openclaw/workspace/polymarket-mm')
from runner.config import load_markets
from pathlib import Path
markets = load_markets(Path('config/markets-recommended.yaml'))
assert len(markets) > 0, 'No markets loaded'
print(f'✅ {len(markets)} markets loaded')
"
```

## Constraints
- Polymarket API requires Tor proxy (socks5://127.0.0.1:9050)
- Supabase connection via pooler: `aws-0-us-west-2.pooler.supabase.com:5432`
- Agent model: `claude-opus-4-6-thinking` (already configured)
- Scripts must be Python (consistent with PMM codebase)
- Market scoring must be deterministic and explainable (not black box)
- Agent MUST NOT execute trades directly — only recommends/configures

## QA Guidance for Luna
1. Does market-scanner.py actually query live Polymarket API and return valid markets?
2. Does performance-analyzer.py correctly parse Supabase data and compute PnL?
3. Are the generated YAML configs compatible with `runner.config.load_markets()`?
4. Is the SOUL.md appropriate for a quant analyst role?
5. Does health-monitor.py detect a dead bot (kill PID, check detection)?
