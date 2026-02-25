"""PaperVenue — realistic simulated order book and matching engine.

Provides a ``MarketDataProvider`` implementation backed by an in-memory
order book with random-walk price dynamics, price-time priority matching,
configurable latency, partial fills, and position/PnL tracking.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any, AsyncIterator
from uuid import UUID, uuid4

import structlog

from core.event_bus import EventBus
from data.market_data_provider import MarketDataProvider
from models.market_state import MarketState, MarketType
from models.order import Order, OrderStatus, Side
from models.position import Position

logger = structlog.get_logger("paper.venue")

# ── Helpers ──────────────────────────────────────────────────────────


def _quantize(price: Decimal, tick: Decimal) -> Decimal:
    """Round *price* down to the nearest tick."""
    return (price / tick).quantize(Decimal("1"), rounding=ROUND_DOWN) * tick


def _is_valid_tick(price: Decimal, tick: Decimal) -> bool:
    """Return True if *price* is an exact multiple of *tick*."""
    return price == _quantize(price, tick)


# ── Book Level / Simulated Book ──────────────────────────────────────


@dataclass
class _BookLevel:
    price: Decimal
    size: Decimal


@dataclass
class _SimulatedBook:
    """In-memory order book for a single token (YES or NO)."""

    token_id: str
    tick_size: Decimal
    bids: list[_BookLevel] = field(default_factory=list)
    asks: list[_BookLevel] = field(default_factory=list)

    def best_bid(self) -> Decimal:
        return self.bids[0].price if self.bids else Decimal("0")

    def best_ask(self) -> Decimal:
        return self.asks[0].price if self.asks else Decimal("0")

    def depth_bid(self) -> Decimal:
        return self.bids[0].size if self.bids else Decimal("0")

    def depth_ask(self) -> Decimal:
        return self.asks[0].size if self.asks else Decimal("0")


@dataclass
class _PendingOrder:
    """An order resting in the paper venue's matching engine."""

    order: Order
    arrival_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ── Market Sim Config ────────────────────────────────────────────────


@dataclass
class MarketSimConfig:
    """Configuration for a single simulated market."""

    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    tick_size: Decimal = Decimal("0.01")
    min_order_size: Decimal = Decimal("5")
    neg_risk: bool = False
    market_type: MarketType = MarketType.OTHER
    initial_yes_mid: Decimal = Decimal("0.50")
    volatility: Decimal = Decimal("0.005")  # per-step random walk σ


# ── PaperVenue ───────────────────────────────────────────────────────


class PaperVenue(MarketDataProvider):
    """Simulated trading venue implementing ``MarketDataProvider``.

    Features:
    - Random-walk mid-price per market
    - Realistic bid/ask spread (1-3 ticks)
    - Price-time priority matching engine
    - Configurable fill latency + partial fills
    - Position and PnL tracking in memory
    - Heartbeat simulation
    - EventBus integration (publishes ``book``, ``fill``, ``heartbeat`` events)
    """

    def __init__(
        self,
        event_bus: EventBus,
        configs: list[MarketSimConfig] | None = None,
        num_random_markets: int = 10,
        fill_latency_ms: float = 50.0,
        partial_fill_probability: float = 0.3,
        heartbeat_interval_s: float = 5.0,
        seed: int | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._fill_latency_ms = fill_latency_ms
        self._partial_fill_prob = partial_fill_probability
        self._heartbeat_interval = heartbeat_interval_s
        self._rng = random.Random(seed)

        # Build market configs
        if configs:
            self._configs: list[MarketSimConfig] = list(configs)
        else:
            self._configs = self._generate_random_configs(num_random_markets)

        # Runtime state
        self._books_yes: dict[str, _SimulatedBook] = {}
        self._books_no: dict[str, _SimulatedBook] = {}
        self._mid_prices: dict[str, Decimal] = {}  # market_id -> YES mid
        self._tick_sizes: dict[str, Decimal] = {}  # mutable (chaos can change)
        self._open_orders: dict[UUID, _PendingOrder] = {}
        self._positions: dict[str, Position] = {}
        self._total_pnl: Decimal = Decimal("0")

        self._connected = False
        self._heartbeat_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._walk_task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._matching_paused = False  # set by ChaosInjector (ENGINE_RESTART)

    # ── MarketDataProvider interface ─────────────────────────────

    async def connect(self) -> None:
        """Initialize books and start background tasks."""
        self._connected = True
        for cfg in self._configs:
            self._init_market(cfg)
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._walk_task = asyncio.create_task(self._random_walk_loop())
        logger.info("paper_venue.connected", markets=len(self._configs))

    async def disconnect(self) -> None:
        self._connected = False
        for task in (self._heartbeat_task, self._walk_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("paper_venue.disconnected")

    async def get_active_markets(self) -> list[MarketState]:
        return [self._build_market_state(cfg) for cfg in self._configs]

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        book = self._find_book(token_id)
        if book is None:
            return {"bids": [], "asks": [], "timestamp": datetime.now(timezone.utc), "hash": None}
        return {
            "bids": [{"price": l.price, "size": l.size} for l in book.bids],
            "asks": [{"price": l.price, "size": l.size} for l in book.asks],
            "timestamp": datetime.now(timezone.utc),
            "hash": str(uuid4())[:8],
        }

    async def subscribe_book_updates(
        self, token_ids: list[str]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yields book update events from EventBus for the requested token_ids."""
        token_set = set(token_ids)
        async for event in self._event_bus.subscribe("book"):
            tid = event.payload.get("token_id")
            if tid in token_set:
                yield {
                    "event_type": event.payload.get("event_type", "book"),
                    "token_id": tid,
                    "data": event.payload,
                    "timestamp": event.timestamp,
                }

    # ── Order management (used by PaperExecution) ────────────────

    async def submit_order(self, order: Order) -> Order:
        """Submit an order into the paper matching engine.

        Returns the order with updated status.
        """
        # Idempotency check
        if order.client_order_id in self._open_orders:
            return self._open_orders[order.client_order_id].order

        # Validate tick size
        market_cfg = self._find_config(order.market_id)
        if market_cfg is None:
            order = order.model_copy(update={"status": OrderStatus.REJECTED})
            return order

        tick = self._tick_sizes.get(order.market_id, market_cfg.tick_size)
        if not _is_valid_tick(order.price, tick):
            order = order.model_copy(update={"status": OrderStatus.REJECTED})
            await self._event_bus.publish(
                "order_rejected",
                {
                    "client_order_id": str(order.client_order_id),
                    "reason": "invalid_tick_size",
                    "price": str(order.price),
                    "tick_size": str(tick),
                },
            )
            return order

        if self._matching_paused:
            order = order.model_copy(update={"status": OrderStatus.REJECTED})
            await self._event_bus.publish(
                "order_rejected",
                {
                    "client_order_id": str(order.client_order_id),
                    "reason": "engine_restart",
                },
            )
            return order

        # Accept and try to match
        order = order.model_copy(update={"status": OrderStatus.OPEN})
        pending = _PendingOrder(order=order)
        self._open_orders[order.client_order_id] = pending

        # Simulate fill latency
        latency_s = self._fill_latency_ms / 1000.0
        await asyncio.sleep(latency_s)

        # Attempt match
        order = await self._try_match(order)
        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            if order.status == OrderStatus.FILLED:
                self._open_orders.pop(order.client_order_id, None)
            else:
                self._open_orders[order.client_order_id] = _PendingOrder(order=order)

        return order

    async def cancel_order(self, client_order_id: UUID) -> bool:
        pending = self._open_orders.pop(client_order_id, None)
        if pending is None:
            return False
        cancelled = pending.order.model_copy(update={"status": OrderStatus.CANCELLED})
        # Don't store cancelled orders back
        await self._event_bus.publish(
            "order_cancelled",
            {"client_order_id": str(client_order_id)},
        )
        return True

    async def amend_order(
        self, client_order_id: UUID, new_price: Decimal, new_size: Decimal
    ) -> Order:
        pending = self._open_orders.get(client_order_id)
        if pending is None:
            raise ValueError(f"Order {client_order_id} not found or not open")

        market_cfg = self._find_config(pending.order.market_id)
        tick = self._tick_sizes.get(
            pending.order.market_id,
            market_cfg.tick_size if market_cfg else Decimal("0.01"),
        )
        if not _is_valid_tick(new_price, tick):
            raise ValueError(
                f"Price {new_price} is not a valid tick (tick_size={tick})"
            )

        amended = pending.order.model_copy(
            update={"price": new_price, "size": new_size}
        )
        self._open_orders[client_order_id] = _PendingOrder(order=amended)
        return amended

    async def get_open_orders(self) -> list[Order]:
        return [p.order for p in self._open_orders.values()]

    # ── Position / PnL ───────────────────────────────────────────

    def get_position(self, market_id: str) -> Position | None:
        return self._positions.get(market_id)

    def get_all_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    @property
    def total_pnl(self) -> Decimal:
        return self._total_pnl

    # ── Chaos hooks (used by ChaosInjector) ──────────────────────

    def pause_matching(self) -> None:
        self._matching_paused = True

    def resume_matching(self) -> None:
        self._matching_paused = False

    def change_tick_size(self, market_id: str, new_tick: Decimal) -> None:
        self._tick_sizes[market_id] = new_tick

    # ── Internal helpers ─────────────────────────────────────────

    def _generate_random_configs(self, n: int) -> list[MarketSimConfig]:
        configs: list[MarketSimConfig] = []
        types = list(MarketType)
        n = max(5, min(n, 20))
        for i in range(n):
            mid = Decimal(str(round(self._rng.uniform(0.10, 0.90), 2)))
            tick = Decimal("0.01") if mid > Decimal("0.04") and mid < Decimal("0.96") else Decimal("0.001")
            configs.append(
                MarketSimConfig(
                    market_id=f"paper-mkt-{i:03d}",
                    condition_id=f"paper-cond-{i:03d}",
                    token_id_yes=f"paper-tok-yes-{i:03d}",
                    token_id_no=f"paper-tok-no-{i:03d}",
                    tick_size=tick,
                    min_order_size=Decimal("5"),
                    market_type=self._rng.choice(types),
                    initial_yes_mid=mid,
                    volatility=Decimal(str(round(self._rng.uniform(0.002, 0.01), 4))),
                )
            )
        return configs

    def _init_market(self, cfg: MarketSimConfig) -> None:
        self._mid_prices[cfg.market_id] = cfg.initial_yes_mid
        self._tick_sizes[cfg.market_id] = cfg.tick_size
        self._rebuild_book(cfg)
        self._positions[cfg.market_id] = Position(
            market_id=cfg.market_id,
            token_id_yes=cfg.token_id_yes,
            token_id_no=cfg.token_id_no,
        )

    def _rebuild_book(self, cfg: MarketSimConfig) -> None:
        """Rebuild simulated books around the current mid price."""
        mid = self._mid_prices[cfg.market_id]
        tick = self._tick_sizes[cfg.market_id]

        # YES book
        spread_ticks = self._rng.randint(1, 3)
        half_spread = tick * spread_ticks
        yes_bid = _quantize(mid - half_spread, tick)
        yes_ask = _quantize(mid + half_spread, tick)
        if yes_bid <= Decimal("0"):
            yes_bid = tick
        if yes_ask > Decimal("1"):
            yes_ask = _quantize(Decimal("1"), tick)
        if yes_ask <= yes_bid:
            yes_ask = yes_bid + tick

        yes_book = _SimulatedBook(
            token_id=cfg.token_id_yes,
            tick_size=tick,
        )
        # Build 5 levels of depth
        for i in range(5):
            bid_p = yes_bid - tick * i
            ask_p = yes_ask + tick * i
            if bid_p > Decimal("0"):
                sz = Decimal(str(self._rng.randint(50, 500)))
                yes_book.bids.append(_BookLevel(price=bid_p, size=sz))
            if ask_p <= Decimal("1"):
                sz = Decimal(str(self._rng.randint(50, 500)))
                yes_book.asks.append(_BookLevel(price=ask_p, size=sz))

        # NO book — complementary prices
        no_bid = _quantize(Decimal("1") - yes_ask, tick)
        no_ask = _quantize(Decimal("1") - yes_bid, tick)
        if no_bid <= Decimal("0"):
            no_bid = tick
        if no_ask > Decimal("1"):
            no_ask = _quantize(Decimal("1"), tick)
        if no_ask <= no_bid:
            no_ask = no_bid + tick

        no_book = _SimulatedBook(
            token_id=cfg.token_id_no,
            tick_size=tick,
        )
        for i in range(5):
            bid_p = no_bid - tick * i
            ask_p = no_ask + tick * i
            if bid_p > Decimal("0"):
                sz = Decimal(str(self._rng.randint(50, 500)))
                no_book.bids.append(_BookLevel(price=bid_p, size=sz))
            if ask_p <= Decimal("1"):
                sz = Decimal(str(self._rng.randint(50, 500)))
                no_book.asks.append(_BookLevel(price=ask_p, size=sz))

        self._books_yes[cfg.market_id] = yes_book
        self._books_no[cfg.market_id] = no_book

    def _build_market_state(self, cfg: MarketSimConfig) -> MarketState:
        yes_book = self._books_yes.get(cfg.market_id)
        no_book = self._books_no.get(cfg.market_id)
        return MarketState(
            market_id=cfg.market_id,
            condition_id=cfg.condition_id,
            token_id_yes=cfg.token_id_yes,
            token_id_no=cfg.token_id_no,
            tick_size=self._tick_sizes.get(cfg.market_id, cfg.tick_size),
            min_order_size=cfg.min_order_size,
            neg_risk=cfg.neg_risk,
            market_type=cfg.market_type,
            yes_bid=yes_book.best_bid() if yes_book else Decimal("0"),
            yes_ask=yes_book.best_ask() if yes_book else Decimal("0"),
            no_bid=no_book.best_bid() if no_book else Decimal("0"),
            no_ask=no_book.best_ask() if no_book else Decimal("0"),
            depth_yes_bid=yes_book.depth_bid() if yes_book else Decimal("0"),
            depth_yes_ask=yes_book.depth_ask() if yes_book else Decimal("0"),
            depth_no_bid=no_book.depth_bid() if no_book else Decimal("0"),
            depth_no_ask=no_book.depth_ask() if no_book else Decimal("0"),
        )

    def _find_book(self, token_id: str) -> _SimulatedBook | None:
        for book in self._books_yes.values():
            if book.token_id == token_id:
                return book
        for book in self._books_no.values():
            if book.token_id == token_id:
                return book
        return None

    def _find_config(self, market_id: str) -> MarketSimConfig | None:
        for cfg in self._configs:
            if cfg.market_id == market_id:
                return cfg
        return None

    async def _try_match(self, order: Order) -> Order:
        """Attempt to match *order* against the simulated book (price-time)."""
        # Determine which book to match against
        cfg = self._find_config(order.market_id)
        if cfg is None:
            return order

        # Find the right book
        if order.token_id == cfg.token_id_yes:
            book = self._books_yes.get(order.market_id)
        elif order.token_id == cfg.token_id_no:
            book = self._books_no.get(order.market_id)
        else:
            return order

        if book is None:
            return order

        # BUY order matches against asks; SELL matches against bids
        if order.side == Side.BUY:
            levels = book.asks
            can_match = lambda lvl: order.price >= lvl.price  # noqa: E731
        else:
            levels = book.bids
            can_match = lambda lvl: order.price <= lvl.price  # noqa: E731

        remaining = order.size - order.filled_qty
        total_filled = order.filled_qty

        for level in list(levels):
            if remaining <= Decimal("0"):
                break
            if not can_match(level):
                break  # price-time: levels are sorted best-first

            # Decide fill amount
            available = level.size
            fill_qty = min(remaining, available)

            # Partial fill probability
            if fill_qty == remaining and self._rng.random() < self._partial_fill_prob:
                fill_qty = _quantize(
                    fill_qty * Decimal(str(round(self._rng.uniform(0.3, 0.9), 2))),
                    Decimal("1"),
                )
                if fill_qty <= Decimal("0"):
                    fill_qty = Decimal("1")

            total_filled += fill_qty
            remaining -= fill_qty
            level.size -= fill_qty

            # Publish fill event
            await self._event_bus.publish(
                "fill",
                {
                    "client_order_id": str(order.client_order_id),
                    "market_id": order.market_id,
                    "token_id": order.token_id,
                    "side": order.side.value,
                    "fill_price": str(level.price),
                    "fill_qty": str(fill_qty),
                },
            )

            # Update position
            self._update_position(order, level.price, fill_qty)

            if level.size <= Decimal("0"):
                levels.remove(level)

        # Update order status
        if total_filled >= order.size:
            new_status = OrderStatus.FILLED
            total_filled = order.size
        elif total_filled > Decimal("0"):
            new_status = OrderStatus.PARTIALLY_FILLED
        else:
            new_status = order.status  # stays OPEN

        return order.model_copy(update={
            "filled_qty": total_filled,
            "status": new_status,
        })

    def _update_position(
        self, order: Order, fill_price: Decimal, fill_qty: Decimal
    ) -> None:
        pos = self._positions.get(order.market_id)
        if pos is None:
            return

        cfg = self._find_config(order.market_id)
        if cfg is None:
            return

        is_yes = order.token_id == cfg.token_id_yes

        if order.side == Side.BUY:
            if is_yes:
                new_qty = pos.qty_yes + fill_qty
                if new_qty > Decimal("0"):
                    new_avg = (
                        (pos.avg_entry_yes * pos.qty_yes + fill_price * fill_qty)
                        / new_qty
                    )
                else:
                    new_avg = Decimal("0")
                self._positions[order.market_id] = pos.model_copy(
                    update={"qty_yes": new_qty, "avg_entry_yes": new_avg}
                )
            else:
                new_qty = pos.qty_no + fill_qty
                if new_qty > Decimal("0"):
                    new_avg = (
                        (pos.avg_entry_no * pos.qty_no + fill_price * fill_qty)
                        / new_qty
                    )
                else:
                    new_avg = Decimal("0")
                self._positions[order.market_id] = pos.model_copy(
                    update={"qty_no": new_qty, "avg_entry_no": new_avg}
                )
        else:  # SELL
            if is_yes:
                sell_qty = min(fill_qty, pos.qty_yes)
                pnl = (fill_price - pos.avg_entry_yes) * sell_qty
                self._total_pnl += pnl
                new_qty = pos.qty_yes - sell_qty
                self._positions[order.market_id] = pos.model_copy(
                    update={
                        "qty_yes": max(new_qty, Decimal("0")),
                        "realized_pnl": pos.realized_pnl + pnl,
                    }
                )
            else:
                sell_qty = min(fill_qty, pos.qty_no)
                pnl = (fill_price - pos.avg_entry_no) * sell_qty
                self._total_pnl += pnl
                new_qty = pos.qty_no - sell_qty
                self._positions[order.market_id] = pos.model_copy(
                    update={
                        "qty_no": max(new_qty, Decimal("0")),
                        "realized_pnl": pos.realized_pnl + pnl,
                    }
                )

    # ── Background loops ─────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while self._connected:
            await asyncio.sleep(self._heartbeat_interval)
            await self._event_bus.publish(
                "heartbeat",
                {"source": "paper_venue", "status": "alive"},
            )

    async def _random_walk_loop(self) -> None:
        """Walk mid prices every 500ms and rebuild books."""
        while self._connected:
            await asyncio.sleep(0.5)
            for cfg in self._configs:
                mid = self._mid_prices[cfg.market_id]
                tick = self._tick_sizes[cfg.market_id]
                # Random walk: mid += N(0, σ) * tick
                delta = Decimal(
                    str(round(self._rng.gauss(0, float(cfg.volatility)), 4))
                )
                new_mid = mid + delta
                # Clamp to [tick, 1 - tick]
                new_mid = max(tick, min(Decimal("1") - tick, new_mid))
                new_mid = _quantize(new_mid, tick)
                if new_mid <= Decimal("0"):
                    new_mid = tick
                self._mid_prices[cfg.market_id] = new_mid
                self._rebuild_book(cfg)

                # Publish book update
                await self._event_bus.publish(
                    "book",
                    {
                        "event_type": "book",
                        "market_id": cfg.market_id,
                        "token_id": cfg.token_id_yes,
                        "mid": str(new_mid),
                    },
                )
