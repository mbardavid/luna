#!/usr/bin/env python3
"""Rules-first gap evaluation for milestone watcher V1."""

from __future__ import annotations

import hashlib
from typing import Any

from mc_control import task_card_type, task_fields, task_status, task_workstream_id

from .outcome_watcher import MilestoneObservation


def _gap_id(observation_id: str, gap_class: str, scope_id: str, reason: str) -> str:
    digest = hashlib.sha1(f"{observation_id}|{gap_class}|{scope_id}|{reason}".encode("utf-8")).hexdigest()
    return f"gap-{digest[:12]}"


def _workstream_id_by_label(observation: MilestoneObservation, needle: str) -> str:
    lowered = needle.lower()
    for workstream in observation.workstreams:
        if lowered in str(workstream.get("title") or "").lower():
            return str(workstream.get("id") or "")
    return ""


def evaluate_gaps(observation: MilestoneObservation, directives: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    observation_id = observation.observation_id
    milestone_id = str(observation.milestone.get("id") or "")
    ws1_id = _workstream_id_by_label(observation, "positioning")
    ws2_id = _workstream_id_by_label(observation, "distribution")
    ws3_id = _workstream_id_by_label(observation, "analytics")

    for artifact_key, meta in observation.freshness.items():
        if not meta.get("exists"):
            reason = f"required artifact `{artifact_key}` is missing"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, "setup_missing", artifact_key, reason),
                    "gap_class": "setup_missing",
                    "severity": "high",
                    "scope_type": "artifact",
                    "scope_id": artifact_key,
                    "reason": reason,
                    "evidence": meta,
                    "target_workstream_id": ws3_id or ws1_id or ws2_id,
                }
            )
        elif not meta.get("fresh", False):
            reason = f"artifact `{artifact_key}` is stale"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, "artifact_stale", artifact_key, reason),
                    "gap_class": "artifact_stale",
                    "severity": "high",
                    "scope_type": "artifact",
                    "scope_id": artifact_key,
                    "reason": reason,
                    "evidence": meta,
                    "target_workstream_id": ws3_id or ws1_id or ws2_id,
                }
            )

    open_repairs = [
        task for task in observation.tasks
        if task_card_type(task) == "repair_bundle" and task_status(task) not in {"done", "failed"}
    ]
    for repair in open_repairs:
        scope_id = str(repair.get("id") or "")
        reason = f"open repair bundle `{scope_id[:8]}` blocks new work in this scope"
        gaps.append(
            {
                "gap_id": _gap_id(observation_id, "blocked_by_repair", scope_id, reason),
                "gap_class": "blocked_by_repair",
                "severity": "high",
                "scope_type": "repair_bundle",
                "scope_id": scope_id,
                "reason": reason,
                "evidence": {
                    "repair_bundle_id": scope_id,
                    "title": str(repair.get("title") or ""),
                    "workstream_id": task_workstream_id(repair),
                },
                "target_workstream_id": task_workstream_id(repair),
            }
        )

    open_leaf_titles = {str(task.get("title") or ""): task for task in observation.tasks if task_card_type(task) == "leaf_task"}
    milestone_title = str(observation.milestone.get("title") or "")
    if "M0" in milestone_title:
        requirements = [
            ("content pillars", "content_gap", ws1_id, "Extract 3-5 content pillars"),
            ("engagement plan", "distribution_gap", ws2_id, "Draft day-1 engagement plan"),
            ("charter", "missing_execution_step", ws1_id, "Audit Luna X account and freeze growth charter"),
        ]
        for needle, gap_class, workstream_id, label in requirements:
            if any(needle in title.lower() and task_status(task) == "done" for title, task in open_leaf_titles.items()):
                continue
            reason = f"required M0 deliverable missing: {label}"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, gap_class, label, reason),
                    "gap_class": gap_class,
                    "severity": "medium",
                    "scope_type": "workstream",
                    "scope_id": workstream_id or milestone_id,
                    "reason": reason,
                    "evidence": {"required_label": label},
                    "target_workstream_id": workstream_id,
                }
            )

    outcome = observation.outcome
    if all(meta.get("fresh") for meta in observation.freshness.values()):
        delta = int(outcome.get("net_followers_delta") or 0)
        if delta <= 0:
            reason = "fresh artifacts show no follower growth yet; distribution experiment is needed"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, "distribution_gap", milestone_id, reason),
                    "gap_class": "distribution_gap",
                    "severity": "medium",
                    "scope_type": "milestone",
                    "scope_id": milestone_id,
                    "reason": reason,
                    "evidence": {
                        "net_followers_delta": delta,
                        "suggested_action": outcome.get("suggested_action"),
                    },
                    "target_workstream_id": ws2_id or ws1_id,
                }
            )

    for review in outcome.get("review_summaries") or []:
        feedback = f"{review.get('reason','')} {review.get('feedback','')}".lower()
        if "stale" in feedback or "fresh" in feedback:
            reason = "recent judge feedback asked for fresher artifacts before further growth work"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, "measurement_gap", str(review.get("id") or ""), reason),
                    "gap_class": "measurement_gap",
                    "severity": "high",
                    "scope_type": "review_bundle",
                    "scope_id": str(review.get("id") or ""),
                    "reason": reason,
                    "evidence": review,
                    "target_workstream_id": ws3_id or ws2_id,
                }
            )

    for directive in directives:
        directive_type = str(directive.get("directive_type") or "").upper()
        if directive_type == "PAUSE":
            reason = "Chairman paused the project"
            gaps.append(
                {
                    "gap_id": _gap_id(observation_id, "needs_chairman", milestone_id, reason),
                    "gap_class": "needs_chairman",
                    "severity": "critical",
                    "scope_type": "project",
                    "scope_id": str(observation.project.get("id") or ""),
                    "reason": reason,
                    "evidence": directive,
                    "target_workstream_id": "",
                }
            )

    deduped: list[dict[str, Any]] = []
    seen = set()
    for gap in gaps:
        key = (
            gap.get("gap_class"),
            gap.get("scope_id"),
            gap.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(gap)
    return deduped
