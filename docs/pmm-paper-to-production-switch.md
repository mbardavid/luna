# PMM Paper → Production Switch Guide

> Single source of truth for everything that differs between paper trading and production.
> Every paper-only change MUST be logged here with the run that introduced it.

## Architecture Overview

```
Paper:   QuoteEngine → PaperVenue (simulated fills) → EventBus → PaperRunner
Prod:    QuoteEngine → ExecutionLayer → Polymarket CLOB API → EventBus → ProdRunner
```

The **QuoteEngine** and **strategy layer** are shared. The venue/execution layer swaps out.

---

## 1. Paper-Only Components (DO NOT deploy to prod)

| Component | Path | Purpose | Prod Replacement |
|-----------|------|---------|------------------|
| PaperVenue | `paper/paper_venue.py` | Simulated order matching | `execution/clob_execution.py` |
| PaperRunner | `paper/paper_runner.py` | Pipeline orchestrator (paper) | `execution/prod_runner.py` (TBD) |
| PaperExecution | `paper/paper_execution.py` | Paper execution adapter | Real CLOB execution |
| ChaosInjector | `paper/chaos_injector.py` | Fault injection for testing | Remove entirely |
| ReplayEngine | `paper/replay_engine.py` | Historical data replay | Remove entirely |
| Dashboard | `paper/dashboard/` | Dev monitoring UI | Grafana/production dashboard |
| Report Generator | `paper/generate_report.py` | Post-run analysis | Production metrics pipeline |

## 2. Paper-Specific Behaviors in Shared Code

### 2.1 Inventory Skew — MIN_SIGMA Fallback
- **File:** `strategy/inventory_skew.py:36`
- **What:** `MIN_SIGMA = Decimal("0.005")` — fallback when historical volatility is 0
- **Why paper:** Short data series in paper → sigma=0 → skew=0
- **Prod action:** KEEP — useful safety net, but real vol should dominate. Monitor that `effective_sigma > MIN_SIGMA` in prod logs. If always hitting fallback, feature engine has a bug
- **Introduced:** Run-003

### 2.2 Quote Engine — Position-Aware Filtering
- **File:** `strategy/quote_engine.py:458` (`_filter_by_position`)
- **What:** Filters ASK slices when no position, suppresses BID when saturated
- **Prod action:** KEEP for BID saturation. **REMOVE ASK filtering** — on Polymarket CLOB, you can sell (short) without holding tokens via complement matching on the exchange side
- **Introduced:** Run-003

### 2.3 Quote Engine — Balance-Based Sizing
- **File:** `strategy/quote_engine.py:540` (`_apply_balance_sizing`)
- **What:** Caps each BID at 5% of available balance
- **Prod action:** KEEP but tune `max_balance_fraction_per_order` (5% paper → maybe 2-3% prod)
- **Introduced:** Run-003

### 2.4 PaperVenue — Complement Routing
- **File:** `paper/paper_venue.py:~400`
- **What:** SELL YES with 0 position → **REJECTED** (complement routing disabled)
- **Why paper:** Complement routing caused double exposure — bot accumulated positions on both YES and NO sides simultaneously
- **Prod action:** RE-ENABLE — Polymarket CLOB handles complement matching natively. This is only disabled in paper
- **Status:** DISABLED in paper since run-005. SELL with 0 position is rejected; SELL with partial position is resized to held qty
- **Introduced:** Run-004 (complement routing), Run-005 (disabled)

### 2.5 PaperVenue — Fill Probability
- **File:** `paper/paper_venue.py:99`
- **What:** Random fill with configurable probability (default 50%, run configs use 20-25%)
- **Prod action:** REMOVE entirely — fills are real on CLOB
- **Introduced:** Original

### 2.6 PaperVenue — Partial Fills
- **File:** `paper/paper_venue.py:135`
- **What:** Random partial fills with configurable probability
- **Prod action:** REMOVE — CLOB handles partial fills natively
- **Introduced:** Original

### 2.7 Kill Switch — Drawdown Threshold
- **File:** `core/kill_switch.py` + `paper/paper_runner.py:880`
- **What:** Halts trading when equity drops >25% from initial (configurable via run config)
- **Alert:** Warning log at 15% drawdown (configurable via `kill_switch_alert_pct`)
- **Prod action:** KEEP but tune threshold. Both `kill_switch_max_drawdown_pct` and `kill_switch_alert_pct` are configurable in run YAML under `params`
- **Previous issue:** 10% was too tight for MM (mark-to-market swings on open positions caused false kills)
- **Introduced:** Original, tuned Run-005

### 2.8 Virtual Wallet
- **File:** `paper/paper_venue.py:150-160`
- **What:** Simulated USDC balance ($500 default)
- **Prod action:** REPLACE with real balance from Polygon RPC (`balanceOf(wallet, USDC.e)`)
- **Introduced:** Run-002

### 2.9 Adversarial Venue — Adverse Selection, Fees, Distance Decay
- **Files:** `paper/paper_venue.py` (`FeeConfig`, `MarketSimConfig.adverse_selection_bps`, `MarketSimConfig.fill_distance_decay`)
- **What:**
  - **Adverse Selection (10bps):** After each fill, mid moves CONTRA fill direction. BUY fill → mid drops, SELL fill → mid rises. Simulates informed flow adverse selection.
  - **Fee Model (-20bps maker rebate):** Each fill incurs a fee/rebate. Polymarket gives makers -20bps (0.2% rebate). Tracked in `total_fees` and logged per-fill.
  - **Fill Distance Decay:** Orders further from mid have lower fill probability. `adjusted_prob = base_prob * max(0.05, 1 - distance / (2 * half_spread))`.
- **Prod action:** REMOVE adverse selection + distance decay (real CLOB handles these naturally). KEEP fee awareness — the -20bps maker rebate is real on Polymarket and should be factored into PnL.
- **Introduced:** Run-007

## 3. Configuration Differences

| Parameter | Paper (run-004) | Production Target | Notes |
|-----------|-----------------|-------------------|-------|
| `initial_balance` | $500 | Real USDC.e balance | ~228 USDC.e on Polygon |
| `fill_probability` | 0.25 | N/A (real fills) | Remove entirely |
| `default_order_size` | 25 shares | 10-15 shares | Conservative start |
| `default_half_spread_bps` | 50 | 30-50 | Tighter for rewards |
| `gamma_risk_aversion` | 0.3 | 0.5 | More conservative |
| `quote_interval_s` | 2.0 | 1.0-2.0 | Faster for real market |
| `max_position_size` | 500 | 200 | Limit exposure |
| `kill_switch_drawdown` | 25% | 15-20% | Alert at 15%, kill at threshold |
| `markets` | 1-2 | 3-5 | Diversify |
| `adverse_selection_bps` | 10 | N/A (real fills) | Paper-only: mid moves against fills |
| `maker_fee_bps` | -20 | -20 (real rebate) | Keep: Polymarket maker rebate is real |
| `fill_distance_decay` | true | N/A (real fills) | Remove entirely |

## 4. Environment Variables (Production)

Already configured in systemd drop-in `polymarket-env.conf`:
```
POLYMARKET_API_KEY=...
POLYMARKET_API_SECRET=...
POLYMARKET_PASSPHRASE=...
POLYGON_RPC_URL=...
```

Wallet: `0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1`
- 228 USDC.e ready
- 8.38 POL for gas
- 6 allowances set (CTF Exchange, Neg Risk CTF, Neg Risk Adapter)

## 5. Pre-Production Checklist

See also: `docs/pmm-bugs-production-checklist.md`

- [ ] All paper-only behaviors disabled/swapped
- [ ] Real CLOB execution layer tested (place + cancel limit order)
- [ ] Real balance query working
- [ ] Kill switch threshold tuned (15-20%)
- [ ] Order size conservative (10-15 shares)
- [ ] Gas estimation for on-chain operations
- [ ] Monitoring/alerting pipeline (not dev dashboard)
- [ ] P6 approval from Matheus

## 6. Change Log

| Run | Change | Paper-Only? | Prod Action |
|-----|--------|-------------|-------------|
| 001 | Basic paper venue | Yes | Replace with CLOB |
| 002 | Virtual wallet + position checks | Partially | Keep position checks, replace wallet |
| 003 | Spread floor, sigma fallback, position-aware quoting, dynamic sizing | Partially | Keep strategy, remove paper-specific filters |
| 004 | Complement routing, SELL position sync | Yes | Remove complement routing (CLOB native) |
| 004 | Kill switch trigger at 10% | Shared | Tune to 15-20% |
| 005 | _(pending)_ Disable complement in paper, raise kill switch | Paper | N/A |
| 005 | Disable complement routing in paper | Paper | Re-enable for prod (CLOB native) |
| 005 | Kill switch 10%→25% | Shared | Keep tunable, alert at 15% |
| 007 | Adversarial PaperVenue: adverse selection (10bps), maker fee rebate (-20bps), fill distance decay | Paper | Remove adverse selection + distance decay (real fills). Keep fee awareness for PnL accounting |

## 7. Dual-Mode: Paper + Production Simultaneously

### Architecture
```
┌─────────────────────────────────┐    ┌─────────────────────────────────┐
│  PaperRunner (run-007)          │    │  ProductionRunner (prod-001)    │
│  └─ PaperVenue (simulated)      │    │  └─ LiveExecution (real CLOB)   │
│  └─ QuoteEngine (shared)        │    │  └─ QuoteEngine (shared)        │
│  └─ FeatureEngine (shared)      │    │  └─ FeatureEngine (shared)      │
│  └─ WS Client (shared protocol) │    │  └─ WS Client (shared protocol) │
│  └─ trades.jsonl                │    │  └─ trades_production.jsonl     │
│  └─ live_state.json             │    │  └─ live_state_production.json  │
└─────────────────────────────────┘    └─────────────────────────────────┘
                     │                                    │
                     └────────────┬───────────────────────┘
                                  │
                     ┌────────────▼──────────────┐
                     │  Dual Dashboard (8501)     │
                     │  /api/state (demo)         │
                     │  /api/state/prod           │
                     │  /api/trades (demo)        │
                     │  /api/trades/prod           │
                     │  Comparison Panel           │
                     └────────────────────────────┘
```

### Key Differences
| Aspect | Paper (DEMO) | Production (PROD) |
|--------|-------------|-------------------|
| Capital | $500 simulated | $25 real USDC.e |
| Order size | 25 shares | 5 shares (minimum) |
| Fill mechanism | PaperVenue (random walk) | Real CLOB matching |
| Kill switch | 25% drawdown | 20% drawdown ($5) |
| Quote interval | 2s | 5s (rate limit safe) |
| Venue | PaperVenue | py_clob_client |
| Logs | trades.jsonl | trades_production.jsonl |
| State | live_state.json | live_state_production.json |

### Parameter Extraction
After 8+ hours of production data:
```bash
python3 -m paper.extract_real_params
```
Outputs `paper/data/real_params.json` with real fill rate, adverse selection,
fees, latency, and suggested PaperVenue calibration config.

### Running Both
```bash
# Paper (already running as systemd service or manual):
python3 -m paper.paper_runner --config paper/runs/run-007.yaml

# Production (separate process):
python3 -m paper.production_runner --config paper/runs/prod-001.yaml

# Dashboard (serves both):
python3 -m paper.dashboard.server
```
