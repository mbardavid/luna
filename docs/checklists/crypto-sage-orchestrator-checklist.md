# Checklist Operacional — Novo Agente Orquestrador do Crypto-Sage

Use este checklist sempre que criar/onboard um novo agente que delega execução ao **crypto-sage** (`agents/blockchain-operator`).

## 0) Pré-requisitos

- [ ] Leu `docs/agent-standard.md`
- [ ] Leu `docs/agent-orchestration-a2a.md`
- [ ] Leu `agents/blockchain-operator/docs/a2a-protocol.md`
- [ ] Confirmou modo inicial em `dev` (nunca iniciar direto em `live`)

## 1) Contrato de handoff (TaskSpec)

- [ ] Todo handoff usa TaskSpec `1.0`
- [ ] `handoffId` único e `correlationId` fim-a-fim
- [ ] `routing.routeKey` preenchido e registrado em `memory/orchestration-state.json`
- [ ] `intent.operation` compatível com operações suportadas pelo crypto-sage
- [ ] `intent.inputSchemaRef` aponta para schema versionado existente
- [ ] `audit.requestId` e `audit.idempotencyKey` definidos
- [ ] Se trigger vier de mention Discord: `authorship.mentionDelegationMode=gated` documentado no TaskSpec

## 2) Roteamento e estado único

- [ ] Estado único em `memory/orchestration-state.json` (sem fonte paralela)
- [ ] `routingTable` possui rota primária para `crypto-sage.execution-plane.v1`
- [ ] fallback definido para capability crítica (quando aplicável)
- [ ] cada transição atualiza `updatedAt` e status do handoff
- [ ] Se houver mention gated: dedupe por `messageId` e validade de TTL são observáveis por auditoria/estado

## 3) Segurança e anti-self-test

- [ ] `safety.e2eActor` = `human` ou `authorized-harness`
- [ ] Não há autoaprovação de E2E por agente autônomo
- [ ] Em modo `live`, aprovação humana explícita está registrada
- [ ] Perímetro A2A respeitado (`A2A_*` sem bypass)

## 4) Modo `delegated-human-proxy` (quando aplicável)

- [ ] `authorship.mode=delegated-human-proxy`
- [ ] Policy explícita com allowlist (`allowAgents`, `allowChannels`, `allowTasks`)
- [ ] Envelope de autoria completo (`principal`, `delegateAgent`, `scope`, `ttl`, `proof.signature/hash`)
- [ ] `ttl.expiresAt` ainda válido no momento da execução
- [ ] **Risk gate**:
  - [ ] `read/diagnostic` permitido somente após validação policy+envelope
  - [ ] `sensitive/live` exige `authorizationRef` válido + confirmação/aprovação aplicável
- [ ] Mention trigger em Discord tratado como **trigger**, não como autorização de execução
- [ ] `mentionDelegationMode=gated` exige contexto `authorship.mentionDelegation` com:
  - [ ] `channel`
  - [ ] `messageId`
  - [ ] `originBotId`
  - [ ] `targetBotId`
  - [ ] `observedAt`
  - [ ] `ttlSeconds`
  - [ ] `dedupeBy=messageId`
- [ ] Anti-loop obrigatório: `originBotId != targetBotId`
- [ ] Dedupe obrigatório por `messageId` dentro da janela TTL
- [ ] Auditoria de delegação registrada em:
  - [ ] `audit.delegation` no TaskSpec/execução
  - [ ] `memory/orchestration-state.json.delegationAuditLog`

## 5) Gates de promoção

- [ ] **G0** (contract gate) passou
- [ ] **G1** (routing gate) passou
- [ ] **G2** (simulation gate) passou
- [ ] **G3** (authorized E2E gate) passou
- [ ] **G4** (live gate) aprovado por humano
- [ ] **D4** (mention gate), quando aplicável, passou: anti-loop + dedupe + TTL

## 6) Rollback obrigatório

- [ ] `rollback.planRef` definido no TaskSpec
- [ ] gatilhos de rollback documentados
- [ ] procedimento de demote para `simulated` testado
- [ ] última execução de rollback (ou simulação) registrada no estado

## 7) Critérios de aceite para onboarding

- [ ] fluxo dry-run completo sem erro de schema/rota/policy
- [ ] correlação (`correlationId`) preservada na resposta do crypto-sage
- [ ] runbook de incidente conhecido pelo time
- [ ] operação liberada apenas no stage aprovado
- [ ] rollout segue as 3 fases: dry-run/log-only -> gated-read -> gated-live

## 8) Operações típicas a validar primeiro (ordem sugerida)

1. [ ] `transfer` (dry-run)
2. [ ] `swap.jupiter` (simulado)
3. [ ] `defi.deposit` (simulado)
4. [ ] canário live de baixo risco (com aprovação humana)

## 9) Rollout por fases para mention delegation

1. [ ] **dry-run / log-only**: somente auditoria e métricas, sem execução por mention
2. [ ] **gated-read**: mention gated liberado apenas para `read/diagnostic`
3. [ ] **gated-live**: mention gated para `sensitive/live` só com autorização válida + aprovação humana
