#!/usr/bin/env python3
"""Project autonomy planner wrapper for controller-v1."""

from __future__ import annotations

from typing import Any

from project_autonomy import plan_project_autonomy


def build_autonomy_plan(tasks: list[dict[str, Any]], *, slot_limits: dict[str, int]) -> dict[str, Any]:
    return plan_project_autonomy(
        tasks,
        max_concurrent_in_progress=int(slot_limits.get("healthy", 4) or 4),
        floor_ratio=0.25,
        cap_ratio=0.5,
        max_active_workstreams=3,
        max_auto_leaf_tasks_per_workstream=2,
        max_new_leaf_tasks_per_cycle=3,
    )

