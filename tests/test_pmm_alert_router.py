from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path


def _load_module():
    path = Path("/home/openclaw/.openclaw/workspace/scripts/pmm-alert-router.py")
    spec = importlib.util.spec_from_file_location("pmm_alert_router_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_incident_owner_aliases_resolve_to_expected_agents():
    module = _load_module()

    assert module.incident_owner("reward_adjusted_pnl_negative") == "quant-strategist"
    assert module.incident_owner("balance_allowance_mismatch") == "crypto-sage"
    assert module.incident_owner("restart_failed_3x") == "luna"


def test_build_runtime_incidents_flags_candidate_waiting_without_promotion():
    module = _load_module()
    snapshot = {
        "runtime": {"status": "halted", "run_id": "prod-004"},
        "live_state": {"run_id": "prod-004"},
        "latest": {
            "decision_id": "latest-1",
            "trading_state": "active",
            "expires_at": (module.utcnow() - timedelta(minutes=1)).isoformat(),
            "metadata": {},
        },
        "applied": {"decision_id": "latest-1"},
        "candidate": {
            "decision_id": "candidate-2",
            "trading_state": "active",
            "expires_at": (module.utcnow() + timedelta(hours=2)).isoformat(),
        },
        "diagnosis": {"analysis": {"post_trade_diagnosis": {}}},
        "cycle_state": {},
        "latest_age_seconds": 60.0,
        "applied_age_seconds": 60.0,
        "candidate_age_seconds": module.CANDIDATE_PROMOTION_THRESHOLD.total_seconds() + 1,
    }

    incidents = module.build_runtime_incidents(snapshot)

    codes = {item["code"] for item in incidents}
    assert "candidate_valid_without_promotion" in codes


def test_build_runtime_incidents_does_not_flag_recoverable_inventory_while_running():
    module = _load_module()
    snapshot = {
        "runtime": {"status": "running", "run_id": "prod-004"},
        "live_state": {"run_id": "prod-004"},
        "latest": {
            "decision_id": "latest-1",
            "trading_state": "active",
            "expires_at": (module.utcnow() + timedelta(hours=1)).isoformat(),
            "metadata": {"transport_live_gates": {"rewards_live_ok": True}},
            "risk_limits": {"allow_directional_live": False},
        },
        "applied": {"decision_id": "latest-1"},
        "candidate": {},
        "diagnosis": {
            "analysis": {
                "post_trade_diagnosis": {
                    "wallet_state": {"recoverable_inventory_usdc": "50.0"},
                    "reward_adjusted_pnl": {"reward_adjusted_pnl_usd": 1.0},
                    "execution_pnl": {"reject_rate_pct": 0.0},
                    "taint": {"reasons": []},
                }
            }
        },
        "cycle_state": {},
        "latest_age_seconds": 60.0,
        "applied_age_seconds": 60.0,
        "candidate_age_seconds": None,
    }

    incidents = module.build_runtime_incidents(snapshot)

    codes = {item["code"] for item in incidents}
    assert "recoverable_inventory_detected" not in codes


def test_build_runtime_incidents_ignores_latest_applied_drift_while_standby():
    module = _load_module()
    snapshot = {
        "runtime": {"status": "standby", "run_id": "prod-004"},
        "live_state": {"run_id": "prod-004"},
        "latest": {
            "decision_id": "latest-2",
            "trading_state": "standby",
            "expires_at": (module.utcnow() + timedelta(hours=1)).isoformat(),
            "metadata": {"transport_live_gates": {"rewards_live_ok": True}},
            "risk_limits": {"allow_directional_live": False},
        },
        "applied": {"decision_id": "older-1"},
        "candidate": {},
        "diagnosis": {"analysis": {"post_trade_diagnosis": {}}},
        "cycle_state": {},
        "latest_age_seconds": module.LATEST_APPLIED_DRIFT_THRESHOLD.total_seconds() + 1,
        "applied_age_seconds": 60.0,
        "candidate_age_seconds": None,
    }

    incidents = module.build_runtime_incidents(snapshot)

    codes = {item["code"] for item in incidents}
    assert "latest_applied_drift" not in codes


def test_normalize_router_state_migrates_resolved_incident_lists():
    module = _load_module()
    state = module.normalize_router_state(
        {
            "open_incidents": {"foo": {"code": "foo", "task_id": "task-open"}},
            "resolved_incidents": [
                {"code": "bar", "task_id": "task-resolved"},
                {"code": "baz", "opened_at": "2026-03-06T00:00:00+00:00"},
            ],
        }
    )

    assert isinstance(state["resolved_incidents"], dict)
    assert "task-resolved" in state["resolved_incidents"]
    assert any(key.startswith("baz:") for key in state["resolved_incidents"])
