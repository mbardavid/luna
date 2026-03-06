"""runner.live_venue_adapter — VenueAdapter wrapping LiveExecution + CLOBRestClient.

Handles complement routing, position caps, and persistent fill dedup
(moved from ProductionTradingPipeline).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from data.rest_client import CLOBRestClient
from execution.live_execution import LiveExecution
from models.order import Order, OrderStatus, OrderType, Side
from runner.config import UnifiedMarketConfig
from runner.venue_adapter import VenueAdapter

logger = structlog.get_logger("runner.live_venue_adapter")

# Data directory for trade dedup persistence
_DATA_DIR = Path(__file__).resolve().parent.parent / "paper" / "data"


class LiveVenueAdapter(VenueAdapter):
    """VenueAdapter backed by real Polymarket CLOB via LiveExecution.

    Includes:
    - Complement routing (SELL YES → BUY NO when no shares to sell)
    - Position cap enforcement
    - Fill dedup by fill_id (persisted across restarts)
    """

    def __init__(
        self,
        execution: LiveExecution,
        rest_client: CLOBRestClient,
        market_configs: list[UnifiedMarketConfig],
        *,
        complement_routing: bool = True,
        max_position_per_side: Decimal = Decimal("100"),
        wallet_adapter: Any = None,  # WalletAdapter reference for position checks
    ) -> None:
        self._execution = execution
        self._rest_client = rest_client
        self._market_configs = market_configs
        self._complement_routing = complement_routing
        self._max_position_per_side = max_position_per_side
        self._wallet = wallet_adapter

        # Market config lookup
        self._market_by_id: dict[str, UnifiedMarketConfig] = {
            m.market_id: m for m in market_configs
        }
        self._token_to_market: dict[str, UnifiedMarketConfig] = {}
        for m in market_configs:
            self._token_to_market[m.token_id_yes] = m
            self._token_to_market[m.token_id_no] = m

        # Trade dedup (BUG-1 FIX from production_runner)
        self._trade_dedup_path = _DATA_DIR / "processed_trade_ids.json"
        self._processed_trades: set[str] = set()
        self._last_processed_trade_ts: str = ""
        self._load_trade_dedup()

    @property
    def mode(self) -> str:
        return "live"

    def set_wallet(self, wallet_adapter: Any) -> None:
        """Set wallet adapter reference (for position checks in routing)."""
        self._wallet = wallet_adapter

    def drain_execution_alerts(self) -> list[dict]:
        return [alert.to_dict() for alert in self._execution.drain_alerts()]

    async def connect(self) -> None:
        await self._rest_client.connect()

    async def disconnect(self) -> None:
        await self._rest_client.disconnect()

    async def submit_order(self, order: Order) -> Order:
        """Submit order with complement routing and position cap enforcement.

        This moves the complement routing + position cap logic from
        ProductionTradingPipeline._process_market into the adapter.
        """
        market_cfg = self._market_by_id.get(order.market_id)
        if not market_cfg:
            logger.warning("live_venue.unknown_market", market_id=order.market_id)
            order.status = OrderStatus.REJECTED
            return order

        # Ensure maker-only GTC
        order = order.model_copy(update={
            "order_type": OrderType.GTC,
            "maker_only": True,
        })

        # ── Complement routing ──────────────────────────
        if order.side == Side.SELL and self._wallet is not None:
            token_is_yes = (order.token_id == market_cfg.token_id_yes)
            pos = self._wallet.get_position(market_cfg.market_id)
            available = Decimal("0")
            if pos is not None:
                available = pos.qty_yes if token_is_yes else pos.qty_no

            if available < order.size:
                if self._complement_routing:
                    complement_token_id = (
                        market_cfg.token_id_no if token_is_yes
                        else market_cfg.token_id_yes
                    )
                    complement_price = Decimal("1") - order.price
                    logger.debug(
                        "live_venue.complement_routing",
                        original=f"SELL {'YES' if token_is_yes else 'NO'} @ {order.price}",
                        routed=f"BUY {'NO' if token_is_yes else 'YES'} @ {complement_price}",
                        market_id=market_cfg.market_id,
                    )
                    order = order.model_copy(update={
                        "side": Side.BUY,
                        "token_id": complement_token_id,
                        "price": complement_price,
                    })
                else:
                    logger.debug(
                        "live_venue.sell_skipped_no_position",
                        market_id=market_cfg.market_id,
                        available=str(available),
                        order_size=str(order.size),
                    )
                    order.status = OrderStatus.REJECTED
                    return order

        # ── Position cap ────────────────────────────────
        if order.side == Side.BUY and self._wallet is not None:
            cap_token_is_yes = (order.token_id == market_cfg.token_id_yes)
            cap_pos = self._wallet.get_position(market_cfg.market_id)
            current = Decimal("0")
            if cap_pos is not None:
                current = cap_pos.qty_yes if cap_token_is_yes else cap_pos.qty_no
            if current + order.size > self._max_position_per_side:
                logger.debug(
                    "live_venue.position_cap",
                    current=str(current),
                    order_size=str(order.size),
                    cap=str(self._max_position_per_side),
                    market_id=market_cfg.market_id,
                )
                order.status = OrderStatus.REJECTED
                return order

        # Set tick_size and neg_risk per market
        self._execution._default_tick_size = str(market_cfg.tick_size)
        self._execution._default_neg_risk = market_cfg.neg_risk

        return await self._execution.submit_order(order)

    async def cancel_order(self, client_order_id: UUID) -> bool:
        return await self._execution.cancel_order(client_order_id)

    async def cancel_all_orders(self) -> None:
        try:
            await self._rest_client.cancel_all_orders()
            logger.info("live_venue.cancelled_all_orders")
        except Exception as e:
            logger.warning("live_venue.cancel_all_error", error=str(e))

    async def cancel_market_orders(self, market_id: str) -> None:
        try:
            open_orders = await self._execution.get_open_orders()
            for oo in open_orders:
                if oo.market_id == market_id:
                    if oo.exchange_order_id:
                        await self._rest_client.cancel_order(oo.exchange_order_id)
                    else:
                        await self._execution.cancel_order(oo.client_order_id)
        except Exception as e:
            logger.warning("live_venue.cancel_market_error",
                           market_id=market_id, error=str(e))

    async def get_open_orders(self) -> list[Order]:
        return await self._execution.get_open_orders()

    async def process_fills(self) -> list[dict]:
        """Poll REST API for trades, dedup by fill_id, return new fills.

        Moved from ProductionTradingPipeline._process_trade with persistent
        dedup (BUG-1 FIX: fill dedup by fill_id).
        """
        fills: list[dict] = []

        our_address = self._rest_client.clob_client.get_address().lower()

        for market_cfg in self._market_configs:
            try:
                trades = await self._rest_client.get_trades(
                    market=market_cfg.condition_id,
                )
                for trade in trades:
                    trade_id = trade.get("id", "")
                    if trade_id in self._processed_trades:
                        continue
                    self._processed_trades.add(trade_id)

                    maker_orders = trade.get("maker_orders", [])
                    for mo in maker_orders:
                        if mo.get("maker_address", "").lower() != our_address:
                            continue

                        mo_order_id = mo.get("order_id", "")
                        mo_side = mo.get("side", "").upper()
                        mo_token_id = mo.get("asset_id", "")
                        mo_price = Decimal(str(mo.get("price", "0")))
                        mo_qty = Decimal(str(mo.get("matched_amount", "0")))
                        mo_fee_rate = Decimal(str(mo.get("fee_rate_bps", "0")))
                        mo_fee = mo_price * mo_qty * mo_fee_rate / Decimal("10000")

                        if mo_qty <= 0:
                            continue

                        mo_dedup_key = f"{trade_id}:{mo_order_id}"
                        if mo_dedup_key in self._processed_trades:
                            continue
                        self._processed_trades.add(mo_dedup_key)

                        is_yes = mo_token_id == market_cfg.token_id_yes

                        fills.append({
                            "market_id": market_cfg.market_id,
                            "token_id": mo_token_id,
                            "side": mo_side,
                            "fill_price": mo_price,
                            "fill_qty": mo_qty,
                            "fee": mo_fee,
                            "fee_rate_bps": float(mo_fee_rate),
                            "fill_id": mo_dedup_key,
                            "exchange_order_id": mo_order_id,
                            "token_is_yes": is_yes,
                        })

                    # Update trade timestamp watermark
                    trade_ts = trade.get("match_time", "") or trade.get("created_at", "")
                    if trade_ts and trade_ts > self._last_processed_trade_ts:
                        self._last_processed_trade_ts = trade_ts

            except Exception as e:
                logger.debug("live_venue.trade_poll_error",
                             market_id=market_cfg.market_id, error=str(e))

        if fills:
            self._save_trade_dedup()

        return fills

    # ── Trade dedup persistence ─────────────────────────────

    def _load_trade_dedup(self) -> None:
        try:
            if self._trade_dedup_path.exists():
                with open(self._trade_dedup_path) as f:
                    data = json.load(f)
                self._processed_trades = set(data.get("trade_ids", []))
                self._last_processed_trade_ts = data.get("last_trade_ts", "")
                logger.info("trade_dedup.loaded", count=len(self._processed_trades))
        except Exception as e:
            logger.warning("trade_dedup.load_error", error=str(e))
            self._processed_trades = set()

    def _save_trade_dedup(self) -> None:
        try:
            data = {
                "trade_ids": list(self._processed_trades),
                "last_trade_ts": self._last_processed_trade_ts,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "count": len(self._processed_trades),
            }
            tmp = self._trade_dedup_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(data, f)
            tmp.replace(self._trade_dedup_path)
        except Exception as e:
            logger.warning("trade_dedup.save_error", error=str(e))
