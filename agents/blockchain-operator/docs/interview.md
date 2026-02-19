# Entrevista objetiva (status atualizado)

## Decisões já confirmadas

1. **Discord**: `1473392629055098942`
2. **Chaves**: segregação obrigatória (1 para Base, 1 para Solana, 1 para Hyperliquid)
3. **Hyperliquid**: tratado como L3 de destino operacional
4. **Bridge**: source apenas Base/Solana; provider deBridge
5. **Saques**: somente para wallets whitelisted (`allowlists.recipients`)
6. **Limites numéricos**: desativados por ora (`limits.* = null`)
7. **Price source**: Chainlink primário + fallback Pyth
8. **Circuit breaker**: auto-retry após cooldown
9. **Reporting**: por operação + resumo diário no canal Discord dedicado

## Lacunas restantes (objetivas)

1. **Recipients finais de produção**: quais endereços entram na allowlist?
2. **Symbols/ativos finais de produção**: quais entram no dia 1?
3. **Horário do resumo diário**: manter `23:00 UTC` ou ajustar?
