# Incident Note — PMM: no rewards-eligible markets for 2h (MC 4055c521)

Date (UTC): 2026-03-08

## Summary
The PMM production run (`prod-006`) has remained in `standby` because the Quant decision engine has not produced **any rewards market that is both (a) live rewards eligible and (b) has positive net reward EV**. The latest envelope explicitly reports `decision_reason=no_live_rewards_markets_with_positive_net_reward_ev`, and the production live-state reducer marks the system as stale for the same reason.

This looks primarily like a **market-selection / rewards-filtering strictness issue** (net EV threshold + canary capital / inventory constraints), not a CLOB connectivity failure: the rewards API fetch is marked `available: true` with 100 candidates.

## Observed signals (evidence)

### Runtime & live-state
- Runtime is not running; supervisor keeps system stopped due to standby:
  - `polymarket-mm/paper/data/pmm_runtime_state.json`
    - `status: "standby"`
    - `trading_state: "standby"`
    - `process_alive: false`
    - `last_supervisor_action: "enforce_non_active_trading_state"`
    - `last_supervisor_result.reason: "standby"`
- Production live state is marked stale with explicit reason:
  - `polymarket-mm/paper/data/live_state_production.json`
    - `stale: true`
    - `reason: "no_live_rewards_markets_with_positive_net_reward_ev"`

### Quant cycle state
- Quant control plane reports extended window without any rewards candidate:
  - `polymarket-mm/paper/data/quant_cycle_state.json`
    - `no_rewards_since: "2026-03-07T17:30:47.640593+00:00"`
    - `last_candidate_trading_state: "standby"`
    - `last_candidate_scope: "rewards_only"`

### Decision envelope (latest)
- Latest envelope is in `standby` and documents the blocker condition:
  - `polymarket-mm/paper/data/decision_envelope_latest.json`
    - `trading_state: "standby"`
    - `decision_reason: "no_live_rewards_markets_with_positive_net_reward_ev"`
    - `decision_scope: "rewards_only"`
- Rewards API itself appears reachable:
  - `decision_envelope_latest.json` → `metadata.rewards_api`
    - `available: true`
    - `candidate_count: 100`
- All sampled reward-model entries have **negative** `net_reward_ev_bps_day` and/or are disabled by canary constraints:
  - Example (selected market):
    - `market_id: will-george-russell-be-the-2026-f1-drivers-champion`
    - `reward_bps_day: 16.34` but `net_reward_ev_bps_day: -0.46`
    - `disable_reason: inventory_cap_below_reward_min_size`
  - Many others:
    - `disable_reason: market_requires_too_much_capital_for_canary`

### Incident routing evidence
- Incident definition and timeline:
  - `polymarket-mm/paper/data/pmm_alert_router_state.json`
    - `open_incidents.no_rewards_eligible_2h.opened_at: "2026-03-07T19:31:08.933653+00:00"`

## Likely causes (ranked)

### 1) Rewards filters are too strict for current market conditions (most likely)
- The system currently requires **positive** net reward EV (`net_reward_ev_bps_day > 0`) to enable any rewards market.
- The envelope’s sample list shows net EV is slightly-to-materially negative across candidates (e.g., -0.46 bps/day on the “best” one shown).
- This can happen if:
  - competitiveness/competition multiplier is too punitive,
  - fees/slippage assumptions increased,
  - reward-rate inputs dropped,
  - or the model is frequently falling back to `heuristic_fallback` and being conservative.

### 2) Canary capital / inventory guardrails make otherwise-live rewards markets ineligible
- Envelope indicates small canary capital:
  - `capital_policy.total_capital_usdc: "50"`
  - `inventory_guards.max_net_inventory_notional_usdc: "10"`
- Many candidates are disabled because they “require too much capital for canary”.
- Even the top candidate is disabled due to `inventory_cap_below_reward_min_size`, which strongly suggests a mismatch between:
  - minimum size required to qualify for rewards, vs
  - allowed per-side inventory/notional caps and/or derived per-side quote notionals.

### 3) Connectivity/transport issues are *not* the primary cause, but can exacerbate selection
- `quant_cycle_state.json` lists `direct_latency_gate_failed` and `proxy_latency_gate_failed` among recent “material change reasons”.
- However, the latest envelope reports `metadata.transport_live_gates.rewards_live_ok: true`, and rewards API availability is true.
- Still worth verifying WS connectivity + CLOB health if “no rewards” coincided with data gaps.

## Operator checks (safe, read-only)

> Goal: confirm this is selection/filtering vs data/connectivity.

### A) Confirm the exact blocker reason and whether any candidates are close to eligibility
```bash
cd /home/openclaw/.openclaw/workspace
python - <<'PY'
import json
p='polymarket-mm/paper/data/decision_envelope_latest.json'
with open(p) as f:
  env=json.load(f)
print('decision_id=', env.get('decision_id'))
print('trading_state=', env.get('trading_state'))
print('decision_reason=', env.get('decision_reason'))
print('rewards_api.available=', env.get('metadata',{}).get('rewards_api',{}).get('available'))
summary=env.get('metadata',{}).get('reward_model_summary',[])
print('reward_model_summary_count=', len(summary))
if summary:
  best=sorted(summary, key=lambda x: x.get('net_reward_ev_bps_day', -1e9), reverse=True)[:10]
  for row in best:
    print(row.get('market_id'), 'net=', row.get('net_reward_ev_bps_day'), 'disable=', row.get('disable_reason'))
PY
```

### B) Confirm live-state & runtime are consistent
```bash
cat polymarket-mm/paper/data/live_state_production.json
cat polymarket-mm/paper/data/pmm_runtime_state.json
```

### C) Check production log for rewards fetch/model errors (if any)
```bash
tail -n 300 polymarket-mm/logs/production.log
# Optional: grep for rewards/model keywords
grep -n "rewards" polymarket-mm/logs/production.log | tail -n 50
grep -n "net_reward" polymarket-mm/logs/production.log | tail -n 50
```

### D) If you suspect connectivity (only if logs show errors)
```bash
# Look for websocket disconnects or HTTP failures
grep -n "ws_" polymarket-mm/logs/production.log | tail -n 50
grep -n "HTTP" polymarket-mm/logs/production.log | tail -n 50
```

## Remediation options (safe action plan)

### Option 1 — Tune canary constraints so at least 1 market can clear rewards minimums
Goal: allow quoting at the **rewards minimum size** for at least one market, without materially increasing operational risk.

Candidate changes (choose one):
1) Increase `max_net_inventory_notional_usdc` from 10 → 25 (or 30)
2) Increase `total_capital_usdc` from 50 → 100 (still “canary”, but matches many rewards min sizes)

Expected impact:
- Should reduce `inventory_cap_below_reward_min_size`
- Should reduce `market_requires_too_much_capital_for_canary`

Rollback:
- Revert the config change and re-promote the previous envelope/run settings.

### Option 2 — Relax the net reward EV gate slightly (temporary) to avoid indefinite standby
Goal: avoid “no market” deadlock when best candidate is only marginally negative net EV (e.g., -0.46 bps/day).

Candidate change:
- Allow `net_reward_ev_bps_day >= -1.0` (or similar small buffer) for canary mode only.

Risk:
- Could enable a market that is slightly negative EV under current model assumptions.

Rollback:
- Restore strict `> 0` gate.

### Option 3 — Explicitly pause with documentation if this is intended behavior
If the current decision is correct (no positive-EV rewards markets exist), then the incident should be resolved by:
- marking the run intentionally paused/standby in the operator notes, and
- adjusting the alert criteria (e.g., only alert if standby persists AND rewards candidates are zero due to API failure).

## Proposed config diffs (DO NOT APPLY — for review)

These are *proposals* based on the evidence above; the exact source-of-truth for these numbers may live in the Quant strategist repo, but the effect is to loosen the canary capital constraints.

### Proposal A — Increase prod-006 canary capital (lowest operational complexity)
File: `polymarket-mm/paper/runs/prod-006.yaml`
```diff
-initial_balance: 50.0
+initial_balance: 100.0
```
Notes:
- The decision envelope currently requests `total_capital_usdc: "50"` and will likely scale up if wallet allows.
- This should help clear `market_requires_too_much_capital_for_canary` disablements.

### Proposal B — Increase per-side inventory cap in runner config to match reward minimums
File: `polymarket-mm/paper/runs/prod-006.yaml`
```diff
 params:
-  max_position_per_side: 10
+  max_position_per_side: 25
```
Notes:
- This may or may not be the binding constraint (envelope currently lists `max_inventory_per_side: "19.42"`).
- If quant disables markets *before* handing off to runner, the real fix is in quant’s inventory guardrails.

## Next steps
1) Run the verification snippets above to confirm the top-10 candidates and their disable reasons.
2) If the top candidate is only marginally negative EV and the system is intended to keep trading, pick one:
   - raise canary capital OR
   - relax net-EV gate slightly for canary.
3) After change, confirm:
   - `decision_envelope_latest.json` contains ≥1 enabled rewards market (no `disable_reason`) and
   - `live_state_production.json.reason` clears.

## References
- `polymarket-mm/paper/data/pmm_runtime_state.json`
- `polymarket-mm/paper/data/live_state_production.json`
- `polymarket-mm/paper/data/quant_cycle_state.json`
- `polymarket-mm/paper/data/decision_envelope_latest.json`
- `polymarket-mm/paper/data/rewards_research_report_state.json`
- `polymarket-mm/paper/data/pmm_alert_router_state.json`
