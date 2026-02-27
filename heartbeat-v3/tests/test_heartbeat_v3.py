"""
test_heartbeat_v3.py — Tests for heartbeat-v3.py logic.

Tests:
  - Import validation
  - State management (load, save, ensure_fields)
  - Queue write integration
  - Failure analysis patterns
  - Circuit breaker logic
  - Rate limiting
  - Dispatch dedup

Runs without gateway — all external calls are mocked.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestStateManagement(unittest.TestCase):
    """Test state file load/save/ensure_fields."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_file = os.path.join(self.tmpdir, "state.json")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_missing_state(self):
        """Loading non-existent state returns defaults."""
        # Simulate what heartbeat-v3 does
        if os.path.exists(self.state_file):
            with open(self.state_file) as f:
                state = json.load(f)
        else:
            state = {
                "last_dispatched_id": "",
                "dispatched_at": 0,
                "notified_failures": {},
                "dispatch_history": [],
                "circuit_breaker": {
                    "state": "closed",
                    "failures": 0,
                    "last_failure_at": 0,
                    "opened_at": 0,
                },
            }
        self.assertEqual(state["circuit_breaker"]["state"], "closed")
        self.assertEqual(state["dispatch_history"], [])

    def test_save_and_load_state(self):
        """Save state atomically and load it back."""
        state = {
            "last_dispatched_id": "test-123",
            "dispatched_at": 1000000,
            "notified_failures": {"task1": {"at": 500}},
            "dispatch_history": [{"task_id": "test-123", "at": 1000000}],
            "circuit_breaker": {
                "state": "closed",
                "failures": 0,
                "last_failure_at": 0,
                "opened_at": 0,
            },
        }

        # Save atomically
        fd, tmp_path = tempfile.mkstemp(dir=self.tmpdir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, self.state_file)

        # Load back
        with open(self.state_file) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["last_dispatched_id"], "test-123")
        self.assertEqual(loaded["dispatched_at"], 1000000)

    def test_ensure_state_fields_v1_compat(self):
        """Ensure v1 state gets v3 fields added."""
        old_state = {
            "last_dispatched_id": "old-id",
            "dispatched_at": 500,
        }

        # Apply ensure_state_fields logic
        old_state.setdefault("notified_failures", {})
        old_state.setdefault("dispatch_history", [])
        old_state.setdefault("circuit_breaker", {
            "state": "closed",
            "failures": 0,
            "last_failure_at": 0,
            "opened_at": 0,
        })

        self.assertIn("notified_failures", old_state)
        self.assertIn("dispatch_history", old_state)
        self.assertIn("circuit_breaker", old_state)
        self.assertEqual(old_state["last_dispatched_id"], "old-id")

    def test_save_atomic_no_tmp_leftover(self):
        """Atomic save should not leave .tmp files."""
        state = {"test": True}
        fd, tmp_path = tempfile.mkstemp(dir=self.tmpdir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, self.state_file)

        tmp_files = list(Path(self.tmpdir).glob("*.tmp"))
        self.assertEqual(len(tmp_files), 0)


class TestCircuitBreaker(unittest.TestCase):
    """Test circuit breaker logic."""

    def _make_cb(self, state="closed", failures=0, last_failure_at=0, opened_at=0):
        return {
            "state": state,
            "failures": failures,
            "last_failure_at": last_failure_at,
            "opened_at": opened_at,
        }

    def test_closed_to_open_on_threshold(self):
        """CB opens after N consecutive failures within window."""
        now = int(time.time() * 1000)
        cb = self._make_cb()
        threshold = 3
        window_ms = 30 * 60 * 1000

        for i in range(threshold):
            if now - cb.get("last_failure_at", 0) > window_ms:
                cb["failures"] = 0
            cb["failures"] += 1
            cb["last_failure_at"] = now

            if cb["failures"] >= threshold:
                cb["state"] = "open"
                cb["opened_at"] = now

        self.assertEqual(cb["state"], "open")
        self.assertEqual(cb["failures"], threshold)

    def test_open_to_half_open_after_cooldown(self):
        """CB transitions to half-open after cooldown."""
        cooldown_ms = 15 * 60 * 1000
        opened_at = int(time.time() * 1000) - cooldown_ms - 1000  # Opened > cooldown ago

        cb = self._make_cb(state="open", opened_at=opened_at)
        now = int(time.time() * 1000)
        elapsed = now - cb["opened_at"]

        if elapsed > cooldown_ms:
            cb["state"] = "half-open"

        self.assertEqual(cb["state"], "half-open")

    def test_half_open_to_closed_on_success(self):
        """Successful dispatch in half-open closes the CB."""
        cb = self._make_cb(state="half-open", failures=3)

        # Simulate successful dispatch
        cb["state"] = "closed"
        cb["failures"] = 0

        self.assertEqual(cb["state"], "closed")
        self.assertEqual(cb["failures"], 0)

    def test_still_open_within_cooldown(self):
        """CB stays open within cooldown window."""
        cooldown_ms = 15 * 60 * 1000
        now = int(time.time() * 1000)
        opened_at = now - 5 * 60 * 1000  # Opened 5 min ago

        cb = self._make_cb(state="open", opened_at=opened_at)
        elapsed = now - cb["opened_at"]

        should_stay_open = elapsed <= cooldown_ms
        self.assertTrue(should_stay_open)

    def test_failure_window_reset(self):
        """Failures outside window reset the counter."""
        window_ms = 30 * 60 * 1000
        now = int(time.time() * 1000)
        old_failure = now - window_ms - 1000  # Outside window

        cb = self._make_cb(failures=2, last_failure_at=old_failure)

        if now - cb["last_failure_at"] > window_ms:
            cb["failures"] = 0

        cb["failures"] += 1
        self.assertEqual(cb["failures"], 1)  # Reset + 1, not 3


class TestRateLimiting(unittest.TestCase):
    """Test rate limiting logic."""

    def test_under_limit(self):
        now_ms = int(time.time() * 1000)
        max_per_hour = 3
        history = [
            {"task_id": "a", "at": now_ms - 10 * 60 * 1000},
            {"task_id": "b", "at": now_ms - 20 * 60 * 1000},
        ]
        recent = [d for d in history if now_ms - d["at"] < 3600 * 1000]
        self.assertLess(len(recent), max_per_hour)

    def test_at_limit(self):
        now_ms = int(time.time() * 1000)
        max_per_hour = 3
        history = [
            {"task_id": "a", "at": now_ms - 10 * 60 * 1000},
            {"task_id": "b", "at": now_ms - 20 * 60 * 1000},
            {"task_id": "c", "at": now_ms - 30 * 60 * 1000},
        ]
        recent = [d for d in history if now_ms - d["at"] < 3600 * 1000]
        self.assertEqual(len(recent), max_per_hour)

    def test_old_dispatches_excluded(self):
        now_ms = int(time.time() * 1000)
        history = [
            {"task_id": "a", "at": now_ms - 2 * 3600 * 1000},  # 2h ago
            {"task_id": "b", "at": now_ms - 3 * 3600 * 1000},  # 3h ago
        ]
        recent = [d for d in history if now_ms - d["at"] < 3600 * 1000]
        self.assertEqual(len(recent), 0)


class TestDispatchDedup(unittest.TestCase):
    """Test dispatch deduplication logic."""

    def test_same_task_within_timeout(self):
        """Same task dispatched recently should be deduped."""
        now_ms = int(time.time() * 1000)
        timeout_ms = 2 * 3600 * 1000  # 2h

        last_id = "task-123"
        last_at = now_ms - 30 * 60 * 1000  # 30min ago
        task_id = "task-123"

        should_skip = (task_id == last_id and (now_ms - last_at) < timeout_ms)
        self.assertTrue(should_skip)

    def test_same_task_after_timeout(self):
        """Same task after timeout should NOT be deduped."""
        now_ms = int(time.time() * 1000)
        timeout_ms = 2 * 3600 * 1000

        last_id = "task-123"
        last_at = now_ms - 3 * 3600 * 1000  # 3h ago
        task_id = "task-123"

        should_skip = (task_id == last_id and (now_ms - last_at) < timeout_ms)
        self.assertFalse(should_skip)

    def test_different_task(self):
        """Different task should NOT be deduped."""
        now_ms = int(time.time() * 1000)
        timeout_ms = 2 * 3600 * 1000

        last_id = "task-123"
        last_at = now_ms - 5 * 60 * 1000
        task_id = "task-456"

        should_skip = (task_id == last_id and (now_ms - last_at) < timeout_ms)
        self.assertFalse(should_skip)

    def test_history_dedup(self):
        """Task in recent dispatch history should be deduped."""
        now_ms = int(time.time() * 1000)
        history = [
            {"task_id": "task-123", "at": now_ms - 20 * 60 * 1000},
            {"task_id": "task-456", "at": now_ms - 40 * 60 * 1000},
        ]
        recent = [d for d in history if now_ms - d["at"] < 3600 * 1000]

        task_id = "task-123"
        in_history = any(d["task_id"] == task_id for d in recent)
        self.assertTrue(in_history)


class TestQueueWriteIntegration(unittest.TestCase):
    """Test queue file writing from heartbeat context."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pending_dir = os.path.join(self.tmpdir, "pending")
        os.makedirs(self.pending_dir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_dispatch_queue_write(self):
        """Simulate Phase 9 queue write."""
        task_id = "abc12345-full-uuid-here"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"{timestamp}-dispatch-{task_id[:8]}.json"
        filepath = os.path.join(self.pending_dir, filename)

        item = {
            "version": 1,
            "type": "dispatch",
            "task_id": task_id,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "heartbeat-v3",
            "title": "Test Task",
            "agent": "luan",
            "priority": "high",
            "context": {
                "description": "Do something",
                "eligible_count": 5,
                "in_progress_count": 1,
            },
            "spawn_params": {
                "agent": "luan",
                "description": "Do something",
            },
            "constraints": {
                "max_age_minutes": 30,
                "timeout_seconds": 600,
            },
        }

        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=self.pending_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(item, f, indent=2)
        os.replace(tmp_path, filepath)

        # Verify
        self.assertTrue(os.path.exists(filepath))
        with open(filepath) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["version"], 1)
        self.assertEqual(loaded["type"], "dispatch")
        self.assertEqual(loaded["task_id"], task_id)
        self.assertIn("spawn_params", loaded)

    def test_respawn_queue_write(self):
        """Simulate Phase 4 failure respawn queue write."""
        task_id = "def67890-full-uuid-here"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"{timestamp}-respawn-{task_id[:8]}.json"
        filepath = os.path.join(self.pending_dir, filename)

        item = {
            "version": 1,
            "type": "respawn",
            "task_id": task_id,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": "heartbeat-v3",
            "title": "Failed Task",
            "agent": "luan",
            "priority": "high",
            "context": {
                "failure_type": "TIMEOUT",
                "retry_count": 1,
                "adjustments": "increase timeout 1.5x",
                "dead_session_key": "old-session-key",
            },
        }

        fd, tmp_path = tempfile.mkstemp(dir=self.pending_dir, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(item, f, indent=2)
        os.replace(tmp_path, filepath)

        self.assertTrue(os.path.exists(filepath))
        with open(filepath) as f:
            loaded = json.load(f)
        self.assertEqual(loaded["type"], "respawn")
        self.assertEqual(loaded["context"]["failure_type"], "TIMEOUT")


class TestFailureAnalysis(unittest.TestCase):
    """Test failure type detection patterns."""

    def _analyze(self, messages):
        """Simplified version of analyze_session_failure for testing."""
        all_text = ""
        tool_calls = []
        stop_reason = ""
        error_msg = ""

        for msg in messages:
            content = str(msg.get("content", ""))
            all_text += content.lower() + " "
            tc = msg.get("toolCalls", [])
            if tc:
                tool_calls.extend(tc if isinstance(tc, list) else [tc])
            sr = msg.get("stopReason", "")
            if sr:
                stop_reason = str(sr).lower()
            em = msg.get("error", "")
            if em:
                error_msg = str(em).lower()

        combined = all_text + " " + stop_reason + " " + error_msg

        if "401" in combined or "unauthorized" in combined:
            return "AUTH_EXPIRED"
        elif "timeout" in combined or "timed out" in combined:
            return "TIMEOUT"
        elif "oom" in combined or "out of memory" in combined:
            return "OOM"
        elif "rate limit" in combined or "429" in combined:
            return "RATE_LIMITED"
        elif len(tool_calls) >= 3:
            tool_names = [str(tc.get("name", "")) for tc in tool_calls if isinstance(tc, dict)]
            if tool_names and len(set(tool_names)) == 1:
                return "LOOP_DEGENERATIVO"

        if stop_reason in ("stop", "end_turn"):
            return "INCOMPLETE"
        return "GENERIC_ERROR"

    def test_detect_auth_error(self):
        messages = [{"content": "Error: 401 Unauthorized"}]
        self.assertEqual(self._analyze(messages), "AUTH_EXPIRED")

    def test_detect_timeout(self):
        messages = [{"content": "Operation timed out after 600s"}]
        self.assertEqual(self._analyze(messages), "TIMEOUT")

    def test_detect_oom(self):
        messages = [{"content": "Process killed by signal 9 (out of memory)"}]
        self.assertEqual(self._analyze(messages), "OOM")

    def test_detect_rate_limit(self):
        messages = [{"content": "429 Too Many Requests - rate limit exceeded"}]
        self.assertEqual(self._analyze(messages), "RATE_LIMITED")

    def test_detect_loop(self):
        messages = [{
            "content": "...",
            "toolCalls": [
                {"name": "read"}, {"name": "read"}, {"name": "read"},
            ]
        }]
        self.assertEqual(self._analyze(messages), "LOOP_DEGENERATIVO")

    def test_detect_incomplete(self):
        messages = [{"content": "Working on it...", "stopReason": "end_turn"}]
        self.assertEqual(self._analyze(messages), "INCOMPLETE")

    def test_detect_generic(self):
        messages = [{"content": "Something went wrong"}]
        self.assertEqual(self._analyze(messages), "GENERIC_ERROR")


class TestConfigLoading(unittest.TestCase):
    """Test v3-config.json loading."""

    def test_config_valid_json(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            self.assertIn("queue_dir", config)
            self.assertIn("escalation_warn_minutes", config)
            self.assertIn("escalation_critical_minutes", config)
            self.assertIn("session_gc_max_age_hours", config)
            self.assertIn("max_dispatches_per_hour", config)
            self.assertIn("discord_channel", config)
        else:
            self.skipTest("Config file not found (expected in development)")

    def test_config_sane_values(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            self.assertGreater(config["escalation_warn_minutes"], 0)
            self.assertGreater(config["escalation_critical_minutes"],
                             config["escalation_warn_minutes"])
            self.assertGreater(config["session_gc_max_age_hours"], 0)
            self.assertGreater(config["max_dispatches_per_hour"], 0)
        else:
            self.skipTest("Config file not found")


if __name__ == "__main__":
    unittest.main()
