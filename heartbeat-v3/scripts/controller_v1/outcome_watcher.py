#!/usr/bin/env python3
"""Observe active controller-owned milestones against real project artifacts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mc_control import (
    task_card_type,
    task_fields,
    task_milestone_id,
    task_project_id,
    task_runtime_owner,
    task_status,
)


UTC = timezone.utc
LUNA_PROJECT_ID = "3a0d8492-412a-4676-b945-cd3b02885e3f"
REPORT_ROOT = Path("/home/openclaw/.openclaw/workspace/artifacts/reports/luna-x-growth")
REQUIRED_ARTIFACTS = {
    "board_packet": ("board-packet-latest.md", 6 * 60),
    "scorecard": ("scorecard-latest.json", 6 * 60),
    "session_health": ("session-health-latest.json", 6 * 60),
    "baseline": ("baseline-latest.json", 24 * 60),
}


@dataclass
class MilestoneObservation:
    observation_id: str
    project: dict[str, Any]
    milestone: dict[str, Any]
    workstreams: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    outcome: dict[str, Any]
    artifacts: dict[str, Any]
    freshness: dict[str, Any]
    summary_hash: str
    observed_at: str


def _now() -> datetime:
    return datetime.now(UTC)


def _to_iso(dt: datetime | None = None) -> str:
    current = dt or _now()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _artifact_freshness(path: Path, max_age_minutes: int) -> dict[str, Any]:
    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "fresh": False,
            "age_minutes": None,
            "max_age_minutes": max_age_minutes,
        }
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    age_minutes = int((_now() - modified).total_seconds() // 60)
    return {
        "path": str(path),
        "exists": True,
        "fresh": age_minutes <= max_age_minutes,
        "age_minutes": age_minutes,
        "max_age_minutes": max_age_minutes,
        "modified_at": _to_iso(modified),
    }


def _active_project(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    candidates = [
        task
        for task in tasks
        if task_runtime_owner(task) == "controller-v1"
        and task_card_type(task) == "project"
        and task_status(task) in {"in_progress", "awaiting_human"}
    ]
    if not candidates:
        return None
    luna = next((task for task in candidates if str(task.get("id") or "") == LUNA_PROJECT_ID), None)
    return luna or candidates[0]


def _active_milestone(tasks: list[dict[str, Any]], project_id: str) -> dict[str, Any] | None:
    candidates = [
        task
        for task in tasks
        if task_runtime_owner(task) == "controller-v1"
        and task_card_type(task) == "milestone"
        and task_project_id(task) == project_id
        and task_status(task) in {"in_progress", "awaiting_human", "review"}
    ]
    return candidates[0] if candidates else None


def _milestone_workstreams(tasks: list[dict[str, Any]], project_id: str, milestone_id: str) -> list[dict[str, Any]]:
    return [
        task for task in tasks
        if task_runtime_owner(task) == "controller-v1"
        and task_card_type(task) == "workstream"
        and task_project_id(task) == project_id
        and task_milestone_id(task) == milestone_id
    ]


def _milestone_tasks(tasks: list[dict[str, Any]], project_id: str, milestone_id: str) -> list[dict[str, Any]]:
    return [
        task for task in tasks
        if task_runtime_owner(task) == "controller-v1"
        and task_project_id(task) == project_id
        and task_milestone_id(task) == milestone_id
        and task_card_type(task) in {"leaf_task", "review_bundle", "repair_bundle"}
    ]


def observe_active_milestone(
    *,
    tasks: list[dict[str, Any]],
    scheduler_snapshot: dict[str, Any],
    workspace: str | Path,
) -> MilestoneObservation | None:
    del workspace  # reserved for future multi-project artifact routing
    project = _active_project(tasks)
    if not project:
        return None
    project_id = str(project.get("id") or "")
    milestone = _active_milestone(tasks, project_id)
    if not milestone:
        return None
    milestone_id = str(milestone.get("id") or "")
    workstreams = _milestone_workstreams(tasks, project_id, milestone_id)
    scoped_tasks = _milestone_tasks(tasks, project_id, milestone_id)

    artifacts: dict[str, Any] = {}
    freshness: dict[str, Any] = {}
    for key, (name, max_age_minutes) in REQUIRED_ARTIFACTS.items():
        path = REPORT_ROOT / name
        artifacts[key] = _parse_json(path) if path.suffix == ".json" else {
            "path": str(path),
            "exists": path.exists(),
            "preview": path.read_text(encoding="utf-8")[:8000] if path.exists() else "",
        }
        freshness[key] = _artifact_freshness(path, max_age_minutes)

    scorecard = artifacts.get("scorecard") or {}
    outcome = {
        "project_id": project_id,
        "milestone_id": milestone_id,
        "followers_current": scorecard.get("followers_current"),
        "followers_baseline": scorecard.get("followers_baseline"),
        "net_followers_delta": scorecard.get("net_followers_delta"),
        "suggested_action": scorecard.get("suggested_action"),
        "scheduler": scheduler_snapshot,
        "project_title": str(project.get("title") or ""),
        "milestone_title": str(milestone.get("title") or ""),
        "workstream_titles": [str(item.get("title") or "") for item in workstreams],
        "open_leaf_titles": [
            str(task.get("title") or "")
            for task in scoped_tasks
            if task_card_type(task) == "leaf_task" and task_status(task) not in {"done", "failed"}
        ],
        "review_summaries": [
            {
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": task_status(task),
                "reason": str(task_fields(task).get("mc_review_reason") or ""),
                "feedback": str(task_fields(task).get("mc_rejection_feedback") or ""),
            }
            for task in scoped_tasks
            if task_card_type(task) == "review_bundle"
        ],
    }
    summary_hash = hashlib.sha1(
        json.dumps(
            {
                "outcome": outcome,
                "freshness": freshness,
                "tasks": [str(task.get("id") or "") for task in scoped_tasks],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    observed_at = _to_iso()
    observation_id = f"obs-{project_id[:8]}-{milestone_id[:8]}-{summary_hash[:12]}"
    return MilestoneObservation(
        observation_id=observation_id,
        project=project,
        milestone=milestone,
        workstreams=workstreams,
        tasks=scoped_tasks,
        outcome=outcome,
        artifacts=artifacts,
        freshness=freshness,
        summary_hash=summary_hash,
        observed_at=observed_at,
    )
