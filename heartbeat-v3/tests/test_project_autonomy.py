import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith("/tests"):
    _scripts_dir = str(Path(_scripts_dir).parent / "scripts")
sys.path.insert(0, _scripts_dir)

from project_autonomy import choose_next_dispatch_task, detect_blocked_autonomy_execution, plan_project_autonomy


def _task(task_id: str, title: str, status: str = "inbox", **fields):
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": fields.pop("priority", "medium"),
        "assigned_agent_id": fields.pop("assigned_agent_id", None),
        "created_at": fields.pop("created_at", f"2026-03-05T00:00:00Z"),
        "updated_at": fields.pop("updated_at", None),
        "custom_field_values": fields,
    }


def test_plan_materializes_seed_leaf_task_before_promotion():
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task(
        "milestone-1",
        "Milestone",
        mc_card_type="milestone",
        mc_project_id="project-1",
        mc_chairman_state="active",
    )
    workstream = _task(
        "workstream-1",
        "Research",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
        assigned_agent_id="luan",
        mc_task_seed_spec=[
            {
                "key": "seed-1",
                "title": "Draft hypothesis",
                "description": "Prepare research memo.",
                "assignee": "luan",
                "acceptance_criteria": "Memo produced",
                "qa_checks": "pytest -q",
                "expected_artifacts": "artifacts/research.md",
            }
        ],
    )

    plan = plan_project_autonomy(
        [project, milestone, workstream],
        max_concurrent_in_progress=3,
    )

    assert plan["actions"]
    action = plan["actions"][0]
    assert action["type"] == "create_leaf_task"
    assert action["fields"]["mc_generation_key"] == "seed-1"
    assert action["fields"]["mc_dispatch_policy"] == "backlog"


def test_plan_promotes_backlog_leaf_task_when_window_has_capacity():
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task(
        "milestone-1",
        "Milestone",
        mc_card_type="milestone",
        mc_project_id="project-1",
        mc_chairman_state="active",
    )
    workstream = _task(
        "workstream-1",
        "Execution",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
    )
    leaf = _task(
        "leaf-1",
        "Run backtest",
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="backlog",
        mc_acceptance_criteria="Backtest report",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/backtest.md",
        assigned_agent_id="luan",
    )

    plan = plan_project_autonomy(
        [project, milestone, workstream, leaf],
        max_concurrent_in_progress=3,
    )

    assert plan["actions"]
    action = plan["actions"][0]
    assert action["type"] == "promote_leaf_task"
    assert action["task_id"] == "leaf-1"
    assert action["fields"]["mc_dispatch_policy"] == "auto"


def test_plan_does_not_promote_leaf_task_gated_by_repair_bundle():
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task("milestone-1", "Milestone", mc_card_type="milestone", mc_project_id="project-1", mc_chairman_state="active")
    workstream = _task(
        "workstream-1",
        "Execution",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
    )
    gated_leaf = _task(
        "leaf-1",
        "Retry execution",
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="backlog",
        mc_gate_reason="repair_open",
        mc_acceptance_criteria="Ready",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output.md",
        assigned_agent_id="luan",
    )

    plan = plan_project_autonomy(
        [project, milestone, workstream, gated_leaf],
        max_concurrent_in_progress=3,
    )

    assert not any(action["type"] == "promote_leaf_task" for action in plan["actions"])


def test_recurring_seed_recreates_only_after_cadence_window():
    now = datetime.now(timezone.utc)
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task("milestone-1", "Milestone", mc_card_type="milestone", mc_project_id="project-1", mc_chairman_state="active")
    workstream = _task(
        "workstream-1",
        "Analytics",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
        mc_task_seed_spec=[
            {
                "key": "daily-scorecard",
                "title": "Run daily scorecard",
                "acceptance_criteria": "scorecard updated",
                "qa_checks": "bash scripts/run.sh",
                "expected_artifacts": "artifacts/scorecard.json",
                "cadence_hours": 24,
            }
        ],
    )
    fresh_done = _task(
        "leaf-1",
        "Run daily scorecard",
        status="done",
        updated_at=(now - timedelta(hours=12)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_generation_key="daily-scorecard",
    )
    stale_done = _task(
        "leaf-2",
        "Run daily scorecard",
        status="done",
        updated_at=(now - timedelta(hours=48)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_generation_key="daily-scorecard",
    )

    fresh_plan = plan_project_autonomy([project, milestone, workstream, fresh_done], max_concurrent_in_progress=3)
    assert not any(action["type"] == "create_leaf_task" for action in fresh_plan["actions"])

    stale_plan = plan_project_autonomy([project, milestone, workstream, stale_done], max_concurrent_in_progress=3)
    assert any(action["type"] == "create_leaf_task" for action in stale_plan["actions"])


def test_recurring_seed_accepts_naive_updated_at_timestamps():
    now = datetime.now(timezone.utc)
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task("milestone-1", "Milestone", mc_card_type="milestone", mc_project_id="project-1", mc_chairman_state="active")
    workstream = _task(
        "workstream-1",
        "Analytics",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
        mc_task_seed_spec=[
            {
                "key": "daily-scorecard",
                "title": "Run daily scorecard",
                "acceptance_criteria": "scorecard updated",
                "qa_checks": "bash scripts/run.sh",
                "expected_artifacts": "artifacts/scorecard.json",
                "cadence_hours": 24,
            }
        ],
    )
    stale_done = _task(
        "leaf-1",
        "Run daily scorecard",
        status="done",
        updated_at=(now - timedelta(hours=48)).replace(tzinfo=None, microsecond=0).isoformat(),
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_generation_key="daily-scorecard",
    )

    plan = plan_project_autonomy([project, milestone, workstream, stale_done], max_concurrent_in_progress=3)

    assert any(action["type"] == "create_leaf_task" for action in plan["actions"])


def test_choose_next_dispatch_prefers_project_until_floor_is_met():
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task(
        "milestone-1",
        "Milestone",
        mc_card_type="milestone",
        mc_project_id="project-1",
        mc_chairman_state="active",
    )
    workstream = _task(
        "workstream-1",
        "Execution",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
    )
    ambient = _task("ambient-1", "Ambient", mc_card_type="leaf_task", mc_lane="ambient", mc_dispatch_policy="auto")
    project_leaf = _task(
        "leaf-1",
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

    chosen = choose_next_dispatch_task(
        [ambient, project_leaf],
        [project, milestone, workstream, ambient, project_leaf],
        max_concurrent_in_progress=3,
    )

    assert chosen["id"] == "leaf-1"


def test_choose_next_dispatch_prefers_ambient_once_project_floor_is_satisfied():
    project = _task("project-1", "Project", mc_card_type="project", mc_chairman_state="active")
    milestone = _task(
        "milestone-1",
        "Milestone",
        mc_card_type="milestone",
        mc_project_id="project-1",
        mc_chairman_state="active",
    )
    workstream = _task(
        "workstream-1",
        "Execution",
        mc_card_type="workstream",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_chairman_state="active",
    )
    ambient = _task("ambient-1", "Ambient", mc_card_type="leaf_task", mc_lane="ambient", mc_dispatch_policy="auto")
    project_in_window = _task(
        "leaf-0",
        "Existing project leaf",
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
    project_candidate = _task(
        "leaf-1",
        "Project leaf",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="auto",
        mc_acceptance_criteria="Ready",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output-2.md",
        assigned_agent_id="luan",
    )

    chosen = choose_next_dispatch_task(
        [ambient, project_candidate],
        [project, milestone, workstream, ambient, project_in_window, project_candidate],
        max_concurrent_in_progress=3,
    )

    assert chosen["id"] == "ambient-1"


def test_detect_blocked_autonomy_execution_flags_main_session_without_progress(tmp_path):
    task = _task(
        "leaf-1",
        "Blocked leaf",
        status="in_progress",
        updated_at="2026-03-06T00:00:00Z",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_session_key="agent:main:main",
        mc_expected_artifacts="artifacts/output.md",
    )
    sessions = {
        "agent:main:main": {
            "key": "agent:main:main",
            "status": "active",
            "updatedAt": 0,
        }
    }

    blocked = detect_blocked_autonomy_execution(
        [task],
        sessions,
        workspace_root=tmp_path,
        now=datetime(2026, 3, 6, 1, 0, tzinfo=timezone.utc),
        stall_minutes=30,
    )

    assert len(blocked) == 1
    assert blocked[0]["task_id"] == "leaf-1"
    assert blocked[0]["reason"] == "main_session_timeout"


def test_detect_blocked_autonomy_execution_ignores_recent_artifact_progress(tmp_path):
    artifact = tmp_path / "artifacts" / "output.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("updated", encoding="utf-8")
    artifact_mtime = datetime(2026, 3, 6, 0, 45, tzinfo=timezone.utc).timestamp()
    os.utime(artifact, (artifact_mtime, artifact_mtime))

    task = _task(
        "leaf-1",
        "Blocked leaf",
        status="in_progress",
        updated_at="2026-03-06T00:00:00Z",
        mc_card_type="leaf_task",
        mc_lane="project",
        mc_session_key="agent:main:main",
        mc_expected_artifacts="artifacts/output.md",
    )
    sessions = {
        "agent:main:main": {
            "key": "agent:main:main",
            "status": "active",
            "updatedAt": 0,
        }
    }

    blocked = detect_blocked_autonomy_execution(
        [task],
        sessions,
        workspace_root=tmp_path,
        now=datetime(2026, 3, 6, 1, 0, tzinfo=timezone.utc),
        stall_minutes=30,
    )

    assert blocked == []
