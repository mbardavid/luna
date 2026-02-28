# Adversarial PaperVenue — Spec para Teste de Lucratividade Real

**Data:** 2026-02-27
**Contexto:** Run-006 mostrou +170%/dia com 0% drawdown — irrealista.
**Objetivo:** Tornar o PaperVenue adversarial o suficiente para que PnL positivo no paper = alta confiança de PnL positivo em produção.

---

## Problema Atual

O PaperVenue atual é "amigável demais":

| Comportamento | Paper (atual) | Produção real |
|---|---|---|
| Fill price | Preço exato da ordem | Pode ter slippage |
| Adverse selection | Nenhuma | Fills tendem a acontecer quando preço move contra você |
| Fees | Nenhuma | ~2% maker fee (ou rebate) |
| Fill timing | Síncrono (instantâneo) | Latência real + pode ser cancelado |
| Counterparty | Random (fill_probability) | Informados (smart money) |
| Mid movement pós-fill | Independente | Correlacionado (preço move contra) |

## O que Implementar

### 1. Adverse Selection Model (CRÍTICO)

**Conceito:** Na vida real, quando sua ordem de BUY é preenchida, frequentemente é porque o preço está caindo (alguém sabe algo que você não sabe). Isso é "adverse selection" — os fills que você RECEBE são os que o mercado NÃO queria.

**Implementação:**
```python
# Após um fill, mover o mid CONTRA a direção do fill
# BUY fill → mid desce (você comprou algo que está desvalorizando)
# SELL fill → mid sobe (você vendeu algo que está valorizando)

adverse_move = order.size * adverse_selection_bps / 10000
if side == BUY:
    new_mid = current_mid - adverse_move
else:
    new_mid = current_mid + adverse_move
```

**Parâmetro:** `adverse_selection_bps: int = 10` (default 10bps = 0.1% por fill)

Isso é o fator mais importante — é o que separa paper trading otimista de realista.

### 2. Fee Model

**Polymarket fees:**
- Maker: -2% (rebate — maker RECEBE)
- Taker: +2% (cobra)

No paper, somos sempre maker (GTC limit orders). Mas o fee model deve existir para:
- Calcular PnL líquido corretamente
- Permitir simular cenários onde somos taker (market orders de hedge)

**Implementação:**
```python
@dataclass
class FeeConfig:
    maker_fee_bps: int = -20  # -20bps = 0.2% rebate (Polymarket maker)
    taker_fee_bps: int = 20   # 20bps = 0.2% fee

# No fill:
fee = fill_notional * maker_fee_bps / 10000
net_pnl = gross_pnl + fee  # fee negativo = rebate = bom pra nós
```

### 3. Fill Probability Baseada em Distância do Mid

Atualmente: probabilidade uniforme (25% pra todas as ordens).
Realidade: ordens mais perto do mid preenchem mais, ordens longe preenchem menos.

**Implementação:**
```python
distance = abs(order.price - current_mid)
half_spread = default_half_spread

# Closer to mid = higher fill prob, further = lower
# Linear decay: prob = base_prob * (1 - distance/max_distance)
adjusted_prob = base_fill_probability * max(0, 1 - distance / (2 * half_spread))
```

### 4. Slippage Model (opcional, menor prioridade)

Em Polymarket o slippage é discreto (tick_size = 0.01). Para ordens grandes:
```python
# Orders > 10% do book depth sofrem slippage de 1 tick
if order.size > depth * 0.10:
    fill_price = order.price + tick_size  # BUY: paga mais
    # ou order.price - tick_size          # SELL: recebe menos
```

### 5. Mark-to-Market PnL Realista

O unrealized PnL deve refletir a diferença entre posição e mid ATUAL:
```python
unrealized = sum(
    (current_mid - avg_entry) * qty_yes +
    ((1 - current_mid) - avg_entry_no) * qty_no
    for each market
)
```

---

## Configuração do Run-007

```yaml
run_id: "run-007"
duration_hours: 8
hypothesis: "H1-adversarial"

initial_balance: 500

params:
  # Quote engine (mesmos do run-006)
  quote_interval_s: 2.0
  default_order_size: 25
  default_half_spread_bps: 50
  gamma_risk_aversion: 0.3

  # Adversarial params (NOVOS)
  fill_probability: 0.25
  adverse_selection_bps: 10
  maker_fee_bps: -20          # Polymarket maker rebate
  fill_distance_decay: true   # fill prob decreases with distance from mid

  # Kill switch
  kill_switch_max_drawdown_pct: 25.0
  kill_switch_alert_pct: 15.0

  markets:
    - "us-strikes-iran-by-march-31-2026"

parent_run: "run-006"
changes_from_parent: "Adversarial fills (adverse selection 10bps, maker fee -20bps, distance-based fill prob)"
```

## Critério de Sucesso

**H1 passa SE:**
- PnL líquido > 0 após 8h com adversarial fills
- PnL/hora > $0 em >60% das horas
- Drawdown max < 15%

**H1 falha SE:**
- PnL líquido negativo = estratégia não é lucrativa em condições realistas
- Drawdown > 25% = kill switch ativa = risco inaceitável

**Se H1 falha:** Precisamos recalibrar spread, gamma, order size, ou a estratégia inteira antes de considerar produção.

---

## Impacto nos Docs

Atualizar:
- `docs/smoke-test-plan.md` — H1 agora usa adversarial venue
- `docs/pmm-paper-to-production-switch.md` — adicionar adversarial params na tabela de diffs
- `docs/pmm-bugs-production-checklist.md` — marcar fee model como implementado
