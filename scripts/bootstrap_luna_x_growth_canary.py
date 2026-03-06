#!/usr/bin/env python3
"""Register the Luna X growth canary in Mission Control."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
MC_CLIENT = WORKSPACE / "scripts" / "mc-client.sh"
SCORECARD_PATH = "artifacts/reports/luna-x-growth/scorecard-latest.json"
CHARTER_PATH = "docs/luna-x-growth-charter.md"
BASELINE_PATH = "artifacts/reports/luna-x-growth/baseline-latest.json"
BOARD_PACKET_PATH = "artifacts/reports/luna-x-growth/board-packet-latest.md"


def run_mc(*args: str) -> str:
    return subprocess.run([str(MC_CLIENT), *args], check=True, capture_output=True, text=True).stdout


def list_tasks() -> list[dict[str, Any]]:
    payload = json.loads(run_mc("list-tasks") or "{}")
    if isinstance(payload, dict):
        return payload.get("items", [])
    return payload if isinstance(payload, list) else []


def task_fields(task: dict[str, Any]) -> dict[str, Any]:
    return task.get("custom_field_values") or {}


def task_generation_key(task: dict[str, Any]) -> str:
    return str(task_fields(task).get("mc_generation_key") or "").strip()


def find_task(tasks: list[dict[str, Any]], generation_key: str) -> dict[str, Any] | None:
    for task in tasks:
        if task_generation_key(task) == generation_key:
            return task
    return None


def create_task(spec: dict[str, Any]) -> dict[str, Any]:
    output = run_mc(
        "create-task",
        spec["title"],
        spec["description"],
        spec.get("assignee", ""),
        spec.get("priority", "medium"),
        spec.get("status", "inbox"),
        json.dumps(spec["fields"], ensure_ascii=False),
    )
    return json.loads(output)


def update_task(task_id: str, spec: dict[str, Any], existing: dict[str, Any]) -> dict[str, Any]:
    merged_fields = dict(task_fields(existing))
    merged_fields.update(spec["fields"])
    output = run_mc(
        "update-task",
        task_id,
        "--status",
        spec.get("status", str(existing.get("status") or "inbox")),
        "--description",
        spec["description"],
        "--fields",
        json.dumps(merged_fields, ensure_ascii=False),
    )
    return json.loads(output)


def ensure_task(tasks: list[dict[str, Any]], spec: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    existing = find_task(tasks, str(spec["fields"].get("mc_generation_key") or ""))
    if existing:
        updated = update_task(str(existing.get("id") or ""), spec, existing)
        return updated, False
    created = create_task(spec)
    return created, True


def build_seed(
    *,
    key: str,
    title: str,
    description: str,
    assignee: str,
    priority: str,
    acceptance_criteria: str,
    qa_checks: str,
    expected_artifacts: str,
    workflow: str = "direct_exec",
    risk_profile: str = "medium",
    budget_scope: str = "project",
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "assignee": assignee,
        "priority": priority,
        "workflow": workflow,
        "dispatch_policy": "backlog",
        "risk_profile": risk_profile,
        "budget_scope": budget_scope,
        "acceptance_criteria": acceptance_criteria,
        "qa_checks": qa_checks,
        "expected_artifacts": expected_artifacts,
    }


def project_description() -> str:
    return (
        "Externally validated autonomy canary for Luna's X account.\n\n"
        "Goal:\n"
        "- M1: +25 net followers from baseline\n"
        "- M2: +200 net followers from baseline\n\n"
        "Canonical artifacts:\n"
        f"- {CHARTER_PATH}\n"
        f"- {BASELINE_PATH}\n"
        f"- {SCORECARD_PATH}\n"
        f"- {BOARD_PACKET_PATH}\n"
    )


def milestone_description(goal: str) -> str:
    return f"Autonomy milestone for the Luna X growth canary.\n\nGoal: {goal}\n"


def ws1_seeds() -> list[dict[str, Any]]:
    return [
        build_seed(
            key="luna-x-m0-charter-audit",
            title="Audit Luna X account and freeze growth charter",
            description=(
                "Review Luna's current X account posture, align it with the canary objective, and update the charter with approved pillars, "
                "prohibited actions, and steering triggers."
            ),
            assignee="luna",
            priority="high",
            acceptance_criteria=(
                "The charter reflects the current Luna account, contains the approved pillars, prohibited actions, and steering triggers, "
                "and references the canonical artifact paths for this canary."
            ),
            qa_checks=(
                f"Update {CHARTER_PATH} with the current account framing and artifact map.\n"
                "Reference the latest baseline or session-health artifact when available."
            ),
            expected_artifacts=CHARTER_PATH,
        ),
        build_seed(
            key="luna-x-m0-content-pillars",
            title="Extract 3-5 content pillars from Luna post history",
            description=(
                "Use the latest Luna profile snapshot to identify the strongest content pillars and write a short growth memo "
                "explaining why they fit the account and the company mission."
            ),
            assignee="luna",
            priority="medium",
            acceptance_criteria=(
                "A short memo exists with 3-5 recommended content pillars, each tied to observable evidence from the current account."
            ),
            qa_checks=(
                "Write a memo at artifacts/reports/luna-x-growth/content-pillars.md with pillars, evidence, and do-not-post examples."
            ),
            expected_artifacts="artifacts/reports/luna-x-growth/content-pillars.md",
        ),
    ]


def ws2_seeds() -> list[dict[str, Any]]:
    return [
        build_seed(
            key="luna-x-m0-distribution-map",
            title="Map target accounts and communities for Luna distribution",
            description=(
                "Map the first batch of target accounts and communities where Luna can engage credibly without leaving the account charter."
            ),
            assignee="luan",
            priority="medium",
            acceptance_criteria=(
                "A distribution map exists with at least 30 targets grouped by relevance, engagement style, and reputational risk."
            ),
            qa_checks=(
                "Write artifacts/reports/luna-x-growth/distribution-map.md with handle list, rationale, and engagement do/don't notes."
            ),
            expected_artifacts="artifacts/reports/luna-x-growth/distribution-map.md",
        ),
        build_seed(
            key="luna-x-m0-engagement-plan",
            title="Draft day-1 engagement plan for Luna X canary",
            description=(
                "Create the first day engagement plan using the distribution map, current account tone, and charter constraints."
            ),
            assignee="luan",
            priority="medium",
            acceptance_criteria=(
                "There is a one-day engagement plan with candidate replies, timing windows, and escalation notes for sensitive conversations."
            ),
            qa_checks=(
                "Write artifacts/reports/luna-x-growth/engagement-plan-day1.md with targets, rationale, and red lines."
            ),
            expected_artifacts="artifacts/reports/luna-x-growth/engagement-plan-day1.md",
        ),
    ]


def ws3_seeds() -> list[dict[str, Any]]:
    return [
        build_seed(
            key="luna-x-m0-session-recovery",
            title="Restore Luna X automation session and prove home/profile access",
            description=(
                "Validate or recover the Luna X automation session on the server, then emit the canonical session-health artifact."
            ),
            assignee="cto-ops",
            priority="critical",
            acceptance_criteria=(
                "The session-health artifact reports session_state=ok and proves access to Luna home/profile without public side effects."
            ),
            qa_checks="bash scripts/luna_x_session_recover.sh",
            expected_artifacts="artifacts/reports/luna-x-growth/session-health-latest.json",
            risk_profile="high",
        ),
        build_seed(
            key="luna-x-m0-baseline",
            title="Capture Luna X baseline snapshot",
            description=(
                "Capture the day-0 baseline for Luna's X account, including follower/following counts, recent posts, themes, and the first scorecard."
            ),
            assignee="cto-ops",
            priority="high",
            acceptance_criteria=(
                "The baseline artifact exists, the first scorecard exists, and both can be referenced from the project board packet."
            ),
            qa_checks="bash scripts/luna_x_growth_baseline.sh",
            expected_artifacts=(
                "artifacts/reports/luna-x-growth/baseline-latest.json\n"
                "artifacts/reports/luna-x-growth/scorecard-latest.json\n"
                "artifacts/reports/luna-x-growth/board-packet-latest.md"
            ),
            risk_profile="high",
        ),
        build_seed(
            key="luna-x-m0-daily-scorecard",
            title="Run Luna X daily scorecard and board packet",
            description=(
                "Refresh the Luna X profile snapshot, render the scorecard, and regenerate the board packet for daily steering."
            ),
            assignee="cto-ops",
            priority="medium",
            acceptance_criteria=(
                "The latest profile snapshot, scorecard, and board packet are refreshed together and agree on the current session state."
            ),
            qa_checks="bash scripts/luna_x_growth_daily.sh",
            expected_artifacts=(
                "artifacts/reports/luna-x-growth/profile-snapshot-latest.json\n"
                "artifacts/reports/luna-x-growth/scorecard-latest.json\n"
                "artifacts/reports/luna-x-growth/board-packet-latest.md"
            ),
            risk_profile="medium",
        ),
    ]


def card_specs() -> dict[str, Any]:
    project = {
        "title": "Grow Luna X account by +200 followers",
        "description": project_description(),
        "priority": "high",
        "status": "in_progress",
        "fields": {
            "mc_card_type": "project",
            "mc_generation_mode": "manual",
            "mc_generation_key": "luna-x-growth-project",
            "mc_dispatch_policy": "human_hold",
            "mc_lane": "project",
            "mc_chairman_state": "active",
            "mc_outcome_ref": SCORECARD_PATH,
            "mc_budget_scope": "project",
        },
    }
    milestones = [
        {
            "title": "M0 Session Recovery + Baseline + Charter",
            "description": milestone_description("restore session, capture baseline, freeze charter"),
            "priority": "high",
            "status": "in_progress",
            "fields": {
                "mc_card_type": "milestone",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m0",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "active",
                "mc_budget_scope": "project",
            },
        },
        {
            "title": "M1 +25 net followers",
            "description": milestone_description("reach +25 net followers from baseline without violating guardrails"),
            "priority": "high",
            "status": "inbox",
            "fields": {
                "mc_card_type": "milestone",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m1",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "planned",
                "mc_budget_scope": "project",
            },
        },
        {
            "title": "M2 +200 net followers",
            "description": milestone_description("reach +200 net followers from baseline with repeatable growth signals"),
            "priority": "high",
            "status": "inbox",
            "fields": {
                "mc_card_type": "milestone",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m2",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "planned",
                "mc_budget_scope": "project",
            },
        },
    ]
    workstreams = [
        {
            "title": "WS1 Positioning and Content Engine",
            "assignee": "luna",
            "priority": "high",
            "status": "in_progress",
            "description": "Define and guard the editorial posture for the Luna X canary, then derive short content guidance from the current account.",
            "fields": {
                "mc_card_type": "workstream",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m0-ws1",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "active",
                "mc_budget_scope": "project",
                "mc_task_seed_spec": ws1_seeds(),
            },
        },
        {
            "title": "WS2 Distribution and Engagement",
            "assignee": "luan",
            "priority": "medium",
            "status": "in_progress",
            "description": "Map the first distribution surface for Luna and propose the first safe engagement moves under the canary charter.",
            "fields": {
                "mc_card_type": "workstream",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m0-ws2",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "active",
                "mc_budget_scope": "project",
                "mc_task_seed_spec": ws2_seeds(),
            },
        },
        {
            "title": "WS3 Analytics and Steering",
            "assignee": "cto-ops",
            "priority": "critical",
            "status": "in_progress",
            "description": "Own session health, baseline capture, scorecards, and board packet refresh for the Luna X canary.",
            "fields": {
                "mc_card_type": "workstream",
                "mc_generation_mode": "manual",
                "mc_generation_key": "luna-x-growth-m0-ws3",
                "mc_dispatch_policy": "backlog",
                "mc_lane": "project",
                "mc_chairman_state": "active",
                "mc_budget_scope": "project",
                "mc_outcome_ref": SCORECARD_PATH,
                "mc_task_seed_spec": ws3_seeds(),
            },
        },
    ]
    review_bundle = {
        "title": "Daily Luna X Growth Judge Bundle",
        "description": "Review bundle placeholder for daily Luna steering once baseline and scorecards are flowing.",
        "priority": "medium",
        "status": "inbox",
        "fields": {
            "mc_card_type": "review_bundle",
            "mc_generation_mode": "manual",
            "mc_generation_key": "luna-x-growth-daily-review",
            "mc_dispatch_policy": "backlog",
            "mc_lane": "review",
            "mc_chairman_state": "active",
            "mc_budget_scope": "project",
        },
    }
    return {
        "project": project,
        "milestones": milestones,
        "workstreams": workstreams,
        "review_bundle": review_bundle,
    }


def active_project_conflict(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for task in tasks:
        fields = task_fields(task)
        if str(fields.get("mc_card_type") or "") != "project":
            continue
        if str(fields.get("mc_chairman_state") or "") != "active":
            continue
        if task_generation_key(task) == "luna-x-growth-project":
            continue
        if str(task.get("status") or "") not in {"done", "failed"}:
            return task
    return None


def main() -> int:
    tasks = list_tasks()
    conflict = active_project_conflict(tasks)
    if conflict:
        print(
            f"another active autonomy project already exists: {conflict.get('title')} ({str(conflict.get('id') or '')[:8]})",
            file=sys.stderr,
        )
        return 2

    specs = card_specs()
    project, _ = ensure_task(tasks, specs["project"])
    tasks = [task for task in tasks if task_generation_key(task) != "luna-x-growth-project"] + [project]
    project_id = str(project.get("id") or "")

    milestone_map: dict[str, dict[str, Any]] = {}
    for milestone_spec in specs["milestones"]:
        fields = dict(milestone_spec["fields"])
        fields.update({"mc_parent_task_id": project_id, "mc_project_id": project_id})
        task, _ = ensure_task(tasks, {**milestone_spec, "fields": fields})
        milestone_map[str(fields["mc_generation_key"])] = task
        tasks = [item for item in tasks if task_generation_key(item) != str(fields["mc_generation_key"])] + [task]

    m0_id = str(milestone_map["luna-x-growth-m0"].get("id") or "")
    for workstream_spec in specs["workstreams"]:
        fields = dict(workstream_spec["fields"])
        fields.update({
            "mc_parent_task_id": m0_id,
            "mc_project_id": project_id,
            "mc_milestone_id": m0_id,
        })
        task, _ = ensure_task(tasks, {**workstream_spec, "fields": fields})
        tasks = [item for item in tasks if task_generation_key(item) != str(fields["mc_generation_key"])] + [task]

    review_fields = dict(specs["review_bundle"]["fields"])
    review_fields.update({
        "mc_parent_task_id": m0_id,
        "mc_project_id": project_id,
        "mc_milestone_id": m0_id,
    })
    ensure_task(tasks, {**specs["review_bundle"], "fields": review_fields})

    print(json.dumps({
        "project_id": project_id,
        "m0_id": m0_id,
        "scorecard_path": SCORECARD_PATH,
        "charter_path": CHARTER_PATH,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
