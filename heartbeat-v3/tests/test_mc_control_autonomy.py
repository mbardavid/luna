import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith("/tests"):
    _scripts_dir = str(Path(_scripts_dir).parent / "scripts")
sys.path.insert(0, _scripts_dir)

from mc_control import (
    extract_session_key_from_agent_result,
    is_executable_leaf_task,
    task_repair_state,
    task_review_agent,
)


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


def test_task_review_agent_defaults_to_luna_judge():
    review_bundle = _task("review-1", "Review", status="review", mc_card_type="review_bundle")
    luna_review = _task(
        "leaf-1",
        "Plan review",
        status="review",
        mc_card_type="leaf_task",
        mc_workflow="dev_loop_v1",
        mc_phase="luna_plan_validation",
        mc_phase_owner="luna",
    )

    assert task_review_agent(review_bundle) == "luna-judge"
    assert task_review_agent(luna_review) == "luna-judge"


def test_is_executable_leaf_task_false_when_repair_gate_is_open():
    task = _task(
        "leaf-1",
        "Execute",
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_gate_reason="repair_open",
        mc_acceptance_criteria="done",
        mc_qa_checks="pytest -q",
        mc_expected_artifacts="artifacts/output.md",
        assigned_agent_id="luan",
    )

    assert is_executable_leaf_task(task) is False


def test_extract_session_key_from_agent_result_prefers_payload_values():
    payload = {
        "result": {
            "sessionKey": "agent:luna-judge:session-123",
            "payloads": [{"text": "DISPATCHED session=agent:luna-judge:session-ignored"}],
        }
    }

    assert extract_session_key_from_agent_result(str(payload).replace("'", '"'), agent="luna-judge") == "agent:luna-judge:session-123"


def test_task_repair_state_is_blank_without_explicit_metadata():
    task = _task("leaf-1", "No repair metadata", mc_card_type="leaf_task")

    assert task_repair_state(task) == ""
    assert task_repair_state(task, default="open") == "open"


def test_extract_session_key_reads_nested_system_prompt_report():
    payload = {
        "result": {
            "meta": {
                "systemPromptReport": {
                    "sessionKey": "agent:luna-judge:main",
                }
            }
        }
    }

    assert extract_session_key_from_agent_result(str(payload).replace("'", '"'), agent="luna-judge") == "agent:luna-judge:main"
