# Deep Dive A1 — Polymarket Rewards Farming (Mechanics + Market Selection)

## 1) Executive summary (3–5 bullets)

- **Polymarket “Liquidity Rewards” are paid for *resting limit orders*** (maker liquidity), not for fills; scoring is based on *how much size you quote* and *how close to the midpoint* you quote, sampled frequently and normalized vs other makers. Rewards are distributed daily at **midnight UTC**. Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md
- **Eligibility is market-specific** and controlled by two parameters: **`rewardsMinSize`** (min qualifying order size) and **`rewardsMaxSpread`** (max qualifying distance from midpoint). These parameters are exposed in market metadata (Gamma API) and in CLOB reward/market endpoints. Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md
- **Scoring is not simply “time-at-best”**. Any qualifying price level inside the max spread contributes, with a *quadratic* boost for tighter quotes. Queue position mostly affects *fill probability / adverse selection*, not the reward score directly (score is computed from the resting book snapshot).
- **Two-sided quoting is strongly favored** via a min() function across “two sides” of the synthetic combined book, with a single-sided penalty factor **`c` (currently 3.0)** when midpoint is in [0.10, 0.90]. Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md
- With **$220** capital, practical participation is usually limited by **inventory + need to quote both outcomes** (and sometimes multiple levels) rather than the minimum size itself (commonly **20 shares** in many active markets). Use 1–2 markets, keep quotes tight, and cap inventory drift.

---

## 2) Mechanics: eligibility + distribution (clear explanation + formulas)

### 2.1 What counts as “scoring” (eligibility)

Polymarket provides an order-scoring check endpoint:

- An order is “scoring” if:
  - it is live on a rewards-eligible market,
  - meets min size,
  - is within valid spread,
  - has been live for the required duration.

Source: https://docs.polymarket.com/api-reference/trade/get-order-scoring-status.md

**Important nuance:** the docs mention “required duration” but do not state the exact time threshold on that page. You can empirically infer it by submitting orders and querying `/order-scoring` until it flips to `true`.

### 2.2 Spread and size definitions

Liquidity Rewards compute score from orders relative to a **size-cutoff-adjusted midpoint**.

- `rewardsMaxSpread` defines a **maximum allowed distance from the midpoint**, noted in the Liquidity Rewards doc as **cents** (“v: max spread from midpoint (in cents)”).
- `rewardsMinSize` defines a **minimum qualifying size cutoff**.

Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md

### 2.3 Order scoring function (per order)

For an order at spread `s` (distance from adjusted midpoint), with maximum spread `v`:

- **Order scoring function** (quadratic):

\[ S(v, s) = ((v - s) / v)^2 \cdot b \]

Where:
- `v` = max qualifying spread (cents)
- `s` = order’s spread from adjusted midpoint
- `b` = in-game multiplier (documented variable; often treat as 1 unless you know a market has a multiplier)

Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md

**Interpretation:**
- If your quote is at the edge of eligibility (`s ≈ v`), score → 0.
- If your quote is near midpoint (`s ≈ 0`), score → 1·b.
- Moving 1 tick tighter near midpoint matters a lot more than moving 1 tick tighter near the boundary.

### 2.4 Market-level score from your entire book (depth contribution)

The doc defines two partial sums, conceptually rewarding depth on both outcomes and both sides.

- First side score (illustrative form):
  - sums **bids** on market `m` plus **asks** on complement `m′`
- Second side score:
  - sums **asks** on `m` plus **bids** on `m′`

Each term looks like:

\[ S(v, Spread) \cdot Size \]

Sources and worked example: https://docs.polymarket.com/market-makers/liquidity-rewards.md

### 2.5 Two-sided boost / single-sided penalty

Define `Q_one` and `Q_two` as the two side scores. Then the effective score per sample is:

If midpoint in **[0.10, 0.90]**:

\[ Q_{min} = \max(\min(Q_{one}, Q_{two}), \max(Q_{one}/c, Q_{two}/c)) \]

If midpoint near extremes **[0, 0.10) or (0.90, 1.0]**:

\[ Q_{min} = \min(Q_{one}, Q_{two}) \]

Where `c` is currently **3.0**.

Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md

**Interpretation for strategy:**
- In mid-probability markets, single-sided quoting still scores but at a **~3× penalty**.
- Near 0/1, you must quote **both sides** (two-sided) to score at all.

### 2.6 Sampling (“time-at-book”) and normalization

- The worked example states: **“Q is calculated every minute using random sampling.”**
- The equation section references **10,080 samples per epoch**, which equals 7 days × 1,440 minutes/day.

Then:

\[ Q_{normal} = Q_{min} / \sum_n (Q_{min})_n \]

\[ Q_{epoch} = \sum_u Q_{normal,u} \]

\[ Q_{final} = Q_{epoch} / \sum_n Q_{epoch,n} \]

And reward payout for a market is:

\[ payout = Q_{final} \times (market\ reward\ pool) \]

Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md

### 2.7 Distribution schedule + minimum payout

- Rewards are “distributed directly to maker addresses **daily at midnight UTC**.”
- Minimum payout is **$1**; below that is not paid.

Source: https://docs.polymarket.com/market-makers/liquidity-rewards.md

---

## 3) Where to fetch reward params (endpoints + examples)

### 3.1 Gamma API (market discovery / metadata)

Gamma exposes reward parameters directly on the market object:

- `rewardsMinSize`
- `rewardsMaxSpread`

Examples:

- List active markets:
  - https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=100&offset=0
- Fetch by slug:
  - https://gamma-api.polymarket.com/markets?slug=<event-slug>

Docs: https://docs.polymarket.com/market-data/fetching-markets.md

### 3.2 CLOB API (reward-specific, maker-specific analytics)

CLOB OpenAPI includes several **Rewards** endpoints (require auth unless noted).

Key endpoints (from `clob-openapi.yaml`): https://docs.polymarket.com/api-spec/clob-openapi.yaml

- **Check if an order is scoring** (requires L2 auth):
  - `GET https://clob.polymarket.com/order-scoring?order_id=<order_hash>`
  - Docs: https://docs.polymarket.com/api-reference/trade/get-order-scoring-status.md

- **User reward percentages (real-time)** (requires L2 auth):
  - `GET https://clob.polymarket.com/rewards/user/percentages`
  - Description: “map of condition_id to percentage of total rewards the user is currently earning in that market.” (see OpenAPI in clob spec)

- **User earnings and markets configuration** (requires L2 auth):
  - `GET https://clob.polymarket.com/rewards/user/markets?date=YYYY-MM-DD`
  - Returns per-market fields including:
    - `rewards_max_spread`, `rewards_min_size`
    - `earning_percentage`
    - **`rate_per_day`, `total_rewards`, `remaining_reward_amount`** via `RewardsConfig`

These endpoints are the cleanest way to estimate **reward/capital** once you can authenticate.

### 3.3 Maker rebates (separate from Liquidity Rewards)

Polymarket also has a **Maker Rebates** program on specific market types (not all markets), paid daily in USDC and based on *filled maker liquidity*.

- Program description + formula: https://docs.polymarket.com/market-makers/maker-rebates.md
- Public endpoint (no auth) to query current rebated fees for a maker address:
  - `GET https://clob.polymarket.com/rebates/current?date=YYYY-MM-DD&maker_address=0x...`
  - Docs: https://docs.polymarket.com/api-reference/rebates/get-current-rebated-fees-for-a-maker.md

**Rebates are fundamentally different:** you only earn if you get filled.

### 3.4 If you *don’t* have auth / endpoints don’t expose public pools

If you cannot access `rate_per_day` / pool sizes via `/rewards/user/markets`:

- **Empirical approach:**
  1) Quote continuously for 24–72h.
  2) Track `earning_percentage` (if available) or infer from actual daily payouts.
  3) Back out implied pool size: `pool ≈ payout / percentage`.

- **Onchain approach (harder):** identify reward-asset transfers to maker addresses and aggregate by market/epoch (requires mapping payouts → markets, which may not be directly labeled).

---

## 4) Market selection heuristics for our capital ($220)

### 4.1 Constraints that matter for small capital

With $220, the binding constraints are:

1) **Two-sided requirement** (to avoid 3× penalty or “no score” near extremes)
2) **Inventory drift** (one side fills repeatedly)
3) **Minimum order size** (`rewardsMinSize`) times number of levels × number of markets

In recent active-market samples via Gamma, `rewardsMinSize` is commonly around **20** and `rewardsMaxSpread` around **3.5** (units per Gamma; Liquidity Rewards doc describes v in cents).

### 4.2 Heuristics (practical ranking)

Pick markets where you can keep quotes live continuously with minimal adverse selection:

- **Midpoint not too extreme** (prefer 0.20–0.80) so single-sided emergency quoting still scores (penalized) and spreads are meaningful.
- **High and stable volume** (volume24hr) so makers compete but also so the market stays “alive” and mid is stable.
- **Reasonable `rewardsMaxSpread`**: larger max spread makes it easier to qualify but encourages wide quoting; since scoring is quadratic, you still want to quote tight.
- **Low/medium volatility subjects** (avoid markets driven by sudden news releases unless you can react fast): sports moneylines close to start time, crypto event windows, etc.
- **End date not too far** (capital isn’t trapped too long by accidental inventory). Prefer markets resolving within days–weeks during experimentation.

### 4.3 What to avoid with $220

- Markets with midpoint near **0 or 1** (must be two-sided to score; inventory gets “sticky”).
- Highly news-sensitive political markets where you can be picked off.
- Markets where you cannot meet **both** sides’ minimum size across levels.

---

## 5) Sizing guidance vs `rewardsMinSize` and capital constraints

### 5.1 Interpreting `rewardsMinSize`

In Liquidity Rewards equations, size is **share-denominated** (BidSize / AskSize are quantities of shares). `rewardsMinSize` is therefore best treated as a **minimum share size per order** for scoring.

### 5.2 A workable “$220” quoting template

Assume:
- `rewardsMinSize = 20 shares`
- price near 0.50

Then cost to place a BUY order of 20 shares is about **$10** collateral (20 × 0.50). For SELL, you need inventory (shares) or need to split USDC into both outcomes.

**Template (1 market, 2-sided, 1 level):**
- Bid YES: 20 shares @ (mid − 1 tick)
- Ask YES (or equivalently bid NO): 20 shares @ (mid + 1 tick)

Capital planning:
- Reserve ~40–80 USDC for safety / rebalancing.
- Allocate ~120–160 USDC to the active quoting inventory + working collateral.

**Template (1 market, 2 levels each side):**
- Level 1: 20 shares
- Level 2: 20 shares
- Total: ~80 shares “advertised” depth across both sides (but actual locked collateral depends on split/positions).

### 5.3 When to run 2 markets

Only run 2 markets when:
- you can still keep **two-sided** quotes in both,
- and you can handle simultaneous inventory swings.

For early proof, **1 market** is preferred.

---

## 6) Expected monthly return simulation (conservative / expected / optimistic)

### 6.1 What we can compute exactly vs what requires auth

Exact estimation needs:
- market pool size (e.g., `rate_per_day`), and
- your real-time `earning_percentage`.

These are available via authenticated CLOB rewards endpoints (see §3.2). Without auth/pool data, we must simulate using assumptions.

### 6.2 Simple model

For a single market:

- `DailyRewardsPool` = market’s rewards budget per day (from `RewardsConfig.rate_per_day`)
- `OurShare` = our normalized share of Q across makers (observable as earning percentage)

Then:

- **Expected daily reward** = `DailyRewardsPool × OurShare`
- **Monthly reward** ≈ daily × 30
- **Reward / capital** = monthly / 220

### 6.3 Assumption set (explicit)

Given small capital and competition, plausible `OurShare` ranges:
- Conservative: 0.02% (2 bps)
- Expected: 0.10% (10 bps)
- Optimistic: 0.30% (30 bps)

For `DailyRewardsPool`, without direct data we parameterize three cases:
- Low: $50/day
- Medium: $150/day
- High: $300/day

### 6.4 Scenario table

| Scenario | Pool ($/day) | OurShare | $/day | $/mo | %/mo on $220 |
|---|---:|---:|---:|---:|---:|
| Conservative | 50 | 0.02% | 0.01 | 0.30 | 0.14% |
| Expected | 150 | 0.10% | 0.15 | 4.50 | 2.0% |
| Optimistic | 300 | 0.30% | 0.90 | 27.0 | 12.3% |

**Interpretation:** with $220, rewards are typically small unless (a) pools are large, and (b) you consistently hold a meaningful share of the scoring liquidity. This is why **market selection + tight uptime + two-sided depth** matters.

### 6.5 How to turn this into a real forecast (actionable)

Once authenticated:
1) Pull `/rewards/user/markets?date=YYYY-MM-DD` to read `rate_per_day`.
2) Pull `/rewards/user/percentages` for `OurShare` per market.
3) Compute reward/capital and rotate to the best markets.

---

## 7) Risks + mitigations

### 7.1 Adverse selection (picked off when price moves)

**Risk:** tight quotes near midpoint get filled right before adverse moves, causing inventory loss that exceeds rewards.

**Mitigations:**
- Quote wider during known news windows; reduce size.
- Use multi-level quoting with smaller top-of-book size.
- Cancel/replace quickly on midpoint moves (WebSocket feeds).

### 7.2 Inventory drift (one-sided fills)

**Risk:** you accumulate YES or NO and can’t keep two-sided quotes, losing score or taking directional risk.

**Mitigations:**
- Enforce inventory caps; when hit, skew prices to mean-revert inventory.
- Use split/merge mechanics to rebalance (see MM inventory docs).
- Prefer mid-probability markets where both sides trade.

### 7.3 Resolution / settlement risk

**Risk:** capital stuck until resolution; edge-case disputes.

**Mitigations:**
- Prefer shorter-dated markets during experimentation.
- Avoid ambiguous resolution sources; verify `resolutionSource` and terms.

### 7.4 Reward-program parameter changes

**Risk:** Polymarket can change reward rates, min size, max spread, multipliers.

**Mitigations:**
- Read `rewardsMinSize`/`rewardsMaxSpread` daily via Gamma.
- Monitor `rate_per_day` and remaining rewards via CLOB endpoints.

### 7.5 “Scoring but not paid” edge cases

**Risk:** payouts below $1 are not paid; you can waste effort.

**Mitigation:** estimate daily payout and ensure it exceeds $1/day or at least $1/epoch.

---

## 8) Recommended next research questions

1) **What is the exact “required duration”** before an order becomes scoring? Measure via `/order-scoring` polling after posting a qualifying order.
2) **Is the epoch 7 days in production?** The doc uses 10,080 samples (weekly), but payouts are daily; confirm how daily payouts map to epoch scoring.
3) **Do any markets have `b` multipliers** (“in-game multiplier”)? If yes, where is it exposed (Gamma vs CLOB)?
4) For small capital, what is the **minimum viable `OurShare`** to clear the $1 payout threshold?
5) Can we compute competitor concentration from public books (e.g., sum of scoring depth inside v), and forecast `OurShare` *before* deploying capital?

---

## Appendix A — Practical implementation notes (Tor / US server)

- Gamma API is generally accessible directly: `https://gamma-api.polymarket.com/...`.
- CLOB API often needs a proxy/Tor from US-hosted infrastructure (per project constraints). Configure HTTP client with SOCKS5 proxy for `https://clob.polymarket.com/...`.

## Appendix B — Notes on third-party research availability (X/Reddit/Dune)

- This environment could not reliably fetch Dune dashboards due to Cloudflare blocks (HTTP 403 from dune.com). If needed, search manually from a browser and then we can ingest specific dashboard URLs via `web_fetch` if accessible.
- Official Polymarket docs already specify the core scoring math; third-party MM threads would mainly add *operational* details (uptime tricks, typical `rate_per_day` pools, observed duration-to-score, and competition patterns). Recommended to gather 3–5 such threads and validate against `/order-scoring` + `/rewards/user/percentages` empirics.

