# Cron Inventory — Estado Oficial

**Última atualização:** 2026-03-02  
**Total crons ativos:** 21  
**Baseline para migração Control Loop v2**

---

## Crons Ativos (verificado via `crontab -l`)

| # | Freq | Script | Categoria | Ação | Status Migração |
|---|------|--------|-----------|------|-----------------|
| 1 | `*/1` | `scripts/gateway-wake-sentinel.sh` | Gateway | Restart se gateway down | **MANTER** (crítico, isolado) |
| 2 | `*/2` | `scripts/gateway-health-sentinel.sh` | Gateway | Health check profundo | **DEPRECIAR** → absorvido por heartbeat-v3 Phase 1 |
| 3 | `*/5` | `scripts/mc-failure-detector.sh` | MC/Detection | Detecta sessões falhadas → notifica | **DEPRECIAR** → absorvido por heartbeat-v3 Phase 4 |
| 4 | `*/5` | `heartbeat-v3/scripts/queue-escalation.sh` | Heartbeat | Escala queue stuck > 15min | **MANTER** (backup se nudge falha) |
| 5 | `*/5` | `scripts/mc-approvals-notify.sh` | MC/Tasks | Notifica tasks pendentes aprovação | **MANTER** |
| 6 | `*/10` | `scripts/mc-stale-task-detector.sh` | MC/Detection | Detecta tasks órfãs → notifica | **DEPRECIAR** → absorvido por heartbeat-v3 Phase 5.5 |
| 7 | `*/10` | `heartbeat-v3/scripts/heartbeat-v3.sh` | Heartbeat | Engine principal (9 fases) | **MANTER** (core, evoluir para */5) |
| 8 | `*/15` | `scripts/pmm-status-updater.sh` | PMM | Atualiza status do bot no MC | **DEPRECIAR** → absorvido por heartbeat-v3 Phase 1 |
| 9 | `*/15` | `scripts/mc-description-watchdog.sh` | MC/Detection | Audita descrições → notifica | **DEPRECIAR** → absorvido por heartbeat-v3 Phase 4.8 |
| 10 | `*/15` | `scripts/mc-delivery.sh` | MC/Tasks | Entrega resultados → Discord POST | **MANTER** |
| 11 | `*/15` | `scripts/mc-resource-monitor.sh` | Gateway | Memory monitoring → GC/restart | **MANTER** |
| 12 | `*/15` | `scripts/mc-watchdog.sh` | MC/Tasks | Retry/completion tracking → age | **MANTER** |
| 13 | `*/30` | `heartbeat-v3/scripts/escalation-recovery.sh` | Heartbeat | Recupera escalations travadas | **DEPRECIAR** → queue-escalation.sh já cobre |
| 14 | `*/30` | `heartbeat-v3/scripts/session-gc.sh` | Sessions | Limpa sessões zombie | **MANTER** |
| 15 | `*/30` | `polymarket-mm/scripts/smoke-test-orchestrator.sh` | PMM | Smoke test do pipeline | **DEPRECIAR** → valor questionável em produção |
| 16 | `*/30` | `scripts/cron-health-check.sh` | Manutenção | Verifica saúde dos crons | **DEPRECIAR** → desnecessário com 14 crons |
| 17 | `0 */6` | `scripts/session-compact-sentinel.sh` | Sessions | Compacta sessões grandes | **MANTER** |
| 18 | `daily 10:00` | `scripts/lessons-sync.sh` | Manutenção | Sync lessons Luna↔Luan | **MANTER** |
| 19 | `daily 06:00` | `scripts/session-smart-compact.py` | Sessions | Compactação inteligente | **MANTER** |
| 20 | `daily 03:17` | `scripts/mc-log-rotate.sh` | Manutenção | Rotaciona logs | **MANTER** |
| 21 | `Mon 09:05` | `scripts/mc-cost-report.sh` | Manutenção | Relatório semanal | **MANTER** |

## Hooks systemd (não são crons)

| Hook | Script | Trigger |
|------|--------|---------|
| ExecStartPost | `scripts/gateway-restart-notify.sh` | Após gateway start |
| ExecStartPost | `scripts/gateway-post-restart-recovery.sh` | Após gateway start |

## Resumo da Migração

| Categoria | Antes | Depois | Delta |
|-----------|-------|--------|-------|
| Total crons | 21 | 14 | -7 |
| MC/Detection | 3 | 0 | -3 (absorvidos pelo heartbeat-v3) |
| Gateway | 2 | 1 | -1 (health-sentinel absorvido) |
| Heartbeat | 3 | 2 | -1 (escalation-recovery redundante) |
| PMM | 2 | 0 | -2 (absorvidos pelo heartbeat-v3) |
| Manutenção | 2 | 1 | -1 (cron-health-check desnecessário) |
| Mantidos inalterados | — | 14 | — |

## Scripts Candidatos a Depreciação (7)

| Script | Absorvido por | Fase |
|--------|---------------|------|
| `mc-failure-detector.sh` | heartbeat-v3 Phase 4 enhanced | Fase 3 D1 |
| `mc-stale-task-detector.sh` | heartbeat-v3 Phase 5.5 enhanced | Fase 3 D2 |
| `mc-description-watchdog.sh` | heartbeat-v3 Phase 4.8 | Fase 3 D3 |
| `gateway-health-sentinel.sh` | heartbeat-v3 Phase 1 | Fase 3 D1 |
| `pmm-status-updater.sh` | heartbeat-v3 Phase 1 (PMM check) | Fase 3 D3 |
| `escalation-recovery.sh` | queue-escalation.sh | Fase 3 D2 |
| `cron-health-check.sh` | Eliminado | Fase 3 D3 |
| `smoke-test-orchestrator.sh` | Eliminado | Fase 3 D3 |

**Nota:** São 8 scripts candidatos mas 21→14 = 7 remoções (smoke-test é o 8º candidato, pode ser mantido se validação mostrar valor).

---

*Baseline congelada em 2026-03-02. Qualquer mudança no crontab antes da migração deve ser registrada aqui.*
