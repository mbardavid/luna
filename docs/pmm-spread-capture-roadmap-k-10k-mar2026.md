# Roadmap Executivo — PMM Polymarket (Rewards/Paper -> Spread Capture Competitivo)

**Objetivo:** evoluir o bot de rewards/paper para captura de spread com edge mensurável conforme o capital sobe de **US$500 → 1K → 3K → 10K**.

**Escopo:** documento de estratégia (sem código). **Sem implementação** nesta etapa.

**Última atualização:** 2026-03-04

---

## 0) Princípios de decisão

A virada para spread capture só acontece quando houver **evidência operacional**, não só apetite de risco.

- Métrica de corte: `PnL líquido por ciclo` (fees, slippage implícito, rejections e rejeitos já descontados) > `PnL rewards de referência ajustado ao risco`.
- Tolerância de execução: sem “ganhar dinheiro” em papel, não vamos escalar capital real.
- Todo avanço de escala exige **duas janelas consecutivas** dentro dos gates de risco.

---

## 1) Hipóteses baseadas em dados reais observáveis

Dados reais disponíveis no momento para a operação atual:

- Spread alvo atual em mercados top observados: **~1 bps**.
- Ordem inicial operacional atual: **US$ 5–100 por nível de preço**.
- Observação de operação atual: tickets pequenos tendem a ficar muito atrás na fila em mercados competidos.
- Profissional de MM no mesmo ativo costuma operar com **US$ 200k+** (ordens de grandeza), logo fila/tempo de vida da ordem é o principal gargalo inicial.

A partir disso, definimos hipóteses de hipótese baseadas em execução real:

### 1.1 Hipóteses de viabilidade

1. **Friction Dominance Hypothesis (F1)**
   - Se `fill_rate < 1,8%` **e** `cost_per_fill_usdc > 0,45% do notional`, o bot não quebra custo operacional, independente do spread.

2. **Queue Reality Hypothesis (F2)**
   - Com tickets de 5–100, é necessário `lat_p95_fill < 1,5s` e `position_in_queue_estimate <= 4` (proxy de priorização) para haver chance de edge bruto positivo em Tier A/B.

3. **Asymmetry Guard Hypothesis (F3)**
   - Se `reject_rate > 6%` ou `cancel_rate > 22%` por janela de 24h, a liquidez efetiva despenca e o risco de drawdown por micro-viés de fila aumenta.

4. **Capital Efficiency Hypothesis (F4)**
   - Edge em spread não escala com capital; escala com **posição no livro** e **tolerância de fila**. Portanto precisamos reduzir custo por fill **antes** de aumentar lote.

### 1.2 Métricas mínimas de prova antes de subir nível

- `fill_rate_14d >= 2,5%` em 14 dias contínuos (após warmup de 48h).
- `net_fill_margin_bps = (spread_capture_bps - fee_bps - reject_drag_bps - latency_drag_bps - slippage_drag_bps)` com média >= **0,4 bps**.
- `lat_p95_fill <= 2,0s`, `lat_p99_fill <= 5,0s`.
- `queue_reject_pressure = reject_count / (submit_count + 1e-9) <= 5%` e `cancel_to_fill_ratio <= 4:1`.

---

## 2) Requisitos de capital mínimo por mercado e por nível de competição

### 2.1 Estrutura de competição do mercado

| Nível | Característica | Competição esperada | Condição de entrada | Capital mínimo por mercado* |
|---|---|---|---|---|
| **C (Baixa)** | baixa profundidade/frequência, evento niche | competição média | spread estável porém fila curta | **US$ 200–300** |
| **B (Média)** | liquidamente moderada | presença de market makers mas sem agressividade extrema | bom histórico local e pouca rejeição | **US$ 350–600** |
| **A (Alta)** | topo de livro forte, muita competição | MM profissionais ativos | livro profundo, mas alta pressão de fila | **US$ 900–1.500** |

**Observação:** abaixo desses valores o mercado fica travado por custo de execução.

\* inclui margem de segurança operacional de 10–20%; não é tamanho total a alocar, é necessidade mínima de caixa operacional por mercado.

### 2.2 Requisito por estágio de capital total

- **500 USDC**: apenas rewards/paper + coleta estruturada de fills/rejections/latência por piloto.
- **1K USDC**: transição técnica para shadow PMM em 1 mercado, sem carga full.
- **3K USDC**: modo piloto real com 1–2 mercados; concentração máxima de capital por mercado e risco.
- **10K USDC**: operação principal de spread capture com 2–4 mercados e alocação dinâmica.


### 2.3 Regra de alocação e limites por estágio

- **Máximo de alocação por mercado:** 25% do capital total em A, 35% em B, 45% em C.
- **Caixa mínimo global:** manter 10% de caixa não alocada.
- **Alavancagem de inventário por lado:** limiar de exposição líquida por lado >=4% do capital alocado ao mercado.

---

## 3) Critérios de seleção de mercados e sizing

### 3.1 Filtro de entrada (hard filters)

Entrar apenas em mercados que satisfaçam simultaneamente:

1. spread observável útil: `effective_spread_bps ∈ [0,8 ; 3,0]`.
2. liquidez top-2 suficiente para pelo menos **8x** o lote alvo (ticket). 
3. risco de fila: `queue_pressure` em janela 24h não acima do quantil 70 histórico local.
4. latência de mercado e API consistente (`lat_p95_api <= 1,8s`).
5. sem gatilho de eventos críticos sem proteção (calendário de notícia/evento definido).

### 3.2 Escore de ranking (sem subjetividade)

`Score_market = 100 * min(SpreadCapturavel, 1) * sqrt(FillRate) * (1 - FrictionIndex) * LiquidityScore`

- `SpreadCapturavel = min(1, effective_spread_bps / 2.5)`
- `FrictionIndex = (fees_bps + reject_drag_bps + latency_drag_bps + slippage_drag_bps) / 8`
- `LiquidityScore = min(1, depth_top2_usd / (ticket_alvo * 8))`

**Aprovado:** somente mercados no `top 40` por score em janela de 7d **e** score `>= 22`.

### 3.3 Sizing inicial e ajuste

- `ticket_inicial = max( min( capital_alocado_mercado * 1%, 5 ) , 5 )` (para piloto) com step de 5.
- Aumentar ticket a cada 3 dias com rule:
  - `ticket_next = ticket_now * (1 + 0,2)` se `PnL_3d > 0` e `cancel_to_fill <= 3:1`.
  - Caso contrário reduzir `ticket` em 30%.
- Evitar quote cruzado: manter inventário líquido por par dentro de >=4% do total alocado ao mercado.

---

## 4) Roadmap com milestones e gates (500 → 1k → 3k → 10k)

### Phase 0 — 500 (base de observação)

**Objetivo:** montar baseline real de execução e risco sem alocação principal.

- Ativar paper/pseudo-live em 3 mercados A/B com logging completo.
- Coletar por 14 dias: fills, rejections, cancelamentos, latência em endpoints, latência de fill, depth snapshot por minuto, inventário líquido.
- **Gates para sair da fase:**
  - no mínimo 2 mercados com `fill_rate_7d >= 2,0%`
  - `net_fill_margin_bps_7d > 0`
  - `p99_fill_latency <= 6s`
  - `reject+cancel <= 35%` (contador de tentativas)

Se falhar: manter apenas rewards + melhorar telemetria.

### Milestone 1 — 1K (go/no-go live inicial)

**Objetivo:** validar que spread capture vale capital real (ainda pequeno).

- Ativar **1 mercado** em modo real, com caixa e posição reduzida:
  - `capital_alocado_mercado = US$ 150–250` inicial.
  - 1 bid + 1 ask com tick mínimo.
- Janela de validação: 14 dias úteis.
- **Gates de avanço para 3K:**
  - `fill_rate_14d >= 2,5%`
  - `net_pnl_14d >= 0` (já descontando fees/rejections/latência)
  - `inventario_liquido_pct` dentro de `[-8%, +8%]` do capital de mercado
  - 2 janelas consecutivas sem incidentes críticos de API > 5 min

Falha no gate: voltar para modo paper + reduzir ticket 50% + revisão de fila.

### Milestone 2 — 3K (escala controlada)

**Objetivo:** provar repetibilidade em mais de um ativo e estabilidade de risco.

- 2 mercados (A+B) no máximo.
- Capital por mercado: 20–30% cada + 10% caixa.
- Ajustar política de reprice com intervalo mínimo e hysteresis de spread.
- Janela: 30 dias.

**Gates para avançar para 10K:**
- `PnL_liquido_30d >= 0` em pelo menos **3/4** das semanas.
- `drawdown_por_mercado <= 1,8%` do capital alocado ao mercado.
- `cancel_to_fill <= 2,5:1` e `reject_rate <= 5%`.
- `event_exposure`: em janela de notícia, inventário líquido retorna ao alvo em <= 3 ciclos de evento.

Se não bater: manter 1 mercado, re-tunar queue policy e latency guard.

### Milestone 3 — 10K (spread capture como modo principal)

**Objetivo:** operação principal com fallback robusto.

- 2–4 mercados, com alocação por score (regra de portfólio).
- Exposição por lado com limite automático e kill-switch por volatilidade/impacto.
- Habilitar automação de pausa por risco e fallback para rewards quando gate falha por 24h.

**Gates permanentes de manutenção (mensais):**
- `spread_capture_net_rate >= 0,6x reward_apr_usd_equiv` no período de 30 dias.
- `tempo_ativo_spread_capture >= 50%` do horário de operação sem risco excessivo.
- `max_drawdown_portfolio <= 3%`/mês.


---

## 5) Plano de instrumentação e observabilidade (ação mínima obrigatória)

### 5.1 Telemetria mínima (painel diário)

1. **Execução por ordem**
   - timestamp, side, market, market_id, order_id, size_usdc, price, ttl, status, cancel_reason.
2. **Fill accounting**
   - fill_id, size_usdc, price, latency_ms, queue_position_estimate, partial/complete.
3. **Erros e rejeições**
   - código de erro, texto, endpoint, origem, retry_count, tempo_atendimento.
4. **Risco em tempo real**
   - inventário líquido por mercado, exposição por lado, pnl unrealized/realized.
5. **Custos e eficiência**
   - fee total, rejeições, cancelamentos, retries, drag_estimado por fill.

### 5.2 Indicadores-alvo por mercado (dashboards)

- `fill_rate_1m`, `fill_rate_15m`, `fill_rate_1h`
- `avg_fill_latency_ms`, p95/p99
- `reject_rate`, `cancel_rate`, `repost_rate`
- `net_fill_margin_bps`
- `inventory_imbalance` e retorno à neutralidade pós-evento
- `pnl_realized`, `pnl_unrealized`, `pnl_net`
- `edge_vs_rewards_ratio`

### 5.3 Alertas e limites automáticos

- **Warn**: fill_rate 1h abaixo de 1,5% com latência em alta.
- **Warn**: `reject_rate 5m > 10%`.
- **Critical**: inventário líquido > limite de exposição por lado por 30m.
- **Kill-switch automático:**
  - p95 fill latency > 6s por 15m **OU**
  - drawdown diário > 2% **OU**
  - 2 erros críticos de API em sequência.

### 5.4 Como validar edge (método técnico)

- **Backtest de validação**: usar replay de livro e simulação com mesma lógica de tamanho/ordens.
- **Paper split**: 50/50 entre mercados com score alto e control, manter a mesma infraestrutura.
- **A/B de execução**:
  1. versão base (ticket atual) vs versão com redução de cancel/repost
  2. sem alteração de estratégia de seleção.
- **Regra de decisão:** rollout só após melhoria significativa de `net_fill_margin_bps` com IC 95% e ganho consistente por duas janelas.

---

## 6) Checklist de próximos experimentos e riscos (pronto para execução)

### Próximos experimentos (próximas 4 semanas)

- [ ] **EXP-01:** Coletar linha de base 14d com PMM paper em 3 mercados e persistir eventos por ordem.
- [ ] **EXP-02:** Medir custo por fill (fees+reject+repost+latência) por mercado e recalibrar score.
- [ ] **EXP-03:** Shadow test de queue-position vs tamanho (5/10/20/50/100) para achar breakeven de custo.
- [ ] **EXP-04:** Simular 500→1000→3000 com gates definidos para validar trigger de avanço.
- [ ] **EXP-05:** Implementar kill-switches de risco (inventário/evento/API) em modo paper primeiro.
- [ ] **EXP-06:** Rodar A/B de política de reprice: agressivo vs conservador em mercados Tier B.
- [ ] **EXP-07:** Comparar retorno spread vs retorno rewards (custo de oportunidade) em janela comum de 30d.
- [ ] **EXP-08:** Testar fallback automático para rewards quando `PnL_net < 0` por 2 dias.

### Principais riscos e gatilhos

- [ ] **Risco: fila profissional extrema** — gatilho: cancel/reject > 8% por 12h.
- [ ] **Risco: capital insuficiente de manutenção** — gatilho: caixa < 10% do capital.
- [ ] **Risco: latência** — gatilho: p95 de preenchimento > 6s.
- [ ] **Risco: inventário desbalanceado** — gatilho: exposição líquida > 1,5% por mercado fora de horário controlado.
- [ ] **Risco operacional/API** — gatilho: > 2 falhas de endpoint em 10m.
- [ ] **Risco de métricas ruins de recompensa** — gatilho: spread capture líquido <= rewards por 2 semanas consecutivas.

---

## 7) Resultado esperado em 10K

Quando o regime estiver saudável, o sistema deve operar com:

- spread capture líquido positivo mensalmente,
- exposição controlada por mercado,
- alocação dinâmica baseada em score,
- fallback automático para rewards em regime anômalo,
- trilha de evidência completa para revisão de cada avanço de capital.