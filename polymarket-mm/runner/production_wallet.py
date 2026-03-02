"""runner.production_wallet — WalletAdapter wrapping ProductionWallet."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from models.position import Position
from paper.production_runner import ProductionWallet
from runner.wallet_adapter import WalletAdapter


class ProductionWalletAdapter(WalletAdapter):
    """WalletAdapter backed by the existing ProductionWallet class.

    Delegates all operations to ProductionWallet, which tracks
    positions, balances, and on-chain reconciliation.
    """

    def __init__(self, wallet: ProductionWallet) -> None:
        self._wallet = wallet

    @property
    def wallet(self) -> ProductionWallet:
        """Expose underlying wallet for direct access when needed."""
        return self._wallet

    @property
    def initial_balance(self) -> Decimal:
        return self._wallet.initial_balance

    @property
    def available_balance(self) -> Decimal:
        return self._wallet.available_balance

    @property
    def locked_balance(self) -> Decimal:
        return self._wallet.locked_balance

    def total_equity(self, mid_prices: dict[str, Decimal] | None = None) -> Decimal:
        return self._wallet.total_equity(mid_prices)

    def wallet_snapshot(self, mid_prices: dict[str, Decimal] | None = None) -> dict:
        return self._wallet.wallet_snapshot(mid_prices)

    def get_position(self, market_id: str) -> Position | None:
        return self._wallet.get_position(market_id)

    def init_position(self, market_id: str, token_id_yes: str, token_id_no: str) -> None:
        self._wallet.init_position(market_id, token_id_yes, token_id_no)

    def update_position_on_fill(
        self,
        market_id: str,
        side: str,
        token_is_yes: bool,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee: Decimal = Decimal("0"),
    ) -> Decimal:
        return self._wallet.update_position_on_fill(
            market_id=market_id,
            side=side,
            token_is_yes=token_is_yes,
            fill_price=fill_price,
            fill_qty=fill_qty,
            fee=fee,
        )

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._wallet._positions)

    @property
    def total_fees(self) -> Decimal:
        return self._wallet.total_fees

    @property
    def test_capital(self) -> Decimal:
        return self._wallet.test_capital

    @property
    def on_chain(self) -> dict[str, Any]:
        return self._wallet.on_chain

    async def reconcile_on_chain(
        self,
        rest_client: Any = None,
        market_configs: list | None = None,
    ) -> None:
        await self._wallet.reconcile_on_chain(rest_client, market_configs)
