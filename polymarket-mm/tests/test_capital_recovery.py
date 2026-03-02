"""Tests for runner.capital_recovery — CapitalRecovery."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from models.order import Order, OrderStatus, Side
from models.position import Position
from runner.capital_recovery import CapitalRecovery, RecoveryPlan, RecoverySellOrder
from runner.config import RotationConfig, UnifiedMarketConfig
from models.market_state import MarketType

MARKET_A = "market-a"
MARKET_B = "market-b"
TOKEN_YES_A = "tok-yes-a"
TOKEN_NO_A = "tok-no-a"
TOKEN_YES_B = "tok-yes-b"
TOKEN_NO_B = "tok-no-b"


@pytest.fixture
def config() -> RotationConfig:
    return RotationConfig(
        capital_recovery=True,
        min_balance_for_recovery=Decimal("10"),
    )


@pytest.fixture
def recovery(config: RotationConfig) -> CapitalRecovery:
    return CapitalRecovery(config)


@pytest.fixture
def market_configs() -> list[UnifiedMarketConfig]:
    return [
        UnifiedMarketConfig(
            market_id=MARKET_A,
            condition_id=MARKET_A,
            token_id_yes=TOKEN_YES_A,
            token_id_no=TOKEN_NO_A,
            description="Market A",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        ),
        UnifiedMarketConfig(
            market_id=MARKET_B,
            condition_id=MARKET_B,
            token_id_yes=TOKEN_YES_B,
            token_id_no=TOKEN_NO_B,
            description="Market B",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        ),
    ]


class TestCapitalRecoveryNeedsRecovery:
    """Test the needs_recovery check."""

    def test_balance_above_threshold(self, recovery: CapitalRecovery) -> None:
        assert not recovery.needs_recovery(Decimal("15"))

    def test_balance_at_threshold(self, recovery: CapitalRecovery) -> None:
        assert not recovery.needs_recovery(Decimal("10"))

    def test_balance_below_threshold(self, recovery: CapitalRecovery) -> None:
        assert recovery.needs_recovery(Decimal("3"))


class TestCapitalRecoveryPlan:
    """Test recovery plan generation."""

    def test_no_recovery_needed(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Balance above threshold → empty plan."""
        plan = recovery.plan_recovery(
            current_balance=Decimal("20"),
            positions={},
            market_configs=market_configs,
        )
        assert plan.is_empty
        assert plan.deficit <= 0

    def test_no_positions_to_sell(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Balance below threshold but no positions → empty plan."""
        plan = recovery.plan_recovery(
            current_balance=Decimal("2"),
            positions={
                MARKET_A: Position(
                    market_id=MARKET_A,
                    token_id_yes=TOKEN_YES_A,
                    token_id_no=TOKEN_NO_A,
                    qty_yes=Decimal("0"),
                    qty_no=Decimal("0"),
                ),
            },
            market_configs=market_configs,
        )
        assert plan.is_empty

    def test_sells_most_profitable_first(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Positions should be sorted by PnL descending."""
        positions = {
            MARKET_A: Position(
                market_id=MARKET_A,
                token_id_yes=TOKEN_YES_A,
                token_id_no=TOKEN_NO_A,
                qty_yes=Decimal("10"),
                qty_no=Decimal("0"),
                avg_entry_yes=Decimal("0.40"),  # bought at 0.40
            ),
            MARKET_B: Position(
                market_id=MARKET_B,
                token_id_yes=TOKEN_YES_B,
                token_id_no=TOKEN_NO_B,
                qty_yes=Decimal("10"),
                qty_no=Decimal("0"),
                avg_entry_yes=Decimal("0.30"),  # bought at 0.30
            ),
        }
        mid_prices = {
            MARKET_A: Decimal("0.50"),  # PnL = (0.50-0.40)*10 = 1.0
            MARKET_B: Decimal("0.60"),  # PnL = (0.60-0.30)*10 = 3.0
        }

        plan = recovery.plan_recovery(
            current_balance=Decimal("2"),
            positions=positions,
            market_configs=market_configs,
            mid_prices=mid_prices,
        )

        assert not plan.is_empty
        # Most profitable (MARKET_B with PnL=3.0) should be first
        assert plan.sell_orders[0].market_id == MARKET_B

    def test_respects_min_order_size(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Positions below min_order_size should be skipped."""
        positions = {
            MARKET_A: Position(
                market_id=MARKET_A,
                token_id_yes=TOKEN_YES_A,
                token_id_no=TOKEN_NO_A,
                qty_yes=Decimal("3"),  # Below min_order_size of 5
                qty_no=Decimal("0"),
                avg_entry_yes=Decimal("0.40"),
            ),
        }

        plan = recovery.plan_recovery(
            current_balance=Decimal("2"),
            positions=positions,
            market_configs=market_configs,
            mid_prices={MARKET_A: Decimal("0.50")},
        )

        assert plan.is_empty

    def test_recovery_plan_covers_deficit(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Plan should aim to cover the full deficit."""
        positions = {
            MARKET_A: Position(
                market_id=MARKET_A,
                token_id_yes=TOKEN_YES_A,
                token_id_no=TOKEN_NO_A,
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
                avg_entry_yes=Decimal("0.40"),
            ),
        }

        plan = recovery.plan_recovery(
            current_balance=Decimal("2"),
            positions=positions,
            market_configs=market_configs,
            mid_prices={MARKET_A: Decimal("0.50")},
        )

        assert not plan.is_empty
        assert plan.deficit == Decimal("8")  # 10 - 2
        assert plan.total_expected_proceeds > 0

    def test_sells_no_side_too(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Should consider NO side positions for selling."""
        positions = {
            MARKET_A: Position(
                market_id=MARKET_A,
                token_id_yes=TOKEN_YES_A,
                token_id_no=TOKEN_NO_A,
                qty_yes=Decimal("0"),
                qty_no=Decimal("20"),
                avg_entry_no=Decimal("0.40"),
            ),
        }

        plan = recovery.plan_recovery(
            current_balance=Decimal("2"),
            positions=positions,
            market_configs=market_configs,
            mid_prices={MARKET_A: Decimal("0.50")},
        )

        assert not plan.is_empty
        assert plan.sell_orders[0].token_is_yes is False


class TestCapitalRecoveryExecution:
    """Test recovery execution."""

    @pytest.mark.asyncio
    async def test_execute_empty_plan(self, recovery: CapitalRecovery) -> None:
        plan = RecoveryPlan(
            target_balance=Decimal("10"),
            current_balance=Decimal("15"),
            deficit=Decimal("-5"),
        )
        results = await recovery.execute_recovery(plan, MagicMock(), [])
        assert results == []

    @pytest.mark.asyncio
    async def test_execute_submits_sell_orders(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Recovery should submit SELL orders via venue adapter."""
        plan = RecoveryPlan(
            target_balance=Decimal("10"),
            current_balance=Decimal("2"),
            deficit=Decimal("8"),
            sell_orders=[
                RecoverySellOrder(
                    market_id=MARKET_A,
                    token_id=TOKEN_YES_A,
                    token_is_yes=True,
                    size=Decimal("10"),
                    estimated_price=Decimal("0.50"),
                    position_pnl=Decimal("1"),
                ),
            ],
        )

        mock_venue = AsyncMock()
        mock_result = MagicMock()
        mock_result.status.value = "submitted"
        mock_result.filled_qty = Decimal("0")
        mock_venue.submit_order.return_value = mock_result

        results = await recovery.execute_recovery(plan, mock_venue, market_configs)

        assert len(results) == 1
        assert results[0]["market_id"] == MARKET_A
        mock_venue.submit_order.assert_called_once()
        submitted_order = mock_venue.submit_order.call_args[0][0]
        assert submitted_order.side == Side.SELL

    @pytest.mark.asyncio
    async def test_execute_handles_failure(
        self,
        recovery: CapitalRecovery,
        market_configs: list[UnifiedMarketConfig],
    ) -> None:
        """Recovery should handle order submission failures gracefully."""
        plan = RecoveryPlan(
            target_balance=Decimal("10"),
            current_balance=Decimal("2"),
            deficit=Decimal("8"),
            sell_orders=[
                RecoverySellOrder(
                    market_id=MARKET_A,
                    token_id=TOKEN_YES_A,
                    token_is_yes=True,
                    size=Decimal("10"),
                    estimated_price=Decimal("0.50"),
                    position_pnl=Decimal("1"),
                ),
            ],
        )

        mock_venue = AsyncMock()
        mock_venue.submit_order.side_effect = Exception("API error")

        results = await recovery.execute_recovery(plan, mock_venue, market_configs)

        assert len(results) == 1
        assert "error" in results[0]
