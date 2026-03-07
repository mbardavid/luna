from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent.parent
sys.path.insert(0, str(WORKSPACE / "heartbeat-v3" / "scripts"))

from autonomy_validation_monitor import build_notification_event, regression_fingerprint, select_regressions


def _report(*checks):
    return {
        "generated_at": "2026-03-07T00:00:00Z",
        "overall_status": "PASS",
        "summary": {"failed": 0, "warnings": 0, "passed": len(checks)},
        "checks": list(checks),
    }


def _check(check_id: str, status: str, summary: str = "summary"):
    return {"id": check_id, "status": status, "summary": summary}


def test_select_regressions_ignores_non_critical_checks():
    report = _report(
        _check("governance_not_in_review", "PASS"),
        _check("project_lane_coexists_with_ambient", "WARN"),
        _check("repair_lane_served", "FAIL", "repair starved"),
    )
    regressions = select_regressions(report, non_critical_checks={"project_lane_coexists_with_ambient"})
    assert regressions == {
        "repair_lane_served": {"status": "FAIL", "summary": "repair starved"}
    }


def test_notification_event_alerts_on_new_regression():
    report = _report(_check("repair_lane_served", "FAIL", "repair starved"))
    previous = {"last_regressions": {}, "last_regression_fingerprint": ""}
    event = build_notification_event(
        report,
        previous,
        cooldown_minutes=60,
        non_critical_checks={"project_lane_coexists_with_ambient"},
    )
    assert event["kind"] == "alert"
    assert "repair_lane_served" in event["regressions"]


def test_notification_event_suppresses_same_regression_inside_cooldown():
    report = _report(_check("repair_lane_served", "FAIL", "repair starved"))
    regressions = {"repair_lane_served": {"status": "FAIL", "summary": "repair starved"}}
    previous = {
        "last_regressions": regressions,
        "last_regression_fingerprint": regression_fingerprint(regressions),
        "last_alert_at": "2026-03-07T00:00:00Z",
    }
    event = build_notification_event(
        report,
        previous,
        cooldown_minutes=60,
        non_critical_checks={"project_lane_coexists_with_ambient"},
    )
    assert event["kind"] == "none"


def test_notification_event_emits_recovery_when_regressions_clear():
    report = _report(_check("governance_not_in_review", "PASS"))
    previous = {
        "last_regressions": {"repair_lane_served": {"status": "FAIL", "summary": "repair starved"}},
        "last_regression_fingerprint": "abc",
        "last_alert_at": "2026-03-07T00:00:00Z",
    }
    event = build_notification_event(
        report,
        previous,
        cooldown_minutes=60,
        non_critical_checks={"project_lane_coexists_with_ambient"},
    )
    assert event["kind"] == "recovery"
    assert "repair_lane_served" in event["recovered"]
