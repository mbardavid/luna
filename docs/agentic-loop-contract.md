# Contrato de Loop de Orquestracao (Luna <-> Luan)

## Objetivo

Todo desenvolvimento deve seguir um loop de 5 etapas, com ownership explicito e transicao registrada no Mission Control.

## Loop obrigatorio

1. Luna planeja a task
2. Luan elabora o plano
3. Luna valida o plano
4. Luan executa e testa
5. Luna faz a validacao final

## Representacao no Mission Control

| Etapa | status | mc_phase | owner |
| --- | --- | --- | --- |
| Intake | `inbox` | `intake` | none |
| 1 | `review` | `luna_task_planning` | luna |
| 2 | `in_progress` | `luan_plan_elaboration` | luan |
| 3 | `review` | `luna_plan_validation` | luna |
| 4 | `in_progress` | `luan_execution_and_tests` | luan |
| 5 | `review` | `luna_final_validation` | luna |
| Human gate | `awaiting_human` | `awaiting_human_decision` | human |
| End | `done` | `done` | none |

## Campos obrigatorios

- `mc_workflow`
- `mc_phase`
- `mc_phase_owner`
- `mc_phase_state`
- `mc_loop_id`
- `mc_plan_artifact`
- `mc_validation_artifact`
- `mc_test_report_artifact`
- `mc_gate_reason`
- `mc_claimed_by`
- `mc_claim_expires_at`
- `mc_phase_retry_count`

## Regras de conformidade

- Nenhum card `dev_loop_v1` pode sair de `inbox` direto para execucao do Luan.
- Toda rejeicao precisa de `review_reason`.
- `review` e reservado para fases da Luna.
- `awaiting_human` e terminal ate intervencao humana.
- Toda fase de review precisa de claim/lease antes do wake da Luna.
