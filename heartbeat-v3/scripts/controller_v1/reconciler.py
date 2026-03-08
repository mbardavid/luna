#!/usr/bin/env python3
"""Reconciliation helpers for controller-v1."""

from __future__ import annotations

from typing import Any

from mc_control import (
    task_card_type,
    task_delivery_state,
    task_execution_owner,
    task_gate_reason,
    task_lane,
    task_milestone_id,
    task_project_id,
    task_repair_fingerprint,
    task_repair_source_task_id,
    task_repair_state,
    task_runtime_owner,
    task_status,
    task_workflow,
    task_workstream_id,
)

from .runtime_store import RuntimeStore


def reconcile_tasks(store: RuntimeStore, tasks: list[dict[str, Any]]) -> dict[str, int]:
    controller_owned = 0
    for task in tasks:
        runtime_owner = task_runtime_owner(task)
        if runtime_owner == "controller-v1":
            controller_owned += 1
        store.upsert_tracked_task(
            task,
            card_type=task_card_type(task),
            lane=task_lane(task),
            workflow=task_workflow(task),
            project_id=task_project_id(task),
            milestone_id=task_milestone_id(task),
            workstream_id=task_workstream_id(task),
            desired_state=task_status(task),
            actual_state=task_delivery_state(task),
            gate_reason=task_gate_reason(task),
            runtime_owner=runtime_owner,
            assigned_agent=task_execution_owner(task),
        )
        if task_card_type(task) == "repair_bundle":
            store.set_repair(
                bundle_id=str(task.get("id") or ""),
                source_task_id=task_repair_source_task_id(task),
                fingerprint=task_repair_fingerprint(task) or f"{task_repair_source_task_id(task)}:repair_bundle",
                status=task_repair_state(task, default="open") or "open",
            )
    return {"tracked": len(tasks), "controller_owned": controller_owned}
