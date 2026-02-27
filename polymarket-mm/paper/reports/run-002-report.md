# Run-002 Analysis Report

**Generated:** 2026-02-26T18:30 UTC  
**Run ID:** run-002  
**Hypothesis:** H7 — Fill Rate Calibration (com Virtual Wallet)  
**Duration:** 11m 17s (676.8s) — 4.7% do target de 4h  
**Status:** FINISHED (processo morreu prematuramente)

---

## 1. Executive Summary

Run-002 corrigiu o problema crítico de run-001 (vendas sem posição) adicionando virtual wallet com enforcement de saldo. O sistema funcionou corretamente a nível de wallet — não houve vendas fantasmas. Porém, o run terminou em ~11 minutos ao invés das 4h planejadas, e o sistema acumulou posição unidirecional sem hedge, esgotando o capital disponível rapidamente.

**Verdict: INCONCLUSIVE** — os fixes de wallet funcionaram, mas a duração insuficiente e problemas de quoting impedem conclusões sobre fill rate ou lucratividade.

---

## 2. Key Metrics

| Metric | Run-001 | Run-002 | Delta |
|--------|---------|---------|-------|
| Duration | 18m | 11m | -7m |
| Quotes | 1,944 | 60 | -96.9% |
| Orders | 1,944 | 60 | -96.9% |
| Fills | 373 | 2 | -99.5% |
| Fill Rate | 19.19% | 3.33% | -15.86pp |
| Realized PnL | $0.00 | $0.00 | — |
| Unrealized PnL | — | +$25.50 | — |
| Errors | 0 | 0 | — |
| WS Messages | 5,069 | 3,251 | -35.9% |
| Book Updates | 4,346 | 2,706 | -37.7% |

---

## 3. Trade Analysis

### Fill #1
- **Time:** 18:07:07 UTC
- **Side:** BUY YES @ $0.30 × 50 tokens
- **Market mid:** $0.585 (spread 170 bps)
- **Entry price $0.30 vs mid $0.585** — comprou 48.7% abaixo do mid ✅ (bom entry)
- **Wallet after:** $273 available, $198 locked, $485 equity

### Fill #2
- **Time:** 18:07:16 UTC (9 seconds later)
- **Side:** BUY YES @ $0.19 × 50 tokens
- **Market mid:** $0.585 (spread 170 bps)
- **Entry price $0.19 vs mid $0.585** — comprou 67.5% abaixo do mid ✅ (excelente entry)
- **Wallet after:** $75 available, $367 locked, $459 equity

### Trade Analysis Summary
- **Average entry:** $0.245 for 100 YES tokens
- **Unrealized PnL:** +$25.50 (mid at $0.50 at close → 100 × ($0.50 − $0.245) = +$25.50)
- ⚠️ **Ambos trades no mesmo lado** (BUY YES) — sem hedge, inventário 100% long
- ⚠️ **Fills muito espaçados do mid** — preços de $0.30 e $0.19 sugerem o bot está cotando BID muito baixo
- ⚠️ **Avg spread captured: 5,812 bps (58%)** — spread irreal, indica quotes muito distantes do mid

---

## 4. Critical Issues Identified

### 4.1 Premature Termination (CRITICAL)
- Run durou 11min ao invés de 4h (4.7% progress)
- O processo morreu mas o orchestrator cron só detectou 30 minutos depois
- Causa provável: inventário acumulou a 2,050 tokens em múltiplos mercados (incluindo mercados não no config!) gerando `inventory_exceeded` warnings (8,290 ocorrências)
- **Root cause:** o paper runner parece estar processando 5 mercados (axiom, iran, colorado, barcelona, jd-vance) mas o config de run-002 só tem 2

### 4.2 Quoting Distance (HIGH)
- Avg spread captured: 5,812 bps (~58% do mid) — **absurdamente distante**
- Fills aconteceram a $0.30 e $0.19 com mid a $0.585
- O quoting engine está gerando preços muito distantes do mid, o que significa:
  - Fill rate artificialmente baixo (3.33%)
  - Os poucos fills são "takers" desesperados ou erros
  - Market making efetivo: zero

### 4.3 One-Sided Inventory (HIGH)
- Posição final: 100 YES tokens, 0 NO tokens no Iran market
- Nenhum fill no Axiom market
- O bot não está fazendo market making (bid+ask), está apenas comprando
- **Kill switch HALTED** em ambos os mercados no final

### 4.4 Wallet Exhaustion (MEDIUM)
- $500 → $5 available em 9 segundos de fills
- Exposure 98.9% com apenas 2 fills
- Default order size $50 × $0.245 avg = $24.50 per fill × 2 = $49 locked
- Mas wallet shows $437 locked — algo não bate, pode haver orders pendentes consumindo capital

### 4.5 Data Gaps (MEDIUM)
- 1,464 data gap warnings nos logs
- WS reconnect necessário 2x (`no close frame received`)
- Alguns mercados com gaps > 80s (barcelona)

---

## 5. Hypothesis Assessment

### H7: Fill Rate Calibration
**Result: INCONCLUSIVE**

**Razão:** O fill_probability parameter (0.20) de run-002 não pôde ser avaliado adequadamente porque:
1. O run durou apenas 11 minutos (4.7% do target)
2. O quoting engine está gerando preços muito distantes do mid (5,812 bps)
3. Apenas 2 fills em 60 orders — mas fill rate baixo pode ser causado por preços ruins, não pelo parâmetro fill_probability
4. O parâmetro Axiom market gerou 0 quotes (spread 19,960 bps — mercado potencialmente broken)

**Para testar H7 adequadamente, precisamos primeiro resolver o quoting distance issue.**

---

## 6. Root Cause Analysis

O problema fundamental é que o **spread model** está produzindo quotes muito distantes do mid:

1. **`default_half_spread_bps: 50`** deveria gerar quotes a ~0.5% do mid
2. Mas os fills estão a 48-67% do mid — **100x** maior que o configurado
3. Isso sugere que o spread model está multiplicando/acumulando o spread de forma incorreta
4. O feature engine reporta `spread_bps=183` para Iran — mas fills a 5,812 bps

**Possíveis causas no código:**
- Inventory skew fator multiplicando o spread exponencialmente
- gamma_risk_aversion (0.3) amplificando demais
- Spread calculado em escala errada (decimal vs bps confusion)

---

## 7. Comparison: Run-001 vs Run-002

| Aspecto | Run-001 | Run-002 | Melhorou? |
|---------|---------|---------|-----------|
| Wallet enforcement | ❌ Sem controle | ✅ Virtual wallet | ✅ |
| Sells without position | ❌ Bug crítico | ✅ Fixado | ✅ |
| Fill rate realista | 19.19% (falso) | 3.33% | ✅ (mais honesto) |
| PnL accuracy | $0 (falso positivo) | $0 real + $25.50 unrealized | ✅ |
| Duration | 18m / target | 11m / 4h target | ❌ Pior |
| Quote quality | Ruim | Ruim | ➖ |
| Inventory management | Não testável | One-sided | ❌ |

**Conclusão:** Run-002 melhorou a integridade dos dados (wallet, PnL), mas revelou que o core problem é o quoting engine produzindo preços absurdos.

---

## 8. Recommendations for Run-003

Run-003 já existe como YAML (`run-003.yaml`) com H1 hypothesis, mas precisa de **code fixes antes de rodar**, não apenas parameter tuning:

### Code Fixes Obrigatórios (antes de run-003):
1. **Fix spread calculation** — investigar por que o half_spread_bps de 50 resulta em quotes a 5,000+ bps do mid
2. **Two-sided quoting** — garantir que cada ciclo gera BID + ASK (não só um lado)
3. **Position-aware quoting** — quando long, shift ask mais agressivo para desfazer posição
4. **Market filter** — garantir que APENAS os mercados do config recebem quotes
5. **Process longevity** — investigar por que o processo morre em ~11 min

### Parameter Adjustments (run-003.yaml):
- ✅ Order size reduzido de 50 → 25 (já feito)
- ✅ Single market (iran only) — simplifica debugging
- 🔧 Considerar `fill_probability: 0.25` — mais agressivo, mas irrelevante se spread estiver errado

### Gating Criteria para Run-003:
- [ ] Spread calculation fix verificado com unit test
- [ ] Two-sided quotes confirmado em dry-run
- [ ] Processo roda > 30 min sem crash em dry-run
- [ ] Max inventory per market limitado

---

## 9. Decision

**⛔ NÃO iniciar run-003 automaticamente.**

O quoting engine tem bugs fundamentais que tornariam run-003 igualmente inconclusivo. É necessário:
1. Debug + fix do spread calculation no código Python
2. Validação com unit tests
3. Dry-run curto (5-10 min) para confirmar fixes

**Próxima ação:** Escalar para Matheus com diagnóstico para decidir se spawna Luan para os fixes ou se quer investigar manualmente.

---

*Report gerado automaticamente pelo paper trading orchestrator.*
