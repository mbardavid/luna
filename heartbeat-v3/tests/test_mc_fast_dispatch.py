"""
test_mc_fast_dispatch.py — Integration-like tests for mc-fast-dispatch.

These tests execute the shell dispatcher with fake `openclaw` and `curl` commands
so we can validate session-key extraction and in_progress behavior without any
real network calls.
"""

import json
import os
import subprocess
import tempfile
import unittest


class TestMcFastDispatch(unittest.TestCase):
    SCRIPT_PATH = "/home/openclaw/.openclaw/workspace/scripts/mc-fast-dispatch.sh"

    def _run_dispatch(self, task_status: str, openclaw_result: dict, *, agent: str = "luan"):
        with tempfile.TemporaryDirectory() as tmpdir:
            bin_dir = os.path.join(tmpdir, "bin")
            os.makedirs(bin_dir, exist_ok=True)
            log_path = os.path.join(tmpdir, "curl.log")
            openclaw_args_path = os.path.join(tmpdir, "openclaw-args.log")

            # Fake curl: logs calls and returns canned responses.
            curl_script = '''#!/usr/bin/env bash
set -euo pipefail

method="GET"
payload=""
url=""

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -X) method="$2"; shift 2 ;;
    -d) payload="$2"; shift 2 ;;
    -H) shift 2 ;;
    --*) shift ;;
    *) url="$1"; shift ;;
  esac

done

if [[ -n "${FAKE_CURL_LOG:-}" ]]; then
  printf "%s\n" "$method|$url|$payload" >> "$FAKE_CURL_LOG"
fi

if [[ "$method" == "GET" && "$url" == *"/api/v1/boards/"*"/tasks/"* ]]; then
  printf "%s" "${FAKE_TASK_JSON:-{}}"
else
  printf "{}"
fi
'''
            with open(os.path.join(bin_dir, "curl"), "w") as f:
                f.write(curl_script)
            os.chmod(os.path.join(bin_dir, "curl"), 0o755)

            fake_task = json.dumps(
                {
                    "id": "task-id-1234",
                    "status": task_status,
                    "title": "Test task",
                    "description": "Dispatch test task",
                    "custom_field_values": {},
                }
            )

            openclaw_script = '''#!/usr/bin/env bash
printf "%s\\n" "$*" >> "${FAKE_OPENCLAW_ARGS_LOG}"
if [ "$1" = "agent" ]; then
  printf "%s" "$FAKE_OPENCLAW_DISPATCH_RESULT"
else
  printf "%s" "$FAKE_OPENCLAW_TASK_JSON"
fi
'''
            with open(os.path.join(bin_dir, "openclaw"), "w") as f:
                f.write(openclaw_script)
            os.chmod(os.path.join(bin_dir, "openclaw"), 0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env.get('PATH', '')}",
                    "MC_API_URL": "http://localhost:8000",
                    "MC_API_TOKEN": "test-token",
                    "MC_BOARD_ID": "board-id",
                    "FAKE_CURL_LOG": log_path,
                    "FAKE_TASK_JSON": fake_task,
                    "FAKE_OPENCLAW_TASK_JSON": fake_task,
                    "FAKE_OPENCLAW_DISPATCH_RESULT": json.dumps(openclaw_result),
                    "FAKE_OPENCLAW_ARGS_LOG": openclaw_args_path,
                }
            )

            proc = subprocess.run(
                [
                    self.SCRIPT_PATH,
                    "--agent",
                    agent,
                    "--task",
                    "Run a dispatch",
                    "--title",
                    "Test task",
                    "--from-mc",
                    "task-id-1234",
                ],
                capture_output=True,
                text=True,
                env=env,
            )

            calls = []
            if os.path.exists(log_path):
                with open(log_path) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        method, url, payload = line.split("|", 2)
                        calls.append({"method": method, "url": url, "payload": payload})

            openclaw_args = []
            if os.path.exists(openclaw_args_path):
                with open(openclaw_args_path) as fh:
                    openclaw_args = [line.strip() for line in fh if line.strip()]

            return proc.returncode, proc.stdout, proc.stderr, calls, openclaw_args

    def test_extracts_session_from_result_sessionKey(self):
        rc, _, _, calls, _ = self._run_dispatch(
            "inbox",
            {
                "status": "completed",
                "result": {
                    "sessionKey": "agent:luan:session-target-111",
                    "meta": {"durationMs": 123},
                },
            },
        )

        self.assertEqual(rc, 0)

        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]

        # Non-review tasks should be marked in_progress before dispatch finishes.
        self.assertTrue(any(p.get("status") == "in_progress" for p in patch_payloads))

        # Last patch must store mc_session_key from target session.
        self.assertEqual(
            patch_payloads[-1].get("custom_field_values", {}).get("mc_session_key"),
            "agent:luan:session-target-111",
        )

    def test_extracts_session_from_payload_fallback_text(self):
        rc, _, _, calls, _ = self._run_dispatch(
            "inbox",
            {
                "status": "completed",
                "result": {
                    "payloads": [
                        {"text": "starting target=luan ... session=agent:luan:session-fallback-222"}
                    ],
                    "meta": {"durationMs": 321},
                },
            },
        )

        self.assertEqual(rc, 0)
        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]
        self.assertEqual(
            patch_payloads[-1].get("custom_field_values", {}).get("mc_session_key"),
            "agent:luan:session-fallback-222",
        )

    def test_prefers_dispatched_marker_session_over_result_fields(self):
        rc, _, _, calls, _ = self._run_dispatch(
            "inbox",
            {
                "status": "completed",
                "result": {
                    "sessionKey": "agent:luan:wrong-session",
                    "payloads": [
                        {"text": "some noise"},
                        {"text": "DISPATCHED session=agent:luan:session-dispatched-999"},
                    ],
                    "meta": {"durationMs": 444},
                },
            },
        )

        self.assertEqual(rc, 0)
        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]
        self.assertEqual(
            patch_payloads[-1].get("custom_field_values", {}).get("mc_session_key"),
            "agent:luan:session-dispatched-999",
        )

    def test_review_tasks_updates_session_key(self):
        _, _, _, calls, _ = self._run_dispatch(
            "review",
            {
                "status": "completed",
                "result": {
                    "sessionKey": "agent:luan:session-review-333",
                },
            },
        )

        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]

        # Even when already in review, session linkage must still be written.
        self.assertTrue(any(p.get("custom_field_values") for p in patch_payloads))
        self.assertEqual(
            patch_payloads[-1].get("custom_field_values", {}).get("mc_session_key"),
            "agent:luan:session-review-333",
        )

    def test_rollback_when_no_session_found(self):
        rc, _, stderr, calls, _ = self._run_dispatch(
            "inbox",
            {
                "status": "completed",
                "result": {
                    "payloads": [{"text": "DISPATCHED but no session key in output"}],
                },
            },
        )

        self.assertNotEqual(rc, 0)
        self.assertIn("Dispatch failed", stderr)

        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]

        # Must rollback to inbox and clear mc_session_key on dispatch failure.
        self.assertEqual(patch_payloads[-1].get("status"), "inbox")
        self.assertEqual(
            patch_payloads[-1].get("fields", {}).get("mc_session_key"),
            "",
        )
        self.assertIn("dispatch response lacked target session_key", patch_payloads[-1].get("fields", {}).get("mc_last_error", ""))

    def test_extracts_session_from_result_meta_system_prompt_report(self):
        rc, _, _, calls, _ = self._run_dispatch(
            "inbox",
            {
                "status": "ok",
                "result": {
                    "meta": {
                        "durationMs": 2413,
                        "systemPromptReport": {
                            "sessionKey": "agent:cto-ops:main",
                        },
                    },
                },
            },
            agent="cto-ops",
        )

        self.assertEqual(rc, 0)
        patch_payloads = [
            json.loads(c["payload"])
            for c in calls
            if c.get("method") == "PATCH"
            and "/api/v1/boards/board-id/tasks/task-id-1234" in c.get("url", "")
        ]
        self.assertEqual(
            patch_payloads[-1].get("custom_field_values", {}).get("mc_session_key"),
            "agent:cto-ops:main",
        )

    def test_cto_ops_bypasses_dispatcher_and_runs_direct(self):
        rc, _, _, _, openclaw_args = self._run_dispatch(
            "inbox",
            {
                "status": "ok",
                "result": {
                    "meta": {
                        "durationMs": 2413,
                        "systemPromptReport": {
                            "sessionKey": "agent:cto-ops:main",
                        },
                    },
                },
            },
            agent="cto-ops",
        )

        self.assertEqual(rc, 0)
        self.assertTrue(openclaw_args)
        self.assertIn("--agent cto-ops", openclaw_args[0])


if __name__ == "__main__":
    unittest.main()
