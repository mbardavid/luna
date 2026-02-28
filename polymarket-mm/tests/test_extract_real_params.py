"""Tests for extract_real_params module.

Tests parameter extraction with sample JSONL data.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from paper.extract_real_params import extract_params, load_trades, _to_float


# ── Sample Trade Data ────────────────────────────────────────────────

SAMPLE_TRADES = [
    {
        "timestamp": "2026-02-28T10:00:00+00:00",
        "run_id": "prod-001",
        "trade_id": "prod-001-000001",
        "is_production": True,
        "market_id": "test-market-001",
        "side": "BUY",
        "token": "YES",
        "price": "0.50",
        "size": "5",
        "fill_qty": "5",
        "fill_price": "0.50",
        "pnl_this_trade": "0",
        "pnl_cumulative": "0",
        "pnl_realized": "0",
        "pnl_unrealized": "0",
        "latency_ms": 200.5,
        "gas_cost_usd": 0.003,
        "rejection_reason": "",
        "real_fee_bps": 30.0,
        "market_context": {
            "mid_price": "0.505",
            "best_bid": "0.49",
            "best_ask": "0.52",
            "spread_bps": 59,
        },
    },
    {
        "timestamp": "2026-02-28T10:05:00+00:00",
        "run_id": "prod-001",
        "trade_id": "prod-001-000002",
        "is_production": True,
        "market_id": "test-market-001",
        "side": "SELL",
        "token": "YES",
        "price": "0.55",
        "size": "5",
        "fill_qty": "5",
        "fill_price": "0.55",
        "pnl_this_trade": "0.2425",
        "pnl_cumulative": "0.2425",
        "pnl_realized": "0.2425",
        "pnl_unrealized": "0",
        "latency_ms": 180.3,
        "gas_cost_usd": 0.004,
        "rejection_reason": "",
        "real_fee_bps": 30.0,
        "market_context": {
            "mid_price": "0.545",
            "best_bid": "0.53",
            "best_ask": "0.56",
            "spread_bps": 55,
        },
    },
    {
        "timestamp": "2026-02-28T10:10:00+00:00",
        "run_id": "prod-001",
        "trade_id": "prod-001-000003",
        "is_production": True,
        "market_id": "test-market-001",
        "side": "BUY",
        "token": "YES",
        "price": "0.48",
        "size": "5",
        "fill_qty": "5",
        "fill_price": "0.48",
        "pnl_this_trade": "0",
        "pnl_cumulative": "0.2425",
        "pnl_realized": "0.2425",
        "pnl_unrealized": "0",
        "latency_ms": 320.1,
        "gas_cost_usd": 0.005,
        "rejection_reason": "",
        "real_fee_bps": 30.0,
        "market_context": {
            "mid_price": "0.490",
            "best_bid": "0.47",
            "best_ask": "0.51",
            "spread_bps": 82,
        },
    },
    # Rejection
    {
        "timestamp": "2026-02-28T10:15:00+00:00",
        "run_id": "prod-001",
        "trade_id": "prod-001-000004",
        "is_production": True,
        "market_id": "test-market-001",
        "side": "BUY",
        "token": "YES",
        "price": "0.50",
        "size": "5",
        "fill_qty": "0",
        "fill_price": "0.50",
        "pnl_this_trade": "0",
        "rejection_reason": "INSUFFICIENT_FUNDS",
        "latency_ms": 50.0,
        "gas_cost_usd": 0.0,
        "real_fee_bps": 0.0,
        "market_context": {
            "mid_price": "0.500",
            "best_bid": "0.48",
            "best_ask": "0.52",
            "spread_bps": 80,
        },
    },
]


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def sample_jsonl_file(tmp_path):
    """Create a temporary JSONL file with sample trades."""
    path = tmp_path / "trades_production.jsonl"
    with open(path, "w") as f:
        for trade in SAMPLE_TRADES:
            f.write(json.dumps(trade) + "\n")
    return path


@pytest.fixture
def empty_jsonl_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.touch()
    return path


# ── Tests ────────────────────────────────────────────────────────────

class TestLoadTrades:
    def test_load_existing_file(self, sample_jsonl_file):
        trades = load_trades(sample_jsonl_file)
        assert len(trades) == 4

    def test_load_empty_file(self, empty_jsonl_file):
        trades = load_trades(empty_jsonl_file)
        assert trades == []

    def test_load_nonexistent_file(self, tmp_path):
        trades = load_trades(tmp_path / "nonexistent.jsonl")
        assert trades == []

    def test_load_skips_malformed_lines(self, tmp_path):
        path = tmp_path / "bad.jsonl"
        with open(path, "w") as f:
            f.write('{"valid": true}\n')
            f.write('not json\n')
            f.write('{"also_valid": true}\n')
        trades = load_trades(path)
        assert len(trades) == 2


class TestExtractParams:
    def test_empty_trades(self):
        params = extract_params([])
        assert params["sample_size"] == 0
        assert "error" in params

    def test_basic_extraction(self):
        params = extract_params(SAMPLE_TRADES)
        assert params["sample_size"] == 4
        assert params["fills_count"] == 3  # 3 fills, 1 rejection
        assert params["rejections_count"] == 1

    def test_fill_rate(self):
        params = extract_params(SAMPLE_TRADES)
        # 3 fills out of 4 total = 0.75
        assert params["real_fill_rate"] == 0.75

    def test_rejection_rate(self):
        params = extract_params(SAMPLE_TRADES)
        # 1 rejection out of 4 total = 0.25
        assert params["real_rejection_rate"] == 0.25

    def test_fee_bps(self):
        params = extract_params(SAMPLE_TRADES)
        # All fills have real_fee_bps: 30.0
        assert params["real_fee_bps"] == 30.0

    def test_latency_ms(self):
        params = extract_params(SAMPLE_TRADES)
        # Average of 200.5, 180.3, 320.1 = 233.63
        expected = (200.5 + 180.3 + 320.1) / 3
        assert abs(params["real_latency_ms"] - expected) < 0.1

    def test_gas_cost(self):
        params = extract_params(SAMPLE_TRADES)
        # Average of 0.003, 0.004, 0.005 = 0.004 (excludes rejection with 0)
        expected = (0.003 + 0.004 + 0.005) / 3
        assert abs(params["real_gas_cost_per_tx_usd"] - expected) < 0.001

    def test_spread_bps(self):
        params = extract_params(SAMPLE_TRADES)
        # Fills have spread from best_bid/best_ask:
        # Trade 1: (0.52 - 0.49) / 0.505 * 10000 = 594 bps
        # Trade 2: (0.56 - 0.53) / 0.545 * 10000 = 550 bps
        # Trade 3: (0.51 - 0.47) / 0.490 * 10000 = 816 bps
        # Average ≈ 653 bps
        assert params["real_spread_bps"] > 500
        assert params["real_spread_bps"] < 900

    def test_max_price_jump(self):
        params = extract_params(SAMPLE_TRADES)
        # Jumps between sequential trades' mid prices
        # 0.505 → 0.545 → 0.490 → 0.500
        # max jump = |0.545 - 0.490| / 0.545 * 100 ≈ 10.09%
        # or |0.490 - 0.545| / 0.545 * 100 ≈ 10.09%
        assert params["real_max_price_jump_pct"] > 5

    def test_adverse_selection(self):
        params = extract_params(SAMPLE_TRADES)
        # Should compute some adverse selection from sequential fills
        assert isinstance(params["real_adverse_selection_bps"], float)

    def test_suggested_paper_config(self):
        params = extract_params(SAMPLE_TRADES)
        suggested = params["suggested_paper_config"]
        assert "fill_probability" in suggested
        assert "adverse_selection_bps" in suggested
        assert "maker_fee_bps" in suggested
        assert "fill_distance_decay" in suggested
        assert suggested["fill_probability"] > 0
        assert suggested["fill_distance_decay"] is True

    def test_extracted_at_timestamp(self):
        params = extract_params(SAMPLE_TRADES)
        assert "extracted_at" in params
        assert "2026" in params["extracted_at"] or "T" in params["extracted_at"]


class TestToFloat:
    def test_none(self):
        assert _to_float(None) == 0.0

    def test_string_number(self):
        assert _to_float("0.50") == 0.5

    def test_integer(self):
        assert _to_float(42) == 42.0

    def test_invalid_string(self):
        assert _to_float("not_a_number") == 0.0

    def test_decimal_string(self):
        from decimal import Decimal
        assert _to_float(Decimal("0.123")) == 0.123


class TestIntegration:
    def test_load_and_extract(self, sample_jsonl_file):
        """Full integration: load from file, extract params."""
        trades = load_trades(sample_jsonl_file)
        params = extract_params(trades)

        assert params["sample_size"] == 4
        assert params["real_fill_rate"] == 0.75
        assert params["real_fee_bps"] == 30.0
        assert params["real_latency_ms"] > 0
        assert "suggested_paper_config" in params

    def test_output_json_serializable(self, sample_jsonl_file):
        """Extracted params should be JSON-serializable."""
        trades = load_trades(sample_jsonl_file)
        params = extract_params(trades)

        # Should not raise
        json_str = json.dumps(params, default=str)
        reloaded = json.loads(json_str)
        assert reloaded["sample_size"] == 4
