#!/usr/bin/env python3
"""Planner adapter for milestone watcher V1."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def _run(cmd: list[str], timeout: int = 90) -> str:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "planner adapter command failed")
    return proc.stdout.strip()


def _severity_rank(value: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(value).lower(), 9)


class PlannerAdapter:
    def __init__(self, workspace: str | Path, *, openclaw_bin: str = "openclaw", dry_run: bool = False):
        self.workspace = Path(workspace)
        self.openclaw_bin = openclaw_bin
        self.dry_run = dry_run
        self.sync_script = self.workspace / "scripts" / "sync-luna-planner-context.sh"
        self.context_builder = self.workspace / "heartbeat-v3" / "scripts" / "build_planner_context.py"
        self.intent_dir = Path("/home/openclaw/.openclaw/workspace-luna-planner/artifacts/planner-intents")
        self.intent_dir.mkdir(parents=True, exist_ok=True)

    def decision_path(self, observation_id: str) -> Path:
        return self.intent_dir / f"{observation_id[:24]}.json"

    def propose(
        self,
        *,
        observation: dict[str, Any],
        gaps: list[dict[str, Any]],
        directives: list[dict[str, Any]],
    ) -> dict[str, Any]:
        decision_path = self.decision_path(str(observation.get("observation_id") or "observation"))
        if self.dry_run:
            return self._heuristic_plan(observation=observation, gaps=gaps, directives=directives)

        try:
            _run([str(self.sync_script)], timeout=45)
            context_path = _run(
                [
                    str(self.context_builder),
                    "--project-id",
                    str((observation.get("project") or {}).get("id") or ""),
                    "--milestone-id",
                    str((observation.get("milestone") or {}).get("id") or ""),
                    "--observation-id",
                    str(observation.get("observation_id") or ""),
                ],
                timeout=60,
            )
            message = f"""Controller-v1 planning dispatch.

Observation ID: {observation.get("observation_id")}
Context pack: {context_path}
Decision file (JSON): {decision_path}

Return a JSON object with this schema:
{{
  "observation_summary": "short summary",
  "gaps": [...],
  "planning_intents": [
    {{
      "intent_type": "create_leaf_task|promote_leaf_task|create_review_bundle|open_repair_bundle|escalate_chairman",
      "gap_class": "artifact_stale|setup_missing|distribution_gap|content_gap|measurement_gap|missing_execution_step|needs_structural_change|needs_chairman|blocked_by_repair",
      "target_scope": "task-or-workstream-id",
      "spec": {{}}
    }}
  ],
  "chairman_proposal": null
}}

Rules:
1. Never create or mutate Mission Control directly.
2. Never create workstreams or milestones.
3. Prefer at most two new leaf tasks and one review bundle.
4. If artifacts are stale, prioritize refresh over new growth tasks.
5. If a structural change is needed, emit only `escalate_chairman`.
6. Output valid JSON only.
"""
            _run([self.openclaw_bin, "agent", "--agent", "luna-planner", "--message", message, "--json"], timeout=45)
            if decision_path.exists():
                return json.loads(decision_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        return self._heuristic_plan(observation=observation, gaps=gaps, directives=directives)

    def _heuristic_plan(
        self,
        *,
        observation: dict[str, Any],
        gaps: list[dict[str, Any]],
        directives: list[dict[str, Any]],
    ) -> dict[str, Any]:
        workstreams = observation.get("workstreams") or []
        ws_by_hint = {}
        for workstream in workstreams:
            title = str(workstream.get("title") or "").lower()
            if "positioning" in title:
                ws_by_hint["content"] = workstream
            if "distribution" in title:
                ws_by_hint["distribution"] = workstream
            if "analytics" in title:
                ws_by_hint["analytics"] = workstream

        steering_text = " ".join(str((directive.get("payload") or {}).get("text") or "") for directive in directives).lower()
        intents: list[dict[str, Any]] = []
        chairman_proposal = None
        sorted_gaps = sorted(gaps, key=lambda item: (_severity_rank(item.get("severity", "")), str(item.get("gap_class") or "")))
        for gap in sorted_gaps:
            gap_class = str(gap.get("gap_class") or "")
            target_workstream_id = str(gap.get("target_workstream_id") or "")
            spec: dict[str, Any] = {}
            intent_type = ""
            if gap_class in {"artifact_stale", "measurement_gap"}:
                analytics_ws = target_workstream_id or str((ws_by_hint.get("analytics") or {}).get("id") or "")
                intent_type = "create_leaf_task"
                spec = {
                    "title": "Refresh Luna X scorecard and board packet",
                    "workstream_id": analytics_ws,
                    "assignee": "cto-ops",
                    "priority": "high",
                    "description": (
                        "Objective:\n- Refresh the operational measurement bundle for the Luna X canary.\n\n"
                        "Acceptance Criteria:\n- Regenerate session health, baseline, scorecard and board packet.\n"
                        "- Confirm all four artifacts are fresh and linked in Mission Control.\n"
                        "- Summarize whether the milestone is closer, flat or regressing.\n\n"
                        "Validation:\n- Artifacts updated under artifacts/reports/luna-x-growth/.\n"
                        "- Include the exact artifact paths in mc_output_summary.\n"
                    ),
                    "acceptance_criteria": "All required canary artifacts regenerated and fresh within policy window.",
                    "qa_checks": "Verify artifact timestamps and ensure scorecard/board packet parse cleanly.",
                    "expected_artifacts": "\n".join(
                        [
                            "artifacts/reports/luna-x-growth/session-health-latest.json",
                            "artifacts/reports/luna-x-growth/baseline-latest.json",
                            "artifacts/reports/luna-x-growth/scorecard-latest.json",
                            "artifacts/reports/luna-x-growth/board-packet-latest.md",
                        ]
                    ),
                }
            elif gap_class in {"content_gap", "missing_execution_step"}:
                content_ws = target_workstream_id or str((ws_by_hint.get("content") or {}).get("id") or "")
                intent_type = "create_leaf_task"
                spec = {
                    "title": "Extract 3-5 content pillars from Luna post history",
                    "workstream_id": content_ws,
                    "assignee": "luan",
                    "priority": "medium",
                    "description": (
                        "Objective:\n- Derive 3-5 durable content pillars for the Luna X canary from the existing profile and company goals.\n\n"
                        "Acceptance Criteria:\n- Produce a concise pillars document with rationale and examples.\n"
                        "- Map each pillar to the current M0 milestone and the crypto business context.\n"
                        "- Highlight what should explicitly be avoided.\n\n"
                        "Validation:\n- Write the artifact path in Mission Control and summarize the recommended pillars.\n"
                    ),
                    "acceptance_criteria": "Pillars document exists with 3-5 pillars, rationale, examples and anti-patterns.",
                    "qa_checks": "Check coherence with Luna X charter and current milestone objectives.",
                    "expected_artifacts": "artifacts/reports/luna-x-growth/content-pillars.md",
                }
            elif gap_class == "distribution_gap":
                distribution_ws = target_workstream_id or str((ws_by_hint.get("distribution") or {}).get("id") or "")
                title = "Draft day-1 engagement plan for Luna X canary"
                if "distribui" in steering_text:
                    title = "Draft focused distribution plan for Luna X canary"
                intent_type = "create_leaf_task"
                spec = {
                    "title": title,
                    "workstream_id": distribution_ws,
                    "assignee": "luan",
                    "priority": "medium",
                    "description": (
                        "Objective:\n- Convert the current milestone state into a concrete day-1 distribution plan for Luna X.\n\n"
                        "Acceptance Criteria:\n- List target accounts/communities, sequencing, reply angles and timing windows.\n"
                        "- Explain why this plan should improve follower growth from the current baseline.\n"
                        "- Stay within the canary guardrails and Luna public persona.\n\n"
                        "Validation:\n- Produce a markdown plan artifact and link it in Mission Control.\n"
                    ),
                    "acceptance_criteria": "Distribution plan includes targets, cadence, hooks and guardrail compliance.",
                    "qa_checks": "Verify the plan does not require prohibited behavior or spam tactics.",
                    "expected_artifacts": "artifacts/reports/luna-x-growth/engagement-plan-day1.md",
                }
            elif gap_class == "needs_structural_change":
                intent_type = "escalate_chairman"
                chairman_proposal = {
                    "proposal_type": "new_workstream",
                    "reason": str(gap.get("reason") or "structural gap detected"),
                    "payload": {"gap": gap},
                }
            elif gap_class == "needs_chairman":
                intent_type = "escalate_chairman"
                chairman_proposal = {
                    "proposal_type": "chairman_steering",
                    "reason": str(gap.get("reason") or "chairman input required"),
                    "payload": {"gap": gap},
                }
            if intent_type:
                intents.append(
                    {
                        "intent_type": intent_type,
                        "gap_class": gap_class,
                        "target_scope": target_workstream_id or str(gap.get("scope_id") or ""),
                        "spec": spec,
                    }
                )

        # Force a review checkpoint when all artifacts are fresh and no critical blockers remain.
        if not any(item.get("gap_class") == "artifact_stale" for item in gaps):
            analytics_ws = str((ws_by_hint.get("analytics") or {}).get("id") or "")
            intents.append(
                {
                    "intent_type": "create_review_bundle",
                    "gap_class": "measurement_gap",
                    "target_scope": analytics_ws,
                    "spec": {
                        "title": "Daily Luna X Growth Judge Bundle",
                        "workstream_id": analytics_ws,
                        "priority": "medium",
                        "description": "Review the fresh canary artifacts and decide whether the project should continue, adjust, or escalate.",
                    },
                }
            )

        return {
            "observation_summary": (
                f"Milestone {str((observation.get('milestone') or {}).get('title') or '').strip()} "
                f"currently has {len(gaps)} open gap(s)."
            ),
            "gaps": gaps,
            "planning_intents": intents[:3],
            "chairman_proposal": chairman_proposal,
        }
