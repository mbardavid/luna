# Heartbeat V3 - Installation Guide

## Preconditions

- testes do `heartbeat-v3` passando
- custom fields bootstrapados no Mission Control
- backend/webhook-worker do Mission Control reiniciados apos qualquer mudanca de custom fields

## Mandatory Setup

### 1. Run tests

```bash
cd /home/openclaw/.openclaw/workspace/heartbeat-v3
python3 -m unittest -v tests/test_control_plane.py tests/test_queue.py tests/test_mc_fast_dispatch.py
```

### 2. Bootstrap custom fields

```bash
cd /home/openclaw/.openclaw/workspace
scripts/mc-bootstrap-custom-fields.sh
cd openclaw-mission-control
sudo -n docker compose restart backend webhook-worker
```

Sem o restart, o backend pode continuar rejeitando campos novos em `PATCH /tasks`.

### 3. Dry-run the heartbeat

```bash
cd /home/openclaw/.openclaw/workspace/heartbeat-v3
python3 scripts/heartbeat-v3.py --dry-run --verbose
```

Esperado:
- backlog em `inbox` e ignorado por politica
- `review` tem prioridade sobre `inbox`
- `awaiting_human` nao e despachado
- nenhum `system-event` vira execucao direta

### 4. Verify config flags

`heartbeat-v3/config/v3-config.json` deve manter:

- `queue_nudge_enabled=false`
- `queue_wake_enabled=false`
- `mirror_notifications=false`
- `inbox_requires_idle=true`

### 5. Crons

O consumo da fila deve continuar em codigo. Nao instalar automacoes paralelas que mandem a sessao principal "verificar queue/pending e processar".

## First Live Validation

1. Criar um card `backlog` ou `human_hold` e confirmar que o heartbeat nao drena.
2. Criar um card `review` e confirmar que o heartbeat acorda o judge loop antes de qualquer `inbox`.
3. Criar um card `dev_loop_v1` de teste e validar as transicoes:
   - intake -> luna planning
   - plan rejection -> volta para `luan_plan_elaboration`
   - final QA rejection -> volta para `luan_execution_and_tests`
   - final approval -> `done`

## Do Not Do

- Nao adicionar regra em SOUL/IDENTITY mandando a Luna consumir `queue/pending/` manualmente.
- Nao tratar `needs_approval` como status independente.
- Nao mover arquivo de fila para `done/` sem metadata de dispatcher.
