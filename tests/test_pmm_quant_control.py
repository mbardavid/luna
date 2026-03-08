from __future__ import annotations

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace


def _load_module():
    path = Path("/home/openclaw/.openclaw/workspace/scripts/pmm_quant_control.py")
    spec = importlib.util.spec_from_file_location("pmm_quant_control_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_auto_promote_when_current_envelope_is_expiring():
    module = _load_module()
    current_envelope = SimpleNamespace(
        decision_id="current-1",
        expires_at=module.utcnow() + timedelta(minutes=5),
    )
    candidate_envelope = SimpleNamespace(decision_id="candidate-1")

    auto_promote, reason = module.should_auto_promote_candidate(
        current_envelope=current_envelope,
        candidate_envelope=candidate_envelope,
        report={},
        runtime_state={"status": "running"},
    )

    assert auto_promote is True
    assert reason == "current_envelope_expiring"


def test_should_auto_promote_when_runtime_is_halted():
    module = _load_module()
    current_envelope = SimpleNamespace(
        decision_id="current-1",
        expires_at=module.utcnow() + timedelta(hours=2),
    )
    candidate_envelope = SimpleNamespace(decision_id="candidate-2")

    auto_promote, reason = module.should_auto_promote_candidate(
        current_envelope=current_envelope,
        candidate_envelope=candidate_envelope,
        report={},
        runtime_state={"status": "halted"},
    )

    assert auto_promote is True
    assert reason == "runtime_halted"


def test_should_auto_promote_when_candidate_restores_active_trading():
    module = _load_module()
    current_envelope = SimpleNamespace(
        decision_id="current-standby",
        expires_at=module.utcnow() + timedelta(hours=2),
        trading_state="standby",
    )
    candidate_envelope = SimpleNamespace(
        decision_id="candidate-active",
        trading_state="active",
    )

    auto_promote, reason = module.should_auto_promote_candidate(
        current_envelope=current_envelope,
        candidate_envelope=candidate_envelope,
        report={},
        runtime_state={"status": "standby"},
    )

    assert auto_promote is True
    assert reason == "candidate_restores_active_trading"


def test_should_auto_promote_when_candidate_requests_risk_off_state():
    module = _load_module()
    current_envelope = SimpleNamespace(
        decision_id="current-active",
        expires_at=module.utcnow() + timedelta(hours=2),
        trading_state="active",
    )
    candidate_envelope = SimpleNamespace(
        decision_id="candidate-standby",
        trading_state="standby",
    )

    auto_promote, reason = module.should_auto_promote_candidate(
        current_envelope=current_envelope,
        candidate_envelope=candidate_envelope,
        report={},
        runtime_state={"status": "running"},
    )

    assert auto_promote is True
    assert reason == "candidate_requests_risk_off_state"


def test_normalize_wake_target_supports_crypto_sage_and_luna():
    module = _load_module()

    assert module.normalize_wake_target("quant") == "quant-strategist"
    assert module.normalize_wake_target("crypto_sage") == "crypto-sage"
    assert module.normalize_wake_target("blockchain-operator") == "crypto-sage"
    assert module.normalize_wake_target("luna") == "main"


def test_runtime_payload_uses_latest_envelope_when_standby_is_applied():
    module = _load_module()
    latest = SimpleNamespace(
        decision_id="latest-standby",
        trading_state="standby",
        metadata={"selected_transport": "direct"},
    )
    payload = module.runtime_payload(
        status="standby",
        config_path=Path("/tmp/prod.yaml"),
        desired_envelope=latest,
        applied_envelope=latest,
        action="enforce_non_active_trading_state",
        action_result={"stopped": True},
    )

    assert payload["desired_decision_id"] == "latest-standby"
    assert payload["applied_decision_id"] == "latest-standby"
    assert payload["trading_state"] == "standby"
