# Polymarket Market Maker — Research Notes

## Fonte 1: ChatGPT 5.2 Pro (Análise completa)

### Contexto
Matheus submeteu arquitetura de 10 etapas (60 arquivos, 51 módulos Python) + "Prompt Mestre" modelando Gabagool22/Charles-de-g no Polymarket CLOB.

### Críticas Principais do GPT 5.2
1. **Signal Engine vs Quote Engine** — O `TradeSignal.action` (BUY_YES etc.) é direcional, não maker. Precisa de `QuotePlan` (bid/ask simultâneos em YES/NO com slices)
2. **Float → Decimal/Ticks** — Preço como float causa rejeição. Precisa `quantize_price(price, tick_size)` + tick size dinâmico (evento `tick_size_change` no WS)
3. **Token IDs + negRisk** — Faltam `token_id_yes`, `token_id_no`, `tick_size`, `min_order_size`, `neg_risk` no MarketState
4. **Kill Switch contextual** — ENGINE_RESTART (425): pausa, backoff. HEARTBEAT_MISSED: fatal. kill_on_data_gap_seconds=8 deve ser contextual
5. **Arbitragem CTF** — YES+NO < 0.985 só funciona com merge on-chain (não vendendo no book). Precisa state machine: PAIR_PLANNED → LEG1_WORKING → LEG1_FILLED → LEG2_WORKING → BOTH_FILLED → MERGED/CLOSED
6. **Fees variam por tipo** — crypto 5m/15m tem taker fee ~1.56% em p=0.5. Precisa `expected_fee_bps` no FeatureVector

### Ajustes Arquiteturais Recomendados
- Interface explícita Live vs Paper (MarketDataProvider, ExecutionProvider)
- Event-driven interno (asyncio.Queue + trace_id)
- Idempotência persistente (unique index client_order_id)
- Reconciliação contábil (ordens vs venue vs token balances vs PnL)

### Hipóteses de Micro-Edge
1. Complete-set capture com merge (arbitragem estrutural)
2. Reverse arbitrage via split + vender caro
3. NegRisk multi-outcome
4. Quote engine com inventory skew + staleness
5. RTDS como alternativa a Binance direto

---

## Fonte 2: Gemini 3 Deep Think

### Críticas Adicionais
1. **DB no hot-path** — Postgres/SQLAlchemy a cada ciclo = +10-50ms. Solução: Memory-First (RAM) + Cold-Path async (filas → workers background)
2. **GIL + EIP-712** — Assinatura trava GIL. Solução: uvloop + ProcessPoolExecutor ou bindings Rust/PyO3
3. **Arbitragem sem merge** — Vender de volta no book = pagar spread + taker fee. Solução: `mergePositions` on-chain = $1/par sem taxa
4. **Breakeven exit trap** — Se mercado bateu na ordem, tem informação nova. Breakeven apodre ce. Solução: Avellaneda-Stoikov inventory skewing
5. **Oráculo míope** — BTC pra tudo faz cancelar ordens em "Oscar". Solução: Oracle Router por categoria

### Novas Hipóteses
1. **Liquidity Rewards Farming** — MMs grandes operam breakeven no book; lucro vem de rewards USDC diárias do protocolo perto do mid-price. Precisa `rewards_optimizer.py`
2. **Smart Pennying + Queue Tracking** — Cancelar/reposicionar = perder fila (price-time priority). Precisa `queue_tracker.py` — se está no topo, não reprecifica
3. **Toxic Flow Fade** — Order Imbalance extremo = fluxo informado. Micro-halt, retirar ordens

### Novos Módulos Propostos
- `web3_infra/ctf_adapter.py` — merge/split on-chain
- `web3_infra/eip712_signer.py` — offload assinaturas
- `web3_infra/rpc_manager.py` — fallbacks RPC
- `data/collectors/oracles/crypto_ws.py` + `macro_odds.py`
- `strategy/inventory_skew.py` — Avellaneda-Stoikov
- `strategy/rewards_farming.py` — caçador de rewards
- `execution/queue_tracker.py` — estimativa de posição na fila

### Roadmap Reescrito (Gemini)
- Etapa 1: Bootstrap HFT (uvloop, structlog, Decimal, tolerâncias 425/heartbeat)
- Etapa 2: Schemas com QuotePlan substituindo TradeSignal; tick_size, neg_risk
- Etapa 3: Data Layer com simulador de caos (tick_size_change, 425, delays)
- Etapa 4: Features com fee_bps, toxic flow, queue position
- Etapa 5: Quote Engine + Inventory Skew + Complete-set capture
- Etapa 6: Risk com tolerância contextual (425 vs heartbeat)
- Etapa 7: Execution com reconciliação contábil + quantize_price
- Etapa 8: Orquestração event-driven (asyncio.Queue, DB sai do hot-path)
- Etapa 9: IA Copilot (análise, não execução)
- Etapa 10: Monitoring API

### Código da Etapa 1 (Entregue pelo Gemini)
- requirements.txt com uvloop, structlog, asyncpg
- config.py com Decimal para limites + HEARTBEAT_INTERVAL + ENGINE_RESTART_TOLERANCE
- logger.py com structlog (JSON prod, console dev)
- storage.py async (pool_size=10, max_overflow=20)
- main.py com uvloop + heartbeat loop
- docker-compose.yml (Postgres + Redis)
- Dockerfile, Makefile, README

---

## Consenso entre GPT e Gemini

Ambos concordam em:
1. ✅ Quote Engine > Signal Engine direcional
2. ✅ Decimal/ticks, não float
3. ✅ CTF merge/split é operação central
4. ✅ Memory-first hot-path
5. ✅ Heartbeat + 425 handling são críticos
6. ✅ Inventory skew (Avellaneda-Stoikov)
7. ✅ IA só como copiloto analítico
8. ✅ Event-driven interno
9. ✅ Paper mode que simula venue real (caos)

Diferenças:
- Gemini mais agressivo em perf (uvloop, Rust bindings) — GPT diz "medir antes"
- GPT mais detalhado em state machine de pares e reconciliação contábil
- Gemini trouxe Liquidity Rewards como fonte primária de lucro (insight importante)
