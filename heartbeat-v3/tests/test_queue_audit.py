import json
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE / "scripts"))

from mc_queue_audit import apply_report_to_metrics, audit_queue


class TestQueueAudit(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.queue = self.tmpdir / "queue"
        for name in ("pending", "active", "done", "failed"):
            (self.queue / name).mkdir(parents=True, exist_ok=True)
        self.metrics_file = self.tmpdir / "metrics.json"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_done(self, filename: str, payload: dict):
        (self.queue / "done" / filename).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    def test_audit_detects_invalid_done_and_duplicate_meta(self):
        self._write_done(
            "20260305T200816-dispatch-task1.json",
            {
                "task_id": "task-1",
                "type": "dispatch",
                "queue_key": "task-1|dispatch|inbox|direct_exec|abc123",
                "completed_at": "2026-03-05T20:09:23Z",
                "completed_by": "mc-fast-dispatch",
                "success": True,
                "result": {"action": "dispatch", "session_id": "agent:luan:1"},
            },
        )
        self._write_done(
            "20260305T055510-dispatch-task2.json",
            {
                "task_id": "task-2",
                "type": "dispatch",
                "title": "legacy invalid completion",
            },
        )
        self._write_done(
            "20260305T044516-dispatch-task2.json.meta",
            {"dedupe": "true", "reason": "duplicate dispatch"},
        )

        report = audit_queue(self.queue)
        self.assertEqual(report["invalid_done_total"], 1)
        self.assertEqual(report["invalid_done_post_recovery"], 0)
        self.assertEqual(report["duplicate_meta_markers_total"], 1)

    def test_audit_detects_duplicate_groups(self):
        payload = {
            "task_id": "task-3",
            "type": "dispatch",
            "completed_at": "2026-03-05T20:09:23Z",
            "completed_by": "mc-fast-dispatch",
            "success": True,
            "result": {"action": "dispatch", "session_id": "agent:luan:2"},
        }
        self._write_done("a.json", payload)
        self._write_done("b.json", payload)

        report = audit_queue(self.queue)
        self.assertEqual(report["duplicate_groups_total"], 1)
        self.assertEqual(report["duplicate_groups_top"][0]["count"], 2)

    def test_apply_report_to_metrics_sets_queue_audit_fields(self):
        self._write_done(
            "bad.json",
            {
                "task_id": "task-4",
                "type": "dispatch",
                "queue_key": "task-4|dispatch|inbox|direct_exec|bad123",
            },
        )
        report = audit_queue(self.queue)
        metrics = apply_report_to_metrics(report, self.metrics_file)
        self.assertEqual(metrics["counters_today"]["queue_items_invalid_completed"], 1)
        self.assertEqual(metrics["queue_audit"]["invalid_done_post_recovery"], 1)


if __name__ == "__main__":
    unittest.main()
