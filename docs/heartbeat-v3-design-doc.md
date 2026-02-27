# Heartbeat V3 — Design Document

## Problema Central
O heartbeat é o mecanismo de "sistema nervoso autônomo" da Luna — detecta falhas, despacha trabalho, e mantém o sistema vivo. Mas cada versão introduziu um novo modo de falha catastrófico.

## Princípio de Design (diretriz do Matheus)
> O objetivo não é só corrigir o crash atual — é construir um sistema que **aceite cada vez mais automação e complexidade de forma segura**.

Isso implica:
- **Confiança progressiva:** começar conservador, expandir capacidades conforme estabilidade é provada
- **Fail-safe por default:** cada novo nível de automação deve ter kill switch, circuit breaker, e rollback
- **Composabilidade:** novos comportamentos autônomos devem ser plug-in, não rewrite
- **Observabilidade:** cada ação autônoma deve ser rastreável, auditável, e reversível
- **Degradação graceful:** falha em um subsistema não derruba os outros

O heartbeat V3 deve ser a **fundação** sobre a qual automações mais complexas são empilhadas — não um fix pontual.

---

## Histórico de Falhas

### V0: AI Heartbeat nativo do OpenClaw (Gemini Flash)
**Config:** `agents.defaults.heartbeat.every = 30m` no `openclaw.json`
**Como funciona:** Gateway injeta mensagem de heartbeat na sessão principal a cada 30min. Agente (Flash) processa e age.

**Crash 1 — 2026-02-26 ~03h UTC:**
- Flash recebia heartbeat → tentava notificar inbox tasks
- Usava `message` tool **sem `channel: discord`** → "Channel is required"
- Retry storm: 20+ notificações falhando por ciclo
- Cada retry infla o contexto da sessão principal
- Gateway OOM → SIGKILL → processo órfão segurando porta → crash loop
- **Matheus matou processo órfão manualmente**

**Root cause:** Gemini Flash não segue instruções com precisão (omite parâmetro `channel`). Sem state tracking, notifica a mesma coisa repetidamente. Sem circuit breaker, spamma até OOM.

**Lições:**
1. Flash é unreliable como agente executor (omite params, entra em loops)
2. Sem state dedup → spam infinito
3. Sem circuit breaker → amplifica falhas
4. OOM killer do Linux deixa processos órfãos → crash loop

### V1: Bash heartbeat determinístico (`heartbeat-v2.sh`)
**Config:** Crontab `*/10 * * * *`, 9 fases, zero tokens quando nada a fazer.
**Design:** "Bash detecta, Cron injeta, Agent executa"

**O que funciona bem:**
- Phase 1-3: Health check, active hours, data fetch (puro bash, zero risco)
- Phase 5-9: Subagent check, stale detection, inbox dispatch (bash + cron one-shot)
- State dedup via `/tmp/.heartbeat-check-state.json`
- Circuit breaker (3 falhas → pause 30min)
- `flock` para concorrência

**Crash 2 — 2026-02-26 ~21h UTC (Phase 4: auto-respawn):**
- Phase 4 detecta subagent falhado (task `cd0c8e37`)
- Cria cron one-shot: `openclaw cron add --agent main --session isolated --no-deliver`
- Agente (Opus) roda em sessão ISOLADA (sem canal Discord)
- Tenta usar `message` tool → "Channel is required"
- **3.623 erros/hora** (!) → memória 820MB → Discord listener bloqueado 32min
- Gateway não crashou (OOM não atingido) mas ficou efetivamente morto
- **Matheus reiniciou gateway manualmente**

**Root cause:** `--session isolated` = sessão sem canal de output. Qualquer tentativa do agente de enviar mensagem → erro → retry → storm. O `--no-deliver` previne output DE VOLTA pro cron system, mas não impede o agente de usar `message` tool internamente.

**Observação crítica:** O wake sentinel (`gateway-wake-sentinel.sh`) TAMBÉM usa `--session isolated` e funciona. Diferença: o wake sentinel injeta um briefing que o agente processa sem enviar mensagens (lê arquivos, spawna subagents). O hb-respawn pede "re-executar task" → agente tenta notificar progresso → crash.

### V2 (atual): Phase 4 disabled, notify-only
**Fix emergencial:** Auto-respawn removido. Phase 4 agora:
- Detecta falha → incrementa retry count
- Move task para `review`
- Notifica `#general-luna` + `#notifications` via `openclaw message send`
- **Não cria nenhum cron one-shot**

**Gap:** Zero auto-recovery. Tasks falhadas ficam em `review` até Luna (na sessão principal) ou Matheus intervir manualmente.

---

## O Problema Fundamental

Existem **3 modos** de fazer o agente agir:

| Modo | Canal? | Context? | Risco |
|------|--------|----------|-------|
| **Sessão principal** (Discord) | ✅ | ✅ Completo | Nenhum (é o modo normal) |
| **Cron isolated** | ❌ | ❌ Mínimo | Alto (sem canal → crash se tentar msg) |
| **Cron main** | ✅ | ✅ Compartilhado | Médio (injeta na conversa ativa) |

O trade-off:
- `isolated` é seguro para tarefas "read-only" (wake sentinel lê e spawna)
- `isolated` é PERIGOSO para tarefas que precisam comunicar (respawn precisa notificar)
- `main` tem canal mas injeta na conversa ativa (pode confundir contexto)

---

## Proposta V3

### Princípio: "Bash detecta, Bash notifica, Main session age"

```
heartbeat-v2.sh (bash, cron)
  ↓ detecta falha
  ↓
  ├── Notifica #general-luna via openclaw message send (bash)
  ├── Move task → review no MC (bash)
  └── Cria cron one-shot:
        openclaw cron add \
          --agent main \
          --session main \          ← MAIN, não isolated
          --name "hb-respawn-{id}" \
          --delete-after-run \
          --announce \              ← resultado vai pro Discord
          --to 1473367119377731800 \ ← #general-luna
          --timeout-seconds 120 \   ← max 2min
          --thinking low \
          --message "..."
```

### Guardrails adicionais:

1. **Mensagem do cron deve incluir instrução explícita:**
   ```
   IMPORTANTE: Você está rodando na sessão principal.
   Use sessions_spawn para re-executar a task.
   NÃO use message tool — o --announce cuida da entrega.
   Responda com NO_REPLY se não puder agir.
   ```

2. **Rate limit no bash:** Máximo 1 respawn por execução do heartbeat (já existe via `respawn_dispatched` early exit)

3. **Circuit breaker:** 3 respawns falhados consecutivos → pause 30min (já existe)

4. **MAX_RETRIES=2:** Após 2 respawns falhados → review + notificação humana (já existe)

5. **Timeout curto:** `--timeout-seconds 120` (2min max, não 5min)

---

## Hipóteses de Falha (passadas, presentes, futuras)

### H1: Agente tenta `message` tool em sessão sem canal [PASSADO ✅ RESOLVIDO]
- V0: Flash sem `channel: discord` → retry storm → OOM
- V2: Opus em isolated sem canal → "Channel is required" → 3.6k erros/h
- **Fix V3:** `--session main` garante canal. `--announce` entrega resultado.

### H2: Agente entra em loop degenerativo (Flash) [PASSADO ✅ RESOLVIDO]
- Flash repete mesma tool call 15x → 38M tokens
- **Fix:** Heartbeat bash não usa Flash. Crons Opus com `--timeout-seconds 120`.

### H3: State dedup falha → notificação spam [PASSADO ✅ RESOLVIDO]
- V0: Sem state → mesmo inbox notificado a cada 30min
- **Fix V1:** State file com cooldown. Circuit breaker.

### H4: Cron one-shot injeta na sessão principal durante conversa ativa [PRESENTE 🔴 CONFIRMADO]
- `--session main` injeta system message na sessão do Discord
- **Problema confirmado por Matheus:** quando Luna está ocupada processando outra mensagem, o cron main **passa despercebido** — a mensagem é engolida pelo contexto e nunca processada
- Não é crash, é **falha silenciosa** — pior que crash porque ninguém percebe
- Wake sentinel "funciona" porque roda pós-restart quando Luna está idle
- **Conclusão:** `--session main` NÃO é confiável para ações críticas (respawns)
- **Implicação:** Nem `isolated` (crash) nem `main` (silent fail) servem. Precisamos de um mecanismo com garantia de entrega — fila persistente que Luna consome quando disponível

### H5: Gateway restart durante cron one-shot [PRESENTE ⚠️]
- Cron roda → gateway cai → sessão morre → task não re-spawna
- **Mitigação:** Wake sentinel detecta restart. Heartbeat bash detecta na próxima execução (10min).
- **Risco residual:** Delay de até 10min. Aceitável.

### H6: Concorrência: heartbeat bash + cron one-shot + wake sentinel simultâneos [PRESENTE ⚠️]
- `flock` protege heartbeat bash de si mesmo
- Mas cron one-shots não têm lock entre si
- Cenário: heartbeat cria respawn → gateway reinicia → wake sentinel cria outro → dois agentes processando a mesma task
- **Mitigação:** MC task já está em `in_progress` → segundo spawn vê isso e pula. Idempotência no spawn.
- **Risco residual:** Médio. Pode duplicar work se timing é apertado.
- **Fix proposto:** Antes de spawnar, checar MC task status. Se já `in_progress` com session ativa → skip.

### H7: Acúmulo de sessões cron (memory pressure) [FUTURO ⚠️]
- 9 bash crons + N openclaw crons + one-shots → muitas sessões simultâneas
- Cada sessão Opus: ~15k tokens de contexto = overhead no gateway
- **Mitigação atual:** `--delete-after-run`, `--timeout-seconds` limitam vida útil
- **Fix proposto:** Resource monitor já existe. Adicionar check: se > 20 sessões ativas, pause spawns.

### H8: Token/cost explosion em cron Opus [FUTURO ⚠️]
- Crons isolados rodam como Opus (`claude-opus-4-6-thinking`)
- Heartbeat bash é zero-token, mas crons OpenClaw gastam tokens
- mc-watchdog, mc-delivery, mc-resource-monitor: ~15k tokens cada, 4x/hora = ~180k tokens/hora
- **Mitigação:** Usar `--model gemini-3-flash` para crons read-only (watchdog, delivery, resource).
- **Fix proposto:** Diferenciar crons que PRECISAM de Opus (spawns, orchestração) dos que são read-only (monitoring).

### H9: Processo órfão pós-OOM [PASSADO ✅ PARCIALMENTE RESOLVIDO]
- OOM killer mata gateway → processo filho sobrevive → segura porta → crash loop
- **Fix V1:** `KillMode=process` no systemd + `Restart=always`
- **Risco residual:** Se o filho é um processo Node.js separado (não child do gateway), `KillMode=process` não o mata.
- **Fix proposto:** Pré-start script no systemd que mata processos na porta 18789: `ExecStartPre=/usr/bin/fuser -k 18789/tcp`

### H10: Heartbeat bash falha silenciosamente [FUTURO ⚠️]
- Script Python embedded no bash → crash de sintaxe, módulo faltando, etc.
- Cron redireciona stderr para log → falha invisível
- **Mitigação atual:** Logs em `logs/heartbeat-v2.log`. Mas ninguém monitora.
- **Fix proposto:** Adicionar health check META: se heartbeat não escreveu no log há 20min → alerta. Pode ser um cron simples que checa mtime do log.

---

## Questões para Luan

1. `--session main` vs `--session isolated`: O Luan validou que o wake sentinel funciona com isolated. Para o respawn, main é melhor. Mas existe risco de poluir o contexto da sessão principal com mensagens de sistema acumuladas?

2. Concorrência (H6): Qual mecanismo de lock/idempotência é mais robusto? File-based lock, MC status check, ou ambos?

3. Overhead de crons (H7/H8): Deveríamos consolidar os 3 crons MC (watchdog + delivery + resource) em um único script bash? Reduziria de 3 sessões para 0.

4. Kill switch de processos órfãos (H9): `ExecStartPre=/usr/bin/fuser -k 18789/tcp` é seguro? Pode matar outro serviço legítimo?

5. Meta-monitoring (H10): Um cron que monitora os outros crons é... turtles all the way down. Existe uma abordagem mais limpa?
