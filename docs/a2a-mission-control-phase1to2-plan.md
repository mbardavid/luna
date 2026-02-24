# A2A Mission Control — Diagnóstico Fase 1 + Plano Fase 2

**Autor:** pg-gpt (recuperado de session transcript)  
**Data:** 2026-02-24  

---

## ASSUMPTIONS CHECK

- **(a) Sub-agent → MC direto vs Luna proxy:** V1 mantém Luna como proxy (correto — simplifica auth/debug). V2 migra para sub-agents escrevendo direto com tokens scoped por agente. O gargalo de Luna como single-point-of-failure é real.
- **(b) Status 'review' como catch-all:** **É um bug semântico**. `mc_normalize_status()` mapeia `blocked`, `needs_approval`, `stalled`, `retry` → `review` quando fora da allowlist. Na prática a allowlist default já inclui todos esses status, mas o fallback é perigoso. Fix: rejeitar status desconhecido com erro, não mapear silenciosamente.
- **(c) Polling vs listener:** Polling 15min é correto para V1 (SLA 30min atendido). Para V2, o MC backend **já tem infraestrutura de webhooks** (`app/services/webhooks/dispatch.py` + `board_webhooks` API). Configurar webhook push, não SSE (SSE morre com a sessão da Luna).
- **(d) Bash vs Python:** **Migrar lógica complexa para Python puro.** Os 3 scripts principais (`mc-watchdog.sh`, `mc-delivery.sh`, `mc-resource-monitor.sh`) são bash que imediatamente fazem `exec python3` com heredoc de 100-300 linhas. Impossível de testar unitariamente. Proposta: criar `mc_lib/` (package Python) + `mc-cli` (CLI unificado). `mc-client.sh` pode ficar em bash — é simples e funciona.

---

## 5 BUGS ENCONTRADOS

1. **BUG-1 (P0): Watchdog spam** — Tasks sem session_key recebem 2 updates por ciclo (retry + stalled). Log confirma: `"recovered": 27, "stalled": 7` no mesmo ciclo.
2. **BUG-2 (P0): Status conflation** — `review` mistura stalled/needs_approval/review legítimo. Dashboard vira sopa.
3. **BUG-3 (P2): Idempotência assimétrica** — Guard para re-mark stalled existe, mas não para needs_approval.
4. **BUG-4 (P1): Token em cleartext** — `auth_token` hardcoded em JSON + runbook. Se commitado em git, vaza.
5. **BUG-5 (P1): Kill allowlist ativado no cron** — `--kill-allowlist` presente na crontab instalada, risco de kill acidental.

---

## PROPOSTA: WRAPPER ÚNICO `mc-cli`

```bash
# Operação atômica: create task → spawn session → link → track
mc-cli spawn --assignee luan --title "Feature X" --description "..." --json
# Retorna: {"task_id": "uuid", "session_key": "key", "status": "in_progress"}
```

Comportamento em falhas parciais:
- MC down antes de criar task → exit 1, sem side-effect
- Gateway down após criar task → task fica "inbox", watchdog retry
- MC down após spawn → session roda mas task não sabe, watchdog detecta

---

## PLANO POR FASES

### Fase 1.5 (1-2 dias) — Fix bugs P0/P1
5 cards:
- Fix watchdog spam (BUG-1)
- Fix status conflation (BUG-2)
- Fix idempotência assimétrica (BUG-3)
- Remover token hardcoded (BUG-4)
- Desativar kill por default (BUG-5)

### Fase 2.0 (3-5 dias) — Migração Python + mc-cli
7 cards:
- Criar `mc_lib/` (package Python com client, models, utils)
- Criar `mc-cli` (CLI unificado com subcommands: spawn, status, update, list, deliver)
- Wrapper atômico de spawn (create → spawn → link → track)
- Migrar watchdog de heredoc bash → Python importável
- Migrar delivery de heredoc bash → Python importável
- Migrar resource monitor de heredoc bash → Python importável
- Testes unitários para mc_lib

### Fase 2.5 (1 semana) — Webhooks + Auto-steering
4 cards:
- Configurar webhooks do MC (push notifications em vez de polling)
- Implementar auto-steering via sessions_send
- Sub-agent tokens scoped (cada agente com token próprio pro MC)
- Listener de webhooks (endpoint local que recebe push do MC)

### Fase 3.0 (1 semana) — Aprovações + Cost Tracking
3 cards:
- Aprovações Discord com botões interativos
- Tiers de aprovação (auto-approve, notify-only, require-approval)
- Cost tracking por task/agente com relatório semanal

---

## VALIDAÇÃO

- 4 cenários existentes em `fase1_qa_validate.sh` (manter)
- 5 novos cenários propostos:
  - **S5:** Anti-spam — verificar que watchdog não duplica updates
  - **S6:** Status strict — verificar que status desconhecido é rejeitado
  - **S7:** Wrapper spawn — create → spawn → link atômico
  - **S8:** Delivery idempotência — não re-entrega tasks já entregues
  - **S9:** Token security — token não aparece em logs/git
- Todos com comandos executáveis copy-paste

---

## AÇÕES JÁ EXECUTADAS PELO PG-GPT (antes do timeout)

O pg-gpt também fez várias edições nos scripts antes de dar timeout:

1. ✅ Criou `scripts/mc-approval-notify.sh` — notifica Discord sobre aprovações pendentes
2. ✅ Editou `mc-watchdog.sh` — fix de idempotência para needs_approval
3. ✅ Editou `mc-delivery.sh` — busca último comment relevante como output
4. ✅ Editou `mc-client.sh` — adicionou custom field `mc_output_summary`
5. ✅ Editou `mc-phase1-cron.sh` — kill desativado por default
6. ✅ Atualizou `docs/mission-control-phase1-runbook.md` — removeu tokens hardcoded
7. ⚠️ Algumas edits falharam por conflito (eu já tinha modificado os mesmos arquivos)
8. ❌ Timeout antes de completar todas as mudanças
