"""Quantizer — price/size rounding helpers for CLOB constraints.

All operations use ``Decimal`` exclusively; floats are never accepted.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, InvalidOperation


def quantize_price(price: Decimal, tick_size: Decimal) -> Decimal:
    """Round *price* to the nearest tick (half-up).

    Parameters
    ----------
    price:
        Raw price as ``Decimal``.  Must be > 0.
    tick_size:
        Minimum price increment.  Must be > 0.

    Returns
    -------
    Decimal
        Price rounded to the nearest multiple of *tick_size*.
        Clamped to ``[tick_size, 1 - tick_size]`` for binary markets.

    Raises
    ------
    ValueError
        If *price* or *tick_size* are not positive ``Decimal``.
    TypeError
        If arguments are not ``Decimal``.
    """
    _validate_decimal_positive(price, "price")
    _validate_decimal_positive(tick_size, "tick_size")

    # Number of ticks (round half-up)
    ticks = (price / tick_size).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    quantized = ticks * tick_size

    # Clamp to valid binary-market range [tick_size, 1 - tick_size]
    lower = tick_size
    upper = Decimal("1") - tick_size
    if upper < lower:
        # Degenerate tick_size (>= 0.5) — just clamp to lower
        upper = lower
    quantized = max(lower, min(upper, quantized))
    return quantized


def quantize_size(size: Decimal, min_order_size: Decimal) -> Decimal:
    """Round *size* down to an integer multiple of *min_order_size*.

    Parameters
    ----------
    size:
        Raw order size as ``Decimal``.
    min_order_size:
        Minimum order quantum.  Must be > 0.

    Returns
    -------
    Decimal
        Size rounded down.  Returns ``Decimal("0")`` if the result
        would be less than *min_order_size* (i.e. the order is too small).

    Raises
    ------
    ValueError
        If *size* is negative or *min_order_size* is not positive.
    TypeError
        If arguments are not ``Decimal``.
    """
    _validate_decimal_non_negative(size, "size")
    _validate_decimal_positive(min_order_size, "min_order_size")

    # Number of full quanta (truncate)
    quanta = (size / min_order_size).quantize(Decimal("1"), rounding=ROUND_DOWN)
    quantized = quanta * min_order_size

    if quantized < min_order_size:
        return Decimal("0")
    return quantized


# ── Internal validators ──────────────────────────────────────────────


def _validate_decimal_positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if value <= Decimal("0"):
        raise ValueError(f"{name} must be positive, got {value}")


def _validate_decimal_non_negative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal):
        raise TypeError(f"{name} must be a Decimal, got {type(value).__name__}")
    if value < Decimal("0"):
        raise ValueError(f"{name} must be non-negative, got {value}")
