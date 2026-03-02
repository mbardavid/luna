"""
test_absorbed_detectors.py — Tests for Phase 1 absorbed detector functionality.

Tests:
  - PMM Health Check (3 tests)
  - Failure Classification (4 tests)
  - Description Quality Audit (3 tests)
  - Completion Detection / qa-review (3 tests)
  - Queue Consumer qa-review (2 tests)
  - State schema expansion (1 test)

Total: 16 tests

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

# Add scripts dir to path for imports
_scripts_dir = str(Path(__file__).resolve().parent.parent / "scripts")
sys.path.insert(0, _scripts_dir)

# Import queue consumer
import importlib
_spec = importlib.util.spec_from_file_location(
    "queue_consumer",
    os.path.join(_scripts_dir, "queue-consumer.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
QueueConsumer = _mod.QueueConsumer


# ============================================================
# Helper: Load heartbeat-v3 functions without executing main code
# ============================================================
# We can't import heartbeat-v3.py directly because it runs on import
# (has module-level code with gateway calls, locks, etc.)
# Instead, we test the logic patterns directly.

def _ensure_state_fields(state: dict) -> dict:
    """Mirror of heartbeat-v3.py ensure_state_fields with absorbed{} expansion."""
    state.setdefault("last_dispatched_id", "")
    state.setdefault("dispatched_at", 0)
    state.setdefault("notified_failures", {})
    state.setdefault("dispatch_history", [])
    state.setdefault("circuit_breaker", {
        "state": "closed",
        "failures": 0,
        "last_failure_at": 0,
        "opened_at": 0,
    })
    cb = state["circuit_breaker"]
    cb.setdefault("state", "closed")
    cb.setdefault("failures", 0)
    cb.setdefault("last_failure_at", 0)
    cb.setdefault("opened_at", 0)
    state.setdefault("review_dispatched", {})
    state.setdefault("absorbed", {
        "pmm_restarts": [],
        "alerted_description_violations": {},
        "completion_pending_notified": {},
    })
    absorbed = state["absorbed"]
    absorbed.setdefault("pmm_restarts", [])
    absorbed.setdefault("alerted_description_violations", {})
    absorbed.setdefault("completion_pending_notified", {})
    return state


def _classify_failure_from_messages(messages: list, loop_threshold: int = 5,
                                     known_errors: list = None) -> tuple:
    """
    Mirror of heartbeat-v3.py classify_failure logic, but takes messages directly
    (no gateway call). Tests the classification algorithm.
    """
    if known_errors is None:
        known_errors = ["thinking.signature", "RESOURCE_EXHAUSTED", "capacity"]

    failure_type = "GENERIC_ERROR"
    adjustments = "re-tentar sem ajustes específicos"

    all_text = ""
    tool_calls = []
    stop_reason = ""
    error_msg = ""

    for msg in messages:
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        all_text += content.lower() + " "
        tc = msg.get("toolCalls", msg.get("tool_calls", []))
        if tc:
            tool_calls.extend(tc if isinstance(tc, list) else [tc])
        sr = msg.get("stopReason", msg.get("stop_reason", ""))
        if sr:
            stop_reason = str(sr).lower()
        em = msg.get("errorMessage", msg.get("error_message", msg.get("error", "")))
        if em:
            error_msg = str(em).lower()

    combined = all_text + " " + stop_reason + " " + error_msg

    # Known provider errors
    for known_error in known_errors:
        if known_error.lower() in combined:
            if "thinking.signature" in known_error.lower():
                return "THINKING_SIGNATURE", f"erro de provider ({known_error}), trocar modelo ou aguardar"
            return "PROVIDER_ERROR", f"erro de provider ({known_error}), trocar modelo ou aguardar"

    # Auth
    if "401" in combined or "unauthorized" in combined:
        return "PROVIDER_ERROR", "verificar credenciais, possivelmente trocar modelo"

    # Timeout
    if "timeout" in combined or "timed out" in combined:
        return "TIMEOUT", "aumentar runTimeoutSeconds (1.5x)"

    # OOM
    if "oom" in combined or "out of memory" in combined or "signal 9" in combined:
        return "PROVIDER_ERROR", "reduzir contexto, adicionar constraint de brevidade"

    # Rate limit
    if "rate limit" in combined or "429" in combined:
        return "PROVIDER_ERROR", "aguardar cooldown, possivelmente trocar modelo"

    # Loop degenerativo
    if len(tool_calls) >= loop_threshold:
        tool_names = [
            str(tc.get("name", tc.get("function", {}).get("name", "")))
            for tc in tool_calls if isinstance(tc, dict)
        ]
        if tool_names and len(set(tool_names)) == 1:
            return "LOOP_DEGENERATIVO", "simplificar task, trocar modelo"

    # Incomplete
    if stop_reason in ("stop", "end_turn"):
        return "INCOMPLETE", "re-spawn com 'continue de onde parou'"

    return failure_type, adjustments


def _check_description_quality(tasks: list, state: dict,
                                min_length: int = 200,
                                markers: list = None,
                                check_statuses: set = None) -> list:
    """Mirror of heartbeat-v3.py check_description_quality logic."""
    if markers is None:
        markers = ["## ", "Objective", "Objetivo", "Context", "Criteria", "Problem", "Approach"]
    if check_statuses is None:
        check_statuses = {"inbox", "in_progress", "review"}

    absorbed = state.get("absorbed", {})
    alerted = absorbed.get("alerted_description_violations", {})
    violations = []

    for task in tasks:
        status = str(task.get("status", "")).lower()
        if status not in check_statuses:
            continue

        task_id = task.get("id", "")
        if task_id in alerted:
            continue

        desc = task.get("description", "") or ""
        title = task.get("title", "?")
        issues = []

        if len(desc) < min_length:
            issues.append(f"short ({len(desc)} chars)")

        if not any(marker in desc for marker in markers) and len(desc) < 500:
            issues.append("no structure")

        if issues:
            violations.append({
                "task_id": task_id,
                "title": title[:50],
                "status": status,
                "issues": ", ".join(issues),
            })
            alerted[task_id] = {"at": int(time.time() * 1000)}

    absorbed["alerted_description_violations"] = alerted
    state["absorbed"] = absorbed
    return violations


def _check_session_completion(messages: list) -> str:
    """Mirror of heartbeat-v3.py check_session_completion logic (takes messages directly)."""
    for msg in reversed(messages):
        content = str(msg.get("content", "") or msg.get("text", "") or "")
        if "COMPLETION_STATUS:" in content:
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("COMPLETION_STATUS:"):
                    status_val = line.split(":", 1)[1].strip().lower()
                    if status_val in ("complete", "partial", "blocked"):
                        return status_val
        if "status:" in content.lower() and ("complete" in content.lower() or "partial" in content.lower()):
            if "complete" in content.lower():
                return "complete"
            if "partial" in content.lower():
                return "partial"
    return ""


# ============================================================
# TESTS
# ============================================================

class TestStateSchemaExpansion(unittest.TestCase):
    """Test that state file schema includes absorbed{} section."""

    def test_ensure_state_fields_adds_absorbed(self):
        """Bare state gets absorbed{} with all sub-fields."""
        state = {}
        state = _ensure_state_fields(state)
        self.assertIn("absorbed", state)
        absorbed = state["absorbed"]
        self.assertIn("pmm_restarts", absorbed)
        self.assertIn("alerted_description_violations", absorbed)
        self.assertIn("completion_pending_notified", absorbed)
        self.assertIsInstance(absorbed["pmm_restarts"], list)
        self.assertIsInstance(absorbed["alerted_description_violations"], dict)
        self.assertIsInstance(absorbed["completion_pending_notified"], dict)

    def test_ensure_state_fields_preserves_existing_absorbed(self):
        """Pre-existing absorbed data is preserved, missing sub-fields added."""
        state = {
            "absorbed": {
                "pmm_restarts": [{"at": 1000, "pid": 123}],
            }
        }
        state = _ensure_state_fields(state)
        self.assertEqual(len(state["absorbed"]["pmm_restarts"]), 1)
        self.assertIn("alerted_description_violations", state["absorbed"])
        self.assertIn("completion_pending_notified", state["absorbed"])


class TestPMMHealthCheck(unittest.TestCase):
    """Test PMM health check logic."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.pid_file = os.path.join(self.tmpdir, "test.pid")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_pmm_alive_returns_healthy(self):
        """PID exists, process running → alive=True, restarted=False."""
        # Write our own PID (we know it's alive)
        my_pid = os.getpid()
        with open(self.pid_file, "w") as f:
            f.write(str(my_pid))

        # Simulate the PID check logic
        with open(self.pid_file) as f:
            pid = int(f.read().strip())

        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True

        self.assertTrue(alive)
        self.assertEqual(pid, my_pid)

    def test_pmm_dead_detected(self):
        """PID exists, process dead → alive=False."""
        # Use a PID that almost certainly doesn't exist
        dead_pid = 99999
        # Make sure it's actually dead
        try:
            os.kill(dead_pid, 0)
            # If it exists, skip this test
            self.skipTest(f"PID {dead_pid} unexpectedly exists")
        except ProcessLookupError:
            pass
        except PermissionError:
            self.skipTest(f"PID {dead_pid} exists but no permission")

        with open(self.pid_file, "w") as f:
            f.write(str(dead_pid))

        with open(self.pid_file) as f:
            pid = int(f.read().strip())

        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False

        self.assertFalse(alive)

    def test_pmm_no_pid_file_returns_none(self):
        """No PID file → alive=None, no restart attempted."""
        nonexistent = os.path.join(self.tmpdir, "nonexistent.pid")
        exists = os.path.exists(nonexistent)
        self.assertFalse(exists)
        # When PID file doesn't exist, alive should be None
        alive = None if not exists else False
        self.assertIsNone(alive)

    def test_pmm_restart_cooldown_suppression(self):
        """Max restarts/hour suppresses further restarts."""
        now_ms = int(time.time() * 1000)
        max_restarts = 3
        pmm_restarts = [
            {"at": now_ms - 10 * 60 * 1000, "pid": 100},
            {"at": now_ms - 5 * 60 * 1000, "pid": 101},
            {"at": now_ms - 2 * 60 * 1000, "pid": 102},
        ]

        # Filter to last hour
        one_hour_ago = now_ms - 3600 * 1000
        recent = [r for r in pmm_restarts if r.get("at", 0) > one_hour_ago]

        should_suppress = len(recent) >= max_restarts
        self.assertTrue(should_suppress, "Should suppress restart after max restarts/hour")


class TestFailureClassification(unittest.TestCase):
    """Test enhanced 6-category failure classification."""

    def test_classify_loop_degenerativo(self):
        """5+ identical tool calls → LOOP_DEGENERATIVO."""
        messages = [{
            "content": "trying...",
            "toolCalls": [
                {"name": "web_fetch"}, {"name": "web_fetch"}, {"name": "web_fetch"},
                {"name": "web_fetch"}, {"name": "web_fetch"},
            ]
        }]
        failure_type, _ = _classify_failure_from_messages(messages, loop_threshold=5)
        self.assertEqual(failure_type, "LOOP_DEGENERATIVO")

    def test_classify_thinking_signature(self):
        """Error contains 'thinking.signature' → THINKING_SIGNATURE."""
        messages = [{
            "content": "",
            "error": "thinking.signature: Field required",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "THINKING_SIGNATURE")

    def test_classify_incomplete(self):
        """stopReason=stop, no COMPLETION_STATUS → INCOMPLETE."""
        messages = [{
            "content": "Working on the implementation...",
            "stopReason": "end_turn",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "INCOMPLETE")

    def test_classify_timeout(self):
        """Session exceeded timeout → TIMEOUT."""
        messages = [{
            "content": "Operation timed out after 600s",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "TIMEOUT")

    def test_classify_provider_error_429(self):
        """429 rate limit → PROVIDER_ERROR."""
        messages = [{
            "content": "429 Too Many Requests",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "PROVIDER_ERROR")

    def test_classify_provider_error_resource_exhausted(self):
        """RESOURCE_EXHAUSTED → PROVIDER_ERROR."""
        messages = [{
            "content": "",
            "error": "RESOURCE_EXHAUSTED: quota exceeded",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "PROVIDER_ERROR")

    def test_classify_generic_error(self):
        """Unclassifiable error → GENERIC_ERROR."""
        messages = [{
            "content": "Something unexpected happened",
        }]
        failure_type, _ = _classify_failure_from_messages(messages)
        self.assertEqual(failure_type, "GENERIC_ERROR")

    def test_classify_mixed_tools_not_loop(self):
        """Multiple different tool calls should NOT be classified as loop."""
        messages = [{
            "content": "...",
            "toolCalls": [
                {"name": "read"}, {"name": "write"}, {"name": "exec"},
                {"name": "read"}, {"name": "write"},
            ]
        }]
        failure_type, _ = _classify_failure_from_messages(messages, loop_threshold=5)
        # Different tool names → not a loop → should fall through to GENERIC_ERROR
        self.assertNotEqual(failure_type, "LOOP_DEGENERATIVO")


class TestDescriptionQuality(unittest.TestCase):
    """Test description quality audit."""

    def test_description_too_short_flagged(self):
        """Description < 200 chars → violation."""
        tasks = [{
            "id": "task-001",
            "title": "Short Task",
            "status": "inbox",
            "description": "Do something",
        }]
        state = _ensure_state_fields({})
        violations = _check_description_quality(tasks, state)
        self.assertEqual(len(violations), 1)
        self.assertIn("short", violations[0]["issues"])

    def test_description_no_structure_flagged(self):
        """No ## headers and short → violation with 'no structure'."""
        tasks = [{
            "id": "task-002",
            "title": "Unstructured Task",
            "status": "in_progress",
            "description": "This is a task that needs to be done but has no structure markers at all. It's just a plain paragraph of text.",
        }]
        state = _ensure_state_fields({})
        violations = _check_description_quality(tasks, state)
        self.assertEqual(len(violations), 1)
        self.assertIn("no structure", violations[0]["issues"])

    def test_description_good_not_flagged(self):
        """200+ chars with structure → no violation."""
        long_desc = (
            "## Objective\n\n"
            "Implement the new feature as described in the design doc. "
            "The implementation should follow the existing patterns in the codebase.\n\n"
            "## Context\n\n"
            "This feature is needed because the current implementation has limitations. "
            "Specifically, it doesn't handle edge cases properly and lacks proper error handling.\n\n"
            "## Criteria\n\n"
            "- All tests pass\n"
            "- Edge cases handled\n"
            "- Error handling implemented\n"
        )
        tasks = [{
            "id": "task-003",
            "title": "Good Task",
            "status": "review",
            "description": long_desc,
        }]
        state = _ensure_state_fields({})
        violations = _check_description_quality(tasks, state)
        self.assertEqual(len(violations), 0)

    def test_description_dedup(self):
        """Same task should only be flagged once (dedup via state)."""
        tasks = [{
            "id": "task-004",
            "title": "Repeated Task",
            "status": "inbox",
            "description": "Short",
        }]
        state = _ensure_state_fields({})

        # First check
        violations1 = _check_description_quality(tasks, state)
        self.assertEqual(len(violations1), 1)

        # Second check — same task should be deduped
        violations2 = _check_description_quality(tasks, state)
        self.assertEqual(len(violations2), 0)

    def test_description_skips_done_status(self):
        """Tasks with status 'done' should not be checked."""
        tasks = [{
            "id": "task-005",
            "title": "Done Task",
            "status": "done",
            "description": "Short",
        }]
        state = _ensure_state_fields({})
        violations = _check_description_quality(tasks, state)
        self.assertEqual(len(violations), 0)


class TestCompletionDetection(unittest.TestCase):
    """Test completion pending QA detection."""

    def test_completion_status_complete_detected(self):
        """Dead session with COMPLETION_STATUS: complete → 'complete'."""
        messages = [{
            "content": "## Completion Report\n\nCOMPLETION_STATUS: complete\nFILES_CHANGED: 3\n",
        }]
        result = _check_session_completion(messages)
        self.assertEqual(result, "complete")

    def test_completion_status_partial_detected(self):
        """Dead session with COMPLETION_STATUS: partial → 'partial'."""
        messages = [{
            "content": "Task partially done.\nCOMPLETION_STATUS: partial\n",
        }]
        result = _check_session_completion(messages)
        self.assertEqual(result, "partial")

    def test_no_completion_status_returns_empty(self):
        """Dead session without COMPLETION_STATUS → ''."""
        messages = [{
            "content": "Working on things...",
        }]
        result = _check_session_completion(messages)
        self.assertEqual(result, "")

    def test_completion_status_blocked_detected(self):
        """Dead session with COMPLETION_STATUS: blocked → 'blocked'."""
        messages = [{
            "content": "COMPLETION_STATUS: blocked\nBLOCKERS: missing API key\n",
        }]
        result = _check_session_completion(messages)
        self.assertEqual(result, "blocked")


class TestQueueConsumerQaReview(unittest.TestCase):
    """Test queue-consumer.py qa-review type support."""

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

    def test_queue_consumer_accepts_qa_review(self):
        """qa-review item is processed without error."""
        self._write_item("20260302T230000-qa-review-abc12345.json", {
            "version": 1,
            "type": "qa-review",
            "task_id": "abc12345-full-uuid",
            "title": "Test QA Task",
            "agent": "main",
            "priority": "high",
            "context": {
                "task_id": "abc12345-full-uuid",
                "task_title": "Test QA Task",
                "session_key": "agent:luan:subagent:test-session",
                "completion_status": "complete",
                "action": "QA review",
            },
        })

        result = self.consumer.consume_one()
        self.assertIsNotNone(result, "qa-review item should be consumed")
        self.assertEqual(result["action"], "qa-review")
        self.assertEqual(result["agent"], "main")
        self.assertEqual(result["task_id"], "abc12345-full-uuid")

    def test_qa_review_generates_action_brief(self):
        """qa-review produces structured action brief with spawn_params."""
        self._write_item("20260302T230100-qa-review-def67890.json", {
            "version": 1,
            "type": "qa-review",
            "task_id": "def67890-full-uuid",
            "title": "Completed Feature",
            "agent": "main",
            "priority": "high",
            "context": {
                "task_id": "def67890-full-uuid",
                "task_title": "Completed Feature",
                "session_key": "agent:luan:subagent:session-xyz",
                "completion_status": "partial",
            },
        })

        result = self.consumer.consume_one()
        self.assertIsNotNone(result)
        self.assertIn("spawn_params", result)
        self.assertIn("message", result["spawn_params"])
        self.assertEqual(result["spawn_params"]["agent"], "main")

        # Check the message contains QA-specific content
        msg = result["spawn_params"]["message"]
        self.assertIn("QA Review", msg)
        self.assertIn("def67890", msg)
        self.assertIn("partial", msg)
        self.assertIn("Completed Feature", msg)
        self.assertIn("mc-client.sh", msg)

    def test_qa_review_label_format(self):
        """qa-review spawn label follows hb-qa-review-{short_id} format."""
        self._write_item("20260302T230200-qa-review-abc12345.json", {
            "version": 1,
            "type": "qa-review",
            "task_id": "abc12345-full-uuid",
            "title": "Test",
            "context": {
                "session_key": "s1",
                "completion_status": "complete",
            },
        })

        result = self.consumer.consume_one()
        self.assertIsNotNone(result)
        label = result["spawn_params"]["label"]
        self.assertTrue(label.startswith("hb-qa-review-"), f"Label '{label}' doesn't match expected format")


class TestConfigSections(unittest.TestCase):
    """Test that v3-config.json has all required sections."""

    def test_config_has_pmm_section(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        if not config_path.exists():
            self.skipTest("Config file not found")
        with open(config_path) as f:
            config = json.load(f)
        self.assertIn("pmm", config)
        pmm = config["pmm"]
        self.assertIn("auto_restart", pmm)
        self.assertIn("pid_file", pmm)
        self.assertIn("restart_cooldown_minutes", pmm)
        self.assertIn("max_restarts_per_hour", pmm)
        self.assertIn("env_file", pmm)
        self.assertIn("default_config", pmm)

    def test_config_has_description_quality_section(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        if not config_path.exists():
            self.skipTest("Config file not found")
        with open(config_path) as f:
            config = json.load(f)
        self.assertIn("description_quality", config)
        dq = config["description_quality"]
        self.assertIn("min_length", dq)
        self.assertIn("required_markers", dq)
        self.assertIn("check_statuses", dq)
        self.assertIsInstance(dq["required_markers"], list)
        self.assertGreater(dq["min_length"], 0)

    def test_config_has_failure_classification_section(self):
        config_path = Path(__file__).resolve().parent.parent / "config" / "v3-config.json"
        if not config_path.exists():
            self.skipTest("Config file not found")
        with open(config_path) as f:
            config = json.load(f)
        self.assertIn("failure_classification", config)
        fc = config["failure_classification"]
        self.assertIn("loop_threshold", fc)
        self.assertIn("known_provider_errors", fc)
        self.assertIsInstance(fc["known_provider_errors"], list)
        self.assertGreater(fc["loop_threshold"], 0)


if __name__ == "__main__":
    unittest.main()
