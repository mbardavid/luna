# Backlog priorizado — Crypto Sage

## Fase 0 — Foundation (concluída)

- [x] Estrutura base do agente
- [x] Parser PT/EN para Control Plane
- [x] Policy engine com guardrails
- [x] Auditoria + idempotência + replay + breaker
- [x] Transfer nativa Base/Solana
- [x] Hyperliquid live (spot/perp + cancel/modify + nonce)

## Fase 1 — Dual-plane (concluída)

- [x] `execute-plane` dedicado
- [x] Schema A2A v1 por operação
- [x] Mapeamento execution payload -> intent canônica
- [x] Integração com idempotência/breaker/auditoria

## Fase 2 — Connectors live (concluída neste ciclo)

- [x] Jupiter swap live
- [x] Raydium swap live
- [x] Pump.fun trade live
- [x] DeFi deposit live (adapter pattern por protocolo)
- [x] DeFi withdraw live (adapter pattern por protocolo)
- [x] Bridge deBridge end-to-end (quote/build + broadcast + tracking básico)

## Fase 3 — Segurança A2A / hardening (concluída neste ciclo)

- [x] Perímetro A2A com assinatura HMAC opcional/enforce
- [x] Anti-replay por nonce + janela de timestamp
- [x] Fallback seguro para live sem assinatura
- [x] Checklist de hardening no runbook
- [x] Threat model atualizado

## Fase 4 — Próximos incrementos

- [ ] Expandir adapters DeFi para Solana (ex.: Kamino/Marinade)
- [ ] Tracking avançado de bridge com estado cross-chain final
- [ ] Métricas Prometheus + alertas por operação
- [ ] Retries com jitter e failover multi-RPC
- [ ] Rotação automática de chaves A2A + versão assinada de policy/schema
