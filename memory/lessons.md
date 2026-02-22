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
