#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-/home/openclaw/.openclaw/workspace}"
OUTROOT="${OUTROOT:-$WORKDIR/artifacts/backups}"
TS="${TS:-$(date -u +%Y%m%dT%H%M%SZ)}"
BACKUP_DIR="$OUTROOT/$TS"
mkdir -p "$BACKUP_DIR"

log(){ echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$BACKUP_DIR/README.txt"; }

log "backup start"
{
  echo "ts=$TS";
  openclaw --version || true;
  uname -a;
  node -v || true;
  python3 --version || true;
  git -C "$WORKDIR" rev-parse HEAD || true;
} > "$BACKUP_DIR/system.txt" 2>&1

cp -a /home/openclaw/.openclaw/openclaw.json "$BACKUP_DIR/openclaw.json" 2>/dev/null || true
crontab -l > "$BACKUP_DIR/crontab.txt" 2>/dev/null || true

log "tar workspace snapshot (excluding secrets/heavy dirs)"
cd "$WORKDIR"
TAR="$BACKUP_DIR/workspace.tgz"

tar -czf "$TAR" \
  --exclude='./node_modules' \
  --exclude='./openclaw-mission-control/node_modules' \
  --exclude='./research' \
  --exclude='./artifacts/backups' \
  --exclude='./logs' \
  --exclude='./.git' \
  --exclude='./config/mission-control-ids.local.json' \
  --exclude='./openclaw-mission-control/.env' \
  --exclude='./**/.env' \
  --exclude='./**/token*.json' \
  .

sha256sum "$TAR" > "$BACKUP_DIR/SHA256SUMS.txt"
ls -lah "$BACKUP_DIR" >> "$BACKUP_DIR/README.txt"
log "backup complete at $BACKUP_DIR"
