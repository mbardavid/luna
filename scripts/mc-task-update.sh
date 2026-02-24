#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MC_CLIENT="${SCRIPT_DIR}/mc-client.sh"

usage() {
  cat <<'USAGE'
mc-task-update.sh

Parse a TASK_UPDATE block and mirror it into Mission Control.

Usage:
  mc-task-update.sh [--task-id <taskId>] [--input <file>] [--strict] [--dry-run]
  mc-task-update.sh [--task-id <taskId>] [--strict] [--dry-run] < /path/to/output.txt

Task ID precedence:
- --task-id (if supplied) must match parsed taskId (unless --strict=0 and taskId missing)
- parsed taskId from TASK_UPDATE payload when present

Input format:
- raw output text with a `TASK_UPDATE {...}` JSON block
- optional plain JSON object (same fields as TASK_UPDATE)
USAGE
}

EXPECTED_TASK_ID=""
INPUT_FILE=""
STRICT=0
DRY_RUN=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --task-id)
      EXPECTED_TASK_ID="${2:-}"
      shift 2
      ;;
    --input)
      INPUT_FILE="${2:-}"
      shift 2
      ;;
    --strict)
      STRICT=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown arg: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [ ! -x "$MC_CLIENT" ]; then
  echo "mc-client script not found or not executable: $MC_CLIENT" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 not available" >&2
  exit 1
fi

if [ -n "$INPUT_FILE" ]; then
  if [ ! -f "$INPUT_FILE" ]; then
    echo "input file not found: $INPUT_FILE" >&2
    exit 1
  fi
  raw_payload="$(cat "$INPUT_FILE")"
else
  raw_payload="$(cat)"
fi

parsed_json=$(
  RAW_PAYLOAD="$raw_payload" TASK_ID_EXPECTED="$EXPECTED_TASK_ID" STRICT="$STRICT" python3 - <<'PY'
import json
import os
import sys

text = os.environ.get("RAW_PAYLOAD", "")
expected_task_id = os.environ.get("TASK_ID_EXPECTED", "").strip()
strict = os.environ.get("STRICT", "0") == "1"


def _normalize_status(value: str) -> str:
    if not value:
        return "in_progress"
    normalized = value.strip().lower().replace("-", "_")
    mapping = {
        "inprogress": "in_progress",
        "running": "in_progress",
        "running_task": "in_progress",
        "active": "in_progress",
        "completed": "done",
        "finished": "done",
        "failed": "failed",
        "error": "failed",
        "blocked": "blocked",
        "needs_approval": "needs_approval",
        "needsapproval": "needs_approval",
        "review": "review",
        "stalled": "stalled",
        "retry": "retry",
    }
    return mapping.get(normalized, normalized if normalized in {"in_progress", "done", "failed", "blocked", "needs_approval", "review", "stalled", "retry"} else value)


def _extract_updates(raw_text: str):
    lower = raw_text.lower()
    marker = "task_update"
    updates = []
    start = 0
    while True:
        idx = lower.find(marker, start)
        if idx < 0:
            break
        brace = raw_text.find("{", idx)
        if brace < 0:
            break

        depth = 0
        for pos in range(brace, len(raw_text)):
            ch = raw_text[pos]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = raw_text[brace : pos + 1]
                    try:
                        payload = json.loads(candidate)
                        updates.append(payload)
                    except Exception:
                        pass
                    start = pos + 1
                    break
        else:
            break
    return updates


updates = _extract_updates(text)
if not updates:
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            fallback = json.loads(stripped)
            updates.append(fallback)
        except Exception:
            pass

if not updates:
    if strict:
        raise SystemExit("no TASK_UPDATE block found")
    updates = [{}]

payload = updates[-1]
task_id = str(payload.get("taskId") or payload.get("task_id") or "").strip()
if expected_task_id and task_id and task_id != expected_task_id:
    raise SystemExit("taskId mismatch")
if not task_id:
    if expected_task_id:
        task_id = expected_task_id
    elif strict:
        raise SystemExit("TASK_UPDATE missing taskId")

status = _normalize_status(str(payload.get("status", "in_progress")))
progress = payload.get("progress", 0)
try:
    progress = int(progress)
except Exception:
    progress = 0
progress = max(0, min(100, progress))

summary = payload.get("summary") or ""
error = payload.get("error")
cost = payload.get("cost")
artifacts = payload.get("artifacts") or []
if not isinstance(artifacts, list):
    artifacts = [str(artifacts)]

comment_lines = [
    f"[TASK_UPDATE] taskId={task_id}",
    f"status={status}",
    f"progress={progress}",
]
if summary:
    comment_lines.append(f"summary={summary}")
if error is not None:
    comment_lines.append(f"error={error}")
if artifacts:
    comment_lines.append("artifacts=" + ", ".join(map(str, artifacts)))
comment = "\n".join(comment_lines)

fields = {"mc_progress": progress}
if summary and status in {"done", "failed"}:
    fields["mc_output_summary"] = str(summary)
if cost is not None:
    try:
        fields["mc_actual_cost_usd"] = float(cost)
    except Exception:
        pass
if error is not None:
    fields["mc_last_error"] = str(error)

print(json.dumps({
    "task_id": task_id,
    "status": status,
    "progress": progress,
    "summary": summary,
    "error": error,
    "artifacts": artifacts,
    "comment": comment,
    "fields": fields,
}))
PY
<<< "$raw_payload"
)

if [ -z "$parsed_json" ]; then
  echo "failed to parse TASK_UPDATE payload" >&2
  exit 1
fi

task_id="$(python3 -c 'import sys, json; print(json.load(sys.stdin)["task_id"])' <<< "$parsed_json")"
status="$(python3 -c 'import sys, json; print(json.load(sys.stdin)["status"])' <<< "$parsed_json")"
comment="$(python3 -c 'import sys, json; print(json.load(sys.stdin)["comment"])' <<< "$parsed_json")"
fields_json="$(python3 -c 'import sys, json; print(json.dumps(json.load(sys.stdin)["fields"]))' <<< "$parsed_json")"

if [ "$DRY_RUN" -eq 1 ]; then
  printf '%s\n' "$parsed_json"
  exit 0
fi

if [ -z "$status" ]; then
  status="in_progress"
fi

"$MC_CLIENT" update-task "$task_id" --status "$status" --comment "$comment" --fields "$fields_json"
echo "TASK_UPDATE processed and mirrored to MC task: $task_id"
