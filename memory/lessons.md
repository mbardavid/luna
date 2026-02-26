# Lessons Learned
*Every mistake documented once, never repeated.*

## 2026-02-15
- **Onboarding as Root**: Don't run `openclaw onboard` as root if the main instance is for a user. It creates a token mismatch. Always use `sudo -u openclaw -H openclaw`.
- **Browser Relay Tunnel**: The extension needs an active SSH tunnel (`ssh -L 18792...`) to connect from a local machine to a remote VPS.
- **X Chat Passcode**: The new X Chat (encrypted) requires a 4-digit passcode setup before DMs can be read programmatically.

## 2026-02-12
- **Self-Update Limits**: `gateway update.run` does not work for `not-git-install` environments. Updates must be handled by the installation provider or manual re-install.

## 2026-02-16
- **Áudio no Telegram**: Ao receber mensagem de voz, transcrever automaticamente com Whisper (faster-whisper) antes de responder; não pedir texto ao usuário sem tentar a transcrição.

## 2026-02-18
- **Gateway reset em limbo (systemd vs foreground)**: antes de resetar, detectar modo de execução do gateway. Se o serviço user `openclaw-gateway.service` estiver instalado/ativo, operar só via `systemctl --user` (com `XDG_RUNTIME_DIR` e `DBUS_SESSION_BUS_ADDRESS` definidos). Não misturar com `openclaw gateway` em foreground.
- **Nunca sugerir restart cego por CLI quando DBUS está ausente**: em ambientes sem user bus, `openclaw gateway restart|stop|start` pode falhar e deixar estado confuso. Primeiro validar bus/session e existência da unit.
- **Runbook seguro obrigatório**: usar sequência fixa (check -> restart no modo correto -> verify logs). Evitar matar PID manualmente como abordagem padrão.

## 2026-02-21
- **Browser Relay Tunnel**: The extension needs an active SSH tunnel (`ssh -L 18792:localhost:18792 openclaw@<remote-ip>`) to connect from a local machine to a remote VPS.
- **X Persistent Session (Stealth)**: To achieve 24/7 access to X without user presence, use a persistent browser profile with a Stealth motor on the server. Capture `auth_token` and `ct0` cookies via Relay once and inject them into the server profile. This bypasses IP/Fingerprint blocks that expire simple cookie copies.

## 2026-02-24
- **Spawning de subagentes: usar a tool, não RPC**: para criar subagentes, usar `sessions_spawn` (tool). Não tentar `openclaw gateway call sessions.spawn` — o gateway não expõe esse método e retorna `unknown method`.
- **Persistência de artefatos críticos**: qualquer plano/diagnóstico importante deve ser escrito em arquivo (tool `write`) e validado com `read`/`ls` imediatamente. Não assumir que “foi salvo”.
- **A2A em Discord: thread-bound pode estar desabilitado**: se `mode=session` exigir `thread=true` mas o account tiver `threadBindings.spawnSubagentSessions=false`, usar `mode=run`.
- **MC não é hook automático**: spawns feitos direto via `sessions_spawn` não aparecem no Mission Control sem criar/linkar task. Padronizar fluxo MC para rastreabilidade.
- **Tasks do MC precisam de contexto completo**: Cada task no MC deve ter na description: (1) referência ao documento/plano fonte com seção específica, (2) lista de entregáveis concretos (arquivos/módulos), (3) critério de validação (comando de teste). Sem isso, o card é inútil — ninguém sabe o que precisa ser feito. Template: `**Plano:** \`path/to/doc.md\` → Seção X\n**Entregáveis:** file1.py, file2.py\n**Validação:** \`pytest tests/test_x.py\``
- **NUNCA spawnar subagent sem criar card no MC ANTES**: O spawn e o card devem ser atômicos — criar card (in_progress) → spawnar → linkar session_key. Se spawnar sem card, o trabalho fica invisível no MC e o Matheus não consegue acompanhar. Isso já foi cobrado 2x.
- **Pós-restart: reassumir tarefas imediatamente, sem esperar prompt**: Quando o gateway reinicia e a sessão volta, a PRIMEIRA coisa a fazer é: (1) verificar status do gateway, (2) checar subagents ativos/recentes, (3) validar trabalho em andamento, (4) re-spawnar o que parou, (5) reportar ao Matheus — tudo isso ANTES de qualquer interação. Nunca esperar o humano perguntar "voltou?"
- **Execução contínua: quando instrução é "execute o plano", não parar entre fases**: Se o Matheus diz "execute esse plano com o Luan", significa spawnar fase após fase automaticamente, sem esperar confirmação entre cada uma. Cada fase que termina deve imediatamente iniciar a próxima.
- **Subagent falhou → reagir imediatamente, sem esperar o humano cobrar**: Quando um subagente falha (timeout, erro, crash), a Luna DEVE automaticamente: (1) investigar o motivo (sessions_history, subagents list), (2) decidir se re-spawna com ajustes (mais tempo, task simplificada) ou reporta ao Matheus, (3) agir sem esperar provocação. Nunca deixar falha de subagente "morrer em silêncio". O Matheus não deveria precisar perguntar "porque falhou?" — a Luna deve antecipar.

## 2026-02-22
- **Instalação duplicada no root**: O OpenClaw estava instalado tanto em `/root/.openclaw/` quanto em `/home/openclaw/.openclaw/`. Isso causa confusão e conflitos potenciais. A instalação correta é **somente** no usuário `openclaw`. Sempre verificar e remover `/root/.openclaw/` se existir. Nunca rodar `openclaw` como root.
- **Trocar modelo da Luna/gateway quebra o sistema**: Alterar o modelo de IA da Luna ou do gateway no `openclaw.json` é uma operação de alto risco que pode derrubar todo o sistema. Antes de trocar:
  1. Fazer backup do `openclaw.json` atual.
  2. Parar o gateway de forma controlada (`systemctl --user stop openclaw-gateway`).
  3. Alterar o modelo.
  4. Reiniciar o gateway e verificar logs imediatamente.
  5. Se o sistema quebrar, restaurar o backup e reiniciar.
  - **Nunca trocar o modelo com o gateway rodando** — o estado interno pode ficar inconsistente e travar todo o fluxo de agentes.
- **Sessão do Discord tem modelo gravado independente do config global**: Ao trocar o modelo no `openclaw.json`, a sessão ativa do canal Discord (`agents/main/sessions/sessions.json`) mantém o modelo antigo. **Sempre limpar a sessão do canal após trocar o modelo**, deletando a entrada `agent:main:discord:channel:<id>` antes de reiniciar o gateway.
- **Reset de sessão apaga contexto operacional da Luna**: Deletar a sessão do `#general-luna` faz a Luna "esquecer" tudo — inclusive que aquele canal é o gateway principal com trust admin. O `IDENTITY.md` precisa documentar explicitamente a hierarquia de canais e nível de confiança para que a Luna recarregue esse contexto automaticamente em sessões novas. **Evitar reset de sessão do canal principal sempre que possível** — preferir reiniciar apenas o gateway (o systemd faz isso sem apagar a sessão).
- **`systemctl restart` pode causar crash loop**: O comando `systemctl --user restart openclaw-gateway` tem timeout de ~90s. Se o processo anterior não parar a tempo, o systemd marca como `timeout` e reinicia automaticamente — criando um loop que pode chegar a restart counter 30+. O correto é: `systemctl stop` → `pkill -9 -f 'openclaw-gateway'` → aguardar 3s → `reset-failed` → `start`. **NUNCA usar `systemctl restart` em loop quando o gateway está instável.**
- **`pkill -9 -f 'openclaw'` mata a sessão SSH atual**: O processo `sshd` filho herda o nome do usuário mas não o `openclaw`. Porém dependendo do ambiente, o `pkill -f` com padrão amplo pode matar processos críticos. Usar sempre `ps aux | grep openclaw | grep -v grep | awk '{print $2}' | xargs kill -9` em vez de `pkill -f`.
- **`session.routers` não existe nesta versão do OpenClaw**: Adicionar este campo ao `openclaw.json` faz o gateway crashar na inicialização com `Config invalid: Unrecognized key "routers"`. O gateway entra em crash loop silencioso — o SIGTERM vem do próprio processo saindo com código 1, não do systemd matando. **Nunca adicionar campos manualmente ao `openclaw.json` sem verificar se a versão suporta**. Diagóstico: `journalctl --user -u openclaw-gateway -n 30`.
- **Diagnóstico de crash loop**: Sempre usar `journalctl --user -u openclaw-gateway -n 30` antes de qualquer outra ação — o journal mostra o motivo real do exit code 1, diferente dos logs do gateway que só mostram o SIGTERM.

  1. Parar gateway
  2. Backup do config
  3. Alterar modelo no config
  4. Deletar sessão do canal Discord afetado
  5. Reiniciar gateway
  6. Verificar logs — confirmar `agent model: <novo-modelo>` e `tool=message` no primeiro run.
- **`gateway install --force` causa conflito de processos**: Rodar `openclaw gateway install --force` enquanto gateway está ativo cria um novo processo que conflita com o anterior pelo lock file (`gateway already running (pid X); lock timeout`). Resultado: crash loop. **Sempre parar o gateway antes de `gateway install --force`** e garantir que não há processos `openclaw-gateway` rodando com `ps aux | grep openclaw-gateway`.
- **Delivery queue acumulada causa crash loop**: As 11 `pending delivery entries` acumuladas após múltiplos restarts podem causar falhas no health-monitor e derrubar o gateway em loop. Se o gateway crashar repetidamente após subir, verificar e limpar `/home/openclaw/.openclaw/delivery-queue/*.json` antes de reiniciar.
- **Como adicionar novo provider de AI corretamente**:
  1. Rodar `openclaw onboard --non-interactive --accept-risk --<provider>-api-key <key> --skip-channels --skip-skills --skip-health --skip-daemon --skip-ui`
  2. Verificar auth profile criado no `openclaw.json` (o onboard usa o mode correto, ex: `api_key` para Google)
  3. Adicionar `Environment=<PROVIDER>_API_KEY=<key>` no arquivo do serviço systemd user
  4. Rodar `systemctl --user daemon-reload`
  5. **Não adicionar auth profiles manualmente** — o formato exato (ex: `api_key` vs `api-key`) só é conhecido via onboard.
- **Modelos Anthropic via Antigravity OAuth disponíveis**: Apenas `claude-opus-4-6-thinking` confirmado funcional. `claude-opus-4-6` (sem thinking) e `claude-sonnet-4-6` retornam 404. `claude-opus-4-5-thinking` e `claude-sonnet-4-5-thinking` também registrados mas não testados.
- **`systemctl --user reset-failed` necessário após crash loop**: Após o gateway falhar várias vezes, o systemd marca o serviço como `failed` e impede restart. Sempre rodar `systemctl --user reset-failed openclaw-gateway` antes de tentar reiniciar após crash loop.

## 2026-02-25: Responsividade e A2A

### Nunca travar turno com timeouts longos
- `sessions_send` com timeout > 30s trava a Luna e impede responder ao Matheus
- Usar `sessions_spawn` (fire-and-forget, auto-announces) em vez de `sessions_send` síncrono
- Se precisar de `sessions_send`, timeout máximo de 30s

### Updates intermediários obrigatórios
- Antes de cadeia longa de tool calls (>3), mandar update curto pro Matheus
- "Investigando X, volto em 1 min" é melhor que silêncio de 5 min
- Nunca fazer >5 tool calls sem dar status

### A2A correto
- `sessions_spawn` para delegar tasks a outros agentes (fire-and-forget)
- `sessions_send` só para mensagens curtas com timeout baixo
- Discord é superfície de output, não de orquestração entre agentes

### MC card obrigatório no turno do spawn
- Protocolo AGENTS.md já exige: criar MC task no mesmo turno do `sessions_spawn`
- Falha em 2026-02-25: spawn do crypto-sage sem card, Matheus cobrou
- Checklist atômico: 1) criar card MC → 2) spawn → 3) linkar session_key

### Atualizar MC no mesmo turno que recebe resultado de subagent
- Quando subagent auto-announces (completion), o turno DEVE:
  1. Processar o resultado
  2. Atualizar MC task → `done` + `mc_output_summary`
  3. Reportar pro Matheus no Discord
- Falha em 2026-02-25: Luan completou 2 tasks mas MC ficou `in_progress`
- Matheus cobrou: "Esse seu sistema de monitoramento não está 100%"
- Regra: MC update é PARTE do processamento do resultado, não step separado

## 2026-02-26

### [CRÍTICO] Nunca operar o gateway via tool `exec`
- Em 26/02, o gateway caiu 3 vezes porque a Luna detectou conflito de serviços e executou `sudo systemctl stop openclaw-gateway.service` via `exec`
- Isso matou o próprio gateway no meio da execução — a tool retornou `Command aborted by signal SIGTERM`
- **Regra absoluta: nunca usar `exec` para stop/start/restart do gateway**
- Se detectar instabilidade: **listar o diagnóstico e reportar ao Matheus** — o systemd com `Restart=always` cuida do restart automaticamente

### [RESOLVIDO] Conflito user-service vs system-service eliminado (26/02)
- Havia dois serviços systemd concorrentes na porta 18789:
  - **User-service** (`~/.config/systemd/user/`): tinha `--bind lan`, `Restart=always`, envs dos agentes (bird, crypto-sage, polymarket)
  - **System-service** (`/etc/systemd/system/`): legado sem os envs, estava `disabled`
- Quando um subia bloqueava a porta do outro → crash loop → Luna tentava corrigir → se matava → loop infinito
- **Fix aplicado pelo Matheus**: user-service desabilitado; drop-ins de env migrados para system-service; `execstart-lan.conf` criado com `--bind lan` e `Restart=always`; system-service habilitado no boot com `systemctl enable`
- **Estado atual**: existe apenas o system-service, com todos os envs e `Restart=always`. Conflito resolvido permanentemente

### Runbook seguro para investigar gateway instável
1. `sudo systemctl status openclaw-gateway --no-pager -l`
2. `journalctl -u openclaw-gateway --no-pager --since '30 minutes ago'`
3. `ss -tlnp | grep 18789`
4. `free -h`
5. **Reportar diagnóstico ao Matheus** — nunca executar stop/start via `exec`
6. Se Matheus autorizar: `sudo systemctl restart openclaw-gateway`
