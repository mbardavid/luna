"""InventorySkew — Avellaneda-Stoikov inventory risk adjustment.

Implements the reservation-price skew from the Avellaneda-Stoikov (2008)
framework for high-frequency market-making:

    δ = γ · σ² · (T - t) · q

Where:
- **γ** (gamma) — risk aversion parameter (higher = more aggressive skew)
- **σ²** — variance of the mid-price (realised volatility squared)
- **(T - t)** — remaining time horizon (normalised fraction of a period)
- **q** — net inventory (positive = long, negative = short)

The skew shifts the **mid-price** so that:
- When long  (q > 0): mid shifts DOWN → bid becomes more aggressive to sell
- When short (q < 0): mid shifts UP   → ask becomes more aggressive to buy

This naturally mean-reverts inventory toward zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

import structlog

from models.position import Position

logger = structlog.get_logger("strategy.inventory_skew")

_ZERO = Decimal("0")
_ONE = Decimal("1")

# Minimum volatility floor — prevents skew from being zero when
# historical data is insufficient (e.g. paper trading with short series).
MIN_SIGMA = Decimal("0.005")  # 0.5% as volatility floor


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class InventorySkewConfig:
    """Tunable parameters for the Avellaneda-Stoikov skew."""

    # Risk aversion (γ). Higher = more aggressive inventory skew.
    # Typical range: 0.1 (relaxed) – 1.0 (aggressive)
    gamma: Decimal = Decimal("0.3")

    # Time horizon in hours. Used to normalise (T-t).
    # For 24h markets, 24.0 gives smooth skew decay.
    time_horizon_hours: Decimal = Decimal("24")

    # Maximum absolute skew in price units. Prevents extreme skewing
    # that would create un-competitive quotes.
    max_skew: Decimal = Decimal("0.10")

    # Maximum net inventory (|q|) before hard position limits kick in.
    # Beyond this the quote engine should refuse to add to the side.
    max_inventory: Decimal = Decimal("2000")

    # Soft inventory threshold as fraction of max_inventory.
    # Above this, skew ramps up non-linearly.
    soft_inventory_pct: Decimal = Decimal("0.7")

    # Non-linear ramp exponent above soft threshold (1 = linear, 2 = quadratic)
    ramp_exponent: Decimal = Decimal("1.5")

    # Linear skew coefficient — adds a volatility-independent skew component
    # that guarantees meaningful mean-reversion even when σ is tiny.
    # linear_skew = linear_coeff * (q / max_inventory)
    # Default 0.15 means ±15% price shift at full inventory, providing
    # strong mean-reversion pressure even in low-vol / paper trading.
    linear_coeff: Decimal = Decimal("0.15")


# ── InventorySkew ────────────────────────────────────────────────────


class InventorySkew:
    """Computes inventory-risk skew using Avellaneda-Stoikov framework.

    The skew is a signed Decimal that shifts the mid-price:

        adjusted_mid = mid_price - skew

    - Positive skew (long inventory) → adjusted mid lower → more eager to sell
    - Negative skew (short inventory) → adjusted mid higher → more eager to buy

    Usage::

        skew_model = InventorySkew()
        skew = skew_model.compute_skew(
            position=position,
            volatility=Decimal("0.008"),
            elapsed_hours=Decimal("6"),
        )
        adjusted_mid = mid_price - skew
    """

    def __init__(self, config: InventorySkewConfig | None = None) -> None:
        self._config = config or InventorySkewConfig()

    @property
    def config(self) -> InventorySkewConfig:
        """Return current configuration (read-only access)."""
        return self._config

    def compute_skew(
        self,
        position: Position,
        volatility: Decimal,
        elapsed_hours: Decimal = _ZERO,
    ) -> Decimal:
        """Compute the inventory skew δ.

        Parameters
        ----------
        position:
            Current bilateral position with ``qty_yes`` and ``qty_no``.
        volatility:
            Realised volatility of mid-price (σ, in price units).
        elapsed_hours:
            Hours elapsed since the start of the current time horizon.
            Used to compute (T - t); if elapsed >= T, skew decays to 0.

        Returns
        -------
        Decimal
            Signed skew in price units. Positive when long (shift mid down),
            negative when short (shift mid up).
        """
        c = self._config
        q = self._net_inventory(position)

        if q == _ZERO:
            return _ZERO

        # Apply volatility floor — when sigma=0 (e.g. insufficient data
        # in paper trading), the skew formula produces 0 regardless of
        # inventory, preventing any mean-reversion. The floor ensures
        # a minimum skew response to inventory imbalances.
        effective_sigma = max(volatility, MIN_SIGMA)

        # σ² (variance)
        sigma_sq = effective_sigma * effective_sigma

        # (T - t) — remaining time fraction, floored at 0
        t_remaining = self._time_remaining(elapsed_hours)

        # Base Avellaneda-Stoikov: δ = γ · σ² · (T-t) · q
        skew = c.gamma * sigma_sq * t_remaining * q

        # Linear skew component — guarantees a minimum inventory-dependent
        # price shift regardless of σ.  This is essential for paper trading
        # or low-vol markets where σ² ≈ 0 renders the A-S formula inert.
        # The linear component is also scaled by t_remaining so it decays
        # to zero at the time horizon boundary, consistent with A-S.
        if c.max_inventory > _ZERO and c.linear_coeff > _ZERO and t_remaining > _ZERO:
            linear_skew = c.linear_coeff * (q / c.max_inventory) * t_remaining
            skew = skew + linear_skew

        # Non-linear ramp for large inventories
        skew = self._apply_nonlinear_ramp(skew, q)

        # Clamp to max_skew
        skew = _clamp_abs(skew, c.max_skew)

        # Quantise
        skew = skew.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)

        logger.debug(
            "inventory_skew.computed",
            q=str(q),
            sigma=str(volatility),
            effective_sigma=str(effective_sigma),
            sigma_sq=str(sigma_sq),
            t_remaining=str(t_remaining),
            raw_skew=str(c.gamma * sigma_sq * t_remaining * q),
            clamped_skew=str(skew),
        )

        return skew

    def is_inventory_exceeded(self, position: Position) -> bool:
        """Return True if net inventory exceeds hard limit."""
        q = abs(self._net_inventory(position))
        return q > self._config.max_inventory

    def inventory_utilisation(self, position: Position) -> Decimal:
        """Return inventory utilisation as fraction of max [0, ∞).

        Values > 1.0 mean the hard limit is breached.
        """
        q = abs(self._net_inventory(position))
        if self._config.max_inventory <= _ZERO:
            return Decimal("999")
        return q / self._config.max_inventory

    # ── Internals ────────────────────────────────────────────────

    @staticmethod
    def _net_inventory(position: Position) -> Decimal:
        """Net inventory: positive = long YES exposure, negative = long NO.

        q = qty_yes - qty_no
        """
        return position.qty_yes - position.qty_no

    def _time_remaining(self, elapsed_hours: Decimal) -> Decimal:
        """Compute (T - t) normalised to [0, 1]."""
        t_horizon = self._config.time_horizon_hours
        if t_horizon <= _ZERO:
            return _ZERO

        remaining = t_horizon - min(elapsed_hours, t_horizon)
        return remaining / t_horizon

    def _apply_nonlinear_ramp(self, skew: Decimal, q: Decimal) -> Decimal:
        """Amplify skew when inventory exceeds soft threshold.

        Below soft threshold: linear (no change)
        Above soft threshold: multiply by (|q|/soft)^ramp_exponent
        """
        c = self._config
        abs_q = abs(q)
        soft_limit = c.max_inventory * c.soft_inventory_pct

        if soft_limit <= _ZERO or abs_q <= soft_limit:
            return skew

        # How much over the soft limit (ratio)
        excess_ratio = abs_q / soft_limit  # > 1.0

        # Apply ramp: multiply skew by excess_ratio^exponent
        exp = float(c.ramp_exponent)
        ramp = Decimal(str(round(float(excess_ratio) ** exp, 6)))

        return skew * ramp


# ── Helpers ──────────────────────────────────────────────────────────


def _clamp_abs(value: Decimal, max_abs: Decimal) -> Decimal:
    """Clamp absolute value while preserving sign."""
    if abs(value) > max_abs:
        return max_abs if value > _ZERO else -max_abs
    return value
