# PMM Paper Trading — Bugs Encontrados & Fixes para Produção

**Data:** 2026-02-26  
**Run afetado:** run-001  
**Identificado por:** Matheus (validação humana do dashboard)

---

## Bug 1: SELL sem posição — "Dinheiro Grátis"

**Sintoma:** SELL NO gerando PnL de +$49 por trade. Todas operações lucrativas.

**Causa:** O PaperVenue (v1) permitia SELL de tokens que nunca foram comprados.
- `avg_entry_no = 0` (nunca comprou NO)
- PnL = `(fill_price - 0) * qty` = revenue total contado como lucro
- SELL NO @ 0.98 x50 → PnL = $49 (100% da receita como "lucro")

**Dados:** 144 SELLs de NO com 0 BUYs de NO. PnL cumulativo fictício: $3,607.

**Fix aplicado (run-002+):** VirtualWallet no PaperVenue verifica posição antes de SELL.
`InsufficientPositionError` rejeita ordens sem posição suficiente.

**⚠️ ATENÇÃO PRODUÇÃO:** Em produção real no CLOB, SELL orders são aceitas pelo exchange mesmo sem posição (short selling não existe no Polymarket, mas o CLOB pode rejeitar com erro diferente). Validar que `live_execution.py` tem check equivalente ANTES de enviar ao CLOB.

---

## Bug 2: PnL inconsistente entre dashboard e trade log

**Sintoma:** PnL no trade log (trades.jsonl) não bate com PnL cumulativo no gráfico (live_state.json).

**Causa:** Duas fontes de PnL com cálculos diferentes:
- `trades.jsonl` → calculado no `_fill_event_loop` com `(fill_price - avg_entry) * qty`
- `live_state.json` → calculado no `_pnl_update_loop` via `venue.total_pnl`

**Fix necessário:** Unificar PnL tracking — `live_state.json` deve ler de `trade_logger.cumulative_pnl` (single source of truth). O `pnl_update_loop` deve ser informativo, não autoritativo.

**⚠️ ATENÇÃO PRODUÇÃO:** Em produção, PnL MUST vir da reconciliação com o exchange (posições reais + fills confirmados), não de cálculos internos. O `reconciler.py` é o source of truth.

---

## Bug 3: Quote engine gera ASK NO sem posição

**Sintoma:** Quote engine produz slices bilaterais (BID YES + ASK YES + BID NO + ASK NO) independente da posição.

**Causa:** O `quote_engine.py` gera quotes para AMBOS os lados sem considerar se o agente tem posição pra vender. Em produção, ASK NO sem posição = ordem que vai ser rejeitada.

**Fix necessário:** O quote engine deve filtrar slices baseado na posição disponível:
- ASK YES → só se `position.qty_yes > 0`
- ASK NO → só se `position.qty_no > 0`
- OU: tratar ASK como "mint + sell" (comprar YES é equivalente a vender NO via complete set)

**⚠️ ATENÇÃO PRODUÇÃO:** No Polymarket, o modelo bilateral funciona diferente:
- BUY YES = SELL NO (são equivalentes via complete set)
- O CLOB aceita BUY/SELL diretamente por token_id
- Mas é preciso ter USDC pra BUY ou tokens pra SELL
- Complete set (merge/split) pode ser usado pra converter posições

---

## Bug 4: Fill rate irrealista (run-001: 96%, mesmo com fill_probability=0.20)

**Possível causa:** O `fill_probability` pode não estar sendo aplicado corretamente no código v1 (foi adicionado pelo Luan mas run-001 usou código pré-patch).

**Verificar no run-002:** Confirmar que fill rate caiu pra ~20%.

---

## Checklist Pré-Produção (P6)

Baseado nos bugs acima, antes de ir pra produção real:

- [ ] **Position check em SELL orders** — implementado no PaperVenue, falta no LiveExecution
- [ ] **PnL single source of truth** — via reconciler, não cálculo interno
- [ ] **Quote engine position-aware** — não gerar ASK sem posição
- [ ] **Fill rate validation** — confirmar que paper trading com fill_prob=0.20 é realista
- [x] **Fee model implementado** — `FeeConfig` com maker_fee_bps=-20 (rebate). Aplicado ao PnL de cada fill. Run-007 valida lucratividade com fees reais
- [ ] **Order rejection handling** — como o bot reage quando CLOB rejeita ordem?
- [ ] **Complete set strategy** — definir quando usar merge/split vs direct buy/sell
- [ ] **Wallet balance sync** — reconciliar saldo on-chain vs saldo interno
- [ ] **Gas management** — POL pra gas em cada tx na Polygon
- [x] **Adversarial venue testing** — PaperVenue com adverse selection (10bps), distance decay, e fee model. Run-007 testa lucratividade sob condições adversas realistas
