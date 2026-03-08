#!/usr/bin/env python3
"""Normalize legacy repair leaf tasks so they remain executable."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/openclaw/.openclaw/workspace")
MC_CLIENT = ROOT / "scripts" / "mc-client.sh"
sys.path.insert(0, str(ROOT / "heartbeat-v3" / "scripts"))
sys.path.insert(0, str(ROOT / "scripts"))

from mc_control import task_card_type, task_fields, task_lane, task_parent_task_id  # noqa: E402


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


def mc_update_task(task_id: str, *, assignee: str, fields: dict) -> dict:
    raw = run(
        [
            str(MC_CLIENT),
            "update-task",
            task_id,
            "--assignee",
            assignee,
            "--fields",
            json.dumps(fields, ensure_ascii=False),
        ],
        timeout=45,
    )
    return json.loads(raw or "{}") if raw else {}


def normalize_owner(task: dict, default_owner: str) -> str:
    fields = task_fields(task)
    explicit = str(fields.get("mc_assigned_agent") or "").strip().lower()
    if explicit and explicit not in {"none", "human"}:
        return explicit
    phase_owner = str(fields.get("mc_phase_owner") or "").strip().lower()
    if phase_owner and phase_owner not in {"none", "human"}:
        return phase_owner
    return default_owner


def normalize_fields(task: dict, owner: str) -> dict:
    fields = dict(task_fields(task))
    fields["mc_lane"] = "repair"
    fields["mc_card_type"] = task_card_type(task)
    if task_card_type(task) == "leaf_task":
        fields["mc_assigned_agent"] = owner
    if fields.get("mc_repair_bundle_id"):
        fields["mc_generation_mode"] = fields.get("mc_generation_mode") or "autonomy"
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--default-owner", default="cto-ops")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    tasks = mc_list_tasks()
    normalized: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []

    for task in tasks:
        if task_card_type(task) != "leaf_task":
            continue
        if task_lane(task) != "repair":
            continue
        parent_id = task_parent_task_id(task)
        bundle_id = str(task_fields(task).get("mc_repair_bundle_id") or "").strip()
        if not parent_id or not bundle_id or parent_id != bundle_id:
            continue
        if str(task.get("assigned_agent_id") or "").strip():
            continue
        owner = normalize_owner(task, args.default_owner)
        if not owner:
            skipped.append({
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "reason": "no_owner_candidate",
            })
            continue
        fields = normalize_fields(task, owner)
        if not args.dry_run:
            mc_update_task(str(task.get("id") or ""), assignee=owner, fields=fields)
        normalized.append({
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "owner": owner,
        })

    payload = {
        "normalized": normalized,
        "normalized_count": len(normalized),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "dry_run": args.dry_run,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"normalized={len(normalized)} skipped={len(skipped)} dry_run={args.dry_run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
