#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from controller_v1.queue_adapter import QueueAdapter
from controller_v1.runtime_store import RuntimeStore
from mc_control import task_runtime_owner


def test_task_runtime_owner_defaults_and_normalizes() -> None:
    assert task_runtime_owner({"custom_field_values": {}}) == "legacy"
    assert task_runtime_owner({"custom_field_values": {"mc_runtime_owner": "controller_v1"}}) == "controller-v1"
    assert task_runtime_owner({"custom_field_values": {"mc_runtime_owner": "controller-v1"}}) == "controller-v1"
    assert task_runtime_owner({"custom_field_values": {"mc_runtime_owner": "weird"}}) == "legacy"


def test_runtime_store_events_are_idempotent(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "controller-v1.db")
    assert store.add_event(source_ref="queue:item-1", event_type="queue-result", task_id="task-1", payload={"ok": True}) is True
    assert store.add_event(source_ref="queue:item-1", event_type="queue-result", task_id="task-1", payload={"ok": True}) is False
    snapshot = store.snapshot()
    assert snapshot.events == 1


def test_queue_adapter_allows_redispatch_after_historical_done(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    adapter = QueueAdapter(workspace)
    task = {
        "id": "abc12345-task",
        "title": "Repair child",
        "description": "Fix it",
        "status": "inbox",
        "priority": "high",
        "assigned_agent_id": "cto-ops",
        "custom_field_values": {
            "mc_runtime_owner": "controller-v1",
            "mc_card_type": "leaf_task",
            "mc_lane": "repair",
            "mc_dispatch_policy": "auto",
        },
    }
    queue_path = Path(adapter.write_dispatch_item(task))
    done_path = adapter.done / queue_path.name
    queue_path.rename(done_path)
    second = adapter.write_dispatch_item(task)
    assert second
    assert Path(second).parent.name == "pending"


def test_queue_adapter_writes_controller_owned_dispatch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    adapter = QueueAdapter(workspace)
    task = {
        "id": "abc12345-task",
        "title": "Repair child",
        "description": "Fix it",
        "status": "inbox",
        "priority": "high",
        "assigned_agent_id": "cto-ops",
        "custom_field_values": {
            "mc_runtime_owner": "controller-v1",
            "mc_card_type": "leaf_task",
            "mc_lane": "repair",
            "mc_dispatch_policy": "auto",
            "mc_acceptance_criteria": "One",
            "mc_qa_checks": "Two",
            "mc_expected_artifacts": "Three",
            "mc_repair_bundle_id": "bundle-1",
        },
    }
    queue_path = adapter.write_dispatch_item(task)
    assert queue_path
    payload = json.loads(Path(queue_path).read_text(encoding="utf-8"))
    assert payload["runtime_owner"] == "controller-v1"
    assert payload["agent"] == "cto-ops"
    assert payload["lane"] == "repair"
    assert payload["context"]["runtime_owner"] == "controller-v1"
    assert adapter.write_dispatch_item(task) == ""


class DummyProjection:
    def __init__(self):
        self.updates = []

    def apply_if_changed(self, store, *, task_id, status=None, comment=None, fields=None, assignee=None):
        self.updates.append({
            "task_id": task_id,
            "status": status,
            "comment": comment,
            "fields": fields or {},
            "assignee": assignee,
        })
        return True


def test_ingest_execution_proofs_closes_repair_leaf(tmp_path: Path) -> None:
    module_path = ROOT / 'scripts' / 'controller-v1.py'
    spec = importlib.util.spec_from_file_location('controller_v1_main', module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)

    workspace = tmp_path / 'workspace'
    artifact = workspace / 'artifacts' / 'repairs' / 'bundle-diagnose.md'
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('# proof\n', encoding='utf-8')
    module.WORKSPACE = workspace

    store = RuntimeStore(tmp_path / 'controller-v1.db')
    projection = DummyProjection()
    bundle = {
        'id': 'bundle-1',
        'title': 'Repair bundle',
        'status': 'done',
        'custom_field_values': {
            'mc_runtime_owner': 'controller-v1',
            'mc_card_type': 'repair_bundle',
            'mc_repair_state': 'resolved',
        },
    }
    task = {
        'id': 'leaf-1',
        'title': 'Diagnose child',
        'status': 'in_progress',
        'in_progress_at': '2026-03-09T00:00:00Z',
        'custom_field_values': {
            'mc_runtime_owner': 'controller-v1',
            'mc_card_type': 'leaf_task',
            'mc_lane': 'repair',
            'mc_parent_task_id': 'bundle-1',
            'mc_assigned_agent': 'cto-ops',
            'mc_expected_artifacts': 'artifacts/repairs/bundle-diagnose.md',
            'mc_delivery_state': 'dispatched',
        },
    }
    applied = module.ingest_execution_proofs(store=store, projection=projection, tasks=[bundle, task])
    assert applied == 1
    assert projection.updates[0]['status'] == 'done'
    assert projection.updates[0]['fields']['mc_delivery_state'] == 'done'
    assert 'bundle-diagnose.md' in projection.updates[0]['fields']['mc_proof_ref']
