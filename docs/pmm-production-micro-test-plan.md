# PMM Production Micro Test + Dual Dashboard — Plano Consolidado

**Data:** 2026-02-27
**Aprovado por:** Matheus (go-ahead verbal)
**Capital em risco:** $25 USDC.e + ~$2 gas (POL)

---

## 1. Research: Adversidades Reais de MM (não simuladas)

### 1.1 Adversidades que JÁ simulamos
- ✅ Adverse selection (mid move contra fills) — 10bps
- ✅ Maker fee/rebate — -20bps (ERRADO: Polymarket cobra 30bps base fee)
- ✅ Fill probability decay com distância do mid
- ✅ Partial fills
- ✅ Kill switch (drawdown)

### 1.2 Adversidades que NÃO simulamos (descobertas na research)

**A) Latência de cancel (stale quote risk)**
- Na produção real, há delay entre detectar mudança de preço e cancelar ordens
- Se o mid move 2% em 200ms e nosso cancel demora 500ms, a ordem "velha" é preenchida a preço ruim
- Impacto: fills adversariais em momentos de alta volatilidade
- **Métrica a extrair:** tempo médio de round-trip (submit→confirmed, cancel→confirmed)

**B) Queue priority / time priority**
- Em CLOBs reais, ordens no mesmo preço são preenchidas por FIFO (first-in-first-out)
- MMs que postam primeiro no best bid/ask são preenchidos primeiro
- Nosso bot pode postar e NUNCA ser preenchido porque há MMs mais rápidos na fila
- **Métrica a extrair:** fill rate real por nível de preço (best bid vs 2nd bid vs 3rd bid)

**C) Informed flow / toxicity (Avellaneda-Stoikov)**
- Alguns takers são "informed" — sabem algo sobre o resultado que o MM não sabe
- Em prediction markets: insider info, breaking news, poll leaks
- Fills de informed flow são SEMPRE perdedores para o MM
- **Métrica a extrair:** PnL de fills que aconteceram vs fills que não aconteceram (seleção adversa real)

**D) Event risk (jump risk)**
- Prediction markets têm eventos discretos: "Iran confirmado" → preço pula de 0.55 pra 0.95 instantaneamente
- Qualquer posição aberta nesse momento = loss catastrófico
- Não é volatility normal — é descontinuidade
- **Métrica a extrair:** max price jump por intervalo de tempo

**E) Fee real do Polymarket: 30bps (não 20bps)**
- API `/fee-rate` retorna `base_fee: 30` (30 basis points = 0.3%)
- Nosso modelo usa -20bps — precisa ser corrigido para 30bps
- Maker pode ter rebate parcial, mas base é 30bps
- **Métrica a extrair:** fee efetiva por trade (pode variar por mercado)

**F) Gas costs por transação**
- Cada order placement/cancel na Polygon custa gas
- Com centenas de ordens/dia, gas pode ser significativo vs $25
- **Métrica a extrair:** gas cost médio por operação

**G) Order rejection rate**
- CLOB pode rejeitar por: insufficient balance, invalid price, rate limit, geoblock
- Rate limit do Polymarket: undocumented, mas bots agressivos são throttled
- **Métrica a extrair:** rejection rate + motivos

**H) Spread competition**
- Outros MMs competem pelo mesmo spread
- Em mercados líquidos, spread real pode ser 1 tick (1%)
- Nosso spread mínimo de 50bps pode não ser competitivo
- **Métrica a extrair:** spread real do order book em tempo real vs nosso spread

---

## 2. Arquitetura: Production Runner + Dual Dashboard

### 2.1 Production Runner (`paper/production_runner.py`)
- Usa `execution/clob_client.py` real (não PaperVenue)
- Order size: 5 shares (mínimo)
- Kill switch: $5 loss (20% de $25)
- Modo: maker-only (GTC limit orders)
- Mesmo quote engine, mesmos parâmetros de run-007
- Logging: JSONL idêntico ao paper, com campos extras:
  - `gas_cost_usd` por tx
  - `latency_ms` (submit→fill)
  - `rejection_reason`
  - `queue_position` (se disponível)
  - `real_fee_bps`

### 2.2 Parameter Extraction Script (`paper/extract_real_params.py`)
Roda a cada hora, lê trades production JSONL e calcula:
```python
{
    "real_fill_rate": 0.08,           # % de ordens que filam
    "real_adverse_selection_bps": 45,  # mid move médio pós-fill
    "real_fee_bps": 30,               # fee efetiva média
    "real_latency_ms": 250,           # round-trip médio
    "real_rejection_rate": 0.05,      # % de ordens rejeitadas
    "real_spread_bps": 120,           # spread real do book
    "real_gas_cost_per_tx": 0.005,    # USD por transação
    "real_max_jump_pct": 3.5,         # maior price jump observado
    "suggested_paper_config": {       # config calibrada para PaperVenue
        "fill_probability": 0.08,
        "adverse_selection_bps": 45,
        "maker_fee_bps": 30,
        # ...
    }
}
```

### 2.3 Dual Dashboard
Dashboard mostra DEMO e PROD lado a lado:
- Painel esquerdo: Paper trading (run-007+)
- Painel direito: Production ($25 micro test)
- Comparação: fill rate, PnL/hora, adverse selection, latência
- Alerta visual quando métricas divergem >2x

### 2.4 Monitoring Script (`paper/prod_monitor.sh`)
Cron a cada 5min:
- Checa se production runner está vivo
- Verifica drawdown (<$5 loss)
- Verifica balance on-chain vs internal
- Alerta Discord se qualquer anomalia

---

## 3. Setup de Wallet (Crypto-sage)

Precisa resolver antes de iniciar:
1. Enviar ~2 POL para `0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1` na Polygon
2. Swap $25 USDC nativo → USDC.e via QuickSwap/Uniswap
3. Set allowances: CTF Exchange + Neg Risk CTF Exchange

---

## 4. Mercado Target

Critérios:
- Volume > $100k/dia
- Spread < 5%
- Resolução > 30 dias (pra dar tempo de coletar dados)
- Não ser evento único (tipo eleição amanhã)
- Preço perto de 0.50 (maximiza entropy, minimiza event risk)

O paper_runner já seleciona automaticamente via REST API.

---

## 5. Cronograma

1. **Agora:** Crypto-sage envia POL
2. **Luan (+30min):** Implementa production runner + dual dashboard + parameter extraction
3. **Quando POL chegar:** Swap USDC→USDC.e + set allowances
4. **Então:** Start production micro test (24h)
5. **+24h:** Extrair parâmetros reais, calibrar PaperVenue
6. **+48h:** Rodar bateria de testes com parâmetros calibrados → decisão P6
