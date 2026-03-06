
# Padrão Oficial de Arquitetura de Agentes Persistentes (Workspace)

**Status:** vigente
**Escopo:** todo agente persistente deve ter runtime canônico em `workspace-*`.

`agents/*` permanece apenas como camada legada de template/documentação. Não é runtime canônico.

## 1) Estrutura mínima obrigatória

```text
workspace-<agent>/
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
    └── YYYY-MM-DD.md             # diário operacional do dia
```

## 2) Contrato de cada arquivo

- `SOUL.md`: identidade e estilo do agente.
- `AGENTS.md`: ordem de leitura, limites, rotina de sessão e contrato operacional.
- `USER.md`: preferências do operador.
- `MEMORY.md`: memória de longo prazo curada.
- `HEARTBEAT.md`: checklist periódico de saúde e manutenção.
- `TOOLS.md`: comandos, aliases e detalhes de ambiente.
- `memory/active-tasks.md`: backlog vivo; ler primeiro em toda sessão.
- `memory/lessons.md`: erros e lições que não podem se repetir.
- `memory/workflow-registry.md`: workflows e skills reutilizáveis.
- `memory/orchestration-state.json`: estado único de handoffs A2A quando aplicável.
- `memory/YYYY-MM-DD.md`: log diário bruto.

## 3) Rotina padrão de sessão

1. Ler `memory/active-tasks.md`
2. Se atuar como orquestrador A2A: ler `memory/orchestration-state.json`
3. Ler `SOUL.md`
4. Ler `USER.md`
5. Ler `memory/lessons.md`
6. Ler `memory/YYYY-MM-DD.md` (hoje + ontem)
7. Ler `memory/workflow-registry.md`
8. Em sessão principal com humano, ler também `MEMORY.md`

## 4) Rotina de aprendizado contínuo

- Durante o trabalho: registrar decisões e incidentes no diário do dia.
- Após erro recorrente: sintetizar em `memory/lessons.md`.
- Ao criar ou refinar processo: registrar em `memory/workflow-registry.md`.
- Ao fechar um ciclo: atualizar `memory/active-tasks.md` e promover aprendizados duráveis para `MEMORY.md`.

## 5) Convenções de segurança

- Não executar ações destrutivas sem gate explícito.
- Preferir dry-run quando aplicável.
- Não manter duas fontes vivas de identidade sem regra de sincronização.
- Registrar mudança de policy, protocolo ou guardrail no diário e, se permanente, no `MEMORY.md`.

## 6) Regra de runtime canônico

- Fonte única de runtime: `/home/openclaw/.openclaw/openclaw.json`
- Workspaces persistentes canônicos:
  - `workspace-main`
  - `workspace-luan`
  - `workspace-crypto-sage`
  - `workspace-quant-strategist`
  - `workspace-dispatcher`
  - `workspace-cto-ops`
- Aliases legados são aceitos apenas por compatibilidade e devem sempre normalizar para o nome canônico.
