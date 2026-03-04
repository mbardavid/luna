# Estratégia DeFi Yield Farming (Base) — stablecoin vaults

**Status:** Planejamento/pesquisa apenas (sem execução on-chain)  
**Objetivo:** estratégia de yield farming de stablecoins na Base, com foco em risco menor e exposição single-sided em USDC.

## 1) Abordagem/tese + seleção de vaults

### Tese operacional
- Objetivo é **otimizar retorno ajustado a risco**, não APY absoluto.
- Estratégia inicial: manter tudo em **stablecoin principal (USDC)** e priorizar protocolos com:
  1. menor risco de perda de capital,
  2. melhor transparência (vaults single-sided),
  3. histórico de funcionamento estável.
- Governança: operação 100% paper/dry-run durante validação; sem execução on-chain até aprovação do playbook.

### Seleção proposta (núcleo e reserva)
- **Núcleo:** **Yearn USDC Vault (Base)**
  - Justificativa: menor complexidade operacional, alocação single-sided, menor risco relativo no cenário atual.
  - Uso: **preferência primária** enquanto os critérios de saúde estiverem verdes.
- **Reserva/diversificação condicional:** **MaxAPY USDC (Base)**
  - Justificativa: alternativa agregadora para fallback quando Yearn tiver qualidade degradada ou APY líquido insuficiente.
  - Uso: complemento só após validação de risco + custo de movimento.
- **Exclusão do core:** **Aerodrome EURC-USDC (Base)**
  - Motivo: apesar de APY elevado, envolve risco de stablepair com **desemparelhamento (EUR/USD depeg + dinâmica EURC)** e exposição de par não-estritamente single-sided.
  - Em carteira de baixo risco, risco de peg-out/IL e estabilidade de retorno é superior ao benefício de APY.

### Regra de alocação inicial (pré-live)
- Iniciar com 2 blocos:
  - `W_core = 70%` para Yearn
  - `W_reserve = 30%` em caixa USDC (não investida)
- Aumentar exposição apenas após 14 dias de operação contínua sem gatilhos críticos.

## 2) Risco (checklist detalhado)

### 2.1 Smart-contract risk
- Risco de bug do contrato, upgrade malicioso, pausa de função.
- Mitigações:
  - whitelist de contratos e código fonte/ABI versionado no repositório.
  - verificação de `vault.totalAssets()/totalSupply` comportamental (quebras súbitas).
  - recusa automática em `paused=true` ou `deposit/senha` incompatível.

### 2.2 Custodial risk
- Custódia na wallet + chaves + operador + endpoint.
- Mitigações:
  - chaves em policy (sem uso de chave de produção em ambientes locais).
  - limites transacionais e múltipla assinatura para mudanças de alocação.
  - separar funções de leitura e escrita em papéis distintos.

### 2.3 Bridge risk
- Risco de travas, reorg/replay e atraso de retorno cross-chain.
- Mitigação: sem bridge operacional na fase inicial de piloto.
- Se necessário no futuro: somente `top-up` manual com janela de aprovação e checklist.

### 2.4 Depeg risk
- Risco de desvio de paridades (USDC/USDT/euroc etc.).
- Regras:
  - se indicador de depeg de stablecoins entrar em zona de atenção, reduzir exposição para caixa.
  - excluir pools com ativos não-USD sem proteção explícita.

### 2.5 Taxa de juros / condições de mercado
- Variação de taxas impacta APY líquido e risco de drawdown de retorno.
- Risco: APY aparenta subir, mas retorno líquido cai após fee, slippage e gas.
- Mitigação: calcular **APY líquido projetado mensal** antes de rebalancear.

### 2.6 Governance risk
- Alterações em parâmetros (fees, estratégia interna, admin multisig).
- Mitigação: monitorar eventos on-chain de upgrade/admin e bloquear ação automática em mudança de parâmetros críticos.

### 2.7 Oracle risk
- Falhas de preço/câmbio no cálculo de performance e saúde de pool.
- Mitigação:
  - fontes redundantes de dados (RPC primário + fallback + API de explorer/API de vault).
  - validação cruzada `diff <= threshold` entre fontes.

### 2.8 Liquidez risk
- Saídas forçadas com grande impacto em preço/slippage.
- Mitigação:
  - monitorar diferença entre `share_price_variation` e `redeem_preview`.
  - evitar rebalance em picos de liquidez ruim.

### 2.9 Withdrawal delay / lock / blackout
- Janela de saque, fila de saída, e atrasos de processamento.
- Mitigação:
  - só rebalancear se janela mínima de liquidez for respeitada.
  - simulação de saída antes de nova entrada em outro vault.

## 3) Regras de rebalanceamento

### Variáveis base
- `apy_y` = APY líquida do Yearn (7d)
- `apy_m` = APY líquida do MaxAPY (7d)
- `tvly`, `tvm` = TVL
- `share_chg` = mudança de share price (24h)
- `risk_score` = 0..100 (baixa→alta)

### Regras de entrada/saída
- **Entrada principal (Yearn):**
  - `apy_y >= 18%`
  - `risk_score <= 30`
  - `share_chg` dentro de banda estável (sem variação abrupta)
- **Mover parte para MaxAPY (diversificação):**
  - `apy_y < 17%` por 12h **e** `apy_m >= apy_y + 1.0pp` **e** `risk_score <= 45`.
  - máx. `20%` alocado ao MaxAPY inicialmente.
- **Saída total para caixa:**
  - `risk_score >= 70` ou
  - qualquer evento `paused`/`governance pause`/`oracle divergence`.

### Cooldown + Histerese
- **Cooldown mínimo entre rebalanceamentos:** 24h.
- **Histerese de troca (ex.: Yearn→MaxAPY):**
  - condição precisa persistir por 3 ciclos consecutivos de coleta.
- **Histerese de retorno (MaxAPY→Yearn):**
  - `apy_y > apy_m + 0.8pp` por 2 ciclos + `risk_score <= 25`.

### Saídas de emergência
- **Hard stop imediato** se:
  - contrato pausado,
  - depeg grave detectado,
  - divergência de data feed > limiar definido.
- Ação: bloquear `executions`, manter modo `hold`, abrir `risk_event` crítico.

### APY spike handling (pico súbito)
- Se APY subir > `+4pp` em 4 horas sem confirmação de liquidez/fonte:
  - **não** rebalancear imediato.
  - entrar em estado `await_confirmation` e coletar mais 1 ciclo.
- Confirmado após 2 ciclos e sem risco: rebalancear somente se ganho anualizado projetado superar `delta_min=0.75pp`.

### Pseudocódigo (alto nível)
```text
collect -> score_risk -> compute target_alloc
if hard_stop: set target_alloc=0 (cash), action=hold
else if hysteresis_ok and cooldown_elapsed and delta_gain > threshold:
    set target_alloc according rules
else:
    keep current
log decision to allocations + runner_runs
if dry_run: no tx
```

## 4) Monitoring plan (APY, TVL, share price, health)

### Fontes primárias
1. **RPC da Base** (contratos dos vaults) — dados on-chain reais.
2. **Fonte secundária de APY/TVL** (API oficial dos protocolos, se disponível).
3. **API/Explorer de cadeia** para eventos de pausa/upgrade/governance.

### Fallback
- Se RPC falhar: troca para RPC secundário e manter leitura por no mínimo 2 fontes.
- Se API secundária falhar: operar com RPC + cache de última janela válida por até 30 minutos.
- Se as fontes divergirem acima de limiar: marcar `risk_event` de “data integrity risk” e reduzir decisão agressiva.

### O que monitorar
- **APY:** bruto/líquido por vault; variação 1h/24h/7d.
- **TVL:** tendência e quedas abruptas.
- **Share price:** drift relativo e inconsistências de `pricePerShare`/`sharePrice`.
- **Vault health:** pausa, status de erro, mudanças de parâmetros, upgrade.
- **Saúde do runner:** duração média do ciclo, falhas, retries.

### Alertas sugeridos
- `CRIT` se APY líquido negativo por 2 ciclos.
- `WARN` se TVL cair >10% em 24h.
- `WARN` se `share_chg` >2σ no intervalo de 6h.
- `CRIT` se `paused` ou erro de oracle.

## 5) Arquitetura de automação (runner + Supabase)

### Componentes
- `collectors/`  
  Coleta estados dos vaults (APY/TVL/sharePrice), eventos e metadados de risco.
- `risk_engine/`  
  Calcula score de risco e decide bloqueio automático.
- `allocator/`  
  Aplica regras de rebalanceamento, histerese, cooldown e saída de emergência.
- `planner/`
  Gera plano de ação e mantém dry-run como padrão.
- `runner/`  
  Orquestra cron, chama módulos, persiste logs e aciona alertas.
- `supabase_repo/`  
  Camada de persistência (insert/select) para auditoria e checks.

### Job flow sugerido
1. `runner --mode check` a cada 15min.
2. `collector` grava `vault_snapshots`.
3. `risk_engine` calcula `risk_score` e valida hard-stops.
4. `allocator` calcula `target allocations`.
5. `planner` escreve `allocations` e plano.
6. Em modo paper: apenas registrar.
7. Em modo live autorizado: executar apenas se risco verde e manual override ausente.
8. Sempre gravar `runner_runs` com status final e resumo.

### Tabelas Supabase propostas

#### `vault_snapshots`
- `id` UUID PK
- `captured_at` TIMESTAMPTZ (UTC)
- `vault_key` TEXT (ex.: yearn_usdc_base, maxapy_usdc_base)
- `network` TEXT
- `apy_gross` NUMERIC
- `apy_net` NUMERIC
- `tvl_usd` NUMERIC
- `share_price` NUMERIC
- `share_price_change_24h` NUMERIC
- `tvl_change_24h` NUMERIC
- `is_paused` BOOLEAN
- `risk_score` SMALLINT
- `data_source` TEXT
- `raw_payload` JSONB
- `created_at` TIMESTAMPTZ DEFAULT now()

#### `allocations`
- `id` UUID PK
- `run_id` UUID FK -> runner_runs.id
- `captured_at` TIMESTAMPTZ
- `vault_key` TEXT
- `current_weight_pct` NUMERIC
- `target_weight_pct` NUMERIC
- `decision_reason` TEXT
- `hysteresis_ok` BOOLEAN
- `cooldown_passed` BOOLEAN
- `state` TEXT  
  (`proposed` | `executed` | `skipped`)
- `tx_hash` TEXT NULL
- `executed_at` TIMESTAMPTZ NULL
- `created_at` TIMESTAMPTZ DEFAULT now()

#### `risk_events`
- `id` UUID PK
- `run_id` UUID FK -> runner_runs.id
- `event_at` TIMESTAMPTZ
- `severity` TEXT (`info` `warn` `critical`)
- `category` TEXT (`smart_contract`,`custodial`,`bridge`,`depeg`,`rates`,`governance`,`oracle`,`liquidity`,`withdrawal`)
- `code` TEXT
- `message` TEXT
- `metadata` JSONB
- `mitigation` TEXT
- `resolved` BOOLEAN DEFAULT false
- `resolved_at` TIMESTAMPTZ NULL

#### `runner_runs`
- `id` UUID PK
- `run_at` TIMESTAMPTZ
- `status` TEXT (`started`,`partial`,`success`,`failed`,`hard_stop`)
- `duration_ms` INTEGER
- `env` TEXT
- `dry_run` BOOLEAN
- `allocations_count` INTEGER
- `risk_events_count` INTEGER
- `hard_stop` BOOLEAN
- `error_code` TEXT NULL
- `error_message` TEXT NULL
- `summary_json` JSONB
- `created_at` TIMESTAMPTZ DEFAULT now()

## 6) Checklist operacional

### Antes do funding
- [ ] Endpoints RPC primários + backup validados.
- [ ] Contratos dos vaults validados e documentados.
- [ ] Segredos sem hardcode; `.env` isolado.
- [ ] Limites de risco aprovados (caps, cooldown, depeg threshold).
- [ ] Ambiente em modo dry-run por 72h com logs íntegros.

### Antes de cada depósito
- [ ] Conferir resultado dos últimos 2 ciclos de `runner_runs` (sucesso e sem `risk_events` críticos).
- [ ] Confirmar ausência de alerts de oracle/depeg.
- [ ] Validar que não há `hard_stop` ativo.
- [ ] Registrar no plano de mudança o `target allocation` e risco aceito.

### Check diário
- [ ] Verificar `runner_runs` status do dia.
- [ ] Verificar divergência entre `share_price` reportada e expectativa de saída.
- [ ] Validar `vault_snapshots` recentes sem lacunas.
- [ ] Revisar `risk_events` e resolver críticos.
- [ ] Ajustar parâmetros se necessário (sem mudança não documentada fora da janela de revisão).

### Playbook de incidente
- **Sinais críticos detectados:**
  - Pausa do vault, depeg severo, ou divergência grave de dados.
- **Ação em 10 min:**
  1. setar `runner` em modo `hold`.
  2. criar `risk_event` crítico e registrar evidência.
  3. reduzir exposição para caixa em ordem operacional definida.
  4. executar pós-incidente e atualizar thresholds se necessário.

## 7) Verificação (dry-run) — comandos de prova local

### 7.1 Execução local de dry-run (runner)
```bash
# Check de ciclo sem emissão de transação
python -m runner.cli \
  --mode check \
  --strategy defi_yield_base \
  --network base \
  --env staging \
  --dry-run

# Forçar reprocessamento de um intervalo para validação
python -m runner.cli \
  --mode backfill \
  --strategy defi_yield_base \
  --from 2026-03-01T00:00:00Z \
  --to 2026-03-01T23:59:59Z \
  --dry-run
```

### 7.2 Queries SQL de verificação em logs
```sql
-- Últimos snapshots
select vault_key, captured_at, apy_net, tvl_usd, share_price, is_paused
from vault_snapshots
order by captured_at desc
limit 100;

-- Decisões de alocação do dia
select r.run_at, a.vault_key, a.current_weight_pct, a.target_weight_pct, a.decision_reason, a.state
from runner_runs r
join allocations a on a.run_id = r.id
where r.run_at >= now() - interval '24 hours'
order by r.run_at desc, a.created_at desc;

-- Eventos de risco críticos
select event_at, severity, category, code, message, resolved
from risk_events
where severity = 'critical' and event_at >= now() - interval '7 days'
order by event_at desc;

-- Resumo de execução do runner
select status, count(*) as runs, sum(allocations_count) as total_allocs
from runner_runs
where run_at >= now() - interval '7 days'
group by status
order by status;
```

## Observação final
Este plano mantém operação conservadora: foco em **Yearn como núcleo** + fallback controlado em MaxAPY, com governance de risco explícita para evitar capturar APY alto com risco de principal. A exclusão do Aerodrome do core é deliberada por risco estrutural de paridade e não por falta de retorno.
