# Runbook CTO-ops (Operações de Saúde, Recursos e Gateway)

## Mandato

`cto-ops` é o agente operacional de saúde. Ele não decide estratégia de produto, apenas controla risco de infraestrutura, recurso e continuidade.

## Perímetro

- Monitorar:
  - memória/CPU/GPU/IO
  - sessão gateway/subagentes
  - estado de heartbeat/MC/watchdogs
  - eventos de retry/stall/review

## Níveis de risco e resposta

### Baixo

- Diagnóstico e alerta
- Sem auto-ação
- Registro no `docs/AGENT_AUDIT_LOG.md`

### Médio

- Ação automática com confirmação assíncrona em `mc-output`
- Regras: max 1 ação por 10 minutos por subsistema

### Alto

- Ação automática com guardrails rigorosos
- Gateway restart **permitido** via `scripts/gateway-safe-restart.sh` (NUNCA direto)
- Rate limit: max 3 restarts/hora
- Requer motivo documentado no audit log

## Procedimentos padrão

### 1. CPU/Memória alta

1. marcar `resource_pressure`
2. bloquear novos spawns de risco
3. notificar em `discord`
4. abrir evento de revisão no `orchestration-state`

### 1b. Gateway Memory Pressure (>80% do high watermark)

**Trigger automático** via `mc-resource-monitor.sh`:

1. Detecta gateway memory > 80% do cgroup high watermark
2. Executa `scripts/gateway-safe-restart.sh --auto --reason "<motivo>"`
3. O script faz (nesta ordem):
   - Rate limit check (max 3/hora)
   - Limpa cron runs mortos do `sessions.json`
   - Remove arquivos `.deleted` e `.corrupt` do disco
   - Notifica `#general-luna` que restart está vindo
   - Espera 3s para notificação entregar
   - Executa `sudo systemctl restart openclaw-gateway`
   - ExecStartPost hook notifica quando gateway volta
4. Resultado esperado: ~50-60% de redução de memória

**⚠️ NUNCA usar diretamente:**
- `sudo systemctl restart openclaw-gateway`
- `sudo systemctl stop openclaw-gateway`  
- `kill -USR1 <pid>`

**Sempre usar:** `scripts/gateway-safe-restart.sh [--auto] [--reason "..."]`

**Logs:**
- `logs/gateway-safe-restart.log` — execução do script
- `logs/cto-ops-audit.log` — trilha de auditoria

### 2. Sessão gateway morta com tarefas em andamento

1. mover tarefa para `review`
2. registrar sessão perdida e `taskId`
3. emitir retry policy baseada no histórico

### 3. Retry storm / erro repetitivo

1. congelar execução em cadeia por janela de 30 min
2. enviar alerta de incidente para a fila de revisão

### 4. Incidente com gateway zumbi

1. enviar alerta direto (API + fallback)
2. coletar `journalctl` + estado MC
3. só escalar para ação manual após trilha de evento registrada

## Kill-switch global

- `kill_switch_enabled` default: true para ações destrutivas
- `kill_switch_window_ms`: 5 minutos
- `kill_critical_retries`: max 2 por janela
- Se excedido, ação cai em `needs_approval`

## Artefatos

- `memory/lessons.md`: decisões de saúde
- `memory/orchestration-state.json`: decisão + trilha
- `docs/AGENT_AUDIT_LOG.md`: resumo auditável
