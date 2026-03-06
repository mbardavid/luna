from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from runner.__main__ import _load_decision_envelope
from runner.decision_envelope import DecisionEnvelope
from runner.transport import TransportProbeSample, choose_transport


def _valid_payload() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "decision_id": "quant-test-001",
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=2)).isoformat(),
        "transport_policy": "direct_preferred",
        "capital_policy": {
            "total_capital_usdc": "220",
            "rewards_capital_usdc": "176",
            "directional_capital_usdc": "22",
            "reserve_capital_usdc": "22",
        },
        "mode_allocations": {
            "rewards_enabled": True,
            "directional_enabled": True,
            "directional_max_pct": 10,
            "priority_order": ["rewards_farming", "event_driven"],
        },
        "markets": [
            {
                "mode": "rewards_farming",
                "market_id": "rewards-market",
                "condition_id": "0xrewards",
                "token_id_yes": "yes-1",
                "token_id_no": "no-1",
                "reward_min_size_usdc": "20",
                "reward_max_spread_cents": "3.5",
                "expected_reward_yield_bps_day": 35.5,
                "expected_fill_rate_pct": 14.0,
                "max_inventory_per_side": "160",
                "order_size": "20",
                "half_spread_bps": 40,
                "min_quote_lifetime_s": 45,
                "max_requote_rate_per_min": 2,
            },
            {
                "mode": "event_driven",
                "market_id": "directional-market",
                "condition_id": "0xdirectional",
                "token_id_yes": "yes-2",
                "token_id_no": "no-2",
                "side": "YES",
                "entry_price_limit": "0.55",
                "model_probability": 0.61,
                "market_implied_probability": 0.54,
                "edge_bps": 50,
                "confidence": 0.72,
                "stake_usdc": "22",
                "max_slippage_bps": 20,
                "ttl_seconds": 600,
                "stop_rule": "stop",
                "take_profit_rule": "tp",
                "source_evidence_ids": ["sig-1"],
            },
        ],
    }


def _sample(ttfb_ms: float, *, transport: str, ok: bool = True) -> TransportProbeSample:
    return TransportProbeSample(
        endpoint="clob_public",
        url="https://clob.polymarket.com/",
        transport=transport,
        dns_ms=1.0,
        connect_ms=5.0,
        ttfb_ms=ttfb_ms,
        ok=ok,
    )


def test_decision_envelope_rejects_expired_live_usage():
    payload = _valid_payload()
    envelope = DecisionEnvelope.from_dict(payload)
    envelope.require_live_ready()
    assert not envelope.is_expired(now=envelope.generated_at + timedelta(minutes=30))
    with pytest.raises(ValueError):
        envelope.require_live_ready(now=envelope.expires_at + timedelta(seconds=1))


def test_decision_envelope_enforces_directional_cap():
    payload = _valid_payload()
    payload["capital_policy"]["directional_capital_usdc"] = "40"
    with pytest.raises(ValueError):
        DecisionEnvelope.from_dict(payload)


def test_decision_envelope_rejects_non_active_live_usage():
    payload = _valid_payload()
    payload["trading_state"] = "standby"
    payload["decision_reason"] = "waiting_for_quant_review"
    payload["decision_scope"] = "rewards_only"
    envelope = DecisionEnvelope.from_dict(payload)
    with pytest.raises(ValueError):
        envelope.require_live_ready()


def test_transport_policy_prefers_direct_when_healthy():
    selection = choose_transport(
        "direct_preferred",
        [_sample(210.0, transport="direct")],
        [_sample(950.0, transport="proxy")],
        proxy_url="socks5://127.0.0.1:9050",
    )
    assert selection.selected_transport == "direct"
    assert selection.rewards_live_ok is True
    assert selection.directional_live_ok is True


def test_transport_policy_falls_back_to_proxy_and_blocks_directional():
    selection = choose_transport(
        "direct_preferred",
        [_sample(9999.0, transport="direct", ok=False)],
        [_sample(1100.0, transport="proxy")],
        proxy_url="socks5://127.0.0.1:9050",
    )
    assert selection.selected_transport == "proxy"
    assert selection.rewards_live_ok is True
    assert selection.directional_live_ok is False


def test_live_loader_requires_decision_envelope():
    args = SimpleNamespace(decision_envelope=None)
    with pytest.raises(SystemExit):
        _load_decision_envelope(args, None, required=True)
