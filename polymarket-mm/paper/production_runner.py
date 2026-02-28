"""ProductionRunner — Real CLOB micro-test pipeline.

Connects to real Polymarket CLOB for order submission while reusing
the same strategy pipeline as PaperRunner (Feature Engine → Quote Engine
→ Inventory Skew). Uses live WebSocket for market data.

Capital: $25, order size: 5 shares (minimum), kill switch at $5 loss.

Usage:
    python -m paper.production_runner --config paper/runs/prod-001.yaml

⚠️  This runner places REAL orders on Polymarket. Do NOT start without
    funding the wallet first (POL for gas + USDC.e for trading).
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
from uuid import UUID, uuid4

import structlog
import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.event_bus import EventBus
from core.kill_switch import KillSwitch, KillSwitchState
from data.rest_client import CLOBRestClient
from data.ws_client import CLOBWebSocketClient
from execution.live_execution import LiveExecution
from models.market_state import MarketState, MarketType
from models.order import Order, OrderStatus, OrderType, Side
from models.position import Position
from strategy.feature_engine import FeatureEngine, FeatureEngineConfig
from strategy.inventory_skew import InventorySkew, InventorySkewConfig
from strategy.quote_engine import QuoteEngine, QuoteEngineConfig
from strategy.spread_model import SpreadModel, SpreadModelConfig

# Reuse shared components from paper_runner
from paper.paper_runner import (
    LiveBookTracker,
    LiveStateWriter,
    MetricsCollector,
    RunConfig,
    RunHistory,
    TradeLogger,
)

logger = structlog.get_logger("production.runner")

# ── Data directory ──────────────────────────────────────────────────

DATA_DIR = PROJECT_ROOT / "paper" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Market config from REST API ─────────────────────────────────────

@dataclass
class ProdMarketConfig:
    """Market config fetched from Polymarket REST API."""
    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    description: str
    market_type: MarketType
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool
    spread_min_bps: int = 50
    max_position_size: Decimal = Decimal("100")  # conservative for $25
    enabled: bool = True


# ── Production Trade Logger (extends TradeLogger) ───────────────────

class ProductionTradeLogger(TradeLogger):
    """Extends TradeLogger with production-specific fields."""

    def __init__(self, path: Path | None = None, run_id: str = "unknown"):
        super().__init__(
            path=path or DATA_DIR / "trades_production.jsonl",
            run_id=run_id,
        )

    def log_production_trade(
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
        latency_ms: float = 0,
        gas_cost_usd: float = 0,
        rejection_reason: str = "",
        real_fee_bps: float = 0,
        exchange_order_id: str = "",
        kill_switch_state: str = "RUNNING",
        data_gap_seconds: float = 0,
        wallet_after: dict | None = None,
        spread_model_info: dict | None = None,
        inventory_skew_info: dict | None = None,
    ) -> None:
        """Log a production trade with extra fields."""
        self._trade_counter += 1
        self._cumulative_pnl += pnl_this_trade
        trade_id = f"{self._run_id}-{self._trade_counter:06d}"

        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self._run_id,
            "trade_id": trade_id,
            "is_production": True,
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
            # Production-specific fields
            "latency_ms": round(latency_ms, 1),
            "gas_cost_usd": round(gas_cost_usd, 6),
            "rejection_reason": rejection_reason,
            "real_fee_bps": round(real_fee_bps, 2),
            "exchange_order_id": exchange_order_id,
            # Strategy context
            "entry_rationale": {
                "strategy": "spread_capture",
                "spread_model": spread_model_info or {},
                "inventory_skew": inventory_skew_info or {},
            },
            "market_context": self._build_market_context(market_state),
            "feature_vector": self._build_feature_vector(features),
            "position_after": self._build_position(position),
            "kill_switch_state": kill_switch_state,
            "data_gap_seconds": round(data_gap_seconds, 2),
        }

        if wallet_after:
            record["wallet_after"] = wallet_after

        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning("prod_trade_logger.write_error", error=str(e))


# ── Production Live State Writer ────────────────────────────────────

class ProductionLiveStateWriter(LiveStateWriter):
    """Live state writer for production, writes to live_state_production.json."""

    def __init__(self, run_id: str = "unknown", hypothesis: str = "",
                 config_path: str = "", duration_target_h: float = 24.0):
        super().__init__(
            path=DATA_DIR / "live_state_production.json",
            run_id=run_id,
            hypothesis=hypothesis,
            config_path=config_path,
            duration_target_h=duration_target_h,
        )


# ── Production Wallet Tracker ───────────────────────────────────────

class ProductionWallet:
    """Tracks wallet state for production trading.

    Uses on-chain balance queries via REST client, with local
    position tracking for PnL computation.
    """

    def __init__(self, initial_balance: Decimal = Decimal("25")):
        self._initial_balance = initial_balance
        self._available_balance = initial_balance
        self._locked_balance = Decimal("0")
        self._total_fees = Decimal("0")
        self._total_gas = Decimal("0")
        self._positions: dict[str, Position] = {}

    @property
    def initial_balance(self) -> Decimal:
        return self._initial_balance

    @property
    def available_balance(self) -> Decimal:
        return self._available_balance

    @property
    def locked_balance(self) -> Decimal:
        return self._locked_balance

    @property
    def total_fees(self) -> Decimal:
        return self._total_fees

    @property
    def total_gas(self) -> Decimal:
        return self._total_gas

    def total_equity(self, mid_prices: dict[str, Decimal] | None = None) -> Decimal:
        """Compute total equity = available + locked + mark-to-market."""
        mids = mid_prices or {}
        position_value = Decimal("0")
        for market_id, pos in self._positions.items():
            mid = mids.get(market_id, Decimal("0"))
            if mid > Decimal("0"):
                position_value += pos.qty_yes * mid
                position_value += pos.qty_no * (Decimal("1") - mid)
        return self._available_balance + self._locked_balance + position_value

    def wallet_snapshot(self, mid_prices: dict[str, Decimal] | None = None) -> dict:
        equity = self.total_equity(mid_prices)
        pnl_pct = (
            float((equity - self._initial_balance) / self._initial_balance * 100)
            if self._initial_balance > Decimal("0")
            else 0.0
        )
        return {
            "initial_balance": float(self._initial_balance),
            "available_balance": float(self._available_balance),
            "locked_balance": float(self._locked_balance),
            "total_equity": float(equity),
            "pnl_pct": round(pnl_pct, 2),
            "total_fees": float(self._total_fees),
            "total_gas": float(self._total_gas),
        }

    def get_position(self, market_id: str) -> Position | None:
        return self._positions.get(market_id)

    def init_position(self, market_id: str, token_id_yes: str, token_id_no: str):
        if market_id not in self._positions:
            self._positions[market_id] = Position(
                market_id=market_id,
                token_id_yes=token_id_yes,
                token_id_no=token_id_no,
            )

    def update_position_on_fill(
        self,
        market_id: str,
        side: str,
        token_is_yes: bool,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee: Decimal = Decimal("0"),
    ) -> Decimal:
        """Update position and wallet on fill. Returns realized PnL for this fill."""
        pos = self._positions.get(market_id)
        if pos is None:
            return Decimal("0")

        pnl = Decimal("0")
        self._total_fees += fee

        if side == "BUY":
            cost = fill_price * fill_qty + fee
            self._available_balance -= cost

            if token_is_yes:
                new_qty = pos.qty_yes + fill_qty
                if new_qty > 0:
                    new_avg = (pos.avg_entry_yes * pos.qty_yes + fill_price * fill_qty) / new_qty
                else:
                    new_avg = Decimal("0")
                self._positions[market_id] = pos.model_copy(
                    update={"qty_yes": new_qty, "avg_entry_yes": new_avg}
                )
            else:
                new_qty = pos.qty_no + fill_qty
                if new_qty > 0:
                    new_avg = (pos.avg_entry_no * pos.qty_no + fill_price * fill_qty) / new_qty
                else:
                    new_avg = Decimal("0")
                self._positions[market_id] = pos.model_copy(
                    update={"qty_no": new_qty, "avg_entry_no": new_avg}
                )
        else:  # SELL
            proceeds = fill_price * fill_qty - fee
            self._available_balance += proceeds

            if token_is_yes:
                sell_qty = min(fill_qty, pos.qty_yes)
                pnl = (fill_price - pos.avg_entry_yes) * sell_qty - fee
                new_qty = pos.qty_yes - sell_qty
                self._positions[market_id] = pos.model_copy(
                    update={
                        "qty_yes": max(new_qty, Decimal("0")),
                        "realized_pnl": pos.realized_pnl + pnl,
                    }
                )
            else:
                sell_qty = min(fill_qty, pos.qty_no)
                pnl = (fill_price - pos.avg_entry_no) * sell_qty - fee
                new_qty = pos.qty_no - sell_qty
                self._positions[market_id] = pos.model_copy(
                    update={
                        "qty_no": max(new_qty, Decimal("0")),
                        "realized_pnl": pos.realized_pnl + pnl,
                    }
                )

        return pnl


# ── Production Trading Pipeline ─────────────────────────────────────

class ProductionTradingPipeline:
    """E2E production trading pipeline with real CLOB orders.

    Uses the same strategy pipeline as PaperRunner but sends
    orders to the real Polymarket CLOB via py_clob_client.
    """

    def __init__(
        self,
        market_configs: list[ProdMarketConfig],
        rest_client: CLOBRestClient,
        duration_hours: float = 24.0,
        quote_interval_s: float = 5.0,
        run_config: RunConfig | None = None,
        order_size: Decimal = Decimal("5"),
        half_spread_bps: int = 50,
        gamma: float = 0.3,
        initial_balance: Decimal = Decimal("25"),
        kill_switch_max_drawdown_pct: float = 20.0,
        kill_switch_alert_pct: float = 10.0,
    ):
        self.market_configs = market_configs
        self.rest_client = rest_client
        self.duration_hours = duration_hours
        self.quote_interval = quote_interval_s
        self.run_config = run_config
        self._run_id = run_config.run_id if run_config else f"prod-{uuid4().hex[:8]}"
        self._hypothesis = run_config.hypothesis if run_config else "production-micro-test"

        # Kill switch thresholds
        self._kill_switch_max_drawdown_pct = kill_switch_max_drawdown_pct
        self._kill_switch_alert_pct = kill_switch_alert_pct

        # Core strategy components (shared with paper)
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

        # Real execution backend
        self.execution = LiveExecution(
            rest_client=rest_client,
            default_tick_size="0.01",
            default_neg_risk=False,
        )

        # Kill switch
        self.kill_switch = KillSwitch(
            event_bus=self.event_bus,
            max_daily_loss_usd=Decimal(str(initial_balance * Decimal(str(kill_switch_max_drawdown_pct / 100)))),
            data_gap_tolerance_seconds=30,  # more tolerant in prod
        )

        # Wallet tracker
        self.wallet = ProductionWallet(initial_balance=initial_balance)

        # Trade logger (production-specific)
        self.trade_logger = ProductionTradeLogger(run_id=self._run_id)

        # Live state writer (production-specific)
        self.live_state_writer = ProductionLiveStateWriter(
            run_id=self._run_id,
            hypothesis=self._hypothesis,
            config_path=run_config.params.get("config_path", "") if run_config else "",
            duration_target_h=duration_hours,
        )

        # Metrics and state tracking
        self.run_history = RunHistory()
        self.book_tracker = LiveBookTracker()
        self.metrics = MetricsCollector()
        self.total_pnl = Decimal("0")
        self._realized_pnl = Decimal("0")
        self._unrealized_pnl = Decimal("0")

        # Open orders tracking (client_order_id -> submit_time for latency)
        self._order_submit_times: dict[UUID, float] = {}
        self._open_order_ids: set[UUID] = set()

        # Initialize positions
        for m in market_configs:
            self.wallet.init_position(m.market_id, m.token_id_yes, m.token_id_no)

        # WS client for real-time market data
        token_ids = []
        for m in market_configs:
            token_ids.append(m.token_id_yes)
            token_ids.append(m.token_id_no)

        self.ws_client = CLOBWebSocketClient(
            event_bus=self.event_bus,
            token_ids=token_ids,
        )

        # Token ID → market config mapping
        self._token_to_market: dict[str, ProdMarketConfig] = {}
        for m in market_configs:
            self._token_to_market[m.token_id_yes] = m
            self._token_to_market[m.token_id_no] = m

        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()

    async def start(self):
        """Start the production trading pipeline."""
        logger.info(
            "production.starting",
            markets=len(self.market_configs),
            duration_hours=self.duration_hours,
            initial_balance=str(self.wallet.initial_balance),
            kill_switch_drawdown_pct=self._kill_switch_max_drawdown_pct,
        )

        self._running = True

        # Connect REST client
        await self.rest_client.connect()

        # Verify balance
        try:
            balance_info = await self.rest_client.get_balance_allowance("COLLATERAL")
            on_chain_balance = Decimal(str(balance_info.get("balance", "0")))
            logger.info("production.balance_check",
                        on_chain_balance=str(on_chain_balance),
                        configured_balance=str(self.wallet.initial_balance))
            if on_chain_balance < self.wallet.initial_balance:
                logger.warning(
                    "production.insufficient_balance",
                    on_chain=str(on_chain_balance),
                    required=str(self.wallet.initial_balance),
                )
        except Exception as e:
            logger.warning("production.balance_check_failed", error=str(e))

        # Start WS client
        await self.ws_client.start()

        # Wait for initial WS data
        logger.info("production.waiting_for_initial_data", seconds=15)
        await asyncio.sleep(15)

        ws_msgs = self.ws_client.messages_received
        logger.info("production.initial_data", ws_messages=ws_msgs)

        # Start main loops
        tasks = [
            asyncio.create_task(self._ws_event_loop()),
            asyncio.create_task(self._price_change_loop()),
            asyncio.create_task(self._quote_loop()),
            asyncio.create_task(self._data_gap_monitor()),
            asyncio.create_task(self._duration_watchdog()),
            asyncio.create_task(self._live_state_loop()),
            asyncio.create_task(self._order_status_poll_loop()),
        ]

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("production.shutting_down")
            self._running = False

            # Cancel all open orders before stopping
            try:
                await self._cancel_all_orders()
            except Exception as e:
                logger.error("production.cancel_all_failed", error=str(e))

            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._final_flush()
            await self.ws_client.stop()
            await self.rest_client.disconnect()
            logger.info("production.stopped")

    async def stop(self):
        """Signal graceful shutdown."""
        self._running = False
        self._shutdown_event.set()

    # ── Event Processing Loops ──────────────────────────────────

    async def _ws_event_loop(self):
        """Subscribe to book events and update tracker."""
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
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws_event_loop.error", error=str(e))

    async def _price_change_loop(self):
        """Subscribe to price_change events."""
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
                        self.book_tracker.update_best(asset_id, best_bid, best_ask)
                        market_cfg = self._token_to_market.get(asset_id)
                        if market_cfg:
                            self.kill_switch.record_data_update(market_cfg.market_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("price_change_loop.error", error=str(e))

    async def _quote_loop(self):
        """Main quoting loop: feature engine → quote engine → real CLOB."""
        elapsed_hours = Decimal("0")
        start_time = time.monotonic()

        while self._running:
            try:
                if self.kill_switch.state == KillSwitchState.HALTED:
                    logger.warning("production.quote_loop.halted", reason="kill_switch")
                    # Cancel all orders when halted
                    await self._cancel_all_orders()
                    await asyncio.sleep(10)
                    continue

                if self.kill_switch.state == KillSwitchState.PAUSED:
                    logger.info("production.quote_loop.paused", reason="kill_switch")
                    await asyncio.sleep(5)
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
                logger.error("production.quote_loop.error", error=str(e))
                self.metrics.record_error()
                await asyncio.sleep(5)  # longer backoff in production

    async def _process_market(self, market_cfg: ProdMarketConfig, elapsed_hours: Decimal):
        """Process a single market: feature → quote → submit real order."""
        # Build market state from live WS data
        market_state = self.book_tracker.get_market_state(market_cfg)
        if market_state is None:
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
            logger.warning("production.feature_engine.error",
                           market_id=market_cfg.market_id, error=str(e))
            return

        # Get current position
        position = self.wallet.get_position(market_cfg.market_id)

        # Generate quotes
        try:
            plan = self.quote_engine.generate_quotes(
                state=market_state,
                features=features,
                position=position,
                elapsed_hours=elapsed_hours,
                available_balance=self.wallet.available_balance,
                max_position_size=market_cfg.max_position_size,
                market_min_spread_bps=Decimal(str(market_cfg.spread_min_bps)),
            )
        except Exception as e:
            logger.warning("production.quote_engine.error",
                           market_id=market_cfg.market_id, error=str(e))
            return

        if not plan.slices:
            return

        self.metrics.record_quote(market_cfg.market_id, len(plan.slices))

        # Cancel existing orders for this market before placing new ones
        await self._cancel_market_orders(market_cfg.market_id)

        # Convert to orders and submit to real CLOB
        orders = plan.to_order_intents()
        for order in orders:
            try:
                # Ensure maker-only GTC
                order = order.model_copy(update={
                    "order_type": OrderType.GTC,
                    "maker_only": True,
                })

                self.metrics.record_order(market_cfg.market_id)
                submit_time = time.monotonic()
                self._order_submit_times[order.client_order_id] = submit_time

                # Set tick_size and neg_risk per market
                self.execution._default_tick_size = str(market_cfg.tick_size)
                self.execution._default_neg_risk = market_cfg.neg_risk

                result = await self.execution.submit_order(order)

                latency_ms = (time.monotonic() - submit_time) * 1000

                if result.status == OrderStatus.REJECTED:
                    # Log rejection
                    self.trade_logger.log_production_trade(
                        market_id=market_cfg.market_id,
                        market_description=market_cfg.description,
                        side=order.side.value,
                        token="YES" if order.token_id == market_cfg.token_id_yes else "NO",
                        price=order.price,
                        size=order.size,
                        fill_qty=Decimal("0"),
                        fill_price=order.price,
                        pnl_this_trade=Decimal("0"),
                        pnl_realized=self._realized_pnl,
                        pnl_unrealized=self._unrealized_pnl,
                        position=position,
                        market_state=market_state,
                        features=features,
                        latency_ms=latency_ms,
                        rejection_reason="ORDER_REJECTED",
                        kill_switch_state=self.kill_switch.state.value,
                    )
                    logger.warning(
                        "production.order.rejected",
                        market_id=market_cfg.market_id,
                        side=order.side.value,
                        price=str(order.price),
                        latency_ms=round(latency_ms, 1),
                    )
                else:
                    self._open_order_ids.add(order.client_order_id)
                    logger.info(
                        "production.order.submitted",
                        market_id=market_cfg.market_id,
                        side=order.side.value,
                        price=str(order.price),
                        size=str(order.size),
                        latency_ms=round(latency_ms, 1),
                    )

            except Exception as e:
                logger.warning(
                    "production.order.submit_error",
                    market_id=market_cfg.market_id,
                    error=str(e),
                )

    async def _cancel_all_orders(self):
        """Cancel all open orders on the CLOB."""
        try:
            await self.rest_client.cancel_all_orders()
            self._open_order_ids.clear()
            logger.info("production.orders.cancelled_all")
        except Exception as e:
            logger.warning("production.cancel_all.error", error=str(e))

    async def _cancel_market_orders(self, market_id: str):
        """Cancel open orders for a specific market."""
        try:
            # Get open orders from exchange
            open_orders = await self.execution.get_open_orders()
            for oo in open_orders:
                if oo.market_id == market_id:
                    await self.execution.cancel_order(oo.client_order_id)
        except Exception as e:
            logger.warning("production.cancel_market.error",
                           market_id=market_id, error=str(e))

    async def _order_status_poll_loop(self):
        """Poll for filled orders and update positions/PnL."""
        while self._running:
            try:
                await asyncio.sleep(3)  # Poll every 3 seconds

                if not self._open_order_ids:
                    continue

                # Check trades via REST API
                for market_cfg in self.market_configs:
                    try:
                        trades = await self.rest_client.get_trades(
                            market=market_cfg.condition_id,
                        )
                        for trade in trades:
                            await self._process_trade(trade, market_cfg)
                    except Exception as e:
                        logger.debug("production.trade_poll.error",
                                     market_id=market_cfg.market_id, error=str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("production.order_status_poll.error", error=str(e))

    async def _process_trade(self, trade: dict, market_cfg: ProdMarketConfig):
        """Process a trade from the REST API."""
        trade_id = trade.get("id", "")
        # Skip already-processed trades (simple dedup by ID)
        if not hasattr(self, "_processed_trades"):
            self._processed_trades: set[str] = set()
        if trade_id in self._processed_trades:
            return
        self._processed_trades.add(trade_id)

        side = trade.get("side", "").upper()
        token_id = trade.get("asset_id", trade.get("token_id", ""))
        fill_price = Decimal(str(trade.get("price", "0")))
        fill_qty = Decimal(str(trade.get("size", "0")))
        fee_rate = Decimal(str(trade.get("fee_rate_bps", "30")))
        fee = fill_price * fill_qty * fee_rate / Decimal("10000")

        if fill_qty <= 0:
            return

        is_yes = token_id == market_cfg.token_id_yes
        token_label = "YES" if is_yes else "NO"

        # Compute latency
        latency_ms = 0.0
        order_id_str = trade.get("order_id", "")
        # try to find submit time by matching
        # (in production, we track by exchange order_id)

        # Update position
        pnl = self.wallet.update_position_on_fill(
            market_id=market_cfg.market_id,
            side=side,
            token_is_yes=is_yes,
            fill_price=fill_price,
            fill_qty=fill_qty,
            fee=fee,
        )

        self.total_pnl += pnl
        self._realized_pnl += pnl
        self.metrics.record_fill(market_cfg.market_id, float(fee_rate))

        # Market state for logging
        ms = self.book_tracker.get_market_state(market_cfg)

        # Data gap
        data_gap = self.book_tracker.last_update_age(market_cfg.token_id_yes)
        if data_gap > 1e6:
            data_gap = 0

        # Log the fill
        self.trade_logger.log_production_trade(
            market_id=market_cfg.market_id,
            market_description=market_cfg.description,
            side=side,
            token=token_label,
            price=fill_price,
            size=fill_qty,
            fill_qty=fill_qty,
            fill_price=fill_price,
            pnl_this_trade=pnl,
            pnl_realized=self._realized_pnl,
            pnl_unrealized=self._unrealized_pnl,
            position=self.wallet.get_position(market_cfg.market_id),
            market_state=ms,
            features=None,
            latency_ms=latency_ms,
            real_fee_bps=float(fee_rate),
            exchange_order_id=order_id_str,
            kill_switch_state=self.kill_switch.state.value,
            data_gap_seconds=data_gap,
            wallet_after=self.wallet.wallet_snapshot(self._get_mid_prices()),
        )

        logger.info(
            "production.fill",
            market_id=market_cfg.market_id,
            side=side,
            token=token_label,
            price=str(fill_price),
            qty=str(fill_qty),
            fee=str(fee),
            pnl=str(pnl),
            total_pnl=str(self.total_pnl),
        )

    def _get_mid_prices(self) -> dict[str, Decimal]:
        """Get current mid prices from book tracker."""
        mids = {}
        for mc in self.market_configs:
            ms = self.book_tracker.get_market_state(mc)
            if ms and ms.mid_price > 0:
                mids[mc.market_id] = ms.mid_price
        return mids

    async def _data_gap_monitor(self):
        """Monitor data gaps and trigger kill switch."""
        while self._running:
            try:
                await asyncio.sleep(10)  # check every 10s (less aggressive in prod)

                for market_cfg in self.market_configs:
                    yes_age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                    no_age = self.book_tracker.last_update_age(market_cfg.token_id_no)
                    min_age = min(yes_age, no_age)

                    if min_age > 30 and min_age < float("inf"):
                        await self.kill_switch.trigger_data_gap(
                            market_id=market_cfg.market_id,
                            gap_seconds=min_age,
                        )
                        # Cancel orders for this market immediately
                        await self._cancel_market_orders(market_cfg.market_id)

                self.kill_switch.record_heartbeat()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("production.data_gap_monitor.error", error=str(e))

    async def _duration_watchdog(self):
        """Stop after duration_hours."""
        total_seconds = self.duration_hours * 3600
        try:
            await asyncio.sleep(total_seconds)
            logger.info("production.duration.reached", hours=self.duration_hours)
            await self.stop()
        except asyncio.CancelledError:
            pass

    async def _live_state_loop(self):
        """Write live_state_production.json every 10 seconds."""
        while self._running:
            try:
                await asyncio.sleep(10)
                if not self._running:
                    break

                # Compute PnL
                realized = Decimal("0")
                unrealized = Decimal("0")
                for mc in self.market_configs:
                    pos = self.wallet.get_position(mc.market_id)
                    if pos:
                        realized += pos.realized_pnl
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
                    positions={mc.market_id: self.wallet.get_position(mc.market_id) or Position(
                        market_id=mc.market_id,
                        token_id_yes=mc.token_id_yes,
                        token_id_no=mc.token_id_no,
                    ) for mc in self.market_configs},
                    metrics=self.metrics,
                    market_configs=self.market_configs,
                    book_tracker=self.book_tracker,
                    kill_switch=self.kill_switch,
                    ws_connected=self.ws_client.connected if hasattr(self.ws_client, 'connected') else True,
                    wallet=self.wallet.wallet_snapshot(self._get_mid_prices()),
                )

                # Kill switch check based on wallet equity
                mids = self._get_mid_prices()
                equity = self.wallet.total_equity(mids)
                initial = self.wallet.initial_balance

                if initial > Decimal("0"):
                    drawdown_pct = float((initial - equity) / initial * 100)

                    if drawdown_pct >= self._kill_switch_max_drawdown_pct:
                        logger.critical(
                            "production.kill_switch.drawdown",
                            equity=str(equity),
                            initial=str(initial),
                            drawdown_pct=round(drawdown_pct, 2),
                        )
                        loss = initial - equity
                        await self.kill_switch.trigger_max_drawdown(loss)
                        await self._cancel_all_orders()
                    elif drawdown_pct >= self._kill_switch_alert_pct:
                        logger.warning(
                            "production.drawdown_alert",
                            equity=str(equity),
                            drawdown_pct=round(drawdown_pct, 2),
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("production.live_state_loop.error", error=str(e))

    async def _final_flush(self):
        """Final metrics flush and save."""
        self.metrics.flush_hour(
            {mc.market_id: self.wallet.get_position(mc.market_id) or Position(
                market_id=mc.market_id,
                token_id_yes=mc.token_id_yes,
                token_id_no=mc.token_id_no,
            ) for mc in self.market_configs},
            self.total_pnl,
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.metrics.save(DATA_DIR / f"metrics_production_{timestamp}.json")

        # Write final live state
        self.live_state_writer.write(
            status="FINISHED",
            total_pnl=self.total_pnl,
            realized_pnl=self._realized_pnl,
            unrealized_pnl=self._unrealized_pnl,
            positions={mc.market_id: self.wallet.get_position(mc.market_id) or Position(
                market_id=mc.market_id,
                token_id_yes=mc.token_id_yes,
                token_id_no=mc.token_id_no,
            ) for mc in self.market_configs},
            metrics=self.metrics,
            market_configs=self.market_configs,
            book_tracker=self.book_tracker,
            kill_switch=self.kill_switch,
            wallet=self.wallet.wallet_snapshot(self._get_mid_prices()),
        )

        # Write run history
        uptime_h = (time.monotonic() - self.live_state_writer._start_time) / 3600
        fill_rate = (self.metrics.total_fills / self.metrics.total_orders * 100) if self.metrics.total_orders > 0 else 0
        pnl_per_h = float(self.total_pnl) / uptime_h if uptime_h > 0.01 else 0

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
                max_drawdown=0,
            )
        except Exception as e:
            logger.warning("production.run_history.error", error=str(e))

        logger.info(
            "production.final_metrics",
            total_pnl=str(self.total_pnl),
            total_orders=self.metrics.total_orders,
            total_fills=self.metrics.total_fills,
            uptime_h=round(uptime_h, 2),
        )


# ── Market Auto-Select ──────────────────────────────────────────────

async def auto_select_markets(
    rest_client: CLOBRestClient,
    max_markets: int = 1,
) -> list[ProdMarketConfig]:
    """Auto-select markets from Polymarket API.

    Criteria:
    - Active and not closed
    - Has valid token IDs
    - Price near 0.50 (maximizes entropy)
    """
    logger.info("production.auto_selecting_markets")
    raw_markets = await rest_client.get_active_markets(max_pages=3)

    candidates = []
    for m in raw_markets:
        if not m.get("active") or m.get("closed"):
            continue
        if not m.get("token_id_yes") or not m.get("token_id_no"):
            continue
        candidates.append(m)

    # Sort by question length (shorter = simpler markets, better for testing)
    candidates.sort(key=lambda x: len(x.get("question", "")))

    selected = []
    for m in candidates[:max_markets]:
        selected.append(ProdMarketConfig(
            market_id=m["condition_id"],
            condition_id=m["condition_id"],
            token_id_yes=m["token_id_yes"],
            token_id_no=m["token_id_no"],
            description=m.get("question", m["condition_id"])[:80],
            market_type=MarketType.OTHER,
            tick_size=m.get("tick_size", Decimal("0.01")),
            min_order_size=m.get("min_order_size", Decimal("5")),
            neg_risk=m.get("neg_risk", False),
        ))
        logger.info("production.market_selected",
                     market_id=m["condition_id"],
                     question=m.get("question", "")[:60])

    return selected


# ── Main ────────────────────────────────────────────────────────────

async def async_main(args):
    """Main async entrypoint."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Load run config
    run_config = None
    order_size = Decimal("5")
    half_spread_bps = 50
    gamma = 0.3
    initial_balance = Decimal("25")
    quote_interval = 5.0
    kill_switch_max_dd = 20.0
    kill_switch_alert = 10.0
    duration_hours = 24.0

    if args.config:
        config_file = Path(args.config)
        if not config_file.is_absolute():
            config_file = PROJECT_ROOT / config_file
        run_config = RunConfig.from_yaml(config_file)
        params = run_config.params

        duration_hours = run_config.duration_hours
        initial_balance = run_config.initial_balance
        order_size = Decimal(str(params.get("default_order_size", "5")))
        half_spread_bps = int(params.get("default_half_spread_bps", 50))
        gamma = float(params.get("gamma_risk_aversion", 0.3))
        quote_interval = float(params.get("quote_interval_s", 5.0))
        kill_switch_max_dd = float(params.get("kill_switch_max_drawdown_pct", 20.0))
        kill_switch_alert = float(params.get("kill_switch_alert_pct", 10.0))
        run_config.params["config_path"] = str(config_file)

        logger.info(
            "production.config_loaded",
            run_id=run_config.run_id,
            initial_balance=str(initial_balance),
            order_size=str(order_size),
            quote_interval=quote_interval,
        )

    # Initialize REST client with env vars
    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    api_secret = os.environ.get("POLYMARKET_API_SECRET", "")
    api_passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    private_key = os.environ.get("POLYGON_PRIVATE_KEY", "")

    if not all([api_key, api_secret, api_passphrase, private_key]):
        logger.error("production.missing_credentials",
                      has_key=bool(api_key),
                      has_secret=bool(api_secret),
                      has_passphrase=bool(api_passphrase),
                      has_private_key=bool(private_key))
        print("ERROR: Missing required environment variables:")
        print("  POLYMARKET_API_KEY, POLYMARKET_API_SECRET,")
        print("  POLYMARKET_PASSPHRASE, POLYGON_PRIVATE_KEY")
        sys.exit(1)

    rest_client = CLOBRestClient(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        rate_limit_rps=5.0,  # conservative rate limit
    )

    # Connect and select markets
    await rest_client.connect()

    # Get markets from config or auto-select
    market_ids = []
    if run_config and run_config.params.get("markets"):
        market_ids = run_config.params["markets"]

    if market_ids:
        # Fetch specific markets
        markets = []
        for mid in market_ids:
            try:
                info = await rest_client.get_market_info(mid)
                markets.append(ProdMarketConfig(
                    market_id=info["condition_id"],
                    condition_id=info["condition_id"],
                    token_id_yes=info["token_id_yes"],
                    token_id_no=info["token_id_no"],
                    description=info.get("question", info["condition_id"])[:80],
                    market_type=MarketType.OTHER,
                    tick_size=info.get("tick_size", Decimal("0.01")),
                    min_order_size=info.get("min_order_size", Decimal("5")),
                    neg_risk=info.get("neg_risk", False),
                ))
            except Exception as e:
                logger.warning("production.market_fetch_failed", market_id=mid, error=str(e))
    else:
        markets = await auto_select_markets(rest_client, max_markets=1)

    if not markets:
        logger.error("production.no_markets_found")
        sys.exit(1)

    logger.info("production.markets_ready", count=len(markets))
    for m in markets:
        logger.info("production.market", market_id=m.market_id, description=m.description)

    # Create and run pipeline
    pipeline = ProductionTradingPipeline(
        market_configs=markets,
        rest_client=rest_client,
        duration_hours=duration_hours,
        quote_interval_s=quote_interval,
        run_config=run_config,
        order_size=order_size,
        half_spread_bps=half_spread_bps,
        gamma=gamma,
        initial_balance=initial_balance,
        kill_switch_max_drawdown_pct=kill_switch_max_dd,
        kill_switch_alert_pct=kill_switch_alert,
    )

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()


def main():
    parser = argparse.ArgumentParser(description="Production Trading Pipeline (Micro Test)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to run config YAML (e.g., paper/runs/prod-001.yaml)")
    args = parser.parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
