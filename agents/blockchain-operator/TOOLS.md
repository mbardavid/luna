# TOOLS.md — Notas locais de operação

## RPC defaults (mainnet)

- Base: `https://mainnet.base.org` (rate-limited; trocar por provider dedicado em produção)
- Solana: `https://api.mainnet-beta.solana.com` (público; usar RPC dedicado em produção)
- Hyperliquid API: `https://api.hyperliquid.xyz`
- deBridge API: `https://dln.debridge.finance/v1.0`

## Segredos esperados (env)

- `BASE_PRIVATE_KEY`
- `BASE_RPC_URL`
- `SOLANA_PRIVATE_KEY_B58` ou `SOLANA_PRIVATE_KEY_JSON`
- `SOLANA_RPC_URL`
- `HYPERLIQUID_API_WALLET_PRIVATE_KEY` (futuro live)
- `HYPERLIQUID_ACCOUNT_ADDRESS` (futuro live)

## Integração operacional

- Canal Discord dedicado do operador: `1473392629055098942`
- Reporting esperado: mensagem por operação + resumo diário no mesmo canal

## Observação

Wallet extensions (MetaMask/Phantom) são ótimas para uso humano, mas para executor autônomo servidor-side use chaves dedicadas exportadas/segregadas com rotação.
