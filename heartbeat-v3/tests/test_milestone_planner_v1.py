#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from controller_v1.chairman_adapter import ChairmanAdapter
from controller_v1.gap_evaluator import evaluate_gaps
from controller_v1.outcome_watcher import MilestoneObservation
from controller_v1.planning_runtime import materialize_planning_intents
from controller_v1.runtime_store import RuntimeStore


class DummyProjection:
    def __init__(self):
        self.created = []

    def create_task(self, title, description, assignee, priority, status, fields):
        task_id = f"task-{len(self.created)+1}"
        self.created.append(
            {
                "id": task_id,
                "title": title,
                "description": description,
                "assignee": assignee,
                "priority": priority,
                "status": status,
                "fields": fields,
            }
        )
        return {"id": task_id}


def build_observation(tmp_path: Path) -> MilestoneObservation:
    return MilestoneObservation(
        observation_id="obs-123",
        project={"id": "project-1", "title": "Grow Luna X"},
        milestone={"id": "mile-1", "title": "M0 Session Recovery + Baseline + Charter"},
        workstreams=[
            {"id": "ws-1", "title": "WS1 Positioning and Content Engine"},
            {"id": "ws-2", "title": "WS2 Distribution and Engagement"},
            {"id": "ws-3", "title": "WS3 Analytics and Steering"},
        ],
        tasks=[],
        outcome={"net_followers_delta": 0, "review_summaries": []},
        artifacts={},
        freshness={
            "board_packet": {"exists": True, "fresh": False, "age_minutes": 500},
            "scorecard": {"exists": True, "fresh": True, "age_minutes": 3},
            "session_health": {"exists": True, "fresh": True, "age_minutes": 3},
            "baseline": {"exists": True, "fresh": True, "age_minutes": 3},
        },
        summary_hash="abc",
        observed_at="2026-03-07T00:00:00Z",
    )


def test_gap_evaluator_flags_stale_board_packet(tmp_path):
    observation = build_observation(tmp_path)
    gaps = evaluate_gaps(observation, directives=[])
    assert any(gap["gap_class"] == "artifact_stale" and gap["scope_id"] == "board_packet" for gap in gaps)


def test_chairman_adapter_parses_steer_command(tmp_path):
    adapter = ChairmanAdapter(tmp_path, openclaw_bin="true", config_path=tmp_path / "missing.json")
    parsed = adapter._parse_command("STEER 3a0d8492-412a-4676-b945-cd3b02885e3f focar em distribuição")
    assert parsed is not None
    command, payload = parsed
    assert command == "STEER"
    assert payload["project_id"] == "3a0d8492-412a-4676-b945-cd3b02885e3f"
    assert "distribuição" in payload["text"]


def test_materialize_planning_intents_creates_leaf_once(tmp_path):
    db_path = tmp_path / "controller-v1.db"
    store = RuntimeStore(db_path)
    projection = DummyProjection()
    observation = {
        "observation_id": "obs-123",
        "project": {"id": "project-1"},
        "milestone": {"id": "mile-1"},
        "workstreams": [{"id": "ws-3", "title": "WS3 Analytics and Steering"}],
    }
    planner_result = {
        "planning_intents": [
            {
                "intent_type": "create_leaf_task",
                "gap_class": "artifact_stale",
                "target_scope": "ws-3",
                "spec": {
                    "title": "Refresh Luna X scorecard and board packet",
                    "description": "refresh artifacts",
                    "assignee": "cto-ops",
                    "priority": "high",
                    "workstream_id": "ws-3",
                    "acceptance_criteria": "fresh",
                    "qa_checks": "timestamps",
                    "expected_artifacts": "artifacts/reports/luna-x-growth/scorecard-latest.json",
                },
            }
        ]
    }
    first = materialize_planning_intents(
        store=store,
        projection=projection,
        workspace=tmp_path,
        observation=observation,
        gaps=[],
        result=planner_result,
        tasks=[],
        dry_run=True,
    )
    second = materialize_planning_intents(
        store=store,
        projection=projection,
        workspace=tmp_path,
        observation=observation,
        gaps=[],
        result=planner_result,
        tasks=[],
        dry_run=True,
    )
    assert first["created_leaf"] == 1
    assert second["created_leaf"] == 0
    assert len(projection.created) == 1
