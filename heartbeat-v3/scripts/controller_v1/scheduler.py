#!/usr/bin/env python3
"""Scheduler wrapper for controller-v1."""

from __future__ import annotations

from typing import Any

from scheduler_v2 import build_scheduler_snapshot


def compute_scheduler_snapshot(
    *,
    tasks: list[dict[str, Any]],
    actionable_reviews: list[dict[str, Any]],
    eligible_dispatch_tasks: list[dict[str, Any]],
    resource_level: str = "ok",
    slot_limits: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return build_scheduler_snapshot(
        tasks=tasks,
        actionable_reviews=actionable_reviews,
        eligible_dispatch_tasks=eligible_dispatch_tasks,
        resource_level=resource_level,
        gateway_healthy=True,
        slot_limits=slot_limits,
        mode="full",
        legacy_action={"type": "idle", "task_id": "", "lane": "", "reason": "controller"},
    )

