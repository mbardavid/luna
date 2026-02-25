"""RewardsFarming — adjusts quotes to maximise Polymarket liquidity rewards.

Polymarket distributes daily USDC rewards to market makers whose orders sit
close to the mid-price. The closer to mid and the larger the resting size,
the bigger the reward share.

This module tightens the half-spread when the expected reward more than
compensates for the reduced edge, and caps the tightening so that the
strategy never goes negative-EV per fill.

Key insight (from Gemini Deep Think research): for well-capitalised MMs in
liquid markets, rewards farming can be the **primary** source of profit,
with spread capture at breakeven.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import structlog

logger = structlog.get_logger("strategy.rewards_farming")

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS_DIVISOR = Decimal("10000")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class RewardsFarmingConfig:
    """Tunable parameters for reward-aware spread tightening."""

    # Aggressiveness of reward-driven tightening [0, 1].
    # 0 = ignore rewards entirely, 1 = maximally tight for rewards.
    aggressiveness: Decimal = Decimal("0.5")

    # Maximum tightening as fraction of the half-spread.
    # E.g. 0.6 = never tighten more than 60% of the base half-spread.
    max_tighten_pct: Decimal = Decimal("0.60")

    # Distance threshold (in price units) from mid within which
    # rewards are earned at full rate. Orders beyond this earn
    # exponentially less.
    reward_distance_threshold: Decimal = Decimal("0.02")

    # Estimated daily reward rate per $1 of resting liquidity
    # within the reward zone (in USD, annualised-equivalent for
    # per-trade offset). This is a rough calibration parameter.
    estimated_reward_per_dollar: Decimal = Decimal("0.0005")

    # Minimum half-spread in bps AFTER reward tightening.
    # Hard floor to prevent negative-EV fills.
    min_post_reward_spread_bps: Decimal = Decimal("5")


# ── RewardsFarming ───────────────────────────────────────────────────


class RewardsFarming:
    """Adjusts quotes to optimise for Polymarket liquidity rewards.

    This is designed to be called by ``QuoteEngine`` after the base
    half-spread and inventory skew have been computed.

    Usage::

        farming = RewardsFarming()
        adjusted_hs = farming.adjust_half_spread(
            base_half_spread=Decimal("0.015"),
            mid_price=Decimal("0.50"),
            fee_bps=Decimal("2"),
        )
        # adjusted_hs <= base_half_spread (tighter for rewards)
    """

    def __init__(self, config: RewardsFarmingConfig | None = None) -> None:
        self._config = config or RewardsFarmingConfig()

    @property
    def config(self) -> RewardsFarmingConfig:
        """Return current configuration (read-only access)."""
        return self._config

    def adjust_half_spread(
        self,
        base_half_spread: Decimal,
        mid_price: Decimal,
        fee_bps: Decimal,
    ) -> Decimal:
        """Tighten the half-spread to earn more rewards.

        Parameters
        ----------
        base_half_spread:
            Half-spread in price units BEFORE reward adjustment.
        mid_price:
            Current mid-price of the market.
        fee_bps:
            Exchange fee in basis points.

        Returns
        -------
        Decimal
            Adjusted half-spread (≤ base_half_spread), quantised to
            4 decimal places.
        """
        c = self._config

        if c.aggressiveness <= _ZERO:
            return base_half_spread

        if base_half_spread <= _ZERO or mid_price <= _ZERO:
            return base_half_spread

        # 1. Compute the maximum tightening amount
        max_tighten = base_half_spread * c.max_tighten_pct

        # 2. Scale by aggressiveness
        tighten_amount = max_tighten * c.aggressiveness

        # 3. Compute the reward-zone bonus:
        #    If base_half_spread is already inside the reward zone,
        #    tightening has diminishing returns.
        reward_factor = self._reward_proximity_factor(base_half_spread)
        tighten_amount = tighten_amount * reward_factor

        # 4. Apply tightening
        adjusted = base_half_spread - tighten_amount

        # 5. Hard floor: never go below min bps
        min_hs = (c.min_post_reward_spread_bps * mid_price) / _BPS_DIVISOR
        fee_floor = (fee_bps * mid_price) / _BPS_DIVISOR
        floor = max(min_hs, fee_floor)

        adjusted = max(adjusted, floor)

        # 6. Quantise
        adjusted = adjusted.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        logger.debug(
            "rewards_farming.adjusted",
            base=str(base_half_spread),
            tighten=str(tighten_amount),
            reward_factor=str(round(float(reward_factor), 4)),
            adjusted=str(adjusted),
            floor=str(floor),
        )

        return adjusted

    def compute_reward_edge(
        self,
        half_spread: Decimal,
        order_size: Decimal,
        mid_price: Decimal,
    ) -> Decimal:
        """Estimate the reward earned per fill given current quote placement.

        Parameters
        ----------
        half_spread:
            Distance from mid to our quote (price units).
        order_size:
            Size of the resting order (in shares).
        mid_price:
            Current mid-price.

        Returns
        -------
        Decimal
            Estimated reward in USD for a single fill at this distance.
        """
        c = self._config

        if mid_price <= _ZERO or order_size <= _ZERO:
            return _ZERO

        # Dollar value of the resting order
        dollar_value = order_size * mid_price

        # Proximity factor: how close we are to earning full rewards
        proximity = self._reward_proximity_factor(half_spread)

        # Estimated reward
        reward = dollar_value * c.estimated_reward_per_dollar * Decimal(str(round(float(proximity), 6)))

        return reward.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

    # ── Internals ────────────────────────────────────────────────

    def _reward_proximity_factor(self, distance_from_mid: Decimal) -> Decimal:
        """Compute how much of the reward we earn at this distance.

        Returns a value in [0, 1]:
        - 1.0 if distance ≤ reward_distance_threshold
        - Exponential decay beyond threshold
        """
        c = self._config
        threshold = c.reward_distance_threshold

        if threshold <= _ZERO:
            return _ONE

        if distance_from_mid <= threshold:
            return _ONE

        # Exponential decay: exp(-(d - threshold) / threshold)
        import math
        excess = float(distance_from_mid - threshold) / float(threshold)
        factor = math.exp(-excess)

        return Decimal(str(round(factor, 6)))
