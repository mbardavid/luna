# HEARTBEAT.md — Checklist operacional + aprendizado contínuo (Dual-Plane)

## 1) Saúde operacional

1. Verificar `state/circuit-breaker.json` (open/cooldown)
2. Verificar últimas falhas em `state/audit.jsonl`
3. Verificar fila do execution plane:
   - novos eventos `execution_plane.received`
   - falhas de schema/policy pendentes de tratamento
4. Confirmar latência e saúde dos endpoints RPC/API
5. Confirmar que policy/schema ativos não mudaram sem revisão

## 2) Memória contínua e aprendizado

6. Registrar no diário do dia (`memory/YYYY-MM-DD.md`) qualquer decisão/erro relevante desde o último heartbeat
7. Se houve erro recorrente ou correção útil: atualizar `memory/lessons.md`
8. Se surgiu novo workflow/skill:
   - registrar em `memory/workflow-registry.md`
   - refletir em `TOOLS.md` e/ou skill correspondente
9. Atualizar `memory/active-tasks.md` (in progress / blocked / completed)

## 3) Resposta

- Se houver alerta relevante: reportar com contexto e ação recomendada.
- Se tudo estiver estável e sem pendências: `HEARTBEAT_OK`.
