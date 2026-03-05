#!/usr/bin/env python3
"""Mission Control queue audit helpers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent
DEFAULT_QUEUE_DIR = WORKSPACE / "heartbeat-v3" / "queue"
DEFAULT_METRICS_FILE = WORKSPACE / "state" / "control-loop-metrics.json"
DEFAULT_REPORT_FILE = WORKSPACE / "artifacts" / "reports" / "mc-queue-audit-latest.json"
MC_CONTROL_DIR = WORKSPACE / "heartbeat-v3" / "scripts"

if str(MC_CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(MC_CONTROL_DIR))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _done_identity(payload: dict[str, Any]) -> str:
    queue_key = str(payload.get("queue_key") or "").strip()
    if queue_key:
        return queue_key
    phase = str(payload.get("phase") or payload.get("status") or payload.get("type") or "").strip()
    return "|".join(
        [
            str(payload.get("task_id") or "").strip(),
            str(payload.get("type") or "").strip(),
            phase,
        ]
    )


def _is_post_recovery_schema(payload: dict[str, Any]) -> bool:
    return any(key in payload for key in ("queue_key", "workflow", "phase", "dispatch_policy"))


def classify_done_item(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    result = result if isinstance(result, dict) else {}
    action = str(result.get("action") or "").strip()
    session_id = str(result.get("session_id") or "").strip()

    issues: list[str] = []
    if not str(payload.get("completed_at") or "").strip():
        issues.append("missing_completed_at")
    if not str(payload.get("completed_by") or "").strip():
        issues.append("missing_completed_by")
    if payload.get("success") is not True:
        issues.append("success_not_true")
    if not action:
        issues.append("missing_action")
    elif action in {"dispatch", "review", "qa-review", "respawn"} and not session_id:
        issues.append("missing_session_id")

    return {
        "valid": not issues,
        "issues": issues,
        "action": action,
        "post_recovery": _is_post_recovery_schema(payload),
    }


def audit_queue(queue_dir: str | Path) -> dict[str, Any]:
    queue_root = Path(queue_dir)
    done_dir = queue_root / "done"
    failed_dir = queue_root / "failed"
    pending_dir = queue_root / "pending"
    active_dir = queue_root / "active"

    done_json = sorted(done_dir.glob("*.json"))
    done_meta = sorted(done_dir.glob("*.meta"))
    invalid_samples: list[dict[str, Any]] = []
    duplicate_groups: dict[str, list[str]] = {}

    invalid_total = 0
    invalid_post_recovery = 0

    seen_groups: dict[str, list[str]] = {}
    for path in done_json:
        payload = _load_json(path)
        identity = _done_identity(payload)
        if identity.strip("|"):
            seen_groups.setdefault(identity, []).append(path.name)

        classification = classify_done_item(payload)
        if not classification["valid"]:
            invalid_total += 1
            if classification["post_recovery"]:
                invalid_post_recovery += 1
            if len(invalid_samples) < 10:
                invalid_samples.append(
                    {
                        "file": path.name,
                        "task_id": payload.get("task_id"),
                        "issues": classification["issues"],
                    }
                )

    for identity, files in seen_groups.items():
        if len(files) > 1:
            duplicate_groups[identity] = files

    duplicate_meta = 0
    duplicate_meta_samples: list[str] = []
    for path in done_meta:
        payload = _load_json(path)
        if str(payload.get("dedupe") or "").strip().lower() == "true":
            duplicate_meta += 1
            if len(duplicate_meta_samples) < 10:
                duplicate_meta_samples.append(path.name)

    report = {
        "queue_dir": str(queue_root),
        "counts": {
            "pending": len(list(pending_dir.glob("*.json"))),
            "active": len(list(active_dir.glob("*.json"))),
            "done_json": len(done_json),
            "done_meta": len(done_meta),
            "failed": len(list(failed_dir.glob("*.json"))),
        },
        "invalid_done_total": invalid_total,
        "invalid_done_post_recovery": invalid_post_recovery,
        "invalid_done_samples": invalid_samples,
        "duplicate_meta_markers_total": duplicate_meta,
        "duplicate_meta_samples": duplicate_meta_samples,
        "duplicate_groups_total": len(duplicate_groups),
        "duplicate_groups_top": [
            {"identity": key, "files": files[:10], "count": len(files)}
            for key, files in sorted(
                duplicate_groups.items(),
                key=lambda item: (-len(item[1]), item[0]),
            )[:10]
        ],
    }
    return report


def apply_report_to_metrics(report: dict[str, Any], metrics_file: str | Path) -> dict[str, Any]:
    from mc_control import load_metrics, save_metrics, to_iso

    metrics = load_metrics(metrics_file)
    counters = metrics.setdefault("counters_today", {})
    counters["queue_items_invalid_completed"] = int(report["invalid_done_post_recovery"])
    metrics["queue_audit"] = {
        "last_run": to_iso(),
        "done_json": report["counts"]["done_json"],
        "done_meta": report["counts"]["done_meta"],
        "failed": report["counts"]["failed"],
        "pending": report["counts"]["pending"],
        "active": report["counts"]["active"],
        "invalid_done_total": report["invalid_done_total"],
        "invalid_done_post_recovery": report["invalid_done_post_recovery"],
        "duplicate_meta_markers_total": report["duplicate_meta_markers_total"],
        "duplicate_groups_total": report["duplicate_groups_total"],
        "invalid_done_samples": report["invalid_done_samples"],
        "duplicate_meta_samples": report["duplicate_meta_samples"],
        "duplicate_groups_top": report["duplicate_groups_top"],
    }
    save_metrics(metrics_file, metrics)
    return metrics


def write_report(report: dict[str, Any], report_file: str | Path) -> Path:
    path = Path(report_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-dir", default=str(DEFAULT_QUEUE_DIR))
    parser.add_argument("--metrics-file", default=str(DEFAULT_METRICS_FILE))
    parser.add_argument("--report-file", default=str(DEFAULT_REPORT_FILE))
    parser.add_argument("--write-metrics", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit_queue(args.queue_dir)
    if args.write_metrics:
        apply_report_to_metrics(report, args.metrics_file)
    if args.report_file:
        write_report(report, args.report_file)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(
            json.dumps(
                {
                    "invalid_done_total": report["invalid_done_total"],
                    "invalid_done_post_recovery": report["invalid_done_post_recovery"],
                    "duplicate_meta_markers_total": report["duplicate_meta_markers_total"],
                    "duplicate_groups_total": report["duplicate_groups_total"],
                    "report_file": args.report_file,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
