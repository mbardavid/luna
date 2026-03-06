#!/usr/bin/env python3
"""Conservative project-autonomy helpers for heartbeat-v3."""

from __future__ import annotations

from math import ceil
from typing import Any

from mc_control import (
    is_executable_leaf_task,
    normalize_dispatch_policy,
    normalize_status,
    task_card_type,
    task_chairman_state,
    task_dispatch_policy,
    task_generation_key,
    task_lane,
    task_milestone_id,
    task_project_id,
    task_status,
    task_workstream_id,
)

ACTIVE_PROJECT_STATES = {"active"}
ACTIVE_MILESTONE_STATES = {"active", "approved"}
ACTIVE_WORKSTREAM_STATES = {"active", "approved"}
WINDOW_STATUSES = {"inbox", "in_progress", "review"}
PROMOTABLE_POLICIES = {"backlog"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("id") or "").strip()


def _task_title(task: dict[str, Any]) -> str:
    return str(task.get("title") or "").strip()


def _task_sort_key(task: dict[str, Any]) -> tuple[Any, ...]:
    priority = PRIORITY_ORDER.get(str(task.get("priority") or "medium").lower(), 2)
    return (priority, str(task.get("created_at") or ""), _task_id(task))


def _matches_scope(task: dict[str, Any], *, project_id: str = "", milestone_id: str = "", workstream_id: str = "") -> bool:
    if project_id and task_project_id(task) != project_id:
        return False
    if milestone_id and task_milestone_id(task) != milestone_id:
        return False
    if workstream_id and task_workstream_id(task) != workstream_id:
        return False
    return True


def _active_cards(
    tasks: list[dict[str, Any]],
    *,
    card_type: str,
    states: set[str],
    project_id: str = "",
    milestone_id: str = "",
) -> list[dict[str, Any]]:
    matches = [
        task
        for task in tasks
        if task_card_type(task) == card_type
        and task_chairman_state(task) in states
        and task_status(task) not in {"done", "failed"}
        and _matches_scope(task, project_id=project_id, milestone_id=milestone_id)
    ]
    matches.sort(key=_task_sort_key)
    return matches


def select_active_project(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    projects = _active_cards(tasks, card_type="project", states=ACTIVE_PROJECT_STATES)
    return projects[0] if projects else None


def select_active_milestone(tasks: list[dict[str, Any]], project: dict[str, Any] | None) -> dict[str, Any] | None:
    if not project:
        return None
    milestones = _active_cards(
        tasks,
        card_type="milestone",
        states=ACTIVE_MILESTONE_STATES,
        project_id=_task_id(project),
    )
    return milestones[0] if milestones else None


def select_active_workstreams(
    tasks: list[dict[str, Any]],
    project: dict[str, Any] | None,
    milestone: dict[str, Any] | None,
    *,
    max_active_workstreams: int = 3,
) -> list[dict[str, Any]]:
    if not project or not milestone:
        return []
    workstreams = _active_cards(
        tasks,
        card_type="workstream",
        states=ACTIVE_WORKSTREAM_STATES,
        project_id=_task_id(project),
        milestone_id=_task_id(milestone),
    )
    return workstreams[:max(0, max_active_workstreams)]


def compute_lane_budget(
    tasks: list[dict[str, Any]],
    *,
    max_concurrent_in_progress: int,
    floor_ratio: float = 0.25,
    cap_ratio: float = 0.5,
) -> dict[str, int | bool]:
    capacity = max(1, int(max_concurrent_in_progress or 1))
    floor = max(1, ceil(capacity * float(floor_ratio or 0.25)))
    cap = max(floor, ceil(capacity * float(cap_ratio or 0.5)))
    review_debt = any(task_status(task) == "review" for task in tasks)
    ambient_ready = sum(
        1
        for task in tasks
        if task_lane(task) == "ambient"
        and task_status(task) == "inbox"
        and task_dispatch_policy(task) == "auto"
    )
    ambient_active = sum(
        1
        for task in tasks
        if task_lane(task) == "ambient"
        and task_status(task) in WINDOW_STATUSES
    )
    allow_burst = (not review_debt) and ambient_ready == 0 and ambient_active < max(1, capacity - floor + 1)
    target = cap if allow_burst else floor
    return {
        "capacity": capacity,
        "floor": floor,
        "cap": cap,
        "target": target,
        "allow_burst": allow_burst,
    }


def count_project_window(
    tasks: list[dict[str, Any]],
    *,
    project_id: str,
    milestone_id: str,
    workstream_ids: set[str],
) -> int:
    return sum(
        1
        for task in tasks
        if task_card_type(task) == "leaf_task"
        and task_dispatch_policy(task) == "auto"
        and task_status(task) in WINDOW_STATUSES
        and _matches_scope(task, project_id=project_id, milestone_id=milestone_id)
        and task_workstream_id(task) in workstream_ids
    )


def _workstream_window_counts(tasks: list[dict[str, Any]], workstream_ids: set[str]) -> dict[str, int]:
    counts = {workstream_id: 0 for workstream_id in workstream_ids}
    for task in tasks:
        workstream_id = task_workstream_id(task)
        if workstream_id not in workstream_ids:
            continue
        if task_card_type(task) != "leaf_task":
            continue
        if task_dispatch_policy(task) != "auto":
            continue
        if task_status(task) not in WINDOW_STATUSES:
            continue
        counts[workstream_id] = counts.get(workstream_id, 0) + 1
    return counts


def parse_task_seed_spec(task: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (task.get("custom_field_values") or {}).get("mc_task_seed_spec")
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        try:
            raw = __import__("json").loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        normalized.append(item)
    return normalized


def _seed_identity(seed: dict[str, Any]) -> str:
    explicit = str(seed.get("key") or seed.get("id") or "").strip()
    if explicit:
        return explicit
    return str(seed.get("title") or "").strip().lower()


def _existing_leaf_keys(tasks: list[dict[str, Any]], *, workstream_id: str) -> set[str]:
    keys: set[str] = set()
    for task in tasks:
        if task_workstream_id(task) != workstream_id:
            continue
        if task_card_type(task) != "leaf_task":
            continue
        key = task_generation_key(task) or _task_title(task).lower()
        if key:
            keys.add(key)
    return keys


def _seed_text(value: Any) -> str:
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _build_create_action(
    seed: dict[str, Any],
    *,
    project: dict[str, Any],
    milestone: dict[str, Any],
    workstream: dict[str, Any],
) -> dict[str, Any]:
    workstream_id = _task_id(workstream)
    project_id = _task_id(project)
    milestone_id = _task_id(milestone)
    generation_key = _seed_identity(seed)
    fields = {
        "mc_card_type": "leaf_task",
        "mc_parent_task_id": workstream_id,
        "mc_project_id": project_id,
        "mc_milestone_id": milestone_id,
        "mc_workstream_id": workstream_id,
        "mc_generation_mode": "autonomy",
        "mc_generation_key": generation_key,
        "mc_lane": "project",
        "mc_delivery_state": "queued",
        "mc_dispatch_policy": normalize_dispatch_policy(seed.get("dispatch_policy"), default="backlog"),
        "mc_workflow": str(seed.get("workflow") or "direct_exec").strip() or "direct_exec",
        "mc_risk_profile": str(seed.get("risk_profile") or "medium").strip() or "medium",
        "mc_budget_scope": str(seed.get("budget_scope") or "project").strip() or "project",
        "mc_outcome_ref": str(seed.get("outcome_ref") or (workstream.get("custom_field_values") or {}).get("mc_outcome_ref") or "").strip() or None,
        "mc_acceptance_criteria": _seed_text(seed.get("acceptance_criteria")),
        "mc_qa_checks": _seed_text(seed.get("qa_checks")),
        "mc_expected_artifacts": _seed_text(seed.get("expected_artifacts")),
    }
    return {
        "type": "create_leaf_task",
        "title": str(seed.get("title") or "").strip(),
        "description": str(seed.get("description") or "").strip() or f"Autonomy-generated leaf task for workstream {_task_title(workstream)}.",
        "assignee": str(seed.get("assignee") or workstream.get("assigned_agent_id") or "").strip(),
        "priority": str(seed.get("priority") or "medium").strip() or "medium",
        "status": normalize_status(seed.get("status"), default="inbox"),
        "fields": fields,
    }


def plan_project_autonomy(
    tasks: list[dict[str, Any]],
    *,
    max_concurrent_in_progress: int,
    floor_ratio: float = 0.25,
    cap_ratio: float = 0.5,
    max_active_workstreams: int = 3,
    max_auto_leaf_tasks_per_workstream: int = 2,
    max_new_leaf_tasks_per_cycle: int = 3,
) -> dict[str, Any]:
    project = select_active_project(tasks)
    if not project:
        return {"project": None, "milestone": None, "workstreams": [], "actions": [], "reason": "no_active_project"}

    milestone = select_active_milestone(tasks, project)
    if not milestone:
        return {"project": project, "milestone": None, "workstreams": [], "actions": [], "reason": "no_active_milestone"}

    workstreams = select_active_workstreams(
        tasks,
        project,
        milestone,
        max_active_workstreams=max_active_workstreams,
    )
    if not workstreams:
        return {"project": project, "milestone": milestone, "workstreams": [], "actions": [], "reason": "no_active_workstreams"}

    actions: list[dict[str, Any]] = []
    created = 0
    for workstream in workstreams:
        existing_keys = _existing_leaf_keys(tasks, workstream_id=_task_id(workstream))
        for seed in parse_task_seed_spec(workstream):
            seed_key = _seed_identity(seed)
            if seed_key in existing_keys:
                continue
            actions.append(_build_create_action(seed, project=project, milestone=milestone, workstream=workstream))
            existing_keys.add(seed_key)
            created += 1
            if created >= max(0, max_new_leaf_tasks_per_cycle):
                return {
                    "project": project,
                    "milestone": milestone,
                    "workstreams": workstreams,
                    "actions": actions,
                    "lane_budget": compute_lane_budget(
                        tasks,
                        max_concurrent_in_progress=max_concurrent_in_progress,
                        floor_ratio=floor_ratio,
                        cap_ratio=cap_ratio,
                    ),
                    "reason": "seed_materialized",
                }

    workstream_ids = {_task_id(task) for task in workstreams}
    lane_budget = compute_lane_budget(
        tasks,
        max_concurrent_in_progress=max_concurrent_in_progress,
        floor_ratio=floor_ratio,
        cap_ratio=cap_ratio,
    )
    current_window = count_project_window(
        tasks,
        project_id=_task_id(project),
        milestone_id=_task_id(milestone),
        workstream_ids=workstream_ids,
    )
    available = max(0, int(lane_budget["target"]) - current_window)
    workstream_counts = _workstream_window_counts(tasks, workstream_ids)

    if available <= 0:
        return {
            "project": project,
            "milestone": milestone,
            "workstreams": workstreams,
            "actions": [],
            "lane_budget": lane_budget,
            "current_window": current_window,
            "reason": "window_full",
        }

    for workstream in workstreams:
        workstream_id = _task_id(workstream)
        current_count = workstream_counts.get(workstream_id, 0)
        if current_count >= max_auto_leaf_tasks_per_workstream:
            continue
        candidates = [
            task
            for task in tasks
            if task_card_type(task) == "leaf_task"
            and _matches_scope(
                task,
                project_id=_task_id(project),
                milestone_id=_task_id(milestone),
                workstream_id=workstream_id,
            )
            and task_status(task) == "inbox"
            and task_dispatch_policy(task) in PROMOTABLE_POLICIES
            and is_executable_leaf_task(task)
        ]
        candidates.sort(key=_task_sort_key)
        for candidate in candidates:
            if available <= 0 or current_count >= max_auto_leaf_tasks_per_workstream:
                break
            actions.append(
                {
                    "type": "promote_leaf_task",
                    "task_id": _task_id(candidate),
                    "comment": (
                        f"[autonomy] promoted into active project window "
                        f"({ _task_title(project) } / { _task_title(milestone) } / { _task_title(workstream) })"
                    ),
                    "fields": {
                        "mc_card_type": "leaf_task",
                        "mc_project_id": _task_id(project),
                        "mc_milestone_id": _task_id(milestone),
                        "mc_workstream_id": workstream_id,
                        "mc_generation_mode": "autonomy",
                        "mc_lane": "project",
                        "mc_delivery_state": "queued",
                        "mc_dispatch_policy": "auto",
                    },
                }
            )
            available -= 1
            current_count += 1
            workstream_counts[workstream_id] = current_count

    return {
        "project": project,
        "milestone": milestone,
        "workstreams": workstreams,
        "actions": actions,
        "lane_budget": lane_budget,
        "current_window": current_window,
        "reason": "promotions_planned" if actions else "no_eligible_leaf_tasks",
    }


def choose_next_dispatch_task(
    eligible: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    *,
    max_concurrent_in_progress: int,
    floor_ratio: float = 0.25,
    cap_ratio: float = 0.5,
    max_active_workstreams: int = 3,
) -> dict[str, Any] | None:
    if not eligible:
        return None
    ordered = sorted(eligible, key=_task_sort_key)
    project = select_active_project(tasks)
    if not project:
        return ordered[0]
    milestone = select_active_milestone(tasks, project)
    if not milestone:
        return ordered[0]
    workstreams = select_active_workstreams(
        tasks,
        project,
        milestone,
        max_active_workstreams=max_active_workstreams,
    )
    workstream_ids = {_task_id(task) for task in workstreams}
    if not workstream_ids:
        return ordered[0]

    project_eligible = [
        task
        for task in ordered
        if task_lane(task) == "project"
        and _matches_scope(
            task,
            project_id=_task_id(project),
            milestone_id=_task_id(milestone),
        )
        and task_workstream_id(task) in workstream_ids
    ]
    ambient_eligible = [task for task in ordered if task not in project_eligible]
    if not project_eligible:
        return ambient_eligible[0] if ambient_eligible else ordered[0]

    lane_budget = compute_lane_budget(
        tasks,
        max_concurrent_in_progress=max_concurrent_in_progress,
        floor_ratio=floor_ratio,
        cap_ratio=cap_ratio,
    )
    eligible_ids = {_task_id(task) for task in eligible}
    current_window = sum(
        1
        for task in tasks
        if _task_id(task) not in eligible_ids
        and task_card_type(task) == "leaf_task"
        and task_status(task) in WINDOW_STATUSES
        and _matches_scope(
            task,
            project_id=_task_id(project),
            milestone_id=_task_id(milestone),
        )
        and task_workstream_id(task) in workstream_ids
    )
    if current_window < int(lane_budget["floor"]):
        return project_eligible[0]
    if ambient_eligible:
        return ambient_eligible[0]
    return project_eligible[0]
