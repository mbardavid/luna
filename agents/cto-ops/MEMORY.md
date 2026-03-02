# MEMORY.md — CTO-ops

## Memória de longo prazo

_Inicializado em 2026-03-01._

### Contexto operacional
- CTO-ops monitora gateway, recursos e saúde do ecossistema OpenClaw
- Política de risco definida em `config/cto-risk-policy.json`
- Runbook operacional em `docs/cto-ops-runbook.md`

### Padrões de falha conhecidos
- Gateway killing itself via `exec` — mitigado por sentinel
- OOM por retry storms — mitigado por rate limiting
- Processos orphans em portas — mitigado por cleanup automático
- npm update corrompendo instalação — mitigado por lockfile

### Decisões de saúde históricas
- (nenhuma ainda — será populado conforme operação)

### Integrações
- `scripts/mc-resource-monitor.sh` para métricas
- `scripts/gateway-health-sentinel.sh` para gateway
- `scripts/mc-watchdog.sh` para tasks
- Discord para alertas operacionais
