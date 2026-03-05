# Mission Control Recovery Runbook

**Status:** ativo em 2026-03-05

## 1. Queue corruption

Sinais:
- `queue_audit.invalid_done_total > 0`
- item em `queue/done/` sem `completed_at`, `completed_by`, `success=true`, `result.action`

Comando:

```bash
python3 scripts/mc-queue-audit.py --write-metrics --json
```

Critério de validação:
- `invalid_done_post_recovery = 0`
- `duplicate_meta_markers_total` e `duplicate_groups_total` explicáveis por histórico, não por runs novos

## 2. Claim timeout

Sinais:
- task em `review` com `mc_claimed_by` preenchido e `mc_claim_expires_at` expirado
- judge loop não fecha o card

Comandos:

```bash
scripts/mc-client.sh list-tasks review
python3 heartbeat-v3/scripts/judge-loop-worker.py --task-id <task_id> --dry-run --json
```

Critério de validação:
- task claimed volta a `pending` ou é concluída
- `review_claims` cresce sem duplicar claim ativo para a mesma fase

## 3. Review stall

Sinais:
- `review` não esvazia
- heartbeat só lista review sem gerar claim novo

Comandos:

```bash
python3 heartbeat-v3/scripts/heartbeat-v3.py --dry-run --verbose
python3 heartbeat-v3/scripts/judge-loop-worker.py --dry-run --json
```

Critério de validação:
- review de Luna aparece antes de qualquer `inbox`
- ao existir review elegível, o heartbeat encerra antes de drenar inbox

## 4. Session bloat

Sinais:
- sessão principal > 5MB
- compaction/sentinel recorrendo ao canal errado ou sem relatório

Comandos:

```bash
scripts/session-compact-sentinel.sh
python3 scripts/session-smart-compact.py --dry-run
```

Critério de validação:
- relatórios chegam ao canal principal `1473367119377731800`
- sessões inativas > 1MB são arquivadas
- sessão ativa grande é sinalizada, não arquivada

## 5. OpenClaw constraints that govern remediation

- `system event` apenas acorda ou injeta contexto; não executa o trabalho sozinho.
- A sessão principal é serializada; wake em `main` não equivale a preempção.
- Não depender de hook de fim de sessão.
- Spawn operacional continua via `openclaw agent` / `sessions_spawn`, não via RPC improvisado.

## 6. Escopo do loop canônico

Loop obrigatório:
1. Luna planeja tasks
2. Luan elabora o plano
3. Luna valida o plano
4. Luan implementa e testa
5. Luna valida o resultado final

Estados canônicos:
- `review` para fases Luna
- `in_progress` para fases Luan
- `awaiting_human` para gate humano
- `done` apenas após validação final
