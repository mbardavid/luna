#!/usr/bin/env bash
# lessons-sync.sh — Cross-pollinate lessons between Luna and Luan
#
# Parses headers from both memory/lessons.md files and propagates
# relevant lessons with a (cross-pollinated YYYY-MM-DD) tag.
#
# Usage:
#   lessons-sync.sh              # sync both directions
#   lessons-sync.sh --dry-run    # show what would sync
#
# Cron: 0 10 * * * (7am São Paulo)
#
set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
LUNA_LESSONS="$WORKSPACE/memory/lessons.md"
LUAN_LESSONS="$WORKSPACE/../workspace-luan/memory/lessons.md"
LOG_FILE="$WORKSPACE/logs/lessons-sync.log"
TODAY=$(date -u '+%Y-%m-%d')

mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date -u '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"; }

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if [ ! -f "$LUNA_LESSONS" ] || [ ! -f "$LUAN_LESSONS" ]; then
    log "SKIP: one or both lessons.md files not found"
    log "  Luna: ${LUNA_LESSONS} ($([ -f "$LUNA_LESSONS" ] && echo 'exists' || echo 'MISSING'))"
    log "  Luan: ${LUAN_LESSONS} ($([ -f "$LUAN_LESSONS" ] && echo 'exists' || echo 'MISSING'))"
    exit 0
fi

# Extract lesson headers (## lines) and check which are already cross-pollinated
python3 << 'PYEOF'
import re
import sys
import os
from datetime import datetime

luna_path = os.environ.get("LUNA_LESSONS", "")
luan_path = os.environ.get("LUAN_LESSONS", "")
dry_run = os.environ.get("DRY_RUN", "0") == "1"
today = datetime.utcnow().strftime("%Y-%m-%d")

def parse_lessons(path):
    """Extract lesson blocks (header + body until next header)."""
    lessons = []
    current = None
    with open(path) as f:
        for line in f:
            if line.startswith("## "):
                if current:
                    lessons.append(current)
                current = {"header": line.strip(), "body": "", "is_cross": "cross-pollinated" in line}
            elif current:
                current["body"] += line
    if current:
        lessons.append(current)
    return lessons

def lesson_key(header):
    """Normalize header for dedup (remove dates, cross-pollinated tags)."""
    h = re.sub(r'\(cross-pollinated \d{4}-\d{2}-\d{2}\)', '', header)
    h = re.sub(r'\d{4}-\d{2}-\d{2}', '', h)
    return h.strip().lower()

def coding_relevant(lesson):
    """Check if a lesson is relevant for coding agent."""
    keywords = ["bug", "test", "code", "debug", "error", "fix", "pattern",
                 "import", "api", "endpoint", "function", "class", "module",
                 "refactor", "type", "lint", "build", "deploy", "git"]
    text = (lesson["header"] + lesson["body"]).lower()
    return any(k in text for k in keywords)

luna_lessons = parse_lessons(luna_path)
luan_lessons = parse_lessons(luan_path)

luna_keys = {lesson_key(l["header"]) for l in luna_lessons}
luan_keys = {lesson_key(l["header"]) for l in luan_lessons}

# Luna → Luan (coding-relevant only)
to_luan = []
for l in luna_lessons:
    if l["is_cross"]:
        continue
    key = lesson_key(l["header"])
    if key not in luan_keys and coding_relevant(l):
        to_luan.append(l)

# Luan → Luna (all lessons)
to_luna = []
for l in luan_lessons:
    if l["is_cross"]:
        continue
    key = lesson_key(l["header"])
    if key not in luna_keys:
        to_luna.append(l)

synced = 0

if to_luan:
    print(f"Luna → Luan: {len(to_luan)} lessons to sync")
    for l in to_luan:
        tag = f" (cross-pollinated {today})"
        new_header = l["header"].rstrip() + tag
        print(f"  {new_header}")
        if not dry_run:
            with open(luan_path, "a") as f:
                f.write(f"\n{new_header}\n{l['body']}")
        synced += 1

if to_luna:
    print(f"Luan → Luna: {len(to_luna)} lessons to sync")
    for l in to_luna:
        tag = f" (cross-pollinated {today})"
        new_header = l["header"].rstrip() + tag
        print(f"  {new_header}")
        if not dry_run:
            with open(luna_path, "a") as f:
                f.write(f"\n{new_header}\n{l['body']}")
        synced += 1

if synced == 0:
    print("No new lessons to sync")
else:
    print(f"Total synced: {synced}")
    if dry_run:
        print("(DRY RUN — no files modified)")

PYEOF
