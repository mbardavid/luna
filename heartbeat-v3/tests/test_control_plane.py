import unittest
import sys
from pathlib import Path

_scripts_dir = str(Path(__file__).resolve().parent)
if _scripts_dir.endswith("/tests"):
    _scripts_dir = str(Path(_scripts_dir).parent / "scripts")
sys.path.insert(0, _scripts_dir)

from mc_control import (
    apply_dev_loop_transition,
    claim_review,
    normalize_dispatch_policy,
    normalize_status,
    queue_key_for_task,
    route_dev_loop_intake,
    task_dispatch_policy,
)


class TestCanonicalStatus(unittest.TestCase):
    def test_needs_approval_normalizes_to_awaiting_human(self):
        self.assertEqual(normalize_status("needs_approval"), "awaiting_human")

    def test_dispatch_policy_defaults_to_auto(self):
        self.assertEqual(normalize_dispatch_policy(""), "auto")
        self.assertEqual(normalize_dispatch_policy("human_hold"), "human_hold")

    def test_dispatch_policy_falls_back_to_description_marker(self):
        task = {"description": "Dispatch Policy: backlog\n\nBody"}
        self.assertEqual(task_dispatch_policy(task), "backlog")


class TestDevLoopRouting(unittest.TestCase):
    def setUp(self):
        self.task = {
            "id": "12345678-aaaa-bbbb-cccc-1234567890ab",
            "title": "Implement feature",
            "status": "inbox",
            "custom_field_values": {"mc_workflow": "dev_loop_v1"},
        }

    def test_route_dev_loop_intake(self):
        update = route_dev_loop_intake(self.task)
        self.assertEqual(update["status"], "review")
        self.assertEqual(update["fields"]["mc_phase"], "luna_task_planning")
        self.assertEqual(update["fields"]["mc_phase_owner"], "luna")
        self.assertTrue(update["fields"]["mc_phase_started_at"])

    def test_plan_validation_approval_advances_to_execution(self):
        result = apply_dev_loop_transition(
            "luna_plan_validation",
            {"mc_workflow": "dev_loop_v1", "mc_phase_retry_count": 0},
            "in_progress",
            review_reason="",
        )
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(result["fields"]["mc_phase"], "luan_execution_and_tests")

    def test_plan_validation_rejection_returns_to_planning(self):
        result = apply_dev_loop_transition(
            "luna_plan_validation",
            {"mc_workflow": "dev_loop_v1", "mc_phase_retry_count": 0},
            "in_progress",
            review_reason="missing tests",
        )
        self.assertEqual(result["fields"]["mc_phase"], "luan_plan_elaboration")
        self.assertEqual(result["fields"]["mc_rejection_feedback"], "missing tests")

    def test_final_validation_rejection_increments_retry(self):
        result = apply_dev_loop_transition(
            "luna_final_validation",
            {"mc_workflow": "dev_loop_v1", "mc_phase_retry_count": 2},
            "in_progress",
            review_reason="flaky test",
        )
        self.assertEqual(result["fields"]["mc_phase"], "luan_execution_and_tests")
        self.assertEqual(result["fields"]["mc_phase_retry_count"], 3)

    def test_final_validation_done_marks_completed(self):
        result = apply_dev_loop_transition(
            "luna_final_validation",
            {"mc_workflow": "dev_loop_v1", "mc_phase_retry_count": 0},
            "done",
            artifacts=["artifacts/mc/final-qa.md"],
        )
        self.assertEqual(result["fields"]["mc_phase"], "done")
        self.assertEqual(result["fields"]["mc_phase_state"], "completed")
        self.assertEqual(result["fields"]["mc_validation_artifact"], "artifacts/mc/final-qa.md")
        self.assertTrue(result["fields"]["mc_phase_completed_at"])

    def test_claim_review_sets_claim_metadata(self):
        review_task = {
            "id": self.task["id"],
            "status": "review",
            "custom_field_values": {
                "mc_workflow": "dev_loop_v1",
                "mc_phase": "luna_final_validation",
                "mc_phase_owner": "luna",
            },
        }
        claim = claim_review(review_task, "judge-loop-worker", lease_minutes=5)
        self.assertEqual(claim["fields"]["mc_phase_state"], "claimed")
        self.assertEqual(claim["fields"]["mc_claimed_by"], "judge-loop-worker")
        self.assertTrue(claim["fields"]["mc_claim_expires_at"])

    def test_queue_key_is_phase_aware(self):
        task = {
            "id": self.task["id"],
            "status": "inbox",
            "custom_field_values": {"mc_workflow": "dev_loop_v1", "mc_phase": "intake"},
        }
        key_a = queue_key_for_task(task, "dispatch")
        task["custom_field_values"]["mc_phase"] = "luna_task_planning"
        task["status"] = "review"
        key_b = queue_key_for_task(task, "dispatch")
        self.assertNotEqual(key_a, key_b)


if __name__ == "__main__":
    unittest.main()
