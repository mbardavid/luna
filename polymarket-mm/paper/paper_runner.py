"""PaperRunner — E2E pipeline: WS real → Feature Engine → Quote Engine → Paper Venue.

Connects to real Polymarket WebSocket, processes live orderbook data,
generates quotes through the strategy pipeline, and simulates fills
in the paper venue. All trading is simulated — NO real orders.

Usage:
    python -m paper.paper_runner --duration-hours 4
    python -m paper.paper_runner --config paper/runs/run-001.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.event_bus import EventBus
from core.kill_switch import KillSwitch, KillSwitchState
from data.ws_client import CLOBWebSocketClient
from models.market_state import MarketState, MarketType
from models.order import Order, Side
from models.position import Position
from paper.paper_venue import PaperVenue, MarketSimConfig, FeeConfig
from strategy.feature_engine import FeatureEngine, FeatureEngineConfig
from strategy.inventory_skew import InventorySkew, InventorySkewConfig
from strategy.quote_engine import QuoteEngine, QuoteEngineConfig
from strategy.spread_model import SpreadModel, SpreadModelConfig

logger = structlog.get_logger("paper.runner")

# ── Data directory ──────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "paper" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Market config from YAML ─────────────────────────────────────────

@dataclass
class MarketConfig:
    """Parsed market config from markets.yaml."""
    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    description: str
    market_type: MarketType
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool
    spread_min_bps: int
    max_position_size: Decimal
    enabled: bool


def load_markets(config_path: Path) -> list[MarketConfig]:
    """Load market configs from markets.yaml."""
    with open(config_path) as f:
        data = yaml.safe_load(f)

    markets = []
    for m in data.get("markets", []):
        if not m.get("enabled", True):
            continue
        params = m.get("params", {})
        mt = m.get("market_type", "OTHER")
        markets.append(MarketConfig(
            market_id=m["market_id"],
            condition_id=m["condition_id"],
            token_id_yes=m["token_id_yes"],
            token_id_no=m["token_id_no"],
            description=m.get("description", ""),
            market_type=MarketType(mt),
            tick_size=Decimal(str(params.get("tick_size", "0.01"))),
            min_order_size=Decimal(str(params.get("min_order_size", "5"))),
            neg_risk=params.get("neg_risk", False),
            spread_min_bps=int(params.get("spread_min_bps", 50)),
            max_position_size=Decimal(str(params.get("max_position_size", "500"))),
            enabled=m.get("enabled", True),
        ))
    return markets


# ── Metrics Collector ───────────────────────────────────────────────

@dataclass
class HourlyMetrics:
    """Metrics aggregated per hour."""
    hour: int = 0
    start_time: str = ""
    end_time: str = ""
    quotes_generated: int = 0
    orders_submitted: int = 0
    fills: int = 0
    fill_rate: float = 0.0
    total_pnl: float = 0.0
    realized_pnl: float = 0.0
    spreads_captured: list = field(default_factory=list)
    avg_spread_captured_bps: float = 0.0
    inventory_drift: dict = field(default_factory=dict)
    ws_messages: int = 0
    book_updates: int = 0
    errors: int = 0
    per_market: dict = field(default_factory=dict)


@dataclass
class MarketMetrics:
    """Per-market metrics."""
    market_id: str = ""
    quotes_generated: int = 0
    orders_submitted: int = 0
    fills: int = 0
    pnl: float = 0.0
    avg_spread_bps: float = 0.0
    inventory_yes: float = 0.0
    inventory_no: float = 0.0
    book_updates: int = 0
    last_mid: float = 0.0
    spreads: list = field(default_factory=list)


class MetricsCollector:
    """Collects and aggregates paper trading metrics."""

    def __init__(self):
        self.start_time = datetime.now(timezone.utc)
        self.hourly: list[dict] = []
        self.current_hour_start = time.monotonic()
        self.current_hour = 0

        # Counters for current hour
        self.quotes_generated = 0
        self.orders_submitted = 0
        self.fills = 0
        self.spreads_captured: list[float] = []
        self.ws_messages = 0
        self.book_updates = 0
        self.errors = 0
        self.per_market: dict[str, MarketMetrics] = {}

        # Global counters
        self.total_quotes = 0
        self.total_orders = 0
        self.total_fills = 0
        self.total_ws_messages = 0
        self.total_book_updates = 0
        self.total_errors = 0

    def ensure_market(self, market_id: str):
        if market_id not in self.per_market:
            self.per_market[market_id] = MarketMetrics(market_id=market_id)

    def record_quote(self, market_id: str, num_slices: int):
        self.quotes_generated += num_slices
        self.total_quotes += num_slices
        self.ensure_market(market_id)
        self.per_market[market_id].quotes_generated += num_slices

    def record_order(self, market_id: str):
        self.orders_submitted += 1
        self.total_orders += 1
        self.ensure_market(market_id)
        self.per_market[market_id].orders_submitted += 1

    def record_fill(self, market_id: str, spread_bps: float = 0.0):
        self.fills += 1
        self.total_fills += 1
        if spread_bps > 0:
            self.spreads_captured.append(spread_bps)
        self.ensure_market(market_id)
        self.per_market[market_id].fills += 1
        if spread_bps > 0:
            self.per_market[market_id].spreads.append(spread_bps)

    def record_ws_message(self):
        self.ws_messages += 1
        self.total_ws_messages += 1

    def record_book_update(self, market_id: str):
        self.book_updates += 1
        self.total_book_updates += 1
        self.ensure_market(market_id)
        self.per_market[market_id].book_updates += 1

    def record_error(self):
        self.errors += 1
        self.total_errors += 1

    def flush_hour(self, positions: dict[str, Position], total_pnl: Decimal) -> dict:
        """Flush current hour metrics and return snapshot."""
        now = datetime.now(timezone.utc)
        fill_rate = (self.fills / self.orders_submitted * 100) if self.orders_submitted > 0 else 0.0
        avg_spread = sum(self.spreads_captured) / len(self.spreads_captured) if self.spreads_captured else 0.0

        inventory_drift = {}
        for mid, pos in positions.items():
            inventory_drift[mid] = {
                "qty_yes": float(pos.qty_yes),
                "qty_no": float(pos.qty_no),
                "net": float(pos.qty_yes - pos.qty_no),
                "realized_pnl": float(pos.realized_pnl),
            }

        per_market_snap = {}
        for mid, mm in self.per_market.items():
            avg_s = sum(mm.spreads) / len(mm.spreads) if mm.spreads else 0.0
            per_market_snap[mid] = {
                "quotes": mm.quotes_generated,
                "orders": mm.orders_submitted,
                "fills": mm.fills,
                "avg_spread_bps": round(avg_s, 2),
                "book_updates": mm.book_updates,
                "inventory_yes": float(positions[mid].qty_yes) if mid in positions else 0.0,
                "inventory_no": float(positions[mid].qty_no) if mid in positions else 0.0,
            }

        snapshot = {
            "hour": self.current_hour,
            "start_time": self.start_time.isoformat() if self.current_hour == 0 else self.hourly[-1]["end_time"] if self.hourly else self.start_time.isoformat(),
            "end_time": now.isoformat(),
            "quotes_generated": self.quotes_generated,
            "orders_submitted": self.orders_submitted,
            "fills": self.fills,
            "fill_rate_pct": round(fill_rate, 2),
            "total_pnl": float(total_pnl),
            "avg_spread_captured_bps": round(avg_spread, 2),
            "inventory_drift": inventory_drift,
            "ws_messages": self.ws_messages,
            "book_updates": self.book_updates,
            "errors": self.errors,
            "per_market": per_market_snap,
        }

        self.hourly.append(snapshot)

        # Reset hourly counters
        self.current_hour += 1
        self.current_hour_start = time.monotonic()
        self.quotes_generated = 0
        self.orders_submitted = 0
        self.fills = 0
        self.spreads_captured = []
        self.ws_messages = 0
        self.book_updates = 0
        self.errors = 0
        for mm in self.per_market.values():
            mm.quotes_generated = 0
            mm.orders_submitted = 0
            mm.fills = 0
            mm.spreads = []
            mm.book_updates = 0

        return snapshot

    def save(self, path: Path):
        """Save all metrics to JSON."""
        data = {
            "start_time": self.start_time.isoformat(),
            "end_time": datetime.now(timezone.utc).isoformat(),
            "total_hours": len(self.hourly),
            "totals": {
                "quotes": self.total_quotes,
                "orders": self.total_orders,
                "fills": self.total_fills,
                "ws_messages": self.total_ws_messages,
                "book_updates": self.total_book_updates,
                "errors": self.total_errors,
            },
            "hourly": self.hourly,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info("metrics.saved", path=str(path))

    def save_checkpoint(self, path: Path):
        """Save checkpoint for resume capability."""
        self.save(path)


# ── Trade Logger (JSONL) ────────────────────────────────────────────


class TradeLogger:
    """Appends one JSONL line per fill to paper/data/trades.jsonl."""

    def __init__(self, path: Path | None = None, run_id: str = "unknown"):
        self._path = path or DATA_DIR / "trades.jsonl"
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
    ) -> None:
        self._trade_counter += 1
        self._cumulative_pnl += pnl_this_trade
        trade_id = f"{self._run_id}-{self._trade_counter:06d}"

        record = {
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
                    side, token, price, fill_price, market_state, inventory_skew_info
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

        if wallet_after:
            record["wallet_after"] = wallet_after

        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning("trade_logger.write_error", error=str(e))

    def _build_trigger(self, side, token, price, fill_price, market_state, skew_info) -> str:
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


# ── Live State Writer ───────────────────────────────────────────────


class LiveStateWriter:
    """Writes paper/data/live_state.json every N seconds."""

    def __init__(self, path: Path | None = None, run_id: str = "unknown",
                 hypothesis: str = "", config_path: str = "",
                 duration_target_h: float = 4.0):
        self._path = path or DATA_DIR / "live_state.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._run_id = run_id
        self._hypothesis = hypothesis
        self._config_path = config_path
        self._duration_target_h = duration_target_h
        self._start_time = time.monotonic()
        self._start_dt = datetime.now(timezone.utc)
        self._pnl_history: list[float] = []

    def write(
        self,
        *,
        status: str = "RUNNING",
        total_pnl: Decimal,
        realized_pnl: Decimal,
        unrealized_pnl: Decimal,
        positions: dict,
        metrics: "MetricsCollector",
        market_configs: list,
        book_tracker: "LiveBookTracker",
        kill_switch: Any,
        ws_connected: bool = True,
        wallet: dict | None = None,
    ) -> None:
        uptime_s = time.monotonic() - self._start_time
        target_s = self._duration_target_h * 3600
        progress_pct = min(100.0, uptime_s / target_s * 100) if target_s > 0 else 0

        pnl_f = float(total_pnl)
        self._pnl_history.append(pnl_f)

        # PnL stats
        max_dd = 0.0
        peak = self._pnl_history[0]
        for p in self._pnl_history:
            if p > peak:
                peak = p
            dd = peak - p
            if dd > max_dd:
                max_dd = dd

        hours_elapsed = uptime_s / 3600
        per_hour_avg = pnl_f / hours_elapsed if hours_elapsed > 0.01 else 0

        # Sharpe estimate (crude)
        sharpe = 0.0
        if len(self._pnl_history) > 10:
            returns = [self._pnl_history[i] - self._pnl_history[i-1]
                       for i in range(1, len(self._pnl_history))]
            mean_r = sum(returns) / len(returns) if returns else 0
            import math
            var_r = sum((r - mean_r) ** 2 for r in returns) / len(returns) if returns else 0
            std_r = math.sqrt(var_r) if var_r > 0 else 0
            sharpe = mean_r / std_r if std_r > 0 else 0

        # Markets data
        markets_data = {}
        for mc in market_configs:
            ms = book_tracker.get_market_state(mc)
            pos = positions.get(mc.market_id)
            mm = metrics.per_market.get(mc.market_id)
            mid = float(ms.mid_price) if ms and ms.mid_price > 0 else 0
            spread = int(float(ms.spread_yes) / mid * 10000) if ms and mid > 0 else 0

            pos_net = float(pos.qty_yes - pos.qty_no) if pos else 0
            pos_pnl = float(pos.realized_pnl) if pos else 0
            fills_count = mm.fills if mm else 0
            last_update_age = book_tracker.last_update_age(mc.token_id_yes)
            data_gap = last_update_age if last_update_age < 1e6 else 0

            markets_data[mc.market_id] = {
                "description": getattr(mc, "description", mc.market_id),
                "mid_price": mid,
                "spread_bps": spread,
                "our_bid": 0,  # Filled by quoting data if available
                "our_ask": 0,
                "position_net": pos_net,
                "pnl": pos_pnl,
                "fills_count": fills_count,
                "last_fill_ago_s": 0,
                "kill_switch": kill_switch.state.value if kill_switch else "RUNNING",
                "data_gap_s": round(data_gap, 1),
            }

        # Totals
        fill_rate = (metrics.total_fills / metrics.total_orders * 100) if metrics.total_orders > 0 else 0

        # System
        try:
            mem_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB → MB
        except Exception:
            mem_mb = 0

        state = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "status": status,
            "uptime_seconds": round(uptime_s, 1),
            "duration_target_hours": self._duration_target_h,
            "progress_pct": round(progress_pct, 1),
            "pnl": {
                "cumulative": round(pnl_f, 4),
                "realized": round(float(realized_pnl), 4),
                "unrealized": round(float(unrealized_pnl), 4),
                "per_hour_avg": round(per_hour_avg, 4),
                "max_drawdown": round(-max_dd, 4),
                "sharpe_estimate": round(sharpe, 2),
            },
            "markets": markets_data,
            "totals": {
                "quotes_generated": metrics.total_quotes,
                "orders_submitted": metrics.total_orders,
                "fills": metrics.total_fills,
                "fill_rate_pct": round(fill_rate, 2),
                "ws_messages": metrics.total_ws_messages,
                "errors": metrics.total_errors,
            },
            "system": {
                "memory_mb": round(mem_mb, 1),
                "ws_connected": ws_connected,
            },
            "hypothesis_under_test": self._hypothesis,
            "run_config_path": self._config_path,
        }

        # Add wallet data if available
        if wallet:
            state["wallet"] = wallet

        try:
            tmp_path = self._path.with_suffix(".tmp")
            with open(tmp_path, "w") as f:
                json.dump(state, f, indent=2, default=str)
            tmp_path.replace(self._path)
        except Exception as e:
            logger.warning("live_state.write_error", error=str(e))


# ── Run Config Loader ───────────────────────────────────────────────


@dataclass
class RunConfig:
    """Parsed run config from paper/runs/<run>.yaml."""
    run_id: str = "run-001"
    duration_hours: float = 4.0
    hypothesis: str = "H1"
    params: dict = field(default_factory=dict)
    parent_run: str | None = None
    changes_from_parent: str | None = None
    initial_balance: Decimal = Decimal("500")

    @classmethod
    def from_yaml(cls, path: Path) -> "RunConfig":
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        params = data.get("params", {})
        return cls(
            run_id=data.get("run_id", "run-001"),
            duration_hours=float(data.get("duration_hours", 4.0)),
            hypothesis=data.get("hypothesis", "H1"),
            params=params,
            parent_run=data.get("parent_run"),
            changes_from_parent=data.get("changes_from_parent"),
            initial_balance=Decimal(str(data.get("initial_balance", 500))),
        )


# ── Run History ─────────────────────────────────────────────────────


class RunHistory:
    """Append-only run history in paper/runs/history.json."""

    def __init__(self, path: Path | None = None):
        self._path = path or (PROJECT_ROOT / "paper" / "runs" / "history.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        run_id: str,
        hypothesis: str,
        result: str,
        pnl_per_hour: float,
        duration_h: float,
        fill_rate: float,
        max_drawdown: float,
    ) -> None:
        entries = []
        if self._path.exists():
            try:
                with open(self._path) as f:
                    entries = json.load(f)
            except Exception:
                entries = []

        entries.append({
            "run_id": run_id,
            "hypothesis": hypothesis,
            "result": result,
            "pnl_per_hour": round(pnl_per_hour, 4),
            "duration_h": round(duration_h, 2),
            "fill_rate": round(fill_rate, 2),
            "max_drawdown": round(max_drawdown, 4),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        with open(self._path, "w") as f:
            json.dump(entries, f, indent=2)


# ── Live Book State Tracker ─────────────────────────────────────────

class LiveBookTracker:
    """Tracks live orderbook state from WS updates per token_id."""

    def __init__(self):
        self._books: dict[str, dict] = {}  # token_id -> {bids, asks}
        self._last_update: dict[str, float] = {}

    def update(self, token_id: str, payload: dict):
        """Update book from WS event payload."""
        bids = payload.get("bids", [])
        asks = payload.get("asks", [])

        if bids or asks:
            self._books[token_id] = {
                "bids": bids,
                "asks": asks,
                "timestamp": datetime.now(timezone.utc),
            }
            self._last_update[token_id] = time.monotonic()

    def get_book(self, token_id: str) -> dict:
        """Get current book for a token_id."""
        return self._books.get(token_id, {"bids": [], "asks": []})

    def get_market_state(
        self,
        market_cfg: MarketConfig,
    ) -> MarketState | None:
        """Build MarketState from live book data."""
        yes_book = self._books.get(market_cfg.token_id_yes)
        no_book = self._books.get(market_cfg.token_id_no)

        if not yes_book and not no_book:
            return None

        # Extract best bid/ask for YES
        yes_bid = Decimal("0")
        yes_ask = Decimal("0")
        depth_yes_bid = Decimal("0")
        depth_yes_ask = Decimal("0")

        if yes_book:
            bids = yes_book.get("bids", [])
            asks = yes_book.get("asks", [])
            if bids:
                # bids are sorted highest first
                best_bid = bids[0] if isinstance(bids[0], dict) else {"price": bids[0].get("price", "0") if isinstance(bids[0], dict) else "0", "size": "0"}
                yes_bid = Decimal(str(best_bid.get("price", "0")))
                depth_yes_bid = Decimal(str(best_bid.get("size", "0")))
            if asks:
                best_ask = asks[0] if isinstance(asks[0], dict) else {"price": "0", "size": "0"}
                yes_ask = Decimal(str(best_ask.get("price", "0")))
                depth_yes_ask = Decimal(str(best_ask.get("size", "0")))

        # Extract best bid/ask for NO
        no_bid = Decimal("0")
        no_ask = Decimal("0")
        depth_no_bid = Decimal("0")
        depth_no_ask = Decimal("0")

        if no_book:
            bids = no_book.get("bids", [])
            asks = no_book.get("asks", [])
            if bids:
                best_bid = bids[0] if isinstance(bids[0], dict) else {"price": "0", "size": "0"}
                no_bid = Decimal(str(best_bid.get("price", "0")))
                depth_no_bid = Decimal(str(best_bid.get("size", "0")))
            if asks:
                best_ask = asks[0] if isinstance(asks[0], dict) else {"price": "0", "size": "0"}
                no_ask = Decimal(str(best_ask.get("price", "0")))
                depth_no_ask = Decimal(str(best_ask.get("size", "0")))

        # Validate bid <= ask
        if yes_bid > Decimal("0") and yes_ask > Decimal("0") and yes_bid >= yes_ask:
            # Crossed book — skip
            return None
        if no_bid > Decimal("0") and no_ask > Decimal("0") and no_bid >= no_ask:
            return None

        try:
            return MarketState(
                market_id=market_cfg.market_id,
                condition_id=market_cfg.condition_id,
                token_id_yes=market_cfg.token_id_yes,
                token_id_no=market_cfg.token_id_no,
                tick_size=market_cfg.tick_size,
                min_order_size=market_cfg.min_order_size,
                neg_risk=market_cfg.neg_risk,
                market_type=market_cfg.market_type,
                yes_bid=yes_bid,
                yes_ask=yes_ask,
                no_bid=no_bid,
                no_ask=no_ask,
                depth_yes_bid=depth_yes_bid,
                depth_yes_ask=depth_yes_ask,
                depth_no_bid=depth_no_bid,
                depth_no_ask=depth_no_ask,
            )
        except Exception as e:
            logger.warning("market_state.build_failed", market_id=market_cfg.market_id, error=str(e))
            return None

    def update_best(self, token_id: str, best_bid: str, best_ask: str):
        """Update best bid/ask from price_change events."""
        existing = self._books.get(token_id, {"bids": [], "asks": []})
        bid_dec = Decimal(str(best_bid))
        ask_dec = Decimal(str(best_ask))

        # Update or create top-of-book
        if existing.get("bids"):
            existing["bids"][0] = {"price": bid_dec, "size": existing["bids"][0].get("size", Decimal("100"))}
        else:
            existing["bids"] = [{"price": bid_dec, "size": Decimal("100")}]

        if existing.get("asks"):
            existing["asks"][0] = {"price": ask_dec, "size": existing["asks"][0].get("size", Decimal("100"))}
        else:
            existing["asks"] = [{"price": ask_dec, "size": Decimal("100")}]

        existing["timestamp"] = datetime.now(timezone.utc)
        self._books[token_id] = existing
        self._last_update[token_id] = time.monotonic()

    def last_update_age(self, token_id: str) -> float:
        """Seconds since last update for token_id."""
        ts = self._last_update.get(token_id)
        if ts is None:
            return float("inf")
        return time.monotonic() - ts


# ── Paper Trading Pipeline ──────────────────────────────────────────

class PaperTradingPipeline:
    """E2E paper trading pipeline with real WS data."""

    def __init__(
        self,
        market_configs: list[MarketConfig],
        duration_hours: float = 4.0,
        quote_interval_s: float = 2.0,
        metrics_flush_interval_s: float = 3600.0,
        run_config: RunConfig | None = None,
        fill_probability: float = 0.5,
        order_size: Decimal = Decimal("50"),
        half_spread_bps: int = 50,
        gamma: float = 0.3,
        initial_balance: Decimal = Decimal("500"),
        kill_switch_max_drawdown_pct: float = 25.0,
        kill_switch_alert_pct: float = 15.0,
        adverse_selection_bps: int = 0,
        maker_fee_bps: int = 0,
        fill_distance_decay: bool = False,
    ):
        self.market_configs = market_configs
        self.duration_hours = duration_hours
        self.quote_interval = quote_interval_s
        self.metrics_flush_interval = metrics_flush_interval_s
        self.run_config = run_config
        self._run_id = run_config.run_id if run_config else f"run-{uuid4().hex[:8]}"
        self._hypothesis = run_config.hypothesis if run_config else ""

        # Kill switch thresholds (configurable via run config)
        self._kill_switch_max_drawdown_pct = kill_switch_max_drawdown_pct
        self._kill_switch_alert_pct = kill_switch_alert_pct

        # Core components
        self.event_bus = EventBus()
        self.feature_engine = FeatureEngine(FeatureEngineConfig(
            momentum_window=20,
            volatility_window=60,
            imbalance_window=30,
        ))
        self.quote_engine = QuoteEngine(
            spread_model=SpreadModel(SpreadModelConfig(
                min_half_spread_bps=Decimal(str(half_spread_bps)),
            )),
            inventory_skew=InventorySkew(InventorySkewConfig(
                gamma=Decimal(str(gamma)),
            )),
            config=QuoteEngineConfig(
                default_order_size=order_size,
                num_levels=1,
                default_ttl_ms=30_000,
            ),
        )

        # Paper venue for order simulation
        venue_configs = [
            MarketSimConfig(
                market_id=m.market_id,
                condition_id=m.condition_id,
                token_id_yes=m.token_id_yes,
                token_id_no=m.token_id_no,
                tick_size=m.tick_size,
                min_order_size=m.min_order_size,
                neg_risk=m.neg_risk,
                market_type=m.market_type,
                initial_yes_mid=Decimal("0.50"),
                volatility=Decimal("0.005"),
                fill_probability=fill_probability,
                adverse_selection_bps=adverse_selection_bps,
                fill_distance_decay=fill_distance_decay,
            )
            for m in market_configs
        ]

        # Fee config
        fee_config = FeeConfig(maker_fee_bps=maker_fee_bps)

        self.venue = PaperVenue(
            event_bus=self.event_bus,
            configs=venue_configs,
            fill_latency_ms=50.0,
            partial_fill_probability=0.5,
            initial_balance=initial_balance,
            fee_config=fee_config,
        )

        # Kill switch
        self.kill_switch = KillSwitch(
            event_bus=self.event_bus,
            max_daily_loss_usd=Decimal("50"),
            data_gap_tolerance_seconds=15,
        )

        # Trade logger & live state
        self.trade_logger = TradeLogger(run_id=self._run_id)
        self.live_state_writer = LiveStateWriter(
            run_id=self._run_id,
            hypothesis=self._hypothesis,
            config_path=run_config.params.get("config_path", "") if run_config else "",
            duration_target_h=duration_hours,
        )
        self.run_history = RunHistory()

        # State tracking
        self.book_tracker = LiveBookTracker()
        self.metrics = MetricsCollector()
        self.positions: dict[str, Position] = {}
        self.total_pnl = Decimal("0")
        self._realized_pnl = Decimal("0")
        self._unrealized_pnl = Decimal("0")

        # Initialize positions
        for m in market_configs:
            self.positions[m.market_id] = Position(
                market_id=m.market_id,
                token_id_yes=m.token_id_yes,
                token_id_no=m.token_id_no,
            )

        # WS client
        token_ids = []
        for m in market_configs:
            token_ids.append(m.token_id_yes)
            token_ids.append(m.token_id_no)

        self.ws_client = CLOBWebSocketClient(
            event_bus=self.event_bus,
            token_ids=token_ids,
        )

        # Token ID → market config mapping
        self._token_to_market: dict[str, MarketConfig] = {}
        for m in market_configs:
            self._token_to_market[m.token_id_yes] = m
            self._token_to_market[m.token_id_no] = m

        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the paper trading pipeline."""
        logger.info(
            "pipeline.starting",
            markets=len(self.market_configs),
            duration_hours=self.duration_hours,
        )

        self._running = True

        # Connect venue (initializes books)
        await self.venue.connect()

        # Start WS client
        await self.ws_client.start()

        # Wait a bit for initial WS data
        logger.info("pipeline.waiting_for_initial_data", seconds=10)
        await asyncio.sleep(10)

        ws_msgs = self.ws_client.messages_received
        logger.info("pipeline.initial_data", ws_messages=ws_msgs)

        # Start main loops
        tasks = [
            asyncio.create_task(self._ws_event_loop()),
            asyncio.create_task(self._price_change_loop()),
            asyncio.create_task(self._quote_loop()),
            asyncio.create_task(self._metrics_flush_loop()),
            asyncio.create_task(self._data_gap_monitor()),
            asyncio.create_task(self._duration_watchdog()),
            asyncio.create_task(self._fill_event_loop()),
            asyncio.create_task(self._position_rebalance_loop()),
            asyncio.create_task(self._live_state_loop()),
        ]

        try:
            # Wait for shutdown or duration to complete
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("pipeline.shutting_down")
            self._running = False
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._final_flush()
            await self.ws_client.stop()
            await self.venue.disconnect()
            logger.info("pipeline.stopped")

    async def stop(self):
        """Signal graceful shutdown."""
        self._running = False
        self._shutdown_event.set()

    # ── Event Processing Loops ──────────────────────────────────

    async def _ws_event_loop(self):
        """Subscribe to book events from EventBus and update tracker."""
        try:
            async for event in self.event_bus.subscribe("book"):
                if not self._running:
                    break

                self.metrics.record_ws_message()
                payload = event.payload
                token_id = payload.get("token_id", "")

                if token_id:
                    self.book_tracker.update(token_id, payload)
                    market_cfg = self._token_to_market.get(token_id)
                    if market_cfg:
                        self.kill_switch.record_data_update(market_cfg.market_id)
                        self.metrics.record_book_update(market_cfg.market_id)

                        logger.debug(
                            "ws.book_update",
                            market_id=market_cfg.market_id,
                            token_id=token_id[:20] + "...",
                            bids=len(payload.get("bids", [])),
                            asks=len(payload.get("asks", [])),
                        )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws_event_loop.error", error=str(e))

    async def _price_change_loop(self):
        """Subscribe to price_change events for continuous bid/ask updates."""
        try:
            async for event in self.event_bus.subscribe("price_change"):
                if not self._running:
                    break

                self.metrics.record_ws_message()
                payload = event.payload
                raw = payload.get("raw", {})
                price_changes = raw.get("price_changes", [])

                for pc in price_changes:
                    asset_id = pc.get("asset_id", "")
                    best_bid = pc.get("best_bid")
                    best_ask = pc.get("best_ask")

                    if asset_id and best_bid and best_ask:
                        # Update book tracker with best bid/ask from price_change
                        self.book_tracker.update_best(asset_id, best_bid, best_ask)
                        market_cfg = self._token_to_market.get(asset_id)
                        if market_cfg:
                            self.kill_switch.record_data_update(market_cfg.market_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("price_change_loop.error", error=str(e))

    async def _fill_event_loop(self):
        """Subscribe to fill events and update positions/PnL."""
        try:
            async for event in self.event_bus.subscribe("fill"):
                if not self._running:
                    break

                payload = event.payload
                market_id = payload.get("market_id", "")
                token_id = payload.get("token_id", "")
                fill_price = Decimal(str(payload.get("fill_price", "0")))
                fill_qty = Decimal(str(payload.get("fill_qty", "0")))
                fill_fee = Decimal(str(payload.get("fee", "0")))
                side = payload.get("side", "")

                logger.info(
                    "fill.received",
                    market_id=market_id,
                    side=side,
                    price=str(fill_price),
                    qty=str(fill_qty),
                )

                # Compute approximate spread captured (rough)
                market_cfg = next(
                    (m for m in self.market_configs if m.market_id == market_id),
                    None,
                )
                spread_bps = 0.0
                ms = None
                if market_cfg:
                    ms = self.book_tracker.get_market_state(market_cfg)
                    if ms and ms.mid_price > 0:
                        spread_bps = float(abs(fill_price - ms.mid_price) / ms.mid_price * 10000)

                self.metrics.record_fill(market_id, spread_bps)

                # Compute PnL for this trade
                pos = self.positions.get(market_id)
                pnl_this = Decimal("0")
                if side == "SELL" and pos:
                    # Rough PnL: (fill_price - avg_entry) * fill_qty
                    # Determine which token
                    if market_cfg and token_id == market_cfg.token_id_yes:
                        pnl_this = (fill_price - pos.avg_entry_yes) * fill_qty
                    elif market_cfg and token_id == market_cfg.token_id_no:
                        pnl_this = (fill_price - pos.avg_entry_no) * fill_qty

                realized = pos.realized_pnl if pos else Decimal("0")
                unrealized = Decimal("0")

                # Determine token label
                token_label = "YES"
                if market_cfg and token_id == market_cfg.token_id_no:
                    token_label = "NO"

                # Data gap
                data_gap = 0.0
                if market_cfg:
                    age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                    data_gap = age if age < 1e6 else 0

                # Log trade
                self.trade_logger.log_trade(
                    market_id=market_id,
                    market_description=getattr(market_cfg, "description", market_id) if market_cfg else market_id,
                    side=side,
                    token=token_label,
                    price=fill_price,
                    size=fill_qty,
                    fill_qty=fill_qty,
                    fill_price=fill_price,
                    pnl_this_trade=pnl_this,
                    pnl_realized=realized,
                    pnl_unrealized=unrealized,
                    position=pos,
                    market_state=ms,
                    features=None,  # Not available in fill context
                    kill_switch_state=self.kill_switch.state.value,
                    data_gap_seconds=data_gap,
                    wallet_after={
                        "available": float(self.venue.available_balance),
                        "locked": float(self.venue.locked_balance),
                        "equity": float(self.venue.total_equity()),
                        "fee": float(fill_fee),
                        "total_fees": float(self.venue.total_fees),
                    },
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("fill_event_loop.error", error=str(e))

    async def _quote_loop(self):
        """Main quoting loop: feature engine → quote engine → paper venue."""
        elapsed_hours = Decimal("0")
        start_time = time.monotonic()

        while self._running:
            try:
                if self.kill_switch.state == KillSwitchState.HALTED:
                    logger.warning("quote_loop.halted", reason="kill_switch")
                    await asyncio.sleep(5)
                    continue

                if self.kill_switch.state == KillSwitchState.PAUSED:
                    logger.info("quote_loop.paused", reason="kill_switch")
                    await asyncio.sleep(1)
                    continue

                elapsed_hours = Decimal(str(round(
                    (time.monotonic() - start_time) / 3600, 4
                )))

                for market_cfg in self.market_configs:
                    if market_cfg.market_id in self.kill_switch.paused_markets:
                        continue

                    await self._process_market(market_cfg, elapsed_hours)

                await asyncio.sleep(self.quote_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("quote_loop.error", error=str(e))
                self.metrics.record_error()
                await asyncio.sleep(1)

    async def _process_market(self, market_cfg: MarketConfig, elapsed_hours: Decimal):
        """Process a single market: feature → quote → submit."""
        # Build market state from live data
        market_state = self.book_tracker.get_market_state(market_cfg)
        if market_state is None:
            logger.debug(
                "process_market.no_data",
                market_id=market_cfg.market_id,
            )
            return

        if market_state.mid_price <= 0:
            return

        # Get orderbook for feature computation
        yes_book = self.book_tracker.get_book(market_cfg.token_id_yes)

        # Compute features
        try:
            features = await self.feature_engine.compute(
                market_state=market_state,
                orderbook=yes_book,
            )
        except Exception as e:
            logger.warning(
                "feature_engine.error",
                market_id=market_cfg.market_id,
                error=str(e),
            )
            return

        # Get current position
        position = self.positions.get(market_cfg.market_id)

        # Generate quotes
        try:
            plan = self.quote_engine.generate_quotes(
                state=market_state,
                features=features,
                position=position,
                elapsed_hours=elapsed_hours,
                available_balance=self.venue.available_balance,
                max_position_size=market_cfg.max_position_size,
                market_min_spread_bps=Decimal(str(market_cfg.spread_min_bps)),
            )
        except Exception as e:
            logger.warning(
                "quote_engine.error",
                market_id=market_cfg.market_id,
                error=str(e),
            )
            return

        if not plan.slices:
            return

        self.metrics.record_quote(market_cfg.market_id, len(plan.slices))

        # Cancel all outstanding orders before placing new quotes.
        # This prevents capital from being locked in stale orders
        # and avoids wallet exhaustion from accumulated open orders.
        open_orders = await self.venue.get_open_orders()
        for oo in open_orders:
            if oo.market_id == market_cfg.market_id:
                await self.venue.cancel_order(oo.client_order_id)

        # Convert to orders and submit to paper venue
        orders = plan.to_order_intents()
        for order in orders:
            try:
                self.metrics.record_order(market_cfg.market_id)
                result = await self.venue.submit_order(order)

                if result.filled_qty > 0:
                    # Update position from venue
                    venue_pos = self.venue.get_position(market_cfg.market_id)
                    if venue_pos:
                        self.positions[market_cfg.market_id] = venue_pos

                    self.total_pnl = self.venue.total_pnl

                    logger.info(
                        "order.result",
                        market_id=market_cfg.market_id,
                        side=order.side.value,
                        price=str(order.price),
                        status=result.status.value,
                        filled=str(result.filled_qty),
                        total_pnl=str(self.total_pnl),
                    )
            except Exception as e:
                logger.warning(
                    "order.submit_error",
                    market_id=market_cfg.market_id,
                    error=str(e),
                )

    async def _metrics_flush_loop(self):
        """Flush metrics every hour."""
        while self._running:
            try:
                await asyncio.sleep(self.metrics_flush_interval)
                if not self._running:
                    break

                snapshot = self.metrics.flush_hour(self.positions, self.total_pnl)
                logger.info(
                    "metrics.hourly_flush",
                    hour=snapshot["hour"],
                    quotes=snapshot["quotes_generated"],
                    orders=snapshot["orders_submitted"],
                    fills=snapshot["fills"],
                    fill_rate=snapshot["fill_rate_pct"],
                    pnl=snapshot["total_pnl"],
                    ws_messages=snapshot["ws_messages"],
                )

                # Save checkpoint
                self.metrics.save_checkpoint(
                    DATA_DIR / "metrics_checkpoint.json"
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("metrics_flush.error", error=str(e))

    async def _data_gap_monitor(self):
        """Monitor data gaps and trigger kill switch."""
        while self._running:
            try:
                await asyncio.sleep(5)

                for market_cfg in self.market_configs:
                    # Check YES token data age
                    yes_age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                    no_age = self.book_tracker.last_update_age(market_cfg.token_id_no)
                    min_age = min(yes_age, no_age)

                    if min_age > 15 and min_age < float("inf"):
                        await self.kill_switch.trigger_data_gap(
                            market_id=market_cfg.market_id,
                            gap_seconds=min_age,
                        )

                # Record heartbeat
                self.kill_switch.record_heartbeat()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("data_gap_monitor.error", error=str(e))

    async def _duration_watchdog(self):
        """Stop after duration_hours."""
        total_seconds = self.duration_hours * 3600
        try:
            await asyncio.sleep(total_seconds)
            logger.info("duration.reached", hours=self.duration_hours)
            await self.stop()
        except asyncio.CancelledError:
            pass

    async def _position_rebalance_loop(self):
        """Periodically reset positions when they exceed limits.

        In paper trading, the venue fills too aggressively, causing
        inventory to grow unbounded. This loop simulates a rebalancing
        by resetting positions at the current mark-to-market, keeping
        the realized PnL intact.
        """
        MAX_NET_POSITION = 500  # Max net qty per market before rebalance

        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds

                for market_cfg in self.market_configs:
                    pos = self.positions.get(market_cfg.market_id)
                    if not pos:
                        continue

                    net = abs(pos.qty_yes - pos.qty_no)
                    if net > MAX_NET_POSITION:
                        realized = pos.realized_pnl
                        # Reset position but keep PnL
                        self.positions[market_cfg.market_id] = Position(
                            market_id=market_cfg.market_id,
                            token_id_yes=market_cfg.token_id_yes,
                            token_id_no=market_cfg.token_id_no,
                        )
                        # Carry forward PnL
                        self.positions[market_cfg.market_id].realized_pnl = realized

                        # Also reset venue position
                        venue_pos = self.venue.get_position(market_cfg.market_id)
                        if venue_pos:
                            self.venue.reset_position(market_cfg.market_id)

                        logger.info(
                            "position.rebalanced",
                            market_id=market_cfg.market_id,
                            old_net=float(net),
                            realized_pnl=float(realized),
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("rebalance.error", error=str(e))

    async def _live_state_loop(self):
        """Write live_state.json every 5 seconds."""
        while self._running:
            try:
                await asyncio.sleep(5)
                if not self._running:
                    break

                # Compute realized/unrealized PnL
                realized = Decimal("0")
                unrealized = Decimal("0")
                for mid, pos in self.positions.items():
                    realized += pos.realized_pnl
                    # Rough unrealized: (mid - avg_entry) * qty for each token
                    mc = next((m for m in self.market_configs if m.market_id == mid), None)
                    if mc:
                        ms = self.book_tracker.get_market_state(mc)
                        if ms and ms.mid_price > 0:
                            if pos.qty_yes > 0 and pos.avg_entry_yes > 0:
                                unrealized += (ms.mid_price - pos.avg_entry_yes) * pos.qty_yes
                            if pos.qty_no > 0 and pos.avg_entry_no > 0:
                                no_mid = Decimal("1") - ms.mid_price
                                unrealized += (no_mid - pos.avg_entry_no) * pos.qty_no

                self._realized_pnl = realized
                self._unrealized_pnl = unrealized

                self.live_state_writer.write(
                    status="RUNNING",
                    total_pnl=self.total_pnl,
                    realized_pnl=realized,
                    unrealized_pnl=unrealized,
                    positions=self.positions,
                    metrics=self.metrics,
                    market_configs=self.market_configs,
                    book_tracker=self.book_tracker,
                    kill_switch=self.kill_switch,
                    ws_connected=self.ws_client.connected if hasattr(self.ws_client, 'connected') else True,
                    wallet=self.venue.wallet_snapshot(),
                )

                # ── Wallet-based kill switch checks ──────────────
                equity = self.venue.total_equity()
                initial = self.venue.initial_balance
                if initial > Decimal("0"):
                    drawdown_pct = float((initial - equity) / initial * 100)

                    # Configurable thresholds (from run config)
                    kill_pct = self._kill_switch_max_drawdown_pct
                    alert_pct = self._kill_switch_alert_pct

                    if drawdown_pct >= kill_pct:
                        # Drawdown exceeds kill threshold — trigger kill switch
                        loss = initial - equity
                        logger.critical(
                            "wallet.drawdown_kill_switch",
                            equity=str(equity),
                            initial=str(initial),
                            drawdown_pct=round(drawdown_pct, 2),
                            kill_threshold_pct=kill_pct,
                        )
                        await self.kill_switch.trigger_max_drawdown(loss)
                    elif drawdown_pct >= alert_pct:
                        # Drawdown exceeds alert threshold — warn but continue
                        logger.warning(
                            "wallet.drawdown_alert",
                            equity=str(equity),
                            initial=str(initial),
                            drawdown_pct=round(drawdown_pct, 2),
                            alert_threshold_pct=alert_pct,
                            kill_threshold_pct=kill_pct,
                        )

                if self.venue.available_balance < Decimal("10"):
                    # Very low balance — pause quoting
                    if self.kill_switch.state != KillSwitchState.HALTED:
                        logger.warning(
                            "wallet.low_balance_pause",
                            available=str(self.venue.available_balance),
                        )
                        # Pause all markets
                        for mc in self.market_configs:
                            self.kill_switch._paused_markets.add(mc.market_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("live_state_loop.error", error=str(e))

    async def _final_flush(self):
        """Final metrics flush and save."""
        # Flush remaining hour
        self.metrics.flush_hour(self.positions, self.total_pnl)

        # Save final metrics
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.metrics.save(DATA_DIR / f"metrics_{timestamp}.json")
        self.metrics.save(DATA_DIR / "metrics_latest.json")

        # Save positions
        positions_data = {}
        for mid, pos in self.positions.items():
            positions_data[mid] = {
                "qty_yes": float(pos.qty_yes),
                "qty_no": float(pos.qty_no),
                "avg_entry_yes": float(pos.avg_entry_yes),
                "avg_entry_no": float(pos.avg_entry_no),
                "realized_pnl": float(pos.realized_pnl),
            }

        with open(DATA_DIR / "positions_final.json", "w") as f:
            json.dump({
                "total_pnl": float(self.total_pnl),
                "positions": positions_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }, f, indent=2)

        logger.info(
            "final_metrics",
            total_pnl=str(self.total_pnl),
            total_quotes=self.metrics.total_quotes,
            total_orders=self.metrics.total_orders,
            total_fills=self.metrics.total_fills,
            total_ws_messages=self.metrics.total_ws_messages,
            total_hours=len(self.metrics.hourly),
        )

        # Write final live state
        self.live_state_writer.write(
            status="FINISHED",
            total_pnl=self.total_pnl,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self._unrealized_pnl,
            positions=self.positions,
            metrics=self.metrics,
            market_configs=self.market_configs,
            book_tracker=self.book_tracker,
            kill_switch=self.kill_switch,
            wallet=self.venue.wallet_snapshot(),
        )

        # Write run history
        uptime_h = (time.monotonic() - self.live_state_writer._start_time) / 3600
        fill_rate = (self.metrics.total_fills / self.metrics.total_orders * 100) if self.metrics.total_orders > 0 else 0
        pnl_per_h = float(self.total_pnl) / uptime_h if uptime_h > 0.01 else 0

        # Determine result
        result = "INCONCLUSIVE"
        if uptime_h >= 1:
            if pnl_per_h > 0:
                result = "PASS"
            else:
                result = "FAIL"

        try:
            self.run_history.append(
                run_id=self._run_id,
                hypothesis=self._hypothesis,
                result=result,
                pnl_per_hour=pnl_per_h,
                duration_h=uptime_h,
                fill_rate=fill_rate,
                max_drawdown=float(self.live_state_writer._pnl_history[-1]) - max(self.live_state_writer._pnl_history) if self.live_state_writer._pnl_history else 0,
            )
        except Exception as e:
            logger.warning("run_history.append_error", error=str(e))


# ── Kill Switch Test Suite ──────────────────────────────────────────

async def test_kill_switch(event_bus: EventBus) -> dict:
    """Test all kill switch triggers and return results."""
    results = {}
    cancelled_orders = 0

    async def mock_cancel_all():
        nonlocal cancelled_orders
        cancelled_orders += 3
        return 3

    async def mock_cancel_market(market_id: str):
        nonlocal cancelled_orders
        cancelled_orders += 1
        return 1

    ks = KillSwitch(
        event_bus=event_bus,
        order_cancel_callback=mock_cancel_all,
        market_cancel_callback=mock_cancel_market,
        max_daily_loss_usd=Decimal("50"),
        data_gap_tolerance_seconds=5,
    )

    # Test 1: Drawdown trigger
    logger.info("kill_switch_test.drawdown")
    await ks.trigger_max_drawdown(Decimal("60"))
    results["drawdown"] = {
        "triggered": ks.state == KillSwitchState.HALTED,
        "state": ks.state.value,
        "loss": "60",
        "limit": "50",
    }
    assert ks.state == KillSwitchState.HALTED
    await ks.reset()
    logger.info("kill_switch_test.drawdown.passed")

    # Test 2: Heartbeat failure
    logger.info("kill_switch_test.heartbeat")
    await ks.trigger_heartbeat_missed({"reason": "test"})
    results["heartbeat_missed"] = {
        "triggered": ks.state == KillSwitchState.HALTED,
        "state": ks.state.value,
    }
    assert ks.state == KillSwitchState.HALTED
    await ks.reset()
    logger.info("kill_switch_test.heartbeat.passed")

    # Test 3: Data gap
    logger.info("kill_switch_test.data_gap")
    await ks.trigger_data_gap("test-market", gap_seconds=10.0)
    results["data_gap"] = {
        "triggered": "test-market" in ks.paused_markets,
        "paused_markets": list(ks.paused_markets),
    }
    assert "test-market" in ks.paused_markets
    await ks.reset()
    logger.info("kill_switch_test.data_gap.passed")

    # Test 4: Engine restart (HTTP 425/429)
    logger.info("kill_switch_test.engine_restart")
    await ks.trigger_engine_restart({"status_code": 425})
    results["engine_restart"] = {
        "triggered": ks.state == KillSwitchState.PAUSED,
        "state": ks.state.value,
    }
    assert ks.state == KillSwitchState.PAUSED

    # Wait for auto-resume (backoff is 2s)
    await asyncio.sleep(3)
    results["engine_restart"]["auto_resumed"] = ks.state == KillSwitchState.RUNNING
    logger.info("kill_switch_test.engine_restart.passed", auto_resumed=ks.state == KillSwitchState.RUNNING)
    await ks.reset()

    # Test 5: Reconciliation mismatch
    logger.info("kill_switch_test.reconciliation")
    await ks.trigger_reconciliation_mismatch([
        {"type": "position", "detail": "expected 100, got 95"},
    ])
    results["reconciliation_mismatch"] = {
        "triggered": ks.state == KillSwitchState.HALTED,
        "state": ks.state.value,
    }
    assert ks.state == KillSwitchState.HALTED
    await ks.reset()
    logger.info("kill_switch_test.reconciliation.passed")

    results["all_passed"] = all(
        r.get("triggered", False) for r in results.values()
        if isinstance(r, dict) and "triggered" in r
    )
    results["total_orders_cancelled"] = cancelled_orders

    logger.info("kill_switch_test.complete", all_passed=results["all_passed"])
    return results


# ── Main ────────────────────────────────────────────────────────────

async def async_main(args):
    """Main async entrypoint."""
    # Configure structured logging
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Load run config if provided
    run_config = None
    fill_probability = 0.5
    order_size = Decimal("50")
    half_spread_bps = 50
    gamma = 0.3

    if args.config:
        config_file = Path(args.config)
        if not config_file.is_absolute():
            config_file = PROJECT_ROOT / config_file
        run_config = RunConfig.from_yaml(config_file)
        params = run_config.params
        args.duration_hours = run_config.duration_hours
        fill_probability = float(params.get("fill_probability", params.get("fill_probability_override", 0.5)))
        order_size = Decimal(str(params.get("order_size", params.get("default_order_size", "50"))))
        half_spread_bps = int(params.get("half_spread_bps", params.get("default_half_spread_bps", 50)))
        gamma = float(params.get("gamma", params.get("gamma_risk_aversion", 0.3)))
        if params.get("quote_interval", params.get("quote_interval_s")):
            args.quote_interval = float(params.get("quote_interval", params.get("quote_interval_s", 2.0)))
        run_config.params["config_path"] = str(config_file)
        logger.info(
            "run_config.loaded",
            run_id=run_config.run_id,
            hypothesis=run_config.hypothesis,
            fill_probability=fill_probability,
            duration_hours=args.duration_hours,
            adverse_selection_bps=params.get("adverse_selection_bps", 0),
            maker_fee_bps=params.get("maker_fee_bps", 0),
            fill_distance_decay=params.get("fill_distance_decay", False),
        )

    config_path = PROJECT_ROOT / "config" / "markets.yaml"
    markets = load_markets(config_path)

    # Filter markets if run config specifies them
    if run_config and run_config.params.get("markets"):
        market_ids = set(run_config.params["markets"])
        markets = [m for m in markets if m.market_id in market_ids]
        if not markets:
            logger.warning("No markets matched config filter, using all")
            markets = load_markets(config_path)

    logger.info(
        "paper_runner.config",
        markets=len(markets),
        duration_hours=args.duration_hours,
        quote_interval=args.quote_interval,
        fill_probability=fill_probability,
    )

    for m in markets:
        logger.info(
            "market.loaded",
            market_id=m.market_id,
            description=m.description,
            tick_size=str(m.tick_size),
        )

    # Run kill switch tests first
    if args.test_kill_switch:
        logger.info("=== KILL SWITCH TESTS ===")
        event_bus = EventBus()
        ks_results = await test_kill_switch(event_bus)

        ks_results_path = DATA_DIR / "kill_switch_test_results.json"
        with open(ks_results_path, "w") as f:
            json.dump(ks_results, f, indent=2, default=str)
        logger.info("kill_switch_tests.saved", path=str(ks_results_path))

        if not args.run_pipeline:
            return

    # Run the pipeline
    if args.run_pipeline:
        # Extract kill switch thresholds from run config params
        ks_max_dd = float(
            run_config.params.get("kill_switch_max_drawdown_pct", 25.0)
        ) if run_config else 25.0
        ks_alert = float(
            run_config.params.get("kill_switch_alert_pct", 15.0)
        ) if run_config else 15.0

        # Extract adversarial params from run config
        adv_sel_bps = int(
            run_config.params.get("adverse_selection_bps", 0)
        ) if run_config else 0
        maker_fee = int(
            run_config.params.get("maker_fee_bps", 0)
        ) if run_config else 0
        fill_decay = bool(
            run_config.params.get("fill_distance_decay", False)
        ) if run_config else False

        pipeline = PaperTradingPipeline(
            market_configs=markets,
            duration_hours=args.duration_hours,
            quote_interval_s=args.quote_interval,
            metrics_flush_interval_s=args.flush_interval,
            run_config=run_config,
            fill_probability=fill_probability,
            order_size=order_size,
            half_spread_bps=half_spread_bps,
            gamma=gamma,
            initial_balance=run_config.initial_balance if run_config else Decimal("500"),
            kill_switch_max_drawdown_pct=ks_max_dd,
            kill_switch_alert_pct=ks_alert,
            adverse_selection_bps=adv_sel_bps,
            maker_fee_bps=maker_fee,
            fill_distance_decay=fill_decay,
        )

        # Handle signals
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

        await pipeline.start()


def main():
    parser = argparse.ArgumentParser(description="Paper Trading Pipeline")
    parser.add_argument("--duration-hours", type=float, default=4.0,
                        help="Duration in hours (default: 4)")
    parser.add_argument("--quote-interval", type=float, default=2.0,
                        help="Quote cycle interval in seconds (default: 2)")
    parser.add_argument("--flush-interval", type=float, default=3600.0,
                        help="Metrics flush interval in seconds (default: 3600)")
    parser.add_argument("--test-kill-switch", action="store_true", default=True,
                        help="Run kill switch tests")
    parser.add_argument("--no-test-kill-switch", dest="test_kill_switch", action="store_false")
    parser.add_argument("--run-pipeline", action="store_true", default=True,
                        help="Run the paper trading pipeline")
    parser.add_argument("--no-run-pipeline", dest="run_pipeline", action="store_false")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to run config YAML (e.g., paper/runs/run-001.yaml)")
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
