#!/usr/bin/env python3
"""
mc-simple-drain.py — deterministic, low-complexity selector for MC auto-drain.

Purpose:
  - Keep the critical path tiny.
  - Choose exactly one next task using canonical order:
      review -> inbox(with deps satisfied) -> FIFO
  - Avoid dispatch when any execution is already in progress.
  - Avoid duplicate queue items.
  - Write a single queue item to heartbeat-v3/queue/pending/.

This script intentionally DOES NOT:
  - talk to Discord
  - poll external channels
  - spawn sessions directly
  - do recovery (watchdog owns recovery)

The queue item can be consumed later by existing dispatch flows.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


WORKSPACE = os.environ.get("WORKSPACE", "/home/openclaw/.openclaw/workspace")
V3_DIR = os.environ.get("HEARTBEAT_V3_DIR", os.path.join(WORKSPACE, "heartbeat-v3"))
QUEUE_DIR = Path(V3_DIR) / "queue"
PENDING = QUEUE_DIR / "pending"
ACTIVE = QUEUE_DIR / "active"
DONE = QUEUE_DIR / "done"
FAILED = QUEUE_DIR / "failed"
LOCK_FILE = os.environ.get("MC_SIMPLE_DRAIN_LOCK", "/tmp/mc-simple-drain.lock")
LOG_FILE = os.path.join(WORKSPACE, "logs", "mc-simple-drain.log")
MC_CLIENT = os.environ.get("MC_CLIENT", os.path.join(WORKSPACE, "scripts", "mc-client.sh"))
MAX_ACTIVE_QUEUE = int(os.environ.get("MC_SIMPLE_DRAIN_MAX_ACTIVE_QUEUE", "1"))
# Allow up to 2 execution tasks in_progress before blocking inbox drain.
# Review tasks handled by judge-loop do NOT count against this limit.
MAX_IN_PROGRESS = int(os.environ.get("MC_SIMPLE_DRAIN_MAX_IN_PROGRESS", "2"))
DRY_RUN = "--dry-run" in sys.argv
VERBOSE = "--verbose" in sys.argv or DRY_RUN
EMIT_JSON = "--json" in sys.argv


for d in (PENDING, ACTIVE, DONE, FAILED, Path(LOG_FILE).parent):
    d.mkdir(parents=True, exist_ok=True)


def log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    if VERBOSE:
        print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def run_cmd(cmd: list[str], timeout: int = 20) -> str:
    import subprocess

    cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or f"command failed: {' '.join(cmd)}")
    return cp.stdout.strip()


def list_tasks() -> list[dict[str, Any]]:
    raw = run_cmd([MC_CLIENT, "list-tasks"], timeout=20)
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", []) or []
    return []


def task_status(task: dict[str, Any]) -> str:
    return str(task.get("status", "") or "").lower().strip()


def task_fields(task: dict[str, Any]) -> dict[str, Any]:
    fields = task.get("custom_field_values") or {}
    return fields if isinstance(fields, dict) else {}


def normalize_status(status: str, default: str = "inbox") -> str:
    value = str(status or "").strip().lower()
    return value or default


def queue_phase(item_type: str, task: dict[str, Any]) -> str:
    if item_type == "respawn":
        return "respawn"
    fields = task_fields(task)
    workflow = str(fields.get("mc_workflow", "") or "").strip().lower()
    phase = str(fields.get("mc_phase", "") or "").strip().lower()
    if workflow == "direct_exec" and phase in {"", "intake", "inbox"}:
        return "direct_exec"
    return phase or item_type


def queue_key(task_id: str, item_type: str, status: str, phase: str) -> str:
    raw = "|".join([
        str(task_id or "").strip(),
        str(item_type or "").strip().lower(),
        normalize_status(status, default="inbox"),
        str(phase or "").strip().lower(),
    ])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{raw}|{digest}"


def queue_item_exists(task_id: str, key: str, directories: list[Path]) -> bool:
    for directory in directories:
        for path in directory.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            payload_task_id = str(payload.get("task_id", "") or "")
            payload_key = str(payload.get("queue_key", "") or "")
            if payload_task_id != str(task_id):
                continue
            # Strong dedupe: exact queue_key match OR same task still present in queue.
            # This prevents stale legacy keys from re-dispatching the same task forever.
            if payload_key == key or payload_task_id == str(task_id):
                return True
    return False


def dependencies_satisfied(task: dict[str, Any], status_by_id: dict[str, str]) -> bool:
    deps = task.get("depends_on_task_ids", []) or []
    for dep in deps:
        if status_by_id.get(str(dep), "") != "done":
            return False
    return True


def auto_dispatch_allowed(task: dict[str, Any]) -> bool:
    """Returns True only if the task may be auto-dispatched."""
    fields = task_fields(task)
    policy = str(fields.get("mc_dispatch_policy", "") or "").strip().lower()
    card_type = str(fields.get("mc_card_type", "") or "").strip().lower()
    # Block tasks that explicitly require human approval or are backlog/milestone/project goals.
    if policy in ("human_hold", "human_approval", "manual", "hold", "backlog"):
        return False
    # Milestones and project-level cards are not execution tasks.
    if card_type in ("milestone", "project", "goal"):
        return False
    return True


def select_task(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    Select the next eligible task.

    Policy (canonical order):
      1. awaiting_human  → never drains
      2. review          → handled by heartbeat judge-loop, NOT by this selector
      3. inbox (deps satisfied, policy not blocked) → FIFO

    Note: review tasks are intentionally excluded here.
    Heartbeat-v3 judge loop owns review → leave them alone.
    """
    status_by_id = {str(t.get("id", "")): task_status(t) for t in tasks}

    inbox = [
        t for t in tasks
        if task_status(t) == "inbox"
        and dependencies_satisfied(t, status_by_id)
        and auto_dispatch_allowed(t)
    ]
    inbox.sort(key=lambda t: t.get("created_at", ""))
    if inbox:
        return {"item_type": "dispatch", "task": inbox[0]}

    return None


def active_queue_count() -> int:
    return len(list(ACTIVE.glob("*.json")))


def execution_in_progress_count(tasks: list[dict[str, Any]]) -> int:
    """
    Count tasks with real execution in_progress.

    Excludes:
    - tasks owned by luna-judge (review/intake sessions) — these are governance, not workers
    - tasks in luna-owned review/validation/planning phases
    """
    total = 0
    for task in tasks:
        if task_status(task) != "in_progress":
            continue
        fields = task_fields(task)
        phase = str(fields.get("mc_phase", "") or "").strip().lower()
        session_key = str(fields.get("mc_session_key", "") or "").strip().lower()

        # luna-judge owns review/intake tasks; exclude from execution capacity.
        if "luna-judge" in session_key:
            continue
        # Exclude governance phases regardless of session.
        if any(kw in phase for kw in ("review", "validation", "planning", "intake")):
            continue

        total += 1
    return total


def build_queue_payload(item_type: str, task: dict[str, Any]) -> dict[str, Any]:
    """
    Build a queue payload compatible with mc-fast-dispatch.sh --from-queue.

    mc-fast-dispatch.sh reads:
      - agent         (top-level)
      - title         (top-level)
      - task_id       (top-level)
      - context.description  ← REQUIRED, empty string fails validation
      - context.acceptance_criteria
      - context.qa_checks
      - context.expected_artifacts
      - context.project_id
      - phase / lane  (top-level)
    """
    task_id = str(task.get("id", "") or "")
    status = task_status(task)
    title = str(task.get("title", "(sem título)") or "(sem título)")
    description = str(task.get("description", "") or "")[:500]
    assigned_agent_id = str(task.get("assigned_agent_id", "") or "")
    fields = task_fields(task)

    # Default to "luan" (primary execution agent).
    # If task has an explicit assigned agent use it; otherwise let dispatcher route.
    if assigned_agent_id:
        agent = assigned_agent_id[:8]
    else:
        agent = "luan"

    # If no description, construct a minimal one so mc-fast-dispatch won't reject.
    if not description:
        description = f"Execute task: {title}"

    phase = queue_phase(item_type, task)

    return {
        "title": title,
        "agent": agent,
        "priority": str(task.get("priority", "medium") or "medium").lower(),
        "status": status,
        "phase": phase,
        "lane": str(fields.get("mc_lane", "") or ""),
        "source": "mc-simple-drain",
        # Use direct dispatch — bypass dispatcher agent (which may be unavailable).
        "dispatch_mode": "direct",
        "queue_key": queue_key(task_id, item_type, status, phase),
        "depends_on_task_ids": task.get("depends_on_task_ids", []) or [],
        "context": {
            "description": description,
            "acceptance_criteria": str(fields.get("mc_acceptance_criteria", "") or ""),
            "qa_checks": str(fields.get("mc_qa_checks", "") or ""),
            "expected_artifacts": str(fields.get("mc_expected_artifacts", "") or ""),
            "project_id": str(fields.get("mc_project_id", "") or ""),
            "card_type": str(fields.get("mc_card_type", "") or ""),
            "lane": str(fields.get("mc_lane", "") or ""),
            "runtime_owner": str(fields.get("mc_runtime_owner", "mc-simple-drain") or "mc-simple-drain"),
        },
        "spawn_params": {
            "agent": agent,
            "task_id": task_id,
            "title": title,
            "description": description,
            "priority": str(task.get("priority", "medium") or "medium").lower(),
        },
        "constraints": {
            "max_age_minutes": 30,
            "timeout_seconds": 600,
        },
    }


def write_queue_item(item_type: str, task: dict[str, Any]) -> str:
    task_id = str(task.get("id", "") or "")
    payload = build_queue_payload(item_type, task)
    key = payload["queue_key"]

    if queue_item_exists(task_id, key, [PENDING, ACTIVE]):
        log(f"SKIP: queue dedupe hit for {task_id[:8]} key={key}")
        return ""

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    filename = f"{timestamp}-{item_type}-{task_id[:8]}.json"
    item = {
        "version": 1,
        "type": item_type,
        "task_id": task_id,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": "mc-simple-drain",
        **payload,
    }

    if DRY_RUN:
        log(f"DRY-RUN: would write {filename} for {task_id[:8]} ({item_type})")
        return filename

    fd, tmp_path = tempfile.mkstemp(dir=str(PENDING), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(item, fh, indent=2)
        os.replace(tmp_path, PENDING / filename)
        log(f"QUEUE: wrote {filename}")
        return filename
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception:
            pass


def main() -> int:
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("SKIP: already running (lock held)")
        return 0

    tasks = list_tasks()
    in_progress = execution_in_progress_count(tasks)
    active_q = active_queue_count()

    if active_q >= MAX_ACTIVE_QUEUE:
        log(f"SKIP: active queue at capacity ({active_q}/{MAX_ACTIVE_QUEUE})")
        result = {"action": "skip", "reason": "active_queue_capacity", "active_queue": active_q}
        if EMIT_JSON:
            print(json.dumps(result))
        return 0

    if in_progress > MAX_IN_PROGRESS:
        log(f"SKIP: execution in_progress above limit ({in_progress}>{MAX_IN_PROGRESS})")
        result = {"action": "skip", "reason": "in_progress_capacity", "in_progress": in_progress}
        if EMIT_JSON:
            print(json.dumps(result))
        return 0

    selection = select_task(tasks)
    if not selection:
        log("IDLE: no eligible task")
        result = {"action": "idle"}
        if EMIT_JSON:
            print(json.dumps(result))
        return 0

    item_type = selection["item_type"]
    task = selection["task"]
    filename = write_queue_item(item_type, task)
    result = {
        "action": "queued" if filename else "deduped",
        "item_type": item_type,
        "task_id": str(task.get("id", "") or ""),
        "title": str(task.get("title", "") or ""),
        "filename": filename,
    }
    if EMIT_JSON:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
