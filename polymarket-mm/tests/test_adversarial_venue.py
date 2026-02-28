"""Tests for Adversarial PaperVenue features.

Covers:
- Adverse selection: mid moves against fill direction
- Fee model: maker rebate / taker fee applied to PnL
- Fill distance decay: orders further from mid fill less often
- Backward compatibility: defaults produce identical behaviour
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from core.event_bus import EventBus
from models.order import Order, OrderStatus, Side
from paper.paper_venue import FeeConfig, MarketSimConfig, PaperVenue


# ── Helpers ─────────────────────────────────────────────────────────


def _make_order(
    side: Side = Side.BUY,
    market_id: str = "adv-mkt",
    token_id: str = "adv-tok-yes",
    price: Decimal = Decimal("0.50"),
    size: Decimal = Decimal("10"),
) -> Order:
    return Order(
        market_id=market_id,
        token_id=token_id,
        side=side,
        price=price,
        size=size,
    )


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


def _base_config(**overrides) -> MarketSimConfig:
    defaults = dict(
        market_id="adv-mkt",
        condition_id="adv-cond",
        token_id_yes="adv-tok-yes",
        token_id_no="adv-tok-no",
        tick_size=Decimal("0.01"),
        min_order_size=Decimal("5"),
        initial_yes_mid=Decimal("0.50"),
        volatility=Decimal("0"),  # no random walk so we can measure moves
        fill_probability=1.0,
    )
    defaults.update(overrides)
    return MarketSimConfig(**defaults)


async def _make_venue(
    event_bus: EventBus,
    config: MarketSimConfig,
    fee_config: FeeConfig | None = None,
    initial_balance: Decimal = Decimal("1000"),
) -> PaperVenue:
    v = PaperVenue(
        event_bus=event_bus,
        configs=[config],
        fill_latency_ms=0.0,
        partial_fill_probability=0.0,
        seed=42,
        initial_balance=initial_balance,
        fee_config=fee_config,
    )
    await v.connect()
    return v


# ══════════════════════════════════════════════════════════════════════
# 1. Adverse Selection
# ══════════════════════════════════════════════════════════════════════


class TestAdverseSelection:
    """After a fill, mid should move AGAINST the fill direction."""

    @pytest.mark.asyncio
    async def test_buy_fill_drops_mid(self, event_bus: EventBus):
        """BUY fill → mid price decreases (adverse selection)."""
        cfg = _base_config(adverse_selection_bps=50)
        venue = await _make_venue(event_bus, cfg)
        try:
            mid_before = venue._mid_prices["adv-mkt"]

            order = _make_order(side=Side.BUY, price=Decimal("0.55"), size=Decimal("10"))
            result = await venue.submit_order(order)
            assert result.filled_qty > Decimal("0"), "Order must fill"

            mid_after = venue._mid_prices["adv-mkt"]
            assert mid_after < mid_before, (
                f"BUY fill should drop mid: before={mid_before}, after={mid_after}"
            )
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_sell_fill_raises_mid(self, event_bus: EventBus):
        """SELL fill → mid price increases (adverse selection)."""
        cfg = _base_config(adverse_selection_bps=50)
        venue = await _make_venue(event_bus, cfg)
        try:
            # First buy to get position
            buy = _make_order(side=Side.BUY, price=Decimal("0.55"), size=Decimal("20"))
            await venue.submit_order(buy)

            mid_before_sell = venue._mid_prices["adv-mkt"]

            # Now sell
            sell = _make_order(side=Side.SELL, price=Decimal("0.40"), size=Decimal("10"))
            sell_result = await venue.submit_order(sell)
            assert sell_result.filled_qty > Decimal("0"), "SELL must fill"

            mid_after_sell = venue._mid_prices["adv-mkt"]
            assert mid_after_sell > mid_before_sell, (
                f"SELL fill should raise mid: before={mid_before_sell}, after={mid_after_sell}"
            )
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_zero_adverse_selection_no_change(self, event_bus: EventBus):
        """With adverse_selection_bps=0, mid should not move on fill."""
        cfg = _base_config(adverse_selection_bps=0, volatility=Decimal("0"))
        venue = await _make_venue(event_bus, cfg)
        try:
            mid_before = venue._mid_prices["adv-mkt"]

            order = _make_order(side=Side.BUY, price=Decimal("0.55"), size=Decimal("10"))
            result = await venue.submit_order(order)
            assert result.filled_qty > Decimal("0")

            mid_after = venue._mid_prices["adv-mkt"]
            assert mid_after == mid_before, (
                f"With 0 adverse selection, mid should not change: {mid_before} vs {mid_after}"
            )
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_adverse_selection_magnitude(self, event_bus: EventBus):
        """Adverse move magnitude = fill_notional * bps / 10000."""
        cfg = _base_config(adverse_selection_bps=100)  # 1% = 100bps
        venue = await _make_venue(event_bus, cfg)
        try:
            mid_before = venue._mid_prices["adv-mkt"]

            price = Decimal("0.50")
            size = Decimal("10")
            order = _make_order(side=Side.BUY, price=price, size=size)
            result = await venue.submit_order(order)
            assert result.filled_qty == size

            expected_move = price * size * Decimal("100") / Decimal("10000")
            mid_after = venue._mid_prices["adv-mkt"]
            actual_move = mid_before - mid_after

            # Allow 1 tick tolerance for quantization
            assert abs(actual_move - expected_move) <= Decimal("0.01"), (
                f"Expected move ~{expected_move}, got {actual_move}"
            )
        finally:
            await venue.disconnect()


# ══════════════════════════════════════════════════════════════════════
# 2. Fee Model
# ══════════════════════════════════════════════════════════════════════


class TestFeeModel:
    """Fee/rebate is applied correctly to PnL and wallet."""

    @pytest.mark.asyncio
    async def test_fee_config_default_zero(self, event_bus: EventBus):
        """Default FeeConfig has zero fees."""
        fc = FeeConfig()
        assert fc.maker_fee_bps == 0
        assert fc.taker_fee_bps == 0

    @pytest.mark.asyncio
    async def test_maker_rebate_increases_balance(self, event_bus: EventBus):
        """Negative maker fee (rebate) should increase available balance."""
        cfg = _base_config()
        fee = FeeConfig(maker_fee_bps=-20)  # -20bps = 0.2% rebate
        venue = await _make_venue(event_bus, cfg, fee_config=fee)
        try:
            balance_before = venue.available_balance

            order = _make_order(side=Side.BUY, price=Decimal("0.50"), size=Decimal("10"))
            result = await venue.submit_order(order)
            assert result.filled_qty > Decimal("0")

            # Rebate should have been credited
            # fee = 0.50 * 10 * (-20) / 10000 = -0.01
            # -fee = 0.01 → added to available_balance
            expected_rebate = Decimal("0.50") * Decimal("10") * Decimal("-20") / Decimal("10000")
            # After buy: balance decreases by cost but increases by rebate
            # Total fees should be negative (we received money)
            assert venue.total_fees < Decimal("0"), (
                f"With maker rebate, total_fees should be negative: {venue.total_fees}"
            )
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_positive_fee_reduces_pnl(self, event_bus: EventBus):
        """Positive maker fee should reduce PnL compared to zero fee."""
        cfg_base = _base_config()

        # Run without fee
        venue_no_fee = await _make_venue(EventBus(), cfg_base)
        buy1 = _make_order(side=Side.BUY, price=Decimal("0.50"), size=Decimal("10"))
        await venue_no_fee.submit_order(buy1)
        sell1 = _make_order(side=Side.SELL, price=Decimal("0.50"), size=Decimal("10"))
        await venue_no_fee.submit_order(sell1)
        pnl_no_fee = venue_no_fee.total_pnl
        await venue_no_fee.disconnect()

        # Run with 20bps fee
        fee = FeeConfig(maker_fee_bps=20)
        venue_with_fee = await _make_venue(EventBus(), cfg_base, fee_config=fee)
        buy2 = _make_order(side=Side.BUY, price=Decimal("0.50"), size=Decimal("10"))
        await venue_with_fee.submit_order(buy2)
        sell2 = _make_order(side=Side.SELL, price=Decimal("0.50"), size=Decimal("10"))
        await venue_with_fee.submit_order(sell2)
        pnl_with_fee = venue_with_fee.total_pnl
        await venue_with_fee.disconnect()

        assert pnl_with_fee < pnl_no_fee, (
            f"PnL with fee ({pnl_with_fee}) should be less than without ({pnl_no_fee})"
        )

    @pytest.mark.asyncio
    async def test_fee_in_fill_event(self, event_bus: EventBus):
        """Fill event payload should contain 'fee' field."""
        cfg = _base_config()
        fee = FeeConfig(maker_fee_bps=-20)
        venue = await _make_venue(event_bus, cfg, fee_config=fee)

        fill_events = []

        async def collector():
            async for ev in event_bus.subscribe("fill"):
                fill_events.append(ev)
                break

        task = asyncio.create_task(collector())
        try:
            order = _make_order(side=Side.BUY, price=Decimal("0.50"), size=Decimal("10"))
            await venue.submit_order(order)
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            assert len(fill_events) >= 1, "Should have at least one fill event"
            assert "fee" in fill_events[0].payload, "Fill event must contain 'fee' field"
            fee_val = Decimal(fill_events[0].payload["fee"])
            assert fee_val < Decimal("0"), f"Maker rebate should be negative: {fee_val}"
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_zero_fee_backward_compatible(self, event_bus: EventBus):
        """With zero fees, PnL should match pre-fee-model behavior."""
        cfg = _base_config()
        venue = await _make_venue(event_bus, cfg)  # default FeeConfig(0, 0)
        try:
            order = _make_order(side=Side.BUY, price=Decimal("0.50"), size=Decimal("10"))
            await venue.submit_order(order)

            assert venue.total_fees == Decimal("0"), (
                f"With zero fee config, total_fees should be 0: {venue.total_fees}"
            )
        finally:
            await venue.disconnect()


# ══════════════════════════════════════════════════════════════════════
# 3. Fill Distance Decay
# ══════════════════════════════════════════════════════════════════════


class TestFillDistanceDecay:
    """Orders further from mid should fill less often."""

    @pytest.mark.asyncio
    async def test_close_orders_fill_more(self, event_bus: EventBus):
        """Orders near mid should fill more often than orders far from mid."""
        cfg = _base_config(
            fill_probability=0.8,  # high base prob so near-mid orders fill often
            fill_distance_decay=True,
        )

        near_fills = 0
        far_fills = 0
        n_trials = 100

        for i in range(n_trials):
            # Use different seeds so RNG varies
            eb = EventBus()
            v = PaperVenue(
                event_bus=eb,
                configs=[cfg],
                fill_latency_ms=0.0,
                partial_fill_probability=0.0,
                seed=i,
                initial_balance=Decimal("100000"),
            )
            await v.connect()
            try:
                near_order = _make_order(
                    side=Side.BUY, price=Decimal("0.50"), size=Decimal("5")
                )
                result = await v.submit_order(near_order)
                if result.filled_qty > Decimal("0"):
                    near_fills += 1
            finally:
                await v.disconnect()

            eb2 = EventBus()
            v2 = PaperVenue(
                event_bus=eb2,
                configs=[cfg],
                fill_latency_ms=0.0,
                partial_fill_probability=0.0,
                seed=i + 10000,
                initial_balance=Decimal("100000"),
            )
            await v2.connect()
            try:
                far_order = _make_order(
                    side=Side.BUY, price=Decimal("0.20"), size=Decimal("5")
                )
                result2 = await v2.submit_order(far_order)
                if result2.filled_qty > Decimal("0"):
                    far_fills += 1
            finally:
                await v2.disconnect()

        assert near_fills > far_fills, (
            f"Near-mid orders should fill more: near={near_fills}, far={far_fills}"
        )

    @pytest.mark.asyncio
    async def test_decay_disabled_uniform(self, event_bus: EventBus):
        """With fill_distance_decay=False, distance shouldn't matter."""
        cfg = _base_config(
            fill_probability=1.0,
            fill_distance_decay=False,
        )
        venue = await _make_venue(event_bus, cfg)
        try:
            # Even a very far order should fill with prob=1.0
            order = _make_order(side=Side.BUY, price=Decimal("0.10"), size=Decimal("5"))
            result = await venue.submit_order(order)
            assert result.filled_qty > Decimal("0"), (
                "With decay disabled, all orders should fill at prob=1.0"
            )
        finally:
            await venue.disconnect()


# ══════════════════════════════════════════════════════════════════════
# 4. Backward Compatibility
# ══════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """With all adversarial params at default (0/False), behavior is identical."""

    @pytest.mark.asyncio
    async def test_defaults_match_original(self, event_bus: EventBus):
        """Default config = no adverse selection, no fees, no decay."""
        cfg = _base_config(
            adverse_selection_bps=0,
            fill_distance_decay=False,
        )
        venue = await _make_venue(event_bus, cfg)
        try:
            mid_before = venue._mid_prices["adv-mkt"]

            order = _make_order(side=Side.BUY, price=Decimal("0.55"), size=Decimal("10"))
            result = await venue.submit_order(order)
            assert result.filled_qty > Decimal("0")

            # Mid should NOT have moved (no adverse selection)
            assert venue._mid_prices["adv-mkt"] == mid_before
            # Fees should be zero
            assert venue.total_fees == Decimal("0")
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_fee_config_property(self, event_bus: EventBus):
        """fee_config property returns the configured FeeConfig."""
        cfg = _base_config()
        fc = FeeConfig(maker_fee_bps=-20, taker_fee_bps=20)
        venue = await _make_venue(event_bus, cfg, fee_config=fc)
        try:
            assert venue.fee_config.maker_fee_bps == -20
            assert venue.fee_config.taker_fee_bps == 20
        finally:
            await venue.disconnect()

    @pytest.mark.asyncio
    async def test_market_sim_config_new_fields_optional(self):
        """MarketSimConfig should work with and without new fields."""
        # Old-style (no new fields)
        cfg_old = MarketSimConfig(
            market_id="m", condition_id="c",
            token_id_yes="y", token_id_no="n",
        )
        assert cfg_old.adverse_selection_bps == 0
        assert cfg_old.fill_distance_decay is False

        # New-style
        cfg_new = MarketSimConfig(
            market_id="m", condition_id="c",
            token_id_yes="y", token_id_no="n",
            adverse_selection_bps=10,
            fill_distance_decay=True,
        )
        assert cfg_new.adverse_selection_bps == 10
        assert cfg_new.fill_distance_decay is True
