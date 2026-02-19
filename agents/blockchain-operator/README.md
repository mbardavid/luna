# Crypto Sage — Blockchain Operator (Dual-Plane)

Executor de operações on-chain/DEX com dois fluxos fixos:

1. **Control Plane** → humano para bot (NL PT/EN)
2. **Execution Plane** → bot para bot (JSON estruturado, baixa latência, sem parsing NL)

## Status atual

- ✅ Control Plane (parser PT/EN + normalização + policy + execução)
- ✅ Execution Plane v1 (schema por operação + validação forte + path dedicado no CLI)
- ✅ Auditoria + replay + idempotência + circuit breaker
- ✅ Execução live:
  - transfer/send nativo (Base/ETH, Solana/SOL)
  - Hyperliquid (spot/perp + cancel + modify)
  - Jupiter swap
  - Raydium swap
  - Pump.fun trade
  - DeFi deposit/withdraw (adapter pattern, protocolo inicial Aave v3 Base)
  - deBridge bridge (quote/build + broadcast + tracking básico)
- ✅ Perímetro A2A: assinatura/verificação HMAC + anti-replay nonce/timestamp

## Estrutura

- `src/` código principal
- `config/` policies + schemas (`policy.schema.json`, `execution-plane.schema.v1.json`)
- `docs/` arquitetura, runbook, A2A protocol, setup
- `docs/examples/a2a-v1/` exemplos prontos de payload
- `state/` audit log, idempotência, breaker, nonce

## Setup rápido

```bash
cd agents/blockchain-operator
npm install
python3 -m pip install -r requirements-hyperliquid.txt
cp .env.example .env
cp config/policy.live.example.json config/policy.live.json
```

## Comandos

### Control Plane

```bash
# plan
node src/cli.mjs plan --instruction "enviar 0.001 ETH para 0x000000000000000000000000000000000000dEaD na base"

# dry-run
node src/cli.mjs execute --instruction "send 0.001 ETH to 0x000000000000000000000000000000000000dEaD on base" --dry-run

# saldo consolidado (Base + Solana + Hyperliquid)
node src/cli.mjs execute --instruction "/saldo" --dry-run

# simulação da rota nativa de slash command (/saldo)
node src/cli.mjs execute-native --command saldo --dry-run
```

### Execution Plane

```bash
# via arquivo
node src/cli.mjs execute-plane --payload-file docs/examples/a2a-v1/swap-jupiter.json --dry-run --policy config/policy.live.json

# via stdin
cat docs/examples/a2a-v1/bridge.json | node src/cli.mjs execute-plane --stdin --policy config/policy.live.json

# bateria dry-run A2A v1
npm run dry-run:a2a
```

### Replay

```bash
node src/cli.mjs replay --run-id <RUN_ID>
```

## Guardrails mandatórios

- Mainnet-only
- Allowlists (chains/assets/recipients/rotas)
- Recipient policy para operações de saída
- Segregação obrigatória de chaves (Base/Solana/Hyperliquid)
- Idempotência + circuit breaker
- Auditoria completa por execução

## Segurança A2A

Ver `docs/a2a-protocol.md` e `docs/runbook.md` para:

- assinatura HMAC (`hmac-sha256-v1`)
- nonce/timestamp window anti-replay
- fallback seguro para live sem assinatura

## Observação

Nunca commitar `.env` ou chaves privadas.
