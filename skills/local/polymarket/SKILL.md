---
name: polymarket
description: Operate Polymarket safely using official CLOB docs (read market data, prepare orders, and execute authenticated trading flows).
---

# Polymarket Skill (Official-doc based)

Use this skill when the user asks to:
- consultar mercados/eventos/preços da Polymarket,
- preparar ou executar ordens (buy/sell/cancel),
- revisar autenticação CLOB (L1/L2) e configuração de conta,
- depurar erros de integração da Polymarket.

## Canonical sources (official)
- Docs index: `https://docs.polymarket.com/llms.txt`
- Trading overview: `https://docs.polymarket.com/trading/overview`
- Trading quickstart: `https://docs.polymarket.com/trading/quickstart`
- Authentication: `https://docs.polymarket.com/api-reference/authentication`
- Clients & SDKs: `https://docs.polymarket.com/api-reference/clients-sdks`

## Core protocol facts (must follow)
1. CLOB host: `https://clob.polymarket.com`.
2. Trading chain: Polygon (`chain_id=137`).
3. Two auth levels:
   - **L1**: EIP-712 signature with wallet private key (create/derive API creds).
   - **L2**: HMAC headers using `apiKey/secret/passphrase`.
4. Even with L2 headers, order payloads still need wallet signing.
5. Signature types:
   - `0` EOA
   - `1` POLY_PROXY
   - `2` GNOSIS_SAFE

## Execution policy
- Prefer official SDKs over raw REST:
  - TypeScript: `@polymarket/clob-client` (+ `ethers@5`)
  - Python: `py-clob-client`
- Use raw REST only for diagnostics or when explicitly requested.
- For live trading, verify geoblock/risk constraints before sending orders.
- Never log or expose private keys, api secret, or passphrase.

## Standard runbook
1. Validate environment (host, chain_id, wallet type, funder).
2. Create/derive L2 credentials via L1.
3. Read market metadata (token IDs, tick size, negRisk).
4. Build order with explicit side/price/size/order type.
5. Submit order; return orderID + status.
6. Provide follow-up actions (open orders, cancel, trades).

## Required response shape for ops tasks
When asked to execute, always return:
- intent summary,
- exact operation attempted,
- success/failure with error code/message,
- next actionable step.

## Common errors checklist
- `INVALID_SIGNATURE`: wrong PK / signer mismatch.
- `L2 AUTH NOT AVAILABLE`: wrong signature type or funder.
- insufficient balance/allowance: no USDC.e / missing approval.
- geoblock/cloudflare restrictions.

## Do-not-do list
- Don’t invent unsupported endpoints.
- Don’t skip signature type/funder validation.
- Don’t claim order executed without order ID evidence.
