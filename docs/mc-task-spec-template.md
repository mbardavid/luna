# MC Task Spec Template v2 — Coding Tasks

Toda task de coding delegada ao Luan DEVE seguir este template.
Luna é responsável por preencher todos os campos ao criar o card no MC.

## Template Completo

```markdown
## Objective
[1-2 frases: o que precisa ser feito e POR QUÊ]

## Context
[O que existe hoje, o que falhou, o que mudou. Links para logs/runs anteriores se relevante]

## Execution Plan
1. [Passo 1 — ex: "Ler e entender quoting_engine.py linhas 45-120"]
2. [Passo 2 — ex: "Implementar cálculo de volatilidade via rolling window"]
3. [Passo 3 — ex: "Adicionar testes unitários para edge cases"]
4. [Passo 4 — ex: "Rodar suite completa e validar zero regressões"]

## Workspace
- Repository/dir: `path/to/code`
- Key files: `file1.py`, `file2.ts`
- Tests: `path/to/tests/`
- Config: `path/to/config.yaml` (se aplicável)

## Acceptance Criteria
- [ ] Critério 1 (específico, testável, binário — passa ou não passa)
- [ ] Critério 2
- [ ] Todos os testes existentes passam (zero regressões)
- [ ] Novos testes cobrem os cenários adicionados

## Verification Checks
[Comandos exatos que o agente DEVE rodar para validar antes de reportar "done"]

```bash
# Rodar testes
cd polymarket-mm && python -m pytest tests/ -v

# Verificar que não quebrou nada
python -m pytest tests/ -x --tb=short

# Validar output específico (quando aplicável)
python -c "from paper.quoting_engine import QuoteEngine; qe = QuoteEngine(...); assert qe.spread(0.5) >= 0.002"
```

## QA Guidance for Luna
[Perguntas que Luna deve fazer ao revisar o resultado]

- O agente alterou APENAS os arquivos listados em Workspace?
- Os testes novos cobrem edge cases (boundary values, nulls, errors)?
- A solução é backward-compatible com configs existentes?
- O agente rodou os Verification Checks e todos passaram?
- Há alguma dependência nova introduzida? Se sim, é justificável?

## Constraints
- [Não alterar arquivo X]
- [Não adicionar dependências externas]
- [Manter compatibilidade com Y]
- [Tempo máximo: N minutos]

## Rollback
[Como reverter se a mudança causar problemas em produção]
- `git checkout HEAD -- path/to/file`
- Ou: "reverter config X para valor Y"
```

---

## Exemplo Real (Bom)

**Title:** Implement dynamic spread based on market volatility

```markdown
## Objective
Implementar spread dinâmico no QuoteEngine que ajusta automaticamente baseado na
volatilidade recente do mercado. Atualmente spread é fixo (30bps), causando
over-quoting em mercados calmos e under-quoting em mercados voláteis.

## Context
- prod-002 rodando com spread fixo de 30bps no mercado "Iranian regime fall"
- Mercado teve períodos de volatilidade alta (mid moveu 0.45→0.52 em 2h) onde
  fomos filled adversarially
- Paper trading runs 005-007 mostraram que spread fixo perde 89% do edge em
  cenários adversariais

## Execution Plan
1. Ler `quoting_engine.py` e entender o fluxo de `calculate_spread()`
2. Implementar `VolatilityEstimator` com rolling window de 5min sobre mid prices
3. Modificar `calculate_spread()` para usar `vol_estimator.current_vol * spread_multiplier`
4. Adicionar config params: `vol_window_seconds`, `spread_floor_bps`, `spread_ceil_bps`
5. Escrever testes: low vol → spread floor, high vol → spread ceil, transition
6. Rodar suite completa + validar com replay de dados do prod-002

## Workspace
- Repository/dir: `polymarket-mm/`
- Key files: `paper/quoting_engine.py`, `paper/market_data.py`
- Tests: `tests/test_quoting_engine.py`, `tests/test_volatility.py` (novo)
- Config: `paper/runs/prod-002.yaml`

## Acceptance Criteria
- [ ] Spread varia entre floor (20bps) e ceil (200bps) baseado em volatilidade
- [ ] Vol estimator usa rolling window configurável (default 5min)
- [ ] Quando vol=0 (mercado parado), spread = floor
- [ ] Quando vol > threshold, spread sobe proporcionalmente até ceil
- [ ] Config backward-compatible (spread fixo funciona se vol params ausentes)
- [ ] Todos os testes existentes passam
- [ ] ≥5 testes novos cobrindo cenários de volatilidade

## Verification Checks
```bash
cd /home/openclaw/.openclaw/workspace/polymarket-mm
python -m pytest tests/ -v --tb=short
python -m pytest tests/test_volatility.py -v
python -c "
from paper.quoting_engine import QuoteEngine
# Verify floor
qe = QuoteEngine(spread_floor_bps=20, spread_ceil_bps=200)
assert 0.0019 < qe.calculate_spread(vol=0.0) < 0.0021, 'Floor check failed'
# Verify ceil
assert 0.0199 < qe.calculate_spread(vol=1.0) < 0.0201, 'Ceil check failed'
print('All spread checks passed')
"
```

## QA Guidance for Luna
- O `VolatilityEstimator` mantém estado entre chamadas? (deve manter)
- Os testes usam dados reais do prod-002 ou dados sintéticos? (prefer real)
- O fallback pra spread fixo funciona quando `vol_window_seconds` não está no config?
- O agente verificou que `calculate_spread()` é chamado no hot path? Performance ok?
- Foram tratados edge cases: primeiro tick (sem histórico), gap de dados, overflow?

## Constraints
- Não alterar `production_runner.py` nem `paper_venue.py`
- Não adicionar dependências externas (usar só stdlib + numpy que já existe)
- Manter interface pública do QuoteEngine inalterada
- Max 15min de execução

## Rollback
git checkout HEAD -- paper/quoting_engine.py paper/market_data.py
# Config: remover vol_window_seconds, spread_floor_bps, spread_ceil_bps do yaml
```

---

## Exemplo (Ruim — não dispatchável)

**Title:** Fix spread
**Description:** O spread tá errado. Arruma aí.

→ Sem plano, sem criteria, sem checks, sem QA guidance. Impossível de automatizar.
