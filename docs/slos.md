# SLOs - Mission Control Recovery

**Ultima atualizacao:** 2026-03-05

## SLOs

| ID | Metrica | Alvo |
| --- | --- | --- |
| S1 | Review claim latency | P95 < 10 min |
| S2 | Duplicate dispatch attempts | 0 por task/fase em janela de 3h |
| S3 | Invalid queue completion | 0 |
| S4 | Awaiting-human auto-drain | 0 |
| S5 | Inbox redispatch de backlog/human_hold | 0 |
| S6 | Main session queue consumption manual | 0 |

## Fonte de medicao

Arquivos:
- `state/control-loop-metrics.json`
- `artifacts/reports/mc-queue-audit-latest.json`

Campos principais:
- `heartbeat_runs`
- `tasks_dispatched`
- `review_claims`
- `queue_items_written`
- `queue_items_deduped`
- `queue_items_completed`
- `queue_items_invalid_completed`
- `duplicate_dispatch_attempts`
- `judge_wakeups`
- `phase_transitions`
- `queue_audit.invalid_done_total`
- `queue_audit.invalid_done_post_recovery`
- `queue_audit.duplicate_meta_markers_total`
- `queue_audit.duplicate_groups_total`

## Criterios operacionais

- Se `queue_items_invalid_completed > 0`, abrir incidente.
- Se `duplicate_dispatch_attempts > 0`, inspecionar queue key e cooldown.
- Se houver card `awaiting_human` despachado automaticamente, abrir incidente critico.
- Se backlog voltar a redispatchar, revisar `mc_dispatch_policy` e dedup de `done/`.

## Runbook

Arquivo: `docs/mission-control-recovery-runbook.md`
