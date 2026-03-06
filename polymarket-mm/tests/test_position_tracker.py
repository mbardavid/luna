from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

import pytest

from runner.position_tracker import PositionTracker


class FakeMarketConfig:
    def __init__(self, market_id='mkt-1', token_id_yes='101', token_id_no='202'):
        self.market_id = market_id
        self.token_id_yes = token_id_yes
        self.token_id_no = token_id_no
        self.description = 'Test Market'


@pytest.mark.asyncio
async def test_collect_maps_blockscout_positions_and_warns_on_complement_mismatch():
    rest = AsyncMock()
    rest.get_price = AsyncMock(side_effect=[Decimal('0.70'), Decimal('0.20'), Decimal('0.33')])
    tracker = PositionTracker(rest, [FakeMarketConfig()])

    with patch.object(tracker, '_fetch_blockscout_holdings', AsyncMock(return_value={
        '101': Decimal('10'),
        '202': Decimal('5'),
        '999': Decimal('2'),
    })):
        snapshot = await tracker.collect('0xabc')

    assert snapshot.source == 'blockscout'
    assert snapshot.market_positions['mkt-1']['yes_shares'] == Decimal('10')
    assert snapshot.market_positions['mkt-1']['no_shares'] == Decimal('5')
    assert len(snapshot.unmatched_positions) == 1
    assert snapshot.unmatched_positions[0]['token_id'] == '999'
    assert any('complement price mismatch' in warning for warning in snapshot.warnings)


@pytest.mark.asyncio
async def test_collect_falls_back_to_rpc_when_blockscout_fails():
    rest = AsyncMock()
    rest.get_price = AsyncMock(return_value=Decimal('0.51'))
    tracker = PositionTracker(rest, [FakeMarketConfig()])

    with patch.object(tracker, '_fetch_blockscout_holdings', AsyncMock(side_effect=RuntimeError('down'))):
        with patch.object(tracker, '_fetch_rpc_holdings', Mock(return_value={'101': Decimal('7')})):
            snapshot = await tracker.collect('0xabc')

    assert snapshot.source == 'rpc'
    assert snapshot.market_positions['mkt-1']['yes_shares'] == Decimal('7')
    assert any('blockscout_unavailable' in warning for warning in snapshot.warnings)
