#!/usr/bin/env bash
set -euo pipefail

AGENTS_DIR="${AGENTS_DIR:-/home/openclaw/.openclaw/agents}"
THRESHOLD_BYTES="${THRESHOLD_BYTES:-1048576}"  # 1MB
INACTIVE_DAYS="${INACTIVE_DAYS:-7}"
DRY_RUN="${DRY_RUN:-false}"

archived=0
skipped=0
errors=0

log() { echo "[$(date -u +%FT%TZ)] [session-archive] $*"; }

for agent_dir in "$AGENTS_DIR"/*; do
  [ -d "$agent_dir" ] || continue
  sessions_dir="$agent_dir/sessions"
  [ -d "$sessions_dir" ] || continue
  archived_dir="$sessions_dir/archived"
  mkdir -p "$archived_dir"

  while IFS= read -r -d '' f; do
    base="$(basename "$f")"
    agent="$(basename "$agent_dir")"

    # Skip if file appears in use
    if command -v fuser >/dev/null 2>&1; then
      if fuser "$f" >/dev/null 2>&1; then
        log "SKIP in-use: $agent/$base"
        skipped=$((skipped+1))
        continue
      fi
    fi

    if [ "$DRY_RUN" = "true" ]; then
      log "DRY_RUN would archive: $agent/$base"
      archived=$((archived+1))
      continue
    fi

    if mv "$f" "$archived_dir/$base"; then
      log "ARCHIVED $agent/$base"
      archived=$((archived+1))
    else
      log "ERROR moving $agent/$base"
      errors=$((errors+1))
    fi
  done < <(find "$sessions_dir" -maxdepth 1 -name '*.jsonl' -size +"${THRESHOLD_BYTES}c" -mtime +"$INACTIVE_DAYS" -print0 2>/dev/null)

done

log "DONE archived=$archived skipped=$skipped errors=$errors"
echo "archived=$archived skipped=$skipped errors=$errors"
