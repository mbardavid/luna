from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(WORKSPACE / "heartbeat-v3" / "scripts"))

from mc_control import task_lane
from scheduler_v2 import build_scheduler_snapshot


def _task(task_id: str, title: str, status: str = "inbox", **fields):
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": fields.pop("priority", "medium"),
        "assigned_agent_id": fields.pop("assigned_agent_id", None),
        "created_at": fields.pop("created_at", "2026-03-06T00:00:00Z"),
        "updated_at": fields.pop("updated_at", None),
        "custom_field_values": fields,
    }


def test_project_lane_is_not_blocked_by_ambient_execution():
    ambient_running = _task(
        "ambient-run",
        "Ambient running",
        status="in_progress",
        mc_card_type="leaf_task",
        mc_lane="ambient",
        mc_dispatch_policy="auto",
    )
    project_leaf = _task(
        "project-leaf",
        "Project leaf",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Ready",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output.md",
        assigned_agent_id="luan",
    )

    snapshot = build_scheduler_snapshot(
        tasks=[ambient_running, project_leaf],
        actionable_reviews=[],
        eligible_dispatch_tasks=[project_leaf],
        resource_level="ok",
        mode="full",
    )

    assert snapshot["dispatch_decision"]["type"] == "dispatch"
    assert snapshot["dispatch_decision"]["lane"] == "project"
    assert snapshot["dispatch_decision"]["task_id"] == "project-leaf"


def test_repair_lane_preempts_project_and_ambient():
    ambient_running = _task(
        "ambient-run",
        "Ambient running",
        status="in_progress",
        mc_card_type="leaf_task",
        mc_lane="ambient",
        mc_dispatch_policy="auto",
    )
    project_leaf = _task(
        "project-leaf",
        "Project leaf",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Ready",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output.md",
        assigned_agent_id="luan",
    )
    repair_leaf = _task(
        "repair-leaf",
        "Diagnose repair",
        mc_card_type="leaf_task",
        mc_lane="repair",
        mc_repair_bundle_id="bundle-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Root cause identified",
        mc_qa_checks="Provide evidence",
        mc_expected_artifacts="artifacts/repairs/bundle-1-diagnose.md",
        assigned_agent_id="cto-ops",
    )

    snapshot = build_scheduler_snapshot(
        tasks=[ambient_running, project_leaf, repair_leaf],
        actionable_reviews=[],
        eligible_dispatch_tasks=[project_leaf, repair_leaf],
        resource_level="ok",
        mode="full",
    )

    assert snapshot["dispatch_decision"]["lane"] == "repair"
    assert snapshot["dispatch_decision"]["task_id"] == "repair-leaf"


def test_claimed_review_consumes_review_reservation_but_not_project_slot():
    claimed_review = _task(
        "review-1",
        "Validate repair",
        status="review",
        mc_card_type="review_bundle",
        mc_claimed_by="luna-judge",
        mc_claim_expires_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
    )
    project_leaf = _task(
        "project-leaf",
        "Project leaf",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Ready",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output.md",
        assigned_agent_id="luan",
    )

    snapshot = build_scheduler_snapshot(
        tasks=[claimed_review, project_leaf],
        actionable_reviews=[],
        eligible_dispatch_tasks=[project_leaf],
        resource_level="ok",
        mode="full",
    )

    assert snapshot["running_by_lane"]["review"] == 1
    assert snapshot["reserved_slots"]["project"] == 1
    assert snapshot["dispatch_decision"]["lane"] == "project"


def test_task_lane_infers_repair_from_repair_bundle_context():
    task = _task(
        "repair-child",
        "Diagnose repair",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_repair_bundle_id="bundle-1",
    )

    assert task_lane(task) == "repair"


def test_repair_gated_queued_execution_does_not_count_as_running_capacity():
    gated_source = _task(
        "ambient-gated",
        "PMM Live Operations",
        status="in_progress",
        mc_card_type="leaf_task",
        mc_lane="ambient",
        mc_dispatch_policy="backlog",
        mc_gate_reason="repair_open",
        mc_delivery_state="queued",
    )
    repair_leaf = _task(
        "repair-leaf",
        "Diagnose repair",
        mc_card_type="leaf_task",
        mc_lane="repair",
        mc_repair_bundle_id="bundle-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Root cause identified",
        mc_qa_checks="Provide evidence",
        mc_expected_artifacts="artifacts/repairs/bundle-1-diagnose.md",
        assigned_agent_id="cto-ops",
    )

    snapshot = build_scheduler_snapshot(
        tasks=[gated_source, repair_leaf],
        actionable_reviews=[],
        eligible_dispatch_tasks=[repair_leaf],
        resource_level="ok",
        mode="full",
    )

    assert snapshot["running_by_lane"]["ambient"] == 0
    assert snapshot["dispatch_decision"]["lane"] == "repair"
    assert snapshot["dispatch_decision"]["task_id"] == "repair-leaf"
