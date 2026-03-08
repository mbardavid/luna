#!/usr/bin/env python3
"""Normalize legacy governance cards that were left in review."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


WORKSPACE = Path("/home/openclaw/.openclaw/workspace")
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
TARGET_CARD_TYPES = {"project", "milestone", "workstream", "repair_bundle"}


def run(cmd: list[str], timeout: int = 45) -> str:
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


def main() -> int:
    tasks = list_tasks()
    normalized = []
    for task in tasks:
        fields = task.get("custom_field_values") or {}
        if task.get("status") != "review":
            continue
        if str(fields.get("mc_runtime_owner") or "legacy") != "legacy":
            continue
        if str(fields.get("mc_card_type") or "") not in TARGET_CARD_TYPES:
            continue
        task_id = str(task.get("id") or "")
        if not task_id:
            continue
        run(
            [
                str(MC_CLIENT),
                "update-task",
                task_id,
                "--status",
                "in_progress",
                "--comment",
                "[cleanup] normalized legacy governance card out of review into in_progress.",
            ]
        )
        normalized.append({"id": task_id, "title": str(task.get("title") or "")})
    print(json.dumps({"normalized": normalized, "count": len(normalized)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
