# HEARTBEAT.md

# Este heartbeat principal e notify-only.
# Ele nao consome fila, nao move arquivos e nao executa dispatch manual.

## Objetivo

Resumir o estado do Mission Control e acordar a Luna quando houver mudanca operacional relevante.

## Checklist rapido

1. Verificar subagents ativos e falhados recentes.
2. Verificar contagem de tasks em `review`, `inbox`, `in_progress` e `awaiting_human`.
3. Verificar anti-spam state para nao repetir a mesma notificacao.
4. Escalar apenas quando houver mudanca de estado ou review pendente sem acao.

## Regras semanticas canonicas

- Status aceitos: `inbox`, `in_progress`, `review`, `awaiting_human`, `blocked`, `stalled`, `retry`, `done`, `failed`
- `needs_approval` deve ser tratado como `awaiting_human`
- `review` nao pode representar `awaiting_human` ou `stalled`
- `review` drena antes de `inbox`
- `awaiting_human` nunca entra em auto-drain

## Regras criticas

- Nao consumir `queue/pending/` manualmente.
- Nao mover arquivos da fila manualmente.
- `system event` apenas acorda; nao substitui worker ou dispatch.
- Crons de manutencao nao contam como trabalho ativo.
- Sempre especificar `channel: discord` no `message` tool quando notificar via Discord.

## Ownership

- `heartbeat-v3.py`: deteccao, politicas de drain, escrita da fila de `direct_exec`, wake do judge loop
- `judge-loop-worker.py`: claim/lease das fases de `review`
- `queue-consumer.py` e dispatcher: consumo da fila filesystem
- sessao principal da Luna: responde ao wake e fecha a fase corretamente no MC
