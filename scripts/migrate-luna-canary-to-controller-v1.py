#!/usr/bin/env python3
"""Migrate the Luna autonomy canary task tree to controller-v1 ownership."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


WORKSPACE = Path("/home/openclaw/.openclaw/workspace")
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
AUTONOMY_RUNTIME = WORKSPACE / "state" / "autonomy-runtime.json"


def run(cmd: list[str], timeout: int = 30) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    return proc.stdout.strip()


def list_tasks() -> list[dict]:
    raw = run([str(MC_CLIENT), "list-tasks"])
    payload = json.loads(raw or "{}")
    if isinstance(payload, dict):
        return payload.get("items", [])
    return payload if isinstance(payload, list) else []


def update_task(task_id: str, *, fields: dict, comment: str | None = None) -> None:
    cmd = [str(MC_CLIENT), "update-task", task_id, "--fields", json.dumps(fields, ensure_ascii=False)]
    if comment:
        cmd += ["--comment", comment]
    run(cmd, timeout=45)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    runtime = {}
    if AUTONOMY_RUNTIME.exists():
        runtime = json.loads(AUTONOMY_RUNTIME.read_text(encoding="utf-8") or "{}")
    project_id = args.project_id or str(runtime.get("project_id") or "").strip()
    if not project_id:
        raise SystemExit("project_id not found in autonomy-runtime.json; pass --project-id")

    tasks = list_tasks()
    by_id = {str(task.get("id") or ""): task for task in tasks}

    owned_ids = {project_id}
    changed = True
    while changed:
        changed = False
        for task in tasks:
            task_id = str(task.get("id") or "")
            fields = task.get("custom_field_values") or {}
            parent_id = str(fields.get("mc_parent_task_id") or "")
            task_project_id = str(fields.get("mc_project_id") or "")
            repair_bundle_id = str(fields.get("mc_repair_bundle_id") or "")
            repair_source_id = str(fields.get("mc_repair_source_task_id") or "")
            if (
                task_project_id == project_id
                or parent_id in owned_ids
                or repair_bundle_id in owned_ids
                or repair_source_id in owned_ids
            ):
                if task_id and task_id not in owned_ids:
                    owned_ids.add(task_id)
                    changed = True

    updates = []
    for task_id in sorted(owned_ids):
        task = by_id.get(task_id)
        if not task:
            continue
        fields = dict(task.get("custom_field_values") or {})
        if str(fields.get("mc_runtime_owner") or "") == "controller-v1":
            continue
        fields["mc_runtime_owner"] = "controller-v1"
        if str(fields.get("mc_card_type") or "") == "review_bundle":
            fields.setdefault("mc_review_agent", "luna-judge")
        updates.append((task_id, fields))

    if args.apply:
        for task_id, fields in updates:
            update_task(task_id, fields=fields, comment="[controller-v1] ownership migrated from legacy runtime.")

    print(json.dumps({
        "project_id": project_id,
        "owned_count": len(owned_ids),
        "updates": len(updates),
        "updated_task_ids": [task_id for task_id, _ in updates],
        "applied": bool(args.apply),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

