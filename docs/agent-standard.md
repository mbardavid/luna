# Padrão Oficial de Arquitetura de Agentes (Workspace)

**Status:** ✅ Oficial (vigente desde 2026-02-18)  
**Escopo:** todo novo agente em `agents/*` deve nascer com este padrão.

## 1) Estrutura mínima obrigatória

```text
agents/<agent-name>/
├── SOUL.md
├── AGENTS.md
├── USER.md
├── MEMORY.md
├── HEARTBEAT.md
├── TOOLS.md
└── memory/
    ├── active-tasks.md
    ├── lessons.md
    ├── workflow-registry.md
    ├── orchestration-state.json  # obrigatório se houver handoff A2A
    └── YYYY-MM-DD.md             # criar diariamente (log do dia)
```

## 2) Contrato de cada arquivo

- `SOUL.md`: identidade e estilo do agente.
- `AGENTS.md`: regras operacionais, ordem de leitura de contexto, limites e rotina diária.
- `USER.md`: perfil/preferências do usuário dono do agente.
- `MEMORY.md`: memória de longo prazo (curada); não é log bruto.
- `HEARTBEAT.md`: checklist periódico (saúde + manutenção de memória).
- `TOOLS.md`: setup local, aliases, endpoints e observações de ambiente.
- `memory/active-tasks.md`: backlog vivo; lido primeiro em toda sessão.
- `memory/lessons.md`: lições aprendidas e erros que não devem se repetir.
- `memory/workflow-registry.md`: registro canônico de novos workflows/skills.
- `memory/orchestration-state.json`: fonte única do estado de orquestração A2A (quando aplicável).
- `memory/YYYY-MM-DD.md`: diário operacional bruto por data.

## 3) Rotina padrão de sessão

1. Ler `memory/active-tasks.md`
2. **Se atuar como orquestrador A2A:** ler `memory/orchestration-state.json`
3. Ler `SOUL.md`
4. Ler `USER.md`
5. Ler `memory/lessons.md`
6. Ler `memory/YYYY-MM-DD.md` (hoje + ontem)
7. Ler `memory/workflow-registry.md`
8. Em sessão principal com humano, ler também `MEMORY.md`

## 4) Rotina de aprendizado contínuo

- Durante o trabalho: registrar decisões/incidentes no diário do dia.
- Após erro recorrente: converter em entrada de `memory/lessons.md`.
- Ao criar/refinar processo: registrar em `memory/workflow-registry.md`.
- Se envolver setup local/ferramenta: atualizar `TOOLS.md` e skill relevante.
- No fechamento: atualizar `active-tasks.md` e promover aprendizados estáveis para `MEMORY.md`.

## 5) Convenções de segurança (obrigatórias)

- Não exfiltrar dados privados.
- Não executar ações destrutivas sem confirmação explícita.
- Preferir modos seguros por padrão (ex.: dry-run quando aplicável).
- Registrar mudanças de policy/guardrails no diário e, se permanentes, no `MEMORY.md`.
- Em grupos/canais compartilhados: não expor contexto pessoal do usuário.

## 6) Dual-plane (opcional, recomendado para agentes operacionais)

Use dual-plane quando o agente recebe tanto linguagem natural quanto payload estruturado:

- **Control Plane:** humano -> agente (NL), com parsing/normalização.
- **Execution Plane:** sistema -> agente (JSON), com schema estrito e sem parsing NL.

Requisitos mínimos em dual-plane:
- validação de schema por operação;
- policy/risk checks antes de executar;
- idempotência + auditoria por run;
- fail-safe em ambiguidade ou schema inválido.

## 7) Padrão interno A2A de orquestração (obrigatório quando houver handoff)

Ao delegar tarefa entre agentes, é obrigatório cumprir:

1. **TaskSpec obrigatório por handoff**
   - contrato canônico: `docs/agent-orchestration-a2a.md`
   - schema: `docs/schemas/task-spec.schema.json`
2. **Estado único de orquestração**
   - arquivo: `memory/orchestration-state.json`
   - schema: `docs/schemas/orchestration-state.schema.json`
3. **Regra anti-self-test**
   - E2E aceito apenas com ator `human` ou `authorized-harness`
   - autoaprovação por agente autônomo é inválida
4. **Gates de promoção com rollback obrigatório**
   - fluxo: `dev -> simulated -> live`
   - sem `rollback.planRef`, promoção para live é bloqueada
5. **Modo `delegated-human-proxy` (quando responder/agir em nome do usuário)**
   - policy explícita com allowlist de agentes/canais/tarefas
   - envelope de autoria obrigatório (`principal`, `delegateAgent`, `scope`, `ttl`, `signature/hash`)
   - `read/diagnostic` só com policy+envelope válidos
   - `sensitive/live` exige confirmação/autorização válida + trilha de auditoria
   - mention em Discord (`@bot`) só pode acionar execução quando `mentionDelegationMode=gated` + anti-loop + dedupe por `messageId` + TTL válidos

Checklist reutilizável para onboarding de orquestradores do crypto-sage:
- `docs/checklists/crypto-sage-orchestrator-checklist.md`

## 8) Checklist curto para criar novo agente

- [ ] Criar pasta `agents/<nome>` com os 6 arquivos base (`SOUL`, `AGENTS`, `USER`, `MEMORY`, `HEARTBEAT`, `TOOLS`).
- [ ] Criar `memory/active-tasks.md`, `memory/lessons.md`, `memory/workflow-registry.md` e diário do dia.
- [ ] Escrever no `AGENTS.md` a ordem de leitura e a rotina de aprendizado diário.
- [ ] Definir convenções de segurança e limites operacionais explícitos.
- [ ] (Opcional) Declarar arquitetura dual-plane e contratos de entrada.
- [ ] **Se houver orquestração A2A:** criar `memory/orchestration-state.json` e validar TaskSpec por schema.
- [ ] **Se usar `delegated-human-proxy`:** configurar policy allowlist, envelope de autoria e auditoria de delegação.
- [ ] **Se usar mention em Discord para handoff:** habilitar `mentionDelegationMode=gated` com anti-loop, dedupe por `messageId` e TTL.
- [ ] Registrar no diário inicial que o agente segue este padrão oficial.
