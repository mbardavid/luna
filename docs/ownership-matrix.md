# Ownership Matrix — Matriz de Responsabilidade Consolidada (Phase 4)

**Última atualização:** 2026-03-03  
**Status:** CONSOLIDADO (Pós-Migração Control Loop v2)

---

## Regra de Ouro

> Cada tipo de evento tem **exatamente um owner**. Se dois scripts detectam o mesmo evento, um é redundante. O owner é quem **age** — o outro deve ser depreciado.

---

## Matriz de Responsabilidade (Estado Final)

| Tipo de Evento | Owner | Ação | Status |
|----------------|-------|------|--------|
| **Gateway down** | `gateway-wake-sentinel` (*/1) | `systemctl restart` | ✅ OK |
| **Gateway health** | `heartbeat-v3` Phase 1 | Log + Alert | ✅ OK |
| **Gateway memory > threshold** | `mc-resource-monitor` (*/15) | Session GC / Safe restart | ✅ OK |
| **Subagent falhou** | `heartbeat-v3` Phase 4 | Queue respawn + Classify | ✅ OK |
| **Task completion pendente QA** | `heartbeat-v3` Phase 5.5 | Queue `qa-review` + Nudge | ✅ OK |
| **Task stale/órfã** | `heartbeat-v3` Phase 5.5 | Alert + Nudge | ✅ OK |
| **Descrição ruim no MC** | `heartbeat-v3` Phase 4.8 | Log + Alert | ✅ OK |
| **Inbox com tasks elegíveis** | `heartbeat-v3` Phase 7-9 | Queue `dispatch` + Nudge | ✅ OK |
| **Review tasks (Luan→Luna)** | `heartbeat-v3` Phase 6 | Queue `review-dispatch` | ✅ OK |
| **Task retry/completion** | `mc-watchdog` (*/15) | Auto-retry (max 2) | ✅ OK |
| **Delivery de resultados** | `mc-delivery` (*/15) | POST Discord | ✅ OK |
| **Queue stuck > 15min** | `queue-escalation` (*/5) | Escalation alert | ✅ OK |
| **Session cleanup** | `session-gc` (*/30) | Limpa sessões zombie | ✅ OK |
| **PMM morto** | `heartbeat-v3` Phase 1 | **Auto-restart + Log** | ✅ OK |
| **Tasks pendentes aprovação** | `mc-approvals-notify` (*/5) | Discord notify | ✅ OK |
| **Gateway restart** | `gateway-restart-notify` (hook) | Discord notify | ✅ OK |
| **Post-restart recovery** | `gateway-post-restart-recovery` (hook) | Snapshot-less recovery + Context respawn | ✅ OK |

---

## Regras de Governança e Operação Contínua

1. **Antes de criar novo detector:** Verificar esta matriz. Se o tipo de evento já tem owner, adicionar ao owner existente (preferencialmente `heartbeat-v3`).
2. **Se heartbeat-v3 não pode cobrir:** Justificar por escrito por que precisa ser script separado. Adicionar à matriz com owner explícito.
3. **Revisão Semanal (Luna):** 
    - Validar se a matriz reflete o crontab real.
    - Checar `control-loop-metrics.json` para performance.
    - Revisar logs do `heartbeat-v3` por novos patterns de falha.
4. **Tratamento de Incidentes:** Se um tipo de evento não estava na matriz, adicioná-lo com owner definido antes de criar qualquer script de remediação.

---

*Estado final pós-migração. Qualquer alteração deve ser registrada nesta tabela.*
