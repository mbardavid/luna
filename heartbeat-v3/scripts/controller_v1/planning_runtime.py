#!/usr/bin/env python3
"""Materialize milestone planning intents into controller-owned Mission Control tasks."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mc_control import (
    task_card_type,
    task_dispatch_policy,
    task_fields,
    task_gate_reason,
    task_project_id,
    task_status,
    task_workstream_id,
)

from .repair_manager import open_or_reuse_repair_bundle
from .runtime_store import RuntimeStore, to_iso


ALLOWED_INTENTS = {
    "create_leaf_task",
    "promote_leaf_task",
    "create_review_bundle",
    "open_repair_bundle",
    "escalate_chairman",
}


def _slug(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value.strip())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-") or "gap"


def normalized_objective(spec: dict[str, Any]) -> str:
    text = str(spec.get("title") or spec.get("description") or "").strip().lower()
    return " ".join(text.split())


def build_dedupe_key(
    *,
    project_id: str,
    milestone_id: str,
    gap_class: str,
    target_scope: str,
    spec: dict[str, Any],
) -> str:
    return "::".join(
        [
            project_id,
            milestone_id,
            gap_class,
            target_scope,
            _slug(normalized_objective(spec)),
        ]
    )


def _existing_equivalent_task(tasks: list[dict[str, Any]], *, project_id: str, workstream_id: str, title: str) -> dict[str, Any] | None:
    target_title = " ".join(str(title or "").strip().lower().split())
    for task in tasks:
        if task_project_id(task) != project_id:
            continue
        if workstream_id and task_workstream_id(task) != workstream_id:
            continue
        if task_card_type(task) not in {"leaf_task", "review_bundle"}:
            continue
        current = " ".join(str(task.get("title") or "").strip().lower().split())
        if current == target_title and task_status(task) not in {"done", "failed"}:
            return task
    return None


def _leaf_fields(
    *,
    project_id: str,
    milestone_id: str,
    workstream_id: str,
    intent_id: str,
    observation_id: str,
    gap_class: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    return {
        "mc_runtime_owner": "controller-v1",
        "mc_card_type": "leaf_task",
        "mc_lane": "project",
        "mc_dispatch_policy": "auto",
        "mc_workflow": "direct_exec",
        "mc_delivery_state": "queued",
        "mc_generation_mode": "autonomy",
        "mc_project_id": project_id,
        "mc_milestone_id": milestone_id,
        "mc_workstream_id": workstream_id,
        "mc_planning_intent_id": intent_id,
        "mc_gap_class": gap_class,
        "mc_source_observation_id": observation_id,
        "mc_assigned_agent": str(spec.get("assignee") or ""),
        "mc_acceptance_criteria": str(spec.get("acceptance_criteria") or ""),
        "mc_qa_checks": str(spec.get("qa_checks") or ""),
        "mc_expected_artifacts": str(spec.get("expected_artifacts") or ""),
    }


def _review_fields(
    *,
    project_id: str,
    milestone_id: str,
    workstream_id: str,
    intent_id: str,
    observation_id: str,
    gap_class: str,
) -> dict[str, Any]:
    return {
        "mc_runtime_owner": "controller-v1",
        "mc_card_type": "review_bundle",
        "mc_lane": "review",
        "mc_dispatch_policy": "auto",
        "mc_workflow": "direct_exec",
        "mc_delivery_state": "review",
        "mc_generation_mode": "autonomy",
        "mc_project_id": project_id,
        "mc_milestone_id": milestone_id,
        "mc_workstream_id": workstream_id,
        "mc_review_agent": "luna-judge",
        "mc_planning_intent_id": intent_id,
        "mc_gap_class": gap_class,
        "mc_source_observation_id": observation_id,
    }


def _create_chairman_proposal(
    *,
    store: RuntimeStore,
    observation_id: str,
    gap_class: str,
    reason: str,
    payload: dict[str, Any],
) -> str:
    digest = hashlib.sha1(f"{observation_id}|{gap_class}|{reason}".encode("utf-8")).hexdigest()[:12]
    proposal_id = f"proposal-{digest}"
    store.upsert_chairman_proposal(
        proposal_id=proposal_id,
        observation_id=observation_id,
        proposal_type=gap_class,
        reason=reason,
        payload=payload,
        status="pending",
    )
    return proposal_id


def materialize_planning_intents(
    *,
    store: RuntimeStore,
    projection,
    workspace,
    observation: dict[str, Any],
    gaps: list[dict[str, Any]],
    result: dict[str, Any],
    tasks: list[dict[str, Any]],
    dry_run: bool = False,
) -> dict[str, Any]:
    del gaps, dry_run
    project = observation.get("project") or {}
    milestone = observation.get("milestone") or {}
    project_id = str(project.get("id") or "")
    milestone_id = str(milestone.get("id") or "")
    observation_id = str(observation.get("observation_id") or "")

    created_leaf = 0
    created_review = 0
    applied = 0
    proposal_ids: list[str] = []
    intent_ids: list[str] = []
    for raw_intent in result.get("planning_intents") or []:
        intent_type = str(raw_intent.get("intent_type") or "")
        if intent_type not in ALLOWED_INTENTS:
            continue
        gap_class = str(raw_intent.get("gap_class") or "")
        target_scope = str(raw_intent.get("target_scope") or "")
        spec = dict(raw_intent.get("spec") or {})
        dedupe_key = build_dedupe_key(
            project_id=project_id,
            milestone_id=milestone_id,
            gap_class=gap_class,
            target_scope=target_scope,
            spec=spec,
        )
        if store.has_open_intent(dedupe_key):
            continue
        intent_id = f"intent-{hashlib.sha1(dedupe_key.encode('utf-8')).hexdigest()[:12]}"
        target_workstream_id = str(spec.get("workstream_id") or target_scope or "")
        if intent_type == "create_leaf_task":
            if created_leaf >= 2:
                continue
            existing = _existing_equivalent_task(
                tasks,
                project_id=project_id,
                workstream_id=target_workstream_id,
                title=str(spec.get("title") or ""),
            )
            if existing:
                if task_status(existing) == "inbox" and not task_gate_reason(existing) and task_card_type(existing) == "leaf_task":
                    fields = dict(task_fields(existing))
                    fields.update(
                        _leaf_fields(
                            project_id=project_id,
                            milestone_id=milestone_id,
                            workstream_id=target_workstream_id,
                            intent_id=intent_id,
                            observation_id=observation_id,
                            gap_class=gap_class,
                            spec=spec,
                        )
                    )
                    if task_dispatch_policy(existing) != "auto":
                        fields["mc_dispatch_policy"] = "auto"
                    projection.apply_if_changed(
                        store,
                        task_id=str(existing.get("id") or ""),
                        status="inbox",
                        comment=f"[controller-v1] planner promoted existing task for gap `{gap_class}`.",
                        fields=fields,
                    )
                    store.upsert_planning_intent(
                        intent_id=intent_id,
                        observation_id=observation_id,
                        intent_type="promote_leaf_task",
                        target_scope=target_workstream_id,
                        dedupe_key=dedupe_key,
                        spec=spec,
                        status="materialized",
                        created_task_id=str(existing.get("id") or ""),
                        materialized_at=to_iso(),
                    )
                    created_leaf += 1
                    applied += 1
                    intent_ids.append(intent_id)
                continue
            fields = _leaf_fields(
                project_id=project_id,
                milestone_id=milestone_id,
                workstream_id=target_workstream_id,
                intent_id=intent_id,
                observation_id=observation_id,
                gap_class=gap_class,
                spec=spec,
            )
            created = projection.create_task(
                str(spec.get("title") or "Autonomy task"),
                str(spec.get("description") or ""),
                str(spec.get("assignee") or ""),
                str(spec.get("priority") or "medium"),
                "inbox",
                fields,
            )
            task_id = str(created.get("id") or "")
            if task_id:
                store.upsert_planning_intent(
                    intent_id=intent_id,
                    observation_id=observation_id,
                    intent_type=intent_type,
                    target_scope=target_workstream_id,
                    dedupe_key=dedupe_key,
                    spec=spec,
                    status="materialized",
                    created_task_id=task_id,
                    materialized_at=to_iso(),
                )
                created_leaf += 1
                applied += 1
                intent_ids.append(intent_id)
            continue
        if intent_type == "create_review_bundle":
            if created_review >= 1:
                continue
            existing = _existing_equivalent_task(
                tasks,
                project_id=project_id,
                workstream_id=target_workstream_id,
                title=str(spec.get("title") or ""),
            )
            if existing:
                continue
            fields = _review_fields(
                project_id=project_id,
                milestone_id=milestone_id,
                workstream_id=target_workstream_id,
                intent_id=intent_id,
                observation_id=observation_id,
                gap_class=gap_class,
            )
            created = projection.create_task(
                str(spec.get("title") or "Autonomy review bundle"),
                str(spec.get("description") or ""),
                "",
                str(spec.get("priority") or "medium"),
                "review",
                fields,
            )
            task_id = str(created.get("id") or "")
            if task_id:
                store.upsert_planning_intent(
                    intent_id=intent_id,
                    observation_id=observation_id,
                    intent_type=intent_type,
                    target_scope=target_workstream_id,
                    dedupe_key=dedupe_key,
                    spec=spec,
                    status="materialized",
                    created_task_id=task_id,
                    materialized_at=to_iso(),
                )
                created_review += 1
                applied += 1
                intent_ids.append(intent_id)
            continue
        if intent_type == "open_repair_bundle":
            source_task_id = str(spec.get("source_task_id") or "")
            if not source_task_id:
                continue
            result_payload = open_or_reuse_repair_bundle(
                workspace,
                source_task_id=source_task_id,
                anomaly=str(spec.get("anomaly") or gap_class or "planner_repair"),
                reason=str(spec.get("reason") or "planner requested repair"),
                dry_run=False,
            )
            bundle_id = str(result_payload.get("bundle_id") or "")
            if bundle_id:
                store.upsert_planning_intent(
                    intent_id=intent_id,
                    observation_id=observation_id,
                    intent_type=intent_type,
                    target_scope=source_task_id,
                    dedupe_key=dedupe_key,
                    spec=spec,
                    status="materialized",
                    created_task_id=bundle_id,
                    materialized_at=to_iso(),
                )
                applied += 1
                intent_ids.append(intent_id)
            continue
        if intent_type == "escalate_chairman":
            proposal = result.get("chairman_proposal") or {
                "proposal_type": gap_class or "needs_chairman",
                "reason": str(spec.get("reason") or "planner requested chairman input"),
                "payload": {"spec": spec},
            }
            proposal_id = _create_chairman_proposal(
                store=store,
                observation_id=observation_id,
                gap_class=str(proposal.get("proposal_type") or gap_class or "needs_chairman"),
                reason=str(proposal.get("reason") or "planner requested chairman input"),
                payload=proposal.get("payload") or {"spec": spec},
            )
            store.upsert_planning_intent(
                intent_id=intent_id,
                observation_id=observation_id,
                intent_type=intent_type,
                target_scope=target_scope,
                dedupe_key=dedupe_key,
                spec=spec,
                status="pending_chairman",
            )
            proposal_ids.append(proposal_id)
            applied += 1
            intent_ids.append(intent_id)

    return {
        "applied": applied,
        "created_leaf": created_leaf,
        "created_review": created_review,
        "proposal_ids": proposal_ids,
        "intent_ids": intent_ids,
    }
