"""runner.paper_wallet — WalletAdapter wrapping PaperVenue internal wallet."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from models.position import Position
from paper.paper_venue import PaperVenue
from runner.wallet_adapter import WalletAdapter


class PaperWalletAdapter(WalletAdapter):
    """WalletAdapter backed by PaperVenue's internal wallet state.

    Delegates balance/position queries to the PaperVenue, which
    already tracks positions and balances internally.
    """

    def __init__(self, venue: PaperVenue) -> None:
        self._venue = venue

    @property
    def initial_balance(self) -> Decimal:
        return self._venue.initial_balance

    @property
    def available_balance(self) -> Decimal:
        return self._venue.available_balance

    @property
    def locked_balance(self) -> Decimal:
        return self._venue.locked_balance

    def total_equity(self, mid_prices: dict[str, Decimal] | None = None) -> Decimal:
        return self._venue.total_equity()

    def wallet_snapshot(self, mid_prices: dict[str, Decimal] | None = None) -> dict:
        return self._venue.wallet_snapshot()

    def get_position(self, market_id: str) -> Position | None:
        return self._venue.get_position(market_id)

    def init_position(self, market_id: str, token_id_yes: str, token_id_no: str) -> None:
        # PaperVenue initializes positions via its configs, so this is a no-op.
        # Positions are created when the venue is configured with MarketSimConfigs.
        pass

    def update_position_on_fill(
        self,
        market_id: str,
        side: str,
        token_is_yes: bool,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee: Decimal = Decimal("0"),
    ) -> Decimal:
        """PaperVenue updates positions internally on fill.

        We still need to compute PnL from the venue's position state.
        The venue handles position tracking, so we just read the result.
        Returns the realized PnL (approximation from venue state).
        """
        # PaperVenue handles position updates internally during submit_order.
        # This method is called by the pipeline after processing fill events.
        # We return 0 because the venue already updated the position.
        # The pipeline reads PnL from venue.total_pnl.
        return Decimal("0")

    @property
    def positions(self) -> dict[str, Position]:
        """Get all positions from PaperVenue."""
        return self._venue.get_all_positions()

    @property
    def total_fees(self) -> Decimal:
        return self._venue.total_fees
