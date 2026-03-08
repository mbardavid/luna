#!/usr/bin/env python3
"""Mission Control projection helpers for controller-v1."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from .runtime_store import RuntimeStore


class MCProjection:
    def __init__(self, workspace: str | Path, *, dry_run: bool = False):
        self.workspace = Path(workspace)
        self.mc_client = str(self.workspace / "scripts" / "mc-client.sh")
        self.dry_run = dry_run

    def _run(self, cmd: list[str], timeout: int = 30) -> str:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "mc-client failed")
        return proc.stdout.strip()

    def list_tasks(self) -> list[dict[str, Any]]:
        raw = self._run([self.mc_client, "list-tasks"], timeout=30)
        payload = json.loads(raw or "{}")
        if isinstance(payload, dict):
            return payload.get("items", [])
        return payload if isinstance(payload, list) else []

    def get_task(self, task_id: str) -> dict[str, Any]:
        raw = self._run([self.mc_client, "get-task", task_id], timeout=30)
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}

    def create_task(self, title: str, description: str, assignee: str, priority: str, status: str,
                    fields: dict[str, Any]) -> dict[str, Any]:
        fields = dict(fields or {})
        fields.setdefault("mc_runtime_owner", "controller-v1")
        serialized = json.dumps(fields, ensure_ascii=False)
        if self.dry_run:
            pseudo_id = hashlib.sha1(f"{title}|{description}".encode("utf-8")).hexdigest()[:12]
            return {"id": f"dryrun-{pseudo_id}", "title": title, "custom_field_values": fields}
        raw = self._run([self.mc_client, "create-task", title, description, assignee or "", priority, status, serialized], timeout=45)
        payload = json.loads(raw or "{}")
        return payload if isinstance(payload, dict) else {}

    def update_task(self, task_id: str, *, status: str | None = None, comment: str | None = None,
                    fields: dict[str, Any] | None = None, assignee: str | None = None) -> None:
        cmd = [self.mc_client, "update-task", task_id]
        if status:
            cmd += ["--status", status]
        if assignee:
            cmd += ["--assignee", assignee]
        if comment:
            cmd += ["--comment", comment]
        if fields is not None:
            cmd += ["--fields", json.dumps(fields, ensure_ascii=False)]
        if self.dry_run:
            return
        self._run(cmd, timeout=45)

    def create_comment(self, task_id: str, message: str) -> None:
        if self.dry_run:
            return
        self._run([self.mc_client, "create-comment", task_id, message], timeout=30)

    def apply_if_changed(
        self,
        store: RuntimeStore,
        *,
        task_id: str,
        status: str | None = None,
        comment: str | None = None,
        fields: dict[str, Any] | None = None,
        assignee: str | None = None,
    ) -> bool:
        fields_payload = dict(fields or {})
        status_hash = hashlib.sha1((status or "").encode("utf-8")).hexdigest()
        fields_hash = hashlib.sha1(json.dumps(fields_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        existing = store.get_projection(task_id)
        if existing and existing.get("status_hash") == status_hash and existing.get("fields_hash") == fields_hash and not comment and not assignee:
            return False
        self.update_task(task_id, status=status, comment=comment, fields=fields_payload if fields is not None else None, assignee=assignee)
        store.set_projection(task_id=task_id, status_hash=status_hash, fields_hash=fields_hash)
        return True

