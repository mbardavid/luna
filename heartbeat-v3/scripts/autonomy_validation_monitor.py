#!/usr/bin/env python3
"""Run lightweight autonomy architecture validation and notify on regression/recovery."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(ROOT))

from validate_autonomy_architecture import (
    DEFAULT_JSON_OUTPUT,
    DEFAULT_MD_OUTPUT,
    AUTONOMY_RUNTIME_FILE,
    METRICS_FILE,
    SCHEDULER_STATE_FILE,
    evaluate_autonomy_architecture,
    load_tasks,
    render_markdown,
    resolve_artifact_paths,
    run_pytest_validation,
)
from project_autonomy import select_active_project

OPENCLAW_BIN = os.environ.get("OPENCLAW_BIN", "openclaw")
OPENCLAW_CONFIG = os.environ.get("OPENCLAW_CONFIG", "/home/openclaw/.openclaw/openclaw.json")
GATEWAY_URL = os.environ.get("MC_GATEWAY_URL", "ws://127.0.0.1:18789")
STATE_FILE = WORKSPACE / "state" / "autonomy-architecture-monitor-state.json"
LOG_FILE = WORKSPACE / "logs" / "autonomy-validation-monitor.log"
LOCK_FILE = "/tmp/.autonomy-validation-monitor.lock"
V3_CONFIG_FILE = ROOT.parent / "config" / "v3-config.json"
NON_CRITICAL_DEFAULT = {"project_lane_coexists_with_ambient", "pytest_replays"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime | None = None) -> str:
    current = dt or utcnow()
    return current.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def log(message: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{to_iso()}] {message}"
    with open(LOG_FILE, "a", encoding="utf-8") as fp:
        fp.write(line + "\n")


def load_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name, dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def load_gateway_token() -> str:
    env_token = os.environ.get("MC_GATEWAY_TOKEN", "").strip()
    if env_token:
        return env_token
    data = load_json(Path(OPENCLAW_CONFIG))
    gateway = data.get("gateway") if isinstance(data, dict) else {}
    auth = gateway.get("auth") if isinstance(gateway, dict) else {}
    token = str(auth.get("token") or "").strip()
    if not token:
        raise RuntimeError("gateway token not found")
    return token


_gateway_token: str | None = None


def gateway_call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _gateway_token
    if _gateway_token is None:
        _gateway_token = load_gateway_token()
    cmd = [
        OPENCLAW_BIN,
        "gateway",
        "call",
        "--url",
        GATEWAY_URL,
        "--token",
        _gateway_token,
        "--json",
        "--params",
        json.dumps(params or {}, ensure_ascii=False),
        method,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gateway call failed: {' '.join(cmd[:4])}")
    return json.loads(proc.stdout or "{}")


def notification_channels(config: dict[str, Any]) -> list[str]:
    primary = str(config.get("discord_channel") or "1473367119377731800").strip()
    notifications = str(config.get("notifications_channel") or "").strip()
    mirror = bool(config.get("mirror_notifications", False))
    channels = [primary] if primary else []
    if mirror and notifications and notifications not in channels:
        channels.append(notifications)
    return channels


def send_discord(channel: str, message: str) -> bool:
    try:
        cmd = [
            OPENCLAW_BIN,
            "message",
            "send",
            "--channel",
            "discord",
            "--target",
            channel,
            "--message",
            message,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "message send failed")
        return True
    except Exception as exc:
        log(f"ERROR: Discord notification failed for {channel}: {exc}")
        return False


def load_config() -> dict[str, Any]:
    cfg = load_json(V3_CONFIG_FILE)
    section = cfg.get("autonomy_validation") if isinstance(cfg, dict) else {}
    if not isinstance(section, dict):
        section = {}
    return {
        "discord_channel": cfg.get("discord_channel", "1473367119377731800"),
        "notifications_channel": cfg.get("notifications_channel", ""),
        "mirror_notifications": cfg.get("mirror_notifications", False),
        "enabled": bool(section.get("enabled", True)),
        "cooldown_minutes": int(section.get("cooldown_minutes", 60) or 60),
        "run_with_pytest": bool(section.get("run_with_pytest", False)),
        "non_critical_checks": list(section.get("non_critical_checks") or sorted(NON_CRITICAL_DEFAULT)),
    }


def select_regressions(report: dict[str, Any], *, non_critical_checks: set[str]) -> dict[str, dict[str, str]]:
    regressions: dict[str, dict[str, str]] = {}
    for check in report.get("checks") or []:
        check_id = str(check.get("id") or "")
        status = str(check.get("status") or "FAIL")
        if not check_id or check_id in non_critical_checks:
            continue
        if status == "PASS":
            continue
        regressions[check_id] = {
            "status": status,
            "summary": str(check.get("summary") or "").strip(),
        }
    return regressions


def regression_fingerprint(regressions: dict[str, dict[str, str]]) -> str:
    normalized = {key: regressions[key]["status"] for key in sorted(regressions)}
    return hashlib.sha1(json.dumps(normalized, sort_keys=True).encode("utf-8")).hexdigest()


def build_notification_event(
    report: dict[str, Any],
    previous_state: dict[str, Any],
    *,
    cooldown_minutes: int,
    non_critical_checks: set[str],
    force_notify: bool = False,
) -> dict[str, Any]:
    regressions = select_regressions(report, non_critical_checks=non_critical_checks)
    fingerprint = regression_fingerprint(regressions)
    current_ts = parse_iso(str(report.get("generated_at") or "")) or utcnow()
    previous_fingerprint = str(previous_state.get("last_regression_fingerprint") or "")
    previous_regressions = previous_state.get("last_regressions") or {}
    last_alert_at = parse_iso(str(previous_state.get("last_alert_at") or ""))
    cooldown_seconds = max(0, int(cooldown_minutes or 0)) * 60
    alert_due = force_notify
    if regressions:
        if fingerprint != previous_fingerprint or not previous_regressions:
            alert_due = True
        elif last_alert_at is None:
            alert_due = True
        else:
            elapsed = (current_ts - last_alert_at).total_seconds()
            alert_due = elapsed >= cooldown_seconds
        if alert_due:
            return {
                "kind": "alert",
                "regressions": regressions,
                "fingerprint": fingerprint,
            }
        return {"kind": "none", "regressions": regressions, "fingerprint": fingerprint}

    if previous_regressions:
        return {
            "kind": "recovery",
            "regressions": {},
            "fingerprint": fingerprint,
            "recovered": previous_regressions,
        }
    return {"kind": "none", "regressions": regressions, "fingerprint": fingerprint}


def format_alert_message(report: dict[str, Any], event: dict[str, Any]) -> str:
    project = report.get("active_project") or {}
    summary = report.get("summary") or {}
    regressions = event.get("regressions") or {}
    lines = [
        "🚨 **Autonomy architecture regression detected**",
        f"Project: `{str(project.get('id') or '')[:8]}` — **{project.get('title') or '(none)'}**",
        f"Milestone: `{str(project.get('milestone_id') or '')[:8]}` — **{project.get('milestone_title') or '(none)'}**",
        f"Overall: `{report.get('overall_status', 'FAIL')}` | failed={summary.get('failed', 0)} warn={summary.get('warnings', 0)}",
    ]
    for check_id, payload in list(sorted(regressions.items()))[:6]:
        lines.append(f"- `{check_id}` [{payload.get('status')}] {payload.get('summary')}")
    lines.append(f"Report: `{DEFAULT_MD_OUTPUT}`")
    return "\n".join(lines)


def format_recovery_message(report: dict[str, Any], event: dict[str, Any]) -> str:
    project = report.get("active_project") or {}
    recovered = event.get("recovered") or {}
    lines = [
        "✅ **Autonomy architecture recovered**",
        f"Project: `{str(project.get('id') or '')[:8]}` — **{project.get('title') or '(none)'}**",
        f"Recovered checks: {', '.join(sorted(recovered.keys())[:6]) or 'all clear'}",
        f"Overall: `{report.get('overall_status', 'PASS')}`",
        f"Report: `{DEFAULT_MD_OUTPUT}`",
    ]
    return "\n".join(lines)


def save_monitor_state(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_json(path, payload)


def run_validation(*, with_pytest: bool = False) -> dict[str, Any]:
    tasks = load_tasks()
    scheduler_state = load_json(SCHEDULER_STATE_FILE)
    metrics = load_json(METRICS_FILE)
    autonomy_runtime = load_json(AUTONOMY_RUNTIME_FILE)
    project = select_active_project(tasks)
    artifact_paths = resolve_artifact_paths(project)
    pytest_result = run_pytest_validation() if with_pytest else None
    report = evaluate_autonomy_architecture(
        tasks,
        scheduler_state=scheduler_state,
        metrics=metrics,
        autonomy_runtime=autonomy_runtime,
        artifact_paths=artifact_paths,
        pytest_result=pytest_result,
    )
    DEFAULT_JSON_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_MD_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_JSON_OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    DEFAULT_MD_OUTPUT.write_text(render_markdown(report), encoding="utf-8")
    return report


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="manual")
    parser.add_argument("--force-notify", action="store_true")
    parser.add_argument("--with-pytest", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config = load_config()
    if not config.get("enabled", True):
        print(json.dumps({"status": "disabled"}))
        return 0

    Path(LOCK_FILE).parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log("SKIP: autonomy validation monitor already running")
        return 0

    try:
        report = run_validation(with_pytest=bool(args.with_pytest or config.get("run_with_pytest", False)))
        previous_state = load_json(STATE_FILE)
        non_critical = set(str(item) for item in (config.get("non_critical_checks") or []))
        event = build_notification_event(
            report,
            previous_state,
            cooldown_minutes=int(config.get("cooldown_minutes", 60) or 60),
            non_critical_checks=non_critical,
            force_notify=bool(args.force_notify),
        )
        sent_channels: list[str] = []
        if event["kind"] == "alert":
            message = format_alert_message(report, event)
            for channel in notification_channels(config):
                if send_discord(channel, message):
                    sent_channels.append(channel)
            log(f"ALERT: autonomy architecture regression notified via {','.join(sent_channels) or 'none'}")
        elif event["kind"] == "recovery":
            message = format_recovery_message(report, event)
            for channel in notification_channels(config):
                if send_discord(channel, message):
                    sent_channels.append(channel)
            log(f"RECOVERY: autonomy architecture recovered via {','.join(sent_channels) or 'none'}")
        else:
            log("OK: no autonomy architecture notification needed")

        state_payload = {
            "last_run_at": str(report.get("generated_at") or to_iso()),
            "last_source": args.source,
            "last_overall_status": str(report.get("overall_status") or ""),
            "last_regressions": event.get("regressions") or select_regressions(report, non_critical_checks=non_critical),
            "last_regression_fingerprint": event.get("fingerprint") or regression_fingerprint(select_regressions(report, non_critical_checks=non_critical)),
            "last_notification_kind": event.get("kind"),
            "last_alert_at": previous_state.get("last_alert_at"),
            "last_recovery_at": previous_state.get("last_recovery_at"),
            "last_notified_channels": sent_channels,
        }
        if event["kind"] == "alert":
            state_payload["last_alert_at"] = str(report.get("generated_at") or to_iso())
        if event["kind"] == "recovery":
            state_payload["last_recovery_at"] = str(report.get("generated_at") or to_iso())
        save_monitor_state(STATE_FILE, state_payload)
        print(json.dumps({"overall_status": report["overall_status"], "notification": event["kind"], "channels": sent_channels}, ensure_ascii=False))
        return 0
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        lock_fd.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
