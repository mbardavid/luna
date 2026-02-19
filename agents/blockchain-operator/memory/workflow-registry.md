# Workflow & Skill Registry
*Registro oficial de novos workflows e habilidades operacionais do agente.*

## Como registrar (template)
- **Data:** YYYY-MM-DD
- **Tipo:** workflow | skill
- **Nome:** ...
- **Trigger:** quando usar
- **Procedimento resumido:** 3-7 passos objetivos
- **Artefatos:** arquivos/scripts envolvidos
- **Validação:** como confirmar que funcionou
- **Status:** active | deprecated

## Entradas ativas

### 2026-02-18 · workflow · Dual-plane execution pipeline
- **Trigger:** recebimento de instrução NL (control plane) ou payload JSON (execution plane).
- **Procedimento resumido:** normalizar intent -> validar schema/policy -> preflight -> executar conector -> auditar + idempotência.
- **Artefatos:** `src/cli.mjs`, `config/execution-plane.schema.v1.json`, `state/audit.jsonl`.
- **Validação:** presença de `runId`, evento de execução e capacidade de replay.
- **Status:** active

### 2026-02-18 · workflow · Continuous-memory update loop
- **Trigger:** início/fim de sessão e heartbeat com mudanças.
- **Procedimento resumido:** ler `active-tasks` -> executar trabalho -> registrar no diário -> extrair lições -> atualizar tarefas ativas.
- **Artefatos:** `memory/active-tasks.md`, `memory/YYYY-MM-DD.md`, `memory/lessons.md`, `MEMORY.md`.
- **Validação:** contexto recuperável em nova sessão sem depender do histórico do chat.
- **Status:** active
