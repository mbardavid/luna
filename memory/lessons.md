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
- **Procedure correta para trocar modelo**:
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
