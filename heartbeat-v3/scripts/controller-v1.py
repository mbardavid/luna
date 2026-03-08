#!/usr/bin/env python3
"""controller-v1 — single runtime owner for controller-managed tasks."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
WORKSPACE = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

from mc_control import (
    is_actionable_review_task,
    is_claim_active,
    is_execution_task,
    is_executable_leaf_task,
    is_ready_to_run,
    task_attempt,
    task_card_type,
    task_dispatch_policy,
    task_execution_owner,
    task_fields,
    task_gate_reason,
    task_lane,
    task_project_id,
    task_repair_bundle_id,
    task_repair_fingerprint,
    task_runtime_owner,
    task_status,
)
from controller_v1.health_monitor import build_health_summary
from controller_v1.chairman_adapter import ChairmanAdapter
from controller_v1.gap_evaluator import evaluate_gaps
from controller_v1.judge_adapter import JudgeAdapter
from controller_v1.mc_projection import MCProjection
from controller_v1.outcome_watcher import MilestoneObservation, observe_active_milestone
from controller_v1.planner_adapter import PlannerAdapter
from controller_v1.planner import build_autonomy_plan
from controller_v1.planning_runtime import materialize_planning_intents
from controller_v1.queue_adapter import QueueAdapter
from controller_v1.reconciler import reconcile_tasks
from controller_v1.repair_manager import (
    detect_blocked,
    open_or_reuse_repair_bundle,
    progress_repair_bundles,
    repair_children,
)
from controller_v1.runtime_store import RuntimeStore, to_iso
from controller_v1.scheduler import compute_scheduler_snapshot


LOCK_FILE = Path("/tmp/.controller-v1.lock")
STATE_DIR = WORKSPACE / "state"
DB_PATH = STATE_DIR / "controller-v1.db"
SNAPSHOT_PATH = STATE_DIR / "controller-v1-snapshot.json"
SCHEDULER_STATE_PATH = STATE_DIR / "scheduler-state.json"
AUTONOMY_RUNTIME_PATH = STATE_DIR / "autonomy-runtime.json"
OPENCLAW_CONFIG = Path(os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json"))
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
SLOT_LIMITS = {"healthy": 4, "degraded": 2, "critical": 1}
PLANNING_INTERVAL_SECONDS = 15 * 60


def run(cmd: list[str], timeout: int = 30) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "command failed")
    return proc.stdout.strip()


def load_gateway_token() -> str:
    token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
    if token:
        return token
    payload = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
    token = str(((payload.get("gateway") or {}).get("auth") or {}).get("token") or "").strip()
    if not token:
        raise RuntimeError("gateway token not found")
    return token


def gateway_call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = run(
        [
            OPENCLAW_BIN,
            "gateway",
            "call",
            "--url",
            GATEWAY_URL,
            "--token",
            load_gateway_token(),
            "--json",
            "--params",
            json.dumps(params or {}, ensure_ascii=False),
            method,
        ],
        timeout=20,
    )
    return json.loads(raw or "{}")


def sessions_by_key() -> dict[str, Any]:
    payload = gateway_call("sessions.list", {})
    sessions = []
    if isinstance(payload, dict):
        sessions = payload.get("sessions", [])
    elif isinstance(payload, list):
        sessions = payload
    return {
        str(item.get("key") or ""): item
        for item in sessions
        if isinstance(item, dict) and item.get("key")
    }


def owned_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [task for task in tasks if task_runtime_owner(task) == "controller-v1"]


def find_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any] | None:
    return next((task for task in tasks if str(task.get("id") or "") == str(task_id)), None)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def is_planning_due(store: RuntimeStore, observation: MilestoneObservation, *, force: bool = False) -> bool:
    if force:
        return True
    latest = store.latest_observation(
        project_id=str(observation.project.get("id") or ""),
        milestone_id=str(observation.milestone.get("id") or ""),
    )
    if not latest:
        return True
    previous = parse_iso(str(latest.get("observed_at") or ""))
    if not previous:
        return True
    if str(latest.get("summary_hash") or "") != observation.summary_hash:
        return True
    return (datetime.now(timezone.utc) - previous).total_seconds() >= PLANNING_INTERVAL_SECONDS


def notify_chairman_proposals(chairman: ChairmanAdapter, store: RuntimeStore, proposal_ids: list[str]) -> None:
    for proposal_id in proposal_ids:
        proposal = store.get_chairman_proposal(proposal_id)
        if not proposal:
            continue
        payload = proposal.get("payload") or {}
        text = (
            f"🧭 Chairman proposal `{proposal_id}`\n"
            f"Type: {proposal.get('proposal_type')}\n"
            f"Reason: {proposal.get('reason')}\n"
            f"Use `APPROVE_PROPOSAL {proposal_id}` or `REJECT_PROPOSAL {proposal_id} <motivo>`."
        )
        if payload.get("gap"):
            gap = payload.get("gap") or {}
            text += f"\nGap: {gap.get('gap_class')} — {gap.get('reason')}"
        chairman.reply(text)


def adopt_repair_family(
    projection: MCProjection,
    tasks: list[dict[str, Any]],
    *,
    source_task_id: str,
    bundle_id: str,
) -> None:
    family_ids = {source_task_id, bundle_id}
    for task in tasks:
        task_id = str(task.get("id") or "")
        if task_id == bundle_id:
            family_ids.add(task_id)
            continue
        fields = task_fields(task)
        if str(fields.get("mc_parent_task_id") or "") == bundle_id:
            family_ids.add(task_id)
        if str(fields.get("mc_repair_bundle_id") or "") == bundle_id:
            family_ids.add(task_id)

    for task_id in sorted(family_ids):
        task = find_task(tasks, task_id)
        if not task:
            continue
        if task_runtime_owner(task) == "controller-v1":
            continue
        updated_fields = dict(task_fields(task))
        updated_fields["mc_runtime_owner"] = "controller-v1"
        projection.update_task(task_id, fields=updated_fields)


def apply_actions(projection: MCProjection, store: RuntimeStore, tasks: list[dict[str, Any]], actions: list[dict[str, Any]]) -> int:
    applied = 0
    for action in actions:
        task_id = str(action.get("task_id") or "")
        if not task_id:
            continue
        task = find_task(tasks, task_id)
        fields = dict(task_fields(task or {}))
        fields.update(action.get("fields", {}) or {})
        fields["mc_runtime_owner"] = "controller-v1"
        changed = projection.apply_if_changed(
            store,
            task_id=task_id,
            status=action.get("status"),
            comment=action.get("comment"),
            fields=fields,
        )
        if changed:
            applied += 1
    return applied


def normalize_controller_governance(projection: MCProjection, store: RuntimeStore, tasks: list[dict[str, Any]]) -> int:
    applied = 0
    for task in tasks:
        if task_runtime_owner(task) != "controller-v1":
            continue
        if task_card_type(task) not in {"project", "milestone", "workstream", "repair_bundle"}:
            continue
        if task_status(task) != "review":
            continue
        fields = dict(task_fields(task))
        fields["mc_runtime_owner"] = "controller-v1"
        changed = projection.apply_if_changed(
            store,
            task_id=str(task.get("id") or ""),
            status="in_progress",
            comment=f"[controller-v1] normalized {task_card_type(task)} out of review into in_progress.",
            fields=fields,
        )
        if changed:
            applied += 1
    return applied


def ingest_queue_results(
    *,
    store: RuntimeStore,
    projection: MCProjection,
    queue: QueueAdapter,
    tasks: list[dict[str, Any]],
) -> int:
    applied = 0
    by_id = {str(task.get("id") or ""): task for task in tasks}
    for path, payload in queue.iter_results():
        task_id = str(payload.get("task_id") or "")
        task = by_id.get(task_id)
        if not task or task_runtime_owner(task) != "controller-v1":
            continue
        source_ref = f"queue:{Path(path).name}"
        if not store.add_event(source_ref=source_ref, event_type="queue-result", task_id=task_id, payload=payload):
            continue
        result = payload.get("result") or {}
        success = bool(payload.get("success"))
        session_key = str(result.get("session_id") or result.get("sessionKey") or "")
        agent = str(result.get("agent") or payload.get("agent") or task_execution_owner(task) or "")
        fields = dict(task_fields(task))
        fields["mc_runtime_owner"] = "controller-v1"
        if success:
            fields.update({
                "mc_session_key": session_key,
                "mc_delivery_state": "linked",
                "mc_last_error": "",
            })
            projection.apply_if_changed(
                store,
                task_id=task_id,
                status="in_progress" if task_status(task) != "review" else "review",
                comment=f"[controller-v1] queue dispatch linked session `{session_key or agent}` from `{Path(path).name}`.",
                fields=fields,
            )
            store.record_attempt(
                attempt_id=f"queue:{Path(path).name}",
                task_id=task_id,
                kind="dispatch",
                agent=agent,
                session_key=session_key,
                status="linked",
                started_at=str(payload.get("created_at") or ""),
                finished_at=str(payload.get("completed_at") or to_iso()),
                proof_ref=path,
            )
        else:
            fields.update({
                "mc_session_key": "",
                "mc_delivery_state": "queued",
                "mc_last_error": str(result.get("error") or payload.get("error") or "queue_failed"),
            })
            projection.apply_if_changed(
                store,
                task_id=task_id,
                status="inbox",
                comment=f"[controller-v1] queue dispatch failed for `{Path(path).name}`.",
                fields=fields,
            )
            store.record_attempt(
                attempt_id=f"queue:{Path(path).name}",
                task_id=task_id,
                kind="dispatch",
                agent=agent,
                session_key=session_key,
                status="failed",
                started_at=str(payload.get("created_at") or ""),
                finished_at=str(payload.get("completed_at") or to_iso()),
                proof_ref=path,
                error_class=str(result.get("error") or payload.get("error") or "queue_failed"),
            )
        applied += 1
    return applied


def ingest_judge_decisions(
    *,
    store: RuntimeStore,
    projection: MCProjection,
    judge: JudgeAdapter,
    tasks: list[dict[str, Any]],
) -> int:
    applied = 0
    by_id = {str(task.get("id") or ""): task for task in tasks}
    for path, payload in judge.iter_decisions():
        task_id = str(payload.get("task_id") or "")
        task = by_id.get(task_id)
        if not task or task_runtime_owner(task) != "controller-v1":
            continue
        source_ref = f"judge:{Path(path).name}"
        with store.connect() as conn:
            exists = conn.execute(
                "SELECT 1 FROM events WHERE source_ref = ?",
                (source_ref,),
            ).fetchone()
        if exists:
            continue
        decision = str(payload.get("decision") or "").strip().lower()
        next_status = str(payload.get("next_status") or "").strip().lower()
        if next_status not in {"done", "in_progress", "awaiting_human", "review"}:
            next_status = {
                "approve": "done",
                "reject": "in_progress",
                "awaiting_human": "awaiting_human",
            }.get(decision, "review")
        fields = dict(task_fields(task))
        raw_fields = payload.get("fields") or {}
        extra_fields: dict[str, Any] = {}
        if isinstance(raw_fields, dict):
            for key, value in raw_fields.items():
                field_key = str(key or "").strip()
                if not field_key:
                    continue
                if field_key.startswith("mc_"):
                    fields[field_key] = value
                else:
                    extra_fields[field_key] = value
        if extra_fields:
            existing_summary = str(fields.get("mc_output_summary") or "").strip()
            extras_blob = json.dumps(
                {"judge_extras": extra_fields},
                ensure_ascii=False,
                sort_keys=True,
            )
            fields["mc_output_summary"] = (
                f"{existing_summary}\n{extras_blob}" if existing_summary else extras_blob
            )
        if decision:
            fields["mc_review_reason"] = decision
        if decision in {"reject", "awaiting_human"} and payload.get("comment"):
            fields["mc_rejection_feedback"] = str(payload.get("comment") or "")
        fields["mc_runtime_owner"] = "controller-v1"
        fields["mc_proof_ref"] = path
        if next_status == "done":
            fields["mc_delivery_state"] = "done"
        projection.apply_if_changed(
            store,
            task_id=task_id,
            status=next_status,
            comment=str(payload.get("comment") or f"[controller-v1] judge decision `{decision}` from `{Path(path).name}`."),
            fields=fields,
        )
        store.record_attempt(
            attempt_id=f"judge:{Path(path).name}",
            task_id=task_id,
            kind="review",
            agent="luna-judge",
            session_key=str(fields.get("mc_session_key") or ""),
            status=decision or next_status,
            finished_at=str(payload.get("reviewed_at") or to_iso()),
            proof_ref=path,
        )
        store.add_event(source_ref=source_ref, event_type="judge-decision", task_id=task_id, payload=payload)
        applied += 1
    return applied


def maybe_open_repairs(
    *,
    store: RuntimeStore,
    projection: MCProjection,
    tasks: list[dict[str, Any]],
    sessions: dict[str, Any],
    dry_run: bool,
) -> int:
    applied = 0
    blocked = detect_blocked(tasks, sessions, workspace=WORKSPACE, stall_minutes=30)
    by_id = {str(task.get("id") or ""): task for task in tasks}
    for item in blocked:
        task_id = str(item.get("task_id") or "")
        task = by_id.get(task_id)
        if not task or task_runtime_owner(task) != "controller-v1":
            continue
        if task_gate_reason(task):
            continue
        anomaly = str(item.get("reason") or "autonomy_no_progress_timeout")
        result = open_or_reuse_repair_bundle(
            WORKSPACE,
            source_task_id=task_id,
            anomaly=anomaly,
            reason=f"controller-v1 detected stalled execution ({anomaly})",
            dry_run=dry_run,
        )
        bundle_id = str(result.get("bundle_id") or "")
        fingerprint = str(result.get("fingerprint") or f"{task_id}:{anomaly}")
        if not bundle_id:
            continue
        fields = dict(task_fields(task))
        fields.update({
            "mc_runtime_owner": "controller-v1",
            "mc_gate_reason": "repair_open",
            "mc_repair_bundle_id": bundle_id,
            "mc_repair_reason": anomaly,
            "mc_repair_fingerprint": fingerprint,
            "mc_last_error": anomaly,
            "mc_session_key": "",
            "mc_delivery_state": "queued",
            "mc_dispatch_policy": "backlog",
        })
        projection.apply_if_changed(
            store,
            task_id=task_id,
            status="inbox",
            comment=f"[controller-v1] stalled execution moved to repair bundle `{bundle_id[:8]}` ({anomaly}).",
            fields=fields,
        )
        store.set_repair(bundle_id=bundle_id, source_task_id=task_id, fingerprint=fingerprint, status="open")
        refreshed = projection.list_tasks()
        adopt_repair_family(projection, refreshed, source_task_id=task_id, bundle_id=bundle_id)
        applied += 1
    return applied


def apply_autonomy_plan(
    *,
    projection: MCProjection,
    store: RuntimeStore,
    tasks: list[dict[str, Any]],
    plan: dict[str, Any],
) -> int:
    applied = 0
    for action in plan.get("actions") or []:
        if action.get("type") == "create_leaf_task":
            fields = dict(action.get("fields") or {})
            fields["mc_runtime_owner"] = "controller-v1"
            created = projection.create_task(
                action.get("title", "(untitled)"),
                action.get("description", ""),
                action.get("assignee", ""),
                action.get("priority", "medium"),
                action.get("status", "inbox"),
                fields,
            )
            if created:
                applied += 1
        elif action.get("type") in {"promote_leaf_task", "complete_card"}:
            task_id = str(action.get("task_id") or "")
            task = find_task(tasks, task_id)
            if not task:
                continue
            fields = dict(task_fields(task))
            fields.update(action.get("fields") or {})
            fields["mc_runtime_owner"] = "controller-v1"
            changed = projection.apply_if_changed(
                store,
                task_id=task_id,
                status=action.get("status"),
                comment=action.get("comment"),
                fields=fields,
            )
            if changed:
                applied += 1
    return applied


def build_controller_snapshot(*, store: RuntimeStore, owned: list[dict[str, Any]], scheduler_snapshot: dict[str, Any],
                              autonomy_plan: dict[str, Any], health: dict[str, Any],
                              planning: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime = store.snapshot()
    return {
        "controller": {
            "last_tick": runtime.last_tick,
            "owned_tasks": runtime.controller_tasks,
            "tracked_tasks": runtime.tracked_tasks,
            "open_repairs": runtime.open_repairs,
            "attempts": runtime.attempts,
            "events": runtime.events,
            "observations": runtime.observations,
            "planning_intents": runtime.planning_intents,
            "open_proposals": runtime.open_proposals,
        },
        "health": health,
        "scheduler": scheduler_snapshot,
        "autonomy": {
            "project_id": str((autonomy_plan.get("project") or {}).get("id") or ""),
            "milestone_id": str((autonomy_plan.get("milestone") or {}).get("id") or ""),
            "workstream_ids": [str(item.get("id") or "") for item in (autonomy_plan.get("workstreams") or [])],
            "reason": autonomy_plan.get("reason", ""),
            "actions": len(autonomy_plan.get("actions") or []),
        },
        "planning": planning or {},
        "owned_by_lane": {
            lane: sum(1 for task in owned if task_lane(task) == lane)
            for lane in ("repair", "review", "project", "ambient")
        },
    }


def save_autonomy_runtime_compat(plan: dict[str, Any]) -> None:
    project = plan.get("project") or {}
    milestone = plan.get("milestone") or {}
    workstreams = plan.get("workstreams") or []
    payload = {
        "last_tick": to_iso(),
        "project_id": str(project.get("id") or ""),
        "milestone_id": str(milestone.get("id") or ""),
        "workstream_ids": [str(item.get("id") or "") for item in workstreams],
        "reason": str(plan.get("reason") or ""),
        "lane_budget": plan.get("lane_budget") or {},
        "current_window": int(plan.get("current_window", 0) or 0),
        "planned_actions": [
            {
                "type": str(action.get("type") or ""),
                "task_id": str(action.get("task_id") or ""),
                "title": str(action.get("title") or ""),
            }
            for action in (plan.get("actions") or [])
        ],
    }
    AUTONOMY_RUNTIME_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    projection = MCProjection(WORKSPACE, dry_run=args.dry_run)
    queue = QueueAdapter(WORKSPACE)
    judge = JudgeAdapter(WORKSPACE, openclaw_bin=OPENCLAW_BIN, dry_run=args.dry_run)
    planner = PlannerAdapter(WORKSPACE, openclaw_bin=OPENCLAW_BIN, dry_run=args.dry_run)
    chairman = ChairmanAdapter(WORKSPACE, openclaw_bin=OPENCLAW_BIN, config_path=OPENCLAW_CONFIG)
    store = RuntimeStore(DB_PATH)

    with LOCK_FILE.open("w", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        tasks = projection.list_tasks()
        reconcile_tasks(store, tasks)

        controller_tasks = owned_tasks(tasks)
        normalized_governance = normalize_controller_governance(projection, store, controller_tasks)
        if normalized_governance:
            tasks = projection.list_tasks()
            controller_tasks = owned_tasks(tasks)
        sessions = sessions_by_key()

        queue_ingests = ingest_queue_results(store=store, projection=projection, queue=queue, tasks=tasks)
        tasks = projection.list_tasks()
        controller_tasks = owned_tasks(tasks)

        judge_ingests = ingest_judge_decisions(store=store, projection=projection, judge=judge, tasks=tasks)
        tasks = projection.list_tasks()
        controller_tasks = owned_tasks(tasks)

        chairman_updates = chairman.poll(store)

        repair_actions = progress_repair_bundles(controller_tasks)
        apply_actions(projection, store, controller_tasks, repair_actions)
        tasks = projection.list_tasks()
        controller_tasks = owned_tasks(tasks)

        repair_opened = maybe_open_repairs(store=store, projection=projection, tasks=controller_tasks, sessions=sessions, dry_run=args.dry_run)
        tasks = projection.list_tasks()
        controller_tasks = owned_tasks(tasks)

        autonomy_plan = build_autonomy_plan(controller_tasks, slot_limits=SLOT_LIMITS)
        if (autonomy_plan.get("project") or {}).get("id"):
            lane_budget = autonomy_plan.get("lane_budget") or {}
            for workstream in autonomy_plan.get("workstreams") or []:
                store.set_project_window(
                    project_id=str((autonomy_plan.get("project") or {}).get("id") or ""),
                    milestone_id=str((autonomy_plan.get("milestone") or {}).get("id") or ""),
                    workstream_id=str(workstream.get("id") or ""),
                    slot_budget=lane_budget,
                    window_state=str(autonomy_plan.get("reason") or ""),
                )
        save_autonomy_runtime_compat(autonomy_plan)
        planner_actions = apply_autonomy_plan(projection=projection, store=store, tasks=controller_tasks, plan=autonomy_plan)
        if planner_actions:
            tasks = projection.list_tasks()
            controller_tasks = owned_tasks(tasks)

        actionable_reviews = [
            task for task in controller_tasks
            if is_actionable_review_task(task) and not is_claim_active(task)
        ]
        eligible_dispatch = [
            task for task in controller_tasks
            if is_execution_task(task)
            and task_status(task) == "inbox"
            and task_dispatch_policy(task) == "auto"
            and not task_gate_reason(task)
            and ((task_lane(task) in {"repair", "project"} and is_executable_leaf_task(task)) or is_ready_to_run(task))
        ]

        scheduler_snapshot = compute_scheduler_snapshot(
            tasks=controller_tasks,
            actionable_reviews=actionable_reviews,
            eligible_dispatch_tasks=eligible_dispatch,
            resource_level="ok",
            slot_limits=SLOT_LIMITS,
        )

        planning_summary: dict[str, Any] = dict(chairman_updates)
        observation = observe_active_milestone(tasks=tasks, scheduler_snapshot=scheduler_snapshot, workspace=WORKSPACE)
        if observation:
            directives = store.list_active_chairman_directives(project_id=str(observation.project.get("id") or ""))
            if is_planning_due(
                store,
                observation,
                force=bool(chairman_updates.get("new_directives") or chairman_updates.get("proposal_updates")),
            ):
                store.insert_observation(
                    observation_id=observation.observation_id,
                    project_id=str(observation.project.get("id") or ""),
                    milestone_id=str(observation.milestone.get("id") or ""),
                    observed_at=observation.observed_at,
                    outcome=observation.outcome,
                    artifacts=observation.artifacts,
                    freshness=observation.freshness,
                    scheduler=scheduler_snapshot,
                    summary_hash=observation.summary_hash,
                )
                gaps = evaluate_gaps(observation, directives)
                store.replace_gap_evaluations(observation_id=observation.observation_id, gaps=gaps)
                planner_result = planner.propose(
                    observation={
                        "observation_id": observation.observation_id,
                        "project": observation.project,
                        "milestone": observation.milestone,
                        "workstreams": observation.workstreams,
                        "outcome": observation.outcome,
                        "artifacts": observation.artifacts,
                        "freshness": observation.freshness,
                    },
                    gaps=gaps,
                    directives=directives,
                )
                planning_apply = materialize_planning_intents(
                    store=store,
                    projection=projection,
                    workspace=WORKSPACE,
                    observation={
                        "observation_id": observation.observation_id,
                        "project": observation.project,
                        "milestone": observation.milestone,
                        "workstreams": observation.workstreams,
                    },
                    gaps=gaps,
                    result=planner_result,
                    tasks=tasks,
                    dry_run=args.dry_run,
                )
                if planning_apply.get("proposal_ids"):
                    notify_chairman_proposals(chairman, store, planning_apply.get("proposal_ids") or [])
                planning_summary.update(
                    {
                        "observation_id": observation.observation_id,
                        "gap_count": len(gaps),
                        "planner_summary": planner_result.get("observation_summary", ""),
                        "applied_intents": planning_apply.get("applied", 0),
                        "proposal_ids": planning_apply.get("proposal_ids", []),
                    }
                )
                tasks = projection.list_tasks()
                controller_tasks = owned_tasks(tasks)
            else:
                planning_summary.update(
                    {
                        "observation_id": observation.observation_id,
                        "skipped": True,
                        "reason": "planning_not_due",
                    }
                )

        dispatches = 0
        decision = scheduler_snapshot.get("dispatch_decision") or {}
        decision_type = str(decision.get("type") or "idle")
        decision_task_id = str(decision.get("task_id") or "")
        if decision_type == "review" and decision_task_id:
            review_task = find_task(actionable_reviews, decision_task_id) or find_task(controller_tasks, decision_task_id)
            if review_task:
                session_key, decision_path = judge.dispatch_review(review_task)
                fields = dict(task_fields(review_task))
                fields.update({
                    "mc_runtime_owner": "controller-v1",
                    "mc_session_key": session_key,
                    "mc_review_agent": "luna-judge",
                })
                projection.apply_if_changed(
                    store,
                    task_id=decision_task_id,
                    status="review",
                    comment=f"[controller-v1] review dispatched to luna-judge; decision file `{decision_path}`.",
                    fields=fields,
                )
                store.record_attempt(
                    attempt_id=f"review-dispatch:{decision_task_id}:{int(datetime.now(timezone.utc).timestamp())}",
                    task_id=decision_task_id,
                    kind="review",
                    agent="luna-judge",
                    session_key=session_key,
                    status="dispatched",
                    started_at=to_iso(),
                    proof_ref=str(decision_path),
                )
                dispatches += 1
        elif decision_type == "dispatch" and decision_task_id:
            next_task = find_task(eligible_dispatch, decision_task_id) or find_task(controller_tasks, decision_task_id)
            if next_task:
                owner = task_execution_owner(next_task)
                if owner == "main":
                    fields = dict(task_fields(next_task))
                    fields.update({
                        "mc_runtime_owner": "controller-v1",
                        "mc_gate_reason": "main_dispatch_blocked",
                        "mc_last_error": "main_dispatch_blocked",
                    })
                    projection.apply_if_changed(
                        store,
                        task_id=decision_task_id,
                        status="awaiting_human",
                        comment="[controller-v1] auto-dispatch to main is blocked by design.",
                        fields=fields,
                    )
                else:
                    queue_path = queue.write_dispatch_item(next_task)
                    if queue_path:
                        fields = dict(task_fields(next_task))
                        fields.update({
                            "mc_runtime_owner": "controller-v1",
                            "mc_delivery_state": "dispatched",
                            "mc_last_error": "",
                            "mc_attempt": max(task_attempt(next_task), 0) + 1,
                        })
                        projection.apply_if_changed(
                            store,
                            task_id=decision_task_id,
                            status="in_progress",
                            comment=f"[controller-v1] queued dispatch to `{owner}` via `{Path(queue_path).name}`.",
                            fields=fields,
                        )
                        store.record_attempt(
                            attempt_id=f"queue-dispatch:{Path(queue_path).name}",
                            task_id=decision_task_id,
                            kind="dispatch",
                            agent=owner,
                            status="queued",
                            started_at=to_iso(),
                            proof_ref=queue_path,
                        )
                        dispatches += 1

        health = build_health_summary(
            owned_tasks=len(controller_tasks),
            dispatches=dispatches,
            queue_ingests=queue_ingests,
            judge_ingests=judge_ingests,
        )
        snapshot = build_controller_snapshot(
            store=store,
            owned=controller_tasks,
            scheduler_snapshot=scheduler_snapshot,
            autonomy_plan=autonomy_plan,
            health=health,
            planning=planning_summary,
        )
        SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        scheduler_payload = dict(scheduler_snapshot)
        scheduler_payload["generated_at"] = to_iso()
        SCHEDULER_STATE_PATH.write_text(json.dumps(scheduler_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
