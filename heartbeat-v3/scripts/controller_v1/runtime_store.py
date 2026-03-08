#!/usr/bin/env python3
"""SQLite-backed runtime store for controller-v1."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None = None) -> str:
    current = dt or utcnow()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS tracked_tasks (
  task_id TEXT PRIMARY KEY,
  card_type TEXT NOT NULL,
  lane TEXT NOT NULL,
  workflow TEXT NOT NULL,
  project_id TEXT NOT NULL,
  milestone_id TEXT NOT NULL,
  workstream_id TEXT NOT NULL,
  desired_state TEXT NOT NULL,
  actual_state TEXT NOT NULL,
  gate_reason TEXT NOT NULL,
  runtime_owner TEXT NOT NULL,
  assigned_agent TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
  attempt_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  agent TEXT NOT NULL,
  session_key TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  proof_ref TEXT NOT NULL,
  error_class TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_attempts_task_id ON attempts(task_id);

CREATE TABLE IF NOT EXISTS leases (
  lease_key TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  lane TEXT NOT NULL,
  agent TEXT NOT NULL,
  status TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repairs (
  bundle_id TEXT PRIMARY KEY,
  source_task_id TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  status TEXT NOT NULL,
  canonical_bundle_id TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repairs_fingerprint ON repairs(fingerprint);

CREATE TABLE IF NOT EXISTS project_windows (
  scope_key TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  milestone_id TEXT NOT NULL,
  workstream_id TEXT NOT NULL,
  slot_budget TEXT NOT NULL,
  window_state TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  source_ref TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  task_id TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projections (
  task_id TEXT PRIMARY KEY,
  status_hash TEXT NOT NULL,
  fields_hash TEXT NOT NULL,
  last_projected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS milestone_observations (
  observation_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  milestone_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  outcome_json TEXT NOT NULL,
  artifacts_json TEXT NOT NULL,
  freshness_json TEXT NOT NULL,
  scheduler_json TEXT NOT NULL,
  summary_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_milestone_observations_scope
  ON milestone_observations(project_id, milestone_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS gap_evaluations (
  gap_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  gap_class TEXT NOT NULL,
  severity TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  resolved_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gap_evaluations_observation
  ON gap_evaluations(observation_id);

CREATE TABLE IF NOT EXISTS planning_intents (
  intent_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  intent_type TEXT NOT NULL,
  target_scope TEXT NOT NULL,
  dedupe_key TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_task_id TEXT NOT NULL,
  materialized_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_planning_intents_dedupe
  ON planning_intents(dedupe_key, status);

CREATE TABLE IF NOT EXISTS chairman_directives (
  directive_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  directive_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  author_id TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chairman_directives_project
  ON chairman_directives(project_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS chairman_proposals (
  proposal_id TEXT PRIMARY KEY,
  observation_id TEXT NOT NULL,
  proposal_type TEXT NOT NULL,
  reason TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


@dataclass
class RuntimeSnapshot:
    last_tick: str
    tracked_tasks: int
    controller_tasks: int
    open_repairs: int
    attempts: int
    events: int
    observations: int
    planning_intents: int
    open_proposals: int


class RuntimeStore:
    def __init__(self, db_path: str | os.PathLike[str]):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def upsert_tracked_task(self, task: dict[str, Any], *, card_type: str, lane: str, workflow: str,
                            project_id: str, milestone_id: str, workstream_id: str,
                            desired_state: str, actual_state: str, gate_reason: str,
                            runtime_owner: str, assigned_agent: str) -> None:
        payload = json.dumps(task, ensure_ascii=False, sort_keys=True)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tracked_tasks (
                  task_id, card_type, lane, workflow, project_id, milestone_id, workstream_id,
                  desired_state, actual_state, gate_reason, runtime_owner, assigned_agent,
                  updated_at, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  card_type=excluded.card_type,
                  lane=excluded.lane,
                  workflow=excluded.workflow,
                  project_id=excluded.project_id,
                  milestone_id=excluded.milestone_id,
                  workstream_id=excluded.workstream_id,
                  desired_state=excluded.desired_state,
                  actual_state=excluded.actual_state,
                  gate_reason=excluded.gate_reason,
                  runtime_owner=excluded.runtime_owner,
                  assigned_agent=excluded.assigned_agent,
                  updated_at=excluded.updated_at,
                  raw_json=excluded.raw_json
                """,
                (
                    str(task.get("id") or ""),
                    card_type,
                    lane,
                    workflow,
                    project_id,
                    milestone_id,
                    workstream_id,
                    desired_state,
                    actual_state,
                    gate_reason,
                    runtime_owner,
                    assigned_agent,
                    to_iso(),
                    payload,
                ),
            )

    def record_attempt(self, *, attempt_id: str, task_id: str, kind: str, agent: str, session_key: str = "",
                       status: str, started_at: str = "", finished_at: str = "", proof_ref: str = "",
                       error_class: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO attempts (
                  attempt_id, task_id, kind, agent, session_key, status,
                  started_at, finished_at, proof_ref, error_class
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(attempt_id) DO UPDATE SET
                  task_id=excluded.task_id,
                  kind=excluded.kind,
                  agent=excluded.agent,
                  session_key=excluded.session_key,
                  status=excluded.status,
                  started_at=excluded.started_at,
                  finished_at=excluded.finished_at,
                  proof_ref=excluded.proof_ref,
                  error_class=excluded.error_class
                """,
                (
                    attempt_id,
                    task_id,
                    kind,
                    agent,
                    session_key,
                    status,
                    started_at or to_iso(),
                    finished_at or "",
                    proof_ref,
                    error_class,
                ),
            )

    def set_lease(self, *, lease_key: str, task_id: str, lane: str, agent: str, status: str,
                  lease_until: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO leases (lease_key, task_id, lane, agent, status, lease_until, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(lease_key) DO UPDATE SET
                  task_id=excluded.task_id,
                  lane=excluded.lane,
                  agent=excluded.agent,
                  status=excluded.status,
                  lease_until=excluded.lease_until,
                  updated_at=excluded.updated_at
                """,
                (lease_key, task_id, lane, agent, status, lease_until, to_iso()),
            )

    def set_repair(self, *, bundle_id: str, source_task_id: str, fingerprint: str, status: str,
                   canonical_bundle_id: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO repairs (bundle_id, source_task_id, fingerprint, status, canonical_bundle_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(bundle_id) DO UPDATE SET
                  source_task_id=excluded.source_task_id,
                  fingerprint=excluded.fingerprint,
                  status=excluded.status,
                  canonical_bundle_id=excluded.canonical_bundle_id,
                  updated_at=excluded.updated_at
                """,
                (bundle_id, source_task_id, fingerprint, status, canonical_bundle_id, to_iso()),
            )

    def set_project_window(self, *, project_id: str, milestone_id: str, workstream_id: str,
                           slot_budget: dict[str, Any], window_state: str) -> None:
        scope_key = ":".join([project_id or "", milestone_id or "", workstream_id or ""])
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO project_windows (
                  scope_key, project_id, milestone_id, workstream_id, slot_budget, window_state, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                  project_id=excluded.project_id,
                  milestone_id=excluded.milestone_id,
                  workstream_id=excluded.workstream_id,
                  slot_budget=excluded.slot_budget,
                  window_state=excluded.window_state,
                  updated_at=excluded.updated_at
                """,
                (scope_key, project_id, milestone_id, workstream_id, json.dumps(slot_budget or {}, ensure_ascii=False), window_state, to_iso()),
            )

    def add_event(self, *, source_ref: str, event_type: str, task_id: str = "", payload: dict[str, Any] | None = None) -> bool:
        try:
            with self.connect() as conn:
                conn.execute(
                    "INSERT INTO events (source_ref, event_type, task_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                    (source_ref, event_type, task_id, json.dumps(payload or {}, ensure_ascii=False, sort_keys=True), to_iso()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def has_event(self, source_ref: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("SELECT 1 FROM events WHERE source_ref = ? LIMIT 1", (source_ref,)).fetchone()
            return bool(row)

    def set_projection(self, *, task_id: str, status_hash: str, fields_hash: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO projections (task_id, status_hash, fields_hash, last_projected_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                  status_hash=excluded.status_hash,
                  fields_hash=excluded.fields_hash,
                  last_projected_at=excluded.last_projected_at
                """,
                (task_id, status_hash, fields_hash, to_iso()),
            )

    def get_projection(self, task_id: str) -> dict[str, str] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status_hash, fields_hash, last_projected_at FROM projections WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return dict(row) if row else None

    def snapshot(self) -> RuntimeSnapshot:
        with self.connect() as conn:
            tracked = conn.execute("SELECT COUNT(*) AS count FROM tracked_tasks").fetchone()["count"]
            controller = conn.execute(
                "SELECT COUNT(*) AS count FROM tracked_tasks WHERE runtime_owner = 'controller-v1'"
            ).fetchone()["count"]
            repairs = conn.execute(
                "SELECT COUNT(*) AS count FROM repairs WHERE status NOT IN ('resolved','failed')"
            ).fetchone()["count"]
            attempts = conn.execute("SELECT COUNT(*) AS count FROM attempts").fetchone()["count"]
            events = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
            observations = conn.execute("SELECT COUNT(*) AS count FROM milestone_observations").fetchone()["count"]
            intents = conn.execute("SELECT COUNT(*) AS count FROM planning_intents").fetchone()["count"]
            proposals = conn.execute(
                "SELECT COUNT(*) AS count FROM chairman_proposals WHERE status NOT IN ('approved','rejected','implemented')"
            ).fetchone()["count"]
        return RuntimeSnapshot(
            last_tick=to_iso(),
            tracked_tasks=int(tracked or 0),
            controller_tasks=int(controller or 0),
            open_repairs=int(repairs or 0),
            attempts=int(attempts or 0),
            events=int(events or 0),
            observations=int(observations or 0),
            planning_intents=int(intents or 0),
            open_proposals=int(proposals or 0),
        )

    def latest_observation(self, *, project_id: str, milestone_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT observation_id, project_id, milestone_id, observed_at, outcome_json, artifacts_json,
                       freshness_json, scheduler_json, summary_hash
                FROM milestone_observations
                WHERE project_id = ? AND milestone_id = ?
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (project_id, milestone_id),
            ).fetchone()
            if not row:
                return None
            payload = dict(row)
        for key in ("outcome_json", "artifacts_json", "freshness_json", "scheduler_json"):
            try:
                payload[key.replace("_json", "")] = json.loads(payload.pop(key) or "{}")
            except Exception:
                payload[key.replace("_json", "")] = {}
        return payload

    def insert_observation(
        self,
        *,
        observation_id: str,
        project_id: str,
        milestone_id: str,
        observed_at: str,
        outcome: dict[str, Any],
        artifacts: dict[str, Any],
        freshness: dict[str, Any],
        scheduler: dict[str, Any],
        summary_hash: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO milestone_observations (
                  observation_id, project_id, milestone_id, observed_at,
                  outcome_json, artifacts_json, freshness_json, scheduler_json, summary_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    project_id,
                    milestone_id,
                    observed_at,
                    json.dumps(outcome or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(artifacts or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(freshness or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(scheduler or {}, ensure_ascii=False, sort_keys=True),
                    summary_hash,
                ),
            )

    def replace_gap_evaluations(self, *, observation_id: str, gaps: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM gap_evaluations WHERE observation_id = ?", (observation_id,))
            for item in gaps:
                conn.execute(
                    """
                    INSERT INTO gap_evaluations (
                      gap_id, observation_id, gap_class, severity, scope_type, scope_id, reason, evidence_json, resolved_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("gap_id") or ""),
                        observation_id,
                        str(item.get("gap_class") or ""),
                        str(item.get("severity") or "medium"),
                        str(item.get("scope_type") or ""),
                        str(item.get("scope_id") or ""),
                        str(item.get("reason") or ""),
                        json.dumps(item.get("evidence") or {}, ensure_ascii=False, sort_keys=True),
                        str(item.get("resolved_at") or ""),
                    ),
                )

    def has_open_intent(self, dedupe_key: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM planning_intents
                WHERE dedupe_key = ? AND status IN ('proposed', 'materialized', 'pending_chairman')
                LIMIT 1
                """,
                (dedupe_key,),
            ).fetchone()
            return bool(row)

    def upsert_planning_intent(
        self,
        *,
        intent_id: str,
        observation_id: str,
        intent_type: str,
        target_scope: str,
        dedupe_key: str,
        spec: dict[str, Any],
        status: str,
        created_task_id: str = "",
        materialized_at: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO planning_intents (
                  intent_id, observation_id, intent_type, target_scope, dedupe_key,
                  spec_json, status, created_task_id, materialized_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(intent_id) DO UPDATE SET
                  observation_id=excluded.observation_id,
                  intent_type=excluded.intent_type,
                  target_scope=excluded.target_scope,
                  dedupe_key=excluded.dedupe_key,
                  spec_json=excluded.spec_json,
                  status=excluded.status,
                  created_task_id=excluded.created_task_id,
                  materialized_at=excluded.materialized_at
                """,
                (
                    intent_id,
                    observation_id,
                    intent_type,
                    target_scope,
                    dedupe_key,
                    json.dumps(spec or {}, ensure_ascii=False, sort_keys=True),
                    status,
                    created_task_id,
                    materialized_at,
                ),
            )

    def add_chairman_directive(
        self,
        *,
        directive_id: str,
        project_id: str,
        directive_type: str,
        payload: dict[str, Any],
        author_id: str,
        source_ref: str,
        status: str = "active",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chairman_directives (
                  directive_id, project_id, directive_type, payload_json, author_id,
                  source_ref, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(directive_id) DO UPDATE SET
                  project_id=excluded.project_id,
                  directive_type=excluded.directive_type,
                  payload_json=excluded.payload_json,
                  author_id=excluded.author_id,
                  source_ref=excluded.source_ref,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    directive_id,
                    project_id,
                    directive_type,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    author_id,
                    source_ref,
                    status,
                    to_iso(),
                    to_iso(),
                ),
            )

    def list_active_chairman_directives(self, *, project_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT directive_id, project_id, directive_type, payload_json, author_id, source_ref, status, created_at, updated_at
                FROM chairman_directives
                WHERE project_id = ? AND status = 'active'
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["payload"] = json.loads(item.pop("payload_json") or "{}")
            except Exception:
                item["payload"] = {}
            result.append(item)
        return result

    def set_chairman_directive_status(self, directive_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE chairman_directives SET status = ?, updated_at = ? WHERE directive_id = ?",
                (status, to_iso(), directive_id),
            )

    def upsert_chairman_proposal(
        self,
        *,
        proposal_id: str,
        observation_id: str,
        proposal_type: str,
        reason: str,
        payload: dict[str, Any],
        status: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO chairman_proposals (
                  proposal_id, observation_id, proposal_type, reason, payload_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO UPDATE SET
                  observation_id=excluded.observation_id,
                  proposal_type=excluded.proposal_type,
                  reason=excluded.reason,
                  payload_json=excluded.payload_json,
                  status=excluded.status,
                  updated_at=excluded.updated_at
                """,
                (
                    proposal_id,
                    observation_id,
                    proposal_type,
                    reason,
                    json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
                    status,
                    to_iso(),
                    to_iso(),
                ),
            )

    def get_chairman_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT proposal_id, observation_id, proposal_type, reason, payload_json, status, created_at, updated_at
                FROM chairman_proposals
                WHERE proposal_id = ?
                LIMIT 1
                """,
                (proposal_id,),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        try:
            payload["payload"] = json.loads(payload.pop("payload_json") or "{}")
        except Exception:
            payload["payload"] = {}
        return payload

    def set_chairman_proposal_status(self, proposal_id: str, status: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE chairman_proposals SET status = ?, updated_at = ? WHERE proposal_id = ?",
                (status, to_iso(), proposal_id),
            )
