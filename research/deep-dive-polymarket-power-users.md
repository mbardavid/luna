# Deep Dive C8 — Polymarket Power Users & Top Market Makers

> **Note on data limitations (important):**
> - The built-in `web_search` tool is unavailable in this environment (missing Brave API key).
> - The interactive browser tool is also unavailable (no supported browser installed).
> - Reddit/X threads could not be reliably accessed due to blocking/403.
>
> Because of this, this deep dive leans heavily on **official Polymarket documentation** + **public Dune dashboards** (accessible via `r.jina.ai` fetch proxy) and is explicit where wallet-level “top MM” attribution is **not possible**.

## 1) Executive summary

- **Two main “pro” maker profit streams exist on Polymarket CLOB:**
  1) **Liquidity Rewards** (paid for *resting* qualifying limit orders; score depends on *size* and *distance to midpoint*, sampled frequently; strongly favors **two-sided depth**). Source: Liquidity Rewards docs.
  2) **Maker Rebates** (paid from **taker fees** on specific market types; rebates depend on **filled maker volume**, so queue priority / being at touch matters a lot more). Source: Maker Rebates + Fees docs.

- **The Liquidity Rewards formula structurally creates “reward farming” behavior:** makers maximize score by (i) quoting *inside max spread*, (ii) concentrating size very close to the midpoint (quadratic utility), and (iii) keeping **balanced exposure** across complementary books (YES/NO). Source: Liquidity Rewards equations.

- **“Top market makers” in the sense of profitable actors are hard to name from public data alone**, because: (i) Polymarket does not publish a canonical leaderboard of maker PnL; (ii) many makers use multiple wallets and smart-account/proxy flows; (iii) Dune dashboards often show *holders, volume, OI* rather than *maker identity*. We can, however, identify **large on-platform USDC/proxy holders** and **high-volume markets** where pro MMs concentrate.

- **Where the edge concentrates (practically):**
  - Fee-enabled **crypto** and certain **sports** (as of Feb–Mar 2026) are structurally important for **maker rebates** because they are the *only* places with taker fees that fund rebates. Source: Fees docs.
  - Liquidity Rewards exist broadly; the best ROI tends to be in markets where (a) rewards pool is meaningful, (b) competition is lower, and (c) you can maintain two-sided quoting without getting run over by informed flow.

- **With $220 capital**, competing with power users in the most liquid headlines (major politics, hottest crypto) is usually negative EV unless you have (i) latency/infrastructure, (ii) robust inventory & risk controls, and/or (iii) an information edge. A more realistic angle is “small-maker participation”: pick 1–2 markets, satisfy min-size, quote narrowly but not at the very top tick if you’re getting picked off.

## 2) Links / fontes (>=10)

### Official Polymarket documentation (mechanics)
1. Liquidity Rewards (methodology + equations): https://docs.polymarket.com/market-makers/liquidity-rewards
2. Maker Rebates Program: https://docs.polymarket.com/market-makers/maker-rebates
3. Fees (which markets have taker fees; fee formula; rebate %): https://docs.polymarket.com/trading/fees
4. Prices & Orderbook (core microstructure description): https://docs.polymarket.com/concepts/prices-orderbook
5. Matching Engine Restarts (HTTP 425 handling; operational risk): https://docs.polymarket.com/trading/matching-engine

### Public analytics / Dune dashboards (competition, volume, segments)
6. Dune — Polymarket Overview (@datadashboards) (category segmentation + top “proxies”/holders table): https://dune.com/datadashboards/polymarket-overview
7. Dune — Polymarket Market Breakdown (@datadashboards): https://dune.com/datadashboards/polymarket-market-breakdown
8. Dune — Polymarket Activity and Volume (@filarm): https://dune.com/filarm/polymarket-activity
9. Dune — Polymarket Analysis (@lujanodera): https://dune.com/lujanodera/polymarket-analysis
10. Dune — Polymarket Historical Accuracy and Bias (@alexmccullough): https://dune.com/alexmccullough/how-accurate-is-polymarket
11. Dune — Prediction Markets (multi-venue context; includes Polymarket): https://dune.com/datadashboards/prediction-markets
12. Dune — Polymarket Address Tracker: Potential Airdrop Eligibility (@seoul): https://dune.com/seoul/poly

### SDK / client code (how sophisticated makers connect & operate)
13. GitHub — Polymarket TypeScript CLOB client: https://github.com/Polymarket/clob-client
14. GitHub — Polymarket Python CLOB client: https://github.com/Polymarket/py-clob-client

## 3) Patterns / tactics observed (and why they work)

### 3.1 Liquidity Rewards “farm” tactics (score-maximization)
Source for the core scoring logic: https://docs.polymarket.com/market-makers/liquidity-rewards

Key mechanics from the docs:
- Order utility is **quadratic in tightness**:  \(S(v,s) = ((v-s)/v)^2 \cdot b\).
- Per-sample market-side scores are **size-weighted** sums of \(S(v,Spread)\cdot Size\) across price levels.
- Two-sided quoting is strongly favored via:
  - if midpoint in **[0.10, 0.90]**, single-sided still scores but is divided by **c (currently 3.0)**;
  - near extremes, **must be two-sided** to score.

Implications / tactics:
- **Cluster size near midpoint** (not at the boundary). Because the score decays quadratically as you move away from midpoint, 1–2 ticks tighter can dominate many ticks wider.
- **Keep both outcomes quoted** (YES and NO) and on both effective sides of the combined book; otherwise you eat the `c=3` penalty or score 0 near extremes.
- **Avoid “cancel/replace too often” if there is a scoring warm-up.** The docs reference that an order needs to be live long enough to be “scoring” (the precise duration is not clearly stated on the Liquidity Rewards page itself; it’s typically checked via the `/order-scoring` endpoint in the OpenAPI). Frequent cancels can reduce time-in-book at sample times.
- **Size shaping / multiple levels:** rather than a single huge quote, makers often ladder within the max spread to collect depth score while controlling fill risk. With small capital, you do a tiny ladder (1–2 levels).

### 3.2 Queue tactics (more relevant for rebates / fills than for Liquidity Rewards)
- Liquidity Rewards are computed from **book snapshots** (sampled frequently). The docs emphasize tightness and depth; they do **not** explicitly reward “time at best” or queue rank.
- **Maker Rebates**, however, depend on **filled maker volume** (rebated from taker fees on eligible markets), so being early in queue at the touch and maintaining presence during spikes matters more.

Practical queue behavior seen in competitive CLOBs:
- **Cancel/replace at the same price** to regain best position is usually harmful (you go to the back). Better is:
  - keep a “sticky” quote at best that you rarely cancel,
  - manage risk by adjusting *size* or widening *second level*,
  - only cancel the touch quote when information changes (to avoid being picked off).

### 3.3 “Spread capture + hedging” (cross-market / complement hedges)
- Since YES/NO are complements, makers often treat inventory as a **paired book** problem (the Liquidity Rewards formula itself references complements `m'`).
- Pro makers frequently hedge via:
  - taking the other side in the complement book,
  - shifting quotes to rebalance inventory,
  - (where possible) offsetting correlated exposure across related markets/events.

### 3.4 High-volume event focus
From Dune activity tables (example dashboard): https://dune.com/filarm/polymarket-activity
- Power users concentrate where **volume is large**, because:
  - spreads are tighter and turnover is higher (more opportunities),
  - if the market is fee-enabled, maker rebates scale with filled volume.
- Trade-off: high volume also tends to mean **higher adverse selection** + tighter competition.

## 4) Examples of wallets / entities (public only)

### 4.1 “Large holders / proxies” surfaced in Dune
The @datadashboards Polymarket Overview dashboard includes a table “TVL + USDC Held In Polymarket Proxies” with addresses and amounts. This is **not guaranteed to be “top market makers”**, but it is a credible public signal of **large capital** present on-platform.

Examples shown in the dashboard snapshot (amounts as displayed there at time of dashboard update):
- https://polymarket.com/@0x9d84ce0306f8551e02efef1680475fc0f1dc1344 (≈ $1.27m)
- https://polymarket.com/@0xc8ab97a9089a9ff7e6ef0688e6e591a066946418 (≈ $393k)
- https://polymarket.com/@0x1cfc260bfa2b5ae89377863180ca3b4f5c862111 (≈ $382k)
- https://polymarket.com/@0x7789806c754eb0af4c3abeb026c218b4ba78f823 (≈ $353k)
- https://polymarket.com/@0x3cf3e8d5427aed066a7a5926980600f6c3cf87b3 (≈ $296k)

Source dashboard: https://dune.com/datadashboards/polymarket-overview

### 4.2 What we *cannot* attribute from public data here
- We do **not** have a reliable, public mapping of “wallet → market maker bot → PnL/rewards” across all markets.
- Without authenticated `/rewards/user/*` endpoints or a dedicated onchain attribution dataset, we cannot conclusively list “top MMs” by rebates/rewards earned.

## 5) Recommendations for our context ($220 capital, Tor setup)

### 5.1 Choose which program you’re targeting
- If you’re targeting **Liquidity Rewards**, optimize for:
  - meeting `rewardsMinSize` and staying within `rewardsMaxSpread`,
  - two-sided quoting,
  - staying online reliably (sampling is frequent; downtime kills share).

- If you’re targeting **Maker Rebates**, first confirm you’re in **fee-enabled markets** and that your strategy can actually get filled without getting picked off.
  - Fee-enabled market types (as of the fees doc): **crypto markets** and specific sports (NCAAB, Serie A) starting Feb–Mar 2026 for new markets. Source: https://docs.polymarket.com/trading/fees

### 5.2 Practical micro-strategy with $220
- **Run 1 market at a time** (maybe 2 if min-size is small and spreads are wide). You need capital to quote both outcomes.
- **Quote 1 level per side** near the midpoint, small size (e.g., 20–50 shares depending on price), then optionally a second level wider for “inventory cushion”.
- Prefer mid-probability ranges **0.20–0.80** where single-sided scoring is allowed (but still penalized); you still want two-sided, but inventory shocks are less pathological than near 0/1.

### 5.3 Tor / ops
- Since your environment uses Tor for CLOB calls, focus on **reliability over latency**:
  - handle matching engine restarts / HTTP 425 with exponential backoff per docs. Source: https://docs.polymarket.com/trading/matching-engine
  - avoid frantic cancel/replace loops that will both (i) worsen queue, and (ii) increase operational failure rate.

## 6) What to avoid + red flags of hyper-competitive books

- **Red flag: always-1-tick markets with massive displayed depth at touch** (often multiple bots refreshing). These are usually dominated by sophisticated MMs; $220 makers will get:
  - minimal fill share,
  - high adverse selection when news hits,
  - negligible relative reward share unless the pool is huge.

- **Fee-enabled + ultra-liquid crypto** can be especially hard:
  - taker fee exists → rebates attract professional flow,
  - but informed flow is intense → you’ll be picked off on direction.

- **Markets near 0 or 1** when farming Liquidity Rewards:
  - the rewards formula requires **two-sided** liquidity to score at all in the extremes. Source: Liquidity Rewards doc.
  - if you cannot sustain both sides without blowing inventory, avoid.

- **Over-refreshing / cancel-replacing constantly**:
  - harms queue priority,
  - can reduce time-in-book at sample times,
  - increases failed-order operational errors.

## 7) Next research questions (to close the “power user” gap)

1. Can we obtain a dataset of **maker rebates paid per wallet** (e.g., via CLOB public rebate endpoints + onchain transfers) to rank true top MMs?
2. For Liquidity Rewards, what is the exact **minimum live duration** for an order to become “scoring” (via `/order-scoring` experimentation), and how sensitive is score to cancel/replace cadence?
3. Which specific markets currently have the **largest rewards pools** (`rate_per_day` / remaining rewards) and what is their maker concentration (HHI / top-5 share)?
4. Do any Dune dashboards publish “top traders by realized PnL” vs “top by volume”? (volume ≠ profitability).
5. How often do top makers run **multi-wallet sybil** strategies to increase reward share, and what heuristics detect it onchain?

---

## Appendix — Why the Liquidity Rewards formula creates “power user” advantages

From the official equations (Liquidity Rewards doc):
- Tightness is quadratically rewarded, which incentivizes **high-frequency micro-adjustments** around midpoint.
- Two-sided min() strongly incentivizes balanced capital deployment (larger bankroll helps).
- Scores are relative-share normalized, so large makers can “crowd out” small makers in a given market unless the pool is large or competition is thin.
