"""runner.pipeline — UnifiedTradingPipeline for paper and live modes.

Replaces both PaperTradingPipeline and ProductionTradingPipeline with
a single pipeline that uses dependency injection (VenueAdapter, WalletAdapter)
to handle mode-specific behavior.

Core loops (both modes):
- ws_event_loop: subscribe to book events
- price_change_loop: subscribe to price_change events
- quote_loop: feature engine → quote engine → venue adapter
- live_state_loop: write dashboard state JSON
- data_gap_monitor: monitor data freshness
- duration_watchdog: stop after configured duration

Mode-specific loops:
- fill_event_loop (paper only): drain fills from EventBus
- order_status_poll (live only): poll REST for fills
- reconcile_loop (live only): on-chain reconciliation
"""

from __future__ import annotations

import asyncio
import json
import math
import resource
import signal
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from core.event_bus import EventBus
from core.kill_switch import KillSwitch, KillSwitchState
from data.ws_client import CLOBWebSocketClient
from models.market_state import MarketState
from models.order import Order, OrderStatus, OrderType, Side
from models.position import Position
from paper.paper_runner import (
    LiveBookTracker,
    LiveStateWriter,
    MetricsCollector,
    RunConfig,
    RunHistory,
)
from runner.capital_recovery import CapitalRecovery
from runner.config import (
    RotationConfig,
    UnifiedMarketConfig,
    auto_select_markets,
    load_rotation_blacklist,
    save_rotation_blacklist,
)
from runner.decision_envelope import DecisionEnvelope
from runner.market_health import MarketHealthMonitor, MarketHealthStatus
from runner.trade_logger import UnifiedTradeLogger
from runner.venue_adapter import VenueAdapter
from runner.wallet_adapter import WalletAdapter
from strategy.feature_engine import FeatureEngine, FeatureEngineConfig
from strategy.inventory_skew import InventorySkew, InventorySkewConfig
from strategy.quote_engine import QuoteEngine, QuoteEngineConfig
from strategy.spread_model import SpreadModel, SpreadModelConfig

logger = structlog.get_logger("runner.pipeline")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "paper" / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)


class UnifiedTradingPipeline:
    """Unified trading pipeline for paper and live modes.

    Uses VenueAdapter and WalletAdapter for mode-specific operations.
    The pipeline logic (quote generation, kill switch, metrics) is shared.
    """

    def __init__(
        self,
        market_configs: list[UnifiedMarketConfig],
        venue: VenueAdapter,
        wallet: WalletAdapter,
        event_bus: EventBus,
        *,
        duration_hours: float = 4.0,
        quote_interval_s: float = 2.0,
        metrics_flush_interval_s: float = 3600.0,
        run_config: RunConfig | None = None,
        order_size: Decimal = Decimal("50"),
        half_spread_bps: int = 50,
        gamma: float = 0.3,
        kill_switch_max_drawdown_pct: float = 25.0,
        kill_switch_alert_pct: float = 10.0,
        # Balance-aware quoting (shared pipeline, config-driven)
        balance_aware_quoting: bool = False,
        min_balance_to_quote: Decimal = Decimal("5"),
        position_recycling: bool = False,
        recycle_profit_threshold: Decimal = Decimal("0.02"),
        # Market rotation + capital recovery
        rotation_config: RotationConfig | None = None,
        # Live-mode specific
        rest_client: Any = None,
        ws_client: CLOBWebSocketClient | None = None,
        supabase_logger: Any = None,
        decision_envelope: DecisionEnvelope | None = None,
        transport_selection: Any = None,
        latency_recorder: Any = None,
    ) -> None:
        self.market_configs = market_configs
        self.venue = venue
        self.wallet = wallet
        self.event_bus = event_bus
        self.mode = venue.mode  # "paper" or "live"

        self.duration_hours = duration_hours
        self.quote_interval = quote_interval_s
        self.metrics_flush_interval = metrics_flush_interval_s
        self.run_config = run_config
        self._run_id = run_config.run_id if run_config else f"run-{uuid4().hex[:8]}"
        self._hypothesis = run_config.hypothesis if run_config else ""

        # Kill switch thresholds (configurable per mode)
        self._kill_switch_max_drawdown_pct = kill_switch_max_drawdown_pct
        self._kill_switch_alert_pct = kill_switch_alert_pct

        # Strategy components
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
                balance_aware_quoting=balance_aware_quoting,
                min_balance_to_quote=min_balance_to_quote,
                position_recycling=position_recycling,
                recycle_profit_threshold=recycle_profit_threshold,
                max_balance_fraction_per_order=(
                    Decimal("0.15") if self.mode == "live" else Decimal("1")
                ),
            ),
        )

        # Kill switch
        self.kill_switch = KillSwitch(
            event_bus=self.event_bus,
            max_daily_loss_usd=Decimal("50"),
            data_gap_tolerance_seconds=30 if self.mode == "live" else 15,
        )

        # Trade logger (mode-aware)
        self.trade_logger = UnifiedTradeLogger(
            mode=self.mode,
            run_id=self._run_id,
        )

        # Live state writer
        live_state_path = (
            DATA_DIR / "live_state_production.json"
            if self.mode == "live"
            else DATA_DIR / "live_state.json"
        )
        self.live_state_writer = LiveStateWriter(
            path=live_state_path,
            run_id=self._run_id,
            hypothesis=self._hypothesis,
            config_path=run_config.params.get("config_path", "") if run_config else "",
            duration_target_h=duration_hours,
        )
        self.run_history = RunHistory()

        # Supabase logger (live mode only, fire-and-forget)
        self.supabase_logger = supabase_logger

        # State tracking
        self.book_tracker = LiveBookTracker()
        self.metrics = MetricsCollector()
        self.total_pnl = Decimal("0")
        self._realized_pnl = Decimal("0")
        self._unrealized_pnl = Decimal("0")

        # WS client
        if ws_client is None:
            token_ids = []
            for m in market_configs:
                token_ids.append(m.token_id_yes)
                token_ids.append(m.token_id_no)
            ws_client = CLOBWebSocketClient(
                event_bus=self.event_bus,
                token_ids=token_ids,
            )
        self.ws_client = ws_client

        # REST client (for live mode reconciliation)
        self.rest_client = rest_client
        self.decision_envelope = decision_envelope
        self.transport_selection = transport_selection
        self.latency_recorder = latency_recorder
        self._decision_id = decision_envelope.decision_id if decision_envelope else ""
        self._last_quote_refresh: dict[str, float] = {}
        self._directional_allocated = Decimal("0")
        self._max_directional_capital = (
            decision_envelope.capital_policy.directional_capital_usdc
            if decision_envelope
            else Decimal("0")
        )
        self._allow_directional_live = bool(
            decision_envelope
            and decision_envelope.mode_allocations.directional_enabled
            and decision_envelope.risk_limits.allow_directional_live
            and (
                not decision_envelope.risk_limits.directional_live_requires_direct
                or (transport_selection is not None and transport_selection.directional_live_ok)
            )
        )
        self._directional_enabled_for_mode = (
            self.mode == "paper"
            or self._allow_directional_live
        )

        # Token → market mapping
        self._token_to_market: dict[str, UnifiedMarketConfig] = {}
        for m in market_configs:
            self._token_to_market[m.token_id_yes] = m
            self._token_to_market[m.token_id_no] = m

        # Positions mirror (for paper mode where venue tracks positions)
        self._positions: dict[str, Position] = {}
        for m in market_configs:
            wallet.init_position(m.market_id, m.token_id_yes, m.token_id_no)
            pos = wallet.get_position(m.market_id)
            if pos:
                self._positions[m.market_id] = pos
            else:
                self._positions[m.market_id] = Position(
                    market_id=m.market_id,
                    token_id_yes=m.token_id_yes,
                    token_id_no=m.token_id_no,
                )

        # Control
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._shutdown_reason: str = ""

        # ── Market rotation + capital recovery ──
        self._rotation_config = rotation_config or RotationConfig()
        self._health_monitor: MarketHealthMonitor | None = None
        self._capital_recovery: CapitalRecovery | None = None
        self._rotation_blacklist: set[str] = set()
        self._last_rotation_time: float = 0.0

        if self._rotation_config.market_rotation:
            self._health_monitor = MarketHealthMonitor(self._rotation_config)
            self._rotation_blacklist = load_rotation_blacklist(
                self._rotation_config.blacklist_path
            )
            logger.info(
                "pipeline.market_rotation_enabled",
                cooldown_hours=self._rotation_config.rotation_cooldown_hours,
                min_health_score=self._rotation_config.min_market_health_score,
                blacklist_size=len(self._rotation_blacklist),
            )
            if self.decision_envelope is not None:
                logger.info(
                    "pipeline.market_rotation_disabled_by_decision_envelope",
                    decision_id=self._decision_id,
                )
                self._health_monitor = None

        if self._rotation_config.capital_recovery:
            self._capital_recovery = CapitalRecovery(self._rotation_config)
            logger.info(
                "pipeline.capital_recovery_enabled",
                min_balance=str(self._rotation_config.min_balance_for_recovery),
            )

    async def start(self) -> None:
        """Start the unified trading pipeline."""
        logger.info(
            "pipeline.starting",
            mode=self.mode,
            markets=len(self.market_configs),
            duration_hours=self.duration_hours,
        )

        self._running = True

        # Connect venue
        await self.venue.connect()

        # Start Supabase logger if configured
        if self.supabase_logger:
            await self.supabase_logger.start()
            self.supabase_logger.log_run_start(config={
                "mode": self.mode,
                "markets": [m.market_id for m in self.market_configs],
                "duration_hours": self.duration_hours,
                "quote_interval_s": self.quote_interval,
                "decision_id": self._decision_id,
                "transport": self._selected_transport(),
            })

        # Start WS client
        await self.ws_client.start()

        # Wait for initial WS data
        wait_secs = 15 if self.mode == "live" else 10
        logger.info("pipeline.waiting_for_initial_data", seconds=wait_secs)
        await asyncio.sleep(wait_secs)

        ws_msgs = self.ws_client.messages_received
        logger.info("pipeline.initial_data", ws_messages=ws_msgs)

        # Build task list (mode-dependent)
        tasks = [
            asyncio.create_task(self._ws_event_loop()),
            asyncio.create_task(self._price_change_loop()),
            asyncio.create_task(self._quote_loop()),
            asyncio.create_task(self._data_gap_monitor()),
            asyncio.create_task(self._duration_watchdog()),
            asyncio.create_task(self._live_state_loop()),
        ]

        if self.mode == "paper":
            tasks.append(asyncio.create_task(self._fill_event_loop()))
            tasks.append(asyncio.create_task(self._position_rebalance_loop()))
            tasks.append(asyncio.create_task(self._metrics_flush_loop()))
        else:
            tasks.append(asyncio.create_task(self._order_status_poll_loop()))
            tasks.append(asyncio.create_task(self._reconcile_loop()))
            tasks.append(asyncio.create_task(self._execution_guard_loop()))
            if self.latency_recorder is not None:
                tasks.append(asyncio.create_task(self._transport_monitor_loop()))

        # Market rotation + capital recovery loops (both modes, config-gated)
        if self._health_monitor is not None:
            tasks.append(asyncio.create_task(self._market_health_loop()))
        if self._capital_recovery is not None:
            tasks.append(asyncio.create_task(self._capital_recovery_loop()))

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("pipeline.shutting_down", mode=self.mode)
            self._running = False

            # Live mode: unwind positions before stopping
            if self.mode == "live":
                await self._live_unwind()

            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._final_flush()

            # Supabase logging
            if self.supabase_logger:
                self.supabase_logger.log_run_end(
                    total_pnl=self.total_pnl,
                    total_fills=self.metrics.total_fills,
                    total_orders=self.metrics.total_orders,
                    status="completed",
                )
                await self.supabase_logger.stop()

            await self.ws_client.stop()
            await self.venue.disconnect()
            logger.info("pipeline.stopped", mode=self.mode)

    async def stop(self, reason: str = "graceful_shutdown") -> None:
        """Signal graceful shutdown."""
        self._running = False
        self._shutdown_reason = reason
        self._shutdown_event.set()

    # ── Event Processing Loops ──────────────────────────────────

    async def _ws_event_loop(self) -> None:
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
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("ws_event_loop.error", error=str(e))

    async def _price_change_loop(self) -> None:
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
                        self.book_tracker.update_best(asset_id, best_bid, best_ask)
                        market_cfg = self._token_to_market.get(asset_id)
                        if market_cfg:
                            self.kill_switch.record_data_update(market_cfg.market_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("price_change_loop.error", error=str(e))

    async def _fill_event_loop(self) -> None:
        """Paper mode only: subscribe to fill events from EventBus."""
        from runner.paper_venue_adapter import PaperVenueAdapter
        assert isinstance(self.venue, PaperVenueAdapter)

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

                # Update position from venue
                pos = self.venue.venue.get_position(market_id)
                if pos:
                    self._positions[market_id] = pos
                self.total_pnl = self.venue.venue.total_pnl

                # Compute spread captured
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
                if self._health_monitor is not None:
                    self._health_monitor.record_fill(market_id)

                # Compute PnL for this trade
                pos = self._positions.get(market_id)
                pnl_this = Decimal("0")
                if side == "SELL" and pos:
                    if market_cfg and token_id == market_cfg.token_id_yes:
                        pnl_this = (fill_price - pos.avg_entry_yes) * fill_qty
                    elif market_cfg and token_id == market_cfg.token_id_no:
                        pnl_this = (fill_price - pos.avg_entry_no) * fill_qty

                realized = pos.realized_pnl if pos else Decimal("0")
                token_label = "YES"
                if market_cfg and token_id == market_cfg.token_id_no:
                    token_label = "NO"

                data_gap = 0.0
                if market_cfg:
                    age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                    data_gap = age if age < 1e6 else 0

                # Log trade
                self.trade_logger.log_trade(
                    decision_id=self._decision_id,
                    execution_mode=getattr(market_cfg, "execution_mode", "rewards_farming") if market_cfg else "rewards_farming",
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
                    pnl_unrealized=Decimal("0"),
                    position=pos,
                    market_state=ms,
                    features=None,
                    kill_switch_state=self.kill_switch.state.value,
                    data_gap_seconds=data_gap,
                    transport=self._selected_transport(),
                    transport_ttfb_ms=self._selected_transport_ttfb_ms(),
                    latency_bucket=self._latency_bucket(0.0),
                    reward_estimate_usd=self._reward_estimate_for_market(market_cfg),
                    wallet_after={
                        "available": float(self.wallet.available_balance),
                        "locked": float(self.wallet.locked_balance),
                        "equity": float(self.wallet.total_equity()),
                        "fee": float(fill_fee),
                        "total_fees": float(self.wallet.total_fees),
                    },
                )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("fill_event_loop.error", error=str(e))

    async def _order_status_poll_loop(self) -> None:
        """Live mode only: poll REST for fills and update positions/PnL."""
        while self._running:
            try:
                await asyncio.sleep(3)

                fills = await self.venue.process_fills()
                for fill in fills:
                    market_id = fill["market_id"]
                    token_id = fill["token_id"]
                    side = fill["side"]
                    fill_price = fill["fill_price"]
                    fill_qty = fill["fill_qty"]
                    fee = fill["fee"]

                    market_cfg = next(
                        (m for m in self.market_configs if m.market_id == market_id),
                        None,
                    )
                    is_yes = fill.get("token_is_yes", True)
                    token_label = "YES" if is_yes else "NO"

                    # Update wallet position
                    pnl = self.wallet.update_position_on_fill(
                        market_id=market_id,
                        side=side,
                        token_is_yes=is_yes,
                        fill_price=fill_price,
                        fill_qty=fill_qty,
                        fee=fee,
                    )

                    self.total_pnl += pnl
                    self._realized_pnl += pnl
                    self.metrics.record_fill(market_id, fill.get("fee_rate_bps", 0))
                    if self._health_monitor is not None:
                        self._health_monitor.record_fill(market_id)

                    # Update positions mirror
                    pos = self.wallet.get_position(market_id)
                    if pos:
                        self._positions[market_id] = pos

                    ms = self.book_tracker.get_market_state(market_cfg) if market_cfg else None
                    data_gap = 0.0
                    if market_cfg:
                        age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                        data_gap = age if age < 1e6 else 0

                    # Log fill
                    self.trade_logger.log_trade(
                        decision_id=self._decision_id,
                        execution_mode=market_cfg.execution_mode if market_cfg else "rewards_farming",
                        market_id=market_id,
                        market_description=market_cfg.description if market_cfg else market_id,
                        side=side,
                        token=token_label,
                        price=fill_price,
                        size=fill_qty,
                        fill_qty=fill_qty,
                        fill_price=fill_price,
                        pnl_this_trade=pnl,
                        pnl_realized=self._realized_pnl,
                        pnl_unrealized=self._unrealized_pnl,
                        position=pos,
                        market_state=ms,
                        features=None,
                        latency_ms=0.0,
                        real_fee_bps=fill.get("fee_rate_bps", 0),
                        exchange_order_id=fill.get("exchange_order_id", ""),
                        kill_switch_state=self.kill_switch.state.value,
                        data_gap_seconds=data_gap,
                        transport=self._selected_transport(),
                        transport_ttfb_ms=self._selected_transport_ttfb_ms(),
                        latency_bucket=self._latency_bucket(0.0),
                        reward_estimate_usd=self._reward_estimate_for_market(market_cfg),
                        wallet_after=self.wallet.wallet_snapshot(self._get_mid_prices()),
                    )

                    # Supabase logging
                    if self.supabase_logger:
                        self.supabase_logger.log_fill(
                            market_id=market_id,
                            trade_id=fill.get("fill_id", ""),
                            order_id=fill.get("exchange_order_id", ""),
                            side=side,
                            token_side=token_label,
                            price=fill_price,
                            size=fill_qty,
                            fee=fee,
                        )

                    logger.info(
                        "fill.processed",
                        market_id=market_id,
                        side=side,
                        token=token_label,
                        price=str(fill_price),
                        qty=str(fill_qty),
                        pnl=str(pnl),
                        total_pnl=str(self.total_pnl),
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("order_status_poll.error", error=str(e))

    async def _quote_loop(self) -> None:
        """Main quoting loop: feature engine → quote engine → venue."""
        elapsed_hours = Decimal("0")
        start_time = time.monotonic()

        while self._running:
            try:
                if self.kill_switch.state == KillSwitchState.HALTED:
                    logger.warning("quote_loop.halted", reason="kill_switch")
                    if self.mode == "live":
                        await self.venue.cancel_all_orders()
                    pause_time = 10 if self.mode == "live" else 5
                    await asyncio.sleep(pause_time)
                    continue

                if self.kill_switch.state == KillSwitchState.PAUSED:
                    logger.info("quote_loop.paused", reason="kill_switch")
                    pause_time = 5 if self.mode == "live" else 1
                    await asyncio.sleep(pause_time)
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
                backoff = 5 if self.mode == "live" else 1
                await asyncio.sleep(backoff)

    async def _process_market(self, market_cfg: UnifiedMarketConfig, elapsed_hours: Decimal) -> None:
        """Process a single market according to its execution mode."""
        if market_cfg.disable_reason:
            return
        if market_cfg.execution_mode == "event_driven":
            if not self._directional_enabled_for_mode:
                return
            await self._process_directional_market(market_cfg)
            return
        await self._process_rewards_market(market_cfg, elapsed_hours)

    async def _process_rewards_market(
        self,
        market_cfg: UnifiedMarketConfig,
        elapsed_hours: Decimal,
    ) -> None:
        """Rewards-first market making path driven by Quant parameters."""
        market_state = self.book_tracker.get_market_state(market_cfg)
        if market_state is None:
            return

        if market_state.mid_price <= 0:
            return

        # Feed health monitor with spread data
        if self._health_monitor is not None:
            if market_state.yes_bid > 0 and market_state.yes_ask > 0 and market_state.mid_price > 0:
                computed_spread = float(
                    (market_state.yes_ask - market_state.yes_bid)
                    / market_state.mid_price
                    * 10000
                )
                self._health_monitor.record_spread(market_cfg.market_id, computed_spread)

        yes_book = self.book_tracker.get_book(market_cfg.token_id_yes)

        # Compute features
        try:
            features = await self.feature_engine.compute(
                market_state=market_state,
                orderbook=yes_book,
            )
        except Exception as e:
            logger.warning("feature_engine.error",
                           market_id=market_cfg.market_id, error=str(e))
            return

        # Get current position
        position = self._positions.get(market_cfg.market_id)
        if position is None:
            position = self.wallet.get_position(market_cfg.market_id)

        if self._should_hold_quotes(market_cfg):
            return

        original_order_size = self.quote_engine.config.default_order_size
        original_min_half_spread = self.quote_engine.spread_model.config.min_half_spread_bps
        if market_cfg.order_size_override is not None:
            self.quote_engine.config.default_order_size = market_cfg.order_size_override
        if market_cfg.half_spread_bps_override is not None:
            self.quote_engine.spread_model.config.min_half_spread_bps = Decimal(
                str(market_cfg.half_spread_bps_override)
            )

        try:
            plan = self.quote_engine.generate_quotes(
                state=market_state,
                features=features,
                position=position,
                elapsed_hours=elapsed_hours,
                available_balance=self.wallet.available_balance,
                max_position_size=market_cfg.max_position_size,
                market_min_spread_bps=Decimal(
                    str(
                        market_cfg.half_spread_bps_override * 2
                        if market_cfg.half_spread_bps_override is not None
                        else market_cfg.spread_min_bps
                    )
                ),
            )
        except Exception as e:
            logger.warning("quote_engine.error",
                           market_id=market_cfg.market_id, error=str(e))
            return
        finally:
            self.quote_engine.config.default_order_size = original_order_size
            self.quote_engine.spread_model.config.min_half_spread_bps = original_min_half_spread

        if not plan.slices:
            return

        self.metrics.record_quote(market_cfg.market_id, len(plan.slices))

        # Refresh only after the minimum quote lifetime to reduce churn.
        await self.venue.cancel_market_orders(market_cfg.market_id)
        self._last_quote_refresh[market_cfg.market_id] = time.monotonic()

        # Convert to orders and submit
        orders = plan.to_order_intents()
        for order in orders:
            try:
                order = order.model_copy(update={
                    "strategy_tag": f"{market_cfg.execution_mode}:{self._decision_id}",
                })
                self.metrics.record_order(market_cfg.market_id)
                if self._health_monitor is not None:
                    self._health_monitor.record_order(market_cfg.market_id)
                result = await self.venue.submit_order(order)

                if self.mode == "paper" and result.filled_qty > 0:
                    # Paper mode: venue may fill immediately
                    from runner.paper_venue_adapter import PaperVenueAdapter
                    if isinstance(self.venue, PaperVenueAdapter):
                        venue_pos = self.venue.venue.get_position(market_cfg.market_id)
                        if venue_pos:
                            self._positions[market_cfg.market_id] = venue_pos
                        self.total_pnl = self.venue.venue.total_pnl

                    logger.info(
                        "order.result",
                        market_id=market_cfg.market_id,
                        side=order.side.value,
                        price=str(order.price),
                        status=result.status.value,
                        filled=str(result.filled_qty),
                        total_pnl=str(self.total_pnl),
                    )
                elif self.mode == "live":
                    if result.status == OrderStatus.REJECTED:
                        logger.warning(
                            "order.rejected",
                            market_id=market_cfg.market_id,
                            side=order.side.value,
                            price=str(order.price),
                            decision_id=self._decision_id,
                            mode=market_cfg.execution_mode,
                        )
                    else:
                        logger.info(
                            "order.submitted",
                            market_id=market_cfg.market_id,
                            side=order.side.value,
                            price=str(order.price),
                            size=str(order.size),
                            decision_id=self._decision_id,
                            mode=market_cfg.execution_mode,
                        )

                        if self.supabase_logger:
                            token_is_yes = (order.token_id == market_cfg.token_id_yes)
                            self.supabase_logger.log_order(
                                market_id=market_cfg.market_id,
                                order_id=str(order.client_order_id),
                                side=order.side.value,
                                token_side="YES" if token_is_yes else "NO",
                                price=order.price,
                                size=order.size,
                                status="submitted",
                            )

            except Exception as e:
                logger.warning("order.submit_error",
                               market_id=market_cfg.market_id, error=str(e))

    async def _process_directional_market(self, market_cfg: UnifiedMarketConfig) -> None:
        """Directional path gated behind Quant allocation and transport health."""
        market_state = self.book_tracker.get_market_state(market_cfg)
        if market_state is None:
            return

        if self.mode == "live" and not self._allow_directional_live:
            return
        if market_cfg.directional_side not in {"YES", "NO"}:
            return
        if market_cfg.entry_price_limit is None or market_cfg.stake_usdc is None:
            return
        if self._should_hold_quotes(market_cfg, ttl_override=float(market_cfg.ttl_seconds or 0)):
            return
        if self._max_directional_capital > 0 and (
            self._directional_allocated + market_cfg.stake_usdc > self._max_directional_capital
        ):
            logger.info(
                "directional.cap_reached",
                market_id=market_cfg.market_id,
                allocated=str(self._directional_allocated),
                cap=str(self._max_directional_capital),
            )
            return

        if market_cfg.directional_side == "YES":
            token_id = market_cfg.token_id_yes
            live_price = market_state.yes_ask or market_state.mid_price
        else:
            token_id = market_cfg.token_id_no
            live_price = market_state.no_ask or (Decimal("1") - market_state.mid_price)

        if live_price <= 0 or live_price > market_cfg.entry_price_limit:
            return

        size = (market_cfg.stake_usdc / live_price).quantize(Decimal("0.0001"))
        if size < market_cfg.min_order_size:
            return

        order = Order(
            market_id=market_cfg.market_id,
            token_id=token_id,
            side=Side.BUY,
            price=live_price,
            size=size,
            order_type=OrderType.GTC,
            maker_only=True,
            ttl_ms=int((market_cfg.ttl_seconds or 60) * 1000),
            strategy_tag=f"event_driven:{self._decision_id}",
        )

        self.metrics.record_order(market_cfg.market_id)
        result = await self.venue.submit_order(order)
        self._last_quote_refresh[market_cfg.market_id] = time.monotonic()
        if result.status != OrderStatus.REJECTED:
            self._directional_allocated += market_cfg.stake_usdc
            logger.info(
                "directional.order_submitted",
                market_id=market_cfg.market_id,
                price=str(live_price),
                size=str(size),
                decision_id=self._decision_id,
            )
        else:
            logger.warning(
                "directional.order_rejected",
                market_id=market_cfg.market_id,
                price=str(live_price),
                decision_id=self._decision_id,
            )

    def _should_hold_quotes(
        self,
        market_cfg: UnifiedMarketConfig,
        *,
        ttl_override: float | None = None,
    ) -> bool:
        min_lifetime = ttl_override
        if min_lifetime is None:
            min_lifetime = float(market_cfg.min_quote_lifetime_s or 0)
        if min_lifetime <= 0:
            return False
        last_refresh = self._last_quote_refresh.get(market_cfg.market_id)
        if last_refresh is None:
            return False
        return (time.monotonic() - last_refresh) < min_lifetime

    async def _metrics_flush_loop(self) -> None:
        """Paper mode: flush metrics every hour."""
        while self._running:
            try:
                await asyncio.sleep(self.metrics_flush_interval)
                if not self._running:
                    break
                snapshot = self.metrics.flush_hour(self._positions, self.total_pnl)
                logger.info(
                    "metrics.hourly_flush",
                    hour=snapshot["hour"],
                    quotes=snapshot["quotes_generated"],
                    fills=snapshot["fills"],
                    pnl=snapshot["total_pnl"],
                )
                self.metrics.save_checkpoint(DATA_DIR / "metrics_checkpoint.json")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("metrics_flush.error", error=str(e))

    async def _data_gap_monitor(self) -> None:
        """Monitor data gaps and trigger kill switch."""
        check_interval = 10 if self.mode == "live" else 5
        gap_threshold = 30 if self.mode == "live" else 15

        while self._running:
            try:
                await asyncio.sleep(check_interval)

                for market_cfg in self.market_configs:
                    yes_age = self.book_tracker.last_update_age(market_cfg.token_id_yes)
                    no_age = self.book_tracker.last_update_age(market_cfg.token_id_no)
                    min_age = min(yes_age, no_age)

                    if min_age > gap_threshold and min_age < float("inf"):
                        await self.kill_switch.trigger_data_gap(
                            market_id=market_cfg.market_id,
                            gap_seconds=min_age,
                        )
                        if self.mode == "live":
                            await self.venue.cancel_market_orders(market_cfg.market_id)

                self.kill_switch.record_heartbeat()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("data_gap_monitor.error", error=str(e))

    async def _duration_watchdog(self) -> None:
        """Stop after duration_hours."""
        total_seconds = self.duration_hours * 3600
        try:
            await asyncio.sleep(total_seconds)
            logger.info("duration.reached", hours=self.duration_hours)
            await self.stop()
        except asyncio.CancelledError:
            pass

    async def _position_rebalance_loop(self) -> None:
        """Paper mode: periodically reset positions that exceed limits."""
        MAX_NET_POSITION = 500

        while self._running:
            try:
                await asyncio.sleep(30)

                for market_cfg in self.market_configs:
                    pos = self._positions.get(market_cfg.market_id)
                    if not pos:
                        continue

                    net = abs(pos.qty_yes - pos.qty_no)
                    if net > MAX_NET_POSITION:
                        realized = pos.realized_pnl
                        self._positions[market_cfg.market_id] = Position(
                            market_id=market_cfg.market_id,
                            token_id_yes=market_cfg.token_id_yes,
                            token_id_no=market_cfg.token_id_no,
                        )
                        self._positions[market_cfg.market_id].realized_pnl = realized

                        from runner.paper_venue_adapter import PaperVenueAdapter
                        if isinstance(self.venue, PaperVenueAdapter):
                            self.venue.venue.reset_position(market_cfg.market_id)

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

    async def _reconcile_loop(self) -> None:
        """Live mode only: periodically reconcile wallet with on-chain state."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._running:
                    break
                await self.wallet.reconcile_on_chain(
                    self.rest_client,
                    market_configs=self.market_configs,
                )
                logger.debug(
                    "reconcile.done",
                    on_chain_usdc=self.wallet.on_chain.get("usdc_balance"),
                    discrepancy=self.wallet.on_chain.get("discrepancy_usdc"),
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("reconcile_loop.error", error=str(e))

    async def _execution_guard_loop(self) -> None:
        """Live mode: halt on structural execution anomalies."""
        while self._running:
            try:
                await asyncio.sleep(1)
                if not hasattr(self.venue, "drain_execution_alerts"):
                    continue
                alerts = self.venue.drain_execution_alerts()
                for alert in alerts:
                    if not alert.get("critical"):
                        continue
                    logger.critical(
                        "execution_guard.halt",
                        code=alert.get("code"),
                        market_id=alert.get("market_id"),
                        message=alert.get("message"),
                    )
                    await self.venue.cancel_all_orders()
                    await self.stop(reason=f"operational_halt:{alert.get('code')}")
                    return
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("execution_guard.error", error=str(e))

    async def _transport_monitor_loop(self) -> None:
        """Live mode: record latency probes continuously for correlation."""
        while self._running:
            try:
                await asyncio.sleep(60)
                if self.latency_recorder is None:
                    return
                self.latency_recorder.sample_current_transport()
                self.latency_recorder.write_summary()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("transport_monitor.error", error=str(e))

    async def _live_state_loop(self) -> None:
        """Write live state JSON periodically."""
        write_interval = 10 if self.mode == "live" else 5

        while self._running:
            try:
                await asyncio.sleep(write_interval)
                if not self._running:
                    break

                # Compute realized/unrealized PnL
                realized = Decimal("0")
                unrealized = Decimal("0")
                for mid, pos in self._positions.items():
                    realized += pos.realized_pnl
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

                # Write state
                write_kwargs: dict[str, Any] = {
                    "status": "RUNNING",
                    "total_pnl": self.total_pnl,
                    "realized_pnl": realized,
                    "unrealized_pnl": unrealized,
                    "positions": self._positions,
                    "metrics": self.metrics,
                    "market_configs": self.market_configs,
                    "book_tracker": self.book_tracker,
                    "kill_switch": self.kill_switch,
                    "ws_connected": self.ws_client.connected if hasattr(self.ws_client, 'connected') else True,
                    "wallet": self.wallet.wallet_snapshot(
                        self._get_mid_prices() if self.mode == "live" else None
                    ),
                }

                if self.mode == "live":
                    on_chain = self.wallet.on_chain
                    if on_chain:
                        write_kwargs["on_chain"] = on_chain

                self.live_state_writer.write(**write_kwargs)

                # Kill switch checks (shared logic, mode-aware thresholds)
                await self._check_kill_switch()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("live_state_loop.error", error=str(e))

    # ── Market Health + Rotation + Capital Recovery Loops ────────

    async def _market_health_loop(self) -> None:
        """Periodically evaluate market health and trigger rotation if needed."""
        assert self._health_monitor is not None
        check_interval = 60  # every 60 seconds

        while self._running:
            try:
                await asyncio.sleep(check_interval)
                if not self._running:
                    break

                for market_cfg in list(self.market_configs):
                    pos = self._positions.get(market_cfg.market_id)
                    snapshot = self._health_monitor.evaluate(
                        market_cfg.market_id, position=pos
                    )

                    if snapshot.is_unhealthy:
                        # Check cooldown
                        now = time.monotonic()
                        cooldown_s = self._rotation_config.rotation_cooldown_hours * 3600
                        if now - self._last_rotation_time < cooldown_s:
                            logger.info(
                                "market_health.rotation_cooldown",
                                market_id=market_cfg.market_id,
                                seconds_remaining=round(
                                    cooldown_s - (now - self._last_rotation_time)
                                ),
                            )
                            continue

                        logger.warning(
                            "market_health.triggering_rotation",
                            market_id=market_cfg.market_id,
                            health_score=round(snapshot.health_score, 3),
                        )
                        await self._rotate_market(market_cfg)
                        self._last_rotation_time = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("market_health_loop.error", error=str(e))

    async def _rotate_market(self, old_market: UnifiedMarketConfig) -> None:
        """Rotate away from an unhealthy market.

        Steps:
        1. Cancel all orders for the old market
        2. Add old market to blacklist (persisted)
        3. Auto-select a new market (excluding blacklist)
        4. Update internal state
        5. Reconnect WS for new tokens
        """
        logger.info("rotation.starting", old_market=old_market.market_id)

        # 1. Cancel orders
        try:
            await self.venue.cancel_market_orders(old_market.market_id)
        except Exception as e:
            logger.warning("rotation.cancel_failed", error=str(e))

        # 2. Blacklist the old market
        self._rotation_blacklist.add(old_market.market_id)
        save_rotation_blacklist(
            self._rotation_config.blacklist_path, self._rotation_blacklist
        )

        # 3. Clean up health monitor data for old market
        if self._health_monitor:
            self._health_monitor.prune_market(old_market.market_id)

        # 4. Auto-select new market
        new_markets: list[UnifiedMarketConfig] = []
        if self.rest_client is not None:
            try:
                new_markets = await auto_select_markets(
                    self.rest_client,
                    max_markets=1,
                    blacklist=self._rotation_blacklist,
                )
            except Exception as e:
                logger.error("rotation.auto_select_failed", error=str(e))

        if not new_markets:
            logger.warning(
                "rotation.no_replacement_found",
                old_market=old_market.market_id,
            )
            # Remove old market from active list but don't add replacement
            self.market_configs = [
                m for m in self.market_configs if m.market_id != old_market.market_id
            ]
            return

        new_market = new_markets[0]

        # 5. Update internal state
        self.market_configs = [
            m for m in self.market_configs if m.market_id != old_market.market_id
        ]
        self.market_configs.append(new_market)

        # Update token→market mapping
        self._token_to_market.pop(old_market.token_id_yes, None)
        self._token_to_market.pop(old_market.token_id_no, None)
        self._token_to_market[new_market.token_id_yes] = new_market
        self._token_to_market[new_market.token_id_no] = new_market

        # Init position for new market
        self.wallet.init_position(
            new_market.market_id,
            new_market.token_id_yes,
            new_market.token_id_no,
        )
        from models.position import Position as _Pos

        self._positions[new_market.market_id] = self.wallet.get_position(
            new_market.market_id
        ) or _Pos(
            market_id=new_market.market_id,
            token_id_yes=new_market.token_id_yes,
            token_id_no=new_market.token_id_no,
        )

        # 6. Reconnect WS for new tokens
        try:
            await self.ws_client.stop()
            token_ids = []
            for m in self.market_configs:
                token_ids.append(m.token_id_yes)
                token_ids.append(m.token_id_no)
            self.ws_client = CLOBWebSocketClient(
                event_bus=self.event_bus,
                token_ids=token_ids,
            )
            await self.ws_client.start()
        except Exception as e:
            logger.error("rotation.ws_reconnect_failed", error=str(e))

        logger.info(
            "rotation.completed",
            old_market=old_market.market_id,
            new_market=new_market.market_id,
            new_description=new_market.description,
        )

    async def _capital_recovery_loop(self) -> None:
        """Periodically check balance and sell positions if needed."""
        assert self._capital_recovery is not None
        check_interval = 30  # every 30 seconds

        while self._running:
            try:
                await asyncio.sleep(check_interval)
                if not self._running:
                    break

                current_balance = self.wallet.available_balance
                if not self._capital_recovery.needs_recovery(current_balance):
                    continue

                logger.info(
                    "capital_recovery.triggered",
                    current_balance=str(current_balance),
                    threshold=str(self._rotation_config.min_balance_for_recovery),
                )

                plan = self._capital_recovery.plan_recovery(
                    current_balance=current_balance,
                    positions=dict(self._positions),
                    market_configs=self.market_configs,
                    mid_prices=self._get_mid_prices(),
                )

                if plan.is_empty:
                    logger.info("capital_recovery.no_sellable_positions")
                    continue

                results = await self._capital_recovery.execute_recovery(
                    plan=plan,
                    venue=self.venue,
                    market_configs=self.market_configs,
                )

                logger.info(
                    "capital_recovery.executed",
                    orders=len(results),
                    new_balance=str(self.wallet.available_balance),
                )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("capital_recovery_loop.error", error=str(e))

    async def _check_kill_switch(self) -> None:
        """Balance/equity-based kill switch checks (shared, config-driven)."""
        if self.mode == "paper":
            equity = self.wallet.total_equity()
            initial = self.wallet.initial_balance
        else:
            mids = self._get_mid_prices()
            equity = self.wallet.total_equity(mids)
            initial = self.wallet.test_capital

        if initial > Decimal("0"):
            drawdown_pct = float((initial - equity) / initial * 100)

            kill_pct = self._kill_switch_max_drawdown_pct
            alert_pct = self._kill_switch_alert_pct

            if drawdown_pct >= kill_pct:
                loss = initial - equity
                logger.critical(
                    "kill_switch.drawdown",
                    equity=str(equity),
                    initial=str(initial),
                    drawdown_pct=round(drawdown_pct, 2),
                    kill_threshold_pct=kill_pct,
                    mode=self.mode,
                )
                await self.kill_switch.trigger_max_drawdown(loss)
                if self.mode == "live":
                    await self.venue.cancel_all_orders()
            elif drawdown_pct >= alert_pct:
                logger.warning(
                    "drawdown_alert",
                    equity=str(equity),
                    initial=str(initial),
                    drawdown_pct=round(drawdown_pct, 2),
                    mode=self.mode,
                )

        # Low balance pause (paper mode)
        if self.mode == "paper" and self.wallet.available_balance < Decimal("10"):
            if self.kill_switch.state != KillSwitchState.HALTED:
                logger.warning("low_balance_pause",
                               available=str(self.wallet.available_balance))
                for mc in self.market_configs:
                    self.kill_switch._paused_markets.add(mc.market_id)

    async def _live_unwind(self) -> None:
        """Live mode: unwind positions on shutdown."""
        try:
            from execution.unwind import UnwindConfig, UnwindManager, UnwindStrategy
            from execution.ctf_merge import CTFMerger

            if not self.rest_client:
                return

            reason = self._shutdown_reason or "graceful_shutdown"
            strategy = (
                UnwindStrategy.SWEEP if self.kill_switch.is_halted
                else UnwindStrategy.AGGRESSIVE
            )

            unwind_manager = UnwindManager(
                rest_client=self.rest_client,
                ctf_merger=CTFMerger(),
                config=UnwindConfig(),
            )

            unwind_report = await unwind_manager.unwind_all(
                positions=dict(self.wallet.positions),
                reason=reason,
                strategy=strategy,
                market_configs=self.market_configs,
            )

            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            report_path = DATA_DIR / f"unwind_{self._run_id}_{timestamp}.json"
            unwind_report.save(report_path)
            logger.info(
                "unwind_complete",
                success=unwind_report.success,
                proceeds=str(unwind_report.total_proceeds),
            )
        except Exception as e:
            logger.error("unwind_failed", error=str(e))

    async def _final_flush(self) -> None:
        """Final metrics flush and save."""
        self.metrics.flush_hour(self._positions, self.total_pnl)
        if self.latency_recorder is not None:
            self.latency_recorder.write_summary()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        prefix = "metrics_production_" if self.mode == "live" else "metrics_"
        self.metrics.save(DATA_DIR / f"{prefix}{timestamp}.json")

        if self.mode == "paper":
            self.metrics.save(DATA_DIR / "metrics_latest.json")

            # Save positions
            positions_data = {}
            for mid, pos in self._positions.items():
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
            mode=self.mode,
            total_pnl=str(self.total_pnl),
            total_quotes=self.metrics.total_quotes,
            total_orders=self.metrics.total_orders,
            total_fills=self.metrics.total_fills,
        )

        # Write final live state
        write_kwargs: dict[str, Any] = {
            "status": "FINISHED",
            "total_pnl": self.total_pnl,
            "realized_pnl": self._realized_pnl,
            "unrealized_pnl": self._unrealized_pnl,
            "positions": self._positions,
            "metrics": self.metrics,
            "market_configs": self.market_configs,
            "book_tracker": self.book_tracker,
            "kill_switch": self.kill_switch,
            "wallet": self.wallet.wallet_snapshot(
                self._get_mid_prices() if self.mode == "live" else None
            ),
        }
        if self.mode == "live":
            on_chain = self.wallet.on_chain
            if on_chain:
                write_kwargs["on_chain"] = on_chain
        self.live_state_writer.write(**write_kwargs)

        # Write run history
        uptime_h = (time.monotonic() - self.live_state_writer._start_time) / 3600
        fill_rate = (self.metrics.total_fills / self.metrics.total_orders * 100) if self.metrics.total_orders > 0 else 0
        pnl_per_h = float(self.total_pnl) / uptime_h if uptime_h > 0.01 else 0

        result = "INCONCLUSIVE"
        if uptime_h >= 1:
            result = "PASS" if pnl_per_h > 0 else "FAIL"

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
            logger.warning("run_history.error", error=str(e))

    def _selected_transport(self) -> str:
        if self.transport_selection is None:
            return ""
        return getattr(self.transport_selection, "selected_transport", "")

    def _selected_transport_ttfb_ms(self) -> float:
        if self.transport_selection is None:
            return 0.0
        samples = (
            self.transport_selection.direct_samples
            if self.transport_selection.selected_transport == "direct"
            else self.transport_selection.proxy_samples
        )
        ok_samples = [sample.ttfb_ms for sample in samples if getattr(sample, "ok", False)]
        if not ok_samples:
            return 0.0
        return float(sum(ok_samples) / len(ok_samples))

    @staticmethod
    def _latency_bucket(ttfb_ms: float) -> str:
        if ttfb_ms <= 0:
            return "unknown"
        if ttfb_ms < 350:
            return "fast"
        if ttfb_ms < 1300:
            return "degraded"
        return "slow"

    @staticmethod
    def _reward_estimate_for_market(market_cfg: UnifiedMarketConfig | None) -> float:
        if market_cfg is None:
            return 0.0
        if market_cfg.expected_reward_yield_bps_day is None or market_cfg.order_size_override is None:
            return 0.0
        daily_yield = Decimal(str(market_cfg.expected_reward_yield_bps_day)) / Decimal("10000")
        return float((market_cfg.order_size_override * daily_yield).quantize(Decimal("0.000001")))

    def _get_mid_prices(self) -> dict[str, Decimal]:
        """Get current mid prices from book tracker."""
        mids: dict[str, Decimal] = {}
        for mc in self.market_configs:
            ms = self.book_tracker.get_market_state(mc)
            if ms and ms.mid_price > 0:
                mids[mc.market_id] = ms.mid_price
        return mids
