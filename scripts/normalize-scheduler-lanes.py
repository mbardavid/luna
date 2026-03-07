#!/usr/bin/env python3
"""Normalize Mission Control lane fields for scheduler-v2 semantics."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"


def run_cmd(cmd: list[str]) -> str:
    completed = subprocess.run(cmd, text=True, capture_output=True, check=True)
    return completed.stdout.strip()


def list_tasks() -> list[dict[str, Any]]:
    payload = json.loads(run_cmd([str(MC_CLIENT), "list-tasks"]) or "{}")
    if isinstance(payload, dict):
        return payload.get("items", [])
    return payload if isinstance(payload, list) else []


def update_task(task_id: str, fields: dict[str, Any], comment: str, dry_run: bool) -> None:
    cmd = [str(MC_CLIENT), "update-task", task_id, "--fields", json.dumps(fields), "--comment", comment]
    if dry_run:
        print(json.dumps({"task_id": task_id, "fields": fields, "comment": comment}, ensure_ascii=False))
        return
    run_cmd(cmd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    changed = 0
    for task in list_tasks():
        task_id = str(task.get("id") or "")
        fields = dict(task.get("custom_field_values") or {})
        card_type = str(fields.get("mc_card_type") or "").strip()
        repair_bundle_id = str(fields.get("mc_repair_bundle_id") or "").strip()
        updated = dict(fields)
        reason: list[str] = []

        if card_type == "repair_bundle" and fields.get("mc_lane") != "repair":
            updated["mc_lane"] = "repair"
            reason.append("repair_bundle lane -> repair")
        elif repair_bundle_id:
            target_lane = "review" if card_type == "review_bundle" else "repair"
            if fields.get("mc_lane") != target_lane:
                updated["mc_lane"] = target_lane
                reason.append(f"repair child lane -> {target_lane}")

        if card_type == "review_bundle" and not fields.get("mc_review_agent"):
            updated["mc_review_agent"] = "luna-judge"
            reason.append("default review agent -> luna-judge")

        if updated == fields:
            continue

        update_task(
            task_id,
            updated,
            "[scheduler-v2] normalized lane/review fields: " + ", ".join(reason),
            args.dry_run,
        )
        changed += 1

    print(json.dumps({"changed": changed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
