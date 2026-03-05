# Guia de Debug A2A e Estados de Missao

## Status canonicos

- `inbox`
- `in_progress`
- `review`
- `awaiting_human`
- `blocked`
- `stalled`
- `retry`
- `done`
- `failed`

`needs_approval` deve ser lido como `awaiting_human`.

## Ordem de diagnostico

1. Confirmar `status` e `mc_phase` do card.
2. Confirmar se o card e `direct_exec` ou `dev_loop_v1`.
3. Se for `review`, verificar `mc_phase_owner`, `mc_claimed_by` e `mc_claim_expires_at`.
4. Se for `in_progress`, verificar `mc_session_key` e sessao real no gateway.
5. Se for `awaiting_human`, confirmar `mc_gate_reason` e parar o auto-drain.
6. Se houver custom field novo rejeitado por `422`, rodar bootstrap e reiniciar backend/webhook-worker do Mission Control.

## Casos tipicos

### Card em `inbox` re-despachando sem parar
- verificar `mc_dispatch_policy`
- verificar queue dedup key
- verificar se existe `done/` recente para a mesma fase

### Card em `review` sem acao
- verificar se o `judge-loop-worker` claimou a fase
- verificar se o wake foi para `#general-luna`
- verificar se a fase e Luna-owned

### Card em `awaiting_human` sendo mexido automaticamente
- bug de semantica de status
- corrigir consumidor que ainda trata `needs_approval` como funnel de review

## Comandos uteis

- `scripts/mc-client.sh get-task <id>`
- `scripts/mc-task-update.sh --strict`
- `python3 heartbeat-v3/scripts/judge-loop-worker.py --task-id <id> --dry-run --json`
- `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`
