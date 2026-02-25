"""QuoteEngine — generates bilateral QuotePlans from market signals.

The QuoteEngine is the central orchestrator of the quoting strategy.
It combines four sub-models:

1. **SpreadModel** — optimal half-spread = f(volatility, fee, liquidity)
2. **InventorySkew** — Avellaneda-Stoikov skew to mean-revert inventory
3. **RewardsFarming** — tighten spreads to maximise liquidity rewards
4. **ToxicFlowDetector** — widen or halt when informed flow is detected

The output is a ``QuotePlan`` containing bilateral slices (bid YES,
ask YES, bid NO, ask NO) that can be converted to Order intents and
submitted to the CLOB.

Flow::

    MarketState + FeatureVector + Position
        → SpreadModel.optimal_half_spread()
        → InventorySkew.compute_skew()
        → RewardsFarming.adjust_half_spread()
        → ToxicFlowDetector (halt check / spread widening)
        → QuotePlan
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN
from typing import Optional

import structlog

from models.feature_vector import FeatureVector
from models.market_state import MarketState
from models.position import Position
from models.quote_plan import QuotePlan, QuoteSlice, QuoteSide, TokenSide
from strategy.inventory_skew import InventorySkew, InventorySkewConfig
from strategy.rewards_farming import RewardsFarming, RewardsFarmingConfig
from strategy.spread_model import SpreadModel, SpreadModelConfig
from strategy.toxic_flow_detector import ToxicFlowDetector, ToxicFlowConfig

logger = structlog.get_logger("strategy.quote_engine")

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS_DIVISOR = Decimal("10000")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class QuoteEngineConfig:
    """Tunable parameters for the QuoteEngine."""

    # Default order size per slice (in shares)
    default_order_size: Decimal = Decimal("50")

    # Number of price levels to quote on each side (bid/ask)
    num_levels: int = 1

    # Level spacing in price units (distance between consecutive levels)
    level_spacing: Decimal = Decimal("0.005")

    # TTL for each slice in milliseconds
    default_ttl_ms: int = 30_000

    # Minimum price for any quote (Polymarket: > 0)
    price_floor: Decimal = Decimal("0.01")

    # Maximum price for any quote (Polymarket binary: < 1.0)
    price_ceiling: Decimal = Decimal("0.99")

    # Toxic flow spread multiplier: when toxic flow detected (but not halt),
    # multiply the half-spread by this factor.
    toxic_spread_multiplier: Decimal = Decimal("2.0")

    # Minimum data quality score to generate quotes.
    # Below this, the engine returns an empty QuotePlan.
    min_data_quality: float = 0.3

    # Strategy tag for generated quotes
    strategy_tag: str = "quote_engine_v1"


# ── QuoteEngine ──────────────────────────────────────────────────────


class QuoteEngine:
    """Generates bilateral QuotePlans combining spread, skew, rewards, and toxicity.

    Usage::

        engine = QuoteEngine(
            spread_model=SpreadModel(),
            inventory_skew=InventorySkew(),
            rewards_farming=RewardsFarming(),
            toxic_flow=ToxicFlowDetector(),
        )

        plan = engine.generate_quotes(
            state=market_state,
            features=feature_vector,
            position=position,
            elapsed_hours=Decimal("6"),
        )

        orders = plan.to_order_intents()
    """

    def __init__(
        self,
        spread_model: SpreadModel | None = None,
        inventory_skew: InventorySkew | None = None,
        rewards_farming: RewardsFarming | None = None,
        toxic_flow: ToxicFlowDetector | None = None,
        config: QuoteEngineConfig | None = None,
    ) -> None:
        self._spread = spread_model or SpreadModel()
        self._skew = inventory_skew or InventorySkew()
        self._rewards = rewards_farming or RewardsFarming()
        self._toxic = toxic_flow or ToxicFlowDetector()
        self._config = config or QuoteEngineConfig()

    @property
    def config(self) -> QuoteEngineConfig:
        """Return current configuration (read-only)."""
        return self._config

    @property
    def spread_model(self) -> SpreadModel:
        """Access the spread model sub-component."""
        return self._spread

    @property
    def inventory_skew(self) -> InventorySkew:
        """Access the inventory skew sub-component."""
        return self._skew

    @property
    def rewards_farming(self) -> RewardsFarming:
        """Access the rewards farming sub-component."""
        return self._rewards

    @property
    def toxic_flow(self) -> ToxicFlowDetector:
        """Access the toxic flow detector sub-component."""
        return self._toxic

    def generate_quotes(
        self,
        state: MarketState,
        features: FeatureVector,
        position: Position | None = None,
        elapsed_hours: Decimal = _ZERO,
    ) -> QuotePlan:
        """Generate a bilateral QuotePlan for the given market.

        Parameters
        ----------
        state:
            Current MarketState snapshot with bid/ask/depth data.
        features:
            Computed FeatureVector with volatility, imbalance, etc.
        position:
            Current position in this market. If None, assumes flat.
        elapsed_hours:
            Hours elapsed in the current time horizon (for A-S skew).

        Returns
        -------
        QuotePlan
            Plan with bid/ask slices for YES and NO tokens. May be empty
            if data quality is too low or toxic flow triggers a halt.
        """
        c = self._config
        mkt = state.market_id

        # Create empty plan (will be populated or returned empty)
        plan = QuotePlan(
            market_id=mkt,
            token_id_yes=state.token_id_yes,
            token_id_no=state.token_id_no,
            strategy_tag=c.strategy_tag,
        )

        # ── Gate 1: Data quality check ───────────────────────────
        if features.data_quality_score < c.min_data_quality:
            logger.warning(
                "quote_engine.low_data_quality",
                market_id=mkt,
                quality=features.data_quality_score,
                threshold=c.min_data_quality,
            )
            return plan

        # ── Gate 2: Toxic flow halt check ────────────────────────
        self._toxic.update(features)
        if self._toxic.should_halt(features):
            logger.warning(
                "quote_engine.toxic_halt",
                market_id=mkt,
                toxic_score=features.toxic_flow_score,
            )
            return plan

        # ── Gate 3: Need a valid mid-price ───────────────────────
        mid_price = state.mid_price
        if mid_price <= _ZERO:
            logger.warning("quote_engine.no_mid_price", market_id=mkt)
            return plan

        # ── Gate 4: Inventory hard limit ─────────────────────────
        if position is not None and self._skew.is_inventory_exceeded(position):
            logger.warning(
                "quote_engine.inventory_exceeded",
                market_id=mkt,
                qty_yes=str(position.qty_yes),
                qty_no=str(position.qty_no),
            )
            return plan

        # ── Step 1: Optimal half-spread ──────────────────────────
        volatility = Decimal(str(features.volatility_1m))
        half_spread = self._spread.optimal_half_spread(
            volatility=volatility,
            fee_bps=features.expected_fee_bps,
            liquidity_score=features.liquidity_score,
            mid_price=mid_price,
        )

        # ── Step 2: Toxic flow widening (not halt) ───────────────
        is_toxic = self._toxic.is_toxic(features)
        if is_toxic:
            half_spread = half_spread * c.toxic_spread_multiplier
            logger.info(
                "quote_engine.toxic_widening",
                market_id=mkt,
                widened_hs=str(half_spread),
            )

        # ── Step 3: Rewards farming tightening ───────────────────
        if not is_toxic:
            half_spread = self._rewards.adjust_half_spread(
                base_half_spread=half_spread,
                mid_price=mid_price,
                fee_bps=features.expected_fee_bps,
            )

        # ── Step 4: Inventory skew ───────────────────────────────
        skew = _ZERO
        if position is not None:
            skew = self._skew.compute_skew(
                position=position,
                volatility=volatility,
                elapsed_hours=elapsed_hours,
            )

        # Adjusted mid: shift towards offloading inventory
        adjusted_mid = mid_price - skew

        # ── Step 5: Build YES slices ─────────────────────────────
        yes_slices = self._build_slices(
            adjusted_mid=adjusted_mid,
            half_spread=half_spread,
            token=TokenSide.YES,
            tick_size=state.tick_size,
            min_order_size=state.min_order_size,
        )
        plan.slices.extend(yes_slices)

        # ── Step 6: Build NO slices (complement pricing) ─────────
        no_slices = self._build_no_slices(
            adjusted_mid=adjusted_mid,
            half_spread=half_spread,
            tick_size=state.tick_size,
            min_order_size=state.min_order_size,
        )
        plan.slices.extend(no_slices)

        logger.info(
            "quote_engine.plan_generated",
            market_id=mkt,
            mid=str(mid_price),
            adjusted_mid=str(adjusted_mid),
            half_spread=str(half_spread),
            skew=str(skew),
            num_slices=len(plan.slices),
            is_toxic=is_toxic,
        )

        return plan

    # ── Slice builders ───────────────────────────────────────────

    def _build_slices(
        self,
        adjusted_mid: Decimal,
        half_spread: Decimal,
        token: TokenSide,
        tick_size: Decimal,
        min_order_size: Decimal,
    ) -> list[QuoteSlice]:
        """Build bid and ask slices for a given token side."""
        c = self._config
        slices: list[QuoteSlice] = []

        for level in range(c.num_levels):
            level_offset = c.level_spacing * Decimal(str(level))

            # Bid: below adjusted mid
            bid_price = adjusted_mid - half_spread - level_offset
            bid_price = self._quantize_price(bid_price, tick_size)
            bid_price = self._clamp_price(bid_price)

            if bid_price is not None and bid_price > _ZERO:
                size = max(c.default_order_size, min_order_size)
                slices.append(
                    QuoteSlice(
                        side=QuoteSide.BID,
                        token=token,
                        price=bid_price,
                        size=size,
                        ttl_ms=c.default_ttl_ms,
                    )
                )

            # Ask: above adjusted mid
            ask_price = adjusted_mid + half_spread + level_offset
            ask_price = self._quantize_price(ask_price, tick_size)
            ask_price = self._clamp_price(ask_price)

            if ask_price is not None and ask_price > _ZERO:
                size = max(c.default_order_size, min_order_size)
                slices.append(
                    QuoteSlice(
                        side=QuoteSide.ASK,
                        token=token,
                        price=ask_price,
                        size=size,
                        ttl_ms=c.default_ttl_ms,
                    )
                )

        return slices

    def _build_no_slices(
        self,
        adjusted_mid: Decimal,
        half_spread: Decimal,
        tick_size: Decimal,
        min_order_size: Decimal,
    ) -> list[QuoteSlice]:
        """Build NO token slices using complement pricing.

        For binary markets: price_YES + price_NO ≈ 1.0
        So:
            no_mid = 1 - yes_mid
            no_bid = 1 - yes_ask  (buy NO when YES is expensive)
            no_ask = 1 - yes_bid  (sell NO when YES is cheap)
        """
        c = self._config
        slices: list[QuoteSlice] = []

        no_mid = _ONE - adjusted_mid

        for level in range(c.num_levels):
            level_offset = c.level_spacing * Decimal(str(level))

            # NO bid: complement of YES ask
            no_bid = no_mid - half_spread - level_offset
            no_bid = self._quantize_price(no_bid, tick_size)
            no_bid = self._clamp_price(no_bid)

            if no_bid is not None and no_bid > _ZERO:
                size = max(c.default_order_size, min_order_size)
                slices.append(
                    QuoteSlice(
                        side=QuoteSide.BID,
                        token=TokenSide.NO,
                        price=no_bid,
                        size=size,
                        ttl_ms=c.default_ttl_ms,
                    )
                )

            # NO ask: complement of YES bid
            no_ask = no_mid + half_spread + level_offset
            no_ask = self._quantize_price(no_ask, tick_size)
            no_ask = self._clamp_price(no_ask)

            if no_ask is not None and no_ask > _ZERO:
                size = max(c.default_order_size, min_order_size)
                slices.append(
                    QuoteSlice(
                        side=QuoteSide.ASK,
                        token=TokenSide.NO,
                        price=no_ask,
                        size=size,
                        ttl_ms=c.default_ttl_ms,
                    )
                )

        return slices

    # ── Helpers ──────────────────────────────────────────────────

    def _quantize_price(self, price: Decimal, tick_size: Decimal) -> Decimal:
        """Round price to the nearest valid tick."""
        if tick_size <= _ZERO:
            return price.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        # Round down to nearest tick (conservative for bids)
        return (price / tick_size).quantize(
            Decimal("1"), rounding=ROUND_DOWN
        ) * tick_size

    def _clamp_price(self, price: Decimal) -> Optional[Decimal]:
        """Clamp price to [price_floor, price_ceiling]. Returns None if invalid."""
        c = self._config
        if price < c.price_floor:
            return None
        if price > c.price_ceiling:
            return None
        return price
