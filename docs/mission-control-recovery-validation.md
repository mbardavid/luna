# Mission Control Recovery - Validation Report

**Data:** 2026-03-05

## Objetivo

Registrar a validacao objetiva executada durante a recuperacao P0/P1/P2 no servidor.

## P0 - Safety Rails

### Validado

- `mc-client.sh get-task` voltou a funcionar com fallback correto.
- `226588ab` recebeu politica explicita de backlog.
- `heartbeat-v3.py --dry-run --verbose` passou a registrar:
  - `FILTER: 226588ab backlog policy — staying in inbox`
- `awaiting_human` nao foi auto-despachado.
- `review` bloqueou `inbox` em smoke test real:
  - card `34877d8a` entrou em `review`
  - heartbeat registrou `REVIEW CLAIM` e encerrou sem drenar `inbox`
- bootstrap de custom fields foi aplicado e o backend do Mission Control reiniciado.

### Evidencia

- `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`
- `scripts/mc-bootstrap-custom-fields.sh`
- `sudo -n docker compose restart backend webhook-worker`

## P1 - Dev Loop Enforcement

### Validado em card real

Card de teste: `acd7019e-1abe-447c-a9cb-328f5f6ec909`

Transicoes validadas:
1. `inbox + intake` -> `review + luna_task_planning`
2. `review + luna_task_planning` -> `in_progress + luan_plan_elaboration`
3. `in_progress + luan_plan_elaboration` -> `review + luna_plan_validation`
4. rejeicao de plano -> volta para `in_progress + luan_plan_elaboration`
5. aprovacao de plano -> `in_progress + luan_execution_and_tests`
6. `in_progress + luan_execution_and_tests` -> `review + luna_final_validation`
7. rejeicao final -> volta para `in_progress + luan_execution_and_tests` com `mc_phase_retry_count=1`
8. aprovacao final -> `done + mc_phase=done`

### Artifacts gerados

- `artifacts/mc/acd7019e-luna-task-planning.md`
- `artifacts/mc/acd7019e-plan.md`
- `artifacts/mc/acd7019e-test-report.md`
- `artifacts/mc/acd7019e-luna_final_validation.md`

### Evidencia

- `scripts/mc-task-update.sh --strict`
- `python3 heartbeat-v3/scripts/judge-loop-worker.py --task-id ... --dry-run --json`

## P2 - Hardening

### Validado

- custom fields do loop registrados no board
- docs operacionais atualizados para proibir consumo manual da fila
- schema de metricas alinhado ao control loop atual
- replay harness dos incidentes reais adicionado em fixtures/testes
- auditoria de fila gera relatorio e atualiza metricas
- `mc-approvals-notify` e `mc-delivery` validados em dry-run no canal principal `1473367119377731800`
- cron de auditoria de fila instalado (`7 * * * *`)
- suite automatizada passou:
  - `tests/test_control_plane.py`
  - `tests/test_queue.py`
  - `tests/test_mc_fast_dispatch.py`
  - `tests/test_queue_audit.py`
  - `tests/test_incident_replays.py`

### Evidencia objetiva

- `python3 -m unittest -v heartbeat-v3/tests/test_control_plane.py heartbeat-v3/tests/test_queue.py heartbeat-v3/tests/test_mc_fast_dispatch.py heartbeat-v3/tests/test_queue_audit.py heartbeat-v3/tests/test_incident_replays.py`
  - resultado: `Ran 52 tests ... OK`
- `python3 scripts/mc-queue-audit.py --write-metrics --json`
  - `invalid_done_total=142`
  - `invalid_done_post_recovery=0`
  - `duplicate_meta_markers_total=1`
  - `duplicate_groups_total=18`
- `MC_APPROVALS_DRYRUN=1 scripts/mc-approvals-notify.sh --channel 1473367119377731800`
- `MC_DELIVERY_DRYRUN=1 scripts/mc-delivery.sh --status done --max-to-deliver 1 --channel 1473367119377731800`
- `python3 scripts/session-smart-compact.py --dry-run`
  - `Sessions scanned: 1647`
  - `Errors: 0`
- `python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose`
  - continuou filtrando `226588ab` como backlog
  - continuou deduplicando `1c6ab056` por `recent done exists`

## Observacao importante

O backend do Mission Control roda em container sem bind mount do repo. Depois de bootstrapar custom fields, foi necessario reiniciar `backend` e `webhook-worker` para o runtime aceitar os novos campos no `PATCH /tasks`.

## Observacao adicional

A auditoria mostrou corrupcao historica em `queue/done/` anterior ao recovery, mas nao encontrou `invalid_done_post_recovery`. Ou seja: a fila antiga precisa de limpeza/arqueologia, porem os novos writes/completions do loop recuperado estao coerentes.
