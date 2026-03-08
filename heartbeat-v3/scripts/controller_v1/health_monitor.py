#!/usr/bin/env python3
"""Controller-v1 health helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def to_iso(dt: datetime | None = None) -> str:
    current = dt or datetime.now(timezone.utc)
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_health_summary(*, owned_tasks: int, dispatches: int, queue_ingests: int, judge_ingests: int) -> dict[str, Any]:
    return {
        "last_tick": to_iso(),
        "owned_tasks": int(owned_tasks or 0),
        "dispatches_this_tick": int(dispatches or 0),
        "queue_ingests_this_tick": int(queue_ingests or 0),
        "judge_ingests_this_tick": int(judge_ingests or 0),
        "status": "ok",
    }

