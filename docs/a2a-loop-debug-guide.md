# Guia de Debug A2A e Estados de Missão

## Objetivo

Diagnosticar por que uma task de A2A travou, repetiu ou perdeu sessão sem ambiguidade.

## Status semânticos aceitos

- `inbox`
- `in_progress`
- `needs_approval`
- `review`
- `blocked`
- `stalled`
- `retry`
- `done`
- `failed`

`review` não deve representar `needs_approval` ou `stalled`.

## Árvore de decisão rápida

1. Task sem `mc_session_key` e sem sessão ativa:
   - se `retry_count < max`: `retry`
   - se igual/maior: `needs_approval`
2. Task em `in_progress` sem atividade > limite:
   - set `stalled`
3. Task em `retry` sem `session_key` recorrente:
   - revisita `review`
4. Task com payload de revisão sem `review_reason`:
   - bloquear transição e solicitar contrato novo

## Comandos de inspeção

- `scripts/mc-task-update.sh --task-id <id> --input <payload>`
- `scripts/mc-link-task-session.sh <id> <sessionKey>`
- `scripts/mc-spawn.sh --agent luan --title ... --task ...`
- `scripts/mc-watchdog.sh --verbose`

## Eventos obrigatórios a registrar

- `last_transition_at`
- `review_reason`
- `review_feedback_required`
- `proposed_by`
- `loop_id`

Sem esses campos, a task é tratada como revisão incompleta.
