# Plan Review — Market Rotation + Capital Recovery

**Reviewer:** Luna
**Date:** 2026-03-02T19:15:00Z
**Decision:** APPROVED

## Review Notes

1. **Architecture is additive** — 2 new files + config additions + pipeline integration. No rewrites of existing code. Low regression risk.
2. **Complement consistency** (Lesson 1) — Luan's plan explicitly addresses this in CapitalRecovery (complement routing for SELL orders) and in tests.
3. **USDC normalization** (Lesson 3) — Plan calls out normalization at API boundary. Good.
4. **WS client restart** — Correctly identified as the main challenge. Approach (stop → new → start) is safe because quote loop already handles missing data gracefully.
5. **Health score formula** — Simple average of 3 normalized components (spread, fill_rate, skew). Good starting point. Can tune weights later via quant agent.
6. **Capital recovery sells most profitable first** — Correct strategy (lock in gains, keep losers that may recover).
7. **30 tests planned** — Adequate coverage for 3 new modules.

## Concerns (minor, non-blocking)
- `auto_select_markets()` is currently broken (returns 3000 markets, filter finds 0). Luan should check and fix if needed, or use `discover_markets.py` Gamma API as fallback.
- Rotation blacklist (recently-rotated markets) should persist across restarts.

## Verdict
Plan approved for Phase 2 implementation.
