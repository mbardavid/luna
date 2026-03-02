# Plan: Unified Runner (Paper + Production)

**Status:** Backlog
**Risk:** MEDIUM
**Type:** refactor

## Problem

`paper_runner.py` (1857 lines) and `production_runner.py` (1718 lines) are diverging codebases.
Features implemented in production (trade dedup, balance-aware quoting, startup reconciliation)
are missing from paper. Bugs fixed in one don't propagate to the other.

## Objective

Unify into a single `runner.py` with `--mode paper|live` flag.

## Architecture

```
runner.py (unified)
├── VenueAdapter (ABC)
│   ├── PaperVenue (simulated fills, fee model)
│   └── LiveVenue (CLOB API, real orders, Tor proxy)
├── WalletAdapter (ABC)  
│   ├── PaperWallet (in-memory, virtual balance)
│   └── ProductionWallet (on-chain sync, reconciliation)
├── TradingPipeline (shared)
│   ├── QuoteEngine (already shared)
│   ├── LiveBookTracker (already shared)
│   ├── MetricsCollector (already shared)
│   ├── KillSwitch (already shared)
│   └── TradeDedup (persist to JSON — currently production-only)
├── LiveStateWriter (shared, mode-aware output path)
├── StartupReconciler (production-only, skipped in paper)
└── Dashboard (shared)
```

## Key Design Decisions

1. **VenueAdapter ABC**: `submit_order()`, `cancel_order()`, `get_open_orders()`, `get_fills()`
   - PaperVenue: probabilistic fills from live book data
   - LiveVenue: py_clob_client with Tor SOCKS5

2. **WalletAdapter ABC**: `get_balance()`, `get_position()`, `total_equity()`, `reconcile()`
   - PaperWallet: starts with `initial_balance`, tracks virtual fills
   - ProductionWallet: on-chain USDC + CTF queries, `reconcile_on_chain()`

3. **Pipeline is mode-agnostic**: receives venue + wallet via dependency injection.
   Same quote loop, same metrics, same kill switch.

4. **Config-driven**: `mode: paper|live` in YAML. All other params identical.

## Migration Steps

1. Extract `VenueAdapter` ABC from PaperVenue + LiveExecution
2. Extract `WalletAdapter` ABC from PaperWallet + ProductionWallet  
3. Create unified `TradingPipeline` by merging PaperTradingPipeline + ProductionTradingPipeline
   - Keep production as base (has more features)
   - Add paper-specific logic behind `if self.mode == 'paper'` guards
4. Create `runner.py` entry point with `--mode paper|live` 
5. Migrate all paper configs to new format (add `mode: paper`)
6. Run full test suite against unified runner
7. Deprecate `paper_runner.py` and `production_runner.py` (keep for 1 week, then delete)

## Files Changed

- **New**: `runner.py`, `venue/adapter.py`, `venue/paper.py`, `venue/live.py`, `wallet/adapter.py`, `wallet/paper.py`, `wallet/production.py`
- **Modified**: `pipeline.py` (extracted from both runners), tests
- **Deprecated**: `paper/paper_runner.py`, `paper/production_runner.py`

## Acceptance Criteria

- [ ] `python3 -m runner --mode paper --config p5-001.yaml` works identically to old paper_runner
- [ ] `python3 -m runner --mode live --config prod.yaml` works identically to old production_runner
- [ ] All existing tests pass (700+)
- [ ] New tests for VenueAdapter and WalletAdapter ABCs
- [ ] Trade dedup, balance-aware quoting, startup reconciliation available in both modes
- [ ] No behavioral regression in paper simulation accuracy

## Verification Checks

```bash
cd /home/openclaw/.openclaw/workspace/polymarket-mm
python3 -m pytest tests/ -x --tb=short
python3 -m runner --mode paper --config paper/runs/p5-001.yaml --duration-hours 0.01  # smoke test
python3 -m runner --mode live --config paper/runs/prod-test.yaml --dry-run  # no real orders
```

## Estimate

- Effort: ~3-4h coding (Luan)
- Risk: MEDIUM (core refactor, many moving parts)
- Two-Phase: Yes (plan first, then implement)
