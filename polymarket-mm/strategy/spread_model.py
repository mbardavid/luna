"""SpreadModel — computes optimal half-spread as a function of volatility, fees, and liquidity.

The model combines three components:
1. **Volatility component** — wider spreads in volatile markets to compensate
   for adverse selection risk.
2. **Fee floor** — the half-spread must always exceed the exchange fee to
   ensure profitability on round-trips.
3. **Liquidity adjustment** — in illiquid markets spreads widen to compensate
   for execution risk; in liquid markets they tighten to remain competitive.

The resulting half-spread is in *price units* (Decimal, 0–1 range for
Polymarket binary markets).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import structlog

logger = structlog.get_logger("strategy.spread_model")

# ── Constants ────────────────────────────────────────────────────────

_BPS_DIVISOR = Decimal("10000")
_ONE = Decimal("1")
_ZERO = Decimal("0")
_TWO = Decimal("2")


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class SpreadModelConfig:
    """Tunable parameters for the spread model."""

    # Minimum half-spread in basis points (absolute floor)
    min_half_spread_bps: Decimal = Decimal("15")

    # Maximum half-spread in basis points (cap to stay competitive)
    max_half_spread_bps: Decimal = Decimal("500")

    # Volatility multiplier: half_spread_vol = vol_multiplier * σ
    # Higher → more conservative (wider spreads in vol)
    vol_multiplier: Decimal = Decimal("1.5")

    # Liquidity adjustment power: how aggressively to widen in thin markets
    # score ∈ [0,1]; adjustment = 1 / (liquidity_score ^ liq_power)
    # 1.0 → linear, 0.5 → sqrt (gentler), 2.0 → quadratic (aggressive)
    liquidity_power: Decimal = Decimal("0.5")

    # Base liquidity floor: if liquidity_score < this, apply max widening
    liquidity_floor: Decimal = Decimal("0.05")

    # Max liquidity widening multiplier
    max_liquidity_multiplier: Decimal = Decimal("3.0")


# ── SpreadModel ──────────────────────────────────────────────────────


class SpreadModel:
    """Computes the optimal half-spread for a binary market.

    The half-spread is the distance from mid-price to the bid (or ask).
    The full spread is 2 × half-spread.

    Usage::

        model = SpreadModel()
        hs = model.optimal_half_spread(
            volatility=Decimal("0.008"),
            fee_bps=Decimal("2"),
            liquidity_score=0.6,
            mid_price=Decimal("0.55"),
        )
        bid = mid_price - hs
        ask = mid_price + hs
    """

    def __init__(self, config: SpreadModelConfig | None = None) -> None:
        self._config = config or SpreadModelConfig()

    @property
    def config(self) -> SpreadModelConfig:
        """Return current configuration (read-only access)."""
        return self._config

    def optimal_half_spread(
        self,
        volatility: Decimal,
        fee_bps: Decimal,
        liquidity_score: float,
        mid_price: Decimal = Decimal("0.50"),
    ) -> Decimal:
        """Compute optimal half-spread in price units.

        Parameters
        ----------
        volatility:
            Realised volatility of the mid-price (standard deviation of
            price changes, same units as price, e.g. 0.008 = 0.8%).
        fee_bps:
            Exchange fee in basis points (e.g. 2 = 0.02%).
        liquidity_score:
            Normalised liquidity score [0, 1] where 1 is very liquid.
        mid_price:
            Current mid-price (used only for bps→price conversion).

        Returns
        -------
        Decimal
            Half-spread in absolute price units, quantised to 4 decimal places.
        """
        c = self._config

        if mid_price <= _ZERO:
            logger.warning("spread_model.zero_mid", mid_price=str(mid_price))
            return _bps_to_price(c.min_half_spread_bps, Decimal("0.50"))

        # 1. Fee floor — half the round-trip fee (both legs pay fee)
        fee_component = _bps_to_price(fee_bps, mid_price)

        # 2. Volatility component
        vol_component = c.vol_multiplier * abs(volatility)

        # 3. Base half-spread = max(fee_floor, vol_component)
        base = max(fee_component, vol_component)

        # 4. Liquidity adjustment multiplier
        liq_mult = self._liquidity_multiplier(liquidity_score)
        adjusted = base * liq_mult

        # 5. Clamp to [min, max] (in price units)
        min_hs = _bps_to_price(c.min_half_spread_bps, mid_price)
        max_hs = _bps_to_price(c.max_half_spread_bps, mid_price)

        result = _clamp(adjusted, min_hs, max_hs)

        # Quantise to 4 decimal places
        result = result.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        logger.debug(
            "spread_model.computed",
            fee_comp=str(fee_component),
            vol_comp=str(vol_component),
            liq_mult=str(round(float(liq_mult), 4)),
            base=str(base),
            adjusted=str(adjusted),
            result=str(result),
            mid=str(mid_price),
        )

        return result

    def _liquidity_multiplier(self, liquidity_score: float) -> Decimal:
        """Compute liquidity adjustment multiplier.

        Returns a value >= 1.0; higher when liquidity is thin.
        """
        c = self._config
        floor = float(c.liquidity_floor)

        if liquidity_score <= floor:
            return c.max_liquidity_multiplier

        # Normalise score to [0, 1] range above the floor
        effective = max(liquidity_score, floor)
        power = float(c.liquidity_power)

        # multiplier = 1 / (score ^ power), clamped to [1, max]
        divisor = effective ** power
        if divisor <= 0:
            return c.max_liquidity_multiplier

        mult = Decimal(str(round(1.0 / divisor, 6)))
        return _clamp(mult, _ONE, c.max_liquidity_multiplier)


# ── Helpers ──────────────────────────────────────────────────────────


def _bps_to_price(bps: Decimal, mid_price: Decimal) -> Decimal:
    """Convert basis points to absolute price units relative to mid."""
    return (bps * mid_price) / _BPS_DIVISOR


def _clamp(value: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    """Clamp a Decimal between lo and hi."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value
