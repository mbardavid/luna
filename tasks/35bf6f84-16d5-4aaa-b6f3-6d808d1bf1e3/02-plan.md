# Approved Plan: PMM Unified Runner

**MC Task:** 35bf6f84
**Phase 1 Status:** Approved by Luna (2026-03-02)
**Phase 2:** Ready to spawn

## Architecture

```
runner/                          # NEW package
├── __init__.py
├── __main__.py                  # `python -m runner --mode paper|live`
├── config.py                    # UnifiedMarketConfig, load/auto-select
├── pipeline.py                  # UnifiedTradingPipeline (core loop)
├── venue_adapter.py             # VenueAdapter ABC
├── paper_venue_adapter.py       # Wraps PaperVenue
├── live_venue_adapter.py        # Wraps LiveExecution + dedup + complement
├── wallet_adapter.py            # WalletAdapter ABC
├── paper_wallet.py              # Wraps PaperVenue internal wallet
├── production_wallet.py         # Wraps ProductionWallet
└── trade_logger.py              # Unified (mode-aware fields)
```

## VenueAdapter ABC
- connect/disconnect, submit_order, cancel_order, cancel_all, cancel_market, get_open_orders, process_fills
- PaperVenueAdapter: wraps PaperVenue, process_fills drains EventBus
- LiveVenueAdapter: wraps LiveExecution + CLOBRestClient, process_fills polls REST with dedup, complement routing + position cap in submit_order

## WalletAdapter ABC
- initial_balance, available_balance, locked_balance, total_equity, wallet_snapshot
- get_position, init_position, update_position_on_fill, positions, total_fees
- Optional: reconcile_on_chain (live-only, no-op default), on_chain, test_capital

## Implementation Phases (Luan)
1. Foundation: config.py + ABCs (no behavioral change)
2. Adapters: paper + live venue/wallet adapters wrapping existing classes
3. Pipeline: UnifiedTradingPipeline from ProductionTradingPipeline as base
4. Entrypoint: runner/__main__.py with --mode flag
5. Tests: behavioral equivalence tests + adapter unit tests
6. Deprecation shims in old runners

## Key Design Decisions
- ProductionTradingPipeline is the base (more features)
- Paper-specific logic behind `if self.mode == 'paper'` guards
- Balance-aware quoting stays in pipeline (shared, config-driven)
- Complement routing + trade dedup encapsulated in LiveVenueAdapter
- Existing imports via backward-compat shims (no test breakage)

## Scope
- ~2155 new lines across 12 files
- ~3575 deleted after deprecation
- Net: ~1420 lines saved (40% reduction)
- All 700+ tests must pass

## Luna Review Notes
- Cross-ref Lesson 1: complement consistency stays in adapter ✅
- Cross-ref Lesson 2: fill dedup stays in adapter ✅  
- Cross-ref Lesson 11: balance-aware quoting must be in shared pipeline, not adapter-only
- Ensure kill switch thresholds are configurable (paper uses different values)
