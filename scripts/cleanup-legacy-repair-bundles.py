#!/usr/bin/env python3
"""Collapse legacy nested/duplicate repair bundles without losing lineage."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/openclaw/.openclaw/workspace")
MC_CLIENT = ROOT / "scripts" / "mc-client.sh"


def run(cmd: list[str], timeout: int = 45) -> str:
    cp = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if cp.returncode != 0:
        raise RuntimeError(cp.stderr.strip() or cp.stdout.strip() or "command failed")
    return cp.stdout.strip()


def mc_list_tasks() -> list[dict]:
    raw = run([str(MC_CLIENT), "list-tasks"])
    data = json.loads(raw or "{}")
    if isinstance(data, dict):
        return data.get("items", [])
    return data if isinstance(data, list) else []


def mc_update_task(
    task_id: str,
    *,
    status: str | None = None,
    comment: str | None = None,
    title: str | None = None,
    fields: dict | None = None,
) -> dict:
    cmd = [str(MC_CLIENT), "update-task", task_id]
    if status:
        cmd += ["--status", status]
    if comment:
        cmd += ["--comment", comment]
    if title is not None:
        cmd += ["--title", title]
    if fields is not None:
        cmd += ["--fields", json.dumps(fields, ensure_ascii=False)]
    raw = run(cmd, timeout=60)
    return json.loads(raw or "{}") if raw else {}


def task_fields(task: dict) -> dict:
    return task.get("custom_field_values") or {}


def is_nested_bundle(task: dict) -> bool:
    if task_fields(task).get("mc_card_type") != "repair_bundle":
        return False
    title = str(task.get("title") or "")
    return ("Diagnose — Diagnose" in title) or (title.count("Diagnose —") >= 2) or ("Repair bundle: Diagnose" in title)


def normalize_repair_title(title: str) -> str:
    value = str(title or "").strip()
    prefixes = ("Diagnose — ", "Repair — ", "Validate repair — ")
    changed = True
    while changed and value:
        changed = False
        for prefix in prefixes:
            if value.startswith(prefix):
                value = value[len(prefix):].strip()
                changed = True
    return value or str(title or "").strip()


def root_source_task(by_id: dict[str, dict], source_task_id: str) -> dict:
    current = by_id.get(source_task_id) or {}
    seen: set[str] = set()
    while current:
        current_id = str(current.get("id") or "")
        if not current_id or current_id in seen:
            break
        seen.add(current_id)
        fields = task_fields(current)
        repair_bundle_id = str(fields.get("mc_repair_bundle_id") or "").strip()
        if not repair_bundle_id:
            break
        bundle = by_id.get(repair_bundle_id) or {}
        next_task_id = str(task_fields(bundle).get("mc_repair_source_task_id") or "").strip()
        if not next_task_id:
            break
        nxt = by_id.get(next_task_id)
        if not nxt:
            break
        current = nxt
    return current


def child_tasks(tasks: list[dict], bundle_id: str) -> list[dict]:
    return [
        task
        for task in tasks
        if str(task_fields(task).get("mc_parent_task_id") or "").strip() == bundle_id
    ]


def has_active_child(tasks: list[dict], bundle_id: str) -> bool:
    for task in child_tasks(tasks, bundle_id):
        if str(task.get("status") or "") in {"in_progress", "review"}:
            return True
    return False


def normalized_bundle_title(by_id: dict[str, dict], bundle: dict) -> str:
    source_id = str(task_fields(bundle).get("mc_repair_source_task_id") or "").strip()
    source = root_source_task(by_id, source_id)
    source_title = normalize_repair_title(str(source.get("title") or source_id[:8] or "unknown task").strip())
    return f"Repair bundle: {source_title}"


def supersede_fields(task: dict, superseded_by: str) -> dict:
    fields = dict(task_fields(task))
    fields["mc_dispatch_policy"] = "human_hold"
    fields["mc_gate_reason"] = "repair_bundle_superseded"
    if fields.get("mc_card_type") == "repair_bundle":
        fields["mc_repair_state"] = "failed"
    elif fields.get("mc_card_type") in {"leaf_task", "review_bundle"}:
        fields["mc_repair_state"] = "failed"
    fields["mc_last_error"] = f"repair_bundle_superseded:{superseded_by[:8]}"
    fields["mc_session_key"] = ""
    return fields


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    tasks = mc_list_tasks()
    by_id = {str(task.get("id") or ""): task for task in tasks}

    nested_bundles = [task for task in tasks if is_nested_bundle(task)]
    by_fp: dict[str, list[dict]] = defaultdict(list)
    for bundle in nested_bundles:
        by_fp[str(task_fields(bundle).get("mc_repair_fingerprint") or "")].append(bundle)

    renamed: list[dict[str, str]] = []
    superseded: list[dict[str, object]] = []
    skipped: list[dict[str, str]] = []

    for fp, bundles in by_fp.items():
        if not fp:
            skipped.extend({"bundle_id": str(b.get("id") or ""), "reason": "missing_fingerprint"} for b in bundles)
            continue
        source_id = str(task_fields(bundles[0]).get("mc_repair_source_task_id") or "").strip()
        source_task = by_id.get(source_id) or {}
        source_bundle_id = str(task_fields(source_task).get("mc_repair_bundle_id") or "").strip()

        canonical = None
        if source_bundle_id:
            canonical = next((b for b in bundles if str(b.get("id") or "") == source_bundle_id), None)
        if canonical is None:
            canonical = sorted(bundles, key=lambda b: (str(b.get("updated_at") or ""), str(b.get("created_at") or "")), reverse=True)[0]

        canonical_id = str(canonical.get("id") or "")
        new_title = normalized_bundle_title(by_id, canonical)
        if str(canonical.get("title") or "") != new_title:
            renamed.append({"bundle_id": canonical_id, "old_title": str(canonical.get("title") or ""), "new_title": new_title})
            if args.apply:
                mc_update_task(
                    canonical_id,
                    title=new_title,
                    comment=f"[cleanup] normalized legacy repair bundle title to root source task.",
                )

        duplicates = [b for b in bundles if str(b.get("id") or "") != canonical_id]
        for duplicate in duplicates:
            dup_id = str(duplicate.get("id") or "")
            if has_active_child(tasks, dup_id):
                skipped.append({"bundle_id": dup_id, "reason": "active_children"})
                continue
            dup_children = child_tasks(tasks, dup_id)
            superseded.append(
                {
                    "bundle_id": dup_id,
                    "canonical_bundle_id": canonical_id,
                    "children": [str(child.get("id") or "") for child in dup_children],
                }
            )
            if not args.apply:
                continue
            for child in dup_children:
                child_id = str(child.get("id") or "")
                child_fields = supersede_fields(child, canonical_id)
                mc_update_task(
                    child_id,
                    status="done",
                    comment=f"[cleanup] superseded by repair bundle `{canonical_id[:8]}` during legacy dedupe.",
                    fields=child_fields,
                )
            bundle_fields = supersede_fields(duplicate, canonical_id)
            mc_update_task(
                dup_id,
                status="done",
                comment=f"[cleanup] superseded by repair bundle `{canonical_id[:8]}` during legacy dedupe.",
                fields=bundle_fields,
            )

    payload = {
        "renamed": renamed,
        "renamed_count": len(renamed),
        "superseded": superseded,
        "superseded_count": len(superseded),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "apply": args.apply,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"renamed={len(renamed)} superseded={len(superseded)} skipped={len(skipped)} apply={args.apply}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
