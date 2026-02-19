# Segurança e Guardrails para autonomia

## Threat model curto (atualizado 2026-02-18)

### Superfícies principais

1. **Payload A2A adulterado ou replay**
   - risco: execução não autorizada / duplicada
2. **Comprometimento de hot wallet**
   - risco: drenagem de fundos
3. **Bypass de allowlists/recipient policy**
   - risco: envio para destino não autorizado
4. **Falha de integração com DEX/bridge/protocol adapter**
   - risco: execução incorreta ou estado parcial
5. **Cascata de falhas de API/RPC**
   - risco: retries perigosos e perda operacional

## Controles implementados

1. **Mainnet-only enforcement** (policy)
2. **Allowlists** (chains/assets/recipients/bridgeRoutes/symbols)
3. **Recipient policy** em operações de saída e em recipients explícitos
4. **Segregação obrigatória de chaves** (Base/Arbitrum, Solana, Hyperliquid API wallet)
5. **Idempotência** por intent canônica + policy version
6. **Circuit breaker** por janela/cooldown
7. **Auditoria append-only** por run (`state/audit.jsonl`)
8. **Erros explícitos por conector** (`JUPITER_*`, `RAYDIUM_*`, `PUMPFUN_*`, `DEFI_*`, `DEBRIDGE_*`)
9. **Perímetro A2A com anti-replay**:
   - `hmac-sha256-v1`
   - `nonce` + `timestamp window`
   - store de nonce com TTL
   - fallback seguro para payload unsigned em live

## A2A anti-replay (implementação)

- arquivo de estado: `state/a2a-nonce-store.json`
- lock: `state/a2a-nonce.lock`
- janela padrão:
  - `A2A_MAX_SKEW_MS=120000`
  - `A2A_NONCE_TTL_MS=300000`
- assinatura:
  - HMAC-SHA256 sobre payload sem `auth.signature`
  - serialização determinística (`stableStringify`)

## Defaults operacionais recomendados

- `A2A_SECURITY_MODE=enforce`
- `A2A_ALLOW_UNSIGNED_LIVE=false`
- rotação periódica de `A2A_HMAC_KEYS_JSON`
- revisão frequente de allowlists mínimas

## Riscos remanescentes

1. Adapter DeFi multi-protocolo ainda inicial (Aave Base primeiro)
2. Tracking de bridge ainda básico (polling best-effort)
3. Dependência de disponibilidade de APIs de terceiros (DEX/bridge)
4. deBridge não aciona Bridge2/withdraw3 do Hyperliquid automaticamente (rotas diretas com HL são bloqueadas explicitamente)
