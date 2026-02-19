# Crypto Sage A2A Protocol — Execution Plane v1

Contrato oficial de integração bot→bot com path curto (`schema -> policy -> connector`).

## 1) Envelope padrão (obrigatório)

```json
{
  "schemaVersion": "v1",
  "plane": "execution",
  "operation": "transfer",
  "requestId": "req_20260218_0009",
  "correlationId": "decisionbot_cycle_9009",
  "idempotencyKey": "transfer-internal-0001",
  "timestamp": "2026-02-18T02:47:00Z",
  "dryRun": false,
  "meta": {
    "decisionBot": "alpha-v2"
  },
  "auth": {
    "scheme": "hmac-sha256-v1",
    "keyId": "bot-alpha",
    "nonce": "nonce-20260218-0001",
    "timestamp": "2026-02-18T02:47:00Z",
    "signature": "<hex-ou-base64>"
  },
  "intent": {}
}
```

Obrigatórios:

- `schemaVersion` = `v1`
- `plane` = `execution`
- `operation`
- `requestId`
- `correlationId`
- `intent`

Opcionais:

- `idempotencyKey`
- `timestamp`
- `dryRun`
- `meta`
- `auth` (recomendado; obrigatório para live quando perímetro A2A está em modo enforce)

Schema versionado:

- `config/execution-plane.schema.v1.json`

### 1.1 Meta opcional para `mentionDelegationMode: gated` (Discord)

Quando o orquestrador usa mention `@bot` no Discord como trigger de handoff, o runtime suporta `meta.mentionDelegationMode=gated` com este bloco:

```json
{
  "meta": {
    "mentionDelegationMode": "gated",
    "mentionDelegation": {
      "channel": "discord:channel:1473392629055098942",
      "messageId": "1473395000000000001",
      "originBotId": "decision-router",
      "targetBotId": "blockchain-operator",
      "observedAt": "2026-02-18T19:36:58Z",
      "ttlSeconds": 300,
      "dedupeBy": "messageId",
      "delegatedHumanProxy": {
        "mode": "delegated-human-proxy",
        "policyValidated": true,
        "envelopeValidated": true,
        "riskGatePassed": true,
        "riskClassification": "live",
        "authorizationRef": "authz_20260218_user-main_009"
      }
    }
  }
}
```

Regras aplicadas no receiver:

- mention é **apenas trigger**, não autorização autônoma;
- exige contexto de `delegated-human-proxy` já validado (`policy/envelope/risk`);
- anti-loop obrigatório (`originBotId != targetBotId`);
- dedupe obrigatório por `messageId`;
- TTL obrigatório (`observedAt + ttlSeconds` válido no consumo).

Default seguro: se `mentionDelegationMode` não estiver presente, comportamento atual permanece (sem gating por mention).

---

## 2) Segurança A2A (anti-replay/perímetro)

### Auth HMAC (`hmac-sha256-v1`)

Assinatura calculada em:

1. Clone do payload
2. Remover `auth.signature`
3. Serializar com `stableStringify` (ordem determinística de chaves)
4. `HMAC-SHA256(secret, serialized)`

Secrets por `keyId`:

- `A2A_HMAC_KEYS_JSON={"bot-alpha":"secret"}`

### Anti-replay

- `auth.nonce` única por `keyId`
- Janela por timestamp (`A2A_MAX_SKEW_MS`, default `120000`)
- Registro de nonce (`state/a2a-nonce-store.json`) com TTL (`A2A_NONCE_TTL_MS`, default `300000`)

### Fallback seguro

- `A2A_SECURITY_MODE=permissive` (default):
  - dry-run sem auth é aceito
  - live sem auth é bloqueado por padrão (`A2A_ALLOW_UNSIGNED_LIVE=false`)
- `A2A_SECURITY_MODE=enforce`:
  - exige auth válida (assinatura + nonce + janela) para live

---

## 3) Operações v1

### `bridge`

`intent` obrigatório:

- `fromChain` (`base|solana`)
- `toChain` (`base|solana`)
- `asset`
- `amount`

Opcional:

- `recipient`
- `provider` (`debridge`)
- `quoteId`
- `maxSlippageBps`

Exemplo: `docs/examples/a2a-v1/bridge.json`

### `swap.jupiter`

Obrigatório:

- `chain` = `solana`
- `inAsset`
- `outAsset`
- `amount`

Opcional:

- `mode` (`ExactIn|ExactOut`)
- `slippageBps`
- `recipient`
- `routeHint`

Exemplo: `docs/examples/a2a-v1/swap-jupiter.json`

### `swap.raydium`

Obrigatório:

- `chain` = `solana`
- `inAsset`
- `outAsset`
- `amount`

Opcional:

- `poolId`
- `slippageBps`
- `recipient`

Exemplo: `docs/examples/a2a-v1/swap-raydium.json`

### `swap.pumpfun`

Obrigatório:

- `chain` = `solana`
- `side` (`buy|sell`)
- `symbol`
- `amount`

Opcional:

- `mint`
- `amountType` (`base|quote`)
- `slippageBps`
- `recipient`

Exemplo: `docs/examples/a2a-v1/swap-pumpfun.json`

### `defi.deposit`

Obrigatório:

- `chain` (`base|solana`)
- `protocol`
- `target`
- `asset`
- `amount`

Opcional:

- `minSharesOut`
- `recipient`

Exemplo: `docs/examples/a2a-v1/defi-deposit.json`

### `defi.withdraw`

Obrigatório:

- `chain` (`base|solana`)
- `protocol`
- `target`
- `asset`
- `amount`
- `recipient`

Opcional:

- `amountType` (`asset|shares`)

Exemplo: `docs/examples/a2a-v1/defi-withdraw.json`

### `hyperliquid.spot.order` / `hyperliquid.perp.order`

Exemplos:

- `docs/examples/a2a-v1/hyperliquid-spot-order.json`
- `docs/examples/a2a-v1/hyperliquid-perp-order.json`

### `transfer` / `send`

Runtime atual para transferências nativas:

- Base: `ETH`
- Solana: `SOL`

Exemplos:

- `docs/examples/a2a-v1/transfer.json`
- `docs/examples/a2a-v1/send.json`
- `docs/examples/a2a-v1/transfer-mention-gated.json` (trigger por mention Discord + validação gated)

### `hyperliquid.cancel` / `hyperliquid.modify`

Exemplos:

- `docs/examples/a2a-v1/hyperliquid-cancel.json`
- `docs/examples/a2a-v1/hyperliquid-modify.json`

### `hyperliquid.deposit`

Move USDC da classe spot para margem perp dentro da conta Hyperliquid (usdClassTransfer).

Obrigatório:

- `asset` = `USDC`
- `amount`

Opcional:

- `toPerp` (default `true`; `false` retorna de perp -> spot)

Exemplo:

- `docs/examples/a2a-v1/hyperliquid-deposit.json`

---

## 4) Status de suporte (runtime atual)

### Live + dry-run

- `bridge` (quote/build + broadcast + tracking básico)
- `swap.jupiter`
- `swap.raydium`
- `swap.pumpfun`
- `defi.deposit` (adapter pattern, protocolo inicial: `aave-v3` Base)
- `defi.withdraw` (adapter pattern, protocolo inicial: `aave-v3` Base)
- `hyperliquid.spot.order`
- `hyperliquid.perp.order`
- `hyperliquid.cancel`
- `hyperliquid.modify`
- `hyperliquid.deposit`
- `transfer`
- `send`

---

## 5) Convenções de IDs

- `requestId`: único por mensagem recebida
- `correlationId`: ID de ciclo/estratégia no decision bot
- `idempotencyKey`: mesma chave para retry da mesma decisão econômica

Se `idempotencyKey` for omitida, o operador calcula hash determinístico da intent canônica + versão da policy.

---

## 6) Resposta padrão

Sucesso:

```json
{
  "ok": true,
  "runId": "run_...",
  "source": "execution_plane",
  "dryRun": true,
  "idempotencyKey": "...",
  "executionPlane": {
    "schemaVersion": "v1",
    "operation": "transfer",
    "requestId": "...",
    "correlationId": "...",
    "security": {
      "verified": true
    }
  },
  "plan": { "mode": "dry-run", "steps": [] },
  "result": {}
}
```

Erro:

```json
{
  "ok": false,
  "runId": "run_...",
  "source": "execution_plane",
  "error": {
    "code": "POLICY_RECIPIENT_DENIED",
    "message": "Destinatário fora da allowlist",
    "details": {}
  }
}
```

---

## 7) Códigos de erro (principais)

Schema/entrada:

- `EXECUTION_SCHEMA_INVALID`
- `EXECUTION_SCHEMA_ID_INVALID`
- `EXECUTION_SCHEMA_AMOUNT_INVALID`
- `EXECUTION_SCHEMA_ADDRESS_INVALID`

Perímetro A2A:

- `A2A_AUTH_REQUIRED`
- `A2A_KEY_UNKNOWN`
- `A2A_SIGNATURE_INVALID`
- `A2A_NONCE_REPLAY`
- `A2A_TIMESTAMP_WINDOW_EXCEEDED`

Policy/guardrails:

- `POLICY_CHAIN_DENIED`
- `POLICY_ASSET_DENIED`
- `POLICY_RECIPIENT_DENIED`
- `POLICY_BRIDGE_RECIPIENT_REQUIRED`
- `KEY_SEGREGATION_KEYS_MISSING`
- `CIRCUIT_BREAKER_OPEN`

Conectores:

- `JUPITER_*`
- `RAYDIUM_*`
- `PUMPFUN_*`
- `DEFI_*`
- `DEBRIDGE_*`

---

## 8) Versionamento

- Versão ativa: `v1`
- Breaking change: nova versão (`v2`) com janela de coexistência
- Adições backward-compatible em `v1`: apenas campos opcionais

---

## 9) Integração rápida

```bash
# dry-run
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --dry-run --policy config/policy.live.json

# live
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --policy config/policy.live.json
```

Replay de auditoria:

```bash
node src/cli.mjs replay --run-id <RUN_ID>
```
