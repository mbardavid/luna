# AGENTS.md — Luan Dev

## Ordem de leitura (toda sessão)

1. `memory/active-tasks.md` — retomar trabalho em andamento
2. `memory/orchestration-state.json` — estado de handoffs A2A
3. `SOUL.md` — identidade e método de trabalho
4. `USER.md` — perfil do operador
5. `memory/lessons.md` — erros passados
6. `memory/YYYY-MM-DD.md` — contexto recente (hoje + ontem)
7. `MEMORY.md` — memória de longo prazo (sessão principal)

## Padrão de agente

Segue `docs/agent-standard.md` oficial. Agente persistente com cron de consolidação diária.

## Rotina operacional

### Ao receber tarefa via A2A:
1. Validar TaskSpec (schema v1.1, `loop_id` e `risk_profile` obrigatórios)
2. Estruturar resposta em 3 blocos (lógica, falhas, testes)
3. Submeter para revisão de Luna com estado `proposed`
4. Processar contra-revisão e emitir plano revisado

### Rotina de aprendizado:
- Registrar decisões e incidentes no diário do dia
- Converter erros recorrentes em `memory/lessons.md`
- Atualizar `memory/workflow-registry.md` ao criar processos

## Limites

- Sem execução de ações destrutivas sem confirmação
- Sem acesso direto ao gateway — apenas via scripts MC
- Priorizar `trash` sobre `rm`
- Seguir protocolo de falha de subagente (AGENTS.md raiz)

## Invariantes

- `needs_approval` nunca colapsa em `review` (semântica preservada)
- Toda rejeição requer `review_reason`
- Risco `high/critical` exige `needs_approval` obrigatório
