#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_CFG_LOCAL="${SCRIPT_DIR}/../config/mission-control-ids.local.json"
DEFAULT_CFG_VERSIONED="${SCRIPT_DIR}/../config/mission-control-ids.json"
MC_CONFIG_PATH="${MC_CONFIG_PATH:-$DEFAULT_CFG_LOCAL}"
if [ ! -f "$MC_CONFIG_PATH" ] && [ -f "$DEFAULT_CFG_VERSIONED" ]; then
  MC_CONFIG_PATH="$DEFAULT_CFG_VERSIONED"
fi

if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
else
  DRY_RUN=0
fi

if [ ! -f "$MC_CONFIG_PATH" ]; then
  echo "config not found: $MC_CONFIG_PATH" >&2
  exit 2
fi

mc_cfg() {
  local key="$1"
  python3 - "$MC_CONFIG_PATH" "$key" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as fp:
    cfg = json.load(fp)

value = cfg
for token in sys.argv[2].split("."):
    if not isinstance(value, dict) or token not in value:
        raise SystemExit(1)
    value = value[token]

if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

MC_API_URL="$(mc_cfg api_url)"
MC_BOARD_ID="$(mc_cfg board_id)"
# Token can also be provided via env (preferred for CI/cron): MC_AUTH_TOKEN
MC_TOKEN="${MC_AUTH_TOKEN:-}"
if [ -z "$MC_TOKEN" ]; then
  MC_TOKEN="$(mc_cfg auth_token)"
fi

DESIRED_FIELDS='[
  {
    "field_key": "mc_session_key",
    "label": "MC Session Key",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_retry_count",
    "label": "MC Retry Count",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_progress",
    "label": "MC Progress",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_delivered",
    "label": "MC Delivered",
    "field_type": "boolean",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": false
  },
  {
    "field_key": "mc_output_summary",
    "label": "MC Output Summary",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_approval_notified",
    "label": "MC Approval Notified",
    "field_type": "boolean",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": false
  },
  {
    "field_key": "mc_estimated_cost_usd",
    "label": "MC Estimated Cost (USD)",
    "field_type": "decimal",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_actual_cost_usd",
    "label": "MC Actual Cost (USD)",
    "field_type": "decimal",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_last_error",
    "label": "MC Last Error",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_risk_profile",
    "label": "MC Risk Profile",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "medium"
  },
  {
    "field_key": "mc_review_depth",
    "label": "MC Review Depth",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 2
  },
  {
    "field_key": "mc_signature_required",
    "label": "MC Signature Required",
    "field_type": "boolean",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": false
  },
  {
    "field_key": "mc_rejection_feedback",
    "label": "MC Rejection Feedback",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_review_reason",
    "label": "MC Review Reason",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_authorization_status",
    "label": "MC Authorization Status",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_dispatch_policy",
    "label": "MC Dispatch Policy",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "auto"
  },
  {
    "field_key": "mc_workflow",
    "label": "MC Workflow",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "direct_exec"
  },
  {
    "field_key": "mc_phase",
    "label": "MC Phase",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "intake"
  },
  {
    "field_key": "mc_phase_owner",
    "label": "MC Phase Owner",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_phase_state",
    "label": "MC Phase State",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "pending"
  },
  {
    "field_key": "mc_loop_id",
    "label": "MC Loop ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_plan_artifact",
    "label": "MC Plan Artifact",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_validation_artifact",
    "label": "MC Validation Artifact",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_test_report_artifact",
    "label": "MC Test Report Artifact",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_qa_handoff_fp",
    "label": "MC QA Handoff Fingerprint",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_gate_reason",
    "label": "MC Gate Reason",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_claimed_by",
    "label": "MC Claimed By",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_claim_expires_at",
    "label": "MC Claim Expires At",
    "field_type": "date_time",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_phase_retry_count",
    "label": "MC Phase Retry Count",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_plan_version",
    "label": "MC Plan Version",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 1
  },
  {
    "field_key": "mc_phase_started_at",
    "label": "MC Phase Started At",
    "field_type": "date_time",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_phase_completed_at",
    "label": "MC Phase Completed At",
    "field_type": "date_time",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_assigned_agent",
    "label": "MC Assigned Agent",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_completion_status",
    "label": "MC Completion Status",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_card_type",
    "label": "MC Card Type",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "leaf_task"
  },
  {
    "field_key": "mc_runtime_owner",
    "label": "MC Runtime Owner",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "legacy"
  },
  {
    "field_key": "mc_parent_task_id",
    "label": "MC Parent Task ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_project_id",
    "label": "MC Project ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_milestone_id",
    "label": "MC Milestone ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_workstream_id",
    "label": "MC Workstream ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_generation_mode",
    "label": "MC Generation Mode",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "manual"
  },
  {
    "field_key": "mc_generation_key",
    "label": "MC Generation Key",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_lane",
    "label": "MC Lane",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "ambient"
  },
  {
    "field_key": "mc_delivery_state",
    "label": "MC Delivery State",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "queued"
  },
  {
    "field_key": "mc_run_id",
    "label": "MC Run ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_attempt",
    "label": "MC Attempt",
    "field_type": "integer",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": 0
  },
  {
    "field_key": "mc_proof_ref",
    "label": "MC Proof Ref",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_budget_scope",
    "label": "MC Budget Scope",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "project"
  },
  {
    "field_key": "mc_chairman_state",
    "label": "MC Chairman State",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "planned"
  },
  {
    "field_key": "mc_outcome_ref",
    "label": "MC Outcome Ref",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_acceptance_criteria",
    "label": "MC Acceptance Criteria",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_qa_checks",
    "label": "MC QA Checks",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_expected_artifacts",
    "label": "MC Expected Artifacts",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_planning_intent_id",
    "label": "MC Planning Intent ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_gap_class",
    "label": "MC Gap Class",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_source_observation_id",
    "label": "MC Source Observation ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_escalation_reason",
    "label": "MC Escalation Reason",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_task_seed_spec",
    "label": "MC Task Seed Spec",
    "field_type": "json",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_review_agent",
    "label": "MC Review Agent",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "luna-judge"
  },
  {
    "field_key": "mc_repair_bundle_id",
    "label": "MC Repair Bundle ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_repair_source_task_id",
    "label": "MC Repair Source Task ID",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_repair_reason",
    "label": "MC Repair Reason",
    "field_type": "text_long",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_repair_fingerprint",
    "label": "MC Repair Fingerprint",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": null
  },
  {
    "field_key": "mc_repair_state",
    "label": "MC Repair State",
    "field_type": "text",
    "ui_visibility": "if_set",
    "required": false,
    "default_value": "open"
  }
]'

python3 - "$MC_API_URL" "$MC_BOARD_ID" "$MC_TOKEN" "$DRY_RUN" "$DESIRED_FIELDS" <<'PY'
import json
import sys
from urllib import request


base_url, board_id, token, dry_run_str, fields_json = sys.argv[1:6]
dry_run = int(dry_run_str)
desired_fields = json.loads(fields_json)

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/json",
    "Content-Type": "application/json",
}


def request_json(method, path, payload=None):
    url = f"{base_url.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=headers)
    with request.urlopen(req, timeout=20) as resp:
        text = resp.read().decode("utf-8")
        if not text:
            return {}
        return json.loads(text)


existing = request_json("GET", "/organizations/me/custom-fields")
existing_by_key = {item["field_key"]: item for item in existing}
summary = {"created": [], "updated": [], "noop": []}

for field in desired_fields:
    key = field["field_key"]
    current = existing_by_key.get(key)

    if current is None:
        if dry_run:
            summary["created"].append({"field_key": key, "mode": "create"})
            continue
        payload = {
            "field_key": key,
            "label": field["label"],
            "field_type": field["field_type"],
            "ui_visibility": field["ui_visibility"],
            "required": bool(field["required"]),
            "default_value": field["default_value"],
            "board_ids": [board_id],
        }
        created = request_json("POST", "/organizations/me/custom-fields", payload)
        summary["created"].append({"field_key": key, "id": created.get("id")})
        continue

    board_ids = set(current.get("board_ids", []))
    if board_id in board_ids:
        summary["noop"].append({"field_key": key, "id": current["id"]})
        continue

    next_board_ids = sorted(board_ids | {board_id})
    if dry_run:
        summary["updated"].append(
            {
                "field_key": key,
                "id": current["id"],
                "mode": "bind-board",
                "board_ids": next_board_ids,
            },
        )
        continue

    request_json(
        "PATCH",
        f"/organizations/me/custom-fields/{current['id']}",
        {"board_ids": next_board_ids},
    )
    summary["updated"].append(
        {
            "field_key": key,
            "id": current["id"],
            "board_ids": next_board_ids,
        },
    )

print(json.dumps(summary, indent=2, sort_keys=True))
PY
