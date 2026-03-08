# PMM — Recoverable inventory still trapped (diagnosis + remediation)

**Task:** MC 4df3a337 ("PMM: recoverable inventory still trapped")

**Scope / safety:** diagnosis + remediation plan only. No live trades or on-chain actions executed.

---

## 1) Current inventory / positions snapshot (evidence)

### Primary “what is currently trapped” snapshot

Evidence file:
- `polymarket-mm/paper/data/flatten_report.json`

Key fields (from `flatten_report.json`):
- `executed: false` (this is a report from a *dry-run* / non-executed flatten attempt)
- `wallet_state_before.free_collateral_usdc: 57.256196`
- `wallet_state_before.recoverable_inventory_usdc: 0.0650`
- `wallet_state_before.dust_inventory_usdc: 0.3776`

**Recoverable positions (should be flattenable without opening new exposure):**
- `sell_token`: condition `0xed0a...5290` (“Will Iran name a successor to Khamenei by March 7?”), outcome **YES**
  - `shares: 5.000000`
  - `sell_price: 0.013`
  - `estimated_value_usdc: 0.0650`

**Residual dust (below minimum order / not economically recoverable via normal CLOB orders):**
- `Andy Barr YES`: `shares: 0.784312` (value ~0.36 USDC)
- `Hormuz NO`: `shares: 0.059729` (value ~0.001–0.002 USDC depending on price)
- `Regime YES`: `shares: 0.035899` (~0.011 USDC)
- plus some mergeable dust pairs in very small size (`merge_pair` entries)

Interpretation:
- The only *meaningful* recoverable inventory currently shown is ~**$0.065**.
- The remaining ~$0.38 is **dust** by the system thresholds (`dust_threshold_shares=5`, `dust_threshold_usdc=1`) and/or by Polymarket minimum order constraints.

### Quant control-plane confirmation

Evidence file:
- `polymarket-mm/paper/data/quant_diagnosis_latest.json`

Key fields:
- `analysis.wallet_state.recoverable_inventory_usdc: 0.1550`
- `analysis.wallet_state.recoverable_positions`: 1 position
  - same market family: condition `0xed0a...5290` with `shares: 5.000000`

Note:
- `quant_diagnosis_latest.json` and `flatten_report.json` differ slightly in valuation (0.155 vs 0.065) because they were captured at different timestamps and/or using different mark inputs. They agree on *structure*: one recoverable `sell_token` position of **5 shares**.

### Envelope / decision gating

Evidence file:
- `polymarket-mm/paper/data/decision_envelope_candidate.json`

Key fields:
- `decision_reason: recoverable_inventory_must_be_flattened_before_live`
- `trading_state: standby`
- `blockers` includes: `recoverable_inventory_present`

Interpretation:
- System is intentionally in **standby** until recoverable inventory is flattened.

---

## 2) What “trapped” likely means here

Based on the current evidence, “trapped inventory” is **not** a large stuck balance anymore.

The project already executed a large recovery/flatten previously (see §5 History). What remains is:
- **One small recoverable sell** (5 shares) worth < $1.
- Several **dust** positions below min-order-size and/or below configured dust thresholds.

So the question becomes: **Can we recover the 5-share position automatically, and can we do anything about dust?**

- The 5-share position *should* be recoverable using the official flatten script with `--execute`.
- The dust positions are *functionally non-recoverable* without either:
  1) placing *new trades* to increase size above min order size (creates exposure and is not a “recovery-only” action), or
  2) on-chain merges/redemptions that are gas-inefficient for the value involved.

---

## 3) Hypotheses (ranked) for why recoverable inventory remains

### H1 — No auto-flatten path is triggered while the system is in standby
- Evidence: envelope is `trading_state=standby` with `decision_reason=recoverable_inventory_must_be_flattened_before_live`.
- The live runner entrypoint refuses non-active envelopes (`DecisionEnvelope.require_live_ready()` requires `trading_state == "active"`).
- Therefore, no live pipeline is running to trigger any “inventory-guard breach → flatten” automation.

### H2 — Flatten report was produced in dry-run mode (never executed)
- Evidence: `polymarket-mm/paper/data/flatten_report.json` has `executed: false` and `executed_actions: []`.
- This suggests the script was run without `--execute` (manual) or a previous attempt failed before action submission.

### H3 — Execution would fail due to missing/invalid live credentials (CLOB auth)
If someone attempts `--execute`:
- missing `POLYMARKET_API_KEY / SECRET / PASSPHRASE` or wallet key env vars would block execution.
- invalid API key permissions could also block posting the sell.

### H4 — Minimum order constraints / market closed edge-cases
- The recoverable position is exactly `shares=5.0`, which is also the typical `min_order_size=5` in many configs.
- If the market’s real min order size is > 5, or the market is closed/paused, the sell might fail.

### H5 — Proxy-vs-direct transport mismatch for private posting
- Some prior incidents note that certain actions required proxy-only private CLOB flow.
- Candidate envelope blockers include `private_post_gate`/`public_latency_gate` (candidate). If private posting is only reliable via proxy, flatten execution might need proxy.

### H6 — Merge path issues for neg-risk markets (less relevant now)
- Historically, large trapped inventory required merges; at least one merge reverted for a neg-risk market (see §5).
- Current recoverable position is a **sell_token**, not a merge, so merge-specific revert is not the primary issue now.

---

## 4) Exact verification steps (commands)

All commands below are **read-only** unless explicitly marked as EXECUTE.

### Step A — Confirm what the system thinks is recoverable (dry-run)
```bash
cd /home/openclaw/.openclaw/workspace/polymarket-mm
python3 scripts/flatten_positions.py --report paper/data/flatten_report.json --json
```
Expected:
- `executed=false`
- `wallet_state_after.recoverable_positions` contains exactly 1 sellable position (~5 shares)

### Step B — Confirm decision engine is blocking live due to recoverable inventory
```bash
cd /home/openclaw/.openclaw/workspace
cat polymarket-mm/paper/data/decision_envelope_candidate.json | head -n 40
```
Look for:
- `decision_reason: recoverable_inventory_must_be_flattened_before_live`
- `blockers` includes `recoverable_inventory_present`

### Step C — Check if any live runner is active (should not be)
```bash
cd /home/openclaw/.openclaw/workspace
cat polymarket-mm/paper/data/pmm_runtime_state.json
```
Look for:
- `trading_state: standby`
- `process_alive: false`

### Step D — If an EXECUTE attempt is planned, confirm env vars exist (read-only)
```bash
# do NOT print secrets; just check they exist
for k in POLYMARKET_API_KEY POLYMARKET_API_SECRET POLYMARKET_PASSPHRASE POLYGON_PRIVATE_KEY; do
  if [ -n "${!k}" ]; then echo "$k=SET"; else echo "$k=MISSING"; fi
done
```

---

## 5) Historical context: large trapped inventory was already flattened

Evidence file:
- `polymarket-mm/paper/data/manual_flatten_final_state.json`

Key fields:
- `free_usdc: 232.030396`
- `executed_actions`: includes merges + sells with tx hashes
- Notes:
  - “India pair merge reverted on-chain even with negRisk routing; position had to be flattened via CLOB trades through proxy.”
  - “Literal zero was not possible because residual dust is below CLOB minimum order size …”

Interpretation:
- The **major** trapped inventory incident was already remediated.
- Remaining dust is an expected residual.

---

## 6) Remediation plan (safe) + rollback

### Remediation 1 — Flatten the remaining recoverable position (requires human approval)
**Goal:** sell the remaining 5-share recoverable position via CLOB.

**EXECUTE command (requires human approval):**
```bash
cd /home/openclaw/.openclaw/workspace/polymarket-mm
python3 scripts/flatten_positions.py --execute --report paper/data/flatten_report.json --json
```

Expected output changes:
- `executed: true`
- `executed_actions` contains one `sell` action
- `wallet_state_after.recoverable_positions` becomes `[]`

Rollback:
- There is no strict “rollback” for a sell; it is a final state change.
- If the sell posts at an unexpectedly bad price, the only rollback is buying back (creates new exposure; not recommended as “recovery-only”).

Safety guardrails before executing:
- Confirm correct wallet address.
- Confirm market is active and min order size allows `shares=5`.
- Prefer using the transport (proxy/direct) that has historically succeeded for private posting.

### Remediation 2 — Accept dust as unrecoverable (recommended)
**Goal:** stop chasing ~$0.38 of dust.

Rationale:
- Dust positions are below min-order-size and/or not worth gas-heavy merges.

Rollback:
- N/A (no action taken).

### Remediation 3 — (Optional) force-zero dust (NOT recommended; requires explicit approval)
Options (all require new risk/exposure or disproportionate gas):
- place additional trades to increase size above min order, then flatten (introduces exposure).
- attempt merges/redemptions on-chain for tiny notional (high gas).

Rollback:
- would require additional trades/merges; not suitable for a “recovery-only” incident.

---

## 7) Suggested safe guardrail patch (off by default)

No clear code bug was found in the flatten script itself; the current behavior is consistent with:
- envelope blocking live runs until inventory is flattened
- flatten requiring an explicit `--execute` step

If you want automation, a **safe/off-by-default** enhancement would be:
- add a *separate* “maintenance” command (or cron entry) that runs `scripts/flatten_positions.py` in dry-run always, and only runs `--execute` when an explicit env var is set (e.g., `PMM_AUTO_FLATTEN=1`) **and** recoverable inventory exceeds a configurable threshold.

I did **not** implement this automation here because it would create a new live-action pathway and should be reviewed carefully.

---

## 8) Conclusion

- The large trapped inventory appears already resolved (see `manual_flatten_final_state.json`).
- Remaining trapped value is small: one recoverable sell (~$0.06–$0.15 depending on mark) plus dust (~$0.38).
- The recoverable sell can likely be cleared by running the official flatten script with `--execute` (human approval required).
- Dust is expected and realistically not worth forcing to zero.
