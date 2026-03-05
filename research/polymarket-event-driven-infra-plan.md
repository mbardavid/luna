# Plano de Implementação: Infraestrutura Event-Driven Direcional (Polymarket) — Paper/Dry-Run

**Status:** plan_submitted
**MC Task:** `714ea1a9-0fb8-4a77-bd35-cf3c7c7691d1`  
**Objetivo:** definir arquitetura e contrato de implementação da pipeline de trading direcional sem execução real.

## 0) Escopo e decisão inicial

- Pipeline modular, **event-driven** com 5 estágios:
  1) ingestão de notícias/dados
  2) scoring de mercado + detector de mispricing
  3) size (Kelly fracionado)
  4) execução via CLOB em modo **paper/dry-run**
  5) rastreamento de PnL no **Supabase**
- Não executar ordens reais nesta fase (apenas plano e estrutura de segurança).

## 1) Stack recomendada

### Recomendação principal: **Python**
- Já há artefatos já existentes em Python para Polymarket (`research/polymarket_event_driven_trading.py`, `research/nautilus_polymarket_validator/*`) e testes em `tests/` com padrão pytest.
- Forte suporte para:
  - processamento assíncrono (`asyncio`, `anyio`)
  - testes determinísticos com mocks (`pytest`)
  - análise de dados (pandas/pyarrow) e integração com APIs REST/WS
  - tipagem e validação de contratos (`pydantic`/`dataclasses`)
- Node pode ficar para utilitários de integração UI/ops isolados, mas para esta etapa a consistência e governança sugerem Python.

## 2) Arquitetura-alvo (modular por contratos)

```
[Ingestão] -> [Normalização] -> [Detector/Misspricing] -> [Sizer Kelly] -> [Execution Adapter]
   -> [Fill/Trade Ledger] -> [PnL Service] -> [Observabilidade + Guardrails]
```

### 2.1 Módulos e interfaces (contratos claros)

> Todas as interfaces abaixo são propostas em termos de “contrato de I/O” (input/output), sem código.

#### A) `ingestion.news`
- **Responsável:** coletar sinais de notícias e notícias macro/market data.
- **Entrada:** `IngestionTarget` (lista de fontes, símbolos/mercados-alvo, lookback, timezone)
- **Saída:** `NewsEventRaw` padronizado `{source, source_id, title, body, url, published_at, market_hint[], confidence_hint, raw_payload}`
- **Contratos auxiliares:**
  - `normalized` com `event_fingerprint` (hash determinístico) para dedupe.
  - `source_dedupe_key` para idempotência.

#### B) `ingestion.polymarket`
- **Responsável:** dados do livro e metadata de mercados Polymarket.
- **Entrada:** `MarketIngestRequest` `{market_ids?, query_filters?, max_age_minutes}`
- **Saída:** `MarketSnapshot` `{event_id, market_id, outcome_token_ids, ask, bid, mid, volume_24h, liquidity, ts, orderbook_top_levels[]}`

#### C) `ingestion.sports` (opcional inicialmente no MVP)
- **Responsável:** odds de casas/feeds esportivos para `p_fair` de referência.
- **Entrada:** `SportsIngestRequest` `{sport, league, market_ids?, fixture_ids?, max_stale_sec}`
- **Saída:** `SportsBookSnapshot` `{market_id, book, fair_prob_yes, vig_removed, books_coverage[], ts}`

#### D) `scoring.mispricing`
- **Responsável:** calcular fair value, edge e confiança.
- **Entrada:** `ScoringInput`
  - `MarketSnapshot`
  - `NewsEventNormalized[]`
  - `SportsBookSnapshot[]`
  - `PriorConfig` (por categoria)
- **Saída:** `MispricingSignalCandidate`
  - `{event_id, direction(YES/NO), p_model, p_market, edge_raw, edge_net, confidence, category, reasons[], ttl_sec, source_coverage}`
- **Regras mínimas de saída:** só emitir se `abs(edge_net) >= threshold` e `confidence >= threshold`.

#### E) `position.kelly`
- **Responsável:** sizing de posição com Kelly fracionado + limites.
- **Entrada:** `SizingInput` = `{candidate, bankroll_snapshot, risk_cap_state, max_notional_by_asset, max_total_exposure}`
- **Saída:** `SizingDecision`
  - `{size_usdc, direction, max_ticks_tif?, limit_price, rationale, expected_edge, slippage_guard, stop_plan}`
- **Invariantes:** `size_usdc` sempre em [min,max] globais, e zero se violar risco.

#### F) `execution.clob_gateway`
- **Responsável:** simular/rotear ordens no modo paper.
- **Entrada:** `OrderIntent` `{strategy_id, market_id, side, size_usdc, price, venue=polymarket, ttl_sec, dry_run=True}`
- **Saída:** `ExecutionIntent`
  - `{intent_id, status=APPROVED|REJECTED|SIMULATED_FILLED|SIMULATED_REJECTED, message, paper_venue, trace_id}`
- **Observação:** somente em `TRADING_MODE=paper` este módulo pode retornar `SIMULATED_*`.

#### G) `execution.idempotency`
- **Responsável:** garantir no-op para events duplicados.
- **Entrada:** chave composta (`event_fingerprint`, `market_id`, `side`, `size_bucket`, `time_bucket`)
- **Saída:** `IdempotencyResult` `{accepted, deduped, previous_tx_ref?}`

#### H) `pnl.tracker`
- **Responsável:** registrar custo de entrada, mark-to-market, PnL realizado e exposição.
- **Entrada:** `TradeEvent` + snapshots de preços de liquidação + evento de resolução.
- **Saída:** `PnlSnapshot` `{realized_pnl_usdc, unrealized_pnl_usdc, exposure_usdc, hit_rate, fees, created_at}`

#### I) `ops.guardrails`
- **Responsável:** bloqueios globais e circuit-breakers.
- **Entradas:** métricas de saúde, token budgets, error budget, kill-switch.
- **Saída:** `GateDecision` `{allow, reason, expiry_s, override_key?}`

## 3) Pipeline de dados e eventos

- **Canal interno de eventos:** fila (ex: Redis/Postgres NOTIFY/Outbox) com tópico por etapa:
  - `news.raw`, `market.snapshot`, `signal.raw`, `signal.validated`, `size.decided`, `order.intent`, `trade.paper`, `pnl.update`
- **Dedupe e idempotência:** todos os eventos carregam `event_id` e `idempotency_key`; estado transacional por upsert/unique index.
- **Observabilidade mínima:** log de transições de estado + métricas `count`, `latency p50/p95`, `reject_reason`, `error_rate`.

## 4) Esquema mínimo de dados (Supabase)

### Tabela: `pm_pipeline_runs`
- `id` UUID PK
- `run_id` text unique
- `phase` text (`ingest|score|size|exec|pnl`)
- `status` text
- `started_at`, `finished_at`, `created_at`
- `environment` text
- `error` jsonb

### Tabela: `pm_events`
- `id` UUID PK
- `event_key` text unique (`sha256(source+source_id+published_at+market_hint)`) 
- `source` text, `source_id` text, `event_type` text
- `market_id` text
- `payload` jsonb
- `status` text (`new|normalized|deduped|discarded`)
- `normalized_at` timestamptz
- `created_at` timestamptz

### Tabela: `pm_market_snapshots`
- `id` UUID PK
- `market_id` text
- `event_id` text
- `snapshot_at` timestamptz
- `bid` numeric
- `ask` numeric
- `mid` numeric
- `liquidity_usd` numeric
- `orderbook_depth` jsonb
- `raw` jsonb
- unique (`market_id`,`snapshot_at`)

### Tabela: `pm_signals`
- `id` UUID PK
- `signal_id` text unique
- `market_id` text
- `category` text
- `direction` text
- `p_model` numeric
- `p_market` numeric
- `edge_net` numeric
- `confidence` numeric
- `sources` jsonb
- `expires_at` timestamptz
- `state` text (`candidate|accepted|rejected`)
- `created_at` timestamptz

### Tabela: `pm_positions`
- `id` UUID PK
- `market_id` text unique
- `active_position` boolean
- `side` text
- `size_usdc` numeric
- `entry_price` numeric
- `entry_at` timestamptz
- `stop_loss` numeric
- `take_profit` numeric
- `status` text
- `expires_at` timestamptz

### Tabela: `pm_orders`
- `id` UUID PK
- `market_id` text
- `idempotency_key` text unique
- `intent_id` text
- `dry_run` boolean default true
- `venue` text default 'polymarket'
- `status` text
- `size_usdc` numeric
- `price` numeric
- `simulated_fill_price` numeric null
- `simulated_fill_qty` numeric null
- `created_at` timestamptz
- `executed_at` timestamptz
- `error_code` text

### Tabela: `pm_fills`
- `id` UUID PK
- `order_id` UUID FK -> pm_orders(id)
- `fill_id` text unique
- `fill_size` numeric
- `fill_price` numeric
- `filled_at` timestamptz
- `fee_usdc` numeric
- `is_paper` boolean

### Tabela: `pm_pnl`
- `id` UUID PK
- `run_id` text
- `market_id` text
- `snapshot_at` timestamptz
- `realized_pnl` numeric
- `unrealized_pnl` numeric
- `cash_balance` numeric
- `position_notional` numeric
- `fees_paid` numeric
- `equity_curve` jsonb

### Tabela: `pm_dead_letter`
- `id` UUID PK
- `stage` text
- `payload` jsonb
- `error` text
- `retry_count` int
- `next_retry_at` timestamptz
- `created_at` timestamptz

## 5) Arquivos a criar/alterar (futuro implementação)

### Criar
- `research/polymarket-event-driven-infra-plan.md` (este documento)
- `research/polymarket_event_driven_infra/` (estrutura de projeto)
  - `__init__.py`
  - `interfaces.py`
  - `ingestion/news.py`
  - `ingestion/polymarket.py`
  - `ingestion/sports.py`
  - `scoring/mispricing.py`
  - `sizing/kelly.py`
  - `execution/clob_adapter.py`
  - `execution/dry_run_gateway.py`
  - `persistence/supabase.py`
  - `persistence/models.py`
  - `risk/guardrails.py`
  - `pnl/tracker.py`
  - `pipeline/orchestrator.py`
  - `config/event_driven.yaml`
  - `tests/unit/`
  - `tests/integration/smoke/`

### Alterar
- `requirements.txt` ou equivalente (incluir libs já padronizadas de conexão Supabase/validators)
- `scripts/` (runner de pipeline e comandos `make`)
- `docs/` (runbook de operação)

## 6) Comandos de verificação (roadmap de validação)

### Setup
- `python -m pip install -r requirements.txt`  (ou ambiente equivalente)
- `python -m pip install -e .` (caso passe a haver pacote instalável)

### Validação de qualidade (quando implementado)
- `python -m pytest tests/unit/research/test_polymarket_*`
- `python -m pytest tests/integration/smoke/test_polymarket_pipeline_smoke.py -q`
- `python -m pytest --maxfail=1 -q`
- `python -m pytest tests/unit -k "idempotent or dedupe"`

### Verificações operacionais locais
- `python -m scripts.run_polymarket_event_driven --mode paper --once`
- `python -m scripts.run_polymarket_event_driven --mode paper --env staging --config config/event_driven.yaml`
- `python -m scripts.run_polymarket_event_driven --mode paper --drain-dead-letter`

> Observação: estes comandos são de plano; nomes podem ser ajustados no design técnico final.

## 7) Estratégia de testes (unit + smoke)

### Unit tests
- `ingestion`:
  - parser normaliza datas/valores inválidos
  - dedupe por `event_key` e estabilidade de hash
  - fallback quando source indisponível
- `scoring`:
  - edge líquida correta (`edge_raw - fees - slippage`)
  - limiares por categoria
  - rejeição se confiança baixa
- `sizing`:
  - Kelly fracionado com p in (0,1)
  - limite máximo de exposição e per-market cap
  - proteção contra `size<=0`
- `idempotency`:
  - reprocessamento duplicado retorna `deduped=true` sem side effects
- `pnl tracker`:
  - realized/unrealized consistentes com evento de resolução
  - fees acumuladas por ciclo

### Smoke tests
- fluxo end-to-end com fixtures determinísticas:
  - News -> Signal -> Sizing -> Paper Fill -> PnL Write
  - falha de API externa simulada -> dead-letter + retry/backoff sem crash
  - `kill-switch` ativo -> bloqueio de stage de execução
- validação de “no live trades”:
  - `TRADING_MODE=paper` retorna apenas `SIMULATED_*`
  - `TRADING_MODE=live` exige flag explícita de override aprovada e segredo específico.

## 8) Segurança e resiliência (checklist)

### Rate limit
- Rate limiter por fonte e por rota (token bucket + jitter/backoff exponencial + circuit breaker por erro 429/5xx).
- Cache de snapshots e `stale-while-revalidate` para reduzir chamadas.
- Métricas de “request/sec” e janela deslizante por fonte.

### Tor / egress fallback (quando necessário)
- Classificar fontes com risco/geo/IP instável para rota com fallback de egress.
- Injetar `egress_profile` por fonte no config (normal/proxy/tor).
- Se Tor falhar, degradar para polling menos frequente e modo de observação.

### Idempotência e dedupe
- Chaves determinísticas em cada fase:
  - `ingest_event_key`
  - `signal_id`
  - `order_intent_id` + `idempotency_key`
- Upsert por chave única + `SELECT ... FOR UPDATE` para evitar corrida.

### Sem chaves reais nos logs
- Lista bloqueada de variáveis sensíveis em logger masker (`*_KEY`, `*_SECRET`, `PK`, `TOKEN`, `SEED`).
- Logs estruturados sem payloads completos de auth request/response.
- Secrets apenas via vars de ambiente/vault; nunca commitar `.env`.

### Anti-live-trades (obrigatório)
- Ambiente com modos explícitos:
  - `TRADING_MODE=paper` (default)
  - `TRADING_MODE=live` (desabilitado por padrão via flag manual)
- `execution.clob_gateway` sempre valida:
  1. `TRADING_MODE==paper` => `SIMULATED` obrigatório.
  2. sem `DRY_RUN=true` -> **reject**.
  3. `ALLOW_LIVE_TRADES==1` + feature flag aprovada + revisão humana para mudar de modo.
- Separação física de credenciais: chaves de produção em escopo distinto e só carregadas em ambiente autorizado.
- Auditoria: inserir em `pm_orders.is_paper` = `true` e `pm_runs`/`pm_pnl` imutáveis após startup.

## 9) Entregáveis mapeados à aceitação

- [x] Componentes com contratos claros definidos em seções 2A–2I.
- [x] Esquema mínimo de dados e persistência Supabase definido em seção 4.
- [x] Estratégia de testes unit + smoke e comandos em seção 7–6.
- [x] Garantia explícita de no-live-trades em seção 8 (modo paper, flags e guardrails).

## 10) Status final

**plan_submitted**