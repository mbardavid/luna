# Review técnico — setup/arquitetura do “Polymarket Edge Model v4” (tweet @dunik_7)

Fonte: https://x.com/dunik_7/status/2028776579183116652 (conteúdo acessado via `r.jina.ai` porque X/Cloudflare frequentemente bloqueia scraping).

## 0) Resumo do que o tweet descreve (o “modelo”)
O post afirma que um fundo não tenta prever o *resultado* do evento, e sim o **pânico/fechamento forçado** de outros traders após movimentos bruscos (“liquidity vacuum trade”).

**Gatilho de entrada (3 condições simultâneas):**
1) Preço move **> 2% em 90s**
2) Volume **> 3× média**
3) **≥ 70%** das ordens “de um lado” (desequilíbrio)

**Direção:** operar **contra a multidão** (contrarian).

**Preço “justo” esperado:**
- `expected_price = EMA(200) × (1 + sentiment_offset)`

**Entrada:**
- se `deviation > 12%`

**Saída / risco:**
- stop em **25%**
- take profit em **EMA ± 3%**

**Métricas alegadas:** win-rate 73.2% em 1.847 trades, Sharpe 2.7.

> Observação: isso parece mais um “playbook” de momentum reversal/mean reversion em eventos de stress do que algo específico de Polymarket. A especificidade Polymarket entra na microestrutura (CLOB, spreads, liquidez, ticks) e em custos/execução.

---

## 1) O que faz sentido (partes “boas”)

### 1.1 Ideia de modelar comportamento (pânico) em vez do outcome
Isso **faz sentido** em mercados de apostas: muita gente opera com pouco sizing/convicção, fecha rápido, e o book pode ficar “vazio” em movimentos rápidos.

No nosso contexto (PMM + research de rewards-farming), a leitura útil aqui é:
- há edge potencial em **microestrutura** e em **eventos de liquidez**;
- “alpha” pode vir de **capturar spread e reversões** quando o fluxo fica unilateral.

### 1.2 Gatilhos com preço + volume + desequilíbrio
Os 3 sinais (retorno curto, volume anômalo e imbalance) são uma forma razoável de evitar operar toda oscilação pequena.

**Mas** a forma exata (2%/90s, 3×, 70%) é hiper-sensível ao regime e ao mercado.

### 1.3 Stop/take explícitos
Ter regras explícitas é bom. Melhor ainda se forem conectadas a:
- liquidez disponível,
- profundidade do book,
- custo de atravessar spread,
- e risco de evento (resolução/settlement, news).

---

## 2) O que é frágil / “red flags” (o que eu não adotaria do jeito que está)

### 2.1 Métricas sem auditoria (provável overfitting / selection bias)
Win-rate e Sharpe “de tweet” não significam muito sem:
- custos (fees, rebates, maker/taker, gas se houver),
- slippage (principalmente em *liquidity vacuum*),
- amostragem por mercado/regime,
- impacto de *partial fills* e cancel/replace,
- e *survivorship*: quais mercados foram excluídos?

Se o setup realmente “opera contra a multidão” em pânico, **o pior momento para entrar maker é quando o spread explode** e você vira o “último comprador/vendedor” antes do price discovery continuar.

### 2.2 EMA(200) como “fair value” em markets binários
Em Polymarket (tokens Yes/No com payoff 0/1), preço é probabilidade implícita + ruído.
- EMA pode ser um baseline ok para *micro mean reversion*,
- mas EMA(200) sem contexto (timeframe? trades? midpoints?) é arbitrário.

Além disso, o “sentiment_offset” não está definido. Isso é o tipo de variável que vira um **buraco de overfitting**.

### 2.3 “Deviation > 12%” é enorme em probabilidade
Num binário, 12 pontos percentuais de desvio pode ser:
- oportunidade real (informação nova), ou
- ruído/baixa liquidez.

Sem um filtro de “news/market state”, você corre o risco de comprar contra movimento que é **informacional** (não pânico).

### 2.4 Stop 25%: em binários, isso pode ser fatal ou inútil
- Se você compra Yes a 0.60 e stopa a 0.45, isso pode ser simplesmente o mercado reprecificando por notícia.
- Em movimentos rápidos, seu stop vira taker com slippage (ou nem executa).

### 2.5 “70%+ of orders on one side” é mal definido
É:
- contagem de ordens?
- soma de sizes?
- apenas top-of-book (L1) ou profundidade (L10/L50)?

Sem definição, o sinal é fácil de “quebrar” e fácil de manipular com spoofing/cancelamentos.

### 2.6 Execução (o ponto que mais mata)
O tweet não fala de:
- maker vs taker,
- latência,
- cancel/repost agressivo,
- proteção contra *self-trade* e *order churn*,
- e principalmente **reconciliation** (ordens em aberto vs fills vs estado do bot).

No nosso stack atual, isso é exatamente onde já vimos que as coisas quebram: sem reconciliação e logs estruturados, a estratégia pode “dar certo” no papel e falhar em prod.

---

## 3) Como isso conversa com nosso contexto (PMM prod + rewards-farming + Tor/geoblock)

### 3.1 Compatibilidade com PMM
Esse setup é mais **prop trading/event-driven** do que PMM clássico.
- PMM quer capturar spread e rebates, com inventário controlado.
- “liquidity vacuum contrarian” tende a concentrar risco em poucos eventos e exigir **taker** em momentos críticos.

Se formos usar algo disso, eu trataria como um **módulo complementar**:
- PMM roda sempre;
- módulo “vacuum” só aciona em regimes específicos e com limites muito claros.

### 3.2 Rewards-farming
Rewards geralmente favorecem:
- maker volume,
- presença constante no book,
- baixa exposição direcional.

Esse setup pode até aumentar fills, mas provavelmente:
- aumenta adverse selection,
- aumenta volatilidade de inventário,
- e piora PnL ajustado a risco.

### 3.3 Tor / geoblock
O tweet não aborda operação sob proxies/instabilidade.
Para nós:
- qualquer componente que dependa de timing (90s) precisa lidar com falhas do Tor, timeouts e gaps de dados;
- isso exige **kill-switch** por degradação de conectividade (ex.: “sem book/sem midpoint por X segundos → flatten/cancel”).

---

## 4) Recomendações objetivas (checklist)

### A) Definições & dados (antes de codar)
- [ ] Definir timeframe exato: 90s baseado em **midpoint**? last trade? VWAP?
- [ ] Definir “volume”: base em trades executados, ou soma de mudanças no book?
- [ ] Definir “imbalance 70%”: por **size** e por profundidade (ex. top 10 níveis) — não por contagem.
- [ ] Normalizar limiares por mercado: 2%/90s e 12% devem virar **z-scores** por volatilidade/liq.
- [ ] Separar movimentos **informacionais** vs **microestrutura** (heurística: salto de preço + persistência + ausência de reversion + news/mercado relacionado).

### B) Execução & microestrutura (essencial)
- [ ] Decidir maker/taker por regime: em vacuum, maker pode não fill; taker paga spread.
- [ ] Estimar slippage com profundidade real: usar `/book?token_id=...` e simular marketable limit.
- [ ] Limitar churn: mínimo intervalo entre cancel/replace; *cooldown* pós-entrada.
- [ ] Proteção de inventário: cap por mercado e global; auto-hedge (Yes/No) se aplicável.
- [ ] Latência: medir “data-to-order” e “order-to-fill”; logar percentis.

### C) Risco, kill-switch e reconciliação (não negociável)
- [ ] Kill-switch por: perda diária, drawdown, falha Tor, erro de API, mismatch de posições.
- [ ] Reconciliação periódica: estado local vs CLOB (open orders, balances, fills) + correção automática.
- [ ] Logging estruturado: cada decisão com features (ret90s, vol_ratio, imbalance, deviation, spread, depth) + resultado (fill/slip/pnl).
- [ ] Replay determinístico: ability de reprocessar logs e reproduzir sinais.

### D) Métricas corretas (não cair no win-rate)
- [ ] PnL líquido (fees + slippage) por trade e por mercado.
- [ ] Adverse selection: PnL vs move posterior (ex. 30s/5m após fill).
- [ ] Hit rate condicional por regime de spread e profundidade.
- [ ] Impacto em rewards (maker volume qualificado) vs risco direcional.

---

## 5) Plano de validação/experimentos (prático, no nosso ambiente)

### Experimento 1 — Coleta de features em tempo real (read-only)
Objetivo: medir quantas vezes os gatilhos acontecem e em quais mercados.
- Fonte: CLOB endpoints (`/midpoint`, `/spread`, `/book`) via Tor.
- Output: dataset (parquet/csv) com snapshots a cada 1–2s em 10–30 mercados.
- KPIs: frequência de “>2%/90s”, distribuição de spreads/depth durante eventos.

**Critério de sucesso:** detectar eventos suficientes (N>200) e ver se há reversão estatística (ex.: retorno 5m pós-choque).

### Experimento 2 — Backtest com execução simulada realista
Objetivo: testar se “contrarian” funciona *após custos*.
- Simular ordens com:
  - crossing do spread (taker) e/ou maker fill probabilístico baseado em queue.
  - slippage usando profundidade real do `/book`.

**A/B:**
- baseline: “não operar”
- strategy A: contrarian taker
- strategy B: contrarian maker com TTL curto

**Critério de sucesso:** PnL líquido positivo + drawdown controlado, e edge concentrado em poucos regimes/mercados identificáveis.

### Experimento 3 — Paper trading com kill-switch + reconciliação ativa
Objetivo: validar plumbing (o que costuma quebrar em prod).
- Rodar em paper/size mínimo com:
  - reconciliação a cada 30–60s,
  - logs de decisão + fills,
  - falha induzida de Tor (simular downtime) para testar kill-switch.

**Critério de sucesso:** zero “posições fantasmas”, zero loop de cancel spam, e flatten seguro em falhas.

### Experimento 4 — Integração com PMM / rewards
Objetivo: ver se o módulo “vacuum” melhora ou piora o sistema.
- Rodar PMM sozinho vs PMM+vacuum em mercados iguais.
- Medir:
  - rewards qualificado,
  - adverse selection,
  - estabilidade de inventário,
  - PnL líquido.

**Critério de sucesso:** melhoria clara em pelo menos 2 dimensões sem piorar risco sistêmico.

---

## 6) Opinião franca (TL;DR)
- **Ideia macro (operar microestrutura/pânico) é plausível**, e é consistente com onde existe edge em Polymarket.
- **Os números/limiares do tweet não são confiáveis** e provavelmente não transferem sem recalibração por mercado/regime.
- **O maior risco é execução**: em “liquidity vacuum”, você paga caro para entrar/sair; sem simulação de slippage e reconciliação, a estratégia engana.
- Eu **não adotaria** “EMA(200) + deviation 12% + stop 25%” como está; eu adotaria o *framework* de detecção de stress + playbook de execução/risk.

Se quisermos extrair valor disso, o caminho é: **instrumentar, medir, simular execução realista, paper com kill-switch e só então small-prod**.
