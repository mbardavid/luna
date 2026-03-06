import sys
import json
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith("/tests"):
    _scripts_dir = str(Path(_scripts_dir).parent / "scripts")
sys.path.insert(0, _scripts_dir)

from autonomy_board_packet import render_board_packet


def _task(task_id: str, title: str, status: str = "inbox", **fields):
    return {
        "id": task_id,
        "title": title,
        "status": status,
        "priority": fields.pop("priority", "medium"),
        "assigned_agent_id": fields.pop("assigned_agent_id", None),
        "custom_field_values": fields,
    }


def test_render_board_packet_summarizes_active_project() -> None:
    project = _task("project-1", "Autonomy", mc_card_type="project", mc_chairman_state="active")
    milestone = _task(
        "milestone-1",
        "Milestone Alpha",
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
        "in_progress",
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_dispatch_policy="auto",
        mc_risk_profile="high",
    )
    steering = _task(
        "leaf-2",
        "Chairman gate",
        "awaiting_human",
        mc_card_type="leaf_task",
        mc_project_id="project-1",
        mc_milestone_id="milestone-1",
        mc_workstream_id="workstream-1",
        mc_chairman_state="steering",
    )

    packet = render_board_packet([project, milestone, workstream, leaf, steering])

    assert "Autonomy Board Packet" in packet
    assert "Milestone Alpha" in packet
    assert "Execution" in packet
    assert "Run backtest" in packet
    assert "Chairman gate" in packet
    assert "Review 1 high-risk task(s)." in packet



def test_render_board_packet_includes_outcome_snapshot(tmp_path: Path) -> None:
    scorecard_path = tmp_path / "scorecard.json"
    scorecard_path.write_text(
        json.dumps(
            {
                "account": {"handle": "@luna"},
                "session_state": "ok",
                "followers_baseline": 100,
                "followers_current": 113,
                "net_followers_delta": 13,
                "recent_themes": ["crypto", "markets"],
                "guardrail_flags": [],
                "suggested_action": "continue",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    project = _task(
        "project-1",
        "Luna Growth",
        mc_card_type="project",
        mc_chairman_state="active",
        mc_outcome_ref=str(scorecard_path),
    )
    milestone = _task("milestone-1", "M0", mc_card_type="milestone", mc_project_id="project-1", mc_chairman_state="active")
    workstream = _task("workstream-1", "WS", mc_card_type="workstream", mc_project_id="project-1", mc_milestone_id="milestone-1", mc_chairman_state="active")

    packet = render_board_packet([project, milestone, workstream])

    assert "Outcome Snapshot" in packet
    assert "@luna" in packet
    assert "delta=13" in packet
    assert "Suggested action: `continue`" in packet
