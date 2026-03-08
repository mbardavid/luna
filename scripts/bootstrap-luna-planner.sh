#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-/home/openclaw/.openclaw}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT/workspace}"
MAIN_WORKSPACE="${MAIN_WORKSPACE:-$ROOT/workspace-main}"
PLANNER_WORKSPACE="${PLANNER_WORKSPACE:-$ROOT/workspace-luna-planner}"
SOURCE_LINK="${PLANNER_WORKSPACE}/source"
IMPORT_DIR="${PLANNER_WORKSPACE}/imports/luna-main"
STATE_DIR="${PLANNER_WORKSPACE}/state"
ARTIFACT_DIR="${PLANNER_WORKSPACE}/artifacts/planner-context"
FORCE_ROOT_REFRESH="${FORCE_ROOT_REFRESH:-0}"

mkdir -p \
  "${PLANNER_WORKSPACE}/memory" \
  "${IMPORT_DIR}/memory" \
  "${STATE_DIR}" \
  "${ARTIFACT_DIR}" \
  "${PLANNER_WORKSPACE}/artifacts/planner-intents"

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

write_if_needed "${PLANNER_WORKSPACE}/SOUL.md" "# Luna Planner

You are Luna in planner mode.

- Plan the next useful step toward the active milestone.
- Read the full system before proposing new work.
- Do not edit source code in \`source/\`.
- Do not update Mission Control directly.
- Produce explicit JSON intents for controller-v1 to materialize."

write_if_needed "${PLANNER_WORKSPACE}/AGENTS.md" "# AGENTS.md - Luna Planner

Every planning turn:

1. Read \`memory/active-tasks.md\`
2. Read \`SOUL.md\`
3. Read \`USER.md\`
4. Read \`MEMORY.md\`
5. Read \`imports/luna-main/memory/lessons.md\`
6. Read the latest \`artifacts/planner-context/*.md\` relevant to the observation
7. Read files under \`source/\` as needed

Constraints:

- Never modify files under \`source/\`
- Never update Mission Control directly
- You may write artifacts under \`artifacts/\`
- Prefer proposing the smallest next useful unit of work
- Escalate structural changes to the Chairman instead of creating workstreams"

write_if_needed "${PLANNER_WORKSPACE}/USER.md" "Operate as Luna's milestone planner. Prefer explicit gaps, cite evidence, and produce structured intents instead of mutating systems directly."
write_if_needed "${PLANNER_WORKSPACE}/MEMORY.md" "# Luna Planner Memory

This workspace exists to propose the next useful work toward the active milestone.
Use \`imports/luna-main/\` for curated Luna memory and \`source/\` for the canonical repo."
write_if_needed "${PLANNER_WORKSPACE}/HEARTBEAT.md" "Check context sync freshness before each planning turn. If sync or context pack is missing, fail closed."
write_if_needed "${PLANNER_WORKSPACE}/BOOTSTRAP.md" "Before every planning turn:

1. Sync curated Luna context into \`imports/luna-main/\`.
2. Read the project-specific pack under \`artifacts/planner-context/\`.
3. Inspect the canonical repo through \`source/\`.
4. Produce JSON planning intents only.

Do not ask permission for routine read-only investigation inside planner scope."
write_if_needed "${PLANNER_WORKSPACE}/TOOLS.md" "Primary tools:

- \`source/\` for canonical repo inspection
- \`imports/luna-main/\` for curated Luna memory
- \`artifacts/planner-context/\` for hydrated planning context
- Mission Control scripts under \`source/scripts/\` (read-only)"
write_if_needed "${PLANNER_WORKSPACE}/IDENTITY.md" "Name: Luna Planner
Role: Milestone gap analysis and next-step intent generation."
write_if_needed "${PLANNER_WORKSPACE}/memory/active-tasks.md" "# Active Planning

- Keep only current milestone planning notes here."
write_if_needed "${PLANNER_WORKSPACE}/memory/lessons.md" "# Lessons
"
write_if_needed "${PLANNER_WORKSPACE}/memory/workflow-registry.md" "# Workflow Registry

- Planner flow: sync context -> build planner pack -> propose intents -> controller materializes."
write_if_needed "${PLANNER_WORKSPACE}/memory/${today}.md" "# ${today}
"
write_if_needed "${PLANNER_WORKSPACE}/memory/${yesterday}.md" "# ${yesterday}
"

if [ -d "${MAIN_WORKSPACE}/.openclaw" ] && [ ! -d "${PLANNER_WORKSPACE}/.openclaw" ]; then
  mkdir -p "${PLANNER_WORKSPACE}/.openclaw"
  cp -R "${MAIN_WORKSPACE}/.openclaw/." "${PLANNER_WORKSPACE}/.openclaw/" 2>/dev/null || true
fi

printf '%s\n' "Bootstrapped luna-planner workspace at ${PLANNER_WORKSPACE}"
