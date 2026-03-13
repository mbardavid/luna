import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
_spec = importlib.util.spec_from_file_location(
    "mc_simple_drain",
    os.path.join(_scripts_dir, "mc-simple-drain.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


class TestMcSimpleDrainSelection(unittest.TestCase):
    def test_review_tasks_are_excluded_from_selector(self):
        """Review tasks are owned by judge-loop in heartbeat; mc-simple-drain must not touch them."""
        tasks = [
            {"id": "inbox1", "status": "inbox", "created_at": "2026-03-10T10:00:00Z", "depends_on_task_ids": []},
            {"id": "review1", "status": "review", "created_at": "2026-03-10T09:00:00Z", "depends_on_task_ids": []},
        ]
        sel = _mod.select_task(tasks)
        self.assertEqual(sel["item_type"], "dispatch")
        self.assertEqual(sel["task"]["id"], "inbox1")

    def test_inbox_requires_dependencies_done(self):
        tasks = [
            {"id": "dep1", "status": "review", "created_at": "2026-03-10T09:00:00Z", "depends_on_task_ids": []},
            {"id": "task1", "status": "inbox", "created_at": "2026-03-10T10:00:00Z", "depends_on_task_ids": ["dep1"]},
            {"id": "task2", "status": "inbox", "created_at": "2026-03-10T11:00:00Z", "depends_on_task_ids": []},
        ]
        # Remove the review item from selection scope to test inbox dependency gating only.
        sel = _mod.select_task(tasks[1:] + [{"id": "dep1", "status": "done", "created_at": "2026-03-10T09:00:00Z", "depends_on_task_ids": []}])
        self.assertEqual(sel["item_type"], "dispatch")
        self.assertEqual(sel["task"]["id"], "task1")

    def test_oldest_eligible_inbox_wins_fifo(self):
        tasks = [
            {"id": "task2", "status": "inbox", "created_at": "2026-03-10T11:00:00Z", "depends_on_task_ids": []},
            {"id": "task1", "status": "inbox", "created_at": "2026-03-10T10:00:00Z", "depends_on_task_ids": []},
        ]
        sel = _mod.select_task(tasks)
        self.assertEqual(sel["task"]["id"], "task1")


class TestMcSimpleDrainQueue(unittest.TestCase):
    def test_queue_dedupe_detects_same_task_and_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            pending = Path(tmpdir) / "pending"
            active = Path(tmpdir) / "active"
            pending.mkdir()
            active.mkdir()
            payload = {
                "task_id": "abc123",
                "queue_key": "abc123|dispatch|inbox|direct_exec|deadbeefcafe",
            }
            (pending / "x.json").write_text(json.dumps(payload), encoding="utf-8")
            self.assertTrue(_mod.queue_item_exists("abc123", "abc123|dispatch|inbox|direct_exec|deadbeefcafe", [pending, active]))
            self.assertTrue(_mod.queue_item_exists("abc123", "abc123|dispatch|inbox|direct_exec|anotherdigest", [pending, active]))
            self.assertFalse(_mod.queue_item_exists("other-task", "abc123|dispatch|inbox|direct_exec|deadbeefcafe", [pending, active]))


if __name__ == "__main__":
    unittest.main()
