#!/usr/bin/env python3
"""Validate layered autonomy architecture invariants on the live control plane."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(ROOT))

from mc_control import (
    is_actionable_review_task,
    is_claim_active,
    is_governance_card,
    task_card_type,
    task_chairman_state,
    task_dispatch_policy,
    task_fields,
    task_gate_reason,
    task_lane,
    task_milestone_id,
    task_project_id,
    task_repair_bundle_id,
    task_review_agent,
    task_status,
)
from project_autonomy import select_active_milestone, select_active_project, select_active_workstreams

MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
DEFAULT_JSON_OUTPUT = WORKSPACE / "state" / "autonomy-architecture-validation.json"
DEFAULT_MD_OUTPUT = WORKSPACE / "artifacts" / "reports" / "autonomy-architecture-validation-latest.md"
SCHEDULER_STATE_FILE = WORKSPACE / "state" / "scheduler-state.json"
METRICS_FILE = WORKSPACE / "state" / "control-loop-metrics.json"
AUTONOMY_RUNTIME_FILE = WORKSPACE / "state" / "autonomy-runtime.json"
DEFAULT_BOARD_PACKET = WORKSPACE / "artifacts" / "reports" / "autonomy-board-packet-latest.md"
PYTEST_FILES = (
    "heartbeat-v3/tests/test_scheduler_v2.py",
    "heartbeat-v3/tests/test_incident_replays.py",
    "heartbeat-v3/tests/test_mc_fast_dispatch.py",
    "heartbeat-v3/tests/test_validate_autonomy_architecture.py",
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None = None) -> str:
    current = dt or utcnow()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("id") or "").strip()


def _task_title(task: dict[str, Any]) -> str:
    return str(task.get("title") or "(untitled)").strip()


def _task_session_key(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_session_key") or "").strip()


def _metric_counter(metrics: dict[str, Any] | None, key: str) -> int:
    if not metrics:
        return 0
    counters = metrics.get("counters_today")
    if isinstance(counters, dict):
        try:
            return int(counters.get(key) or 0)
        except Exception:
            return 0
    try:
        return int(metrics.get(key) or 0)
    except Exception:
        return 0


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_tasks() -> list[dict[str, Any]]:
    raw = subprocess.run(
        [str(MC_CLIENT), "list-tasks"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    payload = json.loads(raw or "{}")
    if isinstance(payload, dict):
        items = payload.get("items", [])
        return items if isinstance(items, list) else []
    return payload if isinstance(payload, list) else []


def _status_check(check_id: str, title: str, status: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "summary": summary,
        "evidence": evidence or [],
    }


def _pass(check_id: str, title: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return _status_check(check_id, title, "PASS", summary, evidence)


def _warn(check_id: str, title: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return _status_check(check_id, title, "WARN", summary, evidence)


def _fail(check_id: str, title: str, summary: str, evidence: list[str] | None = None) -> dict[str, Any]:
    return _status_check(check_id, title, "FAIL", summary, evidence)


def resolve_artifact_paths(project: dict[str, Any] | None, *, board_packet_path: Path | None = None) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    if board_packet_path:
        paths["board_packet"] = board_packet_path
    else:
        paths["board_packet"] = DEFAULT_BOARD_PACKET
    if not project:
        return paths
    outcome_ref = str(task_fields(project).get("mc_outcome_ref") or "").strip()
    if outcome_ref:
        outcome_path = Path(outcome_ref)
        if not outcome_path.is_absolute():
            outcome_path = WORKSPACE / outcome_path
        paths["outcome"] = outcome_path
        if outcome_path.parent:
            sibling_dir = outcome_path.parent
            for name in ("session-health-latest.json", "baseline-latest.json", "board-packet-latest.md"):
                paths[name] = sibling_dir / name
    return paths


def _path_fresh(path: Path, *, max_age_minutes: int, now: datetime | None = None) -> tuple[bool, int | None]:
    if not path.exists():
        return False, None
    current = now or utcnow()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    age_minutes = int((current - mtime).total_seconds() // 60)
    return age_minutes <= max_age_minutes, age_minutes


def run_pytest_validation() -> dict[str, Any]:
    cmd = [sys.executable, "-m", "pytest", *PYTEST_FILES, "-q"]
    proc = subprocess.run(
        cmd,
        cwd=WORKSPACE,
        capture_output=True,
        text=True,
    )
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    combined = "\n".join(part for part in (stdout, stderr) if part).strip()
    excerpt = "\n".join(combined.splitlines()[-10:]) if combined else ""
    return {
        "command": cmd,
        "returncode": proc.returncode,
        "passed": proc.returncode == 0,
        "summary": excerpt or "pytest produced no output",
    }


def evaluate_autonomy_architecture(
    tasks: list[dict[str, Any]],
    *,
    scheduler_state: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    autonomy_runtime: dict[str, Any] | None = None,
    artifact_paths: dict[str, Path] | None = None,
    pytest_result: dict[str, Any] | None = None,
    max_state_age_minutes: int = 20,
    max_artifact_age_minutes: int = 24 * 60,
) -> dict[str, Any]:
    scheduler_state = scheduler_state or {}
    metrics = metrics or {}
    autonomy_runtime = autonomy_runtime or {}
    now = utcnow()
    checks: list[dict[str, Any]] = []

    project = select_active_project(tasks)
    milestone = select_active_milestone(tasks, project)
    workstreams = select_active_workstreams(tasks, project, milestone, max_active_workstreams=10) if milestone else []
    artifact_paths = artifact_paths or resolve_artifact_paths(project)
    project_id = _task_id(project) if project else ""
    milestone_id = _task_id(milestone) if milestone else ""
    workstream_ids = {_task_id(item) for item in workstreams}

    if project:
        checks.append(
            _pass(
                "active_project_present",
                "Active project detected",
                f"Active project `{project_id[:8]}` with chairman_state=`{task_chairman_state(project)}`.",
                [f"project={_task_title(project)}", f"milestone={_task_title(milestone) if milestone else '(none)'}"],
            )
        )
    else:
        checks.append(
            _fail(
                "active_project_present",
                "Active project detected",
                "No autonomous project is active in Mission Control.",
            )
        )

    governance_in_review = [
        task for task in tasks
        if is_governance_card(task) and task_status(task) == "review"
    ]
    if governance_in_review:
        checks.append(
            _fail(
                "governance_not_in_review",
                "Governance stays out of review",
                f"{len(governance_in_review)} governance card(s) are incorrectly in review.",
                [f"{_task_id(task)[:8]} {_task_title(task)}" for task in governance_in_review[:10]],
            )
        )
    else:
        checks.append(_pass("governance_not_in_review", "Governance stays out of review", "No governance card is currently in `review`."))

    governance_claimed = [
        task for task in tasks
        if is_governance_card(task) and is_claim_active(task)
    ]
    if governance_claimed:
        checks.append(
            _fail(
                "governance_not_claimed",
                "Governance is never claim-driven",
                f"{len(governance_claimed)} governance card(s) still have active claims.",
                [f"{_task_id(task)[:8]} {_task_title(task)}" for task in governance_claimed[:10]],
            )
        )
    else:
        checks.append(_pass("governance_not_claimed", "Governance is never claim-driven", "No governance card has an active lease/claim."))

    invalid_review = [
        task for task in tasks
        if task_status(task) == "review" and not is_actionable_review_task(task)
    ]
    if invalid_review:
        checks.append(
            _fail(
                "review_queue_actionable_only",
                "Review queue contains only actionable work",
                f"{len(invalid_review)} review card(s) are non-actionable.",
                [f"{_task_id(task)[:8]} {_task_title(task)} ({task_card_type(task)})" for task in invalid_review[:10]],
            )
        )
    else:
        checks.append(_pass("review_queue_actionable_only", "Review queue contains only actionable work", "Every card in `review` is actionable by the judge/runtime."))

    judge_legacy = _metric_counter(metrics, "judge_dispatch_main_legacy")
    if judge_legacy > 0:
        checks.append(
            _fail(
                "judge_not_using_main",
                "Judge runtime never falls back to main",
                f"`judge_dispatch_main_legacy` is {judge_legacy}.",
            )
        )
    else:
        checks.append(_pass("judge_not_using_main", "Judge runtime never falls back to main", "No legacy judge dispatch to `main` was recorded."))

    review_on_main = [
        task for task in tasks
        if task_status(task) == "review"
        and _task_session_key(task).startswith("agent:main:")
    ]
    if review_on_main:
        checks.append(
            _fail(
                "review_sessions_not_on_main",
                "Review sessions avoid main",
                f"{len(review_on_main)} review card(s) are still linked to `main`.",
                [f"{_task_id(task)[:8]} {_task_title(task)} | session={_task_session_key(task)}" for task in review_on_main[:10]],
            )
        )
    else:
        checks.append(_pass("review_sessions_not_on_main", "Review sessions avoid main", "No review card is linked to `main`."))

    auto_main_tasks = [
        task for task in tasks
        if task_card_type(task) == "leaf_task"
        and task_dispatch_policy(task) == "auto"
        and task_status(task) == "in_progress"
        and _task_session_key(task).startswith("agent:main:")
    ]
    if auto_main_tasks:
        checks.append(
            _fail(
                "auto_dispatch_not_on_main",
                "Auto execution never lands on main",
                f"{len(auto_main_tasks)} auto-dispatch task(s) are currently linked to `main`.",
                [f"{_task_id(task)[:8]} {_task_title(task)} | lane={task_lane(task)}" for task in auto_main_tasks[:10]],
            )
        )
    else:
        checks.append(_pass("auto_dispatch_not_on_main", "Auto execution never lands on main", "No auto-dispatched execution is linked to `main`."))

    scheduler_tick = parse_iso(str(scheduler_state.get("last_tick") or ""))
    heartbeat_health = metrics.get("cron_health") or {}
    heartbeat_last_run = parse_iso(
        str(
            ((heartbeat_health.get("heartbeat-v3") or {}).get("last_run"))
            or metrics.get("last_updated")
            or ""
        )
    )
    heartbeat_age_minutes = int((now - heartbeat_last_run).total_seconds() // 60) if heartbeat_last_run else None
    if not scheduler_tick:
        if heartbeat_last_run and heartbeat_age_minutes is not None and heartbeat_age_minutes <= max_state_age_minutes:
            checks.append(
                _warn(
                    "scheduler_state_fresh",
                    "Scheduler state is fresh",
                    (
                        "Scheduler snapshot is missing `last_tick`, but heartbeat-v3 ran recently. "
                        "This usually means an early-exit path refreshed liveness before the full scheduler snapshot."
                    ),
                    [
                        f"heartbeat_last_run={to_iso(heartbeat_last_run)}",
                        f"heartbeat_age_minutes={heartbeat_age_minutes}",
                    ],
                )
            )
        else:
            checks.append(_fail("scheduler_state_fresh", "Scheduler state is fresh", "Scheduler state is missing `last_tick`."))
    else:
        age_minutes = int((now - scheduler_tick).total_seconds() // 60)
        if age_minutes > max_state_age_minutes:
            if heartbeat_last_run and heartbeat_age_minutes is not None and heartbeat_age_minutes <= max_state_age_minutes:
                checks.append(
                    _warn(
                        "scheduler_state_fresh",
                        "Scheduler state is fresh",
                        (
                            f"Scheduler state is stale at {age_minutes} minute(s), but heartbeat-v3 ran "
                            f"{heartbeat_age_minutes} minute(s) ago. This points to an early-exit path, "
                            "not a dead scheduler."
                        ),
                        [
                            f"last_tick={scheduler_state.get('last_tick')}",
                            f"heartbeat_last_run={to_iso(heartbeat_last_run)}",
                        ],
                    )
                )
            else:
                checks.append(
                    _fail(
                        "scheduler_state_fresh",
                        "Scheduler state is fresh",
                        f"Scheduler state is stale at {age_minutes} minute(s).",
                        [f"last_tick={scheduler_state.get('last_tick')}"],
                    )
                )
        else:
            checks.append(
                _pass(
                    "scheduler_state_fresh",
                    "Scheduler state is fresh",
                    f"Scheduler state updated {age_minutes} minute(s) ago.",
                )
            )

    scheduler_mode = str(scheduler_state.get("mode") or "").strip()
    if scheduler_mode in {"review_repair", "project", "full"}:
        checks.append(_pass("scheduler_mode_live", "Scheduler v2 is active", f"Scheduler mode is `{scheduler_mode}`."))
    else:
        checks.append(_fail("scheduler_mode_live", "Scheduler v2 is active", f"Scheduler mode `{scheduler_mode or 'unknown'}` is not a live cutover mode."))

    eligible_by_lane = scheduler_state.get("eligible_by_lane") or {}
    running_by_lane = scheduler_state.get("running_by_lane") or {}
    reserved_slots = scheduler_state.get("reserved_slots") or {}
    dispatch_decision = scheduler_state.get("dispatch_decision") or {}
    total_running = sum(int(running_by_lane.get(lane, 0) or 0) for lane in ("repair", "review", "project", "ambient"))

    repair_eligible = int(eligible_by_lane.get("repair", 0) or 0)
    repair_running = int(running_by_lane.get("repair", 0) or 0)
    repair_reserved = int(reserved_slots.get("repair", 0) or 0)
    repair_dispatch = str(dispatch_decision.get("lane") or "") == "repair"
    if repair_eligible <= 0 and repair_running <= 0:
        checks.append(_pass("repair_lane_served", "Repair lane receives capacity", "No repair demand is currently waiting."))
    elif repair_reserved <= 0:
        checks.append(
            _fail(
                "repair_lane_served",
                "Repair lane receives capacity",
                "Repair work is waiting but the scheduler reserved zero repair slots.",
                [json.dumps({"eligible": repair_eligible, "running": repair_running, "reserved": repair_reserved}, sort_keys=True)],
            )
        )
    elif repair_running > 0 or repair_dispatch or (total_running >= int(scheduler_state.get("slots_total", 0) or 0) and repair_running > 0):
        checks.append(
            _pass(
                "repair_lane_served",
                "Repair lane receives capacity",
                "Repair work has live capacity via running tasks or the current dispatch decision.",
                [json.dumps({"eligible": repair_eligible, "running": repair_running, "dispatch": dispatch_decision}, sort_keys=True)],
            )
        )
    else:
        checks.append(
            _fail(
                "repair_lane_served",
                "Repair lane receives capacity",
                "Repair work is eligible but neither running nor selected for dispatch.",
                [json.dumps({"eligible": repair_eligible, "running": repair_running, "dispatch": dispatch_decision}, sort_keys=True)],
            )
        )

    project_eligible = int(eligible_by_lane.get("project", 0) or 0)
    project_running = int(running_by_lane.get("project", 0) or 0)
    project_reserved = int(reserved_slots.get("project", 0) or 0)
    ambient_running = int(running_by_lane.get("ambient", 0) or 0)
    if project_eligible > 0 and ambient_running > 0:
        if project_running > 0 or project_reserved > 0 or str(dispatch_decision.get("lane") or "") == "project":
            checks.append(
                _pass(
                    "project_lane_coexists_with_ambient",
                    "Project lane coexists with ambient",
                    "Ambient execution is active and the project lane still retains reserved capacity.",
                    [json.dumps({"project_reserved": project_reserved, "project_running": project_running, "ambient_running": ambient_running}, sort_keys=True)],
                )
            )
        else:
            checks.append(
                _fail(
                    "project_lane_coexists_with_ambient",
                    "Project lane coexists with ambient",
                    "Ambient execution is active but the project lane has no reserved capacity.",
                    [json.dumps({"project_reserved": project_reserved, "project_running": project_running, "ambient_running": ambient_running}, sort_keys=True)],
                )
            )
    else:
        checks.append(
            _warn(
                "project_lane_coexists_with_ambient",
                "Project lane coexists with ambient",
                "Current snapshot does not exercise ambient+project contention at the same time.",
                [json.dumps({"project_eligible": project_eligible, "ambient_running": ambient_running}, sort_keys=True)],
            )
        )

    runtime_project_id = str(autonomy_runtime.get("project_id") or "").strip()
    runtime_milestone_id = str(autonomy_runtime.get("milestone_id") or "").strip()
    runtime_workstreams = {str(item).strip() for item in (autonomy_runtime.get("workstream_ids") or []) if str(item).strip()}
    runtime_evidence = [
        f"runtime_project={runtime_project_id[:8]}",
        f"runtime_milestone={runtime_milestone_id[:8]}",
        f"runtime_workstreams={','.join(sorted(item[:8] for item in runtime_workstreams)) or '(none)'}",
    ]
    if not project:
        checks.append(_fail("autonomy_runtime_consistent", "Autonomy runtime matches MC active scope", "No active project exists to compare against runtime state.", runtime_evidence))
    elif runtime_project_id != project_id or (milestone_id and runtime_milestone_id != milestone_id) or (workstream_ids and not runtime_workstreams.issubset(workstream_ids)):
        checks.append(
            _fail(
                "autonomy_runtime_consistent",
                "Autonomy runtime matches MC active scope",
                "Runtime state does not match the active project/milestone/workstream selection in Mission Control.",
                runtime_evidence + [f"mc_project={project_id[:8]}", f"mc_milestone={milestone_id[:8]}"],
            )
        )
    else:
        checks.append(_pass("autonomy_runtime_consistent", "Autonomy runtime matches MC active scope", "Runtime state matches the active project, milestone and workstream window.", runtime_evidence))

    open_repairs = {
        _task_id(task): task
        for task in tasks
        if task_card_type(task) == "repair_bundle" and task_status(task) not in {"done", "failed"}
    }
    repair_integrity_failures: list[str] = []
    for bundle_id, bundle in open_repairs.items():
        children = [task for task in tasks if task_repair_bundle_id(task) == bundle_id]
        child_leaf = [task for task in children if task_card_type(task) == "leaf_task"]
        child_review = [task for task in children if task_card_type(task) == "review_bundle"]
        active_children = [task for task in children if task_status(task) not in {"done", "failed"}]
        if not child_leaf or not child_review or not active_children:
            repair_integrity_failures.append(
                f"{bundle_id[:8]} {_task_title(bundle)} | leaf={len(child_leaf)} review={len(child_review)} active={len(active_children)}"
            )
    if repair_integrity_failures:
        checks.append(
            _fail(
                "repair_bundles_integrity",
                "Repair bundles stay executable",
                f"{len(repair_integrity_failures)} open repair bundle(s) are missing executable children or live work.",
                repair_integrity_failures[:10],
            )
        )
    else:
        checks.append(_pass("repair_bundles_integrity", "Repair bundles stay executable", f"All {len(open_repairs)} open repair bundle(s) have executable children and live work."))

    gated_sources = [
        task for task in tasks
        if task_gate_reason(task) == "repair_open"
    ]
    gated_failures: list[str] = []
    for task in gated_sources:
        bundle_id = task_repair_bundle_id(task)
        if not bundle_id or bundle_id not in open_repairs:
            gated_failures.append(f"{_task_id(task)[:8]} {_task_title(task)} -> bundle={bundle_id[:8] or '(missing)'}")
    if gated_failures:
        checks.append(
            _fail(
                "repair_gates_valid",
                "Repair-gated tasks point to live bundles",
                f"{len(gated_failures)} gated task(s) do not reference an open repair bundle.",
                gated_failures[:10],
            )
        )
    else:
        checks.append(_pass("repair_gates_valid", "Repair-gated tasks point to live bundles", f"All {len(gated_sources)} repair-gated task(s) reference open repair bundles."))

    artifact_failures: list[str] = []
    artifact_warnings: list[str] = []
    required_names = ("board_packet", "outcome")
    for name in required_names:
        path = artifact_paths.get(name)
        if not path:
            artifact_failures.append(f"{name}: not configured")
            continue
        fresh, age = _path_fresh(path, max_age_minutes=max_artifact_age_minutes, now=now)
        if not path.exists():
            artifact_failures.append(f"{name}: missing at {path}")
        elif not fresh:
            artifact_failures.append(f"{name}: stale ({age} min) at {path}")
    for name in ("session-health-latest.json", "baseline-latest.json", "board-packet-latest.md"):
        path = artifact_paths.get(name)
        if not path:
            continue
        fresh, age = _path_fresh(path, max_age_minutes=max_artifact_age_minutes, now=now)
        if not path.exists():
            artifact_warnings.append(f"{name}: missing at {path}")
        elif not fresh:
            artifact_warnings.append(f"{name}: stale ({age} min) at {path}")
    if artifact_failures:
        checks.append(_fail("project_artifacts_fresh", "Project artifacts exist and are fresh", f"{len(artifact_failures)} required artifact problem(s) detected.", artifact_failures + artifact_warnings))
    elif artifact_warnings:
        checks.append(_warn("project_artifacts_fresh", "Project artifacts exist and are fresh", "Required artifacts are healthy, but optional project artifacts are missing or stale.", artifact_warnings))
    else:
        checks.append(_pass("project_artifacts_fresh", "Project artifacts exist and are fresh", "Required board/outcome artifacts are present and fresh."))

    if pytest_result:
        if pytest_result.get("passed"):
            checks.append(
                _pass(
                    "pytest_replays",
                    "Replay and scheduler tests pass",
                    "The validation pytest subset passed.",
                    [pytest_result.get("summary", "")],
                )
            )
        else:
            checks.append(
                _fail(
                    "pytest_replays",
                    "Replay and scheduler tests pass",
                    "The validation pytest subset failed.",
                    [pytest_result.get("summary", "")],
                )
            )

    fail_count = sum(1 for item in checks if item["status"] == "FAIL")
    warn_count = sum(1 for item in checks if item["status"] == "WARN")
    pass_count = sum(1 for item in checks if item["status"] == "PASS")
    overall_status = "FAIL" if fail_count else "PASS"
    return {
        "generated_at": to_iso(now),
        "overall_status": overall_status,
        "summary": {
            "passed": pass_count,
            "warnings": warn_count,
            "failed": fail_count,
            "active_project_id": project_id,
            "active_milestone_id": milestone_id,
        },
        "active_project": {
            "id": project_id,
            "title": _task_title(project) if project else "",
            "milestone_id": milestone_id,
            "milestone_title": _task_title(milestone) if milestone else "",
            "workstream_ids": sorted(workstream_ids),
            "default_review_agent": task_review_agent(project) if project else "luna-judge",
        },
        "scheduler": scheduler_state,
        "autonomy_runtime": autonomy_runtime,
        "artifacts": {name: str(path) for name, path in artifact_paths.items()},
        "checks": checks,
        "pytest": pytest_result,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    project = report.get("active_project") or {}
    scheduler = report.get("scheduler") or {}
    checks = report.get("checks") or []
    failed = [item for item in checks if item.get("status") == "FAIL"]
    warnings = [item for item in checks if item.get("status") == "WARN"]
    passed = [item for item in checks if item.get("status") == "PASS"]
    lines = [
        "# Autonomy Architecture Validation",
        f"Generated: {report.get('generated_at', '')}",
        "",
        f"## Overall: `{report.get('overall_status', 'FAIL')}`",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Warnings: {summary.get('warnings', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        f"- Active project: `{str(project.get('id') or '')[:8]}` {project.get('title') or '(none)'}",
        f"- Active milestone: `{str(project.get('milestone_id') or '')[:8]}` {project.get('milestone_title') or '(none)'}",
        "",
        "## Scheduler Snapshot",
        f"- Mode: `{scheduler.get('mode', 'unknown')}` | health=`{scheduler.get('health_state', 'unknown')}` | slots={scheduler.get('slots_total', 0)}",
        f"- Reserved slots: `{json.dumps(scheduler.get('reserved_slots', {}), sort_keys=True)}`",
        f"- Running by lane: `{json.dumps(scheduler.get('running_by_lane', {}), sort_keys=True)}`",
        f"- Eligible by lane: `{json.dumps(scheduler.get('eligible_by_lane', {}), sort_keys=True)}`",
        f"- Dispatch decision: `{json.dumps(scheduler.get('dispatch_decision', {}), sort_keys=True)}`",
        "",
        "## Failed Checks",
    ]
    lines.extend(_render_check_lines(failed) or ["- None."])
    lines.extend(["", "## Warnings"])
    lines.extend(_render_check_lines(warnings) or ["- None."])
    lines.extend(["", "## Passed Checks"])
    lines.extend(_render_check_lines(passed) or ["- None."])
    pytest_result = report.get("pytest") or {}
    if pytest_result:
        lines.extend(
            [
                "",
                "## Pytest Validation",
                f"- Passed: `{bool(pytest_result.get('passed'))}`",
                f"- Command: `{' '.join(pytest_result.get('command') or [])}`",
                "```text",
                str(pytest_result.get("summary") or "").strip(),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def _render_check_lines(checks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for check in checks:
        lines.append(f"- `{check.get('id')}` {check.get('title')}: {check.get('summary')}")
        for evidence in check.get("evidence") or []:
            lines.append(f"  - {evidence}")
    return lines


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-out", default=str(DEFAULT_JSON_OUTPUT))
    parser.add_argument("--md-out", default=str(DEFAULT_MD_OUTPUT))
    parser.add_argument("--skip-pytest", action="store_true")
    parser.add_argument("--max-state-age-minutes", type=int, default=20)
    parser.add_argument("--max-artifact-age-minutes", type=int, default=24 * 60)
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    tasks = load_tasks()
    scheduler_state = _load_json(SCHEDULER_STATE_FILE) if SCHEDULER_STATE_FILE.exists() else {}
    metrics = _load_json(METRICS_FILE) if METRICS_FILE.exists() else {}
    autonomy_runtime = _load_json(AUTONOMY_RUNTIME_FILE) if AUTONOMY_RUNTIME_FILE.exists() else {}
    pytest_result = None if args.skip_pytest else run_pytest_validation()
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=scheduler_state,
        metrics=metrics,
        autonomy_runtime=autonomy_runtime,
        pytest_result=pytest_result,
        max_state_age_minutes=args.max_state_age_minutes,
        max_artifact_age_minutes=args.max_artifact_age_minutes,
    )

    json_out = Path(args.json_out)
    md_out = Path(args.md_out)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_out.write_text(render_markdown(report), encoding="utf-8")

    print(json.dumps({"overall_status": report["overall_status"], "json_out": str(json_out), "md_out": str(md_out)}, ensure_ascii=False))
    return 0 if report["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
