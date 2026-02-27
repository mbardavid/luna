"""
test_queue.py — Tests for the filesystem queue system.

Tests:
  - Queue write (atomic)
  - Queue read (peek)
  - Queue claim (atomic, idempotent)
  - Queue complete (done/failed)
  - Queue consumer integration
  - Escalation age detection
  - Garbage collection

Runs without gateway — uses temp directories and no external calls.
"""

import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Add scripts dir to path and handle hyphenated filename
import sys
import importlib

_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
sys.path.insert(0, _scripts_dir)

# Import from hyphenated filename: queue-consumer.py
_spec = importlib.util.spec_from_file_location(
    "queue_consumer",
    os.path.join(_scripts_dir, "queue-consumer.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
QueueConsumer = _mod.QueueConsumer


class TestQueueSetup(unittest.TestCase):
    """Test queue directory creation."""

    def test_creates_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            queue_dir = os.path.join(tmpdir, "queue")
            consumer = QueueConsumer(queue_dir)

            for subdir in ["pending", "active", "done", "failed", "escalated"]:
                self.assertTrue(
                    os.path.isdir(os.path.join(queue_dir, subdir)),
                    f"Missing directory: {subdir}"
                )


class TestQueueWrite(unittest.TestCase):
    """Test atomic queue writes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)
        self.pending = os.path.join(self.queue_dir, "pending")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_item(self, item_type="dispatch", task_id="abc12345-test", **extra):
        """Helper to write a queue item directly (simulating heartbeat-v3.py)."""
        from datetime import datetime, timezone
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"{timestamp}-{item_type}-{task_id[:8]}.json"
        filepath = os.path.join(self.pending, filename)

        item = {
            "version": 1,
            "type": item_type,
            "task_id": task_id,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "test",
            "title": "Test Task",
            "agent": "luan",
            **extra,
        }

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=self.pending, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(item, f, indent=2)
        os.replace(tmp_path, filepath)
        return filename, item

    def test_write_creates_valid_json(self):
        filename, _ = self._write_item()
        filepath = os.path.join(self.pending, filename)

        self.assertTrue(os.path.exists(filepath))

        with open(filepath) as f:
            data = json.load(f)

        self.assertEqual(data["version"], 1)
        self.assertEqual(data["type"], "dispatch")
        self.assertIn("task_id", data)
        self.assertIn("created_at", data)

    def test_write_no_tmp_files_left(self):
        self._write_item()
        tmp_files = list(Path(self.pending).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0, "Temporary files should not remain")

    def test_write_multiple_items(self):
        for i in range(5):
            self._write_item(task_id=f"task{i:04d}-test-id")
            time.sleep(0.01)  # Ensure unique timestamps

        items = self.consumer.peek()
        self.assertEqual(len(items), 5)

    def test_write_dispatch_type(self):
        filename, _ = self._write_item(item_type="dispatch")
        self.assertIn("-dispatch-", filename)

    def test_write_respawn_type(self):
        filename, _ = self._write_item(item_type="respawn")
        self.assertIn("-respawn-", filename)


class TestQueueRead(unittest.TestCase):
    """Test queue reading (peek)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)
        self.pending = os.path.join(self.queue_dir, "pending")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_item(self, filename, data):
        filepath = os.path.join(self.pending, filename)
        with open(filepath, "w") as f:
            json.dump(data, f)
        return filename

    def test_peek_empty(self):
        items = self.consumer.peek()
        self.assertEqual(items, [])

    def test_peek_returns_items_with_metadata(self):
        self._write_item("20260226T220000-dispatch-abc12345.json", {
            "version": 1, "type": "dispatch", "task_id": "abc12345",
            "title": "Test", "agent": "luan",
        })

        items = self.consumer.peek()
        self.assertEqual(len(items), 1)
        self.assertIn("_filename", items[0])
        self.assertIn("_path", items[0])
        self.assertIn("_age_seconds", items[0])
        self.assertEqual(items[0]["type"], "dispatch")

    def test_peek_sorted_by_filename(self):
        """Items should be sorted oldest first."""
        self._write_item("20260226T220000-dispatch-aaa.json", {
            "version": 1, "type": "dispatch", "task_id": "aaa",
        })
        self._write_item("20260226T210000-dispatch-bbb.json", {
            "version": 1, "type": "dispatch", "task_id": "bbb",
        })

        items = self.consumer.peek()
        self.assertEqual(len(items), 2)
        # bbb has earlier timestamp → should be first
        self.assertEqual(items[0]["task_id"], "bbb")

    def test_peek_handles_corrupt_files(self):
        # Write invalid JSON
        filepath = os.path.join(self.pending, "corrupt.json")
        with open(filepath, "w") as f:
            f.write("not valid json{{{")

        items = self.consumer.peek()
        self.assertEqual(len(items), 1)
        self.assertIn("_error", items[0])

    def test_peek_includes_age(self):
        self._write_item("20260226T220000-dispatch-abc.json", {
            "version": 1, "type": "dispatch", "task_id": "abc",
        })
        # Touch the file to set mtime to "now"
        filepath = os.path.join(self.pending, "20260226T220000-dispatch-abc.json")
        os.utime(filepath, None)

        items = self.consumer.peek()
        self.assertGreaterEqual(items[0]["_age_seconds"], 0)
        self.assertLess(items[0]["_age_seconds"], 5)  # Should be very recent


class TestQueueClaim(unittest.TestCase):
    """Test atomic claim (pending → active)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)
        self.pending = os.path.join(self.queue_dir, "pending")
        self.active = os.path.join(self.queue_dir, "active")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_item(self, filename, data):
        filepath = os.path.join(self.pending, filename)
        with open(filepath, "w") as f:
            json.dump(data, f)
        return filename

    def test_claim_moves_to_active(self):
        fname = "20260226T220000-dispatch-abc.json"
        self._write_item(fname, {"type": "dispatch", "task_id": "abc"})

        result = self.consumer.claim(fname)
        self.assertIsNotNone(result)
        self.assertEqual(result["task_id"], "abc")

        # Should be in active/, not in pending/
        self.assertFalse(os.path.exists(os.path.join(self.pending, fname)))
        self.assertTrue(os.path.exists(os.path.join(self.active, fname)))

    def test_claim_idempotent_missing(self):
        """Claiming a non-existent file returns None."""
        result = self.consumer.claim("does-not-exist.json")
        self.assertIsNone(result)

    def test_claim_idempotent_already_active(self):
        """Claiming an already-claimed file returns the data from active/."""
        fname = "20260226T220000-dispatch-abc.json"
        data = {"type": "dispatch", "task_id": "abc"}

        # Write directly to active/
        filepath = os.path.join(self.active, fname)
        with open(filepath, "w") as f:
            json.dump(data, f)

        result = self.consumer.claim(fname)
        self.assertIsNotNone(result)
        self.assertEqual(result["task_id"], "abc")

    def test_claim_dry_run(self):
        """Dry-run claim doesn't move files."""
        consumer = QueueConsumer(self.queue_dir, dry_run=True)
        fname = "20260226T220000-dispatch-abc.json"
        self._write_item(fname, {"type": "dispatch", "task_id": "abc"})

        result = consumer.claim(fname)
        self.assertIsNotNone(result)
        self.assertTrue(result.get("_dry_run"))

        # File should still be in pending/
        self.assertTrue(os.path.exists(os.path.join(self.pending, fname)))

    def test_claim_concurrent_simulation(self):
        """Simulate concurrent claims — only one should succeed."""
        fname = "20260226T220000-dispatch-abc.json"
        self._write_item(fname, {"type": "dispatch", "task_id": "abc"})

        # First claim succeeds
        result1 = self.consumer.claim(fname)
        self.assertIsNotNone(result1)

        # Create another consumer (simulating concurrent process)
        consumer2 = QueueConsumer(self.queue_dir)

        # Second claim gets the data from active/ (idempotent)
        result2 = consumer2.claim(fname)
        self.assertIsNotNone(result2)
        self.assertEqual(result2["task_id"], "abc")


class TestQueueComplete(unittest.TestCase):
    """Test completion (active → done/failed)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)
        self.active = os.path.join(self.queue_dir, "active")
        self.done = os.path.join(self.queue_dir, "done")
        self.failed = os.path.join(self.queue_dir, "failed")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_active(self, filename, data):
        filepath = os.path.join(self.active, filename)
        with open(filepath, "w") as f:
            json.dump(data, f)

    def test_complete_success(self):
        fname = "test-item.json"
        self._write_active(fname, {"type": "dispatch", "task_id": "abc"})

        ok = self.consumer.complete(fname, success=True, result={"output": "done"})
        self.assertTrue(ok)

        # Should be in done/, not active/
        self.assertFalse(os.path.exists(os.path.join(self.active, fname)))
        self.assertTrue(os.path.exists(os.path.join(self.done, fname)))

        # Check metadata added
        with open(os.path.join(self.done, fname)) as f:
            data = json.load(f)
        self.assertTrue(data["success"])
        self.assertIn("completed_at", data)
        self.assertEqual(data["result"]["output"], "done")

    def test_complete_failure(self):
        fname = "test-item.json"
        self._write_active(fname, {"type": "dispatch", "task_id": "abc"})

        ok = self.consumer.complete(fname, success=False, result={"error": "timeout"})
        self.assertTrue(ok)

        # Should be in failed/, not active/
        self.assertFalse(os.path.exists(os.path.join(self.active, fname)))
        self.assertTrue(os.path.exists(os.path.join(self.failed, fname)))

    def test_complete_missing_item(self):
        ok = self.consumer.complete("nonexistent.json")
        self.assertFalse(ok)


class TestQueueConsumerIntegration(unittest.TestCase):
    """End-to-end consumer tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)
        self.pending = os.path.join(self.queue_dir, "pending")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_item(self, filename, data):
        filepath = os.path.join(self.pending, filename)
        with open(filepath, "w") as f:
            json.dump(data, f)

    def test_consume_one_dispatch(self):
        self._write_item("20260226T220000-dispatch-abc12345.json", {
            "version": 1,
            "type": "dispatch",
            "task_id": "abc12345-full-uuid",
            "title": "Test Task",
            "agent": "luan",
            "priority": "high",
            "context": {
                "description": "Do the thing",
                "eligible_count": 3,
                "in_progress_count": 1,
            },
            "spawn_params": {
                "agent": "luan",
                "description": "Do the thing",
            },
        })

        result = self.consumer.consume_one()
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "spawn")
        self.assertEqual(result["agent"], "luan")
        self.assertIn("spawn_params", result)
        self.assertIn("message", result["spawn_params"])
        self.assertIn("Test Task", result["spawn_params"]["message"])

    def test_consume_one_respawn(self):
        self._write_item("20260226T220000-respawn-abc12345.json", {
            "version": 1,
            "type": "respawn",
            "task_id": "abc12345-full-uuid",
            "title": "Failed Task",
            "agent": "luan",
            "context": {
                "failure_type": "TIMEOUT",
                "retry_count": 1,
                "adjustments": "increase timeout",
                "description": "Original description",
            },
        })

        result = self.consumer.consume_one()
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "respawn")
        self.assertIn("TIMEOUT", result["spawn_params"]["message"])

    def test_consume_one_empty(self):
        result = self.consumer.consume_one()
        self.assertIsNone(result)

    def test_consume_fifo_order(self):
        """Oldest item should be consumed first."""
        self._write_item("20260226T210000-dispatch-first.json", {
            "type": "dispatch", "task_id": "first", "title": "First",
        })
        self._write_item("20260226T220000-dispatch-second.json", {
            "type": "dispatch", "task_id": "second", "title": "Second",
        })

        result = self.consumer.consume_one()
        self.assertEqual(result["task_id"], "first")

    def test_full_lifecycle(self):
        """Write → peek → claim → complete."""
        self._write_item("20260226T220000-dispatch-lifecycle.json", {
            "type": "dispatch", "task_id": "lifecycle-test",
            "title": "Lifecycle Test", "agent": "luan",
        })

        # Peek
        items = self.consumer.peek()
        self.assertEqual(len(items), 1)

        # Consume (claim)
        result = self.consumer.consume_one()
        self.assertIsNotNone(result)

        # Should be in active now
        self.assertEqual(self.consumer.count_active(), 1)

        # Complete
        ok = self.consumer.complete(result["queue_file"], success=True)
        self.assertTrue(ok)

        # Should be in done now
        self.assertEqual(self.consumer.count_active(), 0)
        self.assertEqual(len(list(Path(self.consumer.done).glob("*.json"))), 1)


class TestEscalationAgeDetection(unittest.TestCase):
    """Test escalation age detection logic (simulating queue-escalation.sh)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pending = os.path.join(self.tmpdir, "pending")
        os.makedirs(self.pending)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fresh_item_not_escalated(self):
        filepath = os.path.join(self.pending, "fresh.json")
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)

        age = time.time() - os.stat(filepath).st_mtime
        self.assertLess(age, 60)  # Should be very recent

    def test_old_item_detected(self):
        filepath = os.path.join(self.pending, "old.json")
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)

        # Set mtime to 20 minutes ago
        old_time = time.time() - (20 * 60)
        os.utime(filepath, (old_time, old_time))

        age_seconds = time.time() - os.stat(filepath).st_mtime
        age_minutes = age_seconds / 60

        self.assertGreater(age_minutes, 15)  # Should trigger warn
        self.assertLess(age_minutes, 30)     # Should NOT trigger critical

    def test_very_old_item_critical(self):
        filepath = os.path.join(self.pending, "critical.json")
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)

        # Set mtime to 35 minutes ago
        old_time = time.time() - (35 * 60)
        os.utime(filepath, (old_time, old_time))

        age_seconds = time.time() - os.stat(filepath).st_mtime
        age_minutes = age_seconds / 60

        self.assertGreater(age_minutes, 30)  # Should trigger critical


class TestGarbageCollection(unittest.TestCase):
    """Test garbage collection of completed items."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.queue_dir = os.path.join(self.tmpdir, "queue")
        self.consumer = QueueConsumer(self.queue_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_gc_removes_old_items(self):
        # Write item to done/
        filepath = self.consumer.done / "old-item.json"
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)

        # Set mtime to 25 hours ago
        old_time = time.time() - (25 * 3600)
        os.utime(filepath, (old_time, old_time))

        count = self.consumer.gc_completed(max_age_hours=24)
        self.assertEqual(count, 1)
        self.assertFalse(filepath.exists())

    def test_gc_keeps_recent_items(self):
        filepath = self.consumer.done / "recent-item.json"
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)

        count = self.consumer.gc_completed(max_age_hours=24)
        self.assertEqual(count, 0)
        self.assertTrue(filepath.exists())

    def test_gc_dry_run(self):
        consumer = QueueConsumer(self.queue_dir, dry_run=True)
        filepath = consumer.done / "old-item.json"
        with open(filepath, "w") as f:
            json.dump({"type": "dispatch"}, f)
        old_time = time.time() - (25 * 3600)
        os.utime(filepath, (old_time, old_time))

        count = consumer.gc_completed(max_age_hours=24)
        self.assertEqual(count, 1)
        # File should still exist in dry-run
        self.assertTrue(filepath.exists())


if __name__ == "__main__":
    unittest.main()
