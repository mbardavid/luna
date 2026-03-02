# TOOLS.md - Local Notes

Skills define _how_ tools work. This file is for _your_ specifics — the stuff that's unique to your setup.

## What Goes Here

Things like:

- Camera names and locations
- SSH hosts and aliases
- Preferred voices for TTS
- Speaker/room names
- Device nicknames
- Anything environment-specific

## Examples

```markdown
### Cameras

- living-room → Main area, 180° wide angle
- front-door → Entrance, motion-triggered

### SSH

- home-server → 192.168.1.100, user: admin

### TTS

- Preferred voice: "Nova" (warm, slightly British)
- Default speaker: Kitchen HomePod
```

## Why Separate?

Skills are shared. Your setup is yours. Keeping them apart means you can update skills without losing your notes, and share skills without leaking your infrastructure.

## Local Setup Notes (current)

### Browser

- agent-browser binary: `/home/openclaw/.local/bin/agent-browser`
- Installed version: `0.9.1`
- Linux deps installed with: `agent-browser install --with-deps`

### Gmail OAuth (readonly)

- credentials: `/home/openclaw/.config/gogcli/credentials.json` (600)
- token: `/home/openclaw/.config/gogcli/token.json` (600)
- scope: `https://www.googleapis.com/auth/gmail.readonly`

### Whisper (Audio Transcription)

- Script: `/home/openclaw/.local/bin/local-whisper-transcribe`
- Engine: `faster-whisper` (model: `small`, CPU, int8)
- Usage: `local-whisper-transcribe <audio_file>` → outputs transcribed text to stdout
- Purpose: Transcrever áudios recebidos do Matheus

### Healthcheck Automation

- script: `scripts/healthcheck.sh`
- daily cron job: `daily-setup-healthcheck-summary`

### OpenClaw Gateway — Operational Rules (2026-02-26)

**Serviço único:** system-service (`/etc/systemd/system/openclaw-gateway.service`)
- User-service foi desabilitado permanentemente (conflito resolvido)
- Drop-ins migrados: bird-env, crypto-sage-env, polymarket-env
- Config: `--bind lan`, `Restart=always`, `KillMode=process`, enabled on boot

**⚠️ REGRA CRÍTICA: Gateway restart apenas via script seguro**
- `scripts/gateway-safe-restart.sh --auto --reason "<motivo>"` — método correto
- O script limpa sessões, notifica Discord, respeita rate limit, e só então reinicia
- `sudo systemctl stop/restart openclaw-gateway` direto: **PROIBIDO** (exceto quando Matheus pedir explicitamente)
- CTO-ops (`mc-resource-monitor.sh`) executa restart automático quando gateway memory > 80% do watermark
- Rate limit: max 3 restarts/hora (enforced pelo script)
- Se detectar instabilidade sem atingir threshold → **apenas alertar Matheus**

**Diagnóstico (somente leitura):**
- `sudo systemctl status openclaw-gateway` — status atual
- `journalctl -u openclaw-gateway -n 50 --no-pager` — logs recentes
- `pgrep -af openclaw-gateway` — PID
- `ss -tlnp | grep 18789` — porta

**Config reload (⚠️ causa full restart):**
- `kill -USR1 $(pgrep -f openclaw-gateway)` — **NÃO é hot-reload**; causa supervisor restart completo (PID muda)
- Descoberto empiricamente em 27/fev e confirmado em 28/fev: SIGUSR1 mata o processo e o systemd reinicia
- Equivalente a restart do ponto de vista do gateway — usar com mesma cautela
- Alertar Matheus antes quando possível

---

Add whatever helps you do your job. This is your cheat sheet.
