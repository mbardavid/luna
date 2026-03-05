import json
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(WORKSPACE / "heartbeat-v3" / "scripts"))
sys.path.insert(0, str(WORKSPACE / "scripts"))

from mc_control import is_claim_active, is_luna_review_task, normalize_status, task_dispatch_policy, task_status
from mc_queue_audit import audit_queue


def select_next_action(tasks: list[dict]) -> tuple[str, str]:
    review_candidates = [
        task for task in sorted(tasks, key=lambda t: t.get("created_at", ""))
        if is_luna_review_task(task) and not is_claim_active(task)
    ]
    if review_candidates:
        return ("review", review_candidates[0]["id"])

    inbox_candidates = [
        task for task in sorted(tasks, key=lambda t: t.get("created_at", ""))
        if task_status(task) == "inbox" and task_dispatch_policy(task) == "auto"
    ]
    if inbox_candidates:
        return ("dispatch", inbox_candidates[0]["id"])
    return ("idle", "")


class TestIncidentReplays(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads((ROOT / "fixtures" / "mission_control_incidents.json").read_text(encoding="utf-8"))

    def test_226588ab_backlog_stays_inbox(self):
        task = self.fixtures["226588ab_backlog"]["task"]
        self.assertEqual(task_dispatch_policy(task), "backlog")
        self.assertEqual(select_next_action([task]), ("idle", ""))

    def test_review_priority_blocks_inbox(self):
        tasks = self.fixtures["review_priority"]["tasks"]
        action, task_id = select_next_action(tasks)
        self.assertEqual(action, "review")
        self.assertEqual(task_id, "34877d8a-3992-4015-a725-f074b47627e9")

    def test_awaiting_human_does_not_auto_dispatch(self):
        task = self.fixtures["awaiting_human_hold"]["task"]
        self.assertEqual(normalize_status(task["status"]), "awaiting_human")
        self.assertEqual(select_next_action([task]), ("idle", ""))

    def test_duplicate_meta_and_invalid_done_are_replayable(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            queue = tmpdir / "queue"
            for name in ("pending", "active", "done", "failed"):
                (queue / name).mkdir(parents=True, exist_ok=True)

            (queue / "done" / "duplicate.json.meta").write_text(
                json.dumps(self.fixtures["8cc49d51_duplicate_done_meta"]["meta"]),
                encoding="utf-8",
            )
            (queue / "done" / "invalid.json").write_text(
                json.dumps(self.fixtures["invalid_done_completion"]["done_item"]),
                encoding="utf-8",
            )

            report = audit_queue(queue)
            self.assertEqual(report["duplicate_meta_markers_total"], 1)
            self.assertEqual(report["invalid_done_total"], 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
