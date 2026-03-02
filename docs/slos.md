# SLOs — Service Level Objectives

**Última atualização:** 2026-03-02  
**Status:** Baseline definida, medição contínua a partir de Fase 2

---

## SLOs Definidos

| # | Métrica | SLO Alvo | Baseline Atual | Método de Medição |
|---|---------|----------|---------------|-------------------|
| S1 | Latência detecção→ação | P95 < 6 min | 15-∞ min | `state/control-loop-metrics.json` → `avg_latency_detect_to_action_min` |
| S2 | Alertas duplicados por incidente | ≤ 1 | 3-4 | Contagem manual Discord (semanal) |
| S3 | Tasks órfãs por semana | 0 | 2-3 | MC query: `status ∈ {in_progress, review} AND session_key="" AND age > 1h` |
| S4 | Completions sem QA > 30min | 0 | Ilimitado | heartbeat-v3 metrics: `qa_reviews_dispatched` vs `qa_reviews_pending_time` |
| S5 | Gateway uptime | > 99% (7.2h downtime/mês) | ~95% (~36h downtime/mês) | `journalctl -u openclaw-gateway` restart count × avg restart duration |
| S6 | PMM uptime | > 95% (36h downtime/mês) | ~80% (restarts manuais) | PID check log: `alive_checks / total_checks` |
| S7 | Cron execution rate | > 99% | Desconhecido | `cron-health-check.log` ou control-loop-metrics |

## Definições

- **Latência detecção→ação:** Tempo entre o evento ocorrer (ex: subagent completar) e Luna tomar ação (ex: QA review iniciado). Medido pelo timestamp do queue item vs timestamp da ação.
- **Alerta duplicado:** Mesmo incidente (mesmo task_id ou session_key) notificado mais de 1x em canais Discord em janela de 30min.
- **Task órfã:** Task com status ativo (`in_progress`, `review`) sem sessão de subagent associada OU com sessão morta, por mais de 1 hora.
- **Completion sem QA:** Task cujo subagent reportou `COMPLETION_STATUS: complete` mas Luna não iniciou QA review em 30min.

## Plano de Medição

### Fase 0-1 (agora): Baseline
- Registrar manualmente incidentes dos últimos 7 dias (Apêndice B do relatório v2)
- Configurar `state/control-loop-metrics.json` com contadores zerados

### Fase 2 (após redução de latência): Medição automática
- heartbeat-v3 enhanced grava métricas a cada ciclo
- Revisão diária dos contadores por 1 semana

### Fase 3+ (operação contínua): Revisão semanal
- Luna verifica SLOs durante heartbeat semanal
- Se SLO violado: investigar root cause, criar task de fix se necessário

---

## Schema de Métricas

```json
// state/control-loop-metrics.json
{
  "schema_version": 1,
  "last_updated": "2026-03-02T22:30:00Z",
  
  "counters_today": {
    "heartbeat_runs": 0,
    "opus_triggers": 0,
    "tasks_completed": 0,
    "tasks_dispatched": 0,
    "failures_detected": 0,
    "qa_reviews_dispatched": 0,
    "pmm_restarts": 0,
    "gateway_restarts": 0,
    "alerts_sent": 0,
    "duplicate_alerts_suppressed": 0
  },
  
  "latency": {
    "detect_to_action_samples_ms": [],
    "avg_detect_to_action_min": null,
    "p95_detect_to_action_min": null
  },
  
  "slo_status": {
    "S1_latency": "unknown",
    "S2_duplicate_alerts": "unknown",
    "S3_orphan_tasks": "unknown",
    "S4_completion_qa": "unknown",
    "S5_gateway_uptime": "unknown",
    "S6_pmm_uptime": "unknown",
    "S7_cron_execution": "unknown"
  },
  
  "cron_health": {
    "heartbeat-v3": {"last_run": null, "status": "unknown"},
    "mc-watchdog": {"last_run": null, "status": "unknown"},
    "gateway-sentinel": {"last_run": null, "status": "unknown"},
    "mc-resource-monitor": {"last_run": null, "status": "unknown"}
  }
}
```

---

*SLOs são revisados semanalmente. Baselines atualizadas após 1 semana de medição contínua.*
