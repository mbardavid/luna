# Plan — Audit: Crypto-sage balance discrepancy (missing positions/open orders)
**MC Task ID:** 6f11b9d9-95f2-47cf-9f93-4da3cf2b89b3  
**PHASE:** planning (DO NOT IMPLEMENT)  
**Goal:** explain why `/saldo` (crypto-sage) reported total balance far below expected; produce a technical audit checklist + a correction design to make portfolio “source of truth” accurate (cash + positions + open orders/locked + margin) and prevent regression.

---

## 0) What we already know (strong signal)
From `memory/2026-03-02.md` (workspace):
- Crypto-sage previously reported **$178 instead of ~$401** because **Polymarket CTF (ERC-1155) positions were not counted**.
- Root cause at the time: `POLYMARKET_POSITIONS_JSON` **env var missing from systemd drop-in** → connector never loaded positions.
- Fix described: on-chain scan found multiple YES/NO positions; after env was loaded, portfolio totals matched reality.

**Implication:** the current discrepancy is very likely another instance of “hidden value” living outside the simple spot token balance path:
- ERC-1155 positions (Polymarket CTF) OR
- reserved/locked collateral in open orders OR
- perp/margin account value not included.

---

## 1) Most likely causes (prioritized) + where to look / evidence to gather

### P1 — Positions not included (CTF ERC-1155 / derivatives positions)
**Symptom pattern:** wallet has “low free USDC”, but real net worth is higher when including non-ERC20 assets.

**Sub-causes to check:**
1) **Polymarket CTF ERC-1155 positions** not being fetched (or fetched via stale config).
   - Evidence: on-chain explorer (Blockscout) shows ERC-1155 tokens, but `/saldo` output lacks them.
   - Code/config likely involved:
     - connector reading `POLYMARKET_POSITIONS_JSON` (mentioned previously as `workspace-crypto-sage/src/connectors/portfolio.mjs`, even if this repo snapshot doesn’t contain it).
     - systemd env drop-in: `crypto-sage-env.conf` / drop-in referenced in `logs/gateway-safe-restart.log` (“Load POLYMARKET_POSITIONS_JSON env var for crypto-sage /saldo”).
   - Failure modes:
     - env var missing in systemd context (works in shell but not in gateway)
     - JSON malformed / schema drift
     - token_id missing (ex: unknown NO token discovered previously)
     - RPC provider returning incomplete ERC-1155 (Polygon RPC issues); need Blockscout fallback.

2) **Hyperliquid perp positions / account value** not included.
   - Evidence: HL UI shows open perp position or nonzero “account value”, but `/saldo` only shows spot/withdrawable.
   - Failure modes:
     - connector queries `spot balances` but not `clearinghouse/accountState`
     - ignores `unrealizedPnl`, `positionValue`, `marginSummary.accountValue`.

3) **Open orders** not included (reserved collateral).
   - Evidence: venue shows open orders, but `/saldo` counts only free USDC; reserved/locked in orders is missing.


### P2 — Wrong asset identity / chain or token variant mismatch (USDC vs USDC.e, wrapped variants)
**Symptom pattern:** user “knows there is USDC”, but it’s on another chain or another contract (USDC.e).

- Evidence to collect:
  - Compare on-chain balances per chain + per token contract.
  - Spot-check: USDC address differs on Polygon vs Arbitrum vs Base, and USDC.e contract differs from native USDC on some chains.
- Failure modes:
  - aggregator keys by **symbol** only → overwrites or double-counts
  - price lookup by symbol fails (USDC.e not mapped) → priced as $0 or omitted.


### P3 — Decimals / normalization / parsing bugs
**Symptom pattern:** one asset shows 0.000xxx or 1e-12 of expected; totals way off.
- Failure modes:
  - ERC20 decimals assumed 18 for USDC (should be 6)
  - string → float conversions truncating
  - CTF shares / ERC-1155 amounts treated as int without scaling


### P4 — Credential / env / routing failure (connector silently disabled)
**Symptom pattern:** `/saldo` output missing an entire venue section.
- Evidence:
  - logs show 401/403, “missing API key”, or connector returns empty set
  - systemd env drop-in not loaded / gateway restart not applied
- Failure modes:
  - env present in interactive shell but not in systemd/gateway
  - a connector throws and aggregator swallows error → “0” instead of “error + partial results”.

---

## 2) Audit checklist — what to verify per venue (source of truth)

### A) Global / cross-cutting checks (do first)
1) **Confirm runtime env context** for the `/saldo` execution path:
   - Which process runs it (gateway/systemd)?
   - Which env files are loaded (drop-ins)?
   - Verify presence of required env vars (examples):
     - `POLYMARKET_POSITIONS_JSON`
     - Hyperliquid keys / wallet address
     - RPC URLs per chain (Polygon/Arbitrum/Base)

2) **Define the accounting model** used in `/saldo`:
   - Are we reporting:
     - *Free/available* cash only, or
     - *Net liquidation value* (cash + positions + unrealized PnL), or
     - Both (recommended).

3) **Invariants to test during audit**:
   - USDC-family stablecoins should price near $1.
   - Total = sum(components) where components are explicitly labeled: `free`, `locked`, `margin`, `positions_value`, `unrealized_pnl`.
   - If a connector fails: report “partial” with error metadata; do not silently treat as zero.


### B) Polymarket (CTF + CLOB)
**Balances to check**
- CLOB collateral balance (USDC) and allowances.
- **ERC-1155 CTF positions on-chain** (Polygon):
  - enumerate all ERC-1155 tokens owned by wallet
  - map `token_id → market/outcome → shares`
  - compute mark value using best bid/ask or mid from CLOB price endpoint

**Open orders / locked**
- list open orders on CLOB
- compute reserved collateral per order (especially bids):
  - `reserved = price * size` (adjust for fee model)

**Pitfalls**
- RPC incompleteness (Polygon RPC returning wrong token holdings) → require Blockscout fallback.
- unknown/renamed token_id → must not drop; show as “unmapped token_id” with qty and $0 until priced.


### C) Hyperliquid
**Spot / withdrawable**
- USDC spot balance (withdrawable)

**Perps / margin account value**
- `accountValue` / `marginSummary`
- open perp positions:
  - position size, entry, mark, unrealized pnl

**Open orders**
- open orders list
- compute margin reserved by open orders (venue-specific; may be included in marginSummary already)

**Pitfalls**
- API endpoint differences between agent wallet (orders-only) and main key (withdrawals) as noted in `memory/2026-02-25.md`.
- counting both “accountValue” and “spot USDC” can double-count if not modeled carefully.


### D) EVM chains (Polygon, Arbitrum, Base, etc.)
**Balances to check**
- native gas token (MATIC/ETH)
- ERC-20 balances for:
  - USDC + USDC.e + bridged variants
  - any DeFi vault tokens (Yearn shares, etc.) if used

**Normalization**
- identity key must be `(chainId, tokenAddress)`; symbol is display-only.
- decimals fetched per token contract.


### E) Solana
- SOL balance
- SPL tokens (USDC, staked SOL derivatives, etc.)
- if liquid staking exists: token mint mapping + price feed.

---

## 3) Proposed correction (design) — make portfolio a real “source of truth”

### 3.1 Output model: explicit components + partial results
Implement (later) a portfolio snapshot schema like:
- `assets[]`: `{ venue, chain?, token_id/address?, symbol, qty, usd_price, usd_value, classification }`
- `components`: `{ free_usd, locked_usd, margin_usd, positions_usd, unrealized_pnl_usd }`
- `totals`: `{ net_liquidation_usd, gross_exposure_usd }`
- `errors[]`: connector failures with `{ connector, severity, message }`

**Key requirement:** never silently undercount. If pricing or enumeration fails, keep the position row but mark `usd_value=null` + error.


### 3.2 Connector contract (per venue)
Each connector should return a **typed** response, not a single “balance” number:
- `getSpotBalances()` → free token balances
- `getOpenOrders()` → orders with reserved collateral
- `getPositions()` → derivatives/CTF positions with mark price inputs
- `getMarginSummary()` → account value / margin

Aggregator computes:
- Free = spot - reserved
- Locked = reserved
- Positions value = sum(mark_to_market)
- Net liquidation = free + locked + positions + unrealized pnl (venue-specific)


### 3.3 Token identity & pricing
- Replace symbol-based aggregation with `(chainId,address)` for EVM and `(chainId,tokenId)` for ERC-1155.
- Maintain a `token_registry` mapping:
  - canonical symbol (e.g., USDC)
  - known variants (USDC.e)
  - decimals
  - price feed strategy

Pricing strategy order:
1) Stablecoins: hard peg with sanity bounds (0.995–1.005) unless depeg detected.
2) Venue-native pricing for CTF: CLOB price endpoint.
3) Public oracle/DEX quotes for others.


### 3.4 Env/config hygiene (prevent “works in shell, fails in systemd”)
- Single source: one env file used by both interactive and systemd (or systemd imports it).
- Add a startup self-check endpoint/command: “print loaded connectors + required env vars present + validation errors”.


---

## 4) Tests / validations to prevent regression (proposal)

### Unit tests
- decimals normalization:
  - USDC 6 decimals on each chain
  - ERC-1155 amount parsing
- token identity:
  - USDC vs USDC.e should not overwrite each other
- reserved collateral math:
  - order book examples → reserved computed correctly

### Integration tests (mocked APIs)
- Polymarket:
  - mock Blockscout ERC-1155 holdings response + CLOB price response → positions appear in snapshot
- Hyperliquid:
  - mock accountState with open positions + open orders → totals include margin + positions

### Runtime smoke checks
- `/saldo --debug` (or equivalent): prints per-connector counts and totals; asserts:
  - totals not NaN
  - stablecoins priced ~1
  - at least N connectors ran successfully

---

## 5) Suggested reproduction / verification commands (for later execution)
Because this planning environment doesn’t include the crypto-sage code checkout, focus on commands that validate reality independently:

1) **Polymarket ERC-1155 holdings (Polygon / Blockscout)**
- `curl "https://polygon.blockscout.com/api/v2/addresses/<WALLET>/tokens?type=ERC-1155"`

2) **Polymarket CLOB price sanity**
- `curl "https://clob.polymarket.com/price?token_id=<TOKEN_ID>&side=sell"` (may require Tor/proxy depending on geo)

3) **Hyperliquid account state**
- Use existing HL client script (if present) or direct API call; verify it returns:
  - open positions
  - margin/account value
  - open orders

4) **EVM token balances per chain**
- `cast call` / `ethers` script: ERC20 `balanceOf`, `decimals` for USDC and USDC.e contracts.

5) **Compare vs `/saldo` output**
- Diff:
  - missing assets
  - missing venues
  - free vs locked mismatch

---

## 6) Files likely to touch (later implementation)
*(Names based on repo history notes; actual paths must be confirmed in the crypto-sage codebase checkout)*
- crypto-sage portfolio aggregator / connectors:
  - `src/connectors/portfolio.mjs` (mentioned in prior tasks)
  - connectors for: Polymarket, Hyperliquid, EVM chains, Solana
- systemd env drop-ins / config:
  - `crypto-sage-env.conf` and/or corresponding systemd drop-in under `/etc/systemd/system/openclaw-gateway.service.d/`
- test suites for connectors / portfolio snapshot

If code is not present in this workspace repo, the audit should start by locating the real crypto-sage source directory and mapping the `/saldo` execution path.

---

## 7) Concrete audit sequence (step-by-step)
1) Capture a failing `/saldo` output + timestamp.
2) Independently compute “ground truth” for each venue (checklist section 2) for that timestamp.
3) Identify the missing bucket(s):
   - ERC-1155 CTF positions
   - HL margin/positions
   - open orders reserved
   - token variant not counted
4) Trace in code/config why the bucket is absent:
   - connector disabled? env missing? parsing bug? pricing missing?
5) Choose the correction design:
   - add missing connector call + explicit component accounting
   - add token registry / identity mapping
   - improve error surfacing and “partial snapshot” behavior
6) Add regression tests covering the exact missing bucket.

---

## Status
plan_submitted
