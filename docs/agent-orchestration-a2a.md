# Padrão Interno Obrigatório — A2A de Orquestração

**Status:** ✅ Obrigatório para qualquer handoff agente→agente neste workspace  
**Objetivo:** reduzir falhas de validação, erro de roteamento e uso indevido de delegação em fluxos multiagente.

## 1) Escopo

Este padrão se aplica quando um agente:

- delega tarefa para outro agente;
- recebe tarefa de outro agente;
- promove execução de `dev`/`simulado` para `live`;
- responde outro bot em nome do usuário via modo `delegated-human-proxy`;
- recebe trigger por mention (`@bot`) em canal Discord para iniciar handoff A2A.

Se **não há handoff**, este documento é apenas referência.

---

## 2) Artefatos obrigatórios

1. **Contrato TaskSpec obrigatório** para cada handoff.
2. **Arquivo único de estado de orquestração** em:
   - `memory/orchestration-state.json`
3. **Validação por schema**:
   - `docs/schemas/task-spec.schema.json`
   - `docs/schemas/orchestration-state.schema.json`
4. **Checklist operacional** para novos orquestradores do crypto-sage:
   - `docs/checklists/crypto-sage-orchestrator-checklist.md`
5. **Exemplo pronto de delegação em nome do usuário**:
   - `docs/examples/taskspec-delegated-human-proxy.json`

---

## 3) Contrato obrigatório de handoff: TaskSpec

Nenhum handoff é válido sem `TaskSpec` completo.

### 3.1 Campos mínimos obrigatórios

- `taskSpecVersion` (`"1.0"`)
- `handoffId` (ID único do handoff)
- `correlationId` (ID do ciclo fim-a-fim)
- `createdAt` (ISO-8601)
- `source.agentId` e `source.sessionId`
- `target.agentId` e `target.capability`
- `routing.routeKey` e `routing.strategy`
- `mode` (`dev|simulated|live`)
- `intent.operation`, `intent.inputSchemaRef`, `intent.input`
- `acceptance.doneWhen` (lista não vazia)
- `safety.e2eActor` (`human|authorized-harness`)
- `rollback.required=true` e `rollback.planRef`
- `audit.requestId` e `audit.idempotencyKey`

### 3.2 Autoria da execução (normal vs delegada)

Campo opcional com semântica obrigatória quando presente:

- `authorship.mode = direct` (padrão implícito; sem delegação)
- `authorship.mode = delegated-human-proxy` (responder/agir em nome do usuário)
- `authorship.mentionDelegationMode = disabled|gated` (default seguro implícito: `disabled`)

Se `authorship.mode=delegated-human-proxy`, as regras da seção **3.3** tornam-se mandatórias.

Se `authorship.mentionDelegationMode=gated`, as regras das seções **3.3.5** e **3.3.6** tornam-se mandatórias.

### 3.3 Modo oficial `delegated-human-proxy` (obrigatório quando usado)

#### 3.3.1 Policy explícita de delegação (allowlist)

TaskSpec deve conter `authorship.delegationPolicy` com:

- `allowAgents` (quais agentes podem receber a delegação)
- `allowChannels` (quais canais/contexts são permitidos)
- `allowTasks` (quais operações/tarefas podem ser executadas)
- `policyRef` (fonte da policy usada)

Sem allowlist explícita -> handoff inválido.

#### 3.3.2 Envelope obrigatório de autoria

TaskSpec deve conter `authorship.envelope` com:

- `principal` (quem delega)
- `delegateAgent` (agente que age em nome)
- `scope` (escopo efetivo de agentes/canais/tarefas)
- `ttl` (`issuedAt` e `expiresAt`)
- `proof` (`signature` e `hash`, com algoritmo)

Sem envelope completo/íntegro -> delegação inválida.

#### 3.3.3 Gates por risco

Classificação em `authorship.risk.classification`:

- `read` / `diagnostic`:
  - permitido sem confirmação manual adicional **desde que** policy + envelope + TTL válidos.
- `sensitive` / `live`:
  - exige `authorship.risk.requiresConfirmation=true`
  - exige `authorship.risk.authorizationRef` válido
  - exige aprovação/autorização correspondente em auditoria.

#### 3.3.4 Trilha de auditoria obrigatória para delegação

Quando houver `delegated-human-proxy`, é obrigatório registrar:

- `audit.delegation.policyRef`
- `audit.delegation.envelopeHash`
- `audit.delegation.riskClassification`
- `audit.delegation.authorizationRef` (obrigatório para `sensitive/live`)
- `audit.delegation.decision`
- `audit.delegation.validatedAt`
- `audit.delegation.recordedBy`

Além disso, gravar evento em `memory/orchestration-state.json.delegationAuditLog`.

#### 3.3.5 Trigger por mention (`@bot`) em Discord com execução gated

Quando o canal for Discord e houver mention explícita do bot (`@bot`):

- a mention é apenas **trigger de candidatura de delegação**;
- **não** autoriza execução por si só;
- a execução só pode acontecer quando `authorship.mode=delegated-human-proxy` e os gates de policy+envelope+risk já estiverem validados.

Para `mentionDelegationMode=gated`, o TaskSpec deve conter `authorship.mentionDelegation` com:

- `channel` (ex.: `discord:channel:<id>` ou `discord:thread:<id>`)
- `messageId` (ID único da mensagem que originou a delegação)
- `originBotId` (bot que originou o handoff)
- `targetBotId` (bot que vai executar)
- `observedAt` (timestamp da detecção da mention)
- `ttlSeconds` (janela de validade da trigger)
- `dedupeBy = messageId` (obrigatório neste padrão)

#### 3.3.6 Anti-loop + dedupe + TTL (obrigatório em mention gated)

No modo `mentionDelegationMode=gated`, são mandatórias as seguintes proteções:

- **Anti-loop:** `originBotId != targetBotId`.
- **Dedupe:** `messageId` só pode ser consumido uma vez dentro da janela válida.
- **TTL gate:** após `observedAt + ttlSeconds`, a trigger é inválida e deve ser negada.

Falha em qualquer uma dessas regras => handoff bloqueado com auditoria (`decision=denied|expired`).

### 3.4 Exemplo canônico (handoff padrão, sem delegação)

```json
{
  "taskSpecVersion": "1.0",
  "handoffId": "hs_20260218_router_0001",
  "correlationId": "corr_strategy_cycle_9001",
  "createdAt": "2026-02-18T19:31:00Z",
  "source": {
    "agentId": "decision-router",
    "sessionId": "agent:main:subagent:abc"
  },
  "target": {
    "agentId": "crypto-sage",
    "capability": "execution-plane"
  },
  "routing": {
    "strategy": "capability",
    "routeKey": "crypto-sage.execution-plane.v1",
    "fallbackAgentId": "crypto-sage-dr"
  },
  "mode": "simulated",
  "intent": {
    "operation": "swap.jupiter",
    "summary": "Executar swap SOL->USDC com limite de slippage",
    "inputSchemaRef": "agents/blockchain-operator/config/execution-plane.schema.v1.json",
    "input": {
      "chain": "solana",
      "inAsset": "SOL",
      "outAsset": "USDC",
      "amount": "0.25",
      "slippageBps": 100
    }
  },
  "acceptance": {
    "doneWhen": [
      "Resposta ok=true com runId",
      "operation e correlationId preservados",
      "Sem erro POLICY_* ou A2A_*"
    ],
    "expectedArtifacts": [
      "runId",
      "executionPlane.security.verified"
    ]
  },
  "safety": {
    "e2eActor": "authorized-harness",
    "allowExternalSideEffects": false,
    "requiresHumanApproval": true
  },
  "rollback": {
    "required": true,
    "planRef": "agents/blockchain-operator/docs/runbook.md",
    "trigger": "Qualquer erro live ou desvio de política"
  },
  "audit": {
    "requestId": "req_20260218_0001",
    "idempotencyKey": "idem_swap_cycle_9001",
    "traceId": "trace_9001"
  }
}
```

### 3.5 Exemplo TaskSpec com `delegated-human-proxy`

```json
{
  "taskSpecVersion": "1.0",
  "handoffId": "hs_20260218_proxy_0007",
  "correlationId": "corr_user_proxy_cycle_44",
  "createdAt": "2026-02-18T19:37:00Z",
  "source": {
    "agentId": "decision-router",
    "sessionId": "agent:main:subagent:proxy-44"
  },
  "target": {
    "agentId": "blockchain-operator",
    "capability": "execution-plane"
  },
  "routing": {
    "strategy": "capability",
    "routeKey": "crypto-sage.execution-plane.v1"
  },
  "mode": "live",
  "authorship": {
    "mode": "delegated-human-proxy",
    "delegationPolicy": {
      "policyRef": "policies/delegation/user-main-v1.json",
      "allowAgents": [
        "blockchain-operator"
      ],
      "allowChannels": [
        "discord:channel:1473392629055098942"
      ],
      "allowTasks": [
        "transfer",
        "swap.jupiter"
      ]
    },
    "envelope": {
      "principal": {
        "id": "user:main",
        "type": "human"
      },
      "delegateAgent": {
        "agentId": "decision-router",
        "sessionId": "agent:main:subagent:proxy-44"
      },
      "scope": {
        "agents": [
          "blockchain-operator"
        ],
        "channels": [
          "discord:channel:1473392629055098942"
        ],
        "tasks": [
          "transfer"
        ]
      },
      "ttl": {
        "issuedAt": "2026-02-18T19:36:30Z",
        "expiresAt": "2026-02-18T19:46:30Z"
      },
      "proof": {
        "algorithm": "hmac-sha256-v1",
        "signature": "d1f93c74c4b5f96c7f7d4d8d1a7f4b8c",
        "hash": "sha256:ae9132cf4d70c9c8dc4ab1879ba4fd89"
      }
    },
    "risk": {
      "classification": "live",
      "requiresConfirmation": true,
      "authorizationRef": "authz_20260218_user-main_009"
    },
    "mentionDelegationMode": "gated",
    "mentionDelegation": {
      "channel": "discord:channel:1473392629055098942",
      "messageId": "1473395000000000001",
      "originBotId": "decision-router",
      "targetBotId": "blockchain-operator",
      "observedAt": "2026-02-18T19:36:58Z",
      "ttlSeconds": 300,
      "dedupeBy": "messageId"
    }
  },
  "intent": {
    "operation": "transfer",
    "inputSchemaRef": "agents/blockchain-operator/config/execution-plane.schema.v1.json",
    "input": {
      "chain": "solana",
      "asset": "SOL",
      "amount": "0.05",
      "to": "8YxSExampleRecipient111111111111111111111111"
    }
  },
  "acceptance": {
    "doneWhen": [
      "Resposta ok=true com runId",
      "Autorização delegated-human-proxy validada",
      "Gate de mentionDelegationMode=gated passou (anti-loop+dedupe+TTL)",
      "audit.delegation preenchido"
    ]
  },
  "safety": {
    "e2eActor": "human",
    "allowExternalSideEffects": true,
    "requiresHumanApproval": true
  },
  "rollback": {
    "required": true,
    "planRef": "agents/blockchain-operator/docs/runbook.md",
    "trigger": "Falha live ou decisão de risco indevida"
  },
  "audit": {
    "requestId": "req_20260218_0099",
    "idempotencyKey": "idem_proxy_0099",
    "traceId": "trace_proxy_0099",
    "delegation": {
      "policyRef": "policies/delegation/user-main-v1.json",
      "envelopeHash": "sha256:ae9132cf4d70c9c8dc4ab1879ba4fd89",
      "riskClassification": "live",
      "authorizationRef": "authz_20260218_user-main_009",
      "decision": "allowed",
      "validatedAt": "2026-02-18T19:37:00Z",
      "recordedBy": "decision-router"
    }
  }
}
```

### 3.6 Regras de validação e roteamento

- `handoffId` **não** pode ser reutilizado.
- `routing.routeKey` deve resolver para uma rota cadastrada em `routingTable`.
- `intent.inputSchemaRef` deve apontar para schema existente e versionado.
- `mode=live` exige `safety.requiresHumanApproval=true`.
- sem `rollback.planRef`, promoção para `live` é **bloqueada**.
- em `delegated-human-proxy`, `source.agentId` deve ser compatível com `envelope.delegateAgent.agentId`.
- em `delegated-human-proxy`, operação/canal/alvo devem pertencer ao escopo e allowlists.
- em `mentionDelegationMode=gated`, mention `@bot` é só trigger; execução exige policy+envelope+risk validados.
- em `mentionDelegationMode=gated`, bloquear loop (`originBotId == targetBotId`).
- em `mentionDelegationMode=gated`, dedupe obrigatório por `messageId` dentro da janela `ttlSeconds`.

---

## 4) Estado único de orquestração

Arquivo canônico: `memory/orchestration-state.json`.

### 4.1 Objetivo do estado único

Evitar divergência entre agentes sobre:

- qual handoff está ativo;
- quem é o roteador primário/fallback;
- em qual gate de promoção o fluxo está;
- último rollback e motivo;
- histórico mínimo de delegações em nome do usuário.

### 4.2 Schema mínimo exigido

Campos de topo obrigatórios:

- `schemaVersion`
- `updatedAt`
- `activeHandoffs`
- `routingTable`
- `promotion`
- `rollback`
- `delegationAuditLog`

Regras operacionais:

- atualizar `updatedAt` a cada transição de estado;
- gravar transição de handoff em `activeHandoffs`;
- manter `promotion.currentStage` coerente com gate aprovado;
- registrar rollback em `rollback.lastRollbackAt` + `rollback.lastRollbackReason`;
- anexar evento de delegação em `delegationAuditLog` quando houver `delegated-human-proxy`.

---

## 5) Regra anti-self-test (obrigatória)

**Proibido** considerar válido teste E2E executado apenas por agentes autônomos sem ator externo autorizado.

Somente são aceitos para E2E:

1. `human` (ator humano explícito), ou
2. `authorized-harness` (harness aprovado pelo humano).

Consequências:

- `safety.e2eActor` fora desse conjunto invalida o handoff;
- promoção `simulated -> live` sem evidência de E2E autorizado é bloqueada;
- “autoaprovação” por agente não é evidência válida.

---

## 6) Gates de promoção (dev/simulado -> live) com rollback obrigatório

### Gate G0 — Contract Gate

- TaskSpec válido no schema.
- `routeKey` resolvido.
- `idempotencyKey` presente.

### Gate G1 — Routing Gate

- rota primária responde no modo atual;
- fallback definido para capability crítica.

### Gate G2 — Simulation Gate

- execução em `simulated`/`dry-run` sem side effects não planejados;
- sem erros `EXECUTION_SCHEMA_*`, `A2A_*`, `POLICY_*`.

### Gate G3 — Authorized E2E Gate

- E2E com `human` ou `authorized-harness` registrado no TaskSpec;
- evidência de auditoria anexada (runId / traceId / relatório).

### Gate G4 — Live Gate

- aprovação humana explícita;
- `rollback.planRef` válido e executável;
- canário live inicial com monitoramento ativo.

### Gates adicionais para `delegated-human-proxy` (antes da execução)

- **D1 Policy Gate:** allowlist de agentes/canais/tarefas aprovada.
- **D2 Envelope Gate:** principal/delegate/scope/TTL/proof válidos.
- **D3 Risk Gate:**
  - `read/diagnostic` -> executa se D1+D2 passarem;
  - `sensitive/live` -> exige `authorizationRef` válido + aprovação humana quando aplicável.
- **D4 Mention Gate** (somente quando `mentionDelegationMode=gated`):
  - mention `@bot` confirmada em canal Discord permitido;
  - anti-loop obrigatório (`originBotId != targetBotId`);
  - dedupe obrigatório por `messageId`;
  - TTL obrigatório (`observedAt + ttlSeconds` ainda válido).

### Rollback (obrigatório)

Rollback é obrigatório em promoção para live e deve conter:

- condição de disparo clara (erro crítico, violação de policy, roteamento incorreto);
- passo de reversão (demote para `simulated`, bloquear `routeKey`, restaurar rota anterior);
- registro em `memory/orchestration-state.json`.

Sem rollback documentado, **não há promoção**.

---

## 7) Compatibilidade com crypto-sage (blockchain-operator)

Para orquestrar o crypto-sage:

- manter `intent.operation` compatível com `agents/blockchain-operator/docs/a2a-protocol.md`;
- mapear `audit.requestId` / `audit.idempotencyKey` para envelope execution-plane (`requestId`, `idempotencyKey`);
- preservar `correlationId` fim-a-fim;
- em `live`, respeitar perímetro A2A (`A2A_SECURITY_MODE=enforce`, auth assinada quando exigido);
- em `delegated-human-proxy`, validar allowlists + envelope antes de enviar payload ao execution plane;
- em `mentionDelegationMode=gated`, encaminhar no payload metadados de mention (`messageId`, `originBotId`, `targetBotId`, `observedAt`, `ttlSeconds`) para permitir validação de anti-loop/dedupe/TTL no receiver.

---

## 8) Critério de conformidade rápida (pass/fail)

Um fluxo de orquestração está conforme quando:

- [ ] todo handoff usa TaskSpec válido;
- [ ] `memory/orchestration-state.json` é a única fonte de estado de orquestração;
- [ ] E2E usa apenas `human`/`authorized-harness`;
- [ ] promoção segue gates G0..G4;
- [ ] rollback está documentado e testado;
- [ ] em `delegated-human-proxy`, policy+envelope+risk+audit estão completos;
- [ ] em `mentionDelegationMode=gated`, anti-loop + dedupe por `messageId` + TTL foram validados.

---

## 9) Rollout recomendado em 3 fases (`mentionDelegationMode`)

### Fase 1 — dry-run / log-only

- manter execução efetiva fora do path de mention (sem side effect por trigger de mention);
- registrar eventos de mention elegíveis + decisão de gate (simulada) em auditoria;
- medir taxa de falsos positivos, loops evitados e colisões de dedupe.

### Fase 2 — gated-read

- habilitar `mentionDelegationMode=gated` apenas para operações `read/diagnostic`;
- exigir D1+D2+D3+D4 completos antes de executar;
- manter `sensitive/live` bloqueados para mention delegada.

### Fase 3 — gated-live

- liberar `mentionDelegationMode=gated` para `sensitive/live` somente com `authorizationRef` válido e aprovação humana aplicável;
- manter monitoramento ativo de dedupe/TTL/loop;
- rollback imediato para fase anterior em qualquer violação de gate.

---

## 10) Escopo runtime atual + gap explícito

Implementação aplicada neste workspace:

- validação de `mentionDelegationMode=gated` no receiver do crypto-sage (`execution-plane`), incluindo anti-loop, dedupe por `messageId` e TTL;
- bloqueio de execução quando gates de delegação (`delegated-human-proxy`) não chegam como válidos no payload.

**Gap conhecido (fora do escopo deste patch):** não foi encontrado um roteador Discord genérico único no workspace para capturar mention `@bot` na origem e montar automaticamente o TaskSpec/meta. Portanto, a montagem do envelope `mentionDelegation` no emissor permanece responsabilidade do orquestrador/integrador de canal.
