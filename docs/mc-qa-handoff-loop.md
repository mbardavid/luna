# MC QA Handoff Loop Plan

**Task / MC ID:** `272b14b8-d0af-4d6a-87b7-8b1672f2f288`  
**Risk:** MEDIUM  
**Status:** `PLAN_ONLY`

## Objetivo
Criar um fluxo estrutural que, quando a Luna reprova QA em `review`, o sistema re-dispatche automaticamente sem loop infinito, usando o card do Mission Control como memória operacional.

## Problema atual
Hoje, `review -> inbox` pode perder contexto. Ao reprocessar, o próximo worker não entende por que falhou e repete o mesmo caminho.

---

## Entregáveis
1. `docs/mc-qa-handoff-loop.md` (este plano)  
2. Template padrão de **QA_HANDOFF** (markdown + JSON opcional)  
3. Checklist de caminhos de código para alterar  
4. Checklist de comandos de validação (unit + smoke)

---

## Modelo de loop (alvo)
Quando uma task em `review` recebe rejeição de QA:

1. Detectar rejeição no heartbeat-v3 (status/erro/flags de QA).
2. Montar e persistir `QA_HANDOFF` (idempotente).
3. Atualizar MC:
   - `status = inbox`
   - `mc_retry_count += 1`
   - `mc_last_error = "qa_rejected"`
   - `mc_output_summary` com causa curta
   - opcionalmente `mc_qa_handoff_fp` (fingerprint do handoff)
4. Limpar/arquivar `mc_session_key` no re-dispatch.
5. Reabrir no fluxo normal de heartbeat; a mensagem do dispatch injeta o `QA_HANDOFF` no prompt do worker.

---

## Template do QA_HANDOFF

### A) Markdown (obrigatório no comentário)

```markdown
## QA_HANDOFF v1 {{fingerprint}}
- **Task ID:** {{task_id}}
- **Retry:** {{mc_retry_count}}
- **Resultado QA:** REJECTED
- **Reviewer:** Luna
- **Motivo:** {{qa_reason_short}}
- **Risco:** {{risk_summary}}
- **Ação correta do próximo worker:**
  1) Corrigir: {{required_fix}}
  2) Validar contra: {{acceptance_criteria}}
  3) Retornar QA em novo output
- **Evidência mínima:**
  - {{evidence_1}}
  - {{evidence_2}}
  - {{evidence_3}}

### Diff mínimo (opcional)
```diff
{{minimal_diff_or_fix_steps}}
```

### JSON (opcional, para parser e dedupe)

```json
{
  "schema": "QA_HANDOFF_v1",
  "task_id": "{{task_id}}",
  "mc_task_id": "{{mc_task_id}}",
  "qa_result": "REJECTED",
  "retry_count": {{mc_retry_count}},
  "fingerprint": "{{fingerprint}}",
  "required_fix": "{{required_fix}}",
  "acceptance_criteria": ["{{ac1}}", "{{ac2}}"],
  "evidence": ["{{evidence_1}}", "{{evidence_2}}"],
  "next_check": "{{next_expected_validation_point}}"
}
```
```

---

## Padrões críticos

### Idempotência / deduplicação
- Antes de escrever `QA_HANDOFF`, calcular `fingerprint = sha1(task_id + qa_reason + evidence_hash)`.
- Não duplicar bloqueios:
  - se `mc_qa_handoff_fp == fingerprint`, pular rewrite/append.
  - se comentário mais recente já contém o mesmo fingerprint, pular.
- Sempre registrar a versão no campo `mc_qa_handoff_fp`.

### Política de overwrite segura
- **Padronizar em `append-only` no comentário** (sem risco de perder histórico).
- Atualizar `description` **somente opcionalmente**:
  - prepend de cabeçalho curto + hash
  - manter conteúdo anterior intacto (ou já preservado via comentários)
- Nunca sobrescrever `description` no modo “full replace” sem backup.

### MC fields esperados
- `mc_retry_count` (inteiro)
- `mc_last_error` (`qa_rejected`)
- `mc_output_summary`
- `mc_session_key`
- `mc_progress` (usar `10-20` no re-entrada de loop)
- opcional: `mc_qa_handoff_fp`, `mc_qa_handoff_last_at`

---

## Integração com heartbeat-v3 (pontos de código)

### Fase 0 (startup/lock)
- Carregar estado e dedupe.
- Não quebrar o fluxo normal; se falha no parser, fallback seguro sem dispatch.

### Fase 2 (seleção de candidato)
- Adicionar **filtro preferencial de `review`**: tratar `review` com `mc_last_error=qa_rejected` antes de `inbox` normal.
- Só processar uma task por ciclo para evitar duplo dispatch.

### Fase de preparo de dispatch
- Antes de preparar payload para worker, montar `qa_handoff_context`:
  - buscar comentário mais novo com `QA_HANDOFF`
  - usar `mc_qa_handoff_fp` e `mc_retry_count`.
- Incluir bloco no prompt:
  - título + motivo + ação + risco + aceite esperado.

### Fase de pós-dispatch (watchdog/rollback)
- Se dispatch falhar (sem cron/session) manter em `review` com `mc_last_error=handoff_dispatch_failed`.
- Se sucesso parcial, continuar; se sucesso total, mover para `in_progress`/`inbox` conforme fluxo existente.

---

## Arquitetura de implementação (sem código)

### Etapas de mudança
1. **Adicionar parser de status QA** no heartbeat-v3:
   - detectar rejeição de QA proveniente de comments/código de retorno/`mc_last_error`.
2. **Implementar gerador de handoff**:
   - função `build_qa_handoff_block(task, qa_result)`.
3. **Atualização MC atômica**:
   - atualizar campos + comentário + limpeza de `mc_session_key`.
4. **Prompt injection**:
   - incluir bloco no corpo enviado para o worker.
5. **Guardas de loop**:
   - máximo de retries por task (`MAX_QA_RETRY`), e cap de `mc_retry_count` com sinalização de `needs_approval` se exceder.

### Paths sugeridos a alterar (a confirmar no ambiente alvo)
- `scripts/heartbeat-v3/run_loop.py` (ou entrypoint equivalente)
- `scripts/heartbeat-v3/dispatch.py`
- `scripts/heartbeat-v3/mc_bridge.py` (ou módulo equivalente de leitura/escrita MC)
- `scripts/heartbeat-v3/qa_runner.py` (ou local de parsing de resultados)
- `scripts/mc-task-update.sh` (ajuste de template, se necessário)
- `scripts/mc-client.sh` se precisar de `mc_qa_handoff_fp` nas chamadas utilitárias
- `config/mission-control-ids.json` (somente se for necessário novo campo de custom fields, se não existir)

### Lista de mudanças por ordem
1. Garantir custom fields: validar/registrar `mc_qa_handoff_fp` (e opcionalmente `mc_qa_handoff_last_at`) no custom field bootstrap.
2. Adicionar função `extract_qa_rejection()` no heartbeat-v3.
3. Adicionar função `build_qa_handoff_block()`.
4. Persistir comentário + atualização de campos em sequência segura.
5. Incrementar `mc_retry_count` com saturação.
6. Inserir seção `QA_HANDOFF` no template de prompt do dispatch.
7. Adicionar métrica/telemetria (handoff_count, dedup_hit, loop_skips, review_to_inbox_count).

---

## Test Plan

### Unit (determinístico)
- `qa_handoff_builds_dedup`: mesma falha gera fingerprint igual => sem novo comentário.
- `qa_handoff_description_backup`: quando `description` opcional desativada, comentário preserva tudo.
- `retry_increment`: `mc_retry_count` inicial 0 vira 1.
- `guard_status_transition`: apenas `review -> inbox`, sem pular `inbox`/`in_progress` indevidos.
- `prompt_injection`: dispatch payload contém bloco `QA_HANDOFF` quando `mc_last_error=qa_rejected`.

### Smoke / end-to-end
1. Criar task de teste em `inbox`, mover para `review` com rejeição QA simulada.
2. Rodar heartbeat-v3 em dry-run + modo real de processamento.
3. Verificar:
   - comentário novo (ou não duplicado)
   - campos atualizados (`mc_retry_count`, `mc_last_error`, `mc_output_summary`)
   - status virou `inbox`
   - task reaparece para worker com contexto já embutido.
4. Rodar ciclo 2:
   - mesmo cenário sem mudança de motivo -> **não duplicar QA_HANDOFF**.
   - motivo alterado -> novo QA_HANDOFF e `retry_count` incrementado.
5. Rodar ciclo 3 com `mc_retry_count` acima do limite:
   - status `needs_approval`/`blocked` (política definida no plano de execução final), sem novo dispatch automático.

### Comandos propostos
- `pytest tests/test_heartbeat_v3_qa_handoff.py -q`
- `bash scripts/heartbeat-v3/qa_handoff_smoke.sh --max-cycles 2`
- `bash scripts/heartbeat-v3/qa_handoff_smoke.sh --task-id <fake_task_id> --simulate qa_reject`
- `bash scripts/heartbeat-v3/qa_handoff_smoke.sh --assert-idempotent`

---

## Checklist de segurança operacional
- Limite de retrys: definir `MAX_QA_RETRY` (ex: 3).
- Se exceder limite → não reabrir em `inbox`, mover para `needs_approval`/`review` com flag de intervenção humana.
- Nunca sobrescrever histórico de comentários.
- Idempotência de atualização MC por fingerprint + hash de bloco.
- Toda mudança de estado deve incluir comentário curto para trilha de auditoria.

---

## Métricas de sucesso
- Cada ciclo de rejeição gera exatamente um contexto novo e útil (sem loops cegos).
- Sem “no-op loop” `review -> inbox` repetitivo > 1 ciclo sem mudança de `mc_retry_count`/`QA_HANDOFF`.
- Tempo de re-dispatch após rejeição mantém padrão atual (sem atrasos de polling sem guard).
- Toda task reprocessada torna-se auto-suficiente (qualquer worker entende `Ação exigida` + `Critérios`).