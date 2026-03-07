from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(WORKSPACE / "heartbeat-v3" / "scripts"))

from validate_autonomy_architecture import evaluate_autonomy_architecture


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


def _artifacts(tmp_path: Path) -> dict[str, Path]:
    board_packet = tmp_path / "autonomy-board-packet-latest.md"
    board_packet.write_text("# packet\n", encoding="utf-8")
    outcome = tmp_path / "scorecard-latest.json"
    outcome.write_text(json.dumps({"session_state": "ok"}), encoding="utf-8")
    session = tmp_path / "session-health-latest.json"
    session.write_text(json.dumps({"session_state": "ok"}), encoding="utf-8")
    baseline = tmp_path / "baseline-latest.json"
    baseline.write_text(json.dumps({"followers": 0}), encoding="utf-8")
    local_packet = tmp_path / "board-packet-latest.md"
    local_packet.write_text("# canary packet\n", encoding="utf-8")
    return {
        "board_packet": board_packet,
        "outcome": outcome,
        "session-health-latest.json": session,
        "baseline-latest.json": baseline,
        "board-packet-latest.md": local_packet,
    }


def _scheduler_snapshot(**overrides):
    base = {
        "last_tick": "2026-03-06T00:05:00Z",
        "mode": "full",
        "health_state": "healthy",
        "slots_total": 4,
        "eligible_by_lane": {"repair": 0, "review": 0, "project": 0, "ambient": 0},
        "running_by_lane": {"repair": 0, "review": 0, "project": 0, "ambient": 0},
        "reserved_slots": {"repair": 0, "review": 0, "project": 0, "ambient": 0},
        "dispatch_decision": {"type": "idle", "lane": "", "task_id": "", "status": ""},
    }
    base.update(overrides)
    return base


def _runtime(project_id: str = "project-1", milestone_id: str = "milestone-1", workstreams: list[str] | None = None):
    return {
        "project_id": project_id,
        "milestone_id": milestone_id,
        "workstream_ids": workstreams or ["ws-1"],
    }


def _base_tasks():
    return [
        _task("project-1", "Project", status="in_progress", mc_card_type="project", mc_chairman_state="active", mc_review_agent="luna-judge"),
        _task("milestone-1", "Milestone", status="in_progress", mc_card_type="milestone", mc_project_id="project-1", mc_chairman_state="active"),
        _task("ws-1", "WS1", status="in_progress", mc_card_type="workstream", mc_project_id="project-1", mc_milestone_id="milestone-1", mc_chairman_state="active"),
    ]


def _check(report: dict, check_id: str) -> dict:
    return next(item for item in report["checks"] if item["id"] == check_id)


def test_fails_when_governance_card_returns_to_review(tmp_path):
    tasks = _base_tasks()
    tasks[0]["status"] = "review"
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=_scheduler_snapshot(),
        metrics={"counters_today": {"judge_dispatch_main_legacy": 0}},
        autonomy_runtime=_runtime(),
        artifact_paths=_artifacts(tmp_path),
        pytest_result={"passed": True, "summary": "ok"},
        max_state_age_minutes=999999,
    )
    assert report["overall_status"] == "FAIL"
    assert _check(report, "governance_not_in_review")["status"] == "FAIL"


def test_fails_when_auto_execution_is_linked_to_main(tmp_path):
    tasks = _base_tasks() + [
        _task(
            "leaf-1",
            "Leaf",
            status="in_progress",
            mc_card_type="leaf_task",
            mc_lane="project",
            mc_project_id="project-1",
            mc_milestone_id="milestone-1",
            mc_workstream_id="ws-1",
            mc_dispatch_policy="auto",
            mc_session_key="agent:main:main",
        )
    ]
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=_scheduler_snapshot(),
        metrics={"counters_today": {"judge_dispatch_main_legacy": 0}},
        autonomy_runtime=_runtime(),
        artifact_paths=_artifacts(tmp_path),
        pytest_result={"passed": True, "summary": "ok"},
        max_state_age_minutes=999999,
    )
    assert report["overall_status"] == "FAIL"
    assert _check(report, "auto_dispatch_not_on_main")["status"] == "FAIL"


def test_repair_lane_passes_when_running_repair_has_capacity(tmp_path):
    tasks = _base_tasks() + [
        _task(
            "repair-bundle-1",
            "Repair bundle",
            status="in_progress",
            mc_card_type="repair_bundle",
            mc_project_id="project-1",
            mc_repair_state="open",
        ),
        _task(
            "diag-1",
            "Diagnose",
            status="in_progress",
            mc_card_type="leaf_task",
            mc_project_id="project-1",
            mc_milestone_id="milestone-1",
            mc_workstream_id="ws-1",
            mc_lane="repair",
            mc_dispatch_policy="auto",
            mc_repair_bundle_id="repair-bundle-1",
            mc_session_key="agent:cto-ops:main",
        ),
        _task(
            "repair-1",
            "Repair",
            status="inbox",
            mc_card_type="leaf_task",
            mc_lane="repair",
            mc_dispatch_policy="backlog",
            mc_repair_bundle_id="repair-bundle-1",
        ),
        _task(
            "validate-1",
            "Validate",
            status="inbox",
            mc_card_type="review_bundle",
            mc_lane="review",
            mc_repair_bundle_id="repair-bundle-1",
        ),
        _task(
            "source-1",
            "Source task",
            status="inbox",
            mc_card_type="leaf_task",
            mc_lane="project",
            mc_gate_reason="repair_open",
            mc_repair_bundle_id="repair-bundle-1",
        ),
    ]
    scheduler = _scheduler_snapshot(
        eligible_by_lane={"repair": 1, "review": 0, "project": 0, "ambient": 0},
        running_by_lane={"repair": 1, "review": 0, "project": 0, "ambient": 1},
        reserved_slots={"repair": 1, "review": 0, "project": 0, "ambient": 1},
        dispatch_decision={"type": "idle", "lane": "", "task_id": "", "status": ""},
    )
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=scheduler,
        metrics={"counters_today": {"judge_dispatch_main_legacy": 0}},
        autonomy_runtime=_runtime(),
        artifact_paths=_artifacts(tmp_path),
        pytest_result={"passed": True, "summary": "ok"},
        max_state_age_minutes=999999,
    )
    assert _check(report, "repair_lane_served")["status"] == "PASS"
    assert _check(report, "repair_bundles_integrity")["status"] == "PASS"
    assert _check(report, "repair_gates_valid")["status"] == "PASS"


def test_fails_when_repair_gated_task_loses_bundle(tmp_path):
    tasks = _base_tasks() + [
        _task(
            "source-1",
            "Source task",
            status="inbox",
            mc_card_type="leaf_task",
            mc_lane="project",
            mc_gate_reason="repair_open",
            mc_repair_bundle_id="missing-bundle",
        )
    ]
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=_scheduler_snapshot(),
        metrics={"counters_today": {"judge_dispatch_main_legacy": 0}},
        autonomy_runtime=_runtime(),
        artifact_paths=_artifacts(tmp_path),
        pytest_result={"passed": True, "summary": "ok"},
        max_state_age_minutes=999999,
    )
    assert report["overall_status"] == "FAIL"
    assert _check(report, "repair_gates_valid")["status"] == "FAIL"


def test_scheduler_stale_only_warns_when_heartbeat_is_fresh(tmp_path):
    tasks = _base_tasks()
    now = datetime.now(timezone.utc)
    scheduler = _scheduler_snapshot(last_tick=(now - timedelta(minutes=40)).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    heartbeat_last_run = (now - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    metrics = {
        "last_updated": heartbeat_last_run,
        "counters_today": {"judge_dispatch_main_legacy": 0},
        "cron_health": {"heartbeat-v3": {"status": "running", "last_run": heartbeat_last_run}},
    }
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=scheduler,
        metrics=metrics,
        autonomy_runtime=_runtime(),
        artifact_paths=_artifacts(tmp_path),
        pytest_result={"passed": True, "summary": "ok"},
        max_state_age_minutes=20,
    )

    assert _check(report, "scheduler_state_fresh")["status"] == "WARN"
