# Market selection for prod-004 (Rewards-farming MM) — as of 2026-03-03 (UTC)

**Capital:** ~$220 USDC

**Data sources:**
- Market metadata + rewards params: Gamma API `GET https://gamma-api.polymarket.com/markets/<market_id>`
- Best bid/ask snapshot: CLOB `GET https://clob.polymarket.com/book?token_id=<YES_token_id>` (via Tor socks5 `127.0.0.1:9050`)

**Rewards eligibility knobs (discoverable):**
- `rewardsMinSize` (USDC notional per order)
- `rewardsMaxSpread` (max allowed spread for rewards; **likely in cents**, returned as e.g. `4.5`)

**Practical eligibility check (empirical, if uncertain):**
1) Post two-sided quotes where (a) each order size ≥ `rewardsMinSize` and (b) your quoted spread ≤ `rewardsMaxSpread` converted to price units (e.g. `4.5c` → `0.045`).
2) Verify rewards accrual by polling Gamma market fields (if they change) and/or by checking bot-side “rewards earned” telemetry if available. If no direct API exists in-bot, treat it as “eligible if quoting within constraints for >15–30 min without being cancelled/invalidated”.

---

## Recommended markets (3–5)

All below are **active**, **short duration**, have **rewards params present**, and have **non-1bp spreads** at the time of snapshot.

### 1) Will India win the 2026 ICC Men’s T20 World Cup?
- **market_id:** `956344`
- **conditionId:** `0xfa4ea5b78e44fe46b1cca9e1452293e3d4def76af29a7628905be72606d5763f`
- **CLOB token IDs:**
  - YES `36190803750786635844165995661794551716343290080007015981336350366434982775233`
  - NO  `85713399276764589733546658542324459490852641984839087495926728837133909240937`
- **End:** `2026-03-08T00:00:00Z` (~5d)
- **Top of book (YES token):** bid **0.45**, ask **0.46**
  - mid **0.455**
  - spread **1.0c** (≈ **220 bps**)
- **24h vol (CLOB):** ~**$63,670**
- **Rewards constraints (Gamma):** `rewardsMinSize=20`, `rewardsMaxSpread=3.5`
- **Why it fits ~$220:** low `rewardsMinSize` (20) lets us farm with small quotes; 1c spread is within max; enough flow without being a hyper-efficient 1bp market.

### 2) UFC 326: Charles Oliveira vs. Max Holloway (Lightweight, Main Card)
- **market_id:** `1378736`
- **conditionId:** `0xf6447769c6c2a6931470176868cfa58162dd72f1028d7df7f2dcc0f260c0668c`
- **CLOB token IDs:**
  - YES `88713016820521041488017537571262578754923104954111239847575403193985649381805`
  - NO  `70680997252637870845060297313707679888294694430159052880614680512560318175930`
- **End:** `2026-03-08T04:59:59.999Z` (~5d)
- **Top of book (YES token):** bid **0.33**, ask **0.34**
  - mid **0.335**
  - spread **1.0c** (≈ **299 bps**)
- **24h vol (CLOB):** ~**$62,459**
- **Rewards constraints (Gamma):** `rewardsMinSize=50`, `rewardsMaxSpread=4.5`
- **Why it fits ~$220:** clean event-driven market with manageable min size (50) and ~1c spread; can run ~1–2 quote levels per side without over-allocating inventory.

### 3) LoL: Anyone's Legend vs Invictus Gaming (BO5) - LPL Playoffs
- **market_id:** `1475920`
- **conditionId:** `0x495777e33fccba546f9014a64e8458c4f4d5e7f5d352240aec4cc3ab5cbce746`
- **CLOB token IDs:**
  - YES `91754413059934119247212318808485989425767592851689217825654270058437859450427`
  - NO  `65035974056345418719338746671086722478345366282849360922826147098241530902161`
- **End:** `2026-03-03T15:00:00Z` (<1d)
- **Top of book (YES token):** bid **0.90**, ask **0.91**
  - mid **0.905**
  - spread **1.0c** (≈ **110 bps**)
- **24h vol (CLOB):** ~**$123,092**
- **Rewards constraints (Gamma):** `rewardsMinSize=50`, `rewardsMaxSpread=4.5`
- **Why it fits ~$220:** short-duration, decent volume, not a 1bp book; high price regime means inventory swings are smaller in USDC terms for a given share count.

### 4) Will the price of Bitcoin be between $66,000 and $68,000 on March 3?
- **market_id:** `1428025`
- **conditionId:** `0x0503e6d6db91af814aa9918cf6e4ae7656cef96cb321bffcccaea22fd3643402`
- **CLOB token IDs:**
  - YES `97991882010777221115103098392605458148578487709489360009089965769122793342939`
  - NO  `50564339805690184618524952122920426973396008471676595689603513527144830468652`
- **End:** `2026-03-03T17:00:00Z` (<1d)
- **Top of book (YES token):** bid **0.30**, ask **0.33**
  - mid **0.315**
  - spread **3.0c** (≈ **952 bps**)
- **24h vol (CLOB):** ~**$96,724**
- **Rewards constraints (Gamma):** `rewardsMinSize=50`, `rewardsMaxSpread=4.5`
- **Why it fits ~$220:** spread is meaningfully wider than typical “above X” crypto lines, so less likely to be crowded to 1bp; still high volume for fills/rewards.

### 5) Will US or Israel strike Iran on March 6, 2026?
- **market_id:** `1465971`
- **conditionId:** `0x3205072a9cd4b1f753af22c124b6bce288bf8363797cb4d47d842c4c4a1afc3e`
- **CLOB token IDs:**
  - YES `35130268332935224475565286115604768269756272331656645480839283909873103251104`
  - NO  `107146222609347176293391971937639421859973026690287048623458488832420148693044`
- **End:** `2026-03-10T00:00:00Z` (~7d)
- **Top of book (YES token):** bid **0.94**, ask **0.95**
  - mid **0.945**
  - spread **1.0c** (≈ **106 bps**)
- **24h vol (CLOB):** ~**$133,768**
- **Rewards constraints (Gamma):** `rewardsMinSize=50`, `rewardsMaxSpread=4.5`
- **Why it fits ~$220:** event market tends to keep spreads >1c with fewer ultra-HFT participants vs. top crypto lines; good volume; fits min size 50.

---

## Proposed `paper/runs/prod-004.yaml` snippet

```yaml
run_id: prod-004
mode: paper
base_currency: USDC
notional_cap_usdc: 220

# Target: rewards-farming quotes that satisfy rewardsMinSize and rewardsMaxSpread.
# Keep per-market inventory small to avoid large event/settlement swings.
markets:
  - market_id: 956344
    label: india-t20-win
    target_inventory_usdc: 40
    order_size_usdc: 20   # matches rewardsMinSize
    max_spread_cents: 3.5 # rewardsMaxSpread (Gamma)

  - market_id: 1378736
    label: ufc-oliviera-vs-holloway
    target_inventory_usdc: 45
    order_size_usdc: 50
    max_spread_cents: 4.5

  - market_id: 1475920
    label: lol-anyones-legend-vs-ig
    target_inventory_usdc: 40
    order_size_usdc: 50
    max_spread_cents: 4.5

  - market_id: 1428025
    label: btc-between-66-68-mar3
    target_inventory_usdc: 45
    order_size_usdc: 50
    max_spread_cents: 4.5

  - market_id: 1465971
    label: strike-iran-mar6
    target_inventory_usdc: 45
    order_size_usdc: 50
    max_spread_cents: 4.5
```

**Sizing note:** sum target_inventory ≈ $215; leaves a few dollars for fees / drift. If runner requires fewer markets, drop the shortest (LoL) first.
