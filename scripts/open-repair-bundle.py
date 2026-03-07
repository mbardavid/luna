#!/usr/bin/env python3
"""Open or reuse an audited repair bundle for a broken task."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/openclaw/.openclaw/workspace")
MC_CLIENT = ROOT / "scripts" / "mc-client.sh"
sys.path.insert(0, str(ROOT / "heartbeat-v3" / "scripts"))

from mc_control import (
    task_card_type,
    task_dispatch_policy,
    task_fields,
    task_gate_reason,
    task_milestone_id,
    task_project_id,
    task_repair_fingerprint,
    task_repair_source_task_id,
    task_review_agent,
    task_status,
    task_workstream_id,
)


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
    raw = run([str(MC_CLIENT), "get-task", task_id])
    data = json.loads(raw or "{}")
    return data if isinstance(data, dict) else {}


def mc_create_task(title: str, description: str, assignee: str, priority: str, status: str, fields: dict) -> dict:
    raw = run(
        [
            str(MC_CLIENT),
            "create-task",
            title,
            description,
            assignee,
            priority,
            status,
            json.dumps(fields, ensure_ascii=False),
        ],
        timeout=45,
    )
    return json.loads(raw or "{}")


def mc_update_task(task_id: str, *, status: str | None = None, comment: str | None = None, fields: dict | None = None) -> dict:
    cmd = [str(MC_CLIENT), "update-task", task_id]
    if status:
        cmd += ["--status", status]
    if comment:
        cmd += ["--comment", comment]
    if fields is not None:
        cmd += ["--fields", json.dumps(fields, ensure_ascii=False)]
    raw = run(cmd, timeout=45)
    return json.loads(raw or "{}") if raw else {}


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def fingerprint(source_task_id: str, anomaly_class: str) -> str:
    return f"{source_task_id}:{anomaly_class}".strip()


def child_title(kind: str, source_title: str) -> str:
    return f"{kind} — {source_title}".strip()


def ensure_child(
    tasks: list[dict],
    *,
    bundle_id: str,
    title: str,
    description: str,
    assignee: str,
    priority: str,
    status: str,
    fields: dict,
) -> dict:
    for task in tasks:
        if str(task.get("title") or "").strip() == title and str(task_fields(task).get("mc_parent_task_id") or "") == bundle_id:
            return task
    return mc_create_task(title, description, assignee, priority, status, fields)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-task-id", required=True)
    parser.add_argument("--anomaly", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--default-owner", default="cto-ops")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    tasks = mc_list_tasks()
    source = mc_get_task(args.source_task_id)
    if not source:
        raise SystemExit(f"source task not found: {args.source_task_id}")

    fp = fingerprint(args.source_task_id, args.anomaly)
    open_bundle = next(
        (
            task for task in tasks
            if task_card_type(task) == "repair_bundle"
            and task_repair_fingerprint(task) == fp
            and task_status(task) not in {"done", "failed"}
        ),
        None,
    )

    source_title = str(source.get("title") or f"Task {args.source_task_id[:8]}")
    project_id = task_project_id(source)
    milestone_id = task_milestone_id(source)
    workstream_id = task_workstream_id(source)
    review_agent = task_review_agent(source)

    if open_bundle is None:
        bundle_fields = {
            "mc_card_type": "repair_bundle",
            "mc_project_id": project_id or None,
            "mc_milestone_id": milestone_id or None,
            "mc_workstream_id": workstream_id or None,
            "mc_lane": "repair",
            "mc_dispatch_policy": "human_hold",
            "mc_phase": "repair_bundle_open",
            "mc_phase_state": "active",
            "mc_chairman_state": "active",
            "mc_repair_source_task_id": args.source_task_id,
            "mc_repair_reason": args.reason,
            "mc_repair_fingerprint": fp,
            "mc_repair_state": "open",
            "mc_review_agent": review_agent,
        }
        bundle = mc_create_task(
            f"Repair bundle: {source_title}",
            "\n".join(
                [
                    "## Objective",
                    f"Stabilize the broken task `{args.source_task_id}` after runtime anomaly `{args.anomaly}`.",
                    "",
                    "## Context",
                    args.reason,
                    "",
                    "## Expected Flow",
                    "1. Diagnose the root cause.",
                    "2. Repair the broken execution path.",
                    "3. Validate the repair through judge review.",
                ]
            ),
            "",
            "high",
            "in_progress",
            bundle_fields,
        )
        bundle_id = str(bundle.get("id") or "")
    else:
        bundle = open_bundle
        bundle_id = str(bundle.get("id") or "")
        bundle_fields = dict(task_fields(bundle))
        bundle_fields.update(
            {
                "mc_repair_reason": args.reason,
                "mc_repair_fingerprint": fp,
                "mc_repair_state": bundle_fields.get("mc_repair_state") or "open",
            }
        )
        mc_update_task(
            bundle_id,
            status="in_progress",
            comment=f"[repair-bundle] reused for anomaly `{args.anomaly}` on source task `{args.source_task_id[:8]}`.",
            fields=bundle_fields,
        )

    common_scope = {
        "mc_parent_task_id": bundle_id,
        "mc_project_id": project_id or None,
        "mc_milestone_id": milestone_id or None,
        "mc_workstream_id": workstream_id or None,
        "mc_generation_mode": "autonomy",
        "mc_lane": "repair",
        "mc_repair_bundle_id": bundle_id,
        "mc_repair_source_task_id": args.source_task_id,
        "mc_repair_reason": args.reason,
        "mc_repair_fingerprint": fp,
    }

    diagnose = ensure_child(
        tasks,
        bundle_id=bundle_id,
        title=child_title("Diagnose", source_title),
        description="\n".join(
            [
                "## Objective",
                "Determine the root cause of the task failure.",
                "",
                "## Context",
                args.reason,
                "",
                "## Required Output",
                "- Root cause",
                "- Proposed repair owner",
                "- Explicit next repair step",
            ]
        ),
        assignee=args.default_owner,
        priority="high",
        status="inbox",
        fields={
            **common_scope,
            "mc_card_type": "leaf_task",
            "mc_dispatch_policy": "auto",
            "mc_delivery_state": "queued",
            "mc_acceptance_criteria": "Root cause identified and repair path proposed.",
            "mc_qa_checks": "Provide explicit evidence and next-step recommendation.",
            "mc_expected_artifacts": f"artifacts/repairs/{bundle_id[:8]}-diagnose.md",
            "mc_repair_state": "diagnosing",
        },
    )

    repair = ensure_child(
        tasks,
        bundle_id=bundle_id,
        title=child_title("Repair", source_title),
        description="\n".join(
            [
                "## Objective",
                "Repair the execution path after diagnosis.",
                "",
                "## Context",
                args.reason,
                "",
                "## Required Output",
                "- Applied fix",
                "- Commands/checks run",
                "- Repair artifact",
            ]
        ),
        assignee=args.default_owner,
        priority="high",
        status="inbox",
        fields={
            **common_scope,
            "mc_card_type": "leaf_task",
            "mc_dispatch_policy": "backlog",
            "mc_delivery_state": "queued",
            "mc_acceptance_criteria": "Repair applied and verifiable.",
            "mc_qa_checks": "Run the concrete checks needed to prove the repair.",
            "mc_expected_artifacts": f"artifacts/repairs/{bundle_id[:8]}-repair.md",
            "mc_repair_state": "repairing",
        },
    )

    validate = ensure_child(
        tasks,
        bundle_id=bundle_id,
        title=child_title("Validate repair", source_title),
        description="\n".join(
            [
                "## Objective",
                "Judge whether the repair is sufficient to re-open the source task.",
                "",
                "## Context",
                args.reason,
                "",
                "## Required Output",
                "- Explicit approve/reject decision",
                "- Evidence reviewed",
                "- Clear disposition for the source task",
            ]
        ),
        assignee="",
        priority="high",
        status="inbox",
        fields={
            **common_scope,
            "mc_card_type": "review_bundle",
            "mc_lane": "review",
            "mc_dispatch_policy": "backlog",
            "mc_delivery_state": "queued",
            "mc_review_agent": review_agent,
            "mc_acceptance_criteria": "Judge decision recorded with evidence.",
            "mc_qa_checks": "Validate evidence, repair artifact, and source-task readiness.",
            "mc_expected_artifacts": f"artifacts/repairs/{bundle_id[:8]}-validate.md",
            "mc_repair_state": "validating",
        },
    )

    source_fields = dict(task_fields(source))
    source_fields.update(
        {
            "mc_dispatch_policy": "backlog",
            "mc_delivery_state": "queued",
            "mc_gate_reason": "repair_open",
            "mc_last_error": args.anomaly,
            "mc_session_key": "",
            "mc_repair_bundle_id": bundle_id,
            "mc_repair_reason": args.reason,
            "mc_repair_fingerprint": fp,
        }
    )
    mc_update_task(
        args.source_task_id,
        status="inbox",
        comment=f"[repair-bundle] gated behind repair bundle `{bundle_id[:8]}` after `{args.anomaly}`.",
        fields=source_fields,
    )

    payload = {
        "bundle_id": bundle_id,
        "source_task_id": args.source_task_id,
        "fingerprint": fp,
        "diagnose_id": diagnose.get("id"),
        "repair_id": repair.get("id"),
        "validate_id": validate.get("id"),
        "reused": open_bundle is not None,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(bundle_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
