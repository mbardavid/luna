#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'USAGE'
legacy-subspawn-bridge.sh

Usage:
  cat legacy.json | legacy-subspawn-bridge.sh [--agent-map luan-dev]
  legacy-subspawn-bridge.sh --in /path/legacy.json --out /tmp/task-spec.json

Converte payload legado de subspawn para TaskSpec A2A compatível com schema 1.1.
USAGE
}

OUTFILE=""
INFILE=""
AGENT_MAP="luan-dev"
RISK="medium"
AUTO_APPROVE_WINDOW="600"
REVIEW_DEPTH="2"
REQUIRE_REVIEW=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --in)
      INFILE="$2"; shift 2 ;;
    --out)
      OUTFILE="$2"; shift 2 ;;
    --agent-map)
      AGENT_MAP="$2"; shift 2 ;;
    --risk)
      RISK="$2"; shift 2 ;;
    --auto-approve-window)
      AUTO_APPROVE_WINDOW="$2"; shift 2 ;;
    --review-depth)
      REVIEW_DEPTH="$2"; shift 2 ;;
    --require-review)
      REQUIRE_REVIEW="1"; shift ;;
    --no-require-review)
      REQUIRE_REVIEW="0"; shift ;;
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

if [[ -n "$INFILE" && ! -f "$INFILE" ]]; then
  echo "input file not found: $INFILE" >&2
  exit 1
fi

legacy_payload="$(if [ -n "$INFILE" ]; then cat "$INFILE"; else cat; fi)"
if [ -z "$legacy_payload" ]; then
  echo "empty input payload" >&2
  exit 1
fi

tmp_payload="$(mktemp)"
printf '%s' "$legacy_payload" > "$tmp_payload"
tmp_output="$(mktemp)"
tmp_script="$(mktemp)"
trap 'rm -f "$tmp_payload" "$tmp_output" "$tmp_script"' EXIT

cat > "$tmp_script" <<'PY'
import hashlib
import json
import sys
from datetime import datetime, timezone

payload_raw = sys.stdin.read()
try:
    src = json.loads(payload_raw)
except json.JSONDecodeError:
    raise SystemExit("legacy payload is not valid JSON")

agent_map = sys.argv[1]
risk = (sys.argv[2] or "medium").strip().lower()
try:
    review_depth = int(sys.argv[3])
except Exception:
    review_depth = 2
try:
    require_review = bool(int(sys.argv[4]))
except Exception:
    require_review = True
try:
    auto_approve_window = int(sys.argv[5])
except Exception:
    auto_approve_window = 600

if risk not in {"low", "medium", "high", "critical"}:
    risk = "medium"

raw_text = json.dumps(src, ensure_ascii=False, sort_keys=True)
loop_id = src.get("loop_id") or f"loop_legacy_{hashlib.sha1(raw_text.encode()).hexdigest()[:12]}"
agent = src.get("agent") or src.get("target") or agent_map
title = src.get("title") or src.get("operation") or src.get("task") or "legacy-subspawn-task"
message = src.get("message") or src.get("task") or src.get("summary") or "legacy task payload"
session = src.get("session") or src.get("sessionKey")

output = {
    "taskSpecVersion": "1.1",
    "handoffId": f"hs_{hashlib.sha1(raw_text.encode()).hexdigest()[:18]}",
    "correlationId": f"corr_legacy_{loop_id[:16]}",
    "loop_id": loop_id,
    "createdAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "proposed_by": "legacy-bridge",
    "source": {
        "agentId": "luna",
        "sessionId": "agent:bridge:legacy"
    },
    "target": {
        "agentId": str(agent),
        "capability": "general"
    },
    "routing": {
        "strategy": "capability",
        "routeKey": "openclaw.general.v1",
        "fallbackAgentId": None
    },
    "mode": "dev",
    "risk_profile": risk,
    "review_depth": review_depth,
    "review_feedback_required": require_review,
    "auto_approve_window": auto_approve_window,
    "review_reason": "Converted legacy payload",
    "intent": {
        "operation": str(title)[:128],
        "inputSchemaRef": "docs/agent-orchestration-a2a.md",
        "summary": str(title),
        "input": {
            "legacy_payload": src,
            "sessionKey": session,
            "message": message,
        }
    },
    "acceptance": {
        "doneWhen": ["Task concluída e entregue com summary de risco"],
    },
    "safety": {
        "e2eActor": "authorized-harness",
        "allowExternalSideEffects": False,
        "requiresHumanApproval": risk in {"high", "critical"},
    },
    "rollback": {
        "required": True,
        "planRef": "docs/migration-legacy-subspawn.md",
        "trigger": "Falha de revisão ou falha de bridge",
    },
    "audit": {
        "requestId": hashlib.sha1(raw_text.encode()).hexdigest()[:24],
        "idempotencyKey": f"idem_{loop_id}",
        "traceId": f"trace_{loop_id[:12]}",
        "delegation": {
            "policyRef": "config/cto-risk-policy.json",
            "envelopeHash": f"sha256:{hashlib.sha256(raw_text.encode()).hexdigest()}",
            "riskClassification": risk,
            "authorizationRef": None,
            "decision": "allowed",
            "validatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recordedBy": "legacy-subspawn-bridge"
        }
    }
}

print(json.dumps(output, ensure_ascii=False, indent=2))
PY

if ! python3 "$tmp_script" "$AGENT_MAP" "$RISK" "$REVIEW_DEPTH" "$REQUIRE_REVIEW" "$AUTO_APPROVE_WINDOW" < "$tmp_payload" > "$tmp_output"; then
  echo "failed to execute converter" >&2
  exit 1
fi

if [ -n "$OUTFILE" ]; then
  mv "$tmp_output" "$OUTFILE"
  echo "Wrote converted TaskSpec to $OUTFILE" >&2
else
  cat "$tmp_output"
  rm -f "$tmp_output"
fi

exit 0
