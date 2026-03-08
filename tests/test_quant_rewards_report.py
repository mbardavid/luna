from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    path = Path("/home/openclaw/.openclaw/workspace/scripts/quant-rewards-report.py")
    spec = importlib.util.spec_from_file_location("quant_rewards_report_runtime", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_should_send_on_material_change():
    module = _load_module()
    latest = {
        "decision_reason": "public_latency_gate_failed_for_live_rewards",
        "trading_state": "standby",
        "blockers": ["public_latency_gate"],
        "markets_with_positive_ev": 0,
        "enabled_rewards_markets": 0,
        "top_positive_ev_markets": [],
    }
    window = {
        "recommendation": "keep_standby_no_live_candidate",
        "cycles_with_positive_ev_market": 0,
        "cycles_with_live_eligible_market": 0,
    }
    state = {"last_signature": "", "last_sent_at": 0}

    send, reason = module.should_send(latest, window, state, force=False)
    assert send is True
    assert reason == "material_change"


def test_should_send_live_candidate_immediately():
    module = _load_module()
    latest = {
        "decision_reason": "rewards_markets_selected_for_live_execution",
        "trading_state": "active",
        "blockers": [],
        "markets_with_positive_ev": 2,
        "enabled_rewards_markets": 1,
        "top_positive_ev_markets": [{"market_id": "m1", "net_reward_ev_bps_day": 12.0}],
    }
    window = {
        "recommendation": "review_live_candidate_window",
        "cycles_with_positive_ev_market": 1,
        "cycles_with_live_eligible_market": 1,
    }
    state = {"last_signature": "same", "last_reason": "material_change", "last_sent_at": 1}

    send, reason = module.should_send(latest, window, state, force=False)
    assert send is True
    assert reason == "live_candidate_detected"


def test_render_message_includes_capital_split():
    module = _load_module()
    latest = {
        "run_id": "prod-006",
        "decision_id": "dec-1",
        "trading_state": "standby",
        "decision_reason": "no_edge",
        "markets_considered": 10,
        "markets_with_positive_ev": 0,
        "enabled_rewards_markets": 0,
        "blockers": [],
        "transport_live_gates": {
            "public_quote_direct_ok": True,
            "private_post_proxy_ok": True,
            "rewards_live_ok": True,
        },
        "top_positive_ev_markets": [],
    }
    window = {
        "decision_cycles": 5,
        "cycles_with_positive_ev_market": 0,
        "cycles_with_live_eligible_market": 0,
        "recommendation": "keep_standby_no_live_candidate",
    }
    stack_capital = {
        "pmm": {"total_usd": 57.7},
        "stack": {"total_usd": 229.0, "chain_totals": {"solana": 170.2, "polygon": 58.8}},
        "delta_vs_pmm_usd": 171.3,
    }
    message = module.render_message(latest, window, stack_capital, reason="forced")
    assert "capital_pmm=57.70 stack_total=229.00 delta=171.30" in message
    assert "stack_by_chain=solana=170.20 polygon=58.80" in message
