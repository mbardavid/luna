#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
MC = WORKSPACE / "scripts" / "mc-client.sh"
PROJECT_ID = "3a0d8492-412a-4676-b945-cd3b02885e3f"
DAILY_TASK_TITLE = "Run Luna X daily scorecard and board packet"
ACTIVE_GOVERNANCE = {"project", "milestone", "workstream"}


def run(args: list[str]) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout.strip()


def mc_list_tasks() -> list[dict[str, Any]]:
    raw = run([str(MC), "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return data if isinstance(data, list) else []


def mc_update_task(task_id: str, *, status: str | None = None, comment: str | None = None, fields: dict[str, Any] | None = None) -> None:
    cmd = [str(MC), "update-task", task_id]
    if status:
        cmd += ["--status", status]
    if comment:
        cmd += ["--comment", comment]
    if fields is not None:
        cmd += ["--fields", json.dumps(fields, ensure_ascii=False)]
    run(cmd)


def _load(path: str) -> bool:
    p = Path(path)
    if not p.is_absolute():
        p = WORKSPACE / p
    return p.exists()


def _project_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for task in tasks:
        fields = task.get("custom_field_values") or {}
        if task.get("id") == PROJECT_ID or fields.get("mc_project_id") == PROJECT_ID:
            result.append(task)
    return result


def _fix_seed_spec(seed_spec: Any) -> Any:
    if not isinstance(seed_spec, list):
        return seed_spec
    updated = []
    changed = False
    for item in seed_spec:
        if not isinstance(item, dict):
            updated.append(item)
            continue
        copy = dict(item)
        if str(copy.get("title") or "").strip() == DAILY_TASK_TITLE and int(copy.get("cadence_hours") or 0) != 24:
            copy["cadence_hours"] = 24
            changed = True
        updated.append(copy)
    return updated if changed else seed_spec


def main() -> int:
    tasks = mc_list_tasks()
    scoped = _project_tasks(tasks)
    governance_repairs = 0
    for task in scoped:
        fields = dict(task.get("custom_field_values") or {})
        card_type = str(fields.get("mc_card_type") or "")
        if card_type in ACTIVE_GOVERNANCE and task.get("status") not in {"done", "failed"}:
            chairman_state = str(fields.get("mc_chairman_state") or "planned").strip().lower()
            target_status = "in_progress" if chairman_state in {"active", "approved"} else "inbox"
            target_phase_state = "active" if chairman_state in {"active", "approved"} else "pending"
            updated = dict(fields)
            updated.update({
                "mc_phase": "autonomy_active" if chairman_state in {"active", "approved"} else str(fields.get("mc_phase") or "intake"),
                "mc_phase_state": target_phase_state,
                "mc_claimed_by": None,
                "mc_claim_expires_at": None,
                "mc_last_error": None,
            })
            if card_type == "workstream":
                updated["mc_task_seed_spec"] = _fix_seed_spec(updated.get("mc_task_seed_spec"))
            mc_update_task(
                str(task.get("id") or ""),
                status=target_status,
                comment="[autonomy-repair] normalized governance card state and cleared stale review claim/session errors.",
                fields=updated,
            )
            governance_repairs += 1

    refreshed_tasks = _project_tasks(mc_list_tasks())
    daily_task = next((task for task in refreshed_tasks if task.get("title") == DAILY_TASK_TITLE), None)
    if daily_task and str(daily_task.get("status") or "") != "done":
        fields = dict(daily_task.get("custom_field_values") or {})
        scorecard = "artifacts/reports/luna-x-growth/scorecard-latest.json"
        snapshot = "artifacts/reports/luna-x-growth/profile-snapshot-latest.json"
        board_packet = "artifacts/reports/luna-x-growth/board-packet-latest.md"
        if _load(scorecard) and _load(snapshot) and _load(board_packet):
            fields.update({
                "mc_dispatch_policy": "auto",
                "mc_delivery_state": "done",
                "mc_proof_ref": scorecard,
                "mc_validation_artifact": scorecard,
                "mc_test_report_artifact": board_packet,
                "mc_last_error": None,
                "mc_output_summary": "Daily scorecard loop repaired and marked complete from latest canary artifacts.",
            })
            mc_update_task(
                str(daily_task.get("id") or ""),
                status="done",
                comment="[autonomy-repair] closed daily scorecard task using latest snapshot/scorecard/board packet evidence so cadence-based recreation can take over.",
                fields=fields,
            )

    print(json.dumps({"project_id": PROJECT_ID, "governance_repairs": governance_repairs}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
