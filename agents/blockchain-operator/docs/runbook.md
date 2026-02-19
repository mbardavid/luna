# Runbook Operacional — Crypto Sage Dual-Plane

## Canal operacional oficial

- Discord channel ID: `1473392629055098942`
- Reporting por operação + resumo diário: `config/ops.integration.json`

## Fluxo padrão

### Control Plane (humano -> bot)

1. `plan`
2. `execute --dry-run`
3. `execute` live (policy aprovada)
4. Validar `runId` + `state/audit.jsonl`

### Execution Plane (bot -> bot)

1. Receber envelope `v1`
2. Validar schema + perímetro A2A (`auth`/nonce/timestamp)
3. `execute-plane --dry-run`
4. `execute-plane` live
5. Monitorar `runId`, `idempotencyKey`, `state/audit.jsonl`

---

## Comandos úteis

> Com `policy.live.json`, execução live exige segregação de chaves Base/Solana/Hyperliquid.

### Control Plane

```bash
node src/cli.mjs plan --instruction "send 0.001 ETH to 0x... on base"
node src/cli.mjs execute --instruction "buy 0.001 BTC perp at market on hyperliquid" --policy config/policy.live.json --dry-run
node src/cli.mjs execute --instruction "buy 0.001 BTC perp at market on hyperliquid" --policy config/policy.live.json
node src/cli.mjs execute --instruction "deposit 1 USDC to hyperliquid perp" --policy config/policy.live.json --dry-run
node src/cli.mjs execute --instruction "deposit 1 USDC to hyperliquid perp" --policy config/policy.live.json
node src/cli.mjs execute --instruction "deposit 10 USDC from arbitrum to hyperliquid" --policy config/policy.live.json --dry-run
node src/cli.mjs execute --instruction "withdraw 6 USDC from hyperliquid to arbitrum to 0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6" --policy config/policy.live.json --dry-run
node src/cli.mjs execute --instruction "/saldo" --policy config/policy.live.json --dry-run
node src/cli.mjs execute-native --command saldo --policy config/policy.live.json --dry-run
```

### Execution Plane

```bash
# Dry-run A2A
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --dry-run --policy config/policy.live.json

# Live A2A
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/bridge.json --policy config/policy.live.json
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/hyperliquid-bridge-deposit.json --policy config/policy.live.json
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/hyperliquid-bridge-withdraw.json --policy config/policy.live.json

# Bateria completa de dry-runs A2A v1
npm run dry-run:a2a
```

### Replay

```bash
node src/cli.mjs replay --run-id <RUN_ID>
```

### Comando de saldo consolidado (`/saldo`)

Uso no canal do Crypto Sage (Control Plane / Native command):

- `/saldo`
- `saldo`
- `show balance`

Roteamento nativo `/saldo` usa a mesma pipeline nova (`parser -> normalize -> planner -> executor -> portfolio-balance`) e o mesmo formatter consolidado em USD.

Saída inclui, por rede (Base/Solana/Hyperliquid):

- ativos detectados
- quantidade por ativo
- preço USD mark-to-market (Chainlink com fallback Pyth)
- valor USD por ativo
- subtotal por carteira e total consolidado
- timestamp UTC do snapshot

Falhas parciais por carteira são reportadas sem quebrar a resposta inteira.
Ativos sem preço confiável aparecem como `N/A`.

### Procedimento: crédito de USDC no saldo Hyperliquid (spot -> perp)

Use quando o USDC já foi entregue ao ecossistema Hyperliquid e precisa virar margem perp (`usdClassTransfer`).

1. Confirmar que o USDC está disponível em `spotClearinghouseState` (via dry-run).
2. Executar dry-run:

```bash
node src/cli.mjs execute --instruction "deposit 1 USDC to hyperliquid perp" --policy config/policy.live.json --dry-run
```

3. Validar `result.preflight.checks.freeUsdc` >= `amount`.
4. Executar live somente após confirmação humana:

```bash
node src/cli.mjs execute --instruction "deposit 1 USDC to hyperliquid perp" --policy config/policy.live.json
```

Limitações atuais:

- operação suporta apenas `USDC`;
- operação move saldo entre classes internas Hyperliquid (`spot <-> perp`), **não** faz bridge L1 de Base/Solana;
- requer `HYPERLIQUID_ACCOUNT_ADDRESS` + `HYPERLIQUID_API_WALLET_PRIVATE_KEY` válidos.

### Procedimento: bridge nativo Arbitrum <-> Hyperliquid

#### A) Arbitrum -> Hyperliquid (`hyperliquid.bridge.deposit`)

1. Confirmar rota e mínimo (`HYPERLIQUID_BRIDGE_MIN_DEPOSIT_USDC`, default 5 USDC).
2. Dry-run:

```bash
node src/cli.mjs execute --instruction "deposit 10 USDC from arbitrum to hyperliquid" --policy config/policy.live.json --dry-run
```

3. Validar `preflight.accountCheck.accountMatch=true` (mesmo endereço Arbitrum e conta HL).
4. Live:

```bash
node src/cli.mjs execute --instruction "deposit 10 USDC from arbitrum to hyperliquid" --policy config/policy.live.json
```

#### B) Hyperliquid -> Arbitrum (`hyperliquid.bridge.withdraw`)

1. Dry-run com recipient allowlisted explícito:

```bash
node src/cli.mjs execute --instruction "withdraw 6 USDC from hyperliquid to arbitrum to 0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6" --policy config/policy.live.json --dry-run
```

2. Validar saldo spot USDC quando disponível (`preflight.hyperliquid.checks.freeUsdc`).
3. Live:

```bash
node src/cli.mjs execute --instruction "withdraw 6 USDC from hyperliquid to arbitrum to 0x3dd3b88Ee622415DD85a73E5274d29d52BF2a4c6" --policy config/policy.live.json
```

> `bridge` deBridge tocando `hyperliquid` diretamente retorna `DEBRIDGE_HYPERLIQUID_ROUTE_NOT_SUPPORTED` com passos recomendados via Arbitrum.

---

## Guardrails vigentes

- Mainnet-only (`execution.allowMainnetOnly=true`)
- Recipient policy/allowlist em operações de saída (+ recipient explícito quando informado)
- Hyperliquid como `destination_l3`
- Bridge source permitido: Base/Solana/Arbitrum/Hyperliquid (policy)
- Bridge provider permitido: `debridge`
- Rotas deBridge com `hyperliquid` direto são bloqueadas explicitamente (`DEBRIDGE_HYPERLIQUID_ROUTE_NOT_SUPPORTED`) e exigem pipeline via Arbitrum native bridge
- Idempotência + circuit breaker ativos
- Auditoria append-only por execução
- Segregação obrigatória de chaves (`requireKeySegregation=true`)

---

## Hardening checklist (mandatório)

### Antes de habilitar live

- [ ] `npm test` verde
- [ ] `npm run dry-run:a2a` verde
- [ ] `A2A_SECURITY_MODE=enforce` em produção
- [ ] `A2A_HMAC_KEYS_JSON` configurado com rotação definida
- [ ] `A2A_ALLOW_UNSIGNED_LIVE=false`
- [ ] `allowlists.recipients` revisada e mínima
- [ ] `allowlists.assets` revisada por estratégia
- [ ] `BASE_PRIVATE_KEY`, `ARBITRUM_PRIVATE_KEY` (ou fallback consciente), `SOLANA_PRIVATE_KEY_*`, `HYPERLIQUID_API_WALLET_PRIVATE_KEY` revisadas
- [ ] `config/policy.live.json` validado + versionado

### Operação contínua

- [ ] Rotação periódica de segredos A2A e chaves operacionais
- [ ] Revisão diária de `state/audit.jsonl`
- [ ] Alerta para `CIRCUIT_BREAKER_OPEN`
- [ ] Reconciliação de bridge tracking pendente
- [ ] Backup de estado (`idempotency`, `nonce`, `audit`) com retenção

---

## Incidentes

### 1) `A2A_SIGNATURE_INVALID` / `A2A_NONCE_REPLAY`

1. Bloquear emissor (`keyId`) no roteador A2A
2. Revisar relógio/NTP e janela (`A2A_MAX_SKEW_MS`)
3. Rotacionar secret e atualizar `A2A_HMAC_KEYS_JSON`
4. Reexecutar dry-run assinado antes de voltar live

### 2) `CIRCUIT_BREAKER_OPEN`

1. Inspecionar erros recentes em `state/audit.jsonl`
2. Corrigir causa raiz (RPC/API/policy)
3. Aguardar cooldown ou reset controlado

### 3) Falha de bridge live

1. Capturar `sourceTxHash`
2. Consultar tracking de `orderId`
3. Se tracking indisponível, registrar run + hash e acompanhar manualmente

### 4) Falha de nonce Hyperliquid

1. Verificar `state/hyperliquid-nonce.json`
2. Garantir 1 processo por API wallet (ou wallets isoladas)
3. Remover lock órfão **somente** com processo parado:

```bash
rm -f state/hyperliquid-nonce.lock
```

---

## Comandos finais de go-live (sequência recomendada)

```bash
# 1) testes completos
npm test

# 2) dry-run de todos os flows A2A v1
npm run dry-run:a2a

# 3) verificar policy live
node -e "import('./src/core/policy-engine.mjs').then(({loadPolicy})=>console.log(loadPolicy('config/policy.live.json').path))"

# 4) canário live (exemplo: transfer mínimo)
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/transfer.json --policy config/policy.live.json

# 5) replay/auditoria do canário
node src/cli.mjs replay --run-id <RUN_ID_DO_CANARIO>
```
