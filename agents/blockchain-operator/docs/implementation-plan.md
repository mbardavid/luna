# Plano de implementação — Dual-Plane + A2A

## Fase A — Foundation (concluída)

Entregas:

- parser/normalize para Control Plane
- policy engine
- planner + executor
- audit/idempotency/breaker
- conectores Base/Solana/Hyperliquid

Critérios:

- [x] `plan` e `execute --dry-run` funcionais
- [x] replay por `runId`
- [x] guardrails ativos

---

## Fase B — Execution Plane v1 (concluída)

Entregas:

- path dedicado `execute-plane`
- validação forte por operação (schema v1)
- compatibilidade com auditoria, breaker e idempotência
- documentação A2A para decision bots

Critérios:

- [x] path curto `schema -> policy -> connector`
- [x] contrato v1 versionado
- [x] erro explícito por validação/policy/execução

---

## Fase C — Conectores pendentes (concluída neste ciclo)

Entregas:

- Jupiter live connector (swap)
- Raydium live connector (swap)
- Pump.fun live connector (trade)
- DeFi deposit connector com adapter por protocolo
- DeFi withdraw connector com adapter por protocolo
- deBridge live end-to-end (broadcast + tracking básico)

Critérios:

- [x] dry-run + live por conector
- [x] tratamento explícito de erro por conector
- [x] integração total ao schema A2A v1
- [x] guardrails preservados (mainnet/allowlists/recipient/key segregation)

---

## Fase D — Segurança A2A (concluída neste ciclo)

Entregas:

- assinatura/verificação HMAC (`hmac-sha256-v1`)
- nonce store + timestamp window anti-replay
- fallback seguro para payload unsigned em live
- atualização de threat model + runbook hardening

Critérios:

- [x] `A2A_*` error mapping
- [x] testes de assinatura válida e replay bloqueado
- [x] documentação operacional atualizada

---

## Fase E — Próxima iteração

- adapters DeFi multi-protocolo/multi-chain (além de Aave Base)
- tracking bridge com reconciliação de settlement final
- telemetria e alerting de produção
