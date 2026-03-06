import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith("/tests"):
    _scripts_dir = str(Path(_scripts_dir).parent / "scripts")
sys.path.insert(0, _scripts_dir)

from project_autonomy import choose_next_dispatch_task, plan_project_autonomy


def _task(task_id: str, title: str, status: str = "inbox", **fields):
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": fields.pop("priority", "medium"),
        "assigned_agent_id": fields.pop("assigned_agent_id", None),
        "created_at": fields.pop("created_at", f"2026-03-05T00:00:00Z"),
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
