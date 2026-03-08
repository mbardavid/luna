#!/usr/bin/env python3
"""Filesystem queue adapter for controller-v1."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from mc_control import (
    build_queue_key,
    queue_phase,
    task_acceptance_criteria,
    task_execution_owner,
    task_expected_artifacts,
    task_fields,
    task_gate_reason,
    task_lane,
    task_phase,
    task_project_id,
    task_qa_checks,
    task_status,
)


def to_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class QueueAdapter:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)
        self.queue_dir = self.workspace / "heartbeat-v3" / "queue"
        self.pending = self.queue_dir / "pending"
        self.active = self.queue_dir / "active"
        self.done = self.queue_dir / "done"
        self.failed = self.queue_dir / "failed"
        for directory in (self.pending, self.active, self.done, self.failed):
            directory.mkdir(parents=True, exist_ok=True)

    def _paths(self) -> list[Path]:
        return [self.pending, self.active, self.done, self.failed]

    def _inflight_paths(self) -> list[Path]:
        return [self.pending, self.active]

    def _iter_matching(self, task_id: str, queue_key: str) -> Iterator[Path]:
        for directory in self._paths():
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(payload.get("task_id") or "") != str(task_id):
                    continue
                if queue_key and str(payload.get("queue_key") or "") != queue_key:
                    continue
                yield path

    def has_item(self, task_id: str, queue_key: str) -> bool:
        for directory in self._inflight_paths():
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if str(payload.get("task_id") or "") != str(task_id):
                    continue
                if queue_key and str(payload.get("queue_key") or "") != queue_key:
                    continue
                return True
        return False

    def write_dispatch_item(self, task: dict[str, Any], *, kind: str = "dispatch") -> str:
        task_id = str(task.get("id") or "").strip()
        agent = task_execution_owner(task)
        if not task_id or not agent:
            return ""
        queue_key = build_queue_key(task_id, kind, task_status(task), queue_phase(kind, task))
        if self.has_item(task_id, queue_key):
            return ""
        fields = task_fields(task)
        payload = {
            "version": 1,
            "type": kind,
            "task_id": task_id,
            "created_at": to_iso(),
            "created_by": "controller-v1",
            "queue_key": queue_key,
            "runtime_owner": "controller-v1",
            "lane": task_lane(task),
            "phase": task_phase(task),
            "status": task_status(task),
            "priority": str(task.get("priority") or "medium"),
            "agent": agent,
            "title": str(task.get("title") or ""),
            "context": {
                "description": str(task.get("description") or ""),
                "runtime_owner": "controller-v1",
                "project_id": task_project_id(task),
                "acceptance_criteria": task_acceptance_criteria(task),
                "qa_checks": task_qa_checks(task),
                "expected_artifacts": task_expected_artifacts(task),
                "delivery_state": str(fields.get("mc_delivery_state") or ""),
                "gate_reason": task_gate_reason(task),
            },
        }
        filename = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{kind}-{task_id[:8]}.json"
        target = self.pending / filename
        fd, tmp = tempfile.mkstemp(dir=self.pending, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
            os.replace(tmp, target)
            return str(target)
        finally:
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except OSError:
                pass

    def iter_results(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for directory in (self.done, self.failed):
            for path in sorted(directory.glob("*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                yield str(path), payload
