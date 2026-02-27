# Heartbeat V3 — Análise Técnica do Luan

**Data:** 2026-02-26  
**Autor:** Luan (senior coding agent)  
**Input:** heartbeat-v3-design-doc.md + adendos do Matheus  
**Veredicto:** V3 como proposto **não funciona**. Proponho arquitetura alternativa abaixo.

---

## TL;DR

O design doc propõe `--session main` + `--announce --to` para substituir `--session isolated`. Matheus já confirmou que ambos falham:

| Modo | Falha | Severidade |
|------|-------|------------|
| `--session isolated` | Sem canal → message tool errors → storm → OOM | **Catastrófica** |
| `--session main` | Mensagem injetada é ignorada quando Luna está ocupada | **Silenciosa** |

Nenhum dos dois serve como fundação para automação progressiva. Proponho uma **arquitetura de fila baseada em filesystem** que elimina AI do caminho crítico.

---

## 1. Contestação da Proposta V3

### 1.1 `--session main` — falha silenciosa confirmada

O design doc assume que injetar na sessão principal funciona porque "o wake sentinel já faz isso". Diferença crucial:

- **Wake sentinel**: roda pós-restart quando a sessão principal está **vazia** (sem contexto ativo). O agente processa a mensagem porque não tem nada mais pra fazer.
- **Heartbeat dispatch**: roda a cada 10min, frequentemente enquanto Luna está **processando outra coisa** (conversa com Matheus, subagent em andamento). A mensagem cron se torna ruído no contexto.

Resultado: a mensagem é ingerida no contexto mas o agente não a prioriza. Não é um crash, é um **silent drop** — o pior tipo de falha para um sistema autônomo, porque ninguém sabe que falhou.

### 1.2 `--announce --to` — entrega parcial

`--announce` anuncia o **resultado** da sessão cron para um canal. Mas:
- Se a sessão cron falha ou dá timeout, o announce pode não executar
- Se o agente na sessão não produz output útil (silent fail acima), o announce entrega lixo
- `--announce` é um mecanismo de **output**, não de **ação**. Ele diz "eu terminei", não "faça algo"

### 1.3 `sessions_spawn` dentro de `--session main`

Funciona tecnicamente — o agente na sessão main tem acesso a `sessions_spawn`. Mas depende do agente **ler e interpretar** a mensagem injetada corretamente, o que nos leva ao problema fundamental:

> **Colocar AI no caminho crítico de uma ação autônoma é um antipattern.**

A AI pode: ignorar a instrução, interpretar errado, usar tool call errado, entrar em loop. Cada uma dessas falhas exige um novo guardrail. É uma corrida armamentista contra o próprio modelo.

---

## 2. Respostas às 5 Perguntas do Design Doc

### Q1: Risco de poluir contexto da sessão principal?

**Sim, e é pior do que poluição — é interferência.** Cada mensagem injetada via cron na sessão main:
- Adiciona ~500-1000 tokens ao contexto
- Pode confundir o agente sobre "quem está falando" (user vs system vs cron)
- Se Luna está mid-reasoning sobre outra task, a injeção pode corromper a cadeia de raciocínio
- Com 10min de intervalo, em uma hora são 6 injeções — até 6k tokens de ruído

O wake sentinel funciona porque roda UMA VEZ pós-restart, em sessão limpa. Dispatches periódicos são categoricamente diferentes.

### Q2: Mecanismo de lock/idempotência para concorrência (H6)?

**Ambos, em camadas:**
1. **File lock** (`flock`) para mutual exclusion do heartbeat bash — já existe, funciona
2. **MC task status** como source of truth — se `in_progress` com `session_key` ativa, skip
3. **Queue file atomicity** (proposta abaixo) — rename atômico = processamento garantido once

File lock é necessário mas insuficiente (protege bash de bash, não bash de cron). MC status é necessário mas tem race condition (check-then-act). Queue file com rename atômico resolve os dois.

### Q3: Consolidar os 3 crons MC (watchdog + delivery + resource)?

**Sim, é urgente.** Dados atuais do sistema:
- **28 sessões cron** ativas (zombie sessions que nunca foram limpas)
- **15 sessões** com mais de 24h de idade
- **39 sessões** stale (>1h sem update)
- Gateway usando **535MB** de 900MB disponível (MemoryHigh=900MB)

Cada sessão cron OpenClaw cria uma sessão que persiste no gateway. Mesmo com `--delete-after-run`, a SESSÃO do gateway continua existindo após o cron job terminar. Isso é um **memory leak lento** que eventualmente causa OOM.

**Recomendação:** Consolidar em um único bash script (`mc-unified-monitor.sh`) que roda a cada 15min via crontab nativo. Zero sessions OpenClaw. Se precisa comunicar resultado → `openclaw message send` (stateless, sem session).

### Q4: `ExecStartPre=/usr/bin/fuser -k 18789/tcp` é seguro?

**Sim, com uma salvaguarda.** No sistema atual, porta 18789 é exclusivamente o gateway OpenClaw. Não há outro serviço legítimo nessa porta.

Mas o `fuser -k` manda SIGKILL, que é agressivo. Melhoria:

```ini
ExecStartPre=/bin/sh -c '/usr/bin/fuser -k -TERM 18789/tcp 2>/dev/null; sleep 1; /usr/bin/fuser -k -KILL 18789/tcp 2>/dev/null; true'
```

Isso tenta SIGTERM primeiro (graceful), espera 1s, e só faz SIGKILL se ainda estiver vivo. O `; true` garante que o ExecStartPre não falha se ninguém estiver na porta.

**Adicionar agora** ao systemd unit — é pré-requisito para qualquer outro fix.

### Q5: Meta-monitoring (cron que monitora crons)?

**Não é turtles all the way down se o monitor final é bash puro.**

A cadeia:
1. Crontab nativo (gerenciado pelo kernel) → roda bash script
2. Bash script checa: log do heartbeat tem mtime < 20min? Se não → alerta
3. Alerta via `openclaw message send` (stateless, sem session)

Isso funciona porque o crontab nativo do Linux é o sistema mais confiável que temos. Ele roda independente do gateway, do Node.js, e de qualquer AI. Se o crontab falhar, o servidor caiu.

O anti-pattern seria: cron OpenClaw que monitora outro cron OpenClaw que monitora outro... Cada camada adiciona um ponto de falha AI. **Bash monitora tudo, AI não monitora nada.**

---

## 3. Hipóteses Adicionais (não cobertas no design doc)

### H11: Session Leak — cron sessions não são limpas ⚠️ ATIVO AGORA

**Descoberta na análise:** O gateway tem 28 cron sessions, 15 com >24h de idade. `--delete-after-run` deleta o **cron job**, não a **session**. Sessions acumulam indefinidamente.

**Impacto:** Memory creep. Com 535MB atual e MemoryHigh em 900MB, estamos a ~40 sessions de OOM. Cada ciclo de 10min do heartbeat que cria cron one-shots adiciona sessions que nunca morrem.

**Severidade:** Alta. Este é um OOM em slow motion que já está em andamento.

**Fix imediato:** Script de garbage collection de sessions (ver Plano de Implementação).

### H12: Cron timeout + task stuck in `in_progress`

O heartbeat marca task como `in_progress` ANTES de criar o cron. Se o cron falha na criação, há rollback. Mas se o cron é criado, roda, e o agente não faz nada útil (timeout, silent fail), a task fica `in_progress` sem `session_key`.

Phase 5.5 (stale dispatch) detecta isso mas só para `last_dispatched_id`. Se dois dispatches acontecem em sequência rápida (race condition), o primeiro pode ficar orphaned.

**Fix:** Stale detection deve iterar TODOS os `in_progress` sem `session_key`, não só o último dispatch.

### H13: Bash heartbeat Python inline — single point of fragility

O heartbeat-v2.sh é um shell wrapper que `exec python3 - <<'PYEOF'` com 700+ linhas de Python inline. Se qualquer import falhar (ex: `zoneinfo` em Python < 3.9), o script inteiro morre silenciosamente.

Mais grave: como é heredoc, erros de sintaxe Python não são detectados pelo bash `set -e`. O script simplesmente não roda e a crontab não reporta nada.

**Fix:** Extrair para arquivo `.py` separado com shebang, adicionar smoke test no startup.

### H14: Discord rate limiting no bash

`send_discord()` é chamado múltiplas vezes por execução (Phase 4 failures + Phase 9 dispatch notification). Discord rate limit para bots é ~5 msgs/5s por canal. Se o heartbeat detecta 3 failures e tenta enviar 6+ mensagens (2 canais × 3 failures), pode bater no rate limit.

**Fix:** Batch notifications em uma única mensagem por canal.

### H15: Gateway MemoryHigh triggered → throttling antes de OOM

O systemd mostra `MemoryHigh=943718400` (~900MB). Quando o processo ultrapassa MemoryHigh, o kernel não mata — ele **throttles** (pressão de memória, swap pressure, stalls de I/O). Isso causa:
- Discord listener desacelerando (o que aconteceu no Crash 2: "Discord listener bloqueado 32min")
- Gateway respondendo lentamente mas não morrendo
- Nenhum alerta, nenhum OOM kill — apenas degradação silenciosa

Isso é **pior que OOM** porque o sistema fica num estado zumbi: vivo o suficiente pra não reiniciar, morto o suficiente pra não funcionar.

**Fix:** Resource monitor deve checar `MemoryCurrent` via cgroup e alertar quando >70% de MemoryMax (não quando >MemoryHigh, que já é tarde).

---

## 4. Proposta: Arquitetura de Fila (Dispatch Queue)

### Princípio Fundamental

> **Bash detecta. Filesystem enfileira. Luna consome. Bash escala.**

Nenhuma AI no caminho crítico de detecção, enfileiramento, ou escalação. AI só entra na fase de CONSUMO, onde já tem canal, contexto, e pode falhar sem consequência sistêmica.

### Diagrama

```
┌──────────────────────────────────────────────────────────────┐
│                    CRONTAB NATIVO (kernel)                    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  */10 * * * *  heartbeat-v2.sh                               │
│  */5  * * * *  queue-escalation.sh                           │
│  */30 * * * *  session-gc.sh                                 │
│  */1  * * * *  gateway-wake-sentinel.sh                      │
│  */20 * * * *  meta-health.sh                                │
│                                                              │
└──────┬───────────────┬───────────────┬───────────────────────┘
       │               │               │
       ▼               │               ▼
┌──────────────┐       │        ┌──────────────┐
│ heartbeat    │       │        │ escalation   │
│ (detection)  │       │        │ (bash)       │
│              │       │        │              │
│ • failures   │       │        │ • pending>15m│
│ • inbox      │       │        │   → Discord  │
│ • stale      │       │        │ • pending>30m│
│              │       │        │   → Matheus  │
└──────┬───────┘       │        └──────┬───────┘
       │               │               │
       ▼               │               ▼
┌──────────────────────┴───────────────────────────────────────┐
│                   FILESYSTEM QUEUE                            │
│                                                              │
│  workspace/queue/pending/   ← heartbeat writes here          │
│  workspace/queue/active/    ← Luna moves here during work    │
│  workspace/queue/done/      ← Luna moves here when complete  │
│  workspace/queue/failed/    ← Luna moves here on failure     │
│  workspace/queue/escalated/ ← escalation.sh moves here       │
│                                                              │
│  Format: {timestamp}-{type}-{task_id}.json                   │
│  Atomic write: write to .tmp → rename                        │
│                                                              │
└──────────────────────────────┬───────────────────────────────┘
                               │
                               │ Luna reads on:
                               │  • wake-up (sentinel)
                               │  • system-event nudge
                               │  • user interaction idle
                               │
                               ▼
                    ┌──────────────────┐
                    │  LUNA (main)     │
                    │                  │
                    │  • Has channel ✅│
                    │  • Has context ✅│
                    │  • Can spawn   ✅│
                    │  • Can notify  ✅│
                    └──────────────────┘
```

### Queue File Format

```json
{
  "version": 1,
  "type": "dispatch",          // dispatch | respawn | alert
  "task_id": "cd0c8e37-...",
  "title": "Implementar feature X",
  "agent": "luan",
  "priority": "high",
  "created_at": "2026-02-26T22:00:00Z",
  "created_by": "heartbeat-v2",
  "context": {
    "description": "...",
    "failure_type": "TIMEOUT",   // only for respawn
    "retry_count": 1,            // only for respawn
    "adjustments": "..."         // only for respawn
  },
  "constraints": {
    "max_age_minutes": 30,       // escalate if not consumed
    "timeout_seconds": 600       // max execution time
  }
}
```

### Como Luna Consome a Queue

**Opção A — System Event Nudge (recomendada):**

O heartbeat, após escrever na queue, injeta um system event na sessão principal:

```bash
openclaw cron add \
  --at "+10s" \
  --agent main \
  --system-event "📋 Nova tarefa na dispatch queue: {title}. Verifique workspace/queue/pending/ e processe." \
  --delete-after-run \
  --name "queue-nudge-{id}"
```

`--system-event` injeta uma mensagem de sistema na sessão main SEM criar sessão nova e SEM rodar um agente. É um "poke" — Luna vê na próxima interação.

Se Luna está ocupada, o nudge fica no contexto e ela processa quando puder. Se não processar em 15min, o bash escalation pega.

**Opção B — SOUL.md directive:**

Adicionar ao SOUL.md:
```
## Dispatch Queue
Ao iniciar qualquer interação, verifique `workspace/queue/pending/`.
Se houver arquivos, processe-os ANTES de qualquer outra ação.
```

Isso é menos confiável (depende do modelo seguir a instrução) mas funciona como fallback.

**Opção C — Hybrid (A+B):**

System event para urgência + SOUL.md directive para consistência. É o que recomendo.

### Escalação Determinística (bash)

```bash
#!/bin/bash
# queue-escalation.sh — runs every 5min via crontab
# Checks pending queue items and escalates if too old

QUEUE_DIR="/home/openclaw/.openclaw/workspace/queue"
PENDING="$QUEUE_DIR/pending"
ESCALATED="$QUEUE_DIR/escalated"
DISCORD_CHANNEL="1476255906894446644"
MATHEUS_DM="<matheus-discord-id>"

now=$(date +%s)

for f in "$PENDING"/*.json; do
  [ -f "$f" ] || continue
  
  file_age=$(( now - $(stat -c %Y "$f") ))
  task_title=$(python3 -c "import json; print(json.load(open('$f')).get('title','?'))")
  task_id=$(python3 -c "import json; print(json.load(open('$f')).get('task_id','?')[:8])")
  
  if [ "$file_age" -gt 1800 ]; then
    # >30min: escalate to Matheus
    openclaw message send --channel discord --target "$MATHEUS_DM" \
      --message "🚨 Task pendente >30min sem processamento: \`$task_id\` — **$task_title**. Luna não consumiu da queue."
    mv "$f" "$ESCALATED/"
    
  elif [ "$file_age" -gt 900 ]; then
    # >15min: nudge Discord
    openclaw message send --channel discord --target "$DISCORD_CHANNEL" \
      --message "⏰ Task pendente >15min na queue: \`$task_id\` — **$task_title**. Luna, verifique workspace/queue/pending/."
  fi
done
```

### Session Garbage Collection (bash)

```bash
#!/bin/bash
# session-gc.sh — runs every 30min via crontab
# Cleans up zombie cron sessions from the gateway

OPENCLAW_BIN="openclaw"
MAX_AGE_HOURS=2
DISCORD_CHANNEL="1476255906894446644"

# Get stale cron sessions
stale_sessions=$($OPENCLAW_BIN gateway call --json --params '{}' sessions.list 2>/dev/null | \
  python3 -c "
import json, sys, time
data = json.load(sys.stdin)
sessions = data if isinstance(data, list) else data.get('sessions', [])
now = time.time() * 1000
max_age = $MAX_AGE_HOURS * 3600 * 1000
stale = []
for s in sessions:
    key = s.get('key', '')
    if 'cron' not in key:
        continue
    updated = s.get('updatedAt', 0) or 0
    if (now - updated) > max_age:
        stale.append(key)
for k in stale:
    print(k)
")

count=0
for key in $stale_sessions; do
  # Try to destroy the session
  $OPENCLAW_BIN gateway call --json --params "{\"sessionKey\":\"$key\"}" sessions.destroy 2>/dev/null && count=$((count + 1))
done

if [ "$count" -gt 0 ]; then
  openclaw message send --channel discord --target "$DISCORD_CHANNEL" \
    --message "🧹 Session GC: limpou $count sessão(ões) cron zombie."
fi
```

---

## 5. Confiança Progressiva — Roadmap de Automação

O Matheus quer um sistema que escale de "retry simples" para "orquestração multi-agente". Aqui está como fazer isso de forma segura:

### Nível 0: Fundação (AGORA — esta semana)
- **Queue filesystem** operacional
- **Session GC** rodando
- **Escalation bash** rodando
- **SystemD hardened** (fuser pre-start, MemoryMax alert)
- **Crons MC consolidados** em bash puro
- Capacidade: detectar falhas, notificar, enfileirar trabalho

### Nível 1: Auto-dispatch (semana 2)
- Luna consome queue automaticamente
- System event nudge ativo
- Métricas: taxa de consumo, tempo médio na queue, taxa de escalação
- Capacidade: despachar tasks inbox automaticamente

### Nível 2: Auto-respawn (semana 3-4)
- Se task falha, heartbeat enfileira respawn com análise de failure
- Luna re-spawna com ajustes (timeout, modelo, constraints)
- Circuit breaker por task (não global) — cada task tem max retries independente
- Capacidade: recuperar de falhas automaticamente

### Nível 3: Priorização inteligente (mês 2)
- Queue com prioridade (urgent > high > normal > low)
- Dependency graph: task B espera task A terminar
- Resource-aware: não despacha se memória >70%
- Capacidade: gerenciar backlog complexo

### Nível 4: Orquestração multi-agente (mês 3+)
- Queue items podem especificar agente (luna, luan, crypto-sage, etc.)
- Pipeline: output de um agente alimenta input de outro
- Coordenação: agentes podem enfileirar work para outros agentes
- Capacidade: workflows complexos multi-step

Cada nível só é ativado quando o anterior está **estável por pelo menos 7 dias** sem intervenção manual. Isso é confiança progressiva.

---

## 6. Plano de Implementação

### 🔴 EMERGENCIAL (hoje/amanhã)

**E1: Session GC — parar o memory leak**
```bash
# Executar AGORA manualmente para limpar as 28 sessions zombie:
openclaw gateway call --json --params '{}' sessions.list | \
  python3 -c "
import json, sys, time
data = json.load(sys.stdin)
sessions = data if isinstance(data, list) else data.get('sessions', [])
now = time.time() * 1000
for s in sessions:
    key = s.get('key', '')
    updated = s.get('updatedAt', 0) or 0
    age_h = (now - updated) / 3600000
    if 'cron' in key and age_h > 1:
        print(key)
" | while read key; do
  openclaw gateway call --json --params "{\"sessionKey\":\"$key\"}" sessions.destroy 2>/dev/null
  echo "Destroyed: $key"
done
```
Prioridade: **P0** — estamos a ~40 sessions de OOM.

**E2: SystemD hardening**
```ini
# Adicionar ao openclaw-gateway.service:
ExecStartPre=/bin/sh -c '/usr/bin/fuser -k -TERM 18789/tcp 2>/dev/null; sleep 1; /usr/bin/fuser -k -KILL 18789/tcp 2>/dev/null; true'
KillMode=control-group   # matar TODOS os processos, não só o main
Restart=always
RestartSec=5
```
Prioridade: **P0** — previne crash loop pós-OOM.

**E3: Desabilitar crons OpenClaw que criam sessions desnecessárias**
Listar todos os crons recorrentes e avaliar quais podem ser bash puro:
```bash
openclaw cron list --json 2>/dev/null
```
Prioridade: **P0** — cada cron recorrente cria sessions que vazam.

### 🟡 IMPORTANTE (esta semana)

**I1: Criar diretório de queue**
```bash
mkdir -p /home/openclaw/.openclaw/workspace/queue/{pending,active,done,failed,escalated}
```

**I2: Modificar heartbeat-v2.sh Phase 4 e Phase 9**
- Em vez de criar cron one-shot → escrever queue file
- Em vez de cron isolated → system-event nudge
- Manter toda lógica de detecção existente (funciona bem)

**I3: Criar queue-escalation.sh**
- Crontab nativo, a cada 5min
- Checa age dos arquivos em `queue/pending/`
- Escala para Discord (>15min) e Matheus (>30min)

**I4: Criar session-gc.sh**
- Crontab nativo, a cada 30min
- Destrói sessions cron com >2h de idade
- Notifica limpeza no Discord

**I5: Consolidar mc-watchdog + mc-delivery + mc-resource em bash puro**
- Um único script `mc-unified-monitor.sh`
- Roda via crontab nativo a cada 15min
- Zero sessions OpenClaw
- Notifica via `openclaw message send` (stateless)

**I6: Atualizar SOUL.md da Luna**
- Adicionar seção sobre dispatch queue
- Instrução para verificar `queue/pending/` no início de cada interação

### 🟢 MONITORAR (próximas semanas)

**M1: Meta-health check**
- Bash cron a cada 20min
- Checa mtime dos logs de heartbeat, sentinel, escalation
- Se qualquer um parou de escrever → alerta Discord

**M2: Métricas de queue**
- Tempo médio na queue antes de consumo
- Taxa de escalação (quantas tasks escalam para humano)
- Taxa de sucesso de respawn automático
- Usar para calibrar thresholds e decidir quando ativar próximo nível

**M3: Extrair heartbeat Python de heredoc para arquivo .py**
- Melhor debugging, syntax checking, testing
- Pode ser versionado e testado independentemente

**M4: Memory monitoring proativo**
- Bash cron checa `MemoryCurrent` via cgroup
- Alerta quando >70% de MemoryMax (antes de throttling)
- Gateway atual: 535MB / 1228MB (MemoryMax) = 43% — OK mas subindo

---

## 7. Alternativas Consideradas (e por que rejeitadas)

### 7a: Webhook HTTP (bash → HTTP endpoint → acorda Luna)
- **Problema:** Requer HTTP server separado ou extensão do gateway com webhook handler
- **Complexidade:** Alta. Novo processo, nova porta, novo ponto de falha
- **Veredicto:** Overkill para o problema atual. Filesystem queue é mais simples e igualmente confiável

### 7b: Redis/pub-sub
- **Problema:** Dependência externa (Redis). Mais uma coisa para monitorar e manter
- **Complexidade:** Alta. O sistema já tem um filesystem confiável
- **Veredicto:** Justificável em escala de 100+ agentes. Com 3-5 agentes, filesystem queue é mais que suficiente

### 7c: Dedicated respawn agent
- **Problema:** Mais um agente = mais uma session = mais memória = mais chance de interação inesperada
- **Complexidade:** Média. Mas adiciona um ator AI ao caminho crítico
- **Veredicto:** Viola o princípio "AI fora do caminho crítico". O que um agente dedicado faria que bash + queue não fazem?

### 7d: `--session main` com prioridade/preemption
- **Problema:** Requer mudança no gateway OpenClaw (feature request upstream)
- **Complexidade:** Não controlamos o código do gateway
- **Veredicto:** Ideal a longo prazo, mas não temos controle. Filesystem queue é a solução que temos hoje

### 7e: Named pipes / Unix sockets
- **Problema:** Não sobrevivem restart. Filesystem queue persiste
- **Veredicto:** Inferior a filesystem queue em todos os aspectos para este caso

---

## 8. Riscos da Proposta de Queue

Transparência: a arquitetura de queue também tem riscos.

### R1: Luna nunca checa a queue
Se Luna não receber o system-event nudge E não seguir a instrução do SOUL.md, queue items ficam pendentes indefinidamente.

**Mitigação:** Escalation bash garante que nenhum item fica >30min sem atenção. Pior caso = delay de 30min + escalação para Matheus. Isso é infinitamente melhor que crash ou silent fail.

### R2: Queue directory fills up
Se o heartbeat gera items mais rápido que Luna consome, `pending/` acumula.

**Mitigação:** Rate limit existente (MAX_DISPATCHES_PER_HOUR=3) + MAX_CONCURRENT_IN_PROGRESS=2 limitam a taxa de geração. GC de `done/` e `failed/` a cada 24h.

### R3: File corruption / partial write
Se o heartbeat crash durante escrita do queue file, pode gerar JSON inválido.

**Mitigação:** Atomic write (write to .tmp → `os.replace()` para nome final). Mesmo padrão já usado no state file.

### R4: System-event não é processado como esperado
`--system-event` é uma feature do OpenClaw que pode se comportar diferente do esperado.

**Mitigação:** Testar manualmente ANTES de depender. Se não funcionar, fallback para SOUL.md directive + escalation bash.

---

## 9. Conclusão

O heartbeat V3 como proposto (`--session main` + `--announce`) resolve o crash do V1/V2 mas introduz silent fails. Não é uma fundação — é um band-aid.

A arquitetura de queue proposta aqui inverte o modelo mental:

| Antes | Depois |
|-------|--------|
| AI detecta e age | Bash detecta, filesystem enfileira, AI consome quando pode |
| Falha AI = crash sistêmico | Falha AI = delay, escalação automática |
| Cada fix é um novo guardrail | Cada nível é um novo plugin |
| "Como impedir o agente de crashar?" | "Como garantir que o trabalho é feito?" |

A pergunta certa não é "que parâmetros de sessão evitam o crash" — é **"como remover a AI do caminho crítico da automação"**.

---

*Análise concluída. Disponível para implementar qualquer item do plano.*
