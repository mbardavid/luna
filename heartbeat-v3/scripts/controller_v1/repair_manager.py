#!/usr/bin/env python3
"""Repair bundle lifecycle helpers for controller-v1."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from project_autonomy import detect_blocked_autonomy_execution
from mc_control import (
    task_card_type,
    task_dispatch_policy,
    task_fields,
    task_project_id,
    task_repair_bundle_id,
    task_repair_fingerprint,
    task_repair_source_task_id,
    task_review_agent,
    task_status,
)


def repair_children(tasks: list[dict[str, Any]], bundle_id: str) -> list[dict[str, Any]]:
    return [
        task for task in tasks
        if str(task_fields(task).get("mc_parent_task_id") or "") == bundle_id
    ]


def progress_repair_bundles(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    bundles = [task for task in tasks if task_card_type(task) == "repair_bundle"]
    for bundle in bundles:
        bundle_id = str(bundle.get("id") or "").strip()
        source_task_id = task_repair_source_task_id(bundle)
        if not bundle_id or not source_task_id:
            continue
        children = repair_children(tasks, bundle_id)
        diagnose = next((t for t in children if str(t.get("title", "")).startswith("Diagnose")), None)
        repair = next((t for t in children if str(t.get("title", "")).startswith("Repair")), None)
        validate = next((t for t in children if task_card_type(t) == "review_bundle"), None)
        source = next((t for t in tasks if str(t.get("id", "")) == source_task_id), None)

        if diagnose and task_status(diagnose) == "done" and repair and task_status(repair) == "inbox" and task_dispatch_policy(repair) == "backlog":
            repair_fields = dict(task_fields(repair))
            repair_fields.update({
                "mc_dispatch_policy": "auto",
                "mc_gate_reason": "",
                "mc_last_error": "",
                "mc_runtime_owner": "controller-v1",
            })
            actions.append({
                "task_id": str(repair.get("id") or ""),
                "status": "inbox",
                "comment": f"[controller-v1] diagnosis completed; repair task promoted for `{bundle_id[:8]}`.",
                "fields": repair_fields,
            })

        if repair and task_status(repair) == "done" and validate and task_status(validate) == "inbox":
            validate_fields = dict(task_fields(validate))
            validate_fields.update({
                "mc_review_agent": task_review_agent(validate, default="luna-judge"),
                "mc_gate_reason": "",
                "mc_last_error": "",
                "mc_runtime_owner": "controller-v1",
            })
            actions.append({
                "task_id": str(validate.get("id") or ""),
                "status": "review",
                "comment": f"[controller-v1] repair completed; judge validation requested for `{bundle_id[:8]}`.",
                "fields": validate_fields,
            })

        if validate and task_status(validate) == "done":
            bundle_fields = dict(task_fields(bundle))
            bundle_fields.update({
                "mc_repair_state": "resolved",
                "mc_phase": "repair_bundle_resolved",
                "mc_phase_state": "completed",
                "mc_chairman_state": "completed",
                "mc_last_error": "",
                "mc_runtime_owner": "controller-v1",
            })
            actions.append({
                "task_id": bundle_id,
                "status": "done",
                "comment": f"[controller-v1] repair bundle resolved for source task `{source_task_id[:8]}`.",
                "fields": bundle_fields,
            })
            if source:
                source_fields = dict(task_fields(source))
                source_fields.update({
                    "mc_gate_reason": "",
                    "mc_last_error": "",
                    "mc_repair_bundle_id": None,
                    "mc_repair_reason": "",
                    "mc_repair_fingerprint": "",
                    "mc_dispatch_policy": "auto" if task_project_id(source) else "backlog",
                    "mc_delivery_state": "queued",
                    "mc_runtime_owner": "controller-v1",
                })
                actions.append({
                    "task_id": source_task_id,
                    "status": "inbox",
                    "comment": f"[controller-v1] repair validated; source task `{source_task_id[:8]}` is eligible again.",
                    "fields": source_fields,
                })
    return actions


def open_or_reuse_repair_bundle(
    workspace: str | Path,
    *,
    source_task_id: str,
    anomaly: str,
    reason: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    script = Path(workspace) / "scripts" / "open-repair-bundle.py"
    cmd = [
        str(script),
        "--source-task-id",
        source_task_id,
        "--anomaly",
        anomaly,
        "--reason",
        reason,
        "--json",
    ]
    if dry_run:
        return {
            "bundle_id": f"dryrun-repair-{source_task_id[:8]}",
            "source_task_id": source_task_id,
            "fingerprint": f"{source_task_id}:{anomaly}",
            "reused": False,
        }
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "open-repair-bundle failed")
    payload = json.loads(proc.stdout.strip() or "{}")
    return payload if isinstance(payload, dict) else {}


def detect_blocked(tasks: list[dict[str, Any]], sessions_by_key: dict[str, Any], *, workspace: str | Path,
                   stall_minutes: int = 30) -> list[dict[str, Any]]:
    return detect_blocked_autonomy_execution(
        tasks,
        sessions_by_key,
        workspace_root=workspace,
        stall_minutes=stall_minutes,
    )
