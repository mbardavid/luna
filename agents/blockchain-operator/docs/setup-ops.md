# Setup e Operações — Crypto Sage Dual-Plane

## Pré-requisitos

- Node.js >= 22
- NPM
- Python 3.10+
- RPC/API mainnet para Base/Solana/Hyperliquid
- Chaves segregadas por domínio de execução

## Instalação

```bash
cd agents/blockchain-operator
npm install
python3 -m pip install -r requirements-hyperliquid.txt
cp .env.example .env
cp config/policy.live.example.json config/policy.live.json
```

## Variáveis de ambiente

### Base

- `BASE_PRIVATE_KEY`
- `BASE_RPC_URL`
- `BASE_ACCOUNT_ADDRESS` (opcional; usado para consulta read-only de `/saldo` sem chave)
- `BASE_BALANCE_TOKEN_ADDRESSES_JSON` (opcional; lista JSON de ERC20 adicionais para monitorar no `/saldo`)

### Solana

- `SOLANA_PRIVATE_KEY_B58` **ou** `SOLANA_PRIVATE_KEY_JSON`
- `SOLANA_RPC_URL`
- `SOLANA_ACCOUNT_ADDRESS` (opcional; usado para consulta read-only de `/saldo` sem chave)

### Hyperliquid

- `HYPERLIQUID_API_URL`
- `HYPERLIQUID_ACCOUNT_ADDRESS`
- `HYPERLIQUID_API_WALLET_PRIVATE_KEY`
- `HYPERLIQUID_VAULT_ADDRESS` (opcional)
- `HYPERLIQUID_PYTHON_BIN` (default `python3`)
- `HYPERLIQUID_BRIDGE_TIMEOUT_MS`
- `HYPERLIQUID_EXPIRES_AFTER_MS`
- `HYPERLIQUID_NONCE_LOCK_TIMEOUT_MS`
- `HYPERLIQUID_NONCE_LOCK_STALE_MS`

### Jupiter / Raydium / Pump.fun

- `JUPITER_API_URL` (default: `https://lite-api.jup.ag`)
- `JUPITER_RETRY_ATTEMPTS` (default: `3`)
- `JUPITER_RETRY_BASE_MS` (default: `400`)
- `RAYDIUM_API_URL`
- `PUMPFUN_API_URL`
- `PUMPFUN_TRADE_API_URL`
- `PUMPFUN_TRADE_API_KEY` (se aplicável)
- `PUMPFUN_PRIORITY_FEE_SOL`
- `PUMPFUN_SYMBOL_MAP_JSON` (fallback symbol->mint)

> Nota: `quote-api.jup.ag` pode não resolver em alguns ambientes (DNS/egress). Prefira `lite-api.jup.ag`.

### DeFi adapters (protocol pattern)

- `AAVE_V3_BASE_POOL_ADDRESS`

### deBridge

- `DEBRIDGE_API_URL`
- `DEBRIDGE_TRACK_POLL_ATTEMPTS`
- `DEBRIDGE_TRACK_POLL_INTERVAL_MS`

### A2A perímetro / anti-replay

- `A2A_SECURITY_MODE` (`permissive|enforce|disabled`)
- `A2A_HMAC_KEYS_JSON` (map `keyId -> secret`)
- `A2A_ALLOW_UNSIGNED_LIVE` (default `false`)
- `A2A_MAX_SKEW_MS`
- `A2A_NONCE_TTL_MS`
- `A2A_LOCK_TIMEOUT_MS`
- `A2A_LOCK_STALE_MS`

### Market data para `/saldo`

- `PRICE_MAX_AGE_MS` (default: `172800000` = 48h)
- `PRICE_RPC_RETRIES` (default: `3`)
- `PYTH_HERMES_URL` (default: `https://hermes.pyth.network`)

### Integração operacional

- `OPERATOR_DISCORD_CHANNEL_ID` (default: `1473392629055098942`)

---

## Operação prática

### Control Plane

```bash
node src/cli.mjs plan --instruction "buy 0.001 BTC perp at market on hyperliquid" --policy config/policy.live.json
node src/cli.mjs execute --instruction "buy 0.001 BTC perp at market on hyperliquid" --policy config/policy.live.json --dry-run
node src/cli.mjs execute --instruction "/saldo" --policy config/policy.live.json --dry-run
```

### Execution Plane (A2A)

```bash
# operação específica
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --dry-run --policy config/policy.live.json

# bateria completa
npm run dry-run:a2a
```

Contrato A2A:

- `docs/a2a-protocol.md`
- `config/execution-plane.schema.v1.json`

---

## Estado de runtime

- `state/audit.jsonl`
- `state/idempotency.json`
- `state/circuit-breaker.json`
- `state/hyperliquid-nonce.json`
- `state/a2a-nonce-store.json`

## Observabilidade mínima

- sucesso/falha por operação e por plane
- latência média por conector
- erros A2A (`A2A_*`)
- eventos de breaker aberto
- colisões de nonce (target: 0)

---

## Troubleshooting — Jupiter

Se aparecer `JUPITER_PREFLIGHT_FAILED` com `fetch failed`:

1. Validar DNS/egress do host:
   - `node -e "require('node:dns').promises.lookup('lite-api.jup.ag').then(console.log).catch(console.error)"`
2. Validar quote endpoint:
   - `node -e "fetch('https://lite-api.jup.ag/swap/v1/quote?inputMint=So11111111111111111111111111111111111111112&outputMint=EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v&amount=1000000&slippageBps=100&swapMode=ExactIn').then(async r=>{console.log(r.status); console.log((await r.text()).slice(0,120));}).catch(e=>console.error(e.message))"`
3. Confirmar `JUPITER_API_URL=https://lite-api.jup.ag`.
4. O executor agora faz fallback automático para Raydium em falhas transitórias de rede da Jupiter.

## Notas operacionais

1. Segregação de chaves é mandatória em live.
2. Em produção, usar `A2A_SECURITY_MODE=enforce`.
3. `allowlists.recipients` deve conter apenas destinos explícitos aprovados.
4. Validar sempre dry-run antes de qualquer alteração de policy.
