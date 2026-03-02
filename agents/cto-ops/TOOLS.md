# TOOLS.md — CTO-ops

## Ambiente local
- `OPENCLAW_BIN`: utilitário principal (`openclaw`)
- `MC_CLIENT`: `scripts/mc-client.sh`
- `MC_POLICY`: `config/cto-risk-policy.json`

## Aliases úteis
- `bash scripts/mc-resource-monitor.sh`
- `bash scripts/gateway-health-sentinel.sh`
- `bash scripts/heartbeat-v2.sh`
- `bash scripts/mc-watchdog.sh`
- `bash scripts/mc-cron-guard.sh kill-switch`

## Operação segura
- Nenhuma ação destrutiva sem validação de `kill-switch` e trilha em `docs/AGENT_AUDIT_LOG.md`.
