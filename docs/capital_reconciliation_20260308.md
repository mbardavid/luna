# Capital Reconciliation

Generated at: 2026-03-08T02:37:00Z

## Verdict
The money did not disappear from the total stack.

- Total stack capital: 229.03 USD
- PMM-local capital: 56.75 USD
- Difference: 172.28 USD

The difference is mainly outside the PMM collector, especially on Solana.

## Current capital by chain
- Solana: 170.19 USD
- Polygon: 58.78 USD
- Arbitrum: 0.06 USD
- Base: 0.00 USD
- Hyperliquid: 0.00 USD

## PMM-local state
- PMM local total: 56.7522 USD
- Free collateral: 56.218396 USDC
- Recoverable inventory: 0.1550 USD

## PMM realized PnL from ledger
### By run
- prod-001: 2.197622656175822291371121991
- prod-002: 0
- prod-003: 0
- prod-004: -45.92756718350635144829944814
- prod-005: -0.0875

### By decision
- quant-20260306T033353Z: 36.45343414528616509919779007
- quant-20260306T142026Z: 50.18166115280049487535263558
- quant-20260306T230134Z: -132.5626624815930114228498738
- quant-20260307T165353Z: -0.0875

## Interpretation
- The PMM did lose money, but not ~170 USD of total-stack capital.
- The largest verified PMM loss block was `quant-20260306T230134Z` with about `-132.56 USD`.
- Earlier decision blocks partially offset that loss.
- The reason the balance looked like ~57 USD is that the PMM collector only sees Polymarket/Polygon capital.
- The broader stack still holds most capital outside that collector, mainly on Solana.

## Evidence
- `/home/openclaw/.openclaw/workspace/polymarket-mm/paper/data/stack_capital_latest.json`
- `/home/openclaw/.openclaw/workspace/polymarket-mm/paper/data/trades_production.jsonl`
- `/home/openclaw/.openclaw/workspace-crypto-sage/memory/2026-02-26.md`
- `/home/openclaw/.openclaw/workspace/memory/2026-03-02.md`
- `/home/openclaw/.openclaw/workspace-crypto-sage/state/audit.jsonl`
