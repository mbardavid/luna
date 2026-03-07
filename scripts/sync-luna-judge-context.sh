#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/openclaw/.openclaw}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT/workspace}"
MAIN_WORKSPACE="${MAIN_WORKSPACE:-$ROOT/workspace-main}"
JUDGE_WORKSPACE="${JUDGE_WORKSPACE:-$ROOT/workspace-luna-judge}"
IMPORT_DIR="${JUDGE_WORKSPACE}/imports/luna-main"
STATE_DIR="${JUDGE_WORKSPACE}/state"
SYNC_STATE="${STATE_DIR}/judge-context-sync.json"
SOURCE_LINK="${JUDGE_WORKSPACE}/source"

mkdir -p "${IMPORT_DIR}/memory" "${STATE_DIR}" "${JUDGE_WORKSPACE}/artifacts/judge-context"

if [ ! -L "${SOURCE_LINK}" ]; then
  rm -rf "${SOURCE_LINK}"
  ln -s "${WORKSPACE_ROOT}" "${SOURCE_LINK}"
fi

copy_if_exists() {
  local src="$1"
  local dst="$2"
  if [ -f "${src}" ]; then
    mkdir -p "$(dirname "${dst}")"
    cp "${src}" "${dst}"
  fi
}

today="$(TZ=America/Sao_Paulo date +%F)"
yesterday="$(TZ=America/Sao_Paulo date -d 'yesterday' +%F 2>/dev/null || python3 - <<'PY'
from datetime import datetime, timedelta
print((datetime.utcnow().date() - timedelta(days=1)).isoformat())
PY
)"

for file in SOUL.md USER.md MEMORY.md; do
  copy_if_exists "${MAIN_WORKSPACE}/${file}" "${IMPORT_DIR}/${file}"
done

for file in lessons.md workflow-registry.md active-tasks.md "${today}.md" "${yesterday}.md"; do
  copy_if_exists "${MAIN_WORKSPACE}/memory/${file}" "${IMPORT_DIR}/memory/${file}"
done

python3 - <<PY
import json
from datetime import datetime, timezone
from pathlib import Path

state_path = Path("${SYNC_STATE}")
payload = {
    "synced_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "source_repo": "${WORKSPACE_ROOT}",
    "source_memory": "${MAIN_WORKSPACE}",
    "judge_workspace": "${JUDGE_WORKSPACE}",
}
state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

printf '%s\n' "${SYNC_STATE}"
