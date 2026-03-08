#!/usr/bin/env python3
"""Judge dispatch/ingest adapter for controller-v1."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterator

from mc_control import extract_session_key_from_agent_result, task_fields, task_review_agent


def _run(cmd: list[str], timeout: int = 60) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "judge adapter command failed")
    return proc.stdout.strip()


class JudgeAdapter:
    def __init__(self, workspace: str | Path, *, openclaw_bin: str = "openclaw", dry_run: bool = False):
        self.workspace = Path(workspace)
        self.openclaw_bin = openclaw_bin
        self.dry_run = dry_run
        self.sync_script = self.workspace / "scripts" / "sync-luna-judge-context.sh"
        self.context_builder = self.workspace / "heartbeat-v3" / "scripts" / "build_judge_context.py"
        self.decision_dir = Path("/home/openclaw/.openclaw/workspace-luna-judge/artifacts/judge-decisions")
        self.decision_dir.mkdir(parents=True, exist_ok=True)

    def decision_path(self, task_id: str) -> Path:
        return self.decision_dir / f"{task_id[:8]}.json"

    def dispatch_review(self, task: dict[str, Any]) -> tuple[str, Path]:
        task_id = str(task.get("id") or "").strip()
        title = str(task.get("title") or "(untitled)")
        description = str(task.get("description") or "")
        review_agent = task_review_agent(task, default="luna-judge")
        decision_path = self.decision_path(task_id)
        artifact_hint = str(task_fields(task).get("mc_validation_artifact") or f"artifacts/mc/{task_id[:8]}-controller-review.md")

        if not self.dry_run:
            _run([str(self.sync_script)], timeout=45)
            context_path = _run([str(self.context_builder), "--task-id", task_id], timeout=60)
        else:
            context_path = f"/home/openclaw/.openclaw/workspace-luna-judge/artifacts/judge-context/{task_id[:8]}.md"

        message = f"""Controller-v1 review dispatch.

Task: {title}
Task ID: {task_id}
Context pack: {context_path}
Decision file (JSON): {decision_path}
Review artifact (Markdown): /home/openclaw/.openclaw/workspace-luna-judge/{artifact_hint}

You must:
1. Read the context pack first.
2. Review the task against acceptance criteria, artifacts and tests.
3. Write a JSON decision file to {decision_path} with this exact schema:
{{
  "task_id": "{task_id}",
  "decision": "approve|reject|awaiting_human",
  "next_status": "done|in_progress|awaiting_human",
  "comment": "short operational decision",
  "fields": {{"mc_output_summary": "optional short machine-readable summary"}},
  "reviewed_at": "ISO-8601 UTC"
}}
4. Optionally write supporting rationale to the review artifact path above.
5. In `fields`, only use Mission Control custom field keys that already exist and start with `mc_`.
6. Put any richer structured detail, analysis, evidence tables or nested JSON in the Markdown review artifact, not in `fields`.
7. Do not update Mission Control directly in this turn. Controller-v1 will project your decision.

Original description:
{description[:3500]}
"""
        if self.dry_run:
            return (f"agent:{review_agent}:{review_agent}", decision_path)
        raw = _run(
            [self.openclaw_bin, "agent", "--agent", review_agent, "--message", message, "--json"],
            timeout=90,
        )
        session_key = extract_session_key_from_agent_result(raw, agent=review_agent) or f"agent:{review_agent}:{review_agent}"
        return session_key, decision_path

    def iter_decisions(self) -> Iterator[tuple[str, dict[str, Any]]]:
        for path in sorted(self.decision_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            yield str(path), payload
