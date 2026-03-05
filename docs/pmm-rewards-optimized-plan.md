# Plano de Implementação (sem código): PMM Rewards-Optimized Mode + Roadmap de Scale-up $500→$10k

**Status:** `plan_submitted`  
**MC Task ID:** `5d78b559-d3a3-4620-b741-6b9151719231`  
**Objetivo:** evoluir o runner de PMM para priorizar captação de rewards com segurança de capital, sem implementação, com trilha completa de governança e observabilidade.

---

## 1) Decisão de modo: o que muda no `rewards_mode: true`

### 1.1 Objetivo funcional
Com `rewards_mode: true`, o objetivo deixa de ser **maximizar fill / PnL de spread** e passa a priorizar:

1. **Tempo no livro (time-in-book / book-occupancy)** acima de métricas de retorno imediato;
2. **Aderência a requisitos de rewards** por mercado (`rewardsMinSize`, `rewardsMaxSpread`);
3. **Confiabilidade de execução** (semântica de “não quebrar fila” / não gastar capital sem retorno por rewards);
4. **Conservação de liquidez** (evitar exposição agressiva no modo rewards).

### 1.2 Regras de estratégia (não negociáveis)
- Em `rewards_mode=true`, **não otimizar**:
  - tamanho por “max fill” ou “max edge imediata”;
  - aceleração de repost agressiva para capturar spread curto;
  - aumento de frequência sem evidência de compliance de rewards.
- Em `rewards_mode=false`, manter comportamento atual de execução padrão (referência).

**Regra de decisão interna de ciclo:**
- `selection_score_rewards >= spread_score_threshold` **e** `book_time_compliance >= reward_minimum_compliance` antes de manter/atualizar quotes.

---

## 2) Arquitetura proposta (modo rewards)

### 2.1 Componentes / módulos

```
[Config Layer] -> [Market Discovery] -> [Rewards Gate] -> [Book-Timer Policy] ->
[Reward-Aware Quote Engine] -> [Execution Adapter] -> [Reward/PnL Split Ledger] -> [Telemetry]
```

#### Módulos planejados
- **`rewards_mode` (Feature Flag Service):** orquestra modo, ambiente (paper/live), parâmetros de guardrails.
- **`market_selector` (Gamma + CLOB):** seleciona mercados aptos para rewards.
- **`book_time_policy`:** decide side, frequência e distância de quote para maximizar tempo no book.
- **`reward_gate`:** valida thresholds `rewardsMinSize` e `rewardsMaxSpread` por mercado.
- **`quote_policy_rewards`:** reduz risco de churn (cancel/repost), prioriza continuidade de ordem.
- **`exec_guard`:** bloqueia live sem override explícito e sem aprovação.
- **`ledger_splitter`:** separa events de rewards de PnL de trading.
- **`telemetry/alerts`:** coleta métricas objetivas (ver seção 5).

---

## 3) Mudanças por arquivo em `polymarket-mm/` (plano)

> Observação: repo atual da sessão é pesquisa/planejamento; esta seção descreve **paths-alvo esperados** no repositório `polymarket-mm/`.

### 3.1 Configuração e flags
- `polymarket-mm/config/default.yaml`
  - novos campos:
    - `mode.rewards_mode: false` (default)
    - `mode.allow_live: false` (default)
    - `mode.dry_run: true` (default)
  - `mode.rewards_mode` vira explicitamente “mutuamente exclusivo” com `mode.aggressive_spread: true`.

- `polymarket-mm/config/strategies/pmm.yaml`
  - adicionar bloco:
    - `rewards.mode_enabled`
    - `rewards.target_book_time_sec`
    - `rewards.min_book_time_compliance`
    - `rewards.rewardsMinSize`
    - `rewards.rewardsMaxSpread`
    - `rewards.max_quote_retries_per_min`
    - `rewards.guard.rejection_burn_limit`

- `polymarket-mm/src/config/feature_flags.py` (ou equivalente)
  - parser/validator desses campos + validações de dependência (ex.: `allow_live=false` bloqueia submit real).

### 3.2 Seleção de mercados e seleção por Gamma/CLOB
- `polymarket-mm/src/runner/market_selector.py`
  - integrar fonte Gamma e snapshot CLOB;
  - manter cache de `universe_snapshot` e janela de validade TTL.
- `polymarket-mm/src/runner/sources/gamma_client.py`
  - client dedicado para filtros de volume, liquidez esperada, event end time e histórico de rewards.
- `polymarket-mm/src/runner/sources/clob_client.py`
  - coleta de top-of-book e depth (N níveis para estimação de slippage/fill friction).
- `polymarket-mm/src/runner/rewards_selector.py`
  - pipeline de elegibilidade: `rewardsMinSize`, `rewardsMaxSpread`, score de continuidade e compatibilidade de livro.

### 3.3 Engine de execução (rewards-first)
- `polymarket-mm/src/runner/policies/rewards_policy.py`
  - política de quote por tempo no livro, com menor churn.
- `polymarket-mm/src/runner/policies/order_refresh.py`
  - refresh por janela de maturidade (não agressivo), não por impulso de fill.
- `polymarket-mm/src/runner/execution/clob_quote_adapter.py`
  - separar ordem rewards (sem alterar agressividade de spread).
- `polymarket-mm/src/runner/execution/live_guard.py`
  - bloqueio hard de execução real quando `mode.allow_live=false`.

### 3.4 Tracking (separação rewards × trading PnL)
- `polymarket-mm/src/runner/ledger/rewards_ledger.py`
  - escreve `reward_events` e `reward_eligibility_checks`.
- `polymarket-mm/src/runner/pnl/trading_pnl_ledger.py`
  - continua registrando PnL de trades somente.
- `polymarket-mm/src/runner/runner.py`
  - injeta ambas camadas de ledger e garante idempotência por `run_id` + `market_id` + `time_bucket`.

### 3.5 Operação e segurança
- `polymarket-mm/scripts/run_pmm.py`
  - adicionar modo de operação: `--rewards-mode`, `--simulate`, `--allow-live`.
- `polymarket-mm/scripts/supervisor_health.sh`
  - check automático de kill-switch (ver seção 8).

---

## 4) Auto-seleção de mercados por `rewardsMinSize` + `rewardsMaxSpread`

### 4.1 Entrada de dados
- **Gamma API:** métricas de mercado, status de rewards, histórico de volume e estado de evento.
- **CLOB:** bid/ask atual, tamanho do nível 1 e 2, profundidade acumulada, latência e rejeições recentes.

### 4.2 Filtros hard
1. `rewardsMinSize <= market.reward_minimum_size_usdc <= rewardsMaxSize` (se existir teto de mercado)
2. `spread_bps <= rewardsMaxSpread`
3. `market_status == ACTIVE && !near_resolve(<=72h)`
4. `clob_top_depth_usdc >= k * rewardsMinSize` (k inicial=8)
5. `api_freshness < 12s`, `reject_rate_1h < rewards_reject_max`
6. `time_to_resolution` >= mínimo operacional por tier de risco.

### 4.3 Score de elegibilidade (exemplo)
`reward_raw_score = min(1, reward_rate_usdc_24h / target_reward_usdc_day)`  
`book_score = sqrt(min(1, top2_depth / (8 * rewardsMinSize))) * min(1, p99_fill_latency_sec / p95_latency_sec)`  
`risk_score = 1 - clamp((reject_rate_h - 0.05), 0, 1)`  
`eligibility = 100 * reward_raw_score * book_score * risk_score * governance_score`

### 4.4 Política de seleção
- Ordenar por `eligibility desc`.
- Selecionar top-N por `capital_total_operacional` com:
  - N=1 para capital inicial,
  - expansão gradual por milestones (seção 7).
- Reavaliar universo a cada `selection_refresh_min` (padrão 5–15m).

---

## 5) Supabase: schema mínimo para separação de métricas (rewards vs PnL)

### 5.1 Tabelas novas

#### `pmm_rewards_market_metrics`
- `id uuid pk`
- `run_id text`
- `market_id text`
- `event_id text`
- `snapshot_at timestamptz`
- `rewards_min_size numeric`
- `rewards_max_spread_bps numeric`
- `reward_rate_estimated_usdc_day numeric`
- `book_time_secs numeric`
- `book_time_target_secs numeric`
- `selection_score numeric`
- `selected boolean`
- `selection_reason text[]`
- `metadata jsonb`
- `created_at timestamptz`

#### `pmm_rewards_eligibility_checks`
- `id uuid pk`
- `run_id text`
- `market_id text`
- `check_at timestamptz`
- `check_source text` (`gamma`,`clob`)
- `check_key text`
- `check_name text` (`rewardsMinSize`,`rewardsMaxSpread`,`spread`,`depth`,`liquidity`,`rejects`)
- `value numeric`
- `passed boolean`
- `raw_payload jsonb`

#### `pmm_execution_summary`
- `id uuid pk`
- `run_id text`
- `market_id text`
- `mode text` (`rewards`/`normal`)
- `orders_submitted int`
- `orders_rejected int`
- `book_time_secs numeric`
- `book_time_compliance numeric`
- `rejection_rate numeric`
- `avg_fill_latency_ms int`
- `inserted_at timestamptz`

#### `pmm_rewards_ledger`
- `id uuid pk`
- `run_id text`
- `market_id text`
- `ts timestamptz`
- `reward_usdc numeric`
- `reward_currency text`
- `source text`
- `tx_id text`
- `claim_status text` (`claimed`,`pending`,`failed`)
- `claim_error text`

#### `pmm_trading_pnl_ledger` *(mantida separada)*
- `id uuid pk`
- `run_id text`
- `market_id text`
- `ts timestamptz`
- `realized_pnl_usdc numeric`
- `unrealized_pnl_usdc numeric`
- `fees_paid_usdc numeric`
- `position_notional_usdc numeric`
- `drawdown_bps numeric`

### 5.2 Consultas de KPI (exemplos)
- **`rewards_day`**
  - `sum(reward_usdc) where date_trunc('day', ts)=...`
- **`rewards_per_deployed_usd`**
  - `sum(reward_usdc)/avg(market_cap_deployed_usd)`
- **`book_time_compliance`**
  - `avg(book_time_compliance)` de `pmm_execution_summary`
- **`rejection_rate`**
  - `rejected / nullif(orders_submitted,0)`
- **`rewards_vs_pnl`**
  - `sum(reward_usdc) - sum(realized_pnl_usdc)` por mercado/runner (para governance de custo)

---

## 6) Estratégia de testes e verificações

### 6.1 Estratégia de testes (sem código, para implementação posterior)
- **Unit tests**
  - selector: filtros `rewardsMaxSpread`/`rewardsMinSize`, TTL de cache, fallback Gamma→cache.
  - policy: `book_time_policy` e throttling de repost.
  - guardrails: rejeição de live sem override.
  - split ledger: evento de rewards não altera PnL e vice-versa.
- **Integração**
  - paper run end-to-end com mocks de Gamma+CLOB com 3 mercados elegíveis e 1 in elegível.
  - rejeição de requisições live em ambiente default.
  - validação de schema via insert SQL em banco de testes.
- **Smoke / operação**
  - execução curta 15m em paper com mudança de parâmetros por feature flag.

### 6.2 Comandos de verificação (quando implementado)
- `pytest tests/unit/pmm/test_rewards_mode.py -q`
- `pytest tests/unit/pmm/test_market_selector.py -q`
- `pytest tests/integration/test_rewards_pipeline.py::test_rewards_mode_no_live -q`
- `pytest tests/integration/test_rewards_pipeline.py::test_rewards_metrics_materialization -q`
- `python -m pmm.cli run --mode paper --config config/default.yaml --rewards-mode true --dry-run`
- `python -m pmm.cli run --mode paper --config config/default.yaml --market-refresh 300 --once`
- `python -m pmm.cli schema-check --migrations docs/db/migrations`

---

## 7) Roadmap de Scale-up ($500 → $10k)

### 7.1 Premissas comuns
- Início **só com paper**.
- Não operar live enquanto qualquer kill-switch ativo.
- Cada fase precisa cumprir gate técnico e risco por 2 janelas consecutivas.

### Fase 0 — Preparação (até US$500)
- **Escopo:** 1 mercado piloto.
- **Meta:** comprovar estabilidade de coleta, seleção e escrita split ledger.
- **Gates:**
  - `book_time_compliance >= 70%` nos primeiros 72h;
  - `rejection_rate <= 12%`;
  - 100% das rejeições registradas com razão estruturada.
- **Kill-switches:**
  - se `book_time_compliance < 50%` por 3janelas -> pausar mercado e entrar em rewards-only fallback;
  - se `reject_rate > 20%` por 30m -> reduzir churn 30%.

### Fase 1 — US$500
- **Alocação:** 1 mercado B (ou 2 C), `capital_market_usd = 300..500`.
- **Objetivo:** validar reward capture contínua.
- **Gate semanal:**
  - `rewards/day >= 30%` do alvo de benchmark interno;
  - `rewards_per_deployed_usd > 0.8%` anualizado; 
  - `book_time_compliance >= 60%`.

### Fase 2 — US$1.5k–$3k
- **Alocação:** 1–2 mercados (mix B/C) com `max_cash_locked_per_market <= 65%`.
- **Objetivo:** estabilidade multi-market sem redução de conformidade.
- **Gate quinzenal:**
  - `rewards/day` estável em 2 janelas;
  - `rejection_rate <= 12%`;
  - `reward_skew_per_market` dentro de limites definidos pelo risk budget.
- **Condicional de subida:**
  - downgrade automático para fase anterior em falha de gate.

### Fase 3 — US$3k–$7k
- **Alocação:** 2–3 mercados, introduzir ao menos um Tier A de liquidez.
- **Objetivo:** provar capital efficiency por unidade e governança.
- **Gate de 30 dias:**
  - `rewards_per_deployed_usd` em manutenção >= benchmark (ou +20% do target de 1k?)
  - `book_time_compliance >= 65%`
  - sem incidentes de segurança (execução live não autorizada = blocker).

### Fase 4 — US$7k–$10k
- **Alocação:** 3–4 mercados, limite por mercado por padrão: B: 35%, C: 30%, A: 35%.
- **Objetivo:** operar rewards mode como estado padrão em janela monitorada.
- **Gate de maturidade:**
  - `rewards/day` positivo em 14 dias;
  - `rejection_rate <= 10%`;
  - `book_time_compliance >= 70%`;
  - `kill_switches` sem disparo não justificado por terceiros.

### 7.2 Kill-switches globais
- **Soft stop:** reduzir quote frequency e tamanhos ao cair de compliance.
- **Hard stop:** desligar novos pedidos e manter apenas observação quando:
  - 3+ rejeições críticas consecutivas;
  - indisponibilidade API > 5min;
  - execução real detectada sem override ativo;
  - gap de dados > 5% nas janelas 1m.

---

## 8) Prevenção de execução live (gating obrigatório)

### 8.1 Guardrails de código/infra
- `mode.allow_live=false` padrão em todos ambientes não explicitamente “prod_live_approved”.
- `trading_mode` deve ser `paper` por padrão.
- `dry_run` obrigatoriamente `true` quando `rewards_mode=true` até aprovação formal.
- Execução real exige **todos** estes artefatos:
  1. `ALLOW_LIVE_TRADES=1`;
  2. `MANUAL_APPROVAL_REF` assinado (ou equivalente operacional);
  3. janela de tempo de manutenção de key ativa (ex.: TTL 4h);
  4. validação humana no dashboard.
- Logs/audit com evento `live_guard_blocked` para qualquer tentativa rejeitada.

### 8.2 Guardrails de processo
- PR review checklist exige verificação de flags antes de merge.
- Script de pré-start bloqueia se `rewards_mode=true` && `allow_live=false` + qualquer parâmetro de ação real for enviado.
- Rollback plan automático para modo paper em 60s com kill switch global.

---

## 9) Entrega e aceitação

### Entregável final
- Criar apenas este documento: `docs/pmm-rewards-optimized-plan.md`.

### Critérios (como o documento atende)
- ✅ Explica claramente mudança de objetivo do modo rewards (tempo no book + compliance de thresholds vs fill/PnL).
- ✅ Métricas objetivas: `rewards/day`, `rewards_per_deployed_usd`, `book_time_compliance`, `rejection_rate`.
- ✅ Seleção por dados com Gamma + CLOB depth e score explícito.
- ✅ Prevenção de execução live por default via flags e kill-switches.

---

**Status final:** `plan_submitted`
