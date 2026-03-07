#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/openclaw/.openclaw}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT/workspace}"
MAIN_WORKSPACE="${MAIN_WORKSPACE:-$ROOT/workspace-main}"
JUDGE_WORKSPACE="${JUDGE_WORKSPACE:-$ROOT/workspace-luna-judge}"
SOURCE_LINK="${JUDGE_WORKSPACE}/source"
IMPORT_DIR="${JUDGE_WORKSPACE}/imports/luna-main"
STATE_DIR="${JUDGE_WORKSPACE}/state"
ARTIFACT_DIR="${JUDGE_WORKSPACE}/artifacts/judge-context"
FORCE_ROOT_REFRESH="${FORCE_ROOT_REFRESH:-0}"

mkdir -p \
  "${JUDGE_WORKSPACE}/memory" \
  "${IMPORT_DIR}/memory" \
  "${STATE_DIR}" \
  "${ARTIFACT_DIR}" \
  "${JUDGE_WORKSPACE}/artifacts"

if [ ! -L "${SOURCE_LINK}" ]; then
  rm -rf "${SOURCE_LINK}"
  ln -s "${WORKSPACE_ROOT}" "${SOURCE_LINK}"
fi

write_if_needed() {
  local path="$1"
  local content="$2"
  if [ "${FORCE_ROOT_REFRESH}" = "1" ] || [ ! -f "${path}" ]; then
    printf '%s\n' "${content}" > "${path}"
  fi
}

today="$(TZ=America/Sao_Paulo date +%F)"
yesterday="$(TZ=America/Sao_Paulo date -d 'yesterday' +%F 2>/dev/null || python3 - <<'PY'
from datetime import datetime, timedelta
print((datetime.utcnow().date() - timedelta(days=1)).isoformat())
PY
)"

write_if_needed "${JUDGE_WORKSPACE}/SOUL.md" "# Luna Judge

You are Luna in judge mode.

- Review and validation only.
- Read the full system before deciding.
- Do not edit source code in \`source/\`.
- Produce explicit, auditable decisions.
- Do not ask for permission to perform read-only inspection needed for judging.
- Default to autonomous execution inside the approved judge scope."

write_if_needed "${JUDGE_WORKSPACE}/AGENTS.md" "# AGENTS.md - Luna Judge

Every session:

1. Read \`memory/active-tasks.md\`
2. Read \`SOUL.md\`
3. Read \`USER.md\`
4. Read \`MEMORY.md\`
5. Read \`imports/luna-main/memory/lessons.md\`
6. Read the latest \`artifacts/judge-context/*.md\` relevant to the claimed task
7. Read files under \`source/\` as needed

Constraints:

- Never modify files under \`source/\`
- You may write artifacts under \`artifacts/\`
- You may update Mission Control status/comments/fields
- You should act autonomously on review work without asking humans for routine permission
- Only escalate when the task truly requires chairman/human judgment"

write_if_needed "${JUDGE_WORKSPACE}/USER.md" "Operate as Luna's validation surface. Prefer explicit evidence, cite absolute paths, preserve auditability, and do not ask for permission for normal read-only investigation or MC updates inside review flow."
write_if_needed "${JUDGE_WORKSPACE}/MEMORY.md" "# Luna Judge Memory

This workspace exists to validate work with a top-down system view.
Use \`imports/luna-main/\` for curated Luna memory and \`source/\` for the canonical repo."
write_if_needed "${JUDGE_WORKSPACE}/HEARTBEAT.md" "Check context sync freshness before each review. If sync or context pack is missing, fail closed."
write_if_needed "${JUDGE_WORKSPACE}/BOOTSTRAP.md" "Before every review turn:

1. Sync curated Luna context into \`imports/luna-main/\`.
2. Read the task-specific pack under \`artifacts/judge-context/\`.
3. Inspect the canonical repo through \`source/\`.
4. Decide with explicit evidence and update Mission Control.

Do not ask permission for routine read-only investigation inside judge scope."
write_if_needed "${JUDGE_WORKSPACE}/TOOLS.md" "Primary tools:

- \`source/\` for canonical repo inspection
- \`imports/luna-main/\` for curated Luna memory
- \`artifacts/judge-context/\` for hydrated task context
- Mission Control scripts under \`source/scripts/\`"
write_if_needed "${JUDGE_WORKSPACE}/IDENTITY.md" "Name: Luna Judge
Role: Review, validation, and final decision engine."
write_if_needed "${JUDGE_WORKSPACE}/memory/active-tasks.md" "# Active Reviews

- Keep only currently claimed reviews here."
write_if_needed "${JUDGE_WORKSPACE}/memory/lessons.md" "# Lessons
"
write_if_needed "${JUDGE_WORKSPACE}/memory/workflow-registry.md" "# Workflow Registry

- Review flow: sync context -> build context pack -> validate -> update MC."
write_if_needed "${JUDGE_WORKSPACE}/memory/${today}.md" "# ${today}
"
write_if_needed "${JUDGE_WORKSPACE}/memory/${yesterday}.md" "# ${yesterday}
"

if [ -d "${MAIN_WORKSPACE}/.openclaw" ] && [ ! -d "${JUDGE_WORKSPACE}/.openclaw" ]; then
  mkdir -p "${JUDGE_WORKSPACE}/.openclaw"
  cp -R "${MAIN_WORKSPACE}/.openclaw/." "${JUDGE_WORKSPACE}/.openclaw/" 2>/dev/null || true
fi

printf '%s\n' "Bootstrapped luna-judge workspace at ${JUDGE_WORKSPACE}"
