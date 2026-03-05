# Heartbeat V3 - Architecture

## Principle

> Detectar em codigo. Consumir em codigo. Usar a sessao principal apenas para decisao e entrega.

O control plane atual tem duas trilhas:

1. `direct_exec`
   - `heartbeat-v3.py` detecta e escreve item em `queue/pending/`
   - `queue-consumer.py` e `mc-fast-dispatch.sh` consomem e marcam resultado
2. `dev_loop_v1`
   - `heartbeat-v3.py` roteia `inbox` para `review + luna_task_planning`
   - `judge-loop-worker.py` faz claim/lease da fase de review
   - Luna fecha a fase no Mission Control

## Canonical Rules

- `review` tem prioridade sobre `inbox`
- `awaiting_human` nunca e auto-drenado
- `backlog` e `human_hold` bloqueiam auto-drain de `inbox`
- `queue item` so pode ir para `done/` com metadata de auditoria
- a sessao principal nao e consumer da fila filesystem

## Components

### `heartbeat-v3.py`
- detecta falhas, stale tasks, backlog, inbox e review
- escreve fila apenas para `direct_exec`
- roteia `dev_loop_v1` para a fase `luna_task_planning`
- bloqueia `inbox` quando existe `review` pendente ou `in_progress` ativo (conforme config)

### `judge-loop-worker.py`
- seleciona fases Luna-owned em `review`
- cria artifact inicial se faltar
- grava claim com lease
- envia wake direcionado para a Luna

### `queue-consumer.py`
- faz `peek -> claim -> complete`
- escreve `result.action`, `completed_by`, `completed_at` e `result.session_id` quando aplicavel

### `mc_control.py`
- semantica canonica de status
- politicas de dispatch
- queue key por `task_id + dispatch_type + status + phase`
- transicoes do dev loop

## Queue File Lifecycle

- `pending/`: item elegivel para consumo
- `active/`: item claimado por consumer
- `done/`: item concluido com metadata de auditoria
- `failed/`: item com falha explicita

Consumo manual de fila e proibido.

## Development Loop

Todo card `mc_workflow=dev_loop_v1` deve seguir:

1. `inbox + intake`
2. `review + luna_task_planning`
3. `in_progress + luan_plan_elaboration`
4. `review + luna_plan_validation`
5. `in_progress + luan_execution_and_tests`
6. `review + luna_final_validation`
7. `done` ou `awaiting_human`

## Operational Notes

- `system event` nao e executor; e apenas wake/sinalizacao.
- O runtime do Mission Control roda em containers sem bind mount do repo. Mudancas no backend e no schema operacional exigem restart do backend/webhook-worker.
- Apos rodar `scripts/mc-bootstrap-custom-fields.sh`, reinicie:

```bash
cd /home/openclaw/.openclaw/workspace/openclaw-mission-control
sudo -n docker compose restart backend webhook-worker
```
