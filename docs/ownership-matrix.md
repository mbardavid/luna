# Ownership Matrix — Quem é Dono de Cada Tipo de Evento

**Última atualização:** 2026-03-02  
**Status:** PRÉ-MIGRAÇÃO (reflete estado atual + alvo pós Fase 1)

---

## Regra de Ouro

> Cada tipo de evento tem **exatamente um owner**. Se dois scripts detectam o mesmo evento, um é redundante. O owner é quem **age** — o outro deve ser depreciado.

---

## Matriz Atual (pré-migração)

| Tipo de Evento | Owner Atual | Backup | Ação Atual | Problema |
|----------------|-------------|--------|------------|----------|
| Gateway down | gateway-wake-sentinel (*/1) | — | systemctl restart | ✅ OK |
| Gateway health degradado | gateway-health-sentinel (*/2) | heartbeat-v3 Phase 1 | Log + alert | ⚠️ Duplicado |
| Gateway memory > threshold | mc-resource-monitor (*/15) | — | Session GC / safe restart | ✅ OK |
| Subagent falhou | heartbeat-v3 Phase 4 | mc-failure-detector (*/5) | Queue respawn + nudge | ⚠️ Duplicado |
| Task stale (sem session) | heartbeat-v3 Phase 5.5 | mc-stale-task-detector (*/10) | Alert + nudge | ⚠️ Duplicado |
| Task completion pendente QA | **NINGUÉM** | — | — | 🔴 GAP CRÍTICO |
| Task órfã (sem session_key) | mc-stale-task-detector (*/10) | — | Notifica | ⚠️ Só notifica |
| Descrição ruim no MC | mc-description-watchdog (*/15) | — | Notifica | ⚠️ Só notifica |
| Inbox com tasks elegíveis | heartbeat-v3 Phase 7-9 | — | Queue dispatch + nudge | ✅ OK |
| Review tasks (Luan→Luna) | heartbeat-v3 Phase 6 | — | Queue review-dispatch | ✅ OK |
| Task retry/completion | mc-watchdog (*/15) | — | Auto-retry (max 2) | ✅ OK |
| Delivery de resultados | mc-delivery (*/15) | — | POST Discord | ✅ OK |
| Queue stuck > 15min | queue-escalation (*/5) | — | Escalation alert | ✅ OK |
| Escalation travada | escalation-recovery (*/30) | queue-escalation | Recovery | ⚠️ Duplicado |
| Session cleanup | session-gc (*/30) | — | Limpa zombies | ✅ OK |
| PMM morto | pmm-status-updater (*/15) | — | Atualiza MC card | ⚠️ Só atualiza status, não reinicia |
| Tasks pendentes aprovação | mc-approvals-notify (*/5) | — | Discord notify | ✅ OK |
| Cron health | cron-health-check (*/30) | — | Notifica se cron falhou | ⚠️ Valor baixo |
| PMM smoke test | smoke-test-orchestrator (*/30) | — | Smoke test | ⚠️ Valor questionável |
| Gateway restart | gateway-restart-notify (hook) | — | Discord notify | ✅ OK |
| Post-restart recovery | gateway-post-restart-recovery (hook) | — | PMM restart + tasks inbox | ✅ OK (com gaps a corrigir) |

**Problemas identificados:**
- 🔴 1 gap crítico (completion pendente QA)
- ⚠️ 8 duplicações ou deficiências

---

## Matriz Alvo (pós Fase 1)

| Tipo de Evento | Owner | Ação | Mudança |
|----------------|-------|------|---------|
| Gateway down | gateway-wake-sentinel (*/1) | systemctl restart | Sem mudança |
| Gateway health | heartbeat-v3 Phase 1 | Log + alert | Absorve gateway-health-sentinel |
| Gateway memory > threshold | mc-resource-monitor (*/15) | Session GC / safe restart | Sem mudança |
| Subagent falhou | heartbeat-v3 Phase 4 | Queue respawn + classify | Absorve mc-failure-detector |
| Task completion pendente QA | heartbeat-v3 Phase 5.5 | **Queue qa-review + nudge** | **NOVO** |
| Task stale/órfã | heartbeat-v3 Phase 5.5 | Alert + nudge | Absorve mc-stale-task-detector |
| Descrição ruim | heartbeat-v3 Phase 4.8 | Log + alert | Absorve mc-description-watchdog |
| Inbox com tasks | heartbeat-v3 Phase 7-9 | Queue dispatch + nudge | Sem mudança |
| Review tasks | heartbeat-v3 Phase 6 | Queue review-dispatch | Sem mudança |
| Task retry/completion | mc-watchdog (*/15) | Auto-retry (max 2) | Sem mudança |
| Delivery de resultados | mc-delivery (*/15) | POST Discord | Sem mudança |
| Queue stuck > 15min | queue-escalation (*/5) | Escalation alert | Sem mudança |
| Session cleanup | session-gc (*/30) | Limpa zombies | Sem mudança |
| PMM morto | heartbeat-v3 Phase 1 | **Auto-restart + log** | Absorve pmm-status-updater |
| Tasks pendentes aprovação | mc-approvals-notify (*/5) | Discord notify | Sem mudança |
| Gateway restart | gateway-restart-notify (hook) | Discord notify | Sem mudança |
| Post-restart recovery | gateway-post-restart-recovery (hook) | Snapshot-less recovery + respawn-with-context | **ENHANCED** |

**Resultado:**
- 🔴 0 gaps
- ⚠️ 0 duplicações
- Cada evento tem exatamente 1 owner

---

## Regras de Governança

1. **Antes de criar novo detector:** Verificar ownership matrix. Se o tipo de evento já tem owner, adicionar ao owner existente (não criar script novo).
2. **Se heartbeat-v3 não pode cobrir:** Justificar por escrito por que precisa ser script separado. Adicionar à matriz com owner explícito.
3. **Revisão semanal:** Luna verifica se a matriz reflete o crontab real. Divergência = bug.
4. **Após cada incidente:** Se um tipo de evento não estava na matriz, adicioná-lo com owner definido antes de criar qualquer script.

---

*Matriz viva — atualizada conforme cada fase é executada.*
