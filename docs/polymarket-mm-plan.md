# Polymarket Market Maker — Plano de Projeto

> **Versão:** 1.0 — 25 Fev 2026
> **Baseado em:** Research consolidado GPT 5.2 Pro + Gemini 3 Deep Think
> **Autor:** Luan (agente de engenharia)

---

## 1. Visão Geral

Market maker **maker-only** operando no CLOB (Central Limit Order Book) do Polymarket via REST + WebSocket. O bot publica cotas bilaterais (bid YES + ask YES + bid NO + ask NO) em mercados de eventos selecionados, capturando spread e incentivos de liquidez do protocolo.

**Princípios:**

| Princípio | Implementação |
|---|---|
| Maker-only | Todas as ordens com `orderType: GTC`, flag `maker_only=True`. Nunca cruza o book. |
| Precisão de preço | `Decimal` nativo + `quantize_price()` por tick size dinâmico. Zero floats. |
| Hot-path sem I/O pesado | Nenhum Postgres no loop principal; RAM-first, persistência em cold-path assíncrono. |
| Bilateral por mercado | `QuotePlan` com slices em YES e NO simultaneamente. |
| CTF-aware | Merge/split on-chain para arbitragem estrutural (complete-set). |
| Fail-safe | Kill switch contextual: ENGINE_RESTART (425) → pausa/backoff, HEARTBEAT_MISSED → fatal. |

---

## 2. Arquitetura

### 2.1 Árvore de Pastas

```
polymarket-mm/
├── docker-compose.yml
├── Dockerfile
├── Makefile
├── README.md
├── requirements.txt
├── pyproject.toml
├── .env.example
│
├── config/
│   ├── __init__.py
│   ├── settings.py              # Pydantic BaseSettings, todos Decimal
│   └── markets.yaml             # Allowlist de mercados + parâmetros por mercado
│
├── core/
│   ├── __init__.py
│   ├── main.py                  # Entrypoint: uvloop + orquestrador event-driven
│   ├── event_bus.py             # asyncio.Queue com trace_id + fanout
│   ├── heartbeat.py             # Monitor de heartbeat WS + auto-kill
│   ├── kill_switch.py           # Kill switch contextual (425 vs heartbeat vs data gap)
│   └── logger.py                # structlog (JSON prod, console dev)
│
├── models/
│   ├── __init__.py
│   ├── market_state.py          # MarketState: token_id_yes, token_id_no, tick_size,
│   │                            #   min_order_size, neg_risk, mid_price, book_snapshot
│   ├── quote_plan.py            # QuotePlan: bid/ask YES + bid/ask NO com slices
│   ├── feature_vector.py        # FeatureVector: spread, volatility, imbalance,
│   │                            #   expected_fee_bps, oracle_delta, toxic_flow_score
│   ├── order.py                 # Order: client_order_id (UUID), status, fill_qty
│   └── position.py              # Position: qty_yes, qty_no, avg_entry, unrealized_pnl
│
├── data/
│   ├── __init__.py
│   ├── market_data_provider.py  # Interface ABC: LiveMarketData / PaperMarketData
│   ├── ws_client.py             # WebSocket CLOB: book updates, tick_size_change, fills
│   ├── rest_client.py           # REST CLOB: snapshots, order placement
│   └── collectors/
│       ├── __init__.py
│       └── oracles/
│           ├── __init__.py
│           ├── oracle_router.py     # OracleRouter: despacha por categoria do mercado
│           ├── crypto_ws.py         # Binance/RTDS WebSocket para mercados crypto
│           ├── macro_odds.py        # Scraper de odds externas (esportes, eleições)
│           └── sentiment.py         # Sinais de volume/momentum do próprio CLOB
│
├── strategy/
│   ├── __init__.py
│   ├── quote_engine.py          # QuoteEngine: gera QuotePlan a partir de FeatureVector
│   ├── inventory_skew.py        # InventorySkew: Avellaneda-Stoikov, skew bid/ask
│   ├── spread_model.py          # SpreadModel: calcula half-spread ótimo por mercado
│   ├── rewards_farming.py       # RewardsFarming: otimiza posicionamento perto do mid
│   ├── toxic_flow_detector.py   # ToxicFlowDetector: order imbalance → micro-halt
│   └── complete_set.py          # CompleteSetStrategy: state machine de pares CTF
│
├── execution/
│   ├── __init__.py
│   ├── execution_provider.py    # Interface ABC: LiveExecution / PaperExecution
│   ├── order_manager.py         # OrderManager: submit, amend, cancel, idempotência
│   ├── queue_tracker.py         # QueueTracker: estimativa de posição na fila (FIFO)
│   ├── quantizer.py             # quantize_price(price, tick_size) → Decimal
│   └── reconciler.py            # Reconciler: ordens vs venue vs token balances vs PnL
│
├── web3_infra/
│   ├── __init__.py
│   ├── ctf_adapter.py           # CTFAdapter: mergePositions / splitPosition on-chain
│   ├── eip712_signer.py         # EIP712Signer: offload para ProcessPoolExecutor
│   └── rpc_manager.py           # RPCManager: fallback entre RPCs, health check
│
├── storage/
│   ├── __init__.py
│   ├── memory_store.py          # MemoryStore: dict em RAM para hot-path
│   ├── cold_writer.py           # ColdWriter: asyncio.Queue → asyncpg batch insert
│   └── migrations/
│       └── 001_initial.sql      # Tabelas: orders, fills, positions, pnl_snapshots
│
├── monitoring/
│   ├── __init__.py
│   ├── metrics.py               # Prometheus counters/gauges: fills, PnL, latência
│   ├── health.py                # /health endpoint (aiohttp)
│   ├── dashboard.py             # Exportador Grafana-ready
│   └── alerter.py               # Alertas: Telegram/Discord webhook
│
├── ai_copilot/
│   ├── __init__.py
│   ├── post_mortem.py           # PostMortem: análise diária automática
│   ├── param_tuner.py           # ParamTuner: bayesian optimization de spreads
│   └── anomaly_detector.py      # AnomalyDetector: z-score em métricas chave
│
├── paper/
│   ├── __init__.py
│   ├── paper_venue.py           # PaperVenue: simula book, fills, latência, 425s
│   ├── chaos_injector.py        # ChaosInjector: tick_size_change, delays, desconexão
│   └── replay_engine.py         # ReplayEngine: replay de dados históricos
│
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_quote_engine.py
    ├── test_inventory_skew.py
    ├── test_quantizer.py
    ├── test_ctf_adapter.py
    ├── test_queue_tracker.py
    ├── test_reconciler.py
    └── test_complete_set.py
```

### 2.2 Fluxo de Dados (Hot-Path)

```
WS CLOB ──► ws_client ──► event_bus ──► market_state (RAM)
                                │
Oracle WS ──► oracle_router ────┘
                                │
                         feature_vector
                                │
                         quote_engine
                          ├─ inventory_skew
                          ├─ spread_model
                          ├─ rewards_farming
                          └─ toxic_flow_detector
                                │
                          QuotePlan
                                │
                         order_manager
                          ├─ quantizer
                          ├─ queue_tracker  ← decide se reprecifica ou mantém fila
                          └─ execution_provider
                                │
                     ┌──────────┴──────────┐
                     │                     │
               REST CLOB              cold_writer (async)
              (submit/amend)          (Postgres batch)
```

### 2.3 Classes e Interfaces Principais

```python
# core/event_bus.py
class EventBus:
    async def publish(self, topic: str, payload: dict, trace_id: str) -> None
    async def subscribe(self, topic: str) -> AsyncIterator[Event]

# strategy/quote_engine.py
class QuoteEngine:
    def __init__(self, skew: InventorySkew, spread: SpreadModel,
                 rewards: RewardsFarming, toxic: ToxicFlowDetector)
    async def generate_quotes(self, state: MarketState, features: FeatureVector) -> QuotePlan

# strategy/inventory_skew.py
class InventorySkew:
    """Avellaneda-Stoikov: δ = γ * σ² * (T - t) * q"""
    def compute_skew(self, position: Position, volatility: Decimal,
                     gamma: Decimal, time_horizon: Decimal) -> Decimal

# strategy/complete_set.py
class CompleteSetStrategy:
    """State machine: IDLE → PAIR_PLANNED → LEG1_WORKING → LEG1_FILLED
       → LEG2_WORKING → BOTH_FILLED → MERGING → MERGED"""
    async def evaluate(self, state: MarketState) -> Optional[PairTrade]
    async def advance(self, event: FillEvent) -> None

# execution/queue_tracker.py
class QueueTracker:
    """Estima posição na fila por (price_level, side)."""
    def update(self, book_delta: BookDelta) -> None
    def estimated_position(self, order: Order) -> int
    def should_reprice(self, order: Order, new_price: Decimal) -> bool

# execution/quantizer.py
def quantize_price(price: Decimal, tick_size: Decimal) -> Decimal
def quantize_size(size: Decimal, min_order_size: Decimal) -> Decimal

# web3_infra/ctf_adapter.py
class CTFAdapter:
    async def merge_positions(self, token_id_yes: str, token_id_no: str,
                               amount: Decimal) -> TxReceipt
    async def split_position(self, condition_id: str, amount: Decimal) -> TxReceipt

# execution/reconciler.py
class Reconciler:
    """Reconcilia: ordens locais vs venue vs token balances on-chain vs PnL calculado."""
    async def full_reconciliation(self) -> ReconciliationReport
    async def incremental_check(self, fill: FillEvent) -> bool
```

---

## 3. Stack Tecnológica

| Camada | Tecnologia | Justificativa |
|---|---|---|
| Runtime | Python 3.11+ | asyncio nativo, `TaskGroup`, `ExceptionGroup` |
| Event loop | `uvloop` | 2-4x mais rápido que asyncio default |
| Logging | `structlog` | JSON em produção, console colorido em dev; trace_id nativo |
| Números | `decimal.Decimal` + tick quantization | Sem erros de float; compatível com CLOB |
| HTTP async | `aiohttp` ou `httpx[http2]` | REST API + health endpoint |
| WebSocket | `websockets` | Conexão CLOB + oracles |
| DB (cold-path) | `asyncpg` → PostgreSQL 16 | Batch inserts assíncronos, zero no hot-path |
| Cache/Pub-Sub | Redis 7 | Compartilhar estado entre processos, pub-sub de alertas |
| Assinatura | `eth-account` + `ProcessPoolExecutor` | EIP-712 sem travar GIL |
| Web3 | `web3.py` / `eth-abi` | Interação CTF (merge/split) |
| Config | Pydantic `BaseSettings` | Validação tipada, `.env` support |
| Testes | `pytest` + `pytest-asyncio` + `hypothesis` | Property-based testing para quantizer |
| Containers | Docker + docker-compose | Postgres, Redis, app isolados |
| Monitoramento | Prometheus + Grafana | Métricas de latência, PnL, fills |
| Alertas | Webhook Discord/Telegram | Kill switch acionado, reconciliação falhou |

---

## 4. Roadmap — 11 Fases (0 a 10)

### Fase 0 — Bootstrap Infra
**Objetivo:** Projeto rodando com loop vazio.
- [ ] `pyproject.toml` + `requirements.txt` (uvloop, structlog, asyncpg, pydantic)
- [ ] `config/settings.py` — todos os limites como `Decimal`
- [ ] `core/logger.py` — structlog JSON/console
- [ ] `core/main.py` — uvloop + heartbeat loop vazio
- [ ] `docker-compose.yml` — Postgres 16 + Redis 7
- [ ] Dockerfile multi-stage + Makefile
- [ ] CI básico (lint + type-check)
- **Entregável:** `make up` sobe o stack; loop roda sem erros.

### Fase 1 — Modelos e Schemas
**Objetivo:** Tipos corretos para todo o sistema.
- [ ] `models/market_state.py` — com `token_id_yes`, `token_id_no`, `tick_size`, `min_order_size`, `neg_risk`
- [ ] `models/quote_plan.py` — `QuotePlan` com slices bid/ask YES/NO
- [ ] `models/feature_vector.py` — incluindo `expected_fee_bps`, `toxic_flow_score`, `queue_position`
- [ ] `models/order.py` — `client_order_id` UUID, status enum
- [ ] `models/position.py` — qty_yes/no, avg_entry, PnL
- [ ] Testes unitários para todos os modelos
- **Entregável:** Modelos importáveis, tipados, 100% testados.

### Fase 2 — Data Layer
**Objetivo:** Receber dados do CLOB e oracles em tempo real.
- [ ] `data/ws_client.py` — WebSocket CLOB com reconexão automática, parsing de `tick_size_change`
- [ ] `data/rest_client.py` — Snapshots de book, info de mercados
- [ ] `data/market_data_provider.py` — Interface ABC (Live vs Paper)
- [ ] `core/event_bus.py` — asyncio.Queue com trace_id e fanout
- [ ] `data/collectors/oracles/oracle_router.py` — despacha por categoria
- [ ] `data/collectors/oracles/crypto_ws.py` — Binance WS para mercados crypto
- [ ] Ingestão de `markets.yaml` allowlist
- **Entregável:** `MarketState` atualizado em RAM a cada tick.

### Fase 3 — Paper Trading Venue
**Objetivo:** Simulador realista para desenvolvimento sem risco.
- [ ] `paper/paper_venue.py` — Simula book, matching engine, fills, latência
- [ ] `paper/chaos_injector.py` — Injeta `tick_size_change`, 425s, desconexões, delays
- [ ] `paper/replay_engine.py` — Replay de dados históricos do CLOB
- [ ] Implementar `PaperMarketData` e `PaperExecution`
- **Entregável:** Loop completo rodando em paper com caos simulado.

### Fase 4 — Feature Engineering
**Objetivo:** Extrair sinais do mercado.
- [ ] `models/feature_vector.py` — computação de: spread atual, volatilidade realizada (janela rolante), order imbalance, oracle delta, fee_bps estimado
- [ ] `strategy/toxic_flow_detector.py` — z-score de imbalance, micro-halt quando extremo
- [ ] `data/collectors/oracles/sentiment.py` — volume/momentum do próprio CLOB
- **Entregável:** `FeatureVector` computado a cada ciclo com todos os sinais.

### Fase 5 — Quote Engine + Inventory Skew
**Objetivo:** Geração de cotas bilaterais inteligentes.
- [ ] `strategy/spread_model.py` — half-spread ótimo = f(volatilidade, fee, liquidez)
- [ ] `strategy/inventory_skew.py` — Avellaneda-Stoikov: δ = γσ²(T-t)q
- [ ] `strategy/rewards_farming.py` — ajuste de cotas para maximizar rewards perto do mid
- [ ] `strategy/quote_engine.py` — combina spread + skew + rewards → `QuotePlan`
- [ ] Testes com diversos cenários (inventory alto, baixa vol, toxic flow)
- **Entregável:** `QuotePlan` gerado por ciclo; validado em paper.

### Fase 6 — Execution Layer
**Objetivo:** Enviar e gerenciar ordens no CLOB.
- [ ] `execution/quantizer.py` — `quantize_price()`, `quantize_size()` com `hypothesis` tests
- [ ] `execution/order_manager.py` — submit/amend/cancel, idempotência por `client_order_id` (unique index)
- [ ] `execution/queue_tracker.py` — estima posição na fila, decide se reprecifica
- [ ] `execution/execution_provider.py` — Interface ABC (Live vs Paper)
- [ ] `web3_infra/eip712_signer.py` — assinatura offloaded para `ProcessPoolExecutor`
- **Entregável:** Ordens enviadas em paper com quantização correta e tracking de fila.

### Fase 7 — Risk e Kill Switch
**Objetivo:** Proteção robusta contra perdas e falhas.
- [ ] `core/kill_switch.py` — contextual:
  - `ENGINE_RESTART` (425): pausa N segundos, backoff exponencial, retoma
  - `HEARTBEAT_MISSED`: cancelamento total, alerta fatal
  - `DATA_GAP > 8s`: cancelamento, mas contextual por mercado
  - `MAX_DRAWDOWN`: limite diário de perda
  - `RECONCILIATION_MISMATCH`: halt + alerta
- [ ] `execution/reconciler.py` — reconciliação ordens vs venue vs balances on-chain
- [ ] Alertas via webhook (Discord/Telegram)
- **Entregável:** Kill switch testado com chaos_injector em paper.

### Fase 8 — CTF Arbitragem On-Chain
**Objetivo:** Complete-set e reverse arbitrage via merge/split.
- [ ] `web3_infra/ctf_adapter.py` — `mergePositions()`, `splitPosition()`
- [ ] `web3_infra/rpc_manager.py` — fallback entre RPCs, health check
- [ ] `strategy/complete_set.py` — state machine:
  ```
  IDLE → PAIR_PLANNED → LEG1_WORKING → LEG1_FILLED
       → LEG2_WORKING → BOTH_FILLED → MERGING → MERGED
  ```
- [ ] Cálculo de breakeven incluindo gas cost
- [ ] Testes end-to-end em testnet
- **Entregável:** Arbitragem complete-set funcional em testnet.

### Fase 9 — IA Copilot (Analítico)
**Objetivo:** IA como assistente de análise, **nunca** execução.
- [ ] `ai_copilot/post_mortem.py` — análise diária: PnL, fills, spreads, anomalias
- [ ] `ai_copilot/param_tuner.py` — Bayesian optimization de γ, spread_min, etc.
- [ ] `ai_copilot/anomaly_detector.py` — z-score rolling em métricas chave
- [ ] Dashboard com recomendações (humano aprova)
- **Entregável:** Relatório diário automático com sugestões de ajuste.

### Fase 10 — Monitoring, API e Produção
**Objetivo:** Observabilidade total e deploy seguro.
- [ ] `monitoring/metrics.py` — Prometheus: fills/s, PnL cumulativo, latência p99, inventory
- [ ] `monitoring/health.py` — `/health`, `/ready` endpoints
- [ ] `monitoring/dashboard.py` — Grafana dashboards pré-configurados
- [ ] `monitoring/alerter.py` — alertas multi-canal
- [ ] `storage/cold_writer.py` — batch insert assíncrono + migrations
- [ ] Hardening: rate limits, IP allowlist, secrets via Vault/env
- [ ] Runbook de operação (restart, rollback, incident response)
- **Entregável:** Bot em produção com observabilidade completa.

---

## 5. Seis Fontes de Alpha

### 5.1 Spread Capture (Fonte Primária)
**Como funciona:** Postar bid e ask simultâneos em YES e NO. Quando ambos os lados executam, o lucro é a diferença entre os preços menos fees.

**Módulo:** `strategy/quote_engine.py` → `QuoteEngine.generate_quotes()`

**Edge:** Inventory skew (Avellaneda-Stoikov) garante que as cotas se ajustam ao inventário acumulado, evitando acúmulo direcional.

```python
# Exemplo simplificado
half_spread = spread_model.optimal_half_spread(volatility, fee_bps, liquidity)
skew = inventory_skew.compute_skew(position, volatility, gamma, time_horizon)
bid_yes = mid_price - half_spread + skew
ask_yes = mid_price + half_spread + skew
```

### 5.2 Complete-Set Arbitrage
**Como funciona:** Se `price_yes + price_no < 0.985` (considerando fees), compra ambos e faz `mergePositions` on-chain para receber $1.00 por par.

**Módulo:** `strategy/complete_set.py` → `CompleteSetStrategy`

**Importante:** Vender de volta no book **não funciona** (paga spread + taker fee). O merge on-chain é a única forma viável — custa ~$1 gas por par, sem taxa de protocolo.

```python
# Condição de entrada
margin = Decimal("1.0") - best_ask_yes - best_ask_no - gas_cost_per_pair
if margin > min_profit_threshold:
    strategy.plan_pair_trade(market)
```

### 5.3 Reverse Arbitrage (Split)
**Como funciona:** Se `price_yes + price_no > 1.015`, faz `splitPosition` on-chain ($1 → YES + NO tokens) e vende o lado mais caro no book.

**Módulo:** `strategy/complete_set.py` → reutiliza state machine com direção invertida.

### 5.4 Liquidity Rewards Farming
**Como funciona:** Polymarket distribui rewards USDC diárias para MMs com ordens perto do mid-price. MMs grandes operam **breakeven no spread** e lucram exclusivamente via rewards.

**Módulo:** `strategy/rewards_farming.py` → `RewardsFarming`

```python
class RewardsFarming:
    def adjust_for_rewards(self, quote_plan: QuotePlan,
                           reward_rate: Decimal,
                           distance_to_mid: Decimal) -> QuotePlan:
        """Aperta spread se reward compensa a redução de edge."""
```

**Insight crítico (Gemini):** Esta pode ser a fonte **primária** de lucro para MMs em mercados líquidos.

### 5.5 NegRisk Multi-Outcome
**Como funciona:** Em mercados negRisk (ex: "Quem ganha a eleição?" com 5+ candidatos), as probabilidades devem somar 1.0. Desvios criam oportunidades de arbitragem entre outcomes.

**Módulo:** Extensão de `strategy/complete_set.py` para múltiplos outcomes.

### 5.6 Toxic Flow Fade
**Como funciona:** Order imbalance extremo indica fluxo informado (alguém sabe algo). Em vez de ser atropelado, o bot detecta o imbalance, retira ordens (micro-halt) e só volta quando normalizar.

**Módulo:** `strategy/toxic_flow_detector.py` → `ToxicFlowDetector`

```python
class ToxicFlowDetector:
    def compute_toxicity_score(self, book: BookSnapshot,
                                recent_trades: list[Trade]) -> Decimal:
        """Z-score do order imbalance em janela rolante."""

    def should_halt(self, score: Decimal) -> bool:
        return score > self.halt_threshold  # ex: z > 2.5
```

---

## 6. Autoaperfeiçoamento

O sistema se auto-otimiza através de 5 mecanismos complementares:

### 6.1 Post-Mortem Diário
**Módulo:** `ai_copilot/post_mortem.py`

```python
class PostMortem:
    async def generate_daily_report(self, date: date) -> Report:
        """Analisa: PnL por mercado, fills executados, spreads médios,
        tempo em posição, drawdown máximo, rewards capturados,
        quantas vezes o kill switch acionou."""

    async def identify_patterns(self, reports: list[Report]) -> list[Insight]:
        """Detecta: mercados consistentemente perdedores, horários ótimos,
        correlação entre volatilidade e PnL."""
```

**Output:** Relatório Markdown salvo em `storage/reports/` + alerta no Discord.

### 6.2 Parameter Tuning
**Módulo:** `ai_copilot/param_tuner.py`

```python
class ParamTuner:
    """Bayesian Optimization (Optuna) para ajustar:
    - gamma (Avellaneda-Stoikov risk aversion)
    - spread_min_bps por categoria de mercado
    - toxic_flow_threshold
    - rewards_aggressiveness
    - data_gap_tolerance_seconds
    """
    async def suggest_params(self, history: pd.DataFrame) -> dict[str, Decimal]
    async def backtest_params(self, params: dict, replay_data: list) -> BacktestResult
```

**Regra:** Sugestões são **recomendações**. Humano aprova antes de aplicar em produção.

### 6.3 Anomaly Detection
**Módulo:** `ai_copilot/anomaly_detector.py`

```python
class AnomalyDetector:
    """Z-score rolling em métricas chave:
    - Latência de fill (p99)
    - Taxa de rejeição de ordens
    - PnL por hora vs média histórica
    - Spread realizado vs teórico
    - Inventory acumulado vs limite
    """
    def check(self, metrics: MetricsSnapshot) -> list[Anomaly]
```

### 6.4 A/B Testing
**Mecanismo:** Rodar 2 conjuntos de parâmetros em mercados similares simultaneamente. Comparar PnL/fill rate/drawdown após N dias.

```python
# config/markets.yaml
markets:
  - condition_id: "0xabc..."
    param_group: "A"        # gamma=0.3, spread_min=50bps
  - condition_id: "0xdef..."
    param_group: "B"        # gamma=0.5, spread_min=30bps
```

### 6.5 Feedback Loop Completo

```
Produção → Métricas → Post-Mortem → Insights → ParamTuner
    ↑         │                                      │
    │         └─── Anomaly Detector ─── Alertas      │
    │                                                 │
    └──────────── Humano aprova ──── Novos params ────┘
```

---

## 7. Riscos e Mitigações

| # | Risco | Severidade | Mitigação |
|---|---|---|---|
| 1 | **Execution risk: float pricing** | Crítica | `Decimal` nativo + `quantize_price()` + `hypothesis` tests com edge cases |
| 2 | **ENGINE_RESTART (425)** | Alta | Kill switch contextual: pausa + backoff exponencial, não cancela tudo |
| 3 | **Heartbeat loss** | Crítica | Cancelamento total imediato + alerta. Nunca operar sem heartbeat. |
| 4 | **Inventário direcional** | Alta | Avellaneda-Stoikov skew + hard limits de posição por mercado |
| 5 | **Toxic flow (insider info)** | Alta | `ToxicFlowDetector` micro-halt + retirada de ordens |
| 6 | **Latência DB no hot-path** | Média | Memory-first; Postgres apenas em cold-path assíncrono |
| 7 | **GIL bloqueio (EIP-712)** | Média | `ProcessPoolExecutor` para assinaturas; considerar bindings Rust/PyO3 se necessário |
| 8 | **Arbitragem sem merge** | Alta | **Nunca** vender de volta no book. Sempre `mergePositions` on-chain. |
| 9 | **Tick size change inesperado** | Média | Listener WS para `tick_size_change`; revalida e requantiza todas as ordens |
| 10 | **Reconciliação divergente** | Crítica | `Reconciler` periódico; halt automático se divergência > threshold |
| 11 | **Gas spike em merge** | Média | Monitor de gas price; aborta pair trade se gas > breakeven |
| 12 | **Breakeven exit trap** | Média | Nunca usar breakeven estático. Skew Avellaneda-Stoikov faz o preço de saída refletir informação nova. |
| 13 | **Oráculo míope (BTC para tudo)** | Média | `OracleRouter` despacha por categoria; mercados non-crypto não consultam BTC |
| 14 | **Perda de fila ao reprecificar** | Média | `QueueTracker`: se está no topo da fila, não reprecifica por delta pequeno |
| 15 | **API rate limits** | Média | Rate limiter local + exponential backoff + batch operations |

---

## 8. Infraestrutura

### 8.1 Docker Compose

```yaml
version: "3.9"

services:
  app:
    build: .
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    restart: unless-stopped
    ports:
      - "8080:8080"    # health + metrics
    volumes:
      - ./config:/app/config:ro
      - ./storage/reports:/app/reports

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: polymarket_mm
      POSTGRES_USER: mm
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./storage/migrations:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U mm"]
      interval: 5s
      timeout: 3s
      retries: 5
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    ports:
      - "6379:6379"

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"

  grafana:
    image: grafana/grafana:latest
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    volumes:
      - grafana-data:/var/lib/grafana
    ports:
      - "3000:3000"

volumes:
  pgdata:
  grafana-data:
```

### 8.2 Banco de Dados (Cold-Path)

```sql
-- storage/migrations/001_initial.sql

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    client_order_id UUID UNIQUE NOT NULL,
    market_id       TEXT NOT NULL,
    side            TEXT NOT NULL,       -- BID_YES, ASK_YES, BID_NO, ASK_NO
    price           NUMERIC(10,4) NOT NULL,
    size            NUMERIC(18,6) NOT NULL,
    status          TEXT NOT NULL,       -- PENDING, OPEN, FILLED, PARTIALLY_FILLED, CANCELLED
    venue_order_id  TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS fills (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT REFERENCES orders(id),
    fill_price      NUMERIC(10,4) NOT NULL,
    fill_size       NUMERIC(18,6) NOT NULL,
    fee_bps         NUMERIC(6,2),
    filled_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    market_id       TEXT UNIQUE NOT NULL,
    qty_yes         NUMERIC(18,6) DEFAULT 0,
    qty_no          NUMERIC(18,6) DEFAULT 0,
    avg_entry_yes   NUMERIC(10,4),
    avg_entry_no    NUMERIC(10,4),
    unrealized_pnl  NUMERIC(18,6) DEFAULT 0,
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_time   TIMESTAMPTZ DEFAULT NOW(),
    total_pnl       NUMERIC(18,6) NOT NULL,
    realized_pnl    NUMERIC(18,6) NOT NULL,
    unrealized_pnl  NUMERIC(18,6) NOT NULL,
    rewards_earned  NUMERIC(18,6) DEFAULT 0,
    num_fills       INTEGER DEFAULT 0,
    metadata        JSONB
);

CREATE INDEX idx_orders_market ON orders(market_id, status);
CREATE INDEX idx_orders_client_id ON orders(client_order_id);
CREATE INDEX idx_fills_time ON fills(filled_at);
CREATE INDEX idx_pnl_time ON pnl_snapshots(snapshot_time);
```

### 8.3 Redis (Hot State)

```
Uso principal:
- Cache de MarketState por mercado (hash)
- Pub/Sub para alertas internas entre processos
- Rate limiter (sliding window)
- Lock distribuído para operações CTF (merge/split)

Keys:
  mm:state:{market_id}      → Hash com mid, spread, inventory
  mm:alerts                  → Pub/Sub channel
  mm:ratelimit:{endpoint}    → Sorted set (sliding window)
  mm:lock:merge:{market_id}  → String com TTL (distributed lock)
```

### 8.4 Deploy

```
Produção:
  - VPS dedicada (4 vCPU, 8GB RAM) — latência baixa para Polygon RPC
  - Docker Compose com restart policies
  - Backups Postgres diários (pg_dump → S3)
  - Logs centralizados (structlog JSON → file rotation)
  - Secrets via .env (futuro: Vault)

Desenvolvimento:
  - docker-compose up (local)
  - Paper mode por padrão
  - Chaos injector habilitado
```

---

## Apêndice: Checklist Pré-Produção

- [ ] Todos os modelos tipados com Pydantic + Decimal
- [ ] `quantize_price` testado com hypothesis (edge cases)
- [ ] Paper venue simulando 425s, tick changes, delays
- [ ] Reconciler validado: ordens locais = venue = balances on-chain
- [ ] Kill switch testado para cada cenário (425, heartbeat, data gap, drawdown)
- [ ] Queue tracker calibrado com dados reais
- [ ] Complete-set testado em testnet Polygon
- [ ] Backtest de 30 dias com replay engine
- [ ] Métricas Prometheus + dashboard Grafana funcionando
- [ ] Alertas Discord/Telegram configurados e testados
- [ ] Runbook de operação documentado
- [ ] Revisão humana de todos os parâmetros antes do go-live
