# Cron Inventory — Estado Final Pós-Migração

**Última atualização:** 2026-03-03  
**Total crons ativos:** 13  
**Migração Control Loop v2:** COMPLETA

---

## Crons Ativos (13)

| # | Freq | Script | Categoria | Função |
|---|------|--------|-----------|--------|
| 1 | `*/1` | `scripts/gateway-wake-sentinel.sh` | Gateway | Restart se gateway down |
| 2 | `*/5` | `heartbeat-v3/scripts/heartbeat-v3.sh` | Heartbeat | Engine principal (PMM health, failure classification, description quality, stale/completion detection, dispatch, review) |
| 3 | `*/5` | `heartbeat-v3/scripts/queue-escalation.sh` | Heartbeat | Escala queue stuck > 15min |
| 4 | `*/5` | `scripts/mc-approvals-notify.sh` | MC/Tasks | Notifica tasks pendentes aprovação |
| 5 | `*/15` | `scripts/mc-delivery.sh` | MC/Tasks | Entrega resultados → Discord |
| 6 | `*/15` | `scripts/mc-resource-monitor.sh` | Gateway | Memory monitoring → GC/restart |
| 7 | `*/15` | `scripts/mc-watchdog.sh` | MC/Tasks | Retry/completion tracking |
| 8 | `*/30` | `heartbeat-v3/scripts/session-gc.sh` | Sessions | Limpa sessões zombie |
| 9 | `0 */6` | `scripts/session-compact-sentinel.sh` | Sessions | Compacta sessões grandes |
| 10 | `daily 10:00` | `scripts/lessons-sync.sh` | Manutenção | Sync lessons Luna↔Luan |
| 11 | `daily 06:00` | `scripts/session-smart-compact.py` | Sessions | Compactação inteligente |
| 12 | `daily 03:17` | `scripts/mc-log-rotate.sh` | Manutenção | Rotaciona logs |
| 13 | `Mon 09:05` | `scripts/mc-cost-report.sh` | Manutenção | Relatório semanal |

## Scripts Depreciados (7 → `scripts/archive/`)

| Script | Absorvido por | Data | Razão |
|--------|---------------|------|-------|
| `mc-failure-detector.sh` | heartbeat-v3 Phase 4 | 2026-03-03 | classify_failure() com 6 categorias |
| `mc-stale-task-detector.sh` | heartbeat-v3 Phase 5.5 | 2026-03-03 | detect_stale_and_completions() |
| `mc-description-watchdog.sh` | heartbeat-v3 Phase 4.8 | 2026-03-03 | check_description_quality() |
| `gateway-health-sentinel.sh` | heartbeat-v3 Phase 1 | 2026-03-03 | gateway health check integrado |
| `pmm-status-updater.sh` | heartbeat-v3 Phase 1 | 2026-03-03 | check_pmm_health() com auto-restart |
| `escalation-recovery.sh` | queue-escalation.sh | 2026-03-03 | funcionalidade redundante |
| `cron-health-check.sh` | eliminado | 2026-03-03 | baixo valor com 13 crons |

## Hooks systemd (não são crons)

| Hook | Script | Trigger |
|------|--------|---------|
| ExecStartPost | `scripts/gateway-restart-notify.sh` | Após gateway start |
| ExecStartPost | `scripts/gateway-post-restart-recovery.sh` | Após gateway start (snapshot-less recovery + respawn-with-context) |

## Resultado da Migração

| Métrica | Antes | Depois |
|---------|-------|--------|
| Total crons | 21 | 13 |
| Latência detecção→ação | 10-30 min | 5 min |
| Scripts de detecção | 5 separados | 1 (heartbeat-v3.py, 1770 LOC) |
| PMM restart | manual | automático (cooldown 5min, max 3/hr, crash loop alert) |
| Completion → QA | manual | qa-review queue automático |
| Post-restart recovery | só com snapshot | snapshot-less via MC API |
| Testes | 57 | 130+ |

---

*Inventário congelado em 2026-03-03. Qualquer adição de cron deve ser justificada na ownership-matrix.*
