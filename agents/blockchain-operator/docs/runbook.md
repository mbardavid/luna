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
node src/cli.mjs execute --instruction "/saldo" --policy config/policy.live.json --dry-run
node src/cli.mjs execute-native --command saldo --policy config/policy.live.json --dry-run
```

### Execution Plane

```bash
# Dry-run A2A
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --dry-run --policy config/policy.live.json

# Live A2A
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/bridge.json --policy config/policy.live.json

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

---

## Guardrails vigentes

- Mainnet-only (`execution.allowMainnetOnly=true`)
- Recipient policy/allowlist em operações de saída (+ recipient explícito quando informado)
- Hyperliquid como `destination_l3`
- Bridge source permitido apenas Base/Solana
- Bridge provider permitido: `debridge`
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
- [ ] `BASE_PRIVATE_KEY`, `SOLANA_PRIVATE_KEY_*`, `HYPERLIQUID_API_WALLET_PRIVATE_KEY` segregadas
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
