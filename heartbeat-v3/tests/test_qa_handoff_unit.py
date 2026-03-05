import hashlib
import json
import re
from pathlib import Path



def _load_qa_helpers():
    """Load only QA helper functions from heartbeat-v3.py without running main loop."""
    src = Path("heartbeat-v3/scripts/heartbeat-v3.py").read_text()
    start = src.index("def _hash_qa_handoff")
    end = src.index("\n\ndef send_discord")
    ns = {
        "log": lambda *_args, **_kwargs: None,
        "run_cmd": lambda *args, **kwargs: "[]",
        "json": json,
        "hashlib": hashlib,
        "re": re,
        "QA_HANDOFF_PREFIX": "QA_HANDOFF v1 fp=",
    }
    exec(src[start:end], ns)
    return ns


ns = _load_qa_helpers()


ndef_handoff = ns["_build_qa_handoff_block"]
extract_latest = ns["_extract_latest_qa_handoff_fp"]
extract_rejection = ns["_extract_qa_rejection_feedback"]


def test_build_qa_handoff_block_contains_required_sections():
    task = {
        "id": "task-123",
        "title": "Fix issue",
        "custom_field_values": {
            "mc_output_summary": "artifact-a\nartifact-b",
            "mc_acceptance_criteria": "AC1\nAC2",
            "mc_qa_checks": "pytest -k quick\ncoverage check",
            "mc_rejection_feedback": "Falha na checagem",
        },
    }
    fp, comment, ctx = ndef_handoff(task, {"reason": "Falha na checagem"}, 2)

    assert comment.startswith("QA_HANDOFF v1 fp=")
    assert fp in comment
    assert "Falha na checagem" in comment
    assert "Artefatos" in comment
    assert "AC" in comment
    assert "Checks" in comment
    assert ctx["retry_count"] == 2
    assert ctx["result"] == "rejected"


def test_qa_handoff_comment_dedupe_scan_uses_latest_comment():
    comments = [
        "hello world",
        "QA_HANDOFF v1 fp=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n-old payload",
        "QA_HANDOFF v1 fp=1234567890abcdef1234567890abcdef12345678\n-new payload",
    ]
    assert extract_latest(comments) == "1234567890abcdef1234567890abcdef12345678"


def test_rejection_feedback_from_status_comment_marker():
    task = {
        "status": "review",
        "custom_field_values": {},
    }
    rejection = extract_rejection(task, ["[luna-review-reject]\nCorrigir endpoint"])
    assert rejection
    assert rejection["reason"] == "Corrigir endpoint"
