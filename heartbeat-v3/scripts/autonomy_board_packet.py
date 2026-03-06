#!/usr/bin/env python3
"""Generate a board packet for the active autonomous project."""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mc_control import task_card_type, task_chairman_state, task_dispatch_policy, task_fields, task_project_id, task_status, task_workstream_id
from project_autonomy import select_active_milestone, select_active_project, select_active_workstreams

WORKSPACE = Path(__file__).resolve().parent.parent.parent
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
DEFAULT_OUTPUT = WORKSPACE / "artifacts" / "reports" / "autonomy-board-packet-latest.md"


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("id") or "").strip()


def _task_title(task: dict[str, Any]) -> str:
    return str(task.get("title") or "(untitled)").strip()


def _load_json_artifact(path_value: str | None) -> dict[str, Any] | None:
    if not path_value:
        return None
    path = Path(str(path_value).strip())
    if not path.is_absolute():
        path = WORKSPACE / path
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _render_outcome_snapshot(project: dict[str, Any]) -> list[str]:
    outcome_ref = str(task_fields(project).get("mc_outcome_ref") or "").strip()
    payload = _load_json_artifact(outcome_ref)
    if not payload:
        return ["- No external outcome snapshot available yet."]

    account = payload.get("account") or {}
    handle = str(account.get("handle") or "(unknown)").strip()
    session_state = str(payload.get("session_state") or "unknown").strip()
    followers_current = payload.get("followers_current")
    followers_baseline = payload.get("followers_baseline")
    delta = payload.get("net_followers_delta")
    suggested_action = str(payload.get("suggested_action") or "unknown").strip()
    recent_themes = payload.get("recent_themes") or []
    guardrail_flags = payload.get("guardrail_flags") or []
    lines = [
        f"- Account: `{handle}` | session=`{session_state}` | followers={followers_current} | baseline={followers_baseline} | delta={delta}",
        f"- Suggested action: `{suggested_action}`",
        f"- Recent themes: {', '.join(str(item) for item in recent_themes[:5]) or '(none)'}",
    ]
    lines.extend([f"- Guardrail flag: `{item}`" for item in guardrail_flags[:5]] or ["- Guardrail flag: none"])
    return lines


def load_tasks() -> list[dict[str, Any]]:
    raw = subprocess.run([str(MC_CLIENT), "list-tasks"], capture_output=True, text=True, check=True).stdout
    payload = json.loads(raw or "{}")
    if isinstance(payload, dict):
        return payload.get("items", [])
    return payload if isinstance(payload, list) else []


def _status_counts(tasks: list[dict[str, Any]]) -> Counter:
    return Counter(task_status(task) for task in tasks)


def render_board_packet(tasks: list[dict[str, Any]]) -> str:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    project = select_active_project(tasks)
    if not project:
        return "\n".join([
            "# Autonomy Board Packet",
            f"Generated: {generated_at}",
            "",
            "No active autonomous project.",
        ]) + "\n"

    milestone = select_active_milestone(tasks, project)
    workstreams = select_active_workstreams(tasks, project, milestone, max_active_workstreams=10) if milestone else []
    project_id = _task_id(project)
    milestone_id = _task_id(milestone) if milestone else ""
    workstream_ids = {_task_id(task) for task in workstreams}

    project_leaf_tasks = [
        task for task in tasks
        if task_card_type(task) == "leaf_task"
        and task_project_id(task) == project_id
        and (not milestone_id or task_fields(task).get("mc_milestone_id") == milestone_id)
    ]
    workstream_lines = []
    for workstream in workstreams:
        workstream_leaf_tasks = [task for task in project_leaf_tasks if task_workstream_id(task) == _task_id(workstream)]
        counts = _status_counts(workstream_leaf_tasks)
        workstream_lines.append(
            f"- **{_task_title(workstream)}** `{_task_id(workstream)[:8]}` | "
            f"leaf={len(workstream_leaf_tasks)} auto={sum(1 for task in workstream_leaf_tasks if task_dispatch_policy(task) == 'auto')} "
            f"in_progress={counts.get('in_progress', 0)} review={counts.get('review', 0)} done={counts.get('done', 0)}"
        )

    risk_tasks = [
        task for task in project_leaf_tasks
        if str(task_fields(task).get("mc_risk_profile") or "").strip().lower() in {"high", "critical"}
        and task_status(task) not in {"done", "failed"}
    ]
    steering_tasks = [
        task for task in tasks
        if task_project_id(task) == project_id
        and (
            task_chairman_state(task) == "steering"
            or task_status(task) == "awaiting_human"
        )
    ]
    counts = _status_counts(project_leaf_tasks)
    suggestions: list[str] = []
    if not milestone:
        suggestions.append("Select or approve the next milestone.")
    outcome_payload = _load_json_artifact(str(task_fields(project).get("mc_outcome_ref") or "").strip())
    if outcome_payload and str(outcome_payload.get("session_state") or "") not in {"", "ok"}:
        suggestions.append("Recover the Luna X automation session before public actions.")
    if outcome_payload and str(outcome_payload.get("suggested_action") or "") in {"adjust", "pause", "steering"}:
        suggestions.append(f"Apply scorecard recommendation: {outcome_payload.get('suggested_action')}.")
    if milestone and not workstreams:
        suggestions.append("Activate at least one workstream under the current milestone.")
    if workstreams and not project_leaf_tasks:
        suggestions.append("Seed or create leaf tasks for the active workstreams.")
    if risk_tasks:
        suggestions.append(f"Review {len(risk_tasks)} high-risk task(s).")
    if steering_tasks:
        suggestions.append(f"Resolve steering on {len(steering_tasks)} card(s).")
    if not suggestions:
        suggestions.append("Continue execution; no board intervention is currently required.")

    packet_lines = [
        "# Autonomy Board Packet",
        f"Generated: {generated_at}",
        "",
        "## Active Project",
        f"- Project: **{_task_title(project)}** `{project_id[:8]}`",
        f"- Chairman state: `{task_chairman_state(project)}`",
        f"- Active milestone: **{_task_title(milestone)}** `{milestone_id[:8]}`" if milestone else "- Active milestone: `(none)`",
        "",
        "## Delivery Snapshot",
        f"- Leaf tasks: {len(project_leaf_tasks)} total | inbox={counts.get('inbox', 0)} in_progress={counts.get('in_progress', 0)} review={counts.get('review', 0)} done={counts.get('done', 0)} blocked={counts.get('blocked', 0)} stalled={counts.get('stalled', 0)} retry={counts.get('retry', 0)}",
        f"- Workstreams active: {len(workstreams)}",
        "",
        "## Workstreams",
        *(workstream_lines or ["- No active workstreams."]),
        "",
        "## Risks",
        *(
            [f"- **{_task_title(task)}** `{_task_id(task)[:8]}` | risk={task_fields(task).get('mc_risk_profile', 'unknown')} | status={task_status(task)}" for task in risk_tasks]
            or ["- No open high-risk tasks."]
        ),
        "",
        "## Outcome Snapshot",
        *_render_outcome_snapshot(project),
        "",
        "## Steering Queue",
        *(
            [f"- **{_task_title(task)}** `{_task_id(task)[:8]}` | chairman_state={task_chairman_state(task)} | status={task_status(task)}" for task in steering_tasks]
            or ["- No steering items pending."]
        ),
        "",
        "## Suggested Board Decisions",
        *[f"- {item}" for item in suggestions],
    ]
    return "\n".join(packet_lines) + "\n"


def main(argv: list[str]) -> int:
    output_path = DEFAULT_OUTPUT
    if len(argv) > 1:
        output_path = Path(argv[1]).expanduser().resolve()
    tasks = load_tasks()
    packet = render_board_packet(tasks)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(packet, encoding='utf-8')
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
