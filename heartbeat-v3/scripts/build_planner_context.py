#!/usr/bin/env python3
"""Build milestone planning context for luna-planner."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mc_control import task_card_type, task_fields, task_milestone_id, task_project_id, task_runtime_owner, task_status

MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
PLANNER_WORKSPACE = Path("/home/openclaw/.openclaw/workspace-luna-planner")
CONTEXT_DIR = PLANNER_WORKSPACE / "artifacts" / "planner-context"
REPORT_ROOT = WORKSPACE / "artifacts" / "reports" / "luna-x-growth"


def run(cmd: list[str], timeout: int = 30) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    return proc.stdout.strip()


def mc_list_tasks() -> list[dict]:
    raw = run([str(MC_CLIENT), "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return data if isinstance(data, list) else []


def load_text(path: Path, limit: int = 12000) -> str:
    if not path.exists():
        return "(missing)"
    return path.read_text(encoding="utf-8")[:limit]


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--milestone-id", required=True)
    parser.add_argument("--observation-id", required=True)
    args = parser.parse_args()

    tasks = mc_list_tasks()
    project = next((task for task in tasks if str(task.get("id") or "") == args.project_id), {})
    milestone = next((task for task in tasks if str(task.get("id") or "") == args.milestone_id), {})
    scoped = [
        task for task in tasks
        if task_runtime_owner(task) == "controller-v1"
        and task_project_id(task) == args.project_id
        and task_milestone_id(task) == args.milestone_id
    ]
    scoped.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)

    packet = load_text(REPORT_ROOT / "board-packet-latest.md")
    scorecard = load_json(REPORT_ROOT / "scorecard-latest.json")
    baseline = load_json(REPORT_ROOT / "baseline-latest.json")
    session_health = load_json(REPORT_ROOT / "session-health-latest.json")

    CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
    path = CONTEXT_DIR / f"{args.observation_id[:24]}.md"
    lines = [
        f"# Planner Context — {milestone.get('title')}",
        "",
        f"- Generated at: {datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
        f"- Project ID: {args.project_id}",
        f"- Milestone ID: {args.milestone_id}",
        f"- Observation ID: {args.observation_id}",
        "",
        "## Project",
        json.dumps(project, ensure_ascii=False, indent=2),
        "",
        "## Milestone",
        json.dumps(milestone, ensure_ascii=False, indent=2),
        "",
        "## Scoped Tasks",
        json.dumps(
            [
                {
                    "id": str(task.get("id") or ""),
                    "title": str(task.get("title") or ""),
                    "status": task_status(task),
                    "card_type": task_card_type(task),
                    "fields": task_fields(task),
                }
                for task in scoped[:40]
            ],
            ensure_ascii=False,
            indent=2,
        ),
        "",
        "## Luna X Board Packet",
        packet,
        "",
        "## Luna X Scorecard",
        json.dumps(scorecard, ensure_ascii=False, indent=2),
        "",
        "## Luna X Baseline",
        json.dumps(baseline, ensure_ascii=False, indent=2),
        "",
        "## Luna X Session Health",
        json.dumps(session_health, ensure_ascii=False, indent=2),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
