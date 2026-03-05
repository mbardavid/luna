# Plano de Implantação (Sem On-Chain): DeFi Yield Farming Base + Yearn USDC (Stable)

**Status:** `plan_submitted`  
**MC Task ID:** `856e22f8-9557-4e16-8ac5-9962c791229b`  
**Objetivo:** definir arquitetura e execução técnica para automação de yield em stable na Base, sem executar transações reais.

## 1) Escopo e premissas

- Cadeia alvo de renda: **Base**.
- Ação principal: **deposit/withdraw em Yearn USDC vault** (com fallback observacional para alternativa Yearn MaxAPY, sem sair do princípio do plano).
- Origem de capital: **Polygon → Base** (via ponte oficial).
- Observabilidade + rastreabilidade em **crypto-sage** e **Supabase** (ou JSONL fallback).
- **Sem assinatura/executar tx em mainnet enquanto houver incerteza ou modo `paper` ativo.**
- Seguir **idempotência** por `run_id` + dedupe + estado último-sucedido.

## 2) Arquitetura/proposta de módulos e contratos de I/O

### 2.1 Visão de arquitetura

```text
[scheduler cron] --> [scripts runner/decision] --> [fila/dispatch crypto-sage] --> [executor crypto-sage]
      |                     |                                     |
      v                     v                                     v
[fetchers APY]        [policy + rebalance]                  [on-chain tx handler]
      |                     |                                     |
      +--> [snapshot store] +--> [Supabase/JSONL ledger] <----+ [proof/alerts]
```

### 2.2 Módulos no repositório `scripts` (orquestração/planejamento)

- `scripts/defi_yield_farming_base_runner.py` **(alterar/extender):**
  - Entradas CLI (mode, amount, source chain, risk gates, allowlist version).
  - Chama motor de decisão e emite payload único com `run_id`.
  - Em `paper`, não envia transações, apenas plano + evidência.
- `scripts/adapters/base_chain_client.py` **(novo):**
  - Endpoints de APY/status do Vault e oracle (2+ fontes + fallback).
  - Normalização (`apy`, `tvl`, `paused`, `withdraw_preview_bps`).
- `scripts/adapters/polygon_bridge_provider.py` **(novo):**
  - Resolve prova de roteamento da ponte Polygon→Base, quote + fee + eta.
  - Retorna status normalizado e hashes de quote.
- `scripts/adapters/token_registry.py` **(novo):**
  - Resolve símbolos permitidos, decimal, contract, tokenAddress por chain.
  - Saída padronizada para decisão e validação anti-phishing.
- `scripts/policy/apy_monitor.py` **(novo):**
  - Calcula APY líquido projetado por vault.
  - Gate de risco + divergência de fontes + hysterese + cooldown.
- `scripts/policy/rebalance_orchestrator.py` **(novo):**
  - Constrói plano idempotente de ações (withdraw/deposit/bridge).
  - Garante que o mesmo estado não gera ações duplicadas.
- `scripts/integrations/supabase_writer.py` **(novo):**
  - Normaliza e agrupa rows por tabela; prepara payload JSON pronto p/ insert/upsert.
- `scripts/utils/retry_backoff.py` **(novo):**
  - Retry com backoff+jitter + circuit-breaker.

### 2.3 Contratos de I/O (planejamento)

**Input de decisão (por ciclo)**

```json
{
  "run_id": "uuid",
  "chain": "base",
  "mode": "paper|live",
  "vault_snapshots": [...],
  "allocation_current": {...},
  "free_cash_base_usdc": "Decimal",
  "risk_overrides": {"max_risk": 35, "cooldown_hours": 24},
  "bridge": {"source_chain": "polygon", "provider_preference": ["base_bridge", "cctp", "alt"]}
}
```

**Output de actions (puro para crypto-sage)**

```json
{
  "executor": "crypto-sage",
  "run_id": "uuid",
  "mode": "paper",
  "priority": 10,
  "chain": "base",
  "actions": [
    {"action": "bridge_polygon_to_base_usdc", "params": {...}},
    {"action": "defi_withdraw", "params": {...}},
    {"action": "defi_deposit", "params": {...}}
  ]
}
```

**Output de rastreio**
- `tracking_records[]` com `trace_id`, `event_type`, `source_payload`, `reason`, `risk_score`, `vault_key` e `run_id`.

### 2.4 Módulos no repositório `crypto-sage`

- `src/crypto_sage/actions/defi.py` **(novo):**
  - Handler explícito para `defi_deposit`, `defi_withdraw`.
  - Validar `vault_key`, `amount_usdc_micro`, `receiver`, `chain`, `token`.
- `src/crypto_sage/actions/bridge.py` **(novo):**
  - Handler para `bridge_polygon_to_base_usdc` com seleção de provider.
  - Execução em dry-run por padrão; em `live` exigir confirmação manual + whitelist.
- `src/crypto_sage/strategies/base_yield.py` **(novo):**
  - Orquestrador de ciclo completo, com estado de operação e lock de dedupe.
- `src/crypto_sage/risk/allowlist.py` **(novo):**
  - Contratos/endereços autorizados por ambiente.
- `src/crypto_sage/observability/apy_events.py` **(novo):**
  - Emissão de logs estruturados e eventos de alerta.

## 3) Arquivos a criar/alterar (planejamento de implementação)

### 3.1 Repositório `scripts`

**Criar**
- `scripts/adapters/polygon_bridge_provider.py`
- `scripts/adapters/base_chain_client.py`
- `scripts/adapters/token_registry.py`
- `scripts/policy/apy_monitor.py`
- `scripts/policy/rebalance_orchestrator.py`
- `scripts/integrations/supabase_writer.py`
- `scripts/utils/retry_backoff.py`
- `scripts/fixtures/official_yearn_assets.json` (cache de allowlist e metadados)

**Alterar**
- `scripts/defi_yield_farming_base_runner.py`
- `research/defi_yield_farming_base.py` (apenas ajuste de contratos/seeds de policy, caso necessário)
- `tests/test_defi_yield_farming_base.py` (novos testes abaixo)

### 3.2 Repositório `crypto-sage`

**Criar**
- `src/crypto_sage/strategies/base_yield.py`
- `src/crypto_sage/actions/defi.py`
- `src/crypto_sage/actions/bridge.py`
- `src/crypto_sage/risk/allowlist.py`
- `src/crypto_sage/observability/apy_events.py`
- `tests/strategies/test_base_yield.py`
- `tests/actions/test_defi_actions.py`
- `tests/actions/test_bridge_actions.py`

**Alterar**
- `src/crypto_sage/dispatch.py` (registro de novo executor/handler)
- `config/settings.yaml` (allowlist, provider keys, modos)

## 4) Endereços/links oficiais + sinais de anti-phishing

### 4.1 Links oficiais a usar como fonte de verdade

- Yearn principal: `https://yearn.fi` / `https://docs.yearn.fi`
- Base (bridge + chain info): `https://docs.base.org`, `https://bridge.base.org` (ou página oficial equivalente atual)
- Circle/Cross-chain USDC: `https://www.circle.com` (seção CCTP/USDC)
- Polygon: `https://wallet.polygon.technology/bridge/` (ou canal de bridge oficial vigente)
- Explorador Base: `https://basescan.org`
- Explorador Polygon: `https://polygonscan.com`

### 4.2 Red flags anti-phishing

Checklist obrigatório em cada operação:
1. Domínio é **exatamente** match com allowlist; sem typosquatting.
2. Sempre comparar contrato via explorer + registry oficial + allowlist local versionada.
3. Exigir `chainId`, `token`, `decimals`, `receiver` no payload de ação; falha em qualquer campo = rejeição.
4. Nenhum handler aceita endereços sem validação checksum/prefix.
5. Não aceitar URI/endpoint de bridge de domínios fora da allowlist.
6. Proibir assinatura de transação com permissões globais/ilimitadas.

## 5) USDC vs USDC.e e decimals

### 5.1 Regra operacional

- **Polygon:** padrão atual do sistema = `USDC.e` (token wrapper).
- **Base:** padrão no plano = `USDC` nativo da cadeia (confirmar no registry oficial no runtime).
- Todas as quantidades internas em `Decimal` (escala humana) e **micro-units** em `int` para serialização de tx.

### 5.2 Normalização

- `usdc_to_micro_units(x) = floor(x * 1_000_000)`
- `usdc_from_micro_units(int)` mantém precisão decimal de 6 casas.
- Validar `decimals == 6` nas duas chains antes de execução.
- Guardrail: qualquer mismatch de decimals invalida ciclo (`reason=decimals_mismatch`).
- Guardrail de endereço: se tokenAddress divergente entre registry + onchain + allowlist, ação de depósito/bridge entra em `FAIL_SAFE`.

## 6) Segurança, idempotência e rollback

### 6.1 Segurança operacional

- Lista de permissão (allowlist) versionada com `allowlist_version`.
- Limites por ciclo: `max_delta_pp`, `max_gas_usdc`, `max_slippage_bps`.
- Dupla aprovação para mudanças de parâmetros de risco/allowlist no repo de execução.
- `run_id` obrigatório em todos os eventos; dedupe por (`run_id`,`action_fingerprint`).
- Em qualquer erro de fonte/assinatura/endpoint: **modo HOLD + retry controlado + alerta**.

### 6.2 Idempotência

- `action_fingerprint = sha256(run_id + action_type + vault + amount + receiver + memo)`.
- Execução só acontece se `proof_key` não existir como concluído.
- Repetição da mesma decisão deve retornar estado idêntico e nenhum efeito colateral extra.

### 6.3 Rollback / unwind (obrigatório)

Plano de saída explícito:
1. `defi_withdraw` de vault atual para USDC/Base na Base.
2. Confirmar `withdraw_preview` e saldo pós-ação.
3. `bridge_base_to_polygon` (somente quando aprovado e com política de retorno de ativo) ou manter em caixa Base conforme política diária.
4. Registrar evento `unwind` em rastreio.
5. Em falha parcial:
   - tentar compensação reversa do último passo (idempotente)
   - manter `cash` mínimo de segurança (>= 1 operação planejada)
   - emitir `CRIT_ALERT` e travar novos rebalanceamentos.

## 7) Logging mínimo + acoplamento no crypto-sage

### 7.1 Opção A (preferida): Supabase

Tabelas sugeridas mínimas:
- `yield_runs (run_id, started_at, mode, strategy_version, allowlist_version, status)`
- `yield_snapshots (run_id, vault_key, chain, captured_at, apy_gross, apy_net, tvl, risk_score, data_sources)`
- `yield_actions (run_id, action_type, action_hash, status, params, error_code, dry_run)`
- `yield_events (run_id, level, event_code, message, metadata)`
- `yield_balances (run_id, chain, symbol, symbol_variant, balance_usdc_micro, source)`

Acoplamento no crypto-sage:
- `strategy/base_yield` chama `append_tracking()` após cada etapa.
- Inserir também comando interno padronizado `/saldo snapshot` (modo runner) para validar carteira antes/depois de cada ciclo, persistindo em `yield_snapshots` e `yield_balances`.
- Inserções feitas em lote no fim do ciclo; falha de write não bloqueia decisão crítica, mas gera `events` com `severity=WARN`.

### 7.2 Opção B fallback (sem infra): JSONL local

- `logs/defi_yield_farming/base_yield/<YYYY-MM-DD>.jsonl`
- Esquema line-by-line com campos mínimos: `ts`, `run_id`, `level`, `event`, `payload_sha256`.
- Rotina de ingestão para backfill para Supabase quando infra retornar.

## 8) Fallback/retry e falha de endpoint (design)

- Estratégia por chamada: `3 tentativas` com backoff `2s, 6s, 18s` + jitter `±25%`.
- Se 2/3 fontes de APY falharem: continuar com fonte restante somente em modo conservador (`hold`, `cash +10%`).
- Se bridge quote falhar em provider principal:
  1. marca `source=provider_primary` failed
  2. tenta provider secundário (ex.: CCTP vs bridge oficial)
  3. se falhar, reprocessa sem ação e dispara `WARN`.
- Se execução persistir com falha > 30 min: abrir estado `HOLD+manual_review`.

## 9) Verificações e testes sugeridos

### 9.1 Verificações (comandos)

```bash
# 1) dry-run completo do ciclo
python scripts/defi_yield_farming_base_runner.py \
  --yearn-apy-threshold 0.27 --max-apy-threshold 0.23 \
  --yearn-risk 15 --max-risk 20 \
  --amount-usdc 500 --free-cash-usdc 30 \
  --mode paper --run-id test-001

# 2) validação de conectividade de fontes (mock + live)
python -m pytest tests/test_defi_yield_farming_base.py -q

# 3) smoke de payload crypto-sage (estrutura + assinatura)
python -m pytest tests/strategies/test_base_yield.py::test_dispatch_schema -q

# 4) sanity de logs
python -m pytest tests/test_supabase_writer.py::test_append_only -q
```

### 9.2 Cobertura mínima de testes

- **Unidade:**
  - USDC/USDC.e token resolver (inclui mismatch decimals)
  - cálculo de APY líquido + custo de rebalance
  - dedupe/idempotência por action_fingerprint
  - decisão conservadora em divergência de fontes
  - fallback de providers de bridge
- **Smoke/integração:**
  - fluxo de ciclo paper com bridge opcional + depósito
  - fluxo de ciclo com falha de fonte e fallback conservador
  - fluxo de unwind parcial + compensação

## 10) Guardrails explícitos de NO LIVE FUNDS

- `mode` padrão = `paper`.
- `YIELD_ALLOW_LIVE_EXECUTION` deve ser `false` por padrão (feature flag de ambiente).
- `run_mode=live` exige confirmação explícita em tempo de execução e `--confirm` no CLI.
- `max_daily_live_budget_usdc=0` até validação de 2 ciclos consecutivos sem WARN.
- Campos de segurança que devem causar `abort` em live: 
  - `decimals != 6`,
  - `allowlist_version` desconhecida,
  - `provider != allowlist`,
  - `risk_score>=70` com sinal de de-risco não executado.

## 11) Entrega final esperada (saída executável)

1. Documento de planificação aprovado (`this doc`).
2. Runner ajustado em `scripts` com payload compatível.
3. Handlers novos no `crypto-sage`.
4. Pipeline de observabilidade + alertas + rollback.
5. Testes e smoke cobrindo cenários críticos.
6. Migração de `paper` → `live` apenas por mudança deliberada de config + aprovação humana.

---

**Status final:** `plan_submitted`