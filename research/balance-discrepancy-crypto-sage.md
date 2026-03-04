# Audit — Balance discrepancy (crypto-sage)

**Task:** entender por que o comando de saldo/portfolio retornou **muito abaixo** do esperado.

## 1) Reproduzir comando atual (output bruto)

### Antes do patch (estado inicial observado)
Comando:
```bash
node src/connectors/portfolio.mjs
```
Output (bruto):
```text
📊 Portfolio — 2026-03-03T21:14:29.455Z
   EVM Address: 0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1
   SOL Address: BYzZUKToZbkfe1snHFHSsTKMTb1SNmyCf8tGYTUgnbr

═══════════════════════════════════════════════════════════════════
  PORTFOLIO SUMMARY
═══════════════════════════════════════════════════════════════════

  Chain          Asset                         Qty    Price    Value (USD)
  ────────────────────────────────────────────────────────────────────
  Arbitrum One   ETH                      0.000008                        
                 USDC                       0.0453    $1.00          $0.05
                 USDC.e                       0.00    $1.00          $0.00
  Polygon PoS    POL                          4.98                        
                 USDC                       0.5662    $1.00          $0.57
                 USDC.e                      13.58    $1.00         $13.58
  Base           ETH                          0.00                        
                 USDC                         0.00    $1.00          $0.00
  Solana         SOL                          2.03                        
                 USDC                         1.99    $1.00          $1.99
  Hyperliquid    Perp USDC                    0.00                        
                 Spot USDC                    0.00    $1.00          $0.00
  ────────────────────────────────────────────────────────────────────
  Polymarket     PM:Iran Regime Jun30       0.0659    $0.39          $0.03
  Polymarket     PM:Iran Regime Jun30       0.0300    $0.62          $0.02
  Polymarket     PM:Iran Hormuz Mar31       0.0229    $0.46          $0.01
  Polymarket     PM:Iran Hormuz Mar31       0.0826    $0.56          $0.05

  📊 Polymarket PnL:
     Unrealized: -$0.007249
     Realized:   +$0.00
     Total:      -$0.007249

  ────────────────────────────────────────────────────────────────────
  💰 Total Portfolio: $16.28
```

### Após patch local (valoração de nativos via Coingecko)
Comando:
```bash
node src/connectors/portfolio.mjs
```
Output (bruto):
```text
📊 Portfolio — 2026-03-03T21:16:36.004Z
   EVM Address: 0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1
   SOL Address: BYzZUKToZbkfe1snHFHSsTKMTb1SNmyCf8tGYTUgnbr

═══════════════════════════════════════════════════════════════════
  PORTFOLIO SUMMARY
═══════════════════════════════════════════════════════════════════

  Chain          Asset                         Qty    Price    Value (USD)
  ────────────────────────────────────────────────────────────────────
  Arbitrum One   ETH                      0.000008 $1967.42          $0.02
                 USDC                       0.0453    $1.00          $0.05
                 USDC.e                       0.00    $1.00          $0.00
  Polygon PoS    POL                          4.98    $0.10          $0.50
                 USDC                       0.5662    $1.00          $0.57
                 USDC.e                      13.58    $1.00         $13.58
  Base           ETH                          0.00 $1967.42          $0.00
                 USDC                         0.00    $1.00          $0.00
  Solana         SOL                          2.03   $84.51        $171.39
                 USDC                         1.99    $1.00          $1.99
  Hyperliquid    Perp USDC                    0.00                        
                 Spot USDC                    0.00    $1.00          $0.00
  ────────────────────────────────────────────────────────────────────
  Polymarket     PM:Iran Regime Jun30       0.0659    $0.39          $0.03
  Polymarket     PM:Iran Regime Jun30       0.0300    $0.62          $0.02
  Polymarket     PM:Iran Hormuz Mar31       0.0229    $0.46          $0.01
  Polymarket     PM:Iran Hormuz Mar31       0.0826    $0.55          $0.05

  📊 Polymarket PnL:
     Unrealized: -$0.008075
     Realized:   +$0.00
     Total:      -$0.008075

  ────────────────────────────────────────────────────────────────────
  💰 Total Portfolio: $188.18
```

## 2) Consolidação por chain/token (números)

Fonte: `node src/connectors/portfolio.mjs --json` (2026-03-03 ~21:14 UTC).

### Arbitrum
- ETH: **0.0000084937**
- USDC (native): **0.045281**
- USDC.e: **0**

### Polygon
- POL: **4.9818331357**
- USDC (native): **0.566237**
- USDC.e: **13.577596**

### Base
- ETH: **0**
- USDC: **0**

### Solana
- SOL: **2.027991091**
- USDC: **1.991439**

## 3) Polymarket (collateral / conditional tokens / locked funds)

### O que o conector faz hoje
- Lê **apenas** balances de *Conditional Tokens (ERC-1155)* no contrato `ConditionalTokens` em Polygon.
- **Não** descobre posições automaticamente: depende de `POLYMARKET_POSITIONS_JSON` (lista manual de token_ids).
- Faz preço por token via `https://clob.polymarket.com/price?token_id=...` (público).

### Resultado observado
- Positions ERC-1155 encontradas (apenas as configuradas no `.env`): valores ~**$0.10** total.
- **Collateral USDC no Polymarket CLOB não é incluído.**
- **Funds locked em open orders também não são incluídos** (e não há integração atual com endpoints autenticados do CLOB para isso).

**Conclusão:** mesmo que exista USDC depositado no Polymarket (collateral), o comando atual **vai reportar quase zero** para Polymarket, exceto pelas poucas posições ERC-1155 explicitamente listadas.

## 4) Hyperliquid (account value/margin/open orders/positions)

### O que o conector faz hoje
- Consulta:
  - `clearinghouseState` (perp)
  - `spotClearinghouseState` (spot)
- Exibe:
  - `accountValue` (Perp USDC)
  - `Spot USDC total` e `hold` (em `extra`)

### Resultado observado
- `accountValue` = **0**
- `totalMarginUsed` = **0**
- `withdrawable` = **0**
- `spot total` = **0**, `hold` = **0**
- `perpPositions` = `[]`

**Nota:** o conector *já* tem os campos para “locked funds” em spot via `hold`. Para perp, o valor em `accountValue` já inclui PnL/posições, e `totalMarginUsed` reflete margem alocada.

## 5) Diagnóstico (root cause + fix)

### Root causes da discrepância
1) **Valoração USD ignorava ativos nativos (ETH/SOL/POL).**
   - O total era somado apenas para stablecoins (USDC/USDC.e/USDT/DAI via `isStablecoin`) + `Hyperliquid accountValue`.
   - Isso explica um salto de **$16.28 → ~$188.18** ao adicionar preço para SOL/POL/ETH.

2) **Polymarket: conector não inclui collateral USDC nem descobre posições automaticamente.**
   - Só lê ERC-1155 de `POLYMARKET_POSITIONS_JSON`.
   - Se a maior parte do “saldo esperado” estava como **USDC depositado** (ou preso em ordens) no Polymarket, ele não aparece.

3) (Possível) **Tokens fora da allowlist do conector EVM não aparecem.**
   - `evm-balance.mjs` consulta só USDC/USDC.e por chain. Qualquer WETH, WMATIC/POL wrappers, LP tokens etc. seriam ignorados.

### Correção proposta

#### Patch (feito) — incluir preço USD para nativos
Arquivo alterado:
- `src/connectors/portfolio.mjs`

Mudança:
- Busca preços USD (ETH/SOL/POL) via Coingecko `simple/price`.
- Mostra Price/Value para nativos e soma ao total.

Isso resolve o “under-reporting” mais óbvio quando há SOL/POL/ETH significativos.

#### Próximos passos recomendados (para fechar o gap “de verdade”)
1) **Polymarket collateral + locked funds**
   - Implementar integração com endpoints autenticados do Polymarket CLOB para obter:
     - `collateral balance`
     - `funds locked in open orders`
   - Exigir novas env vars (a definir de acordo com o auth do CLOB):
     - `POLYMARKET_CLOB_API_KEY`, `POLYMARKET_CLOB_SECRET`, `POLYMARKET_CLOB_PASSPHRASE` (ou equivalente)
   - E/ou (alternativa on-chain): descobrir se há contrato com método `balanceOf(user)` para USDC collateral creditado (depende do design atual da Polymarket).

2) **Descoberta automática de posições ERC-1155**
   - Hoje é manual (`POLYMARKET_POSITIONS_JSON`).
   - Ideal: indexar via subgraph/event logs para listar todos tokenIds com balance > 0.

3) **Expandir tokens monitorados por chain**
   - Adicionar allowlist configurável por env para outros ERC-20 relevantes.

---

## Apêndice — Env relevante
`POLYMARKET_POSITIONS_JSON` no `.env` contém só 4 token_ids (Iran Regime / Hormuz), portanto o comando só reporta esses.
