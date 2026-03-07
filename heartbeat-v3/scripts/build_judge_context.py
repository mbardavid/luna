#!/usr/bin/env python3
"""Build hydrated review context for luna-judge."""

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

from mc_control import (
    is_actionable_review_task,
    task_acceptance_criteria,
    task_card_type,
    task_expected_artifacts,
    task_fields,
    task_gate_reason,
    task_milestone_id,
    task_phase,
    task_project_id,
    task_proof_ref,
    task_qa_checks,
    task_repair_bundle_id,
    task_repair_fingerprint,
    task_repair_reason,
    task_repair_source_task_id,
    task_status,
    task_workstream_id,
)

MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
JUDGE_WORKSPACE = Path("/home/openclaw/.openclaw/workspace-luna-judge")
SOURCE_ROOT = Path("/home/openclaw/.openclaw/workspace")
SYNC_STATE = JUDGE_WORKSPACE / "state" / "judge-context-sync.json"
METRICS_FILE = WORKSPACE / "state" / "control-loop-metrics.json"


def run(cmd: list[str], timeout: int = 30) -> str:
    cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "command failed")
    return cp.stdout.strip()


def mc_list_tasks() -> list[dict]:
    raw = run([str(MC_CLIENT), "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return data if isinstance(data, list) else []


def mc_get_task(task_id: str) -> dict:
    try:
        raw = run([str(MC_CLIENT), "get-task", task_id])
        data = json.loads(raw or "{}")
        if isinstance(data, dict) and data:
            return data
    except Exception:
        pass
    for task in mc_list_tasks():
        if str(task.get("id") or "") == task_id:
            return task
    return {}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def related_tasks(tasks: list[dict], target: dict) -> dict[str, dict | list[dict] | None]:
    project_id = task_project_id(target)
    milestone_id = task_milestone_id(target)
    workstream_id = task_workstream_id(target)
    repair_bundle_id = task_repair_bundle_id(target)
    repair_source_task_id = task_repair_source_task_id(target)

    project = next((t for t in tasks if str(t.get("id")) == project_id), None)
    milestone = next((t for t in tasks if str(t.get("id")) == milestone_id), None)
    workstream = next((t for t in tasks if str(t.get("id")) == workstream_id), None)
    repair_bundle = next((t for t in tasks if str(t.get("id")) == repair_bundle_id), None)
    if repair_bundle is None and repair_source_task_id:
        repair_bundle = next(
            (
                t for t in tasks
                if task_card_type(t) == "repair_bundle"
                and task_repair_source_task_id(t) == repair_source_task_id
                and task_status(t) not in {"done", "failed"}
            ),
            None,
        )
    source_task = None
    if repair_source_task_id:
        source_task = next((t for t in tasks if str(t.get("id")) == repair_source_task_id), None)

    siblings = [
        t for t in tasks
        if task_project_id(t) == project_id
        and task_milestone_id(t) == milestone_id
        and task_workstream_id(t) == workstream_id
        and str(t.get("id")) != str(target.get("id"))
    ]
    siblings.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return {
        "project": project,
        "milestone": milestone,
        "workstream": workstream,
        "repair_bundle": repair_bundle,
        "source_task": source_task,
        "siblings": siblings[:10],
    }


def artifact_paths(task: dict) -> list[str]:
    fields = task_fields(task)
    raw_paths = []
    for value in (
        task_proof_ref(task),
        task_expected_artifacts(task),
        fields.get("mc_plan_artifact"),
        fields.get("mc_validation_artifact"),
        fields.get("mc_test_report_artifact"),
    ):
        text = str(value or "").strip()
        if not text:
            continue
        raw_paths.extend([line.strip() for line in text.splitlines() if line.strip()])

    paths = []
    seen: set[str] = set()
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = SOURCE_ROOT / raw
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        paths.append(key)
    return paths


def format_task(task: dict | None) -> str:
    if not task:
        return "- (none)"
    return "\n".join(
        [
            f"- id: {task.get('id')}",
            f"- title: {task.get('title')}",
            f"- status: {task_status(task)}",
            f"- card_type: {task_card_type(task)}",
            f"- phase: {task_phase(task)}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    all_tasks = mc_list_tasks()
    task = mc_get_task(args.task_id)
    if not task:
        raise SystemExit(f"task not found: {args.task_id}")

    rel = related_tasks(all_tasks, task)
    open_repairs = [
        t for t in all_tasks
        if task_card_type(t) == "repair_bundle" and task_status(t) not in {"done", "failed"}
    ]
    actionable_reviews = [t for t in all_tasks if is_actionable_review_task(t)]
    metrics = load_json(METRICS_FILE)
    sync_state = load_json(SYNC_STATE)

    output_path = JUDGE_WORKSPACE / "artifacts" / "judge-context" / f"{str(task.get('id'))[:8]}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Judge Context — {task.get('title')}",
        "",
        f"- Generated at: {datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
        f"- Task ID: {task.get('id')}",
        f"- Card type: {task_card_type(task)}",
        f"- Status: {task_status(task)}",
        f"- Phase: {task_phase(task)}",
        f"- Gate reason: {task_gate_reason(task) or '(none)'}",
        f"- Repair fingerprint: {task_repair_fingerprint(task) or '(none)'}",
        "",
        "## Current Card",
        json.dumps(task, ensure_ascii=False, indent=2),
        "",
        "## Lineage",
        "### Project",
        format_task(rel["project"]),
        "",
        "### Milestone",
        format_task(rel["milestone"]),
        "",
        "### Workstream",
        format_task(rel["workstream"]),
        "",
        "### Source Task",
        format_task(rel["source_task"]),
        "",
        "### Repair Bundle",
        format_task(rel["repair_bundle"]),
        "",
        "## Board State",
        f"- actionable_reviews_open: {len(actionable_reviews)}",
        f"- open_repairs: {len(open_repairs)}",
        f"- sibling_tasks: {len(rel['siblings'])}",
        "",
        "## Acceptance",
        task_acceptance_criteria(task) or "(none)",
        "",
        "## QA Checks",
        task_qa_checks(task) or "(none)",
        "",
        "## Runtime / Repair Notes",
        task_repair_reason(task) or "(none)",
        "",
        "## Artifact Paths",
    ]
    for path in artifact_paths(task):
        lines.append(f"- {path}")

    lines.extend(
        [
            "",
            "## Recent Comments",
        ]
    )
    comments = task.get("comments") or []
    if comments:
        for item in comments[-8:]:
            body = str(item.get("message", item.get("body", "")) or "").strip()
            if not body:
                continue
            lines.append(f"- {body[:1200]}")
    else:
        lines.append("- (none)")

    lines.extend(
        [
            "",
            "## Judge Runtime",
            f"- source_repo: {SOURCE_ROOT}",
            f"- imports_sync_state: {SYNC_STATE}",
            f"- imports_synced_at: {sync_state.get('synced_at', '(unknown)')}",
            "",
            "## Health Snapshot",
            json.dumps(
                {
                    "judge_dispatch_luna_judge": metrics.get("judge_dispatch_luna_judge", 0),
                    "judge_dispatch_main_legacy": metrics.get("judge_dispatch_main_legacy", 0),
                    "repair_bundle_opened": metrics.get("repair_bundle_opened", 0),
                    "repair_bundle_reused": metrics.get("repair_bundle_reused", 0),
                    "repair_bundle_resolved": metrics.get("repair_bundle_resolved", 0),
                    "repair_bundle_failed": metrics.get("repair_bundle_failed", 0),
                },
                ensure_ascii=False,
                indent=2,
            ),
            "",
            "## Related Tasks",
        ]
    )
    for item in rel["siblings"]:
        lines.append(
            f"- {str(item.get('id'))[:8]} | {task_card_type(item)} | {task_status(item)} | {item.get('title')}"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps({"task_id": args.task_id, "path": str(output_path)}, ensure_ascii=False, indent=2))
    else:
        print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
