#!/usr/bin/env bash
set -euo pipefail

##############################################################################
# mc-spawn.sh — Atomic prep for subagent spawn via Mission Control
#
# Creates an MC task (status: in_progress) with the correct assigned_agent_id,
# then outputs a ready-to-use JSON payload for sessions_spawn.
#
# The caller (Luna) invokes sessions_spawn with the returned params, then
# optionally passes the session_key back to link via --auto-link or
# mc-link-task-session.sh.
##############################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"
MC_LINK="${SCRIPT_DIR}/mc-link-task-session.sh"
AGENT_IDS_FILE="${SCRIPT_DIR}/../config/mc-agent-ids.json"
LOG_DIR="${SCRIPT_DIR}/../logs"
AUDIT_LOG="${LOG_DIR}/mc-spawn-audit.log"

usage() {
  cat <<'USAGE'
mc-spawn.sh — Atomic prep for MC task + spawn payload

Usage:
  mc-spawn.sh --agent <name> --title <string> --task <string> [options]

Required:
  --agent <name>        Agent name (luan, crypto-sage, main, quant-strategist, luan-dev, cto-ops)
  --title <string>      Task title
  --task <string>       Full task description / instructions for the subagent

Structured spec (recommended — auto-formats task description):
  --objective <text>    What needs to be done (1-2 sentences)
  --context <text>      Background: what exists, what failed, what changed
  --plan <text>         Execution plan (pipe-separated steps)
  --workspace <path>    Repository/dir path
  --files <list>        Key files (comma-separated)
  --criteria <text>     Acceptance criteria (pipe-separated for multiple)
  --checks <text>       Verification commands the agent MUST run (pipe-separated)
  --qa <text>           QA questions for Luna to review results (pipe-separated)
  --constraints <text>  Constraints (pipe-separated for multiple)
  --rollback <text>     How to revert if things break
  --task-file <path>    Read full task spec from file instead of flags

Optional:
  --timeout <seconds>   Run timeout (default: 900)
  --priority <level>    Priority: low|medium|high (default: medium)
  --mode <mode>         Session mode: run|chat (default: run)
  --json                Output only JSON (machine-readable)
  --auto-link           After output, read session_key from stdin and link it
  --estimated-cost <n>  Estimated cost in USD
  --mc-task-id <id>     Use existing MC task instead of creating new one
  --loop-id <id>        Review loop identifier (A2A v1.1)
  --risk-profile <lvl>  Risk: low|medium|high|critical (default: medium)
  --review-depth <n>    Max review cycles (default: 2)
  --no-signature        Skip signature requirement

Environment:
  MC_AUTH_TOKEN         Override auth token from config
  MC_CONFIG_PATH        Override mission-control-ids.json path

Example:
  mc-spawn.sh \
    --agent luan-dev \
    --title "Fix auth module" \
    --task "Investigate and fix the auth..." \
    --timeout 900 \
    --priority high \
    --risk-profile medium \
    --loop-id loop_abc123 \
    --json
USAGE
}

# --- Parse arguments ---
AGENT=""
TITLE=""
TASK=""
TIMEOUT=900
PRIORITY="medium"
MODE="run"
OUTPUT_JSON=0
AUTO_LINK=0
ESTIMATED_COST=""
EXISTING_TASK_ID=""
LOOP_ID=""
RISK_PROFILE="medium"
REVIEW_DEPTH=2
NO_SIGNATURE=0
# Structured spec fields
SPEC_OBJECTIVE=""
SPEC_CONTEXT=""
SPEC_PLAN=""
SPEC_WORKSPACE=""
SPEC_FILES=""
SPEC_CRITERIA=""
SPEC_CHECKS=""
SPEC_QA=""
SPEC_CONSTRAINTS=""
SPEC_ROLLBACK=""
TASK_FILE=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --agent)      AGENT="${2:-}";          shift 2 ;;
    --title)      TITLE="${2:-}";          shift 2 ;;
    --task)       TASK="${2:-}";           shift 2 ;;
    --timeout)    TIMEOUT="${2:-900}";     shift 2 ;;
    --priority)   PRIORITY="${2:-medium}"; shift 2 ;;
    --mode)       MODE="${2:-run}";        shift 2 ;;
    --json)       OUTPUT_JSON=1;          shift ;;
    --auto-link)  AUTO_LINK=1;            shift ;;
    --objective)    SPEC_OBJECTIVE="${2:-}";   shift 2 ;;
    --context)      SPEC_CONTEXT="${2:-}";     shift 2 ;;
    --plan)         SPEC_PLAN="${2:-}";        shift 2 ;;
    --workspace)    SPEC_WORKSPACE="${2:-}";   shift 2 ;;
    --files)        SPEC_FILES="${2:-}";       shift 2 ;;
    --criteria)     SPEC_CRITERIA="${2:-}";    shift 2 ;;
    --checks)       SPEC_CHECKS="${2:-}";      shift 2 ;;
    --qa)           SPEC_QA="${2:-}";          shift 2 ;;
    --constraints)  SPEC_CONSTRAINTS="${2:-}"; shift 2 ;;
    --rollback)     SPEC_ROLLBACK="${2:-}";    shift 2 ;;
    --task-file)    TASK_FILE="${2:-}";        shift 2 ;;
    --estimated-cost) ESTIMATED_COST="${2:-}"; shift 2 ;;
    --mc-task-id) EXISTING_TASK_ID="${2:-}"; shift 2 ;;
    --loop-id)    LOOP_ID="${2:-}";          shift 2 ;;
    --risk-profile) RISK_PROFILE="${2:-medium}"; shift 2 ;;
    --review-depth) REVIEW_DEPTH="${2:-2}";  shift 2 ;;
    --no-signature) NO_SIGNATURE=1;          shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            echo "unknown arg: $1" >&2; usage; exit 1 ;;
  esac
done

# --- Validate ---
# Build structured task spec if fields provided
if [ -n "$TASK_FILE" ] && [ -f "$TASK_FILE" ]; then
  TASK=$(cat "$TASK_FILE")
elif [ -n "$SPEC_OBJECTIVE" ]; then
  export SPAWN_AGENT="$AGENT"
  TASK=$(python3 << 'SPECEOF'
import os

objective = os.environ.get("SPEC_OBJECTIVE", "")
context = os.environ.get("SPEC_CONTEXT", "")
plan = os.environ.get("SPEC_PLAN", "")
workspace = os.environ.get("SPEC_WORKSPACE", "")
files = os.environ.get("SPEC_FILES", "")
criteria = os.environ.get("SPEC_CRITERIA", "")
checks = os.environ.get("SPEC_CHECKS", "")
qa = os.environ.get("SPEC_QA", "")
constraints = os.environ.get("SPEC_CONSTRAINTS", "")
rollback = os.environ.get("SPEC_ROLLBACK", "")

spec = f"## Objective\n{objective}\n"

if context.strip():
    spec += f"\n## Context\n{context}\n"

if plan.strip():
    spec += "\n## Execution Plan\n"
    for i, step in enumerate(plan.split("|"), 1):
        spec += f"{i}. {step.strip()}\n"

if workspace.strip() or files.strip():
    spec += "\n## Workspace\n"
    if workspace.strip():
        spec += f"- Repository/dir: `{workspace}`\n"
    if files.strip():
        for f in files.split(","):
            spec += f"- `{f.strip()}`\n"

if criteria.strip():
    spec += "\n## Acceptance Criteria\n"
    for c in criteria.split("|"):
        spec += f"- [ ] {c.strip()}\n"

if checks.strip():
    spec += "\n## Verification Checks\n```bash\n"
    for c in checks.split("|"):
        spec += f"{c.strip()}\n"
    spec += "```\n"

if qa.strip():
    spec += "\n## QA Guidance for Luna\n"
    for q in qa.split("|"):
        spec += f"- {q.strip()}\n"

if constraints.strip():
    spec += "\n## Constraints\n"
    for c in constraints.split("|"):
        spec += f"- {c.strip()}\n"

if rollback.strip():
    spec += f"\n## Rollback\n{rollback}\n"

# Auto-inject relevant lessons from agent's lessons.md
agent_name = os.environ.get("SPAWN_AGENT", "").lower()
lessons_paths = [
    f"/home/openclaw/.openclaw/workspace-{agent_name}/memory/lessons.md",
    f"/home/openclaw/.openclaw/workspace/memory/lessons.md",
]

relevant_lessons = []
search_text = (objective + " " + context).lower()

for lpath in lessons_paths:
    if not os.path.isfile(lpath):
        continue
    try:
        with open(lpath) as lf:
            content = lf.read()
        # Parse lessons by ## Lesson headers
        import re
        lessons = re.split(r'(?=## Lesson \d+)', content)
        for lesson in lessons:
            if not lesson.strip() or not lesson.startswith("## Lesson"):
                continue
            # Extract domain and action
            domain = ""
            action_line = ""
            pattern_line = ""
            for line in lesson.strip().split("\n"):
                if line.startswith("**Domain:**"):
                    domain = line.replace("**Domain:**", "").strip().lower()
                if line.startswith("**Action:**"):
                    action_line = line.replace("**Action:**", "").strip()
                if line.startswith("**Pattern:**"):
                    pattern_line = line.replace("**Pattern:**", "").strip().lower()
            
            if not action_line:
                continue
            
            # Score based on domain match + pattern keyword overlap
            score = 0
            domain_terms = [d.strip() for d in domain.split("/")]
            for dt in domain_terms:
                if dt in search_text:
                    score += 3
            
            # Check pattern keywords (meaningful terms only)
            if pattern_line:
                pattern_words = [w for w in pattern_line.split() if len(w) > 5]
                matches = sum(1 for w in pattern_words if w in search_text)
                score += matches
            
            if score >= 3:
                title_line = lesson.strip().split("\n")[0].replace("## ", "")
                relevant_lessons.append((score, f"- **{title_line}**: {action_line}"))
    except Exception:
        pass

# Sort by relevance, take top 5
relevant_lessons.sort(key=lambda x: -x[0])
if relevant_lessons:
    spec += "\n## ⚠️ Known Pitfalls (from lessons.md)\n"
    for _, rl in relevant_lessons[:5]:
        spec += f"{rl}\n"
    spec += "\n_Review ALL lessons at `memory/lessons.md` before starting._\n"

print(spec)
SPECEOF
  )
fi

if [ -z "$AGENT" ] || [ -z "$TITLE" ] || [ -z "$TASK" ]; then
  echo "Error: --agent, --title, and --task are required" >&2
  usage
  exit 1
fi

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client.sh not found or not executable: $MC_CLIENT" >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not available" >&2
  exit 2
fi

case "$RISK_PROFILE" in
  low|medium|high|critical) ;;
  *) echo "Error: invalid --risk-profile: $RISK_PROFILE" >&2; exit 1 ;;
esac

# --- Resolve agent ID from lookup table ---
resolve_agent_short_id() {
  local agent_name="$1"
  if [ -f "$AGENT_IDS_FILE" ]; then
    python3 -c "
import json, sys
with open('$AGENT_IDS_FILE') as f:
    ids = json.load(f)
name = sys.argv[1].lower().replace('_', '-')
if name in ids:
    print(ids[name])
elif name.replace('-', '_') in ids:
    print(ids[name.replace('-', '_')])
else:
    sys.exit(1)
" "$agent_name" 2>/dev/null
  else
    return 1
  fi
}

# Resolve full agent UUID via mc-client (for MC API)
resolve_agent_full_id() {
  local agent_name="$1"
  # mc-client already has the full UUID resolution via config
  source <(grep -A999 '^mc_cfg()' "$MC_CLIENT" | head -0) 2>/dev/null || true
  # Use mc_resolve_agent_id from mc-client
  bash -c "
    source '$MC_CLIENT' 2>/dev/null
    mc_resolve_agent_id '$agent_name'
  " 2>/dev/null || true
}

# We need the full UUID for the MC API (if available)
AGENT_FULL_ID=""
# First try resolving through mc-client's config (has full UUIDs)
AGENT_FULL_ID=$(python3 -c "
import json, sys
with open('${SCRIPT_DIR}/../config/mission-control-ids.json') as f:
    cfg = json.load(f)
agents = cfg.get('agents', {})
name = sys.argv[1].lower().replace('-', '_')
if name in agents:
    print(agents[name])
else:
    # Try with dashes
    name2 = sys.argv[1].lower().replace('_', '-')
    for k, v in agents.items():
        if k.lower().replace('-', '_') == name or k.lower() == name2:
            print(v)
            sys.exit(0)
    sys.exit(1)
" "$AGENT" 2>/dev/null) || true

if [ -z "$AGENT_FULL_ID" ]; then
  echo "Warning: Could not resolve agent '$AGENT' to UUID. Task will be created without assigned_agent_id." >&2
fi

# --- Slugify title for label ---
LABEL_SLUG=$(python3 -c "
import re, sys
title = sys.argv[1].lower().strip()
slug = re.sub(r'[^a-z0-9]+', '-', title).strip('-')
if len(slug) > 40:
    slug = slug[:40].rstrip('-')
print(slug)
" "$TITLE")

# --- Create MC task (or use existing) ---
TASK_ID=""
CREATED_TASK_JSON=""
ASSIGNEE="${AGENT_FULL_ID:-}"

FIELDS_JSON=$(python3 - <<PY
import json
signature_ok = bool(${NO_SIGNATURE:-0} == 0)
fields = {"mc_progress": 0, "mc_signature_required": signature_ok}
cost = """$ESTIMATED_COST""".strip()
if cost:
    try:
        fields["mc_estimated_cost_usd"] = float(cost)
    except:
        pass
loop_id = """$LOOP_ID""".strip()
if loop_id:
    fields["mc_loop_id"] = loop_id
risk = """$RISK_PROFILE""".strip()
if risk:
    fields["mc_risk_profile"] = risk
review_depth = """$REVIEW_DEPTH""".strip()
if review_depth:
    try:
        fields["mc_review_depth"] = int(review_depth)
    except:
        pass
print(json.dumps(fields, ensure_ascii=False))
PY
)

if [ -n "$EXISTING_TASK_ID" ]; then
  TASK_ID="$EXISTING_TASK_ID"
  # Update existing task to in_progress (preserve custom fields/risk info)
  UPDATE_ARGS=("$TASK_ID" --status "in_progress")
  UPDATE_ARGS+=(--fields "$FIELDS_JSON")
  CREATED_TASK_JSON=$(bash "$MC_CLIENT" update-task "${UPDATE_ARGS[@]}" 2>/dev/null) || true
else
  CREATED_TASK_JSON=$(bash "$MC_CLIENT" create-task "$TITLE" "$TASK" "$ASSIGNEE" "$PRIORITY" "in_progress" "$FIELDS_JSON")
  TASK_ID=$(python3 -c "import sys, json; print(json.load(sys.stdin)['id'])" <<< "$CREATED_TASK_JSON")
fi

if [ -z "$TASK_ID" ]; then
  echo "Error: Failed to create/resolve MC task" >&2
  echo "$CREATED_TASK_JSON" >&2
  exit 1
fi

# Short task ID for label (first 8 chars)
TASK_ID_SHORT="${TASK_ID:0:8}"
SESSION_LABEL="${LABEL_SLUG}-${TASK_ID_SHORT}"

# --- Build spawn params ---
# The task text sent to the subagent includes the MC task reference
SPAWN_TASK_TEXT="${TASK}

MC Task: ${TASK_ID}"

SPAWN_PARAMS=$(python3 -c "
import json, sys

params = {
    'agentId': sys.argv[1],
    'label': sys.argv[2],
    'mode': sys.argv[3],
    'runTimeoutSeconds': int(sys.argv[4]),
    'task': sys.argv[5],
}
print(json.dumps(params, ensure_ascii=False))
" "$AGENT" "$SESSION_LABEL" "$MODE" "$TIMEOUT" "$SPAWN_TASK_TEXT")

# --- Build output ---
OUTPUT=$(python3 -c "
import json, sys

result = {
    'mc_task_id': sys.argv[1],
    'spawn_params': json.loads(sys.argv[2]),
}
print(json.dumps(result, ensure_ascii=False, indent=2))
" "$TASK_ID" "$SPAWN_PARAMS")

# --- Audit log ---
mkdir -p "$LOG_DIR"
echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] mc-spawn: agent=$AGENT task_id=$TASK_ID title=\"$TITLE\" timeout=$TIMEOUT priority=$PRIORITY" >> "$AUDIT_LOG" 2>/dev/null || true

# --- Output ---
if [ "$OUTPUT_JSON" -eq 1 ]; then
  echo "$OUTPUT"
else
  echo "═══════════════════════════════════════════════"
  echo "  MC Task Created + Spawn Payload Ready"
  echo "═══════════════════════════════════════════════"
  echo ""
  echo "  Task ID:    $TASK_ID"
  echo "  Agent:      $AGENT"
  echo "  Label:      $SESSION_LABEL"
  echo "  Timeout:    ${TIMEOUT}s"
  echo "  Priority:   $PRIORITY"
  echo ""
  echo "  Spawn Params (pass to sessions_spawn):"
  echo "$SPAWN_PARAMS" | python3 -m json.tool 2>/dev/null || echo "$SPAWN_PARAMS"
  echo ""
  echo "  Next steps:"
  echo "  1. Call sessions_spawn with the params above"
  echo "  2. Run: bash scripts/mc-link-task-session.sh $TASK_ID <session_key>"
  echo "═══════════════════════════════════════════════"
fi

# --- Auto-link mode ---
if [ "$AUTO_LINK" -eq 1 ]; then
  echo "" >&2
  echo "Waiting for session_key on stdin..." >&2
  read -r SESSION_KEY
  if [ -n "$SESSION_KEY" ]; then
    bash "$MC_LINK" "$TASK_ID" "$SESSION_KEY"
    echo "[mc-spawn] Linked task $TASK_ID to session $SESSION_KEY" >&2
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] mc-spawn: linked task_id=$TASK_ID session_key=$SESSION_KEY" >> "$AUDIT_LOG" 2>/dev/null || true
  else
    echo "Error: Empty session_key received" >&2
    exit 1
  fi
fi
