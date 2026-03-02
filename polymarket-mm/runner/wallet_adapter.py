"""runner.wallet_adapter — Abstract interface for wallet/position tracking.

Defines the WalletAdapter ABC that both PaperWalletAdapter and
ProductionWalletAdapter implement.  All money values use Decimal arithmetic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any

from models.position import Position


class WalletAdapter(ABC):
    """Abstract wallet adapter for the unified trading pipeline.

    Provides a uniform interface for:
    - Balance queries (available, locked, equity)
    - Position management
    - Wallet snapshots for logging/dashboard
    """

    @property
    @abstractmethod
    def initial_balance(self) -> Decimal:
        """The starting balance (paper: simulated, live: test_capital)."""
        ...

    @property
    @abstractmethod
    def available_balance(self) -> Decimal:
        """Unencumbered cash available for new orders."""
        ...

    @property
    @abstractmethod
    def locked_balance(self) -> Decimal:
        """Cash locked in open orders."""
        ...

    @abstractmethod
    def total_equity(self, mid_prices: dict[str, Decimal] | None = None) -> Decimal:
        """Total portfolio value = available + locked + mark-to-market positions."""
        ...

    @abstractmethod
    def wallet_snapshot(self, mid_prices: dict[str, Decimal] | None = None) -> dict:
        """Return a JSON-serializable wallet state dict for logging/dashboard."""
        ...

    @abstractmethod
    def get_position(self, market_id: str) -> Position | None:
        """Get current position for a market."""
        ...

    @abstractmethod
    def init_position(self, market_id: str, token_id_yes: str, token_id_no: str) -> None:
        """Initialize a zero position for a market (idempotent)."""
        ...

    @abstractmethod
    def update_position_on_fill(
        self,
        market_id: str,
        side: str,
        token_is_yes: bool,
        fill_price: Decimal,
        fill_qty: Decimal,
        fee: Decimal = Decimal("0"),
    ) -> Decimal:
        """Update position and wallet state on fill. Returns realized PnL."""
        ...

    @property
    @abstractmethod
    def positions(self) -> dict[str, Position]:
        """All positions keyed by market_id."""
        ...

    @property
    @abstractmethod
    def total_fees(self) -> Decimal:
        """Cumulative fees paid."""
        ...

    # ── Optional methods (live-mode only) ───────────────────────

    async def reconcile_on_chain(
        self,
        rest_client: Any = None,
        market_configs: list | None = None,
    ) -> None:
        """Reconcile wallet state with on-chain balances. No-op in paper mode."""
        pass

    @property
    def on_chain(self) -> dict[str, Any]:
        """On-chain snapshot. Empty dict in paper mode."""
        return {}

    @property
    def test_capital(self) -> Decimal:
        """Capital allocated for risk budgeting. Defaults to initial_balance."""
        return self.initial_balance
