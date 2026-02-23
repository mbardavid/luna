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

### OpenClaw Gateway — Safe Reset Runbook (anti-limbo)

1. **Detect mode first**
   - `systemctl --user status openclaw-gateway.service` (if available)
   - `pgrep -af openclaw-gateway`
2. **If systemd user service exists, use only systemd path**
   - `export XDG_RUNTIME_DIR=/run/user/$(id -u)`
   - `export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus`
   - `systemctl --user daemon-reload`
   - `systemctl --user restart openclaw-gateway.service`
3. **If service is not installed, install once (as openclaw user)**
   - `openclaw gateway install`
   - `systemctl --user enable --now openclaw-gateway.service`
4. **Never mix modes**
   - Do **not** run `openclaw gateway` foreground while service is active.
5. **Verify after reset**
   - `systemctl --user is-active openclaw-gateway.service`
   - `journalctl --user -u openclaw-gateway.service -n 80 --no-pager`
   - `openclaw agents list --bindings`

---

Add whatever helps you do your job. This is your cheat sheet.
