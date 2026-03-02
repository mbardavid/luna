#!/usr/bin/env bash
# mc-spawn-luan.sh — Structured task spec generator for spawning Luan
#
# Creates an MC task + generates a validated spawn prompt with structured task spec.
# Enforces: title, type, acceptance_criteria, files, verification_checks.
#
# Usage:
#   mc-spawn-luan.sh \
#     --title "Fix balance normalization bug" \
#     --type bugfix \
#     --description "USDC balances from API are in micro-units..." \
#     --files "core/main.py,execution/order_manager.py" \
#     --acceptance "All tests pass|Balance normalized at API boundary|No regressions" \
#     --checks "pytest polymarket-mm/tests/ -v|python3 -c 'from execution.order_manager import ...; assert ...'" \
#     --risk low \
#     --review false \
#     --timeout 30 \
#     --json
#
# Output (--json): JSON with mc_task_id + spawn_prompt ready for sessions_spawn
#
set -euo pipefail

WORKSPACE="/home/openclaw/.openclaw/workspace"
MC_SCRIPT="$WORKSPACE/scripts/mc-spawn.sh"

# Defaults
TITLE=""
TYPE="feature"
DESCRIPTION=""
FILES=""
ACCEPTANCE=""
CHECKS=""
RISK="low"
REVIEW="false"
TIMEOUT=30
JSON_OUTPUT=0
QA_GUIDANCE=""
CONTEXT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --title)       TITLE="$2"; shift 2 ;;
        --type)        TYPE="$2"; shift 2 ;;
        --description) DESCRIPTION="$2"; shift 2 ;;
        --files)       FILES="$2"; shift 2 ;;
        --acceptance)  ACCEPTANCE="$2"; shift 2 ;;
        --checks)      CHECKS="$2"; shift 2 ;;
        --risk)        RISK="$2"; shift 2 ;;
        --review)      REVIEW="$2"; shift 2 ;;
        --timeout)     TIMEOUT="$2"; shift 2 ;;
        --qa)          QA_GUIDANCE="$2"; shift 2 ;;
        --context)     CONTEXT="$2"; shift 2 ;;
        --json)        JSON_OUTPUT=1; shift ;;
        *)             echo "Unknown: $1" >&2; exit 1 ;;
    esac
done

# Validate required fields
if [ -z "$TITLE" ]; then
    echo "ERROR: --title is required" >&2
    exit 1
fi

if [ -z "$ACCEPTANCE" ]; then
    echo "ERROR: --acceptance is required (pipe-separated list)" >&2
    exit 1
fi

# Build acceptance criteria markdown
AC_MD=""
IFS='|' read -ra AC_ITEMS <<< "$ACCEPTANCE"
for item in "${AC_ITEMS[@]}"; do
    AC_MD="${AC_MD}\n- [ ] ${item}"
done

# Build verification checks markdown
VC_MD=""
if [ -n "$CHECKS" ]; then
    IFS='|' read -ra VC_ITEMS <<< "$CHECKS"
    for item in "${VC_ITEMS[@]}"; do
        VC_MD="${VC_MD}\n\`\`\`bash\n${item}\n\`\`\`\n"
    done
fi

# Build files list
FILES_MD=""
if [ -n "$FILES" ]; then
    IFS=',' read -ra FILE_ITEMS <<< "$FILES"
    for item in "${FILE_ITEMS[@]}"; do
        FILES_MD="${FILES_MD}\n- \`${item}\`"
    done
fi

# Review required?
REVIEW_SECTION=""
if [ "$REVIEW" = "true" ] || [ "$RISK" = "high" ] || [ "$RISK" = "critical" ]; then
    REVIEW_SECTION="
## Review Required
**review_required:** true
**risk_profile:** ${RISK}

Before implementing, output a structured plan (Phase 3a in AGENTS.md) and WAIT for authorization.
Do NOT proceed to implementation until you receive 'authorized' from the orchestrator."
fi

# QA Guidance section
QA_SECTION=""
if [ -n "$QA_GUIDANCE" ]; then
    QA_SECTION="
## QA Guidance
${QA_GUIDANCE}"
fi

# Context section
CTX_SECTION=""
if [ -n "$CONTEXT" ]; then
    CTX_SECTION="
## Context
${CONTEXT}"
fi

# Build the full task spec prompt
TASK_PROMPT="# Task Spec

**Title:** ${TITLE}
**Type:** ${TYPE}
**Risk:** ${RISK}
**Timeout:** ${TIMEOUT} minutes

## Description
${DESCRIPTION}

## Target Files
${FILES_MD}

## Acceptance Criteria
${AC_MD}

## Verification Checks
${VC_MD}
${REVIEW_SECTION}
${QA_SECTION}
${CTX_SECTION}

---
Follow the 10-step Inner Loop in AGENTS.md. Read lessons.md BEFORE starting.
Report with structured Completion Report format including Metrics section."

# Create MC task via mc-spawn.sh
if [ -x "$MC_SCRIPT" ]; then
    MC_RESULT=$(bash "$MC_SCRIPT" --agent luan --title "$TITLE" --task "$DESCRIPTION" --json 2>/dev/null) || MC_RESULT='{"mc_task_id":"none"}'
else
    MC_RESULT='{"mc_task_id":"none","note":"mc-spawn.sh not found"}'
fi

if [ "$JSON_OUTPUT" -eq 1 ]; then
    MC_TASK_ID=$(echo "$MC_RESULT" | python3 -c "import json,sys; print(json.load(sys.stdin).get('mc_task_id','none'))" 2>/dev/null || echo "none")
    python3 -c "
import json
print(json.dumps({
    'mc_task_id': '$MC_TASK_ID',
    'agent': 'luan',
    'title': $(python3 -c "import json; print(json.dumps('$TITLE'))"),
    'type': '$TYPE',
    'risk': '$RISK',
    'review_required': '$REVIEW' == 'true' or '$RISK' in ('high', 'critical'),
    'timeout_minutes': $TIMEOUT,
    'spawn_prompt': $(python3 -c "import json; print(json.dumps('''$TASK_PROMPT'''))"),
}, indent=2))
"
else
    echo "$TASK_PROMPT"
fi
