# PMM Smoke Test Plan — Continuous Agentic Paper Trading

**Autor:** Luna  
**Data:** 2026-02-26  
**Status:** Proposta para aprovação  
**Objetivo:** Validar se a estratégia de market making é lucrativa em condições reais antes de ir pra produção

---

## 1. Visão Geral

O paper runner já existe e funciona: conecta ao WebSocket real do Polymarket, processa orderbooks live, gera quotes via pipeline completo (Feature Engine → Quote Engine → Inventory Skew), e simula fills no PaperVenue. Nenhuma ordem real é enviada.

**O que falta:** um loop contínuo e agentico que:
1. Roda paper trading por períodos controlados (4-8h)
2. Coleta e analisa resultados automaticamente
3. Ajusta parâmetros com base na análise
4. Spawna o próximo teste com parâmetros otimizados
5. Reporta progresso ao Matheus periodicamente

---

## 2. Hipóteses a Testar

### H1 — Spread Capture é Lucrativo Após Fees
- **Teste:** PnL líquido > 0 após simular fees (2% maker rebate no Polymarket)
- **Métrica:** PnL/hora > $0 consistentemente (>60% das horas)
- **Dados existentes:** 1h de teste mostrou +$57.22 (promissor, mas precisa validar se o PaperVenue não está sendo otimista demais com fills)

### H2 — Inventory Skew Controla Posição
- **Teste:** Net inventory (|YES - NO|) não ultrapassa MAX_POSITION_SIZE
- **Métrica:** Max net position < 500 em 95% do tempo
- **Sinal de alerta:** Rebalance loop ativando >5x/hora

### H3 — Quote Engine Adapta ao Regime de Volatilidade
- **Teste:** Spread widening automático em momentos de alta volatilidade
- **Métrica:** Correlação positiva entre volatility feature e spread aplicado
- **Teste de stress:** Chaos injector com spikes de volatilidade

### H4 — Kill Switch Funciona em Condições Extremas
- **Teste:** Kill switch ativa corretamente quando PnL < -MAX_DAILY_LOSS
- **Métrica:** Tempo de reação < 5s após trigger
- **Já validado:** 5/5 tests passaram (drawdown, heartbeat, data_gap, engine_restart, reconciliation)

### H5 — Multi-Market Não Degrada Single-Market
- **Teste:** PnL por mercado individual não piora quando operando 3+ mercados simultâneos
- **Métrica:** PnL/hora por mercado >= PnL single-market * 0.8

### H6 — Estratégia Sobrevive Overnight/Weekend
- **Teste:** Paper trading contínuo 24h+ sem crash, memory leak, ou degradação
- **Métrica:** Latência de quote cycle estável (não crescente), memória estável

### H7 — Fill Rate Realista
- **Teste:** Fill rate do PaperVenue está calibrado com taxas reais do Polymarket
- **Problema potencial:** Fill rate de 95.98% parece MUITO alto — produção provavelmente será 5-30%
- **Ação:** Ajustar PaperVenue para simular fill rates mais conservadores (10-30%)

---

## 3. Arquitetura do Loop Contínuo

```
                    ┌──────────────────────────────────────────┐
                    │          CRON: smoke-test-orchestrator    │
                    │          (a cada 30min)                   │
                    └──────────┬───────────────────────────────┘
                               │
                    ┌──────────▼───────────────────────────────┐
                    │  1. Checar se paper trading está rodando  │
                    │     - PID file? Processo vivo?            │
                    │     - Duração elapsed?                    │
                    └──────────┬───────────────────────────────┘
                               │
                ┌──────────────┼──────────────────┐
                │              │                  │
         ┌──────▼──────┐ ┌────▼─────┐  ┌─────────▼────────┐
         │  RUNNING     │ │ FINISHED │  │ NOT STARTED /    │
         │  → check     │ │ → analyze│  │ CRASHED          │
         │    health    │ │   results│  │ → start new run  │
         └──────┬──────┘ └────┬─────┘  └─────────┬────────┘
                │             │                   │
                │      ┌──────▼──────────────┐    │
                │      │ Cron one-shot → Luna │    │
                │      │ "Analise resultados  │    │
                │      │  de /paper/data/     │    │
                │      │  metrics_latest.json │    │
                │      │  e decida próximos   │    │
                │      │  parâmetros."        │    │
                │      └──────┬──────────────┘    │
                │             │                   │
                │      ┌──────▼──────────────┐    │
                │      │ Luna (sessão isolada)│    │
                │      │ 1. Lê métricas      │◄───┘
                │      │ 2. Gera relatório   │
                │      │ 3. Testa hipóteses   │
                │      │ 4. Ajusta params    │
                │      │ 5. Inicia novo run  │
                │      │ 6. Notifica Discord │
                │      └─────────────────────┘
                │
         ┌──────▼──────────────┐
         │ A cada 4h:          │
         │ Notifica Discord    │
         │ com status parcial  │
         └─────────────────────┘
```

### 3.1 Componentes

| Componente | Tipo | Responsabilidade |
|-----------|------|-----------------|
| `scripts/smoke-test-orchestrator.sh` | Cron (30min) | Detecta estado do paper trading, dispara análise/restart |
| `paper/paper_runner.py` | Python process | Executa paper trading com dados live |
| Luna (sessão isolada via cron) | Agent | Analisa resultados, ajusta parâmetros, inicia próximo run |
| Quant Strategist | Agent (spawn) | Consultor para decisões de parâmetros complexas |
| Luan | Agent (spawn) | Implementa mudanças de código necessárias |
| `paper/data/` | Storage | Métricas, relatórios, histórico de runs |

### 3.2 State Machine do Orchestrator

```
states:
  IDLE         → Nenhum teste rodando, nenhum resultado pendente
  RUNNING      → Paper trading ativo (PID file existe + processo vivo)
  ANALYZING    → Teste terminou, aguardando análise do agente
  ADJUSTING    → Agente decidindo parâmetros do próximo teste
  STARTING     → Iniciando novo paper trading run
  ERROR        → Crash ou problema detectado

transitions:
  IDLE       → STARTING    (orchestrator detecta que deve iniciar)
  STARTING   → RUNNING     (PID file criado)
  RUNNING    → ANALYZING   (duração atingida ou processo terminou)
  ANALYZING  → ADJUSTING   (Luna lê resultados)
  ADJUSTING  → STARTING    (Luna define novos params e inicia)
  RUNNING    → ERROR       (processo crashou)
  ERROR      → STARTING    (orchestrator re-inicia com params seguros)
  *          → IDLE        (human gate / pause)
```

---

## 4. Formato dos Testes

### 4.1 Test Run Config

Cada run é definido por um YAML salvo em `paper/runs/<run_id>.yaml`:

```yaml
run_id: "run-001"
started_at: "2026-02-26T17:00:00Z"
duration_hours: 4
hypothesis: "H1"  # Hipótese primária sendo testada
params:
  quote_interval_s: 2.0
  num_levels: 1
  default_order_size: 50
  default_half_spread_bps: 50
  gamma_risk_aversion: 0.3
  fill_probability_override: 0.20  # Simular fill rate mais realista
  markets:
    - "will-axiom-be-accused-of-insider-trading"
    - "us-strikes-iran-by-march-31-2026"
parent_run: null  # ou "run-000" se é iteração
changes_from_parent: null  # ou "Reduziu fill_probability de 0.5 para 0.2"
```

### 4.2 Test Report (gerado automaticamente)

Salvo em `paper/reports/<run_id>-report.md`:

```markdown
# Run run-001 — Report
## Hipótese: H1 (Spread Capture é Lucrativo)
## Resultado: ✅ PASS / ❌ FAIL / ⚠️ INCONCLUSIVE

### Métricas
- PnL total: $X.XX
- PnL/hora: $X.XX
- Horas lucrativas: X/Y (Z%)
- Fill rate: X%
- Max drawdown: $X.XX
- Avg spread captured: X bps
- Inventory max drift: X

### Parâmetros Usados
(yaml dump)

### Decisão do Agente
- Próximo teste: run-002
- Mudanças: [lista de ajustes]
- Razão: [explicação]
```

### 4.3 Histórico de Runs

`paper/runs/history.json` — log de todos os runs com resultado resumido:

```json
[
  {"run_id": "run-001", "hypothesis": "H1", "result": "PASS", "pnl_per_hour": 14.3, "duration_h": 4},
  {"run_id": "run-002", "hypothesis": "H7", "result": "FAIL", "pnl_per_hour": -2.1, "duration_h": 4}
]
```

---

## 5. Loop Agentico — Como Luna Decide o Próximo Teste

Quando um run termina, Luna (em sessão isolada) recebe um briefing:

```
Smoke test run-001 terminou. Resultados em paper/data/metrics_latest.json.

Instruções:
1. Leia metrics_latest.json + runs/run-001.yaml
2. Gere relatório em paper/reports/run-001-report.md
3. Avalie a hipótese testada (PASS/FAIL/INCONCLUSIVE)
4. Decida:
   a) Se PASS → avançar para próxima hipótese na sequência (H1→H2→...→H7)
   b) Se FAIL → ajustar parâmetros e re-testar mesma hipótese (max 3 tentativas)
   c) Se INCONCLUSIVE → aumentar duração (4h→8h→12h) e re-testar
5. Gere config do próximo run em paper/runs/run-002.yaml
6. Se precisar de mudança de CÓDIGO (não só params), spawne Luan com spec
7. Se precisar de análise estratégica complexa, spawne Quant Strategist
8. Inicie o próximo paper trading run
9. Notifique #general-luna com resumo do resultado e próximo passo
```

### 5.1 Sequência de Hipóteses

```
H7 (fill rate realista) → H1 (lucratividade) → H2 (inventory) → H3 (volatility) 
→ H5 (multi-market) → H6 (overnight) → H4 (kill switch — já validado)
```

H7 primeiro porque o fill rate de 95% é claramente irreal. Precisamos calibrar antes de testar lucratividade.

### 5.2 Critérios de Parada

- **Sucesso global:** Todas as hipóteses H1-H7 PASS → pronto pra P6 (human gate)
- **Falha irrecuperável:** H1 FAIL após 3 iterações com parâmetros variados → estratégia precisa de revisão fundamental
- **Budget de testes:** Max 20 runs ou 7 dias de teste contínuo antes de review humano

---

## 6. Dashboard Live — Acompanhamento Humano em Tempo Real

### 6.1 Abordagem

Dashboard web leve servido via Python HTTP (sem Grafana/Prometheus — muito pesado pro VPS de 3.8GB). O paper runner escreve dados continuamente e o dashboard lê e renderiza no browser.

**URL:** `http://142.93.87.36:8501/` (porta 8501, mesma rede do MC)

### 6.2 Dados gerados pelo Paper Runner

**a) Trade Log — `paper/data/trades.jsonl`** (append-only, 1 linha por operação)

Cada fill gera uma linha com TUDO que o humano precisa pra validar:

```json
{
  "timestamp": "2026-02-26T17:03:22Z",
  "run_id": "run-001",
  "trade_id": "abc123",
  "market_id": "will-axiom-be-accused-of-insider-trading",
  "market_description": "Will Axiom be accused of insider trading?",
  
  "side": "BUY",
  "token": "YES",
  "price": "0.41",
  "size": "50",
  "fill_qty": "50",
  "fill_price": "0.41",
  
  "pnl_this_trade": "0.23",
  "pnl_cumulative": "57.44",
  "pnl_realized": "42.10",
  "pnl_unrealized": "15.34",
  
  "entry_rationale": {
    "strategy": "spread_capture",
    "trigger": "Bid YES abaixo do fair value. Spread 3.2% > min 0.5%. Inventory skew favorável (net=-30, target=0).",
    "spread_model": {
      "optimal_half_spread_bps": 160,
      "volatility_regime": "low",
      "liquidity_score": 0.72
    },
    "inventory_skew": {
      "current_net": -30,
      "skew_applied_bps": -15,
      "direction": "Skewing BID mais agressivo pra reduzir posição NO"
    },
    "toxic_flow": {
      "detected": false,
      "zscore": 0.8,
      "action": "normal"
    },
    "rewards_farming": {
      "adjustment_bps": -5,
      "reason": "Tightening spread pra capturar rewards ($387/day neste mercado)"
    }
  },
  
  "market_context": {
    "mid_price": "0.415",
    "best_bid": "0.40",
    "best_ask": "0.43",
    "spread_bps": 300,
    "depth_bid_usd": 1200,
    "depth_ask_usd": 890,
    "volume_24h": 4300000,
    "time_to_resolution_days": 95
  },
  
  "feature_vector": {
    "momentum_20": 0.02,
    "volatility_60": 0.008,
    "order_flow_imbalance": -0.15,
    "book_imbalance": 0.34,
    "data_quality": 0.95
  },
  
  "position_after": {
    "qty_yes": 280,
    "qty_no": 250,
    "net": 30,
    "exposure_usd": 115.50,
    "avg_entry_yes": "0.39",
    "avg_entry_no": "0.58"
  },
  
  "timing": {
    "quote_to_fill_ms": 1200,
    "quote_age_ms": 3400,
    "ws_latency_ms": 45
  },

  "kill_switch_state": "RUNNING",
  "data_gap_seconds": 0.3
}
```

**b) Live State — `paper/data/live_state.json`** (overwritten a cada 5s)

Snapshot do estado atual do sistema inteiro:

```json
{
  "timestamp": "2026-02-26T17:03:25Z",
  "run_id": "run-001",
  "status": "RUNNING",
  "uptime_seconds": 3625,
  "duration_target_hours": 4,
  "progress_pct": 25.2,
  
  "pnl": {
    "cumulative": 57.44,
    "realized": 42.10,
    "unrealized": 15.34,
    "per_hour_avg": 14.36,
    "max_drawdown": -3.20,
    "sharpe_estimate": 2.1
  },
  
  "markets": {
    "will-axiom-be-accused-of-insider-trading": {
      "description": "Will Axiom be accused of insider trading?",
      "mid_price": 0.415,
      "spread_bps": 300,
      "our_bid": 0.40,
      "our_ask": 0.43,
      "position_net": 30,
      "pnl": 27.46,
      "fills_count": 145,
      "last_fill_ago_s": 12,
      "kill_switch": "RUNNING",
      "data_gap_s": 0.3
    }
  },
  
  "totals": {
    "quotes_generated": 13436,
    "orders_submitted": 13433,
    "fills": 12893,
    "fill_rate_pct": 95.98,
    "ws_messages": 47532,
    "errors": 0
  },
  
  "system": {
    "memory_mb": 120,
    "cpu_pct": 3.2,
    "ws_connected": true,
    "ws_reconnects": 0
  },
  
  "hypothesis_under_test": "H7",
  "run_config_path": "paper/runs/run-001.yaml"
}
```

### 6.3 Dashboard UI — Painéis

O dashboard HTML auto-atualiza a cada 5 segundos lendo `live_state.json` e `trades.jsonl`.

**Header Bar (sempre visível):**
- 🟢 Status (RUNNING/STOPPED/ERROR)
- 💰 PnL Cumulativo (cor: verde se +, vermelho se -)
- ⏱️ Uptime / Progress %
- 📊 Fill Rate
- 🛡️ Kill Switch State
- 🧪 Hipótese atual (H1-H7)

**Painel 1 — PnL Chart (time series)**
- Linha de PnL cumulativo ao longo do tempo
- Linha de PnL/hora (barras)
- Max drawdown marcado em vermelho
- Sharpe ratio estimado

**Painel 2 — Trade Log (tabela scrollável)**
- Últimas 50 operações
- Colunas: Timestamp | Market | Side | Token | Price | Size | PnL | Rationale (resumo)
- Clique numa trade → expande mostrando o `entry_rationale` completo
- Filtro por mercado

**Painel 3 — Mercados (cards por mercado)**
- Para cada mercado ativo:
  - Nome + mid price
  - Nosso bid/ask vs best bid/ask do mercado
  - Posição net (barra visual: -MAX ← 0 → +MAX)
  - PnL do mercado
  - Fills/hora
  - Data gap indicator (verde/amarelo/vermelho)

**Painel 4 — Strategy Internals (pra validação profunda)**
- Feature vector atual por mercado (momentum, volatility, imbalance)
- Spread model: half_spread aplicado vs teórico
- Inventory skew: quanto e em qual direção
- Toxic flow: z-score atual vs threshold
- Rewards farming: adjustment aplicado
- **Cada decisão de quote mostra a "árvore de decisão":** por que esse spread, por que esse skew, por que esse tamanho

**Painel 5 — System Health**
- Memória RSS
- CPU %
- WS connection status + latência
- Reconexões
- Erros por tipo

**Painel 6 — Run History**
- Tabela com todos os runs anteriores
- Run ID | Hipótese | Resultado | PnL/h | Duração | Fill Rate
- Link pro relatório completo de cada run

### 6.4 Info Adicional para Validação Humana

Além do PnL e rationale, esses dados ajudam o Matheus a validar se o sistema tá tomando decisões inteligentes:

| Dado | Por que é útil |
|------|---------------|
| **Quote vs Market** | Ver se nossos preços estão competitivos ou muito afastados |
| **Tempo quote→fill** | Quão rápido o mercado pega nossas quotes (rápido demais = spread tight demais) |
| **Inventory net ao longo do tempo** | Ver se o skew tá controlando ou se inventory tá fugindo |
| **Toxic flow events** | Quando o sistema detectou flow tóxico e quanto widened |
| **Volatility regime changes** | Como o sistema reagiu a mudanças de regime |
| **Reward capture rate** | Quanto de rewards tá sendo capturado vs teórico |
| **Decisões de "não quotar"** | Tão importantes quanto as de quotar — por que o sistema decidiu NÃO entrar |
| **Correlação entre mercados** | Se inventory em um mercado afeta quoting em outro |
| **Rebalance events** | Quando position reset aconteceu e por quê |
| **P&L decomposition** | Quanto veio de spread capture vs inventory gains vs rewards |

---

## 7. Implementação — O que Precisa ser Feito

### Fase A: Preparação (Luan)
1. **Ajustar PaperVenue** — adicionar `fill_probability_override` para simular fill rates realistas (10-30%)
2. **Trade logging** — cada fill escreve em `paper/data/trades.jsonl` com schema completo (rationale, context, features, timing)
3. **Live state writer** — overwrite `paper/data/live_state.json` a cada 5s com snapshot do sistema
4. **Criar `paper/runs/` directory** com schema de run config
5. **Criar `paper/reports/` directory** com geração automática de relatório markdown
6. **Criar `paper/runs/history.json`** — append-only log de runs
7. **Atualizar `paper_runner.py`** — aceitar run config YAML como input (não só CLI args)

### Fase B: Dashboard Live (Luan)
1. **`paper/dashboard/index.html`** — SPA com vanilla JS (sem frameworks pesados)
   - Fetch `live_state.json` + últimas N linhas de `trades.jsonl` via API
   - Auto-refresh 5s
   - 6 painéis: Header, PnL Chart, Trade Log, Markets, Strategy Internals, System Health
2. **`paper/dashboard/server.py`** — HTTP server leve (stdlib `http.server` ou aiohttp)
   - Serve static files + API endpoints
   - GET `/api/state` → live_state.json
   - GET `/api/trades?limit=50` → últimas N trades do JSONL
   - GET `/api/runs` → history.json
   - Porta 8501
3. **Systemd unit ou script** — manter dashboard rodando

### Fase C: Orchestrator (Luan)
1. **`scripts/smoke-test-orchestrator.sh`** — cron script que:
   - Checa PID file + processo vivo
   - Se terminado: cria cron one-shot pra Luna analisar
   - Se crashou: reinicia com params seguros (fallback config)
   - Se running > duration * 1.2: mata (stuck)
   - State file: `/tmp/.smoke-test-state.json`
2. **Crontab entry:** `*/30 * * * *`

### Fase D: Primeiro Run (Luna)
1. Criar `paper/runs/run-001.yaml` com config conservadora
2. Disparar paper trading com fill_probability=0.20
3. Monitorar via orchestrator

### Fase D: Loop Contínuo (automático)
1. Orchestrator detecta fim → acorda Luna
2. Luna analisa → gera report → cria próximo run config → inicia
3. Repete até todas hipóteses PASS ou budget esgotado

---

## 7. Envolvimento dos Agentes

| Agente | Quando é chamado | Tipo de task |
|--------|-----------------|-------------|
| **Luna** | A cada fim de run | Análise de resultados, decisão de próximos params, geração de report |
| **Luan** | Fase A+B (setup) + quando Luna decide que precisa mudar código | Implementação de código |
| **Quant Strategist** | Quando Luna identifica padrão anômalo nos dados | Análise estratégica, sugestão de parâmetros |
| **Matheus** | Quando todas hipóteses PASS (P6 gate) | Aprovação final pra produção |

---

## 8. Métricas de Sucesso (Critérios P5 → P6)

Para aprovar ida pra produção, TODOS devem ser verdade:

- [ ] PnL simulado positivo em >60% das horas (mínimo 4h contínuas)
- [ ] Fill rate calibrado realisticamente (10-30%, não 95%)
- [ ] Inventory drift controlado (<500 net em 95% do tempo)
- [ ] Kill switch ativou corretamente em todos os cenários de stress
- [ ] Zero crashes em run de 24h+
- [ ] Memória estável (sem leak)
- [ ] Quote latency < 500ms p99
- [ ] Max drawdown < $50 em qualquer run

---

## 9. Riscos e Mitigações

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| PaperVenue muito otimista | Alta | Falso positivo de lucratividade | Calibrar fill rate (H7) |
| WS disconnect durante run | Média | Dados incompletos | Kill switch data_gap + auto-reconnect |
| Memory leak em runs longos | Baixa | Crash após horas | Monitorar RSS, cap em 500MB |
| Parâmetros divergem pra valores extremos | Média | Testes inúteis | Bounds no ajuste automático |
| Loop infinito de re-testes | Baixa | Burn de recursos | Budget de 20 runs / 7 dias |

---

## 10. Timeline Estimada

| Fase | Duração | Responsável |
|------|---------|-------------|
| A (Prep code + trade logging) | 1 spawn Luan (~30min) | Luan |
| B (Dashboard live) | Incluído no spawn acima | Luan |
| C (Orchestrator) | Incluído no spawn acima | Luan |
| D (Primeiro run) | 4h de paper trading | Luna (automático) |
| E (Loop: H7→H1→H2→H3→H5→H6) | ~3-5 dias de testes contínuos | Luna + Orchestrator |
| P6 Gate | Quando Matheus aprovar | Matheus |
