# Arquitetura — Crypto Sage Dual-Plane

## Objetivo

Separar de forma explícita:

1. **Control Plane**: entrada em linguagem natural (PT/EN) para uso humano.
2. **Execution Plane**: entrada estruturada JSON para integração bot->bot com baixa latência.

---

## Plane 1: Control Plane (humano -> bot)

Fluxo:

`NL PT/EN -> parser -> normalização -> policy -> planner -> conector -> auditoria`

Características:

- parser determinístico (regex/gramática controlada)
- tolera linguagem humana, mas com sintaxe explícita
- útil para operação assistida/manual

---

## Plane 2: Execution Plane (bot -> bot)

Fluxo:

`JSON v1 -> validação schema por operação -> intent canônica -> policy -> conector -> auditoria`

Características:

- **sem parsing NL**
- validação forte (envelope + schema específico por `operation`)
- caminho curto para latência menor
- compatível com idempotência, breaker e replay

CLI path dedicado:

- `node src/cli.mjs execute-plane --payload-file <file.json>`
- `node src/cli.mjs execute-plane --payload '<json>'`
- `cat payload.json | node src/cli.mjs execute-plane --stdin`

---

## Componentes

- `src/core/parser.mjs` — Control Plane NL parser
- `src/core/native-command-router.mjs` — roteamento de comandos nativos/slash para a mesma intent canônica
- `src/core/execution-plane.mjs` — Execution Plane validator + mapper (A2A -> intent canônica)
- `src/core/normalize.mjs` — normalização de intent NL
- `src/core/policy-engine.mjs` — guardrails globais
- `src/core/planner.mjs` — plano determinístico
- `src/core/executor.mjs` — orquestra execução (ambos planes)
- `src/core/mention-delegation-gate.mjs` — gate de mention delegation (`gated`) com anti-loop/dedupe/TTL
- `src/core/audit-log.mjs` — trilha de auditoria
- `src/core/idempotency-store.mjs` — dedupe
- `src/core/circuit-breaker.mjs` — proteção operacional

Conectores atuais:

- `base` (viem)
- `arbitrum` (viem + ERC20 transfer helpers)
- `solana` (`@solana/web3.js`)
- `hyperliquid` (spot/perp + cancel/modify + withdraw3)
- `hyperliquid_bridge` (pipeline nativo Arbitrum <-> Hyperliquid)
- `jupiter` (swap)
- `raydium` (swap)
- `pumpfun` (trade)
- `defi` (adapter pattern por protocolo; inicial `aave-v3` Base)
- `debridge` (quote/build + broadcast + tracking básico)

---

## Contrato A2A v1

Schemas documentados em:

- `config/execution-plane.schema.v1.json`
- `docs/a2a-protocol.md`

Operações modeladas:

- `bridge`
- `swap.jupiter`
- `swap.raydium`
- `swap.pumpfun`
- `defi.deposit`
- `defi.withdraw`
- `hyperliquid.spot.order`
- `hyperliquid.perp.order`
- `transfer`
- `send`
- `hyperliquid.cancel`
- `hyperliquid.modify`
- `hyperliquid.deposit` (usdClassTransfer interno)
- `hyperliquid.bridge.deposit` (Arbitrum -> HL Bridge2)
- `hyperliquid.bridge.withdraw` (HL withdraw3 -> Arbitrum)

---

## Segurança preservada no dual-plane

- Mainnet-only obrigatório
- Recipient allowlist continua aplicada em saques/saídas
- Política de chains/assets/contracts/symbols ativa em ambos planes
- Idempotência e circuit breaker ativos em ambos planes
- Segregação de chaves obrigatória em live

---

## Status de execução por operação

Live implementado:

- transfer/send nativo (Base/ETH + Solana/SOL)
- Hyperliquid spot/perp order
- Hyperliquid cancel/modify
- swap Jupiter/Raydium/Pump.fun
- DeFi deposit/withdraw (adapter pattern)
- bridge deBridge end-to-end (source tx + tracking básico)
- Hyperliquid native bridge:
  - Arbitrum -> HL deposit (Bridge2)
  - HL -> Arbitrum withdraw (withdraw3)

Rotas deBridge que tocam `hyperliquid` diretamente retornam `DEBRIDGE_HYPERLIQUID_ROUTE_NOT_SUPPORTED` com pipeline recomendado (via Arbitrum native bridge).

Design notes + trust boundaries por rota:

- `docs/pipelines-hl-multiroute.md`

Security perimeter A2A:

- assinatura HMAC (`hmac-sha256-v1`)
- nonce/timestamp anti-replay
- fallback seguro para live unsigned
- mention delegation gated para Discord (`@bot`) com anti-loop (`originBotId != targetBotId`), dedupe por `messageId` e TTL
