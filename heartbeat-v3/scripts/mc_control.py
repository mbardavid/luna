#!/usr/bin/env python3
"""Shared Mission Control control-plane helpers for heartbeat-v3 scripts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

CANONICAL_STATUSES = {
    "inbox",
    "in_progress",
    "review",
    "awaiting_human",
    "done",
    "failed",
    "blocked",
    "stalled",
    "retry",
}

STATUS_ALIASES = {
    "todo": "inbox",
    "new": "inbox",
    "created": "inbox",
    "inprogress": "in_progress",
    "running": "in_progress",
    "running_task": "in_progress",
    "active": "in_progress",
    "completed": "done",
    "finished": "done",
    "error": "failed",
    "needsapproval": "awaiting_human",
    "needs_approval": "awaiting_human",
    "needs-approval": "awaiting_human",
    "requires_approval": "awaiting_human",
    "requires-approval": "awaiting_human",
    "awaiting_approval": "awaiting_human",
    "awaitinghuman": "awaiting_human",
    "waiting": "review",
    "needs_review": "review",
    "needs-review": "review",
}

DISPATCH_POLICIES = {"auto", "backlog", "human_hold"}
WORKFLOWS = {"direct_exec", "dev_loop_v1"}
PHASE_STATES = {"pending", "claimed", "completed", "rejected"}
CARD_TYPES = {"project", "milestone", "workstream", "leaf_task", "review_bundle", "repair_bundle"}
CARD_TYPE_ALIASES = {
    "project": "project",
    "milestone": "milestone",
    "workstream": "workstream",
    "leaf": "leaf_task",
    "leaf_task": "leaf_task",
    "leaf-task": "leaf_task",
    "review_bundle": "review_bundle",
    "review-bundle": "review_bundle",
    "repair_bundle": "repair_bundle",
    "repair-bundle": "repair_bundle",
}
GENERATION_MODES = {"manual", "autonomy"}
LANES = {"ambient", "project", "review", "repair"}
DELIVERY_STATES = {"queued", "dispatched", "linked", "in_progress", "running", "proof_pending", "review", "review_pending", "done"}
CHAIRMAN_STATES = {"planned", "active", "steering", "approved", "paused", "completed", "terminated"}
NON_EXECUTABLE_CARD_TYPES = {"project", "milestone", "workstream", "repair_bundle"}
REPAIR_STATES = {"open", "diagnosing", "repairing", "validating", "resolved", "failed"}
RUNTIME_OWNERS = {"legacy", "controller-v1"}

LUNA_REVIEW_PHASES = {
    "luna_task_planning",
    "luna_plan_validation",
    "luna_final_validation",
}

LUAN_PROGRESS_PHASES = {
    "luan_plan_elaboration",
    "luan_execution_and_tests",
}

PHASE_TO_OWNER = {
    "intake": "none",
    "luna_task_planning": "luna",
    "luan_plan_elaboration": "luan",
    "luna_plan_validation": "luna",
    "luan_execution_and_tests": "luan",
    "luna_final_validation": "luna",
    "awaiting_human_decision": "human",
    "done": "none",
}

PHASE_TO_STATUS = {
    "intake": "inbox",
    "luna_task_planning": "review",
    "luan_plan_elaboration": "in_progress",
    "luna_plan_validation": "review",
    "luan_execution_and_tests": "in_progress",
    "luna_final_validation": "review",
    "awaiting_human_decision": "awaiting_human",
    "done": "done",
}

PRIMARY_CHANNEL = "1473367119377731800"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None = None) -> str:
    current = dt or utcnow()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def normalize_status(value: Any, default: str = "inbox") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    if not text:
        return default
    text = STATUS_ALIASES.get(text, text)
    return text if text in CANONICAL_STATUSES else default


def normalize_dispatch_policy(value: Any, default: str = "auto") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in DISPATCH_POLICIES else default


def normalize_workflow(value: Any, default: str = "direct_exec") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in WORKFLOWS else default


def normalize_phase_state(value: Any, default: str = "pending") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in PHASE_STATES else default


def _description_dispatch_policy(task: dict[str, Any]) -> str:
    description = str(task.get("description") or "")
    for raw_line in description.splitlines()[:20]:
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("dispatch policy:"):
            return normalize_dispatch_policy(line.split(":", 1)[1].strip(), default="auto")
        if lower.startswith("mc_dispatch_policy:"):
            return normalize_dispatch_policy(line.split(":", 1)[1].strip(), default="auto")
    return "auto"


def task_fields(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("custom_field_values") or {}


def task_status(task: dict[str, Any], default: str = "inbox") -> str:
    return normalize_status(task.get("status"), default=default)


def task_dispatch_policy(task: dict[str, Any]) -> str:
    field_value = normalize_dispatch_policy(task_fields(task).get("mc_dispatch_policy"), default="")
    if field_value:
        return field_value
    return _description_dispatch_policy(task)


def task_workflow(task: dict[str, Any]) -> str:
    return normalize_workflow(task_fields(task).get("mc_workflow"), default="direct_exec")


def task_phase(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    phase = str(fields.get("mc_phase") or "").strip()
    if phase:
        return phase
    workflow = task_workflow(task)
    status = task_status(task)
    if workflow == "dev_loop_v1":
        if status == "review":
            return "luna_task_planning"
        if status == "in_progress":
            return "luan_plan_elaboration"
    return "intake" if status == "inbox" else status


def task_phase_owner(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    owner = str(fields.get("mc_phase_owner") or "").strip().lower()
    if owner:
        return owner
    return PHASE_TO_OWNER.get(task_phase(task), "none")


def task_phase_state(task: dict[str, Any]) -> str:
    return normalize_phase_state(task_fields(task).get("mc_phase_state"), default="pending")


def normalize_card_type(value: Any, default: str = "leaf_task") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    if not text:
        return default
    text = CARD_TYPE_ALIASES.get(text, text)
    return text if text in CARD_TYPES else default


def normalize_generation_mode(value: Any, default: str = "manual") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in GENERATION_MODES else default


def normalize_lane(value: Any, default: str = "ambient") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in LANES else default


def normalize_delivery_state(value: Any, default: str = "queued") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in DELIVERY_STATES else default


def normalize_chairman_state(value: Any, default: str = "planned") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    return text if text in CHAIRMAN_STATES else default


def normalize_repair_state(value: Any, default: str = "open") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("-", "_")
    if not text:
        return default
    return text if text in REPAIR_STATES else default


def normalize_runtime_owner(value: Any, default: str = "legacy") -> str:
    if value is None:
        return default
    text = str(value).strip().lower().replace("_", "-")
    if not text:
        return default
    return text if text in RUNTIME_OWNERS else default


def task_card_type(task: dict[str, Any]) -> str:
    return normalize_card_type(task_fields(task).get("mc_card_type"), default="leaf_task")


def task_runtime_owner(task: dict[str, Any], default: str = "legacy") -> str:
    return normalize_runtime_owner(task_fields(task).get("mc_runtime_owner"), default=default)


def is_controller_owned(task: dict[str, Any]) -> bool:
    return task_runtime_owner(task) == "controller-v1"


def task_parent_task_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_parent_task_id") or "").strip()


def task_project_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_project_id") or "").strip()


def task_milestone_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_milestone_id") or "").strip()


def task_workstream_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_workstream_id") or "").strip()


def task_generation_mode(task: dict[str, Any]) -> str:
    return normalize_generation_mode(task_fields(task).get("mc_generation_mode"), default="manual")


def task_generation_key(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    explicit = str(fields.get("mc_generation_key") or "").strip()
    if explicit:
        return explicit
    title = str(task.get("title") or "").strip().lower()
    if title and task_workstream_id(task):
        return title
    return ""


def task_gate_reason(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_gate_reason") or "").strip()


def task_budget_scope(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    value = str(fields.get("mc_budget_scope") or "").strip()
    if value:
        return value
    if task_project_id(task):
        return "project"
    return "task"


def task_review_agent(task: dict[str, Any], default: str = "luna-judge") -> str:
    explicit = str(task_fields(task).get("mc_review_agent") or "").strip().lower()
    if explicit:
        return explicit
    if task_card_type(task) == "review_bundle":
        return default
    if task_phase_owner(task) == "luna":
        return default
    return default


def task_repair_bundle_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_repair_bundle_id") or "").strip()


def task_repair_source_task_id(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_repair_source_task_id") or "").strip()


def task_repair_reason(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_repair_reason") or "").strip()


def task_repair_fingerprint(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_repair_fingerprint") or "").strip()


def task_repair_state(task: dict[str, Any], default: str = "") -> str:
    return normalize_repair_state(task_fields(task).get("mc_repair_state"), default=default)


def task_chairman_state(task: dict[str, Any]) -> str:
    return normalize_chairman_state(task_fields(task).get("mc_chairman_state"), default="planned")


def task_acceptance_criteria(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_acceptance_criteria") or "").strip()


def task_qa_checks(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_qa_checks") or "").strip()


def task_expected_artifacts(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    explicit = str(fields.get("mc_expected_artifacts") or "").strip()
    if explicit:
        return explicit
    fallbacks = [
        str(fields.get("mc_plan_artifact") or "").strip(),
        str(fields.get("mc_validation_artifact") or "").strip(),
        str(fields.get("mc_test_report_artifact") or "").strip(),
    ]
    return "\n".join(item for item in fallbacks if item)


def task_expected_artifact_list(task: dict[str, Any]) -> list[str]:
    raw = task_expected_artifacts(task)
    if not raw:
        return []
    parts: list[str] = []
    for chunk in str(raw).replace(",", "\n").splitlines():
        value = str(chunk or "").strip()
        if value:
            parts.append(value)
    return parts


def resolve_workspace_artifact_path(reference: str, workspace_root: str | Path) -> Path:
    path = Path(str(reference or "").strip())
    if path.is_absolute():
        return path
    return Path(workspace_root) / path


def task_proof_ref(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    explicit = str(fields.get("mc_proof_ref") or "").strip()
    if explicit:
        return explicit
    for key in ("mc_validation_artifact", "mc_test_report_artifact", "mc_plan_artifact"):
        value = str(fields.get(key) or "").strip()
        if value:
            return value
    return ""


def task_execution_owner(task: dict[str, Any]) -> str:
    assigned = str(task.get("assigned_agent_id") or "").strip()
    if assigned:
        return assigned
    explicit = str(task_fields(task).get("mc_assigned_agent") or "").strip().lower()
    if explicit and explicit not in {"none", "human"}:
        return explicit
    owner = str(task_fields(task).get("mc_phase_owner") or "").strip().lower()
    if owner and owner not in {"none", "human"}:
        return owner
    return ""


def task_delivery_state(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    explicit = normalize_delivery_state(fields.get("mc_delivery_state"), default="")
    if explicit:
        return explicit
    status = task_status(task)
    session_key = str(fields.get("mc_session_key") or "").strip()
    if status == "done":
        return "done"
    if status == "review":
        return "review"
    if session_key:
        return "linked"
    if status == "in_progress":
        return "in_progress"
    return "queued"


def is_governance_card(task: dict[str, Any]) -> bool:
    return task_card_type(task) in NON_EXECUTABLE_CARD_TYPES


def is_repair_bundle(task: dict[str, Any]) -> bool:
    return task_card_type(task) == "repair_bundle"


def is_execution_task(task: dict[str, Any]) -> bool:
    return task_card_type(task) == "leaf_task"


def is_running_execution_task(task: dict[str, Any]) -> bool:
    if not is_execution_task(task):
        return False
    if task_status(task) != "in_progress":
        return False
    if task_gate_reason(task):
        return False
    if task_delivery_state(task) in {"queued", "done"}:
        return False
    return True


def requires_session_link(task: dict[str, Any]) -> bool:
    return is_running_execution_task(task)


def is_actionable_review_task(task: dict[str, Any]) -> bool:
    if task_status(task) != "review":
        return False
    card_type = task_card_type(task)
    if card_type == "review_bundle":
        return True
    if card_type != "leaf_task":
        return False
    workflow = task_workflow(task)
    if workflow == "dev_loop_v1":
        return task_phase(task) in LUNA_REVIEW_PHASES and task_phase_owner(task) == "luna"
    return True


def task_lane(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    if task_card_type(task) == "repair_bundle":
        return "repair"
    if task_repair_bundle_id(task):
        if task_card_type(task) == "review_bundle":
            return "review"
        return "repair"
    explicit = normalize_lane(fields.get("mc_lane"), default="")
    if explicit:
        return explicit
    if is_actionable_review_task(task):
        return "review"
    if is_governance_card(task):
        return "project"
    if task_project_id(task) or task_milestone_id(task) or task_workstream_id(task):
        return "project"
    return "ambient"


def task_attempt(task: dict[str, Any]) -> int:
    fields = task_fields(task)
    try:
        return int(fields.get("mc_attempt", 0) or 0)
    except Exception:
        return 0


def build_run_id(task: dict[str, Any], attempt: int | None = None, now: datetime | None = None) -> str:
    task_id = str(task.get("id") or "task").strip()[:8] or "task"
    current_attempt = int(attempt if attempt is not None else max(task_attempt(task), 1))
    stamp = (now or utcnow()).strftime("%Y%m%dT%H%M%SZ")
    return f"{task_id}-a{current_attempt}-{stamp}"


def is_leaf_task(task: dict[str, Any]) -> bool:
    return task_card_type(task) == "leaf_task"


def is_executable_leaf_task(task: dict[str, Any]) -> bool:
    if not is_leaf_task(task):
        return False
    if task_gate_reason(task):
        return False
    if task_lane(task) == "repair":
        return bool(task_execution_owner(task) and task_acceptance_criteria(task) and task_qa_checks(task) and task_expected_artifacts(task))
    if not task_project_id(task) or not task_milestone_id(task) or not task_workstream_id(task):
        return False
    if not task_execution_owner(task):
        return False
    if not task_acceptance_criteria(task):
        return False
    if not task_qa_checks(task):
        return False
    if not task_expected_artifacts(task):
        return False
    return True


def is_ready_to_run(task: dict[str, Any]) -> bool:
    if not is_execution_task(task):
        return False
    if task_status(task) != "inbox":
        return False
    if task_dispatch_policy(task) != "auto":
        return False
    if task_gate_reason(task):
        return False
    if task_lane(task) in {"project", "repair"}:
        return is_executable_leaf_task(task)
    return True


def is_luna_review_task(task: dict[str, Any]) -> bool:
    return is_actionable_review_task(task) and task_phase_owner(task) == "luna"


def is_claim_active(task: dict[str, Any], now: datetime | None = None) -> bool:
    fields = task_fields(task)
    claimer = str(fields.get("mc_claimed_by") or "").strip()
    expires_at = parse_iso(fields.get("mc_claim_expires_at"))
    if not claimer or not expires_at:
        return False
    current = now or utcnow()
    return expires_at > current


def queue_phase(dispatch_type: str, task: dict[str, Any] | None = None) -> str:
    kind = str(dispatch_type or "dispatch").strip().lower().replace("_", "-")
    if kind == "respawn":
        return "respawn"
    if kind in {"review", "qa-review", "qa_review"}:
        if task:
            return task_phase(task)
        return "review"
    if task:
        workflow = task_workflow(task)
        phase = task_phase(task)
        if workflow == "direct_exec" and phase in {"intake", "inbox"}:
            return "direct_exec"
        return phase
    return "direct_exec"


def build_queue_key(task_id: str, dispatch_type: str, status: str, phase: str) -> str:
    raw = "|".join([
        str(task_id or "").strip(),
        str(dispatch_type or "").strip().lower(),
        normalize_status(status, default="inbox"),
        str(phase or "").strip().lower(),
    ])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw}|{digest}"


def queue_key_for_task(task: dict[str, Any], dispatch_type: str) -> str:
    return build_queue_key(
        str(task.get("id") or "").strip(),
        dispatch_type,
        task_status(task),
        queue_phase(dispatch_type, task),
    )


def task_loop_id(task: dict[str, Any]) -> str:
    fields = task_fields(task)
    existing = str(fields.get("mc_loop_id") or "").strip()
    if existing:
        return existing
    seed = f"{task.get('id','')}|{task.get('title','')}|{task.get('created_at','')}"
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def base_phase_fields(task: dict[str, Any]) -> dict[str, Any]:
    fields = dict(task_fields(task))
    fields.setdefault("mc_workflow", task_workflow(task))
    fields.setdefault("mc_dispatch_policy", task_dispatch_policy(task))
    fields.setdefault("mc_loop_id", task_loop_id(task))
    fields.setdefault("mc_card_type", task_card_type(task))
    fields.setdefault("mc_generation_mode", task_generation_mode(task))
    fields.setdefault("mc_generation_key", task_generation_key(task) or None)
    fields.setdefault("mc_lane", task_lane(task))
    fields.setdefault("mc_delivery_state", task_delivery_state(task))
    fields.setdefault("mc_attempt", task_attempt(task))
    fields.setdefault("mc_budget_scope", task_budget_scope(task))
    fields.setdefault("mc_chairman_state", task_chairman_state(task))
    if fields.get("mc_review_agent") or task_card_type(task) in {"review_bundle", "repair_bundle"} or task_phase_owner(task) == "luna":
        fields.setdefault("mc_review_agent", task_review_agent(task))
    if (
        task_card_type(task) == "repair_bundle"
        or task_repair_bundle_id(task)
        or task_repair_source_task_id(task)
        or task_repair_fingerprint(task)
    ):
        fields.setdefault("mc_repair_bundle_id", task_repair_bundle_id(task) or None)
        fields.setdefault("mc_repair_source_task_id", task_repair_source_task_id(task) or None)
        fields.setdefault("mc_repair_reason", task_repair_reason(task) or None)
        fields.setdefault("mc_repair_fingerprint", task_repair_fingerprint(task) or None)
        fields.setdefault("mc_repair_state", task_repair_state(task, default="open"))
    return fields


def plan_artifact_path(task_id: str, phase: str) -> str:
    safe_phase = phase.replace("/", "-")
    return f"artifacts/mc/{task_id[:8]}-{safe_phase}.md"


def extract_session_key_from_agent_result(payload_text: str, agent: str = "") -> str:
    def emit_first(value: Any) -> str:
        return value if isinstance(value, str) and value else ""

    def first_match(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        for pattern in (
            r"DISPATCHED\s+session=([a-zA-Z0-9:_-]+)",
            r"session=([a-zA-Z0-9:_-]+)",
            r"session[:=]{1}([a-zA-Z0-9:_-]+)",
        ):
            match = re.search(pattern, value)
            if match:
                return match.group(1)
        return ""

    try:
        payload = json.loads(payload_text or "{}")
    except Exception:
        return ""

    result = payload.get("result") if isinstance(payload, dict) else {}
    for key in ("sessionKey", "session_id", "sessionId", "session", "targetSession"):
        value = emit_first(result.get(key) if isinstance(result, dict) else "")
        if value:
            return value
        value = emit_first(payload.get(key) if isinstance(payload, dict) else "")
        if value:
            return value

    payloads = []
    if isinstance(result, dict):
        payloads.extend(result.get("payloads") or [])
    if isinstance(payload, dict):
        payloads.extend(payload.get("payloads") or [])

    for item in payloads:
        if not isinstance(item, dict):
            continue
        for field in ("text", "message"):
            extracted = first_match(item.get(field))
            if extracted:
                return extracted

    for parent in (payload, result if isinstance(result, dict) else {}):
        report = parent.get("systemPromptReport") if isinstance(parent, dict) else {}
        value = emit_first((report or {}).get("sessionKey"))
        if value:
            return value
        meta = parent.get("meta") if isinstance(parent, dict) else {}
        if isinstance(meta, dict):
            report = meta.get("systemPromptReport") or {}
            value = emit_first((report or {}).get("sessionKey"))
            if value:
                return value

    if agent == "main":
        return "agent:main:main"
    return ""


def build_phase_update(task: dict[str, Any], phase: str, *, status: str | None = None,
                       phase_state: str = "pending", claimed_by: str | None = None,
                       claim_expires_at: str | None = None, extra_fields: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = base_phase_fields(task)
    resolved_status = normalize_status(status or PHASE_TO_STATUS.get(phase, task_status(task)), default=task_status(task))
    fields.update({
        "mc_phase": phase,
        "mc_phase_owner": PHASE_TO_OWNER.get(phase, "none"),
        "mc_phase_state": normalize_phase_state(phase_state, default="pending"),
        "mc_claimed_by": claimed_by,
        "mc_claim_expires_at": claim_expires_at,
    })
    if extra_fields:
        fields.update(extra_fields)
    return {
        "status": resolved_status,
        "fields": fields,
    }


def route_dev_loop_intake(task: dict[str, Any]) -> dict[str, Any]:
    artifact = plan_artifact_path(str(task.get("id") or ""), "luna-task-planning")
    return build_phase_update(
        task,
        "luna_task_planning",
        status="review",
        extra_fields={
            "mc_plan_artifact": artifact,
            "mc_validation_artifact": "",
            "mc_test_report_artifact": "",
            "mc_gate_reason": "",
            "mc_phase_retry_count": int(task_fields(task).get("mc_phase_retry_count", 0) or 0),
            "mc_phase_started_at": to_iso(),
            "mc_phase_completed_at": None,
            "mc_delivery_state": "review",
        },
    )


def claim_review(task: dict[str, Any], claimer: str, lease_minutes: int = 20) -> dict[str, Any]:
    now = utcnow()
    expires = now + timedelta(minutes=lease_minutes)
    return build_phase_update(
        task,
        task_phase(task),
        status="review",
        phase_state="claimed",
        claimed_by=claimer,
        claim_expires_at=to_iso(expires),
    )


def clear_claim(task: dict[str, Any], *, phase_state: str = "pending") -> dict[str, Any]:
    return build_phase_update(
        task,
        task_phase(task),
        status=task_status(task),
        phase_state=phase_state,
        claimed_by=None,
        claim_expires_at=None,
    )


def apply_dev_loop_transition(current_phase: str, current_fields: dict[str, Any], requested_status: str,
                              review_reason: str = "", artifacts: list[str] | None = None,
                              summary: str = "") -> dict[str, Any]:
    requested_status = normalize_status(requested_status, default="in_progress")
    review_reason = str(review_reason or "").strip()
    fields = dict(current_fields or {})
    fields.setdefault("mc_workflow", normalize_workflow(fields.get("mc_workflow"), default="dev_loop_v1"))
    if not fields.get("mc_loop_id"):
        fields["mc_loop_id"] = hashlib.sha1(json.dumps(fields, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    fields.setdefault("mc_phase_retry_count", int(fields.get("mc_phase_retry_count", 0) or 0))
    artifacts = [str(item).strip() for item in (artifacts or []) if str(item).strip()]

    result_phase = current_phase
    result_status = requested_status
    extra: dict[str, Any] = {
        "mc_claimed_by": None,
        "mc_claim_expires_at": None,
        "mc_gate_reason": fields.get("mc_gate_reason", ""),
    }

    if current_phase == "luna_task_planning" and requested_status == "in_progress":
        result_phase = "luan_plan_elaboration"
        if artifacts:
            extra["mc_plan_artifact"] = artifacts[0]
    elif current_phase == "luan_plan_elaboration" and requested_status == "review":
        result_phase = "luna_plan_validation"
        if artifacts:
            extra["mc_plan_artifact"] = artifacts[0]
    elif current_phase == "luna_plan_validation":
        if requested_status == "awaiting_human":
            result_phase = "awaiting_human_decision"
            extra["mc_gate_reason"] = review_reason or "needs_plan_validation"
        elif requested_status == "in_progress":
            if review_reason:
                result_phase = "luan_plan_elaboration"
                extra["mc_rejection_feedback"] = review_reason
            else:
                result_phase = "luan_execution_and_tests"
        elif requested_status == "review":
            result_phase = "luna_plan_validation"
    elif current_phase == "luan_execution_and_tests" and requested_status == "review":
        result_phase = "luna_final_validation"
        if artifacts:
            extra["mc_test_report_artifact"] = artifacts[0]
    elif current_phase == "luna_final_validation":
        if requested_status == "done":
            result_phase = "done"
            if artifacts:
                extra["mc_validation_artifact"] = artifacts[0]
        elif requested_status == "awaiting_human":
            result_phase = "awaiting_human_decision"
            extra["mc_gate_reason"] = review_reason or "needs_final_validation"
        elif requested_status == "in_progress":
            result_phase = "luan_execution_and_tests"
            extra["mc_rejection_feedback"] = review_reason
            extra["mc_phase_retry_count"] = int(fields.get("mc_phase_retry_count", 0) or 0) + 1
    elif requested_status == "awaiting_human":
        result_phase = "awaiting_human_decision"
        extra["mc_gate_reason"] = review_reason or str(fields.get("mc_gate_reason") or "human_gate")
    elif requested_status == "done":
        result_phase = "done"

    next_phase_state = "completed" if result_phase == "done" else "pending"
    if result_phase == "done":
        extra["mc_phase_completed_at"] = to_iso()
    elif result_phase != current_phase:
        extra["mc_phase_started_at"] = to_iso()
        extra["mc_phase_completed_at"] = None

    current_delivery = normalize_delivery_state(fields.get("mc_delivery_state"), default="queued")
    if result_status == "done":
        extra["mc_delivery_state"] = "done"
    elif result_status == "review":
        extra["mc_delivery_state"] = "review"
    elif result_status == "in_progress":
        extra["mc_delivery_state"] = "in_progress"
    else:
        extra["mc_delivery_state"] = current_delivery or "queued"

    proof_ref = ""
    if artifacts:
        proof_ref = artifacts[-1]
    else:
        proof_ref = str(
            extra.get("mc_validation_artifact")
            or extra.get("mc_test_report_artifact")
            or fields.get("mc_validation_artifact")
            or fields.get("mc_test_report_artifact")
            or fields.get("mc_plan_artifact")
            or fields.get("mc_proof_ref")
            or ""
        ).strip()
    if proof_ref and result_status == "done":
        extra["mc_proof_ref"] = proof_ref

    result = build_phase_update(
        {"status": result_status, "custom_field_values": fields},
        result_phase,
        status=result_status,
        phase_state=next_phase_state,
        extra_fields=extra,
    )
    return result


def load_metrics(metrics_path: str | Path) -> dict[str, Any]:
    path = Path(metrics_path)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "schema_version": 2,
        "last_updated": to_iso(),
        "counters_today": {
            "heartbeat_runs": 0,
            "tasks_dispatched": 0,
            "review_claims": 0,
            "actionable_review_claims": 0,
            "ignored_governance_reviews": 0,
            "governance_repair_actions": 0,
            "queue_items_written": 0,
            "queue_items_deduped": 0,
            "queue_items_completed": 0,
            "queue_items_invalid_completed": 0,
            "duplicate_dispatch_attempts": 0,
            "qa_reviews_dispatched": 0,
            "judge_wakeups": 0
        },
        "cron_health": {},
        "phase_transitions": {}
    }


def save_metrics(metrics_path: str | Path, metrics: dict[str, Any]) -> None:
    path = Path(metrics_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics["last_updated"] = to_iso()
    path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def metrics_increment(metrics: dict[str, Any], key: str, amount: int = 1) -> None:
    counters = metrics.setdefault("counters_today", {})
    counters[key] = int(counters.get(key, 0) or 0) + amount


def metrics_record_cron(metrics: dict[str, Any], name: str, status: str) -> None:
    cron_health = metrics.setdefault("cron_health", {})
    cron_health[name] = {"status": status, "last_run": to_iso()}


def metrics_record_phase_transition(metrics: dict[str, Any], task_id: str, phase: str) -> None:
    transitions = metrics.setdefault("phase_transitions", {})
    transitions[f"{task_id}:{phase}"] = to_iso()
