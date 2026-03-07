#!/usr/bin/env python3
"""Layered scheduler helpers for heartbeat-v3."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from mc_control import (
    is_actionable_review_task,
    is_claim_active,
    is_execution_task,
    is_running_execution_task,
    task_gate_reason,
    task_lane,
    task_status,
)

LANE_PRIORITY: tuple[str, ...] = ("repair", "review", "project", "ambient")
SCHEDULER_MODES = {"shadow", "review_repair", "project", "full"}
DEFAULT_SLOT_LIMITS = {"healthy": 4, "degraded": 2, "critical": 1}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None = None) -> str:
    current = dt or utcnow()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_scheduler_mode(value: Any, default: str = "full") -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    return text if text in SCHEDULER_MODES else default


def normalize_health_state(resource_level: str, *, gateway_healthy: bool = True) -> str:
    if not gateway_healthy:
        return "critical"
    level = str(resource_level or "ok").strip().lower()
    if level == "critical":
        return "critical"
    if level == "degraded":
        return "degraded"
    return "healthy"


def slots_total_for_health(health_state: str, slot_limits: dict[str, Any] | None = None) -> int:
    limits = dict(DEFAULT_SLOT_LIMITS)
    if isinstance(slot_limits, dict):
        for key in ("healthy", "degraded", "critical"):
            try:
                limits[key] = max(1, int(slot_limits.get(key, limits[key]) or limits[key]))
            except Exception:
                continue
    return limits.get(health_state, limits["healthy"])


def lane_count_map() -> dict[str, int]:
    return {lane: 0 for lane in LANE_PRIORITY}


def count_running_by_lane(tasks: list[dict[str, Any]]) -> dict[str, int]:
    counts = lane_count_map()
    for task in tasks:
        lane = task_lane(task)
        if lane == "review":
            if is_actionable_review_task(task) and is_claim_active(task):
                counts["review"] += 1
            continue
        if not is_running_execution_task(task):
            continue
        counts[lane] = counts.get(lane, 0) + 1
    return counts


def count_eligible_by_lane(
    actionable_reviews: list[dict[str, Any]],
    eligible_dispatch_tasks: list[dict[str, Any]],
) -> dict[str, int]:
    counts = lane_count_map()
    counts["review"] = len(actionable_reviews)
    for task in eligible_dispatch_tasks:
        counts[task_lane(task)] = counts.get(task_lane(task), 0) + 1
    return counts


def reserve_slots(
    *,
    slots_total: int,
    eligible_by_lane: dict[str, int],
    running_by_lane: dict[str, int],
) -> dict[str, int]:
    reserved = lane_count_map()
    remaining = max(0, int(slots_total or 0))
    for lane in LANE_PRIORITY:
        has_demand = int(eligible_by_lane.get(lane, 0) or 0) > 0 or int(running_by_lane.get(lane, 0) or 0) > 0
        if not has_demand or remaining <= 0:
            continue
        reserved[lane] = 1
        remaining -= 1
    return reserved


def _deficit_lanes(
    eligible_by_lane: dict[str, int],
    running_by_lane: dict[str, int],
    reserved_slots: dict[str, int],
) -> list[str]:
    lanes: list[str] = []
    for lane in LANE_PRIORITY:
        if int(eligible_by_lane.get(lane, 0) or 0) <= 0:
            continue
        if int(running_by_lane.get(lane, 0) or 0) < int(reserved_slots.get(lane, 0) or 0):
            lanes.append(lane)
    return lanes


def choose_dispatch_lane(
    *,
    slots_total: int,
    eligible_by_lane: dict[str, int],
    running_by_lane: dict[str, int],
    reserved_slots: dict[str, int],
) -> tuple[str | None, list[str]]:
    blocked: list[str] = []
    total_running = sum(int(running_by_lane.get(lane, 0) or 0) for lane in LANE_PRIORITY)
    deficits = _deficit_lanes(eligible_by_lane, running_by_lane, reserved_slots)
    if deficits:
        return deficits[0], blocked

    if total_running >= max(0, int(slots_total or 0)):
        for lane in LANE_PRIORITY:
            if int(eligible_by_lane.get(lane, 0) or 0) <= 0:
                continue
            blocked.append(
                f"{lane}: capacity full ({total_running}/{slots_total}) while eligible={eligible_by_lane.get(lane, 0)}"
            )
        return None, blocked

    for lane in LANE_PRIORITY:
        if int(eligible_by_lane.get(lane, 0) or 0) > 0:
            return lane, blocked
    return None, blocked


def select_task_for_lane(
    lane: str,
    *,
    actionable_reviews: list[dict[str, Any]],
    eligible_dispatch_tasks: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if lane == "review":
        return actionable_reviews[0] if actionable_reviews else None
    candidates = [task for task in eligible_dispatch_tasks if task_lane(task) == lane]
    return candidates[0] if candidates else None


def build_scheduler_snapshot(
    *,
    tasks: list[dict[str, Any]],
    actionable_reviews: list[dict[str, Any]],
    eligible_dispatch_tasks: list[dict[str, Any]],
    resource_level: str,
    gateway_healthy: bool = True,
    slot_limits: dict[str, Any] | None = None,
    mode: str = "full",
    legacy_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_mode = normalize_scheduler_mode(mode)
    health_state = normalize_health_state(resource_level, gateway_healthy=gateway_healthy)
    slots_total = slots_total_for_health(health_state, slot_limits)
    running_by_lane = count_running_by_lane(tasks)
    eligible_by_lane = count_eligible_by_lane(actionable_reviews, eligible_dispatch_tasks)
    reserved_slots = reserve_slots(
        slots_total=slots_total,
        eligible_by_lane=eligible_by_lane,
        running_by_lane=running_by_lane,
    )
    lane, blocked_reasons = choose_dispatch_lane(
        slots_total=slots_total,
        eligible_by_lane=eligible_by_lane,
        running_by_lane=running_by_lane,
        reserved_slots=reserved_slots,
    )
    task = select_task_for_lane(
        lane or "",
        actionable_reviews=actionable_reviews,
        eligible_dispatch_tasks=eligible_dispatch_tasks,
    ) if lane else None
    dispatch_decision = {
        "type": "idle",
        "lane": lane or "",
        "task_id": "",
        "status": "",
    }
    if lane and task:
        dispatch_decision = {
            "type": "review" if lane == "review" else "dispatch",
            "lane": lane,
            "task_id": str(task.get("id") or ""),
            "status": task_status(task),
            "gate_reason": task_gate_reason(task),
        }

    shadow_diff = []
    if legacy_action:
        legacy_type = str(legacy_action.get("type") or "idle")
        legacy_task_id = str(legacy_action.get("task_id") or "")
        if legacy_type != dispatch_decision["type"] or legacy_task_id != dispatch_decision["task_id"]:
            shadow_diff.append(
                {
                    "legacy": {"type": legacy_type, "task_id": legacy_task_id},
                    "scheduler_v2": {"type": dispatch_decision["type"], "task_id": dispatch_decision["task_id"]},
                }
            )

    return {
        "last_tick": to_iso(),
        "mode": normalized_mode,
        "health_state": health_state,
        "slots_total": slots_total,
        "eligible_by_lane": eligible_by_lane,
        "running_by_lane": running_by_lane,
        "reserved_slots": reserved_slots,
        "dispatch_decision": dispatch_decision,
        "blocked_reasons": blocked_reasons,
        "shadow_diff_vs_legacy": shadow_diff,
    }


def scheduler_mode_owns_lane(mode: str, lane: str) -> bool:
    normalized_mode = normalize_scheduler_mode(mode)
    if normalized_mode == "shadow":
        return False
    if normalized_mode == "review_repair":
        return lane in {"review", "repair"}
    if normalized_mode == "project":
        return lane in {"review", "repair", "project"}
    return lane in LANE_PRIORITY


def write_scheduler_state(path: str | Path, snapshot: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(snapshot or {})
    stamp = payload.get("last_tick") or to_iso()
    payload.setdefault("generated_at", stamp)
    tmp_path: Path | None = None
    fd, raw_tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    tmp_path = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(payload, handle, indent=2)
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
