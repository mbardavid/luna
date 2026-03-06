from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from execution.ctf_merge import CTFMerger


def test_calculate_mergeable_preserves_micro_units():
    merger = CTFMerger(ctf_adapter=object())
    assert merger.calculate_mergeable(Decimal("12.345678"), Decimal("9.876543")) == Decimal("9.876543")


@pytest.mark.asyncio
async def test_merge_failure_requests_fallback():
    adapter = AsyncMock()
    adapter.merge_positions.side_effect = RuntimeError("reverted")
    merger = CTFMerger(ctf_adapter=adapter)

    result = await merger.merge_positions(
        condition_id="0xabc",
        amount=Decimal("1.250000"),
        neg_risk=True,
    )

    assert result.success is False
    assert result.route == "neg_risk"
    assert result.fallback_required is True
