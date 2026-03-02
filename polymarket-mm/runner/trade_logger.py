"""runner.trade_logger — Unified trade logger (mode-aware).

Produces JSONL logs with extra fields when running in live mode
(latency, gas cost, exchange order ID, etc.).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger("runner.trade_logger")

_DATA_DIR = Path(__file__).resolve().parent.parent / "paper" / "data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


class UnifiedTradeLogger:
    """Appends one JSONL line per fill.

    In paper mode, writes to ``trades.jsonl``.
    In live mode, writes to ``trades_production.jsonl`` with extra fields.
    """

    def __init__(
        self,
        mode: str = "paper",
        path: Path | None = None,
        run_id: str = "unknown",
    ) -> None:
        self._mode = mode
        if path is None:
            filename = "trades_production.jsonl" if mode == "live" else "trades.jsonl"
            path = _DATA_DIR / filename
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._trade_counter = 0
        self._cumulative_pnl = Decimal("0")

    def log_trade(
        self,
        *,
        market_id: str,
        market_description: str,
        side: str,
        token: str,
        price: Decimal,
        size: Decimal,
        fill_qty: Decimal,
        fill_price: Decimal,
        pnl_this_trade: Decimal,
        pnl_realized: Decimal,
        pnl_unrealized: Decimal,
        position: Any,
        market_state: Any,
        features: Any,
        spread_model_info: dict | None = None,
        inventory_skew_info: dict | None = None,
        toxic_flow_info: dict | None = None,
        rewards_info: dict | None = None,
        strategy: str = "spread_capture",
        trigger_text: str = "",
        quote_to_fill_ms: float = 0,
        quote_age_ms: float = 0,
        kill_switch_state: str = "RUNNING",
        data_gap_seconds: float = 0,
        wallet_after: dict | None = None,
        # Live-mode extra fields
        latency_ms: float = 0,
        gas_cost_usd: float = 0,
        rejection_reason: str = "",
        real_fee_bps: float = 0,
        exchange_order_id: str = "",
    ) -> None:
        self._trade_counter += 1
        self._cumulative_pnl += pnl_this_trade
        trade_id = f"{self._run_id}-{self._trade_counter:06d}"

        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "trade_id": trade_id,
            "market_id": market_id,
            "market_description": market_description,
            "side": side,
            "token": token,
            "price": str(price),
            "size": str(size),
            "fill_qty": str(fill_qty),
            "fill_price": str(fill_price),
            "pnl_this_trade": str(pnl_this_trade),
            "pnl_cumulative": str(self._cumulative_pnl),
            "pnl_realized": str(pnl_realized),
            "pnl_unrealized": str(pnl_unrealized),
            "entry_rationale": {
                "strategy": strategy,
                "trigger": trigger_text or self._build_trigger(
                    side, token, fill_price, market_state, inventory_skew_info
                ),
                "spread_model": spread_model_info or {},
                "inventory_skew": inventory_skew_info or {},
                "toxic_flow": toxic_flow_info or {"detected": False, "zscore": 0, "action": "normal"},
                "rewards_farming": rewards_info or {"adjustment_bps": 0, "reason": "N/A"},
            },
            "market_context": self._build_market_context(market_state),
            "feature_vector": self._build_feature_vector(features),
            "position_after": self._build_position(position),
            "timing": {
                "quote_to_fill_ms": round(quote_to_fill_ms, 1),
                "quote_age_ms": round(quote_age_ms, 1),
            },
            "kill_switch_state": kill_switch_state,
            "data_gap_seconds": round(data_gap_seconds, 2),
        }

        # Live-mode extra fields
        if self._mode == "live":
            record["is_production"] = True
            record["latency_ms"] = round(latency_ms, 1)
            record["gas_cost_usd"] = round(gas_cost_usd, 6)
            record["rejection_reason"] = rejection_reason
            record["real_fee_bps"] = round(real_fee_bps, 2)
            record["exchange_order_id"] = exchange_order_id

        if wallet_after:
            record["wallet_after"] = wallet_after

        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning("trade_logger.write_error", error=str(e))

    def _build_trigger(self, side, token, fill_price, market_state, skew_info) -> str:
        parts = []
        if side == "BUY":
            parts.append(f"Bid {token} a {fill_price}.")
        else:
            parts.append(f"Ask {token} a {fill_price}.")

        if market_state is not None:
            mid = getattr(market_state, "mid_price", Decimal("0"))
            spread = getattr(market_state, "spread_yes", Decimal("0"))
            if mid > 0:
                spread_pct = float(spread / mid * 100) if mid > 0 else 0
                parts.append(f"Spread {spread_pct:.1f}%.")
        if skew_info:
            net = skew_info.get("current_net", 0)
            parts.append(f"Inventory net={net}.")
        return " ".join(parts)

    @staticmethod
    def _build_market_context(ms) -> dict:
        if ms is None:
            return {}
        mid = getattr(ms, "mid_price", Decimal("0"))
        spread_bps = 0
        if mid > 0:
            spread_bps = int(float(getattr(ms, "spread_yes", Decimal("0"))) / float(mid) * 10000)
        return {
            "mid_price": str(getattr(ms, "mid_price", "0")),
            "best_bid": str(getattr(ms, "yes_bid", "0")),
            "best_ask": str(getattr(ms, "yes_ask", "0")),
            "spread_bps": spread_bps,
            "depth_bid_usd": str(getattr(ms, "depth_yes_bid", "0")),
            "depth_ask_usd": str(getattr(ms, "depth_yes_ask", "0")),
        }

    @staticmethod
    def _build_feature_vector(fv) -> dict:
        if fv is None:
            return {}
        return {
            "momentum_20": getattr(fv, "micro_momentum", 0),
            "volatility_60": getattr(fv, "volatility_1m", 0),
            "order_flow_imbalance": getattr(fv, "book_imbalance", 0),
            "data_quality": getattr(fv, "data_quality_score", 1.0),
        }

    @staticmethod
    def _build_position(pos) -> dict:
        if pos is None:
            return {}
        return {
            "qty_yes": str(getattr(pos, "qty_yes", 0)),
            "qty_no": str(getattr(pos, "qty_no", 0)),
            "net": str(getattr(pos, "qty_yes", Decimal("0")) - getattr(pos, "qty_no", Decimal("0"))),
            "exposure_usd": str(getattr(pos, "net_exposure_usd", 0)),
            "avg_entry_yes": str(getattr(pos, "avg_entry_yes", 0)),
            "avg_entry_no": str(getattr(pos, "avg_entry_no", 0)),
        }
