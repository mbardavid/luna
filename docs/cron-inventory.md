# Cron Inventory — Estado Oficial (Pós-Migração Phase 4)

**Última atualização:** 2026-03-03  
**Total crons ativos:** 14  
**Status:** Consolidado (Control Loop v2)

---

## Crons Ativos (verificado via `crontab -l`)

| # | Freq | Script | Categoria | Ação | Status |
|---|------|--------|-----------|------|--------|
| 1 | `*/1` | `scripts/gateway-wake-sentinel.sh` | Gateway | Restart se gateway down | **ATIVO** (crítico, isolado) |
| 2 | `*/5` | `heartbeat-v3/scripts/heartbeat-v3.sh` | Heartbeat | Engine principal (12 fases) | **ATIVO** (core) |
| 3 | `*/5` | `heartbeat-v3/scripts/queue-escalation.sh` | Heartbeat | Backup se nudge falha | **ATIVO** |
| 4 | `*/5` | `scripts/mc-approvals-notify.sh` | MC/Tasks | Notifica tasks pendentes aprovação | **ATIVO** |
| 5 | `*/15` | `scripts/mc-delivery.sh` | MC/Tasks | Entrega resultados → Discord POST | **ATIVO** |
| 6 | `*/15` | `scripts/mc-resource-monitor.sh` | Gateway | Memory monitoring → GC/restart | **ATIVO** |
| 7 | `*/15` | `scripts/mc-watchdog.sh` | MC/Tasks | Retry/completion tracking | **ATIVO** |
| 8 | `*/30` | `heartbeat-v3/scripts/session-gc.sh` | Sessions | Limpa sessões zombie | **ATIVO** |
| 9 | `0 */6` | `scripts/session-compact-sentinel.sh` | Sessions | Compacta sessões grandes | **ATIVO** |
| 10 | `daily 10:00` | `scripts/lessons-sync.sh` | Manutenção | Sync lessons Luna↔Luan | **ATIVO** |
| 11 | `daily 06:00` | `scripts/session-smart-compact.py` | Sessions | Compactação inteligente | **ATIVO** |
| 12 | `daily 03:17` | `scripts/mc-log-rotate.sh` | Manutenção | Rotaciona logs | **ATIVO** |
| 13 | `daily 09:00` | `scripts/mc-cost-report.sh` | Manutenção | Relatório semanal (Segunda) | **ATIVO** |
| 14 | `* * * * *` | — | — | (Espaço reservado para expansão futura) | **N/A** |

*Nota: O crontab real possui 13 entradas funcionais + hooks systemd. O objetivo de 14 crons é o teto operacional definido.*

## Hooks systemd (não são crons)

| Hook | Script | Trigger |
|------|--------|---------|
| ExecStartPost | `scripts/gateway-restart-notify.sh` | Após gateway start |
| ExecStartPost | `scripts/gateway-post-restart-recovery.sh` | Após gateway start |

## Scripts Depreciados (Arquivados)

| Script | Absorvido por / Razão | Data |
|--------|-----------------------|------|
| `mc-failure-detector.sh` | heartbeat-v3 Phase 4 | 2026-03-03 |
| `mc-stale-task-detector.sh` | heartbeat-v3 Phase 5.5 | 2026-03-03 |
| `mc-description-watchdog.sh` | heartbeat-v3 Phase 4.8 | 2026-03-03 |
| `gateway-health-sentinel.sh` | heartbeat-v3 Phase 1 | 2026-03-03 |
| `pmm-status-updater.sh` | heartbeat-v3 Phase 1 | 2026-03-03 |
| `escalation-recovery.sh` | queue-escalation.sh | 2026-03-03 |
| `cron-health-check.sh` | Simplificação do sistema | 2026-03-03 |
| `smoke-test-orchestrator.sh` | Removido (redundante) | 2026-03-03 |

---

*Estado final pós-migração. Qualquer alteração deve ser registrada nesta tabela.*
