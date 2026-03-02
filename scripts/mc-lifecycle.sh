#!/usr/bin/env bash
# mc-lifecycle.sh — Manage A2A task lifecycle artifacts
# Usage:
#   mc-lifecycle.sh save-specs  <task_id> <specs_file_or_stdin>
#   mc-lifecycle.sh save-plan   <task_id> <plan_text_or_stdin>
#   mc-lifecycle.sh save-review <task_id> --decision approved|revision_requested --notes "..."
#   mc-lifecycle.sh save-completion <task_id> <completion_text_or_stdin>
#   mc-lifecycle.sh save-qa     <task_id> --decision approved|rejected --notes "..."
#   mc-lifecycle.sh check-gate  <task_id> <gate_number>
#   mc-lifecycle.sh status      <task_id>

set -euo pipefail

WORKSPACE="${WORKSPACE:-/home/openclaw/.openclaw/workspace}"
TASKS_DIR="$WORKSPACE/tasks"

task_id="${2:-}"
action="${1:-}"

if [[ -z "$action" || -z "$task_id" ]]; then
  echo "Usage: mc-lifecycle.sh <action> <task_id> [options]"
  exit 1
fi

TASK_DIR="$TASKS_DIR/$task_id"
mkdir -p "$TASK_DIR"

META="$TASK_DIR/metadata.json"

# Initialize metadata if missing
init_meta() {
  if [[ ! -f "$META" ]]; then
    cat > "$META" << 'METAEOF'
{
  "task_id": "",
  "title": "",
  "type": "",
  "risk": "low",
  "agent": "luan",
  "phases": {},
  "outcome": null,
  "total_spawns": 0,
  "total_cost_estimate_usd": 0
}
METAEOF
  fi
}

update_meta() {
  local key="$1" value="$2"
  python3 -c "
import json, sys
with open('$META') as f: d = json.load(f)
keys = '$key'.split('.')
obj = d
for k in keys[:-1]:
    if k not in obj: obj[k] = {}
    obj = obj[k]
obj[keys[-1]] = json.loads('$value') if '$value'.startswith('{') or '$value'.startswith('[') or '$value'.startswith('\"') or '$value' in ('true','false','null') or '$value'.replace('.','',1).lstrip('-').isdigit() else '$value'
with open('$META','w') as f: json.dump(d, f, indent=2)
"
}

timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

case "$action" in
  save-specs)
    init_meta
    SPECS_FILE="$TASK_DIR/01-specs.md"
    if [[ -n "${3:-}" && -f "${3:-}" ]]; then
      cp "$3" "$SPECS_FILE"
    else
      # Read from stdin or remaining args
      shift 2
      if [[ -t 0 ]]; then
        echo "$*" > "$SPECS_FILE"
      else
        cat > "$SPECS_FILE"
      fi
    fi
    update_meta "task_id" "$task_id"
    update_meta "phases.specs.timestamp" "$(timestamp)"
    echo "✅ Specs saved: $SPECS_FILE"
    ;;

  save-plan)
    init_meta
    PLAN_FILE="$TASK_DIR/02-plan.md"
    if [[ -n "${3:-}" && -f "${3:-}" ]]; then
      cp "$3" "$PLAN_FILE"
    else
      shift 2
      if [[ -t 0 ]]; then
        echo "$*" > "$PLAN_FILE"
      else
        cat > "$PLAN_FILE"
      fi
    fi
    update_meta "phases.plan.timestamp" "$(timestamp)"
    update_meta "phases.plan.status" "plan_submitted"
    echo "✅ Plan saved: $PLAN_FILE"
    ;;

  save-review)
    init_meta
    REVIEW_FILE="$TASK_DIR/03-plan-review.md"
    shift 2
    decision=""
    notes=""
    lessons=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --decision) decision="$2"; shift 2 ;;
        --notes) notes="$2"; shift 2 ;;
        --lessons) lessons="$2"; shift 2 ;;
        *) shift ;;
      esac
    done
    cat > "$REVIEW_FILE" << EOF
# Plan Review — $task_id
**Date:** $(timestamp)
**Decision:** $decision

## Lessons Checked
$lessons

## Notes
$notes
EOF
    update_meta "phases.plan_review.timestamp" "$(timestamp)"
    update_meta "phases.plan_review.decision" "$decision"
    echo "✅ Review saved: $REVIEW_FILE (decision: $decision)"
    ;;

  save-completion)
    init_meta
    COMPLETION_FILE="$TASK_DIR/04-completion.md"
    if [[ -n "${3:-}" && -f "${3:-}" ]]; then
      cp "$3" "$COMPLETION_FILE"
    else
      shift 2
      if [[ -t 0 ]]; then
        echo "$*" > "$COMPLETION_FILE"
      else
        cat > "$COMPLETION_FILE"
      fi
    fi
    update_meta "phases.implementation.timestamp" "$(timestamp)"
    # Try to parse structured completion block
    python3 -c "
import re, json
with open('$COMPLETION_FILE') as f: text = f.read()
m = re.search(r'COMPLETION_STATUS:\s*(\S+)', text)
if m: 
    status = m.group(1)
    with open('$META') as f: d = json.load(f)
    d.setdefault('phases',{}).setdefault('implementation',{})['status'] = status
    for field, key in [('FILES_CHANGED','files_changed'),('TESTS_TOTAL','tests_total'),('TESTS_NEW','tests_new'),('TESTS_PASSING','tests_passing'),('CRITERIA_MET','criteria_met')]:
        fm = re.search(rf'{field}:\s*(.+)', text)
        if fm:
            val = fm.group(1).strip()
            try: val = int(val)
            except: pass
            d['phases']['implementation'][key] = val
    with open('$META','w') as f: json.dump(d, f, indent=2)
    print(f'  Parsed: status={status}')
else:
    print('  ⚠️  No structured COMPLETION_STATUS found')
" 2>/dev/null || true
    echo "✅ Completion saved: $COMPLETION_FILE"
    ;;

  save-qa)
    init_meta
    QA_FILE="$TASK_DIR/05-qa-review.md"
    shift 2
    decision=""
    notes=""
    lessons_violated=""
    files_inspected=""
    verification="false"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --decision) decision="$2"; shift 2 ;;
        --notes) notes="$2"; shift 2 ;;
        --lessons-violated) lessons_violated="$2"; shift 2 ;;
        --files-inspected) files_inspected="$2"; shift 2 ;;
        --verification-ran) verification="true"; shift ;;
        *) shift ;;
      esac
    done
    cat > "$QA_FILE" << EOF
# QA Review — $task_id
**Date:** $(timestamp)
**Decision:** $decision
**Verification ran:** $verification

## Files Inspected
$files_inspected

## Lessons Violated
${lessons_violated:-none}

## Notes
$notes
EOF
    update_meta "phases.qa_review.timestamp" "$(timestamp)"
    update_meta "phases.qa_review.decision" "$decision"
    update_meta "phases.qa_review.verification_ran" "$verification"
    if [[ "$decision" == "approved" ]]; then
      update_meta "outcome" "done"
    elif [[ "$decision" == "rejected" ]]; then
      update_meta "outcome" "rejected"
    fi
    echo "✅ QA Review saved: $QA_FILE (decision: $decision)"
    ;;

  check-gate)
    gate="${3:-}"
    init_meta
    case "$gate" in
      1) # Specs → Plan
        [[ -f "$TASK_DIR/01-specs.md" ]] && echo "✅ Gate 1 PASS: specs exist" || { echo "❌ Gate 1 FAIL: 01-specs.md missing"; exit 1; }
        ;;
      2) # Plan → Plan Review
        [[ -f "$TASK_DIR/02-plan.md" ]] && echo "✅ Gate 2 PASS: plan exists" || { echo "❌ Gate 2 FAIL: 02-plan.md missing"; exit 1; }
        ;;
      3) # Plan Review → Implementation
        [[ -f "$TASK_DIR/03-plan-review.md" ]] || { echo "❌ Gate 3 FAIL: 03-plan-review.md missing"; exit 1; }
        grep -q "approved" "$TASK_DIR/03-plan-review.md" && echo "✅ Gate 3 PASS: plan approved" || { echo "❌ Gate 3 FAIL: plan not approved"; exit 1; }
        ;;
      4) # Implementation → QA
        [[ -f "$TASK_DIR/04-completion.md" ]] && echo "✅ Gate 4 PASS: completion report exists" || { echo "❌ Gate 4 FAIL: 04-completion.md missing"; exit 1; }
        ;;
      5) # QA → Done
        [[ -f "$TASK_DIR/05-qa-review.md" ]] || { echo "❌ Gate 5 FAIL: 05-qa-review.md missing"; exit 1; }
        grep -q "approved" "$TASK_DIR/05-qa-review.md" && echo "✅ Gate 5 PASS: QA approved" || { echo "❌ Gate 5 FAIL: QA not approved"; exit 1; }
        ;;
      *) echo "Unknown gate: $gate (use 1-5)"; exit 1 ;;
    esac
    ;;

  status)
    init_meta
    echo "=== Task $task_id ==="
    for f in 01-specs.md 02-plan.md 03-plan-review.md 04-completion.md 05-qa-review.md; do
      if [[ -f "$TASK_DIR/$f" ]]; then
        echo "  ✅ $f"
      else
        echo "  ⬜ $f"
      fi
    done
    echo ""
    [[ -f "$META" ]] && python3 -c "
import json
with open('$META') as f: d = json.load(f)
phases = d.get('phases',{})
for p in ['specs','plan','plan_review','implementation','qa_review']:
    ph = phases.get(p,{})
    if ph:
        ts = ph.get('timestamp','?')
        status = ph.get('status', ph.get('decision',''))
        print(f'  {p}: {status} ({ts})')
outcome = d.get('outcome','in_progress')
print(f'  outcome: {outcome}')
" || true
    ;;

  *)
    echo "Unknown action: $action"
    echo "Actions: save-specs, save-plan, save-review, save-completion, save-qa, check-gate, status"
    exit 1
    ;;
esac
