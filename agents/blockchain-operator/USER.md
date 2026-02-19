# USER.md — Perfil esperado do solicitante

## Papel do usuário

- Usuário humano ou agente decisor envia intenção de execução.
- Este agente **não** decide alocação nem tese.

## Preferências confirmadas

- Mainnet only
- Execução autônoma (sem confirmação humana por padrão)
- PT + EN
- Chains iniciais: Solana, Base, Hyperliquid
- Operações iniciais: spot + perp
- Segregação obrigatória de chaves: 1 para Base, 1 para Solana, 1 para Hyperliquid
- Hyperliquid tratado como L3 de destino operacional
- Bridges iniciam apenas de Base/Solana
- Saques apenas para wallets whitelisted
- Sem limites numéricos ativos por ora
- Price source: Chainlink (primário) com fallback Pyth
- Bridge provider: deBridge
- Circuit breaker com auto-retry após cooldown
- Reporting por operação + resumo diário

## Canal operacional confirmado

- Discord channel ID (agente operador): `1473392629055098942`

## Lacunas em aberto

- Endereços finais de recipients whitelisted (produção)
- Allowlists finais de ativos/símbolos por ambiente (produção)
