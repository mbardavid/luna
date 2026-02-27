"""Tests for Phase 3 — Paper Trading Venue.

Covers:
- PaperVenue: market creation, order submit/cancel/fill, matching
- Tick size validation (rejects invalid prices)
- PaperExecution: idempotency, rate limiting
- ChaosInjector: event publication
- ExecutionProvider ABC
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from core.event_bus import Event, EventBus
from execution.execution_provider import ExecutionProvider
from models.market_state import MarketState, MarketType
from models.order import Order, OrderStatus, Side
from paper.chaos_injector import ChaosConfig, ChaosInjector
from paper.paper_execution import PaperExecution
from paper.paper_venue import MarketSimConfig, PaperVenue, _is_valid_tick
from paper.replay_engine import ReplayConfig, ReplayEngine


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def market_config() -> MarketSimConfig:
    return MarketSimConfig(
        market_id="test-mkt-001",
        condition_id="test-cond-001",
        token_id_yes="test-tok-yes-001",
        token_id_no="test-tok-no-001",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        initial_yes_mid=Decimal("0.50"),
        volatility=Decimal("0.005"),
        fill_probability=1.0,  # deterministic fills for tests
    )


@pytest.fixture
async def venue(event_bus: EventBus, market_config: MarketSimConfig) -> PaperVenue:
    v = PaperVenue(
        event_bus=event_bus,
        configs=[market_config],
        fill_latency_ms=1.0,  # very fast for tests
        partial_fill_probability=0.0,  # deterministic fills
        seed=42,
    )
    await v.connect()
    yield v  # type: ignore[misc]
    await v.disconnect()


@pytest.fixture
async def paper_exec(
    venue: PaperVenue, event_bus: EventBus
) -> PaperExecution:
    return PaperExecution(venue=venue, event_bus=event_bus, max_orders_per_second=100)


def _make_buy_order(
    market_id: str = "test-mkt-001",
    token_id: str = "test-tok-yes-001",
    price: Decimal = Decimal("0.55"),
    size: Decimal = Decimal("10"),
) -> Order:
    return Order(
        market_id=market_id,
        token_id=token_id,
        side=Side.BUY,
        price=price,
        size=size,
    )


def _make_sell_order(
    market_id: str = "test-mkt-001",
    token_id: str = "test-tok-yes-001",
    price: Decimal = Decimal("0.45"),
    size: Decimal = Decimal("10"),
) -> Order:
    return Order(
        market_id=market_id,
        token_id=token_id,
        side=Side.SELL,
        price=price,
        size=size,
    )


# ══════════════════════════════════════════════════════════════════════
# PaperVenue — Market creation
# ══════════════════════════════════════════════════════════════════════


class TestPaperVenueMarkets:
    """PaperVenue creates and exposes mock markets."""

    @pytest.mark.asyncio
    async def test_get_active_markets_returns_configured(
        self, venue: PaperVenue
    ):
        markets = await venue.get_active_markets()
        assert len(markets) == 1
        assert markets[0].market_id == "test-mkt-001"
        assert isinstance(markets[0], MarketState)

    @pytest.mark.asyncio
    async def test_market_has_correct_ids(self, venue: PaperVenue):
        markets = await venue.get_active_markets()
        m = markets[0]
        assert m.condition_id == "test-cond-001"
        assert m.token_id_yes == "test-tok-yes-001"
        assert m.token_id_no == "test-tok-no-001"

    @pytest.mark.asyncio
    async def test_market_has_valid_prices(self, venue: PaperVenue):
        markets = await venue.get_active_markets()
        m = markets[0]
        assert m.yes_bid > Decimal("0")
        assert m.yes_ask > m.yes_bid
        assert m.tick_size == Decimal("0.01")

    @pytest.mark.asyncio
    async def test_market_has_depth(self, venue: PaperVenue):
        markets = await venue.get_active_markets()
        m = markets[0]
        assert m.depth_yes_bid > Decimal("0")
        assert m.depth_yes_ask > Decimal("0")

    @pytest.mark.asyncio
    async def test_random_market_generation(self, event_bus: EventBus):
        """PaperVenue with no explicit configs generates 5-20 random markets."""
        v = PaperVenue(
            event_bus=event_bus,
            num_random_markets=8,
            fill_latency_ms=1.0,
            seed=123,
        )
        await v.connect()
        try:
            markets = await v.get_active_markets()
            assert 5 <= len(markets) <= 20
            for m in markets:
                assert m.yes_bid >= Decimal("0")
                assert m.tick_size > Decimal("0")
        finally:
            await v.disconnect()

    @pytest.mark.asyncio
    async def test_get_orderbook(self, venue: PaperVenue):
        book = await venue.get_orderbook("test-tok-yes-001")
        assert "bids" in book
        assert "asks" in book
        assert len(book["bids"]) > 0
        assert len(book["asks"]) > 0
        # Prices should be Decimal
        assert isinstance(book["bids"][0]["price"], Decimal)

    @pytest.mark.asyncio
    async def test_get_orderbook_unknown_token(self, venue: PaperVenue):
        book = await venue.get_orderbook("nonexistent-token")
        assert book["bids"] == []
        assert book["asks"] == []


# ══════════════════════════════════════════════════════════════════════
# PaperVenue — Order Submit / Cancel / Fill
# ══════════════════════════════════════════════════════════════════════


class TestPaperVenueOrders:
    """Submit, cancel, fill, and amend orders on PaperVenue."""

    @pytest.mark.asyncio
    async def test_submit_buy_order_opens(self, venue: PaperVenue):
        """A buy at a reasonable price gets accepted (OPEN or FILLED)."""
        order = _make_buy_order(price=Decimal("0.01"))  # far from asks
        result = await venue.submit_order(order)
        assert result.status in (OrderStatus.OPEN, OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)

    @pytest.mark.asyncio
    async def test_submit_buy_order_fills(self, venue: PaperVenue):
        """A buy at a very high price should match against asks."""
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask
        # Buy at the ask price → should fill
        order = _make_buy_order(price=ask, size=Decimal("5"))
        result = await venue.submit_order(order)
        assert result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
        assert result.filled_qty > Decimal("0")

    @pytest.mark.asyncio
    async def test_submit_sell_order(self, venue: PaperVenue):
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask
        bid = markets[0].yes_bid
        # First buy to have a position
        buy_order = _make_buy_order(price=ask, size=Decimal("10"))
        buy_result = await venue.submit_order(buy_order)
        assert buy_result.filled_qty > Decimal("0"), "Need a filled buy first"

        # Now sell from the position
        order = _make_sell_order(price=bid, size=Decimal("5"))
        result = await venue.submit_order(order)
        assert result.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.OPEN)

    @pytest.mark.asyncio
    async def test_cancel_order(self, nofill_venue: PaperVenue):
        order = _make_buy_order(price=Decimal("0.01"))
        result = await nofill_venue.submit_order(order)
        assert result.status == OrderStatus.OPEN

        cancelled = await nofill_venue.cancel_order(result.client_order_id)
        assert cancelled is True

        # Second cancel should return False
        cancelled_again = await nofill_venue.cancel_order(result.client_order_id)
        assert cancelled_again is False

    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self, venue: PaperVenue):
        result = await venue.cancel_order(uuid4())
        assert result is False

    @pytest.mark.asyncio
    async def test_amend_order(self, nofill_venue: PaperVenue):
        order = _make_buy_order(price=Decimal("0.01"))
        result = await nofill_venue.submit_order(order)
        assert result.status == OrderStatus.OPEN

        amended = await nofill_venue.amend_order(
            result.client_order_id,
            new_price=Decimal("0.02"),
            new_size=Decimal("20"),
        )
        assert amended.price == Decimal("0.02")
        assert amended.size == Decimal("20")

    @pytest.mark.asyncio
    async def test_amend_nonexistent_raises(self, venue: PaperVenue):
        with pytest.raises(ValueError, match="not found"):
            await venue.amend_order(uuid4(), Decimal("0.50"), Decimal("10"))

    @pytest.mark.asyncio
    async def test_get_open_orders(self, nofill_venue: PaperVenue):
        order1 = _make_buy_order(price=Decimal("0.01"))
        order2 = _make_buy_order(price=Decimal("0.02"))
        await nofill_venue.submit_order(order1)
        await nofill_venue.submit_order(order2)

        open_orders = await nofill_venue.get_open_orders()
        assert len(open_orders) >= 2

    @pytest.mark.asyncio
    async def test_idempotent_submit(self, nofill_venue: PaperVenue):
        order = _make_buy_order(price=Decimal("0.01"))
        r1 = await nofill_venue.submit_order(order)
        r2 = await nofill_venue.submit_order(order)
        assert r1.client_order_id == r2.client_order_id
        assert r1.status == r2.status

    @pytest.mark.asyncio
    async def test_fill_updates_position(self, venue: PaperVenue):
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask
        order = _make_buy_order(price=ask, size=Decimal("5"))
        result = await venue.submit_order(order)

        if result.filled_qty > Decimal("0"):
            pos = venue.get_position("test-mkt-001")
            assert pos is not None
            assert pos.qty_yes > Decimal("0")


# ══════════════════════════════════════════════════════════════════════
# Tick Size Validation
# ══════════════════════════════════════════════════════════════════════


class TestTickSizeValidation:
    """Orders with prices not aligned to tick size are rejected."""

    def test_is_valid_tick_true(self):
        assert _is_valid_tick(Decimal("0.50"), Decimal("0.01")) is True
        assert _is_valid_tick(Decimal("0.01"), Decimal("0.01")) is True
        assert _is_valid_tick(Decimal("0.99"), Decimal("0.01")) is True

    def test_is_valid_tick_false(self):
        assert _is_valid_tick(Decimal("0.505"), Decimal("0.01")) is False
        assert _is_valid_tick(Decimal("0.015"), Decimal("0.01")) is False
        assert _is_valid_tick(Decimal("0.123"), Decimal("0.01")) is False

    def test_is_valid_tick_small_tick(self):
        assert _is_valid_tick(Decimal("0.501"), Decimal("0.001")) is True
        assert _is_valid_tick(Decimal("0.5015"), Decimal("0.001")) is False

    @pytest.mark.asyncio
    async def test_submit_invalid_tick_rejected(self, venue: PaperVenue):
        """An order with price not aligned to tick_size is rejected."""
        order = _make_buy_order(price=Decimal("0.505"))  # tick=0.01, invalid
        result = await venue.submit_order(order)
        assert result.status == OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_submit_valid_tick_accepted(self, venue: PaperVenue):
        order = _make_buy_order(price=Decimal("0.01"))  # tick=0.01, valid
        result = await venue.submit_order(order)
        assert result.status != OrderStatus.REJECTED

    @pytest.mark.asyncio
    async def test_amend_invalid_tick_raises(self, nofill_venue: PaperVenue):
        order = _make_buy_order(price=Decimal("0.01"))
        result = await nofill_venue.submit_order(order)
        with pytest.raises(ValueError, match="not a valid tick"):
            await nofill_venue.amend_order(
                result.client_order_id,
                new_price=Decimal("0.015"),
                new_size=Decimal("10"),
            )

    @pytest.mark.asyncio
    async def test_unknown_market_rejected(self, venue: PaperVenue):
        order = _make_buy_order(market_id="nonexistent-mkt")
        result = await venue.submit_order(order)
        assert result.status == OrderStatus.REJECTED


# ══════════════════════════════════════════════════════════════════════
# ChaosInjector
# ══════════════════════════════════════════════════════════════════════


class TestChaosInjector:
    """ChaosInjector publishes chaos events to the EventBus."""

    @pytest.mark.asyncio
    async def test_chaos_disabled_no_events(self, venue: PaperVenue, event_bus: EventBus):
        """When disabled, no chaos events are published."""
        config = ChaosConfig(enabled=False, cycle_interval_s=0.05)
        injector = ChaosInjector(venue, event_bus, config=config, seed=1)

        chaos_events: list[Event] = []

        async def collector():
            async for ev in event_bus.subscribe("chaos"):
                chaos_events.append(ev)

        task = asyncio.create_task(collector())
        await injector.start()
        await asyncio.sleep(0.3)
        await injector.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(chaos_events) == 0

    @pytest.mark.asyncio
    async def test_chaos_enabled_produces_events(
        self, venue: PaperVenue, event_bus: EventBus
    ):
        """With high probabilities, chaos events should fire."""
        config = ChaosConfig(
            enabled=True,
            tick_size_change_prob=0.0,  # skip (needs extreme price)
            engine_restart_prob=0.0,  # skip (too slow)
            ws_disconnect_prob=1.0,  # always fires
            latency_spike_prob=1.0,  # always fires
            cycle_interval_s=0.05,
        )
        injector = ChaosInjector(venue, event_bus, config=config, seed=2)

        chaos_events: list[Event] = []

        async def collector():
            async for ev in event_bus.subscribe("chaos"):
                chaos_events.append(ev)
                if len(chaos_events) >= 3:
                    break

        task = asyncio.create_task(collector())
        await injector.start()
        await asyncio.sleep(1.0)
        await injector.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(chaos_events) >= 2
        types = {e.payload.get("type") for e in chaos_events}
        # At least ws_disconnect or latency_spike should be present
        assert types & {"ws_disconnect", "latency_spike"}

    @pytest.mark.asyncio
    async def test_engine_restart_pauses_matching(
        self, venue: PaperVenue, event_bus: EventBus
    ):
        """ENGINE_RESTART should pause and then resume matching."""
        config = ChaosConfig(
            enabled=True,
            tick_size_change_prob=0.0,
            engine_restart_prob=1.0,
            ws_disconnect_prob=0.0,
            latency_spike_prob=0.0,
            engine_restart_duration_s=0.1,
            cycle_interval_s=0.05,
        )
        injector = ChaosInjector(venue, event_bus, config=config, seed=3)

        chaos_events: list[Event] = []

        async def collector():
            async for ev in event_bus.subscribe("chaos"):
                chaos_events.append(ev)
                if len(chaos_events) >= 2:
                    break

        task = asyncio.create_task(collector())
        await injector.start()
        await asyncio.sleep(0.5)
        await injector.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        restart_events = [
            e for e in chaos_events if e.payload.get("type") == "engine_restart"
        ]
        assert len(restart_events) >= 2
        statuses = [e.payload.get("status") for e in restart_events]
        assert "paused" in statuses
        assert "resumed" in statuses

    @pytest.mark.asyncio
    async def test_chaos_config_setter(
        self, venue: PaperVenue, event_bus: EventBus
    ):
        injector = ChaosInjector(venue, event_bus, seed=4)
        new_config = ChaosConfig(enabled=False)
        injector.config = new_config
        assert injector.config.enabled is False

    @pytest.mark.asyncio
    async def test_tick_size_change_event(
        self, venue: PaperVenue, event_bus: EventBus
    ):
        """Directly invoke tick size change via venue and verify."""
        venue.change_tick_size("test-mkt-001", Decimal("0.001"))
        markets = await venue.get_active_markets()
        m = [ms for ms in markets if ms.market_id == "test-mkt-001"][0]
        assert m.tick_size == Decimal("0.001")


# ══════════════════════════════════════════════════════════════════════
# PaperExecution
# ══════════════════════════════════════════════════════════════════════


class TestPaperExecution:
    """PaperExecution wraps PaperVenue with idempotency and rate limiting."""

    @pytest.mark.asyncio
    async def test_submit_order(self, paper_exec: PaperExecution):
        order = _make_buy_order(price=Decimal("0.01"))
        result = await paper_exec.submit_order(order)
        assert result.status in (OrderStatus.OPEN, OrderStatus.FILLED)

    @pytest.mark.asyncio
    async def test_idempotent_submit(self, paper_exec: PaperExecution):
        order = _make_buy_order(price=Decimal("0.01"))
        r1 = await paper_exec.submit_order(order)
        r2 = await paper_exec.submit_order(order)
        assert r1.client_order_id == r2.client_order_id

    @pytest.mark.asyncio
    async def test_cancel_order(self, nofill_paper_exec: PaperExecution):
        order = _make_buy_order(price=Decimal("0.01"))
        result = await nofill_paper_exec.submit_order(order)
        success = await nofill_paper_exec.cancel_order(result.client_order_id)
        assert success is True

    @pytest.mark.asyncio
    async def test_get_open_orders(self, nofill_paper_exec: PaperExecution):
        order = _make_buy_order(price=Decimal("0.01"))
        await nofill_paper_exec.submit_order(order)
        open_orders = await nofill_paper_exec.get_open_orders()
        assert len(open_orders) >= 1

    @pytest.mark.asyncio
    async def test_rate_limiting(self, venue: PaperVenue, event_bus: EventBus):
        """With max_orders_per_second=2, the 3rd order should be rejected."""
        exec_limited = PaperExecution(
            venue=venue, event_bus=event_bus, max_orders_per_second=2
        )
        results = []
        for _ in range(5):
            order = _make_buy_order(price=Decimal("0.01"))
            r = await exec_limited.submit_order(order)
            results.append(r)

        rejected = [r for r in results if r.status == OrderStatus.REJECTED]
        assert len(rejected) >= 1, "Rate limiting should reject some orders"

    @pytest.mark.asyncio
    async def test_implements_execution_provider(
        self, paper_exec: PaperExecution
    ):
        """PaperExecution is a proper ExecutionProvider."""
        assert isinstance(paper_exec, ExecutionProvider)


# ══════════════════════════════════════════════════════════════════════
# ExecutionProvider ABC
# ══════════════════════════════════════════════════════════════════════


class TestExecutionProviderABC:
    """ExecutionProvider cannot be instantiated directly."""

    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            ExecutionProvider()  # type: ignore[abstract]


# ══════════════════════════════════════════════════════════════════════
# ReplayEngine (stub)
# ══════════════════════════════════════════════════════════════════════


class TestReplayEngine:
    """ReplayEngine stub has the correct interface."""

    @pytest.mark.asyncio
    async def test_connect_disconnect(self, event_bus: EventBus):
        engine = ReplayEngine(event_bus)
        await engine.connect()
        await engine.disconnect()

    @pytest.mark.asyncio
    async def test_get_active_markets_empty(self, event_bus: EventBus):
        engine = ReplayEngine(event_bus)
        await engine.connect()
        markets = await engine.get_active_markets()
        assert markets == []
        await engine.disconnect()

    @pytest.mark.asyncio
    async def test_get_orderbook_empty(self, event_bus: EventBus):
        engine = ReplayEngine(event_bus)
        await engine.connect()
        book = await engine.get_orderbook("any-token")
        assert book["bids"] == []
        assert book["asks"] == []
        await engine.disconnect()


# ══════════════════════════════════════════════════════════════════════
# Heartbeat
# ══════════════════════════════════════════════════════════════════════


class TestHeartbeat:
    """PaperVenue publishes heartbeat events."""

    @pytest.mark.asyncio
    async def test_heartbeat_published(self, event_bus: EventBus):
        venue = PaperVenue(
            event_bus=event_bus,
            configs=[
                MarketSimConfig(
                    market_id="hb-test",
                    condition_id="hb-cond",
                    token_id_yes="hb-yes",
                    token_id_no="hb-no",
                    initial_yes_mid=Decimal("0.50"),
                )
            ],
            heartbeat_interval_s=0.1,
            fill_latency_ms=1.0,
            seed=99,
        )

        heartbeats: list[Event] = []

        async def collector():
            async for ev in event_bus.subscribe("heartbeat"):
                heartbeats.append(ev)
                if len(heartbeats) >= 2:
                    break

        task = asyncio.create_task(collector())
        await venue.connect()
        await asyncio.sleep(0.5)
        await venue.disconnect()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(heartbeats) >= 1
        assert heartbeats[0].payload["source"] == "paper_venue"


# ══════════════════════════════════════════════════════════════════════
# Position and PnL tracking
# ══════════════════════════════════════════════════════════════════════


class TestPositionTracking:
    """PaperVenue tracks positions and PnL in memory."""

    @pytest.mark.asyncio
    async def test_initial_position_zero(self, venue: PaperVenue):
        pos = venue.get_position("test-mkt-001")
        assert pos is not None
        assert pos.qty_yes == Decimal("0")
        assert pos.qty_no == Decimal("0")

    @pytest.mark.asyncio
    async def test_get_all_positions(self, venue: PaperVenue):
        positions = venue.get_all_positions()
        assert "test-mkt-001" in positions

    @pytest.mark.asyncio
    async def test_total_pnl_starts_zero(self, venue: PaperVenue):
        assert venue.total_pnl == Decimal("0")

    @pytest.mark.asyncio
    async def test_position_for_unknown_market(self, venue: PaperVenue):
        assert venue.get_position("nonexistent") is None


# ══════════════════════════════════════════════════════════════════════
# SELL position sync — BUY then SELL flow (bug fix verification)
# ══════════════════════════════════════════════════════════════════════


class TestSellPositionSync:
    """Verify that SELL YES works after BUY YES fills (position sync fix)."""

    @pytest.mark.asyncio
    async def test_buy_then_sell_yes_accepted(self, venue: PaperVenue):
        """After buying YES, selling YES should be accepted by venue."""
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask

        # Step 1: BUY YES — should fill
        buy = _make_buy_order(price=ask, size=Decimal("10"))
        buy_result = await venue.submit_order(buy)
        assert buy_result.filled_qty > Decimal("0"), "BUY should fill"

        # Verify venue position updated
        pos = venue.get_position("test-mkt-001")
        assert pos is not None
        assert pos.qty_yes > Decimal("0"), f"venue qty_yes should be >0, got {pos.qty_yes}"

        # Step 2: SELL YES — should NOT be rejected
        bid = markets[0].yes_bid
        sell_size = min(pos.qty_yes, Decimal("5"))
        sell = _make_sell_order(price=bid, size=sell_size)
        sell_result = await venue.submit_order(sell)
        assert sell_result.status != OrderStatus.REJECTED, (
            f"SELL YES should not be rejected! status={sell_result.status}, "
            f"held_yes={pos.qty_yes}"
        )

    @pytest.mark.asyncio
    async def test_sell_resized_to_held(self, venue: PaperVenue):
        """SELL larger than held position is resized to held qty."""
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask

        # BUY 5
        buy = _make_buy_order(price=ask, size=Decimal("5"))
        buy_result = await venue.submit_order(buy)
        assert buy_result.filled_qty > Decimal("0")

        pos = venue.get_position("test-mkt-001")
        held = pos.qty_yes

        # Try to SELL 100 — more than we have
        bid = markets[0].yes_bid
        sell = _make_sell_order(price=bid, size=Decimal("100"))
        sell_result = await venue.submit_order(sell)
        # Should NOT be rejected — should be resized to held
        assert sell_result.status != OrderStatus.REJECTED, (
            f"SELL should be resized, not rejected. held={held}"
        )

    @pytest.mark.asyncio
    async def test_sell_with_zero_position_rejected(
        self, venue: PaperVenue
    ):
        """SELL YES with 0 position is rejected in paper trading."""
        markets = await venue.get_active_markets()
        bid = markets[0].yes_bid

        # Verify position is 0
        pos = venue.get_position("test-mkt-001")
        assert pos.qty_yes == Decimal("0")

        # Try SELL YES at bid price — should be rejected (no complement routing)
        sell = _make_sell_order(
            price=bid, size=Decimal("5"), token_id="test-tok-yes-001"
        )
        sell_result = await venue.submit_order(sell)
        assert sell_result.status == OrderStatus.REJECTED, (
            f"SELL YES with 0 position should be rejected in paper, "
            f"got {sell_result.status}"
        )

    @pytest.mark.asyncio
    async def test_venue_position_matches_after_multiple_fills(
        self, venue: PaperVenue
    ):
        """Multiple BUY fills accumulate in venue position correctly."""
        markets = await venue.get_active_markets()
        ask = markets[0].yes_ask

        total_bought = Decimal("0")
        for _ in range(3):
            buy = _make_buy_order(price=ask, size=Decimal("5"))
            result = await venue.submit_order(buy)
            total_bought += result.filled_qty

        pos = venue.get_position("test-mkt-001")
        assert pos.qty_yes == total_bought, (
            f"venue position ({pos.qty_yes}) should match total bought ({total_bought})"
        )


@pytest.fixture
def nofill_market_config() -> MarketSimConfig:
    """Market config with fill_probability=0 so orders stay OPEN."""
    return MarketSimConfig(
        market_id="test-mkt-001",
        condition_id="test-cond-001",
        token_id_yes="test-tok-yes-001",
        token_id_no="test-tok-no-001",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        initial_yes_mid=Decimal("0.50"),
        volatility=Decimal("0.005"),
        fill_probability=0.0,  # orders never fill — stay OPEN
    )


@pytest.fixture
async def nofill_venue(event_bus: EventBus, nofill_market_config: MarketSimConfig) -> PaperVenue:
    v = PaperVenue(
        event_bus=event_bus,
        configs=[nofill_market_config],
        fill_latency_ms=1.0,
        partial_fill_probability=0.0,
        seed=42,
    )
    await v.connect()
    yield v  # type: ignore[misc]
    await v.disconnect()


@pytest.fixture
async def nofill_paper_exec(
    nofill_venue: PaperVenue, event_bus: EventBus
) -> PaperExecution:
    return PaperExecution(venue=nofill_venue, event_bus=event_bus, max_orders_per_second=100)
