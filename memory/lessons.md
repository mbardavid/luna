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
