"""Tests for execution/quantizer.py — includes property-based tests via hypothesis."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from execution.quantizer import quantize_price, quantize_size


# ── Strategy helpers ─────────────────────────────────────────────────

# Prices between 0.001 and 0.999 as Decimal
decimal_price = st.decimals(
    min_value="0.001",
    max_value="0.999",
    places=4,
    allow_nan=False,
    allow_infinity=False,
)

# Tick sizes used by Polymarket
tick_sizes = st.sampled_from(
    [Decimal("0.001"), Decimal("0.01"), Decimal("0.05"), Decimal("0.1")]
)

# Sizes from 1 to 10000
decimal_size = st.decimals(
    min_value="0.1",
    max_value="10000",
    places=2,
    allow_nan=False,
    allow_infinity=False,
)

min_order_sizes = st.sampled_from(
    [Decimal("1"), Decimal("5"), Decimal("10"), Decimal("0.1")]
)


# ── Unit tests ───────────────────────────────────────────────────────


class TestQuantizePrice:
    """Deterministic unit tests for quantize_price."""

    def test_exact_tick(self) -> None:
        assert quantize_price(Decimal("0.50"), Decimal("0.01")) == Decimal("0.50")

    def test_round_up(self) -> None:
        # 0.505 should round to 0.51 with tick 0.01 (half-up)
        assert quantize_price(Decimal("0.505"), Decimal("0.01")) == Decimal("0.51")

    def test_round_down(self) -> None:
        # 0.504 rounds to 0.50
        assert quantize_price(Decimal("0.504"), Decimal("0.01")) == Decimal("0.50")

    def test_small_tick(self) -> None:
        result = quantize_price(Decimal("0.1234"), Decimal("0.001"))
        assert result == Decimal("0.123")

    def test_clamp_low(self) -> None:
        result = quantize_price(Decimal("0.001"), Decimal("0.01"))
        assert result == Decimal("0.01")

    def test_clamp_high(self) -> None:
        result = quantize_price(Decimal("0.999"), Decimal("0.01"))
        assert result == Decimal("0.99")

    def test_type_error_float(self) -> None:
        with pytest.raises(TypeError):
            quantize_price(0.5, Decimal("0.01"))  # type: ignore[arg-type]

    def test_value_error_zero_price(self) -> None:
        with pytest.raises(ValueError):
            quantize_price(Decimal("0"), Decimal("0.01"))

    def test_value_error_negative_tick(self) -> None:
        with pytest.raises(ValueError):
            quantize_price(Decimal("0.5"), Decimal("-0.01"))


class TestQuantizeSize:
    """Deterministic unit tests for quantize_size."""

    def test_exact_multiple(self) -> None:
        assert quantize_size(Decimal("100"), Decimal("5")) == Decimal("100")

    def test_round_down(self) -> None:
        assert quantize_size(Decimal("103"), Decimal("5")) == Decimal("100")

    def test_too_small(self) -> None:
        assert quantize_size(Decimal("4"), Decimal("5")) == Decimal("0")

    def test_zero_size(self) -> None:
        assert quantize_size(Decimal("0"), Decimal("5")) == Decimal("0")

    def test_fractional_min(self) -> None:
        assert quantize_size(Decimal("2.3"), Decimal("0.5")) == Decimal("2.0")

    def test_type_error_float(self) -> None:
        with pytest.raises(TypeError):
            quantize_size(100.0, Decimal("5"))  # type: ignore[arg-type]

    def test_negative_size(self) -> None:
        with pytest.raises(ValueError):
            quantize_size(Decimal("-1"), Decimal("5"))


# ── Property-based tests ────────────────────────────────────────────


class TestQuantizePriceProperties:
    """Hypothesis property-based tests for quantize_price."""

    @given(price=decimal_price, tick=tick_sizes)
    @settings(max_examples=200)
    def test_result_is_multiple_of_tick(self, price: Decimal, tick: Decimal) -> None:
        result = quantize_price(price, tick)
        remainder = result % tick
        assert remainder == Decimal("0"), (
            f"quantize_price({price}, {tick}) = {result} is not a multiple of {tick}"
        )

    @given(price=decimal_price, tick=tick_sizes)
    @settings(max_examples=200)
    def test_result_within_bounds(self, price: Decimal, tick: Decimal) -> None:
        result = quantize_price(price, tick)
        assert result >= tick, f"Result {result} < tick {tick}"
        assert result <= Decimal("1") - tick, f"Result {result} > 1 - tick"

    @given(price=decimal_price, tick=tick_sizes)
    @settings(max_examples=200)
    def test_result_close_to_input(self, price: Decimal, tick: Decimal) -> None:
        result = quantize_price(price, tick)
        # Result should be within 1 tick of the input (before clamping)
        clamped_price = max(tick, min(Decimal("1") - tick, price))
        diff = abs(result - clamped_price)
        assert diff <= tick, (
            f"quantize_price({price}, {tick}) = {result}, "
            f"diff {diff} > tick {tick}"
        )


class TestQuantizeSizeProperties:
    """Hypothesis property-based tests for quantize_size."""

    @given(size=decimal_size, min_sz=min_order_sizes)
    @settings(max_examples=200)
    def test_result_lte_input(self, size: Decimal, min_sz: Decimal) -> None:
        result = quantize_size(size, min_sz)
        assert result <= size, (
            f"quantize_size({size}, {min_sz}) = {result} > input"
        )

    @given(size=decimal_size, min_sz=min_order_sizes)
    @settings(max_examples=200)
    def test_result_is_multiple(self, size: Decimal, min_sz: Decimal) -> None:
        result = quantize_size(size, min_sz)
        if result > Decimal("0"):
            remainder = result % min_sz
            assert remainder == Decimal("0"), (
                f"quantize_size({size}, {min_sz}) = {result} not a multiple"
            )

    @given(size=decimal_size, min_sz=min_order_sizes)
    @settings(max_examples=200)
    def test_result_zero_or_gte_min(self, size: Decimal, min_sz: Decimal) -> None:
        result = quantize_size(size, min_sz)
        assert result == Decimal("0") or result >= min_sz
