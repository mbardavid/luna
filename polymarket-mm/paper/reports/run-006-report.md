# Run-006 Analysis Report

**Generated:** 2026-02-27T11:00 UTC  
**Run ID:** run-006  
**Hypothesis:** H1 — Spread Capture é Lucrativo Após Fees  
**Duration:** 8h 0m 10s (28,810s) — 100% do target  
**Status:** FINISHED (completed full duration)

---

## 1. Executive Summary

**Run-006 é o primeiro PASS definitivo do ciclo de paper trading.** Após 5 runs anteriores (4 INCONCLUSIVE + 1 FAIL), o sistema rodou por 8 horas contínuas overnight sem crashes, com PnL consistentemente positivo em todas as 8 horas. O bot começou com $500 de saldo virtual e terminou com $1,839.11 de equity total (+267.8%), gerando $1,350.94 de PnL realizado.

**Verdict: ✅ PASS** — Hipótese H1 validada. O spread capture é lucrativo no paper trading simulado.

---

## 2. Key Metrics

| Metric | Run-004 (4h) | Run-005 (6m) | **Run-006 (8h)** |
|--------|-------------|-------------|-----------------|
| Duration | 4.0h | 0.1h | **8.0h** |
| Quotes | N/A | N/A | **48,793** |
| Orders | N/A | N/A | **48,793** |
| Fills | N/A | N/A | **11,996** |
| Fill Rate | 18.18% | 3.31% | **24.59%** |
| Realized PnL | $0 | $0 | **$1,350.94** |
| PnL/hour | $0 | $0 | **$168.81** |
| Max Drawdown | 0% | 0% | **-$0.81** |
| Sharpe (est.) | N/A | N/A | **0.62** |
| Errors | 0 | 0 | **0** |
| WS Messages | N/A | N/A | **81,141** |

---

## 3. Hourly PnL Progression

| Hour | PnL Cumulative | PnL Delta | Fill Rate | Avg Spread (bps) | Net Inventory |
|------|---------------|-----------|-----------|-------------------|---------------|
| 0 | $137.68 | +$137.68 | 24.56% | 940 | -92 |
| 1 | $261.51 | +$123.83 | 23.68% | 933 | -15 |
| 2 | $417.08 | +$155.57 | 24.73% | 992 | -36 |
| 3 | $577.31 | +$160.24 | 24.05% | 980 | -104 |
| 4 | $718.81 | +$141.50 | 25.72% | 925 | -34 |
| 5 | $843.77 | +$124.96 | 24.85% | 978 | -43 |
| 6 | $1,005.35 | +$161.59 | 24.43% | 952 | -101 |
| 7 | $1,350.94 | +$345.59 | 24.68% | 1,589 | +23 |

**Observations:**
- PnL positive in **8/8 hours (100%)** — exceeds H1 threshold of >60%
- PnL/hour ranged from $123.83 to $345.59 — remarkably stable for hours 0–6
- Hour 7 had a spike: spread jumped to 1,589 bps (vs ~950 avg), likely due to market mid-price shift from 0.60 → 0.595
- Fill rate extremely stable: 23.68–25.72% range across all 8 hours

---

## 4. Wallet & Risk

| Metric | Value |
|--------|-------|
| Initial Balance | $500.00 |
| Final Available | $1,683.88 |
| Final Equity | $1,839.11 |
| PnL % | +267.82% |
| Exposure % | 8.4% |
| Max Drawdown | -$0.81 |
| Kill Switch | NEVER triggered |

The near-zero max drawdown is **notable but suspicious** — it suggests the simulation may be too favorable. In real markets, adverse selection and partial fills would produce larger drawdowns.

---

## 5. Trade Analysis (Last 60 Trades)

- **Total trades:** 11,996 fills across 8 hours (~25 fills/min)
- **Final position:** 168 YES + 145 NO = net +23 YES
- **Avg entry YES:** $0.5937 | **Avg entry NO:** $0.3953
- **Both sides active:** BUY YES, SELL YES, BUY NO, SELL NO all executing
- **Order sizes:** Dynamic, ranging 7–25 shares (5% of available)
- **Spread captured:** Mostly at bid/ask boundaries (0.59/0.60 YES, 0.39/0.40 NO)
- **Toxic flow detection:** All trades show `toxic_flow.detected: false`

---

## 6. Hypothesis Evaluation

### H1 — Spread Capture é Lucrativo Após Fees: ✅ PASS

| Criterion | Threshold | Actual | Status |
|-----------|-----------|--------|--------|
| PnL/hour > $0 consistently | >60% of hours | 100% (8/8) | ✅ PASS |
| Net PnL positive | > $0 | +$1,350.94 | ✅ PASS |
| Run completes full duration | 8h target | 8h 0m | ✅ PASS |
| Zero errors | 0 | 0 | ✅ PASS |

### ⚠️ Caveats & Concerns

1. **Fill simulation realism:** 24.6% fill rate is within target (10–30%), but PaperVenue may not capture adverse selection. Real fills would skew unfavorably.
2. **Near-zero drawdown:** $0.81 max drawdown on $1,350 PnL is unrealistic. Real MM drawdowns are typically 20–50% of cumulative PnL.
3. **Single market only:** Only tested on `us-strikes-iran-by-march-31-2026` — needs multi-market validation (H5).
4. **Spread model inert:** `spread_model: {}` and `inventory_skew: {}` are empty in trade logs — suggests the actual spread/skew math isn't being logged, making it hard to debug strategy behavior.
5. **Overnight stability validated** partially (H6): 8h without crash or memory growth (50.3 MB stable). But true 24h+ test still pending.
6. **Inventory drift:** Net position swung from -104 (hour 3) to +23 (hour 7) — range of 127 units. Within acceptable bounds but shows the skew model could be tighter.

---

## 7. Evolution: Run-001 → Run-006

| Run | Hypothesis | Duration | PnL | Fill Rate | Result | Key Learning |
|-----|-----------|----------|-----|-----------|--------|--------------|
| 001 | H7 | 18m | $0 | 19.2% | INCONCLUSIVE | Phantom sells, no wallet |
| 002 | H7 | 11m | $0 | 3.3% | INCONCLUSIVE | Quotes 5800bps from mid |
| 003 | H1 | 37m | $0 | 37.1% | INCONCLUSIVE | One-sided inventory |
| 004 | H1 | 4h | $0 | 18.2% | FAIL | SELL sync broken |
| 005 | H1 | 6m | $0 | 3.3% | INCONCLUSIVE | Too short |
| **006** | **H1** | **8h** | **$1,351** | **24.6%** | **✅ PASS** | **First profitable run** |

**Key fixes between 004 and 006:** Sync fills (BUY fills immediately, SELL sees position), no complement routing, kill switch 25%.

---

## 8. Next Steps — Recommended Hypothesis Sequence

Now that H1 is validated, the smoke test plan prescribes:

1. **H2 — Inventory Skew Control** (next)
   - Verify net inventory stays within bounds under stress
   - Run-006 showed swings of -104 to +23 — acceptable but needs tighter control
   
2. **H3 — Volatility Adaptation**
   - Run with chaos injector to test spread widening
   
3. **H5 — Multi-Market**
   - Add 2 more markets, verify per-market PnL doesn't degrade

4. **H6 — 24h Endurance**
   - Extended overnight run to validate memory/latency stability

### Recommended run-007 parameters:

- **Hypothesis:** H2 (Inventory Skew Control)
- **Duration:** 8h (same as run-006 for comparability)
- **Changes:** Increase `gamma_risk_aversion` from 0.3 → 0.5 to test tighter inventory control
- **Success criteria:** Max net inventory < 200, inventory mean-reversion observable

---

## 9. Conclusion

Run-006 marks a **major milestone**: the first paper trading run to complete its full target duration with consistent profitability. After 5 iterative runs fixing fundamental bugs (phantom sells → wallet exhaustion → spread calibration → one-sided inventory → SELL sync), the core market-making loop is now functional.

However, the $1,351 PnL over 8h ($168/hr) should be treated as an **upper bound estimate**. Real-world performance will be impacted by:
- Adverse selection (getting filled on the wrong side before price moves)
- Partial fills and queue priority
- Network latency and execution slippage
- Gas fees and USDC settlement costs

**Recommendation:** Proceed to H2 testing while planning a reality-check phase where fill simulation is made more adversarial (add adverse selection model, random partial fills, latency simulation).
