# Lessons — CTO-ops

## Lições ativas
- (nenhuma pendente)

## Lições registradas
- 2026-03-01: preservar trilha de decisão em risco alto/critical antes de qualquer ação de restart.

---

## Cláusula Pétrea: Wakeup Instantâneo via `gateway call agent` (2026-03-03)

### Padrão Arquitetural Obrigatório
`openclaw gateway call agent --json --params '{"message":"...","idempotencyKey":"unique-key"}'` cria um turno AI imediatamente na sessão principal. Scripts bash podem despertar agentes AI em segundos, sem esperar heartbeat.

### 3 Camadas de Uptime
1. **Wakeup instantâneo** — `gateway call agent` para eventos críticos (~3s)
2. **Heartbeat built-in** (2min) — safety net automático
3. **Bash detection** (*/5 cron) — detecção determinística, dispara camada 1

### Regras
- Scripts de detecção DEVEM chamar `gateway call agent` após gerar queue item
- `idempotencyKey` único por evento (timestamp ou task_id)
- Timeout 15-20s, non-fatal (heartbeat é backup)
- Padrão obrigatório para toda automação futura em todos os agentes

