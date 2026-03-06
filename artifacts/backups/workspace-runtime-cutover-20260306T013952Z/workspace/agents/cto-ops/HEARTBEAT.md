# HEARTBEAT.md — CTO-ops

## Checklist periódico (fonte principal de eventos de capacidade)

1. **Recursos** — verificar RAM/CPU/GPU/IO via `mc-resource-monitor.sh`
2. **Gateway** — verificar saúde via `gateway-health-sentinel.sh`
3. **Tasks** — verificar tasks travadas/orphans via `mc-watchdog.sh`
4. **Sessões** — verificar sessões grandes via `session-compact-sentinel.sh`
5. **Retry storms** — verificar padrões de retry excessivo

## Ações automáticas por resultado

| Condição | Ação | Risco |
|----------|------|-------|
| RAM > 80% | Bloquear novos spawns | Medium |
| RAM > 90% | Notificar + compactar sessões | Medium |
| RAM > 95% | Escalonar para `needs_approval` | High |
| Gateway sem heartbeat > 5min | Diagnóstico + alerta | Medium |
| Gateway morto com tasks ativas | Mover tasks para `review` | High |
| Task stalled > 60min | Mover para `stalled` | Low |
| Retry > max_retries | Mover para `needs_approval` | Medium |

## Kill-switch check

- [ ] `kill_switch_enabled` ativo?
- [ ] Ações destrutivas dentro do limite por janela?
- [ ] Algum restart loop detectado?

## Se nada pendente

Responder `HEARTBEAT_OK`.
