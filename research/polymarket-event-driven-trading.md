# Polymarket — Estratégia Event-Driven Direcional (v0.2, operacional)

**Objetivo:** gerar edge direcional explorando mispricing em mercados de baixa cobertura (macro/Fed-BCB, livros de esporte cross-ref, política nichada) sem operar market making.

**Escopo:** design + pseudocódigo + interfaces. Sem ordens on-chain.

---

## 1) Tese de negócio

1. Mercados binários no Polymarket tendem a ficar mal precificados quando:
   - Há **nova informação parcial** (boletins, notícias, decisões) e o livro ainda não ajustou.
   - O preço derivado da linha externa (sportsbook / consenso macro / notícia com confirmação institucional) diverge do preço de CLOB.
   - O fluxo de liquidez estável reduz a urgência de correção, preservando window de execução.

2. Estratégia proposta: **comprar lado direcional com edge > custo implícito** e usar tamanho fracionado + risco por carteira + cooldowns rígidos.

3. Não é arbitragem perfeita; é **aposta dirigida** com controles para evitar repetição de erros do PMM/MM:
   - não operar mercados estressados, sem liquidez ou com risco de execução ruim;
   - nunca usar capital operacional completo em um único trade;
   - bloquear operação em eventos de baixa confiança.

---

## 2) Fontes de dados (mínimo necessário)

### 2.1 Mercado Polymarket (estado e execução)
- Gamma API: `gamma-api.polymarket.com` (metadados, status ativo/fechado, preço de book).
- REST CLOB: melhor preço (`best_bid`, `best_ask`), lote mínimo, spread, volume recente.
- Evento / mercado: `market_id`, `condition_id`, `token_id_yes`, `token_id_no`, `close_time`, categoria.

### 2.2 Esportes (cross-ref)
- Odds feed com normalização para prob implícita:
  - `p_yes = 1 / odds_decimal_yes` (com remoção de vig)
  - manter por-mercado pelo menos 2 casas quando possível.
- Regras: cobertura mínima de books e limite de dispersão entre books.

### 2.3 Macro / Fed / BCB
- Calendários oficiais (datas de decisão, releases econômicos) + histórico de surpresa.
- Feeds de consenso (analyst expectation e resultado realizado).
- Derivar `surprise_z = (realized - forecast)/std_dev` e transformar em prior de probabilidade.

### 2.4 Política nichada
- Feeds de notícias setoriais (jornal/release oficial + fonte secundária confiável).
- Só aceitar quando houver evidência objetiva (documento / pronunciamento / comunicado), não só manchete social.
- LLM/AI apenas para resumo + consistência sem substituir dados primários.

### 2.5 Guardrails de integridade de dados
- Validação `price_yes + price_no == 1.0` (com tolerância micro).
- Timestamp monotônico por feed.
- Rejeitar fontes com staleness > `max_data_age_sec` por categoria.

---

## 3) Pipeline (Ingest → Score → Sizing → Execution)

### 3.1 Ingest
- Coleta periódica (cada ciclo):
  1. Mercado base da Polymarket
  2. Odds esportivas (se categoria sports)
  3. Macro/política signals (se categoria alvo)
  4. Estado do portfólio atual (saldo, posições, exposição por mercado)

- Normalização para objeto interno canônico:
  - `market_id`, `category`, `mid_price`, `best_bid`, `best_ask`, `liquidity_usd`, `ttl_to_resolution_min`, `spread_bps`
  - sinais externos com `prob`, `confidence`, `source`, `captured_at`

### 3.2 Score
- Cálculo de **probabilidade modelo** por categoria:
  - `p_model = prior(category) * prior_w + news_w * f_news + sportsbook_w * f_sports`
  - `f_news` só entra se confiança e fonte comprovadas;
  - `f_sports` só entra se cobertura mínima e baixa dispersão entre books.

- Edge direcional:
  - lado YES: `edge = p_model_yes - ask_yes`
  - lado NO: `edge = (1 - p_model_yes) - ask_no`

- Score final:
  - `raw_score = |edge| * confidence * (1 - spread_bps_norm) * liquidity_guard`.
  - Trade válido só se `abs(edge) >= min_abs_edge` e `confidence >= min_conf`.

### 3.3 Sizing
- **Sizing fracionado de Kelly**:
  - `f_kelly = (p - q)/(q-1)` (binário com q=1/preco_exec)
  - usar `f = min(max_kelly_frac, frac_kelly * cfg.f_kelly_scale)`.
  - `stake = capital_usable * f`
- Regras de sizing:
  - `stake >= min_stake_usdc` e `<= max_stake_usdc`;
  - não alocar mais que `max_portfolio_risk_pct * capital_total` por evento.
  - reduzir tamanho por `cooldown` ou baixa liquidez.

### 3.4 Execution policy
1. Antes do `tick`: verificar kill-switch global e limites de risco.
2. Para cada candidato ordenado por score desc:
   - Se `passa filters` → gerar decisão (`BUY YES/BUY NO` no lado mais barato para o evento).
   - Enviar ordem LIMIT (não agressiva) no preço de entrada alvo (ex.: `ask_side - epsilon`) com TTL curto.
   - Ajuste de rebalance: se não preencher em `ttl_order_sec`, cancelar.
3. Pós-preenchimento:
   - registrar decisão, fill, custos e intenção de saída.
4. Fechamento:
   - target por edge revertida, stop técnico, risco intradiário, cooldown de evento.

---

## 4) Critérios de seleção de mercados

1. **Elegibilidade estrutural**
   - Mercado ativo, não resolvido, com `ttl_to_resolution_min >= min_ttl`.
   - Não em lista de bloqueio/circuit breaker.
   - Liquidez mínima acima de `min_liquidity_usd` e spread abaixo do teto.

2. **Especialização de nicho**
   - Macro/Fed/BCB: evitar mercados já com consenso consolidado (`confidence > 0.85` em 2+ fontes independentes sem surpresa recente).
   - Sports: exigir ≥2 livros e book dispersion baixa.
   - Política nichada: exigir evidência textual >=1 fonte oficial + 1 fonte secundária, sem rumor.

3. **Filtro operacional**
   - Evitar mercados com `open_interest_usd` muito alto e edge micro; evita crowding.
   - Excluir categorias não permitidas (crypto/alto ruído), conforme histórico de execução.

4. **Risco de execução**
   - Não operar se não houver profundidade para preencher stake sem slippage acima de `max_slippage_pct`.
   - Não operar no último *x* minutos antes do encerramento salvo `event_type = macro` com notícias já confirmadas.

---

## 5) Regras explícitas de risco, cooldown e não operar

### 5.1 Sizing/Ciclo de risco
- **Kelly fracionado:** `f_kelly_use = f_kelly * kelly_fraction` (pad: 0.12~0.35).
- **Exposição máxima por mercado:** `max_market_exposure_pct` (ex.: 12% capital).
- **Exposição líquida total:** `max_net_exposure_pct` (ex.: 35% capital).
- **Reserva operacional:** manter `operating_cash_buffer` (ex.: 15% capital) bloqueado para custos/recovery.

### 5.2 Cooldowns
- `global_entry_cooldown_sec` após falha de sinal (ex.: 30–120s).
- `market_cooldown_sec` após três rejeições consecutivas no mesmo mercado.
- `same_category_cooldown_sec` (sports/macro/política) para evitar sobreposição de sinais correlacionados.

### 5.3 Kill-switches (não operar)
- `kill_all_if`:
  - saldo livre < `min_liquid_usdc`;
  - `drawdown_1h > drawdown_limit`;
  - perda consecutiva > `max_consecutive_losses` (ex.: 2)
  - Latência de dados > `max_data_age_ms` ou erro de feed crítico.
- `market_halt_if`:
  - staleness de sinal > limite;
  - score<0 por 3 ciclos seguidos;
  - disparidade books > `dispersion_cap`.

### 5.4 Regras "NÃO OPERAR"
- Não operar quando:
  1. `confidence < min_confidence`.
  2. `abs(edge) < min_abs_edge` ou não cobre fees+slippage.
  3. Liquidez baixa / spread alto.
  4. Evento sem fonte primária no macro/político.
  5. Mercado com volatilidade exógena detectada (novas notícias sem direção clara).

---

## 6) Plano de logging em Supabase (compatível com runner existente)

### 6.1 Compatível imediatamente com o logger atual
Usar as mesmas tabelas do runner (`pmm_runs`, `pmm_orders`, `pmm_fills`, `pmm_exits`) para não quebrar dashboard/monitoring.

- **pmm_runs** (por ciclo de execução)
  - `run_id`, `market_type` (macro/sports/politics), `started_at`, `ended_at`, `status`, `total_fills`, `total_orders`, `realized_pnl_usd`, `params_json`.
- **pmm_orders** (ordens de decisão/evento)
  - `run_id`, `market_id`, `order_id`, `side`, `token_side`, `price`, `size`, `status`, `complement_routed`.
- **pmm_fills** (executado)
  - `run_id`, `market_id`, `trade_id`, `order_id`, `side`, `token_side`, `price`, `size`, `fee`, `fill_ts`, `source`.
- **pmm_exits** (encerramentos)
  - `run_id`, `market_id`, `token_side`, `entry_price`, `exit_price`, `quantity`, `pnl`, `reason`.

### 6.2 Campos extras recomendados no `pmm_runs.params_json` (sem alterar schema)
- `strategy` (`event_driven_v1`)
- `model_inputs` (resumo de odds/news/consenso)
- `selection_count`, `blocked_count`, `signal_count`
- `risk_state` (drawdown, exposure, kill_switch_reason)

### 6.3 Tabela complementar (opcional, futuro)
- `pmm_signal_audit` (novo, opcional) para auditoria de decisão pré-trade:
  - `run_id, market_id, category, p_market, p_model, edge, confidence, kelly_frac, stake_usdc, skip_reason`
- Não é bloqueante para MVP; pode ser adicionada depois sem alterar execução.

---

## 7) Métricas de controle (produção + pesquisa)

1. **Hit rate de decisão** (acerto pós-resolução por categoria).
2. **Edge capture**: `(pnl_real - edge_teórico)` e `expected_value_realized`.
3. **Taxa de preenchimento**: `fills/orders`.
4. **Turnover de capital / custo por ciclo** (fees + slippage).
5. **Max drawdown por run** e **time-to-recovery**.
6. **Latência de ingestão** e `staleness` por fonte.
7. **Concentração de risco**: `exposure_per_category`, `exposure_per_market`, `inventory_turnover`.
8. **Regras de não-operar**: % de ciclos onde “não trade por guardrail”.

---

## 8) Plano de backtest + paper trading

### 8.1 Backtest offline (mín. 3 meses)
- Janela rolante semanal/mensal.
- Replay de eventos com:
  - livros/histórico de preços (proxy de ordem)
  - outcomes conhecidos (settlement)
  - sinais externos (odds/macros/política) em timestamp real.
- Walk-forward:
  1. Treino `W-1` semana, validação semana atual.
  2. Congelar parâmetros por 7 dias.
  3. Recalibrar a cada janela.
- KPIs mínimos para avançar para paper: `hit_rate`, `pnl/volatility`, `max_dd < limiar`, `kill_switch_frequency < limiar`.

### 8.2 Paper trading
- Duração inicial: 1 ciclo de 72h com dry-run + caps baixos.
- Acompanhamento por categoria e mercado.
- Só migrar para volume real se 2 ciclos consecutivos no paper:
  - sem gatilho de kill-switch,
  - drawdown sob controle,
  - edge capturado positiva medianamente estável.

---

## 9) Interface de implementação (POC/arquitetura)

1. `MarketIngestor` → normaliza feeds e atualiza snapshot de mercado.
2. `SignalComposer` → calcula `p_model`, `confidence`, `edge`.
3. `RiskGovernor` → checa sizing, exposição, cooldowns, kill-switch.
4. `ExecutionAdapter` → gera ordem no runner sem mudar pipeline principal.
5. `LoggerBridge` → escreve decisões e fills em Supabase com schema atual.

Arquivos sugeridos:
- `research/polymarket-event-driven-trading.md` (este)
- `research/polymarket_event_driven_trading.py` (POC + interfaces)
- Config: `paper/runs` -> novo perfil de modo directional.

---

## 10) Próximos passos (prático)

1. Validar e mapear `news/sports/macro` para contratos reais via `scripts/discover_markets.py`.
2. Implementar POC em `research/polymarket_event_driven_trading.py` com testes de compilação.
3. Rodar backtest mínimo e registrar decisão de parâmetro no run de paper.
4. Ativar no runner com `supabase_logging=true` e `strategy=event_driven_v1` em ambiente paper.
5. Só depois de 2 ciclos paper favoráveis avaliar rollout parcial em live (valor de exposição menor).