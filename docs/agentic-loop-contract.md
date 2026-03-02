# Contrato de Loop de Orquestração (Luna ⇄ Luan)

## Objetivo

Padronizar revisão de tarefas com dupla passagem ativa:

1. Proposta executiva
2. Crítica e contra-proposta
3. Replano validado
4. Autorização e execução

## 4 Passos obrigatórios

### 1. Proposta inicial (Luna)

Luna cria um `TaskSpec` com:
- `loop_id`
- `proposed_by = luna`
- `risk_profile`
- `review_depth`
- `review_feedback_required`
- `auto_approve_window`

### 2. Entrega para Luan

Luan responde com estrutura mínima:

- bloco lógico de plano (entrega, riscos, testes)
- lista de falhas previstas
- plano de validação objetivo

Estado de orquestração: `proposed`

### 3. Contrarrevisão de Luan

Luna revisa a resposta e pode decidir:

- `needs_critique`: pedir `counter-review`
- `needs_revision`: enviar de volta com ajustes explícitos e novo motivo (`review_reason`)
- `rejected_for_authorization`: mover para `needs_approval`

### 4. Replanejamento + autorização

Luan emite versão revisada. Luna valida schema/assinatura/riscos e:

- aprova automaticamente se `risk_profile=low` e dentro de `auto_approve_window`
- solicita autorização humana se `risk_profile` não `low` ou falhas de risco

Estado final:

- `accepted_for_execution`, `needs_authorized_edit`, `blocked_by_review`, `completed_with_checks`

## Campos obrigatórios no TaskSpec

- `loop_id`
- `proposed_by`
- `risk_profile`
- `review_depth`
- `review_feedback_required`
- `auto_approve_window`
- `review_reason` (na crítica)

## Regras de conformidade

- Nenhum contrato passa sem `review_reason` quando reprovado
- Nenhum contrato de risco alto/critical pode pular `needs_approval`
- Toda revisão grava evento em `memory/orchestration-state.json`
