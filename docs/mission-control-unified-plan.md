# Mission Control — Plano Unificado de Orquestração

**Status:** Draft v2 — 2026-02-23 (incorpora review GPT + Luna)  
**Autor:** Luna (merge plano original + plano externo + review GPT)  
**Objetivo:** Tornar a Luna a operadora central (CEO tática) com autonomia real, resiliência a falhas, e oversight humano mínimo.

---

## Problemas que Este Plano DEVE Resolver

### P1: Tarefas longas sem feedback
**Sintoma:** Luna spawna sub-agente para tarefa longa, tarefa termina, mas Matheus nunca recebe o resultado.  
**Causa raiz:** `sessions_spawn` é fire-and-forget. Se a sessão da Luna expira ou o gateway recicla antes do sub-agente terminar, o resultado se perde.  
**Solução:** Task state machine no MC + polling ativo via heartbeat + delivery guarantee via MC webhook/cron.

### P2: Gateway restart = estado perdido
**Sintoma:** Gateway reinicia (update, crash, manutenção), todas as sessões ativas morrem, sub-agentes em execução perdem contexto.  
**Causa raiz:** Estado de orquestração vive apenas em memória de sessão (JSON local + `orchestration-state.json`).  
**Solução:** MC como fonte de verdade persistente. Ao reiniciar, Luna lê MC, identifica tasks `In_Progress` sem conclusão, e retoma ou re-spawna.

### P3: Sem visibilidade real-time dos agentes
**Sintoma:** Luna não sabe o que Luan/Crypto Sage estão fazendo até eles terminarem (ou falharem silenciosamente).  
**Causa raiz:** Não existe polling intermediário nem reporting obrigatório dos sub-agentes.  
**Solução:** Heartbeat protocol + progress reports obrigatórios + auto-steering quando desvio é detectado.

### P4: Sem auto-correção
**Sintoma:** Sub-agente segue direção errada por muitos tokens antes de alguém perceber.  
**Causa raiz:** Não existe mecanismo de review intermediário.  
**Solução:** Checkpoint gates + Luna faz steering via `sessions_send` quando detecta desvio.

---

## Princípios de Design (V1)

1. **Luna é o único escritor no MC.** Sub-agentes NÃO falam com MC diretamente. Reportam pra Luna com formato `TASK_UPDATE`, Luna espelha no MC. Simplifica segurança e debugging.
2. **taskId ⇄ sessionKey é first-class.** Toda task `In_Progress` tem sessionKey linkado. Sem isso, recovery é heurística.
3. **Recovery usa `sessions_list` (não `subagents list`).** `sessions_list` cobre sessões de cron, outros agentes, e sessões não-spawn. Mais robusto.
4. **Degrade > Kill.** Sob pressão de recursos, degrada (pausa spawns, move tasks pra Blocked) antes de matar qualquer coisa.

---

## Arquitetura

```
┌─────────────────────────────────────────────────┐
│                   MATHEUS (Juiz)                 │
│         Canal #approvals — botões Discord        │
│         Só chamado por tipo de risco (ver tiers) │
└─────────────┬───────────────────────┬───────────┘
              │ aprovar/rejeitar      │ visualizar
              ▼                       ▼
┌─────────────────────────────────────────────────┐
│              MISSION CONTROL (Cofre)             │
│     API: localhost:8000 — Dashboard: :3000       │
│     Fonte de verdade para tasks, status, logs    │
│     Webhooks para notificação de mudança         │
└─────────────┬───────────────────────────────────┘
              │ CRUD tasks + read state
              │ (SOMENTE Luna escreve — V1)
              ▼
┌─────────────────────────────────────────────────┐
│                 LUNA (Cérebro)                    │
│     Orquestradora central — lê/escreve MC        │
│     Spawn wrapper: cria task MC → spawna agente  │
│     Monitoring: sessions_list + sessions_history │
│     Auto-steering: sessions_send quando desvio   │
│     Recovery: lê MC pós-restart e retoma         │
└──────┬──────────────────┬───────────────────────┘
       │ sessions_spawn    │ sessions_send (steer)
       ▼                   ▼
┌──────────────┐  ┌────────────────┐
│    LUAN       │  │  CRYPTO SAGE   │
│  (Coder)      │  │  (Finance)     │
│  Recebe task  │  │  Recebe task   │
│  Reporta Luna │  │  Reporta Luna  │
│  (TASK_UPDATE)│  │  (TASK_UPDATE) │
└──────────────┘  └────────────────┘
```

---

## Protocolo TASK_UPDATE (sub-agente → Luna)

Sub-agentes incluem este bloco na resposta quando atualizam progresso:

```json
TASK_UPDATE {
  "taskId": "mc-task-uuid",
  "status": "in_progress|done|failed|blocked",
  "progress": 65,
  "summary": "Implementei X, falta Y",
  "error": null,
  "artifacts": ["path/to/file"]
}
```

Luna parseia, valida, e espelha no MC via API. Se sub-agente não incluir TASK_UPDATE, Luna infere status do output.

---

## Fases de Implementação

### Fase 0: MC Bridge + Spawn Wrapper (Pré-requisito — 2-3 dias)

**Objetivo:** Luna consegue ler/escrever tasks no MC e spawnar agentes com tracking.

- **0.1 MC API Client** (`scripts/mc-client.sh`):
  - `mc_create_task(title, description, assignee_id, priority)` → retorna task_id
  - `mc_update_task(task_id, status, comment)` 
  - `mc_list_tasks(status_filter)` → lista com taskId + sessionKey
  - `mc_get_task(task_id)` → detalhes completos
  - `mc_create_approval(task_id, description)` → cria approval request
  - `mc_add_comment(task_id, text)` → adiciona checkpoint comment
  - Auth: usa token de `config/mission-control-ids.json`
  
- **0.2 Spawn Wrapper Protocol:**
  Toda vez que Luna spawna sub-agente, o fluxo é:
  1. `mc_create_task(...)` → obtém `taskId`
  2. `sessions_spawn(task=..., label=taskId)` → obtém `sessionKey`
  3. `mc_update_task(taskId, status=In_Progress, comment="sessionKey={sessionKey}")` → linka
  4. Instrução do spawn inclui: "Ao concluir cada etapa, inclua TASK_UPDATE {...} na resposta"

- **0.3 Validação:**
  - [ ] Criar task fake no MC via script, confirmar no dashboard
  - [ ] Spawnar sub-agente de teste com tracking, confirmar link taskId ⇄ sessionKey
  - [ ] Sub-agente termina, Luna recebe resultado e atualiza MC
  - **Comando de teste:** `./scripts/mc-client.sh create-task "Test Task" "Hello World" luna`

### Fase 1: Resiliência e Retomada (Semana 1)

**Objetivo:** Gateway pode reiniciar sem perder estado de orquestração.

- **1.1 Task State Machine no MC:**
  ```
  Created → Assigned → In_Progress → Review → Done
                                    ↘ Failed → Retry (max 2) → In_Progress
                                    ↘ Needs_Approval → (aguarda Matheus) → In_Progress
                                    ↘ Blocked (resource_pressure | dependency)
                                    ↘ Stalled (sem update > 1h)
  ```

- **1.2 Startup Recovery Protocol:**
  Ao iniciar (heartbeat, nova sessão, ou pós-restart), Luna executa:
  1. `mc_list_tasks(status=In_Progress)` → tasks ativas
  2. Para cada task, extrair `sessionKey` do comment/campo
  3. `sessions_list` → verificar se sessão ainda existe e está ativa
  4. Se sessão morreu:
     - Se task tem menos de 2 retries → re-spawn com contexto do MC
     - Se task tem 2+ retries → marcar `Needs_Approval`
  5. Se sessão existe → `sessions_history(sessionKey, includeTools=true)` → verificar progresso
  6. Atualizar MC com status real
  - **Trigger:** heartbeat (30min) + cron pós-restart + cron `mc-watchdog` (30min)

- **1.3 Resource Monitoring — Degrade Mode:**
  - Cron a cada 15min checa RAM/CPU
  - **RAM > 80%:** alerta Luna no Discord
  - **RAM > 90%:** Luna entra em "degrade mode":
    - Pausa novos spawns
    - Move tasks não-críticas pra `Blocked: resource_pressure`
    - Notifica Matheus
  - **RAM > 95%:** auto-kill apenas de processos em allowlist explícita (nunca gateway, nunca MC)
  - **Allowlist de kill:** `[node_modules/.bin/*, venv/*, /tmp/puppeteer*]`

- **1.4 Cron de Delivery** (`mc-delivery`, a cada 15min):
  - Lista tasks `Done` no MC sem flag `delivered=true`
  - Para cada: manda resultado pro Discord (#general-luna)
  - Marca `delivered=true` no MC

- **1.5 Validação:**
  - [ ] Gateway restart com task `In_Progress` → confirmar retomada automática
  - [ ] Simular RAM > 90% → confirmar degrade mode (não kill)
  - [ ] Task concluída com Luna offline → confirmar delivery via cron
  - **Teste de restart:** `systemctl --user restart openclaw-gateway.service` com task ativa

### Fase 2: Monitoring + Auto-Healing (Semana 2)

**Objetivo:** Luna monitora agentes e corrige desvios. SLA de detecção: 30min (V1).

- **2.1 Cron `mc-watchdog`** (a cada 15-30min):
  1. `mc_list_tasks(status=In_Progress)` → tasks ativas
  2. Para cada task:
     - Ler último comment/checkpoint (timestamp)
     - Se sem update há > 1h → marcar `Stalled` + investigar
     - `sessions_history(sessionKey, includeTools=true, limit=5)` → ver o que agente fez
     - Se agente está idle/stuck → `sessions_send` com nudge
     - Se agente desviou do objetivo → `sessions_send` com steering
  3. Registrar cada check no MC como comment

- **2.2 Auto-Steering Protocol:**
  - Luna lê últimas mensagens do sub-agente via `sessions_history`
  - Compara com objetivo original da task (description no MC)
  - Se desvio detectado:
    1. `sessions_send(sessionKey, "STEERING: ...")` — correção de rumo
    2. `mc_add_comment(taskId, "Steering applied: ...")` — registro
  - Se sub-agente não responde a steering em 15min → kill + retry

- **2.3 Auto-Healing Loop:**
  - Se sub-agente reporta `TASK_UPDATE { status: "failed" }`:
    1. Luna lê erro
    2. Cria hipótese de correção
    3. Re-spawna com instruções corrigidas + erro anterior como contexto
    4. MC: `Failed → Retry → In_Progress`
    5. Máximo 2 retries automáticos → depois `Needs_Approval`
  - Se sub-agente morre silenciosamente (sessão some):
    1. mc-watchdog detecta no próximo ciclo
    2. `sessions_history` recupera último output
    3. Decide: retry ou escalar

- **2.4 Validação:**
  - [ ] Spawnar Luan com tarefa que vai falhar → confirmar auto-retry
  - [ ] Spawnar agente que desvia do objetivo → confirmar steering
  - [ ] Task sem update por 1h → confirmar detecção de stall
  - **Teste de healing:** `sessions_spawn(task="Crie arquivo X mas use comando errado: xyz123")`

### Fase 3: Aprovações Interativas + Governance (Semana 3-4)

**Objetivo:** Matheus entra no loop apenas para decisões de risco.

- **3.1 Canal #approvals no Discord:**
  - Criar canal dedicado
  - Luna manda approval request com botões interativos via components v2:
    ```
    🔔 Approval Required — Task #123
    [Contexto mastigado + recomendação]
    [✅ Aprovar] [❌ Rejeitar]
    ```
  - Ao clicar: Luna atualiza MC e libera/cancela task

- **3.2 Approval Tiers (por tipo, não por valor):**
  | Tipo | Policy | Exemplo |
  |---|---|---|
  | Código/pesquisa/docs | Auto-approve | Luan implementa feature |
  | API paga (não-wallet) | Notify-only, teto diário $20 | API call, LLM tokens |
  | Onchain/wallet/tx | SEMPRE approval | Trade, bridge, transfer |
  | Ação pública | SEMPRE approval | Tweet, email, post |

- **3.3 Cost Tracking:**
  - Cada task no MC tem campos `estimated_cost` e `actual_cost`
  - Luna tracka tokens gastos por sub-agente via `session_status`
  - Relatório semanal de custos por agente → #general-luna
  - Teto diário configurável por tier

- **3.4 Validação:**
  - [ ] Simular pedido de tx do Crypto Sage → confirmar pausa + botão
  - [ ] Aprovar via botão → confirmar retomada
  - [ ] Rejeitar via botão → confirmar cancelamento e registro
  - **Teste:** `mc_create_approval(taskId, "Executar swap 0.5 SOL → USDC?")`

---

## Delivery Guarantee: Fluxo Completo

```
Luna spawna sub-agente
  ↓
mc_create_task → task no MC (In_Progress, sessionKey linkado)
  ↓
Sub-agente trabalha → manda TASK_UPDATE pra Luna
  ↓
Luna recebe → mc_update_task (espelha no MC)
  ↓
Sub-agente termina → Luna recebe resultado
  ↓
  ├─ Luna ativa: entrega imediatamente + mc_update_task(Done, delivered=true)
  ├─ Luna inativa: mc_update_task(Done) — cron mc-delivery entrega em ≤15min
  └─ Gateway down: pós-restart, recovery protocol detecta Done não-entregue → entrega
```

**Nenhum resultado se perde.** Pior caso: delay de 15min (cron delivery) ou delay do restart.

---

## SLA Targets

| Métrica | V1 (Fase 1-2) | V2 (futuro) |
|---|---|---|
| Detecção de task stuck | ≤ 30min | ≤ 2min (SSE listener) |
| Delivery de resultado | ≤ 15min | ≤ 1min (webhook push) |
| Recovery pós-restart | ≤ 5min | ≤ 1min (auto) |
| Auto-healing (retry) | ≤ 30min | ≤ 5min |

---

## Infraestrutura Existente

- **MC API:** `http://localhost:8000` — rodando, healthy
- **MC Dashboard:** `http://142.93.87.36:3000`
- **MC Auth Token:** `config/mission-control-ids.json`
- **Board ID:** `0b6371a3-ec66-4bcc-abd9-d4fa26fc7d47`
- **Agentes registrados no MC:** Luna, Luan, Crypto Sage, Quant Strategist
- **Gateway notify hook:** `scripts/gateway-restart-notify.sh` (já funcional)
- **Orchestration state:** `memory/orchestration-state.json` (será migrado pro MC)

---

## Próximos Passos

1. [ ] **Hoje:** Implementar MC API Client (`scripts/mc-client.sh`) — Fase 0.1
2. [ ] **Hoje:** Criar canal #approvals no Discord
3. [ ] **Esta semana:** Spawn wrapper protocol — Fase 0.2
4. [ ] **Esta semana:** Cron mc-watchdog + mc-delivery — Fase 1.2/1.4
5. [ ] **Semana 2:** Auto-healing loop — Fase 2.3
6. [ ] **Semana 3:** Botões de approval no Discord — Fase 3.1
