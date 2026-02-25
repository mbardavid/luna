# MC Automation Upgrade — Spec v1

## Problema
Luna esquece de atualizar MC quando subagents completam, criam tasks sem assigned_agent_id, 
e o ciclo de vida depende de "lembrar" em vez de automação.

## Objetivo
Scripts bash que automatizam o ciclo de vida das tasks no MC **sem depender do contexto de IA**.
Luna chama scripts simples em vez de fazer curl manual.

## Scripts existentes (já funcionam)
| Script | Função | Status |
|--------|--------|--------|
| `mc-client.sh` | CRUD genérico da API MC | ✅ OK |
| `mc-spawn-wrapper.sh` | Cria task MC + gera payload de spawn | ✅ OK mas Luna não usa |
| `mc-task-update.sh` | Parseia TASK_UPDATE e atualiza MC | ✅ OK |
| `mc-link-task-session.sh` | Liga session_key a task | ✅ OK |
| `mc-watchdog.sh` | Detecta tasks stalled, re-spawn | ✅ OK (cron 15min) |
| `mc-delivery.sh` | Entrega resultados no Discord | ✅ OK (cron 15min) |
| `mc-resource-monitor.sh` | Monitora RAM/CPU, kill orphans | ✅ OK (cron 15min) |
| `mc-cron-guard.sh` | Previne concorrência heartbeat/crons | ✅ OK |

## O que falta (gaps)

### Gap 1: `mc-spawn.sh` — Spawn atômico (MC + sessions_spawn + link)
**Problema:** Luna faz 3 chamadas separadas (criar task, spawn, linkar) e às vezes esquece uma.
**Solução:** Script único que faz tudo atomicamente.

```bash
mc-spawn.sh \
  --agent luan \
  --title "Fix Hyperliquid auth" \
  --task "Investigar e corrigir auth..." \
  --timeout 900 \
  --priority high
```

Internamente:
1. Cria task no MC (status: in_progress, assigned_agent_id correto)
2. Chama `openclaw sessions spawn` (ou usa a tool via stdin)
3. Linka mc_session_key
4. Retorna task_id + session_key

**Nota:** `mc-spawn-wrapper.sh` já faz os steps 1+3 mas NÃO executa o spawn.
Este script deve executar o spawn via `openclaw` CLI.

### Gap 2: `mc-complete.sh` — Marca task como done
**Problema:** Quando subagent completa, Luna tem que lembrar de atualizar MC.
**Solução:** Script chamado no turno de completion.

```bash
mc-complete.sh \
  --task-id ace7d4a5 \
  --summary "Luan criou conector com 13 testes" \
  --notify-channel 1473367119377731800
```

Internamente:
1. PATCH task → status: done
2. Set mc_output_summary
3. Set mc_delivered: true
4. (Opcional) Envia notificação no Discord

### Gap 3: `mc-fail.sh` — Marca task como failed + retry logic
```bash
mc-fail.sh \
  --task-id ace7d4a5 \
  --error "Timeout after 900s" \
  --retry  # incrementa mc_retry_count, re-spawna se < max
```

### Gap 4: Integração no HEARTBEAT.md e AGENTS.md
Atualizar os protocolos pra referenciar os scripts em vez de instruções manuais:
- "Quando spawnar: chamar `mc-spawn.sh`" (não "criar task + spawn + linkar")
- "Quando subagent completar: chamar `mc-complete.sh`" (não "lembrar de fazer PATCH")
- "Quando subagent falhar: chamar `mc-fail.sh`"

### Gap 5: Watchdog melhorado — detectar subagents completos sem MC update
O watchdog já detecta tasks stalled. Adicionar:
- Checar se subagent linked à task já completou (via `openclaw sessions list`)
- Se completou mas task ainda é in_progress → auto-completar via `mc-complete.sh`
- Isso é o safety net pra quando Luna esquece

### Gap 6: `assigned_agent_id` automático
O MC aceita `assigned_agent_id` no PATCH mas Luna não seta.
Todos os scripts devem setar automaticamente baseado no agent:
- agent=luan → assigned_agent_id=ccd2e6d0
- agent=crypto-sage → assigned_agent_id=ad3cf364
- agent=main (Luna) → assigned_agent_id=70bd8378
- agent=quant-strategist → assigned_agent_id=b66bda98

Lookup table em `config/mc-agent-ids.json`:
```json
{
  "luan": "ccd2e6d0",
  "crypto-sage": "ad3cf364",
  "main": "70bd8378",
  "quant-strategist": "b66bda98"
}
```

## Prioridade de implementação
1. **`mc-spawn.sh`** — maior impacto, elimina 3 steps manuais
2. **`mc-complete.sh`** — elimina o gap mais frequente (esquecer de marcar done)
3. **Gap 6 (assigned_agent_id)** — integrar nos scripts acima
4. **Gap 5 (watchdog upgrade)** — safety net
5. **`mc-fail.sh`** — menos frequente mas importante

## Referências
- MC API: `http://localhost:8000`
- Board ID: `0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47`
- Auth: `config/mission-control-ids.json`
- Agent IDs: Luna=70bd8378, Luan=ccd2e6d0, Crypto Sage=ad3cf364, Quant Strategist=b66bda98
- Scripts: `/home/openclaw/.openclaw/workspace/scripts/mc-*.sh`
- OpenClaw CLI: verificar se `openclaw sessions spawn` existe como comando
