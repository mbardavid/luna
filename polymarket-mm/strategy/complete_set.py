"""CompleteSetStrategy — state machine for CTF complete-set and reverse arbitrage.

Implements two arbitrage strategies on Polymarket's CTF:

1. **Complete-Set (Merge) Arbitrage:**
   When ``YES_price + NO_price < 1.0 - costs``, buy both sides on the CLOB
   and merge on-chain to receive $1.00 per pair.

2. **Reverse (Split) Arbitrage:**
   When ``YES_price + NO_price > 1.0 + costs``, split $1.00 on-chain into
   YES+NO tokens and sell the more expensive side on the CLOB.

State machine::

    IDLE → PAIR_PLANNED → LEG1_WORKING → LEG1_FILLED
         → LEG2_WORKING → BOTH_FILLED → MERGING → MERGED

For reverse arbitrage::

    IDLE → PAIR_PLANNED → SPLITTING → SPLIT_DONE
         → LEG1_WORKING → LEG1_FILLED → LEG2_WORKING → BOTH_SOLD → COMPLETED

Key insight from plan: selling back on the book doesn't work (taker fee + spread).
The merge on-chain is the only viable path — costs ~$1 gas per pair, no protocol fee.

Architecture note (fast-path / slow-path separation):
    CLOB legs (BUY/SELL) are executed locally (fast-path).
    When the state machine reaches BOTH_FILLED (merge) or PAIR_PLANNED→SPLITTING (split),
    the orchestrator delegates the on-chain operation to Crypto-Sage via A2A TaskSpec
    (see ``a2a.ctf_delegate.CTFDelegate``). The result arrives asynchronously on the
    EventBus, and the orchestrator calls ``on_merge_complete()`` / ``on_split_complete()``
    to advance the state machine.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import structlog

from models.market_state import MarketState

logger = structlog.get_logger("strategy.complete_set")

_ZERO = Decimal("0")
_ONE = Decimal("1")
_BPS_DIVISOR = Decimal("10000")


# ── Enums ────────────────────────────────────────────────────────────


class ArbitrageDirection(str, Enum):
    """Direction of the complete-set arbitrage."""

    MERGE = "MERGE"  # Buy YES+NO, merge to collateral
    SPLIT = "SPLIT"  # Split collateral, sell YES+NO


class PairState(str, Enum):
    """State of a single pair trade."""

    IDLE = "IDLE"

    # Merge path
    PAIR_PLANNED = "PAIR_PLANNED"
    LEG1_WORKING = "LEG1_WORKING"
    LEG1_FILLED = "LEG1_FILLED"
    LEG2_WORKING = "LEG2_WORKING"
    BOTH_FILLED = "BOTH_FILLED"
    MERGING = "MERGING"
    MERGED = "MERGED"

    # Split path
    SPLITTING = "SPLITTING"
    SPLIT_DONE = "SPLIT_DONE"
    BOTH_SOLD = "BOTH_SOLD"

    # Terminal
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Valid state transitions
VALID_TRANSITIONS: dict[PairState, set[PairState]] = {
    PairState.IDLE: {PairState.PAIR_PLANNED},
    # Merge path
    PairState.PAIR_PLANNED: {
        PairState.LEG1_WORKING,
        PairState.SPLITTING,  # Split path
        PairState.CANCELLED,
    },
    PairState.LEG1_WORKING: {
        PairState.LEG1_FILLED,
        PairState.FAILED,
        PairState.CANCELLED,
    },
    PairState.LEG1_FILLED: {
        PairState.LEG2_WORKING,
        PairState.FAILED,
        PairState.CANCELLED,
    },
    PairState.LEG2_WORKING: {
        PairState.BOTH_FILLED,
        PairState.BOTH_SOLD,  # Split path: selling 2nd leg
        PairState.FAILED,
        PairState.CANCELLED,
    },
    PairState.BOTH_FILLED: {PairState.MERGING, PairState.FAILED},
    PairState.MERGING: {PairState.MERGED, PairState.FAILED},
    PairState.MERGED: {PairState.COMPLETED},
    # Split path
    PairState.SPLITTING: {PairState.SPLIT_DONE, PairState.FAILED},
    PairState.SPLIT_DONE: {PairState.LEG1_WORKING, PairState.FAILED},
    PairState.BOTH_SOLD: {PairState.COMPLETED},
    # Terminal — no further transitions
    PairState.COMPLETED: set(),
    PairState.FAILED: set(),
    PairState.CANCELLED: set(),
}


# ── Data Models ──────────────────────────────────────────────────────


@dataclass
class LegOrder:
    """Represents a single leg (buy or sell) of a pair trade."""

    leg_id: str = field(default_factory=lambda: str(uuid4())[:8])
    token_side: str = ""  # "YES" or "NO"
    side: str = ""  # "BUY" or "SELL"
    target_price: Decimal = _ZERO
    target_size: Decimal = _ZERO
    filled_price: Decimal = _ZERO
    filled_size: Decimal = _ZERO
    client_order_id: str | None = None
    filled_at: float | None = None


@dataclass
class PairTrade:
    """A complete pair trade (two legs + on-chain operation)."""

    trade_id: UUID = field(default_factory=uuid4)
    market_id: str = ""
    condition_id: str = ""
    token_id_yes: str = ""
    token_id_no: str = ""
    neg_risk: bool = False

    direction: ArbitrageDirection = ArbitrageDirection.MERGE
    state: PairState = PairState.IDLE

    # Trade parameters
    target_amount: Decimal = _ZERO  # Number of pairs
    expected_profit_usd: Decimal = _ZERO
    estimated_gas_cost_usd: Decimal = _ZERO

    # Legs
    leg1: LegOrder = field(default_factory=LegOrder)
    leg2: LegOrder = field(default_factory=LegOrder)

    # On-chain tx
    merge_tx_hash: str | None = None
    split_tx_hash: str | None = None
    actual_gas_cost_usd: Decimal = _ZERO

    # Timing
    created_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    state_history: list[tuple[PairState, float]] = field(default_factory=list)

    # Error tracking
    last_error: str | None = None
    retry_count: int = 0

    @property
    def actual_profit_usd(self) -> Decimal:
        """Compute actual profit after execution."""
        if self.direction == ArbitrageDirection.MERGE:
            # Bought YES + NO, merged to $1.00 per pair
            cost = (
                self.leg1.filled_price * self.leg1.filled_size
                + self.leg2.filled_price * self.leg2.filled_size
            )
            revenue = min(self.leg1.filled_size, self.leg2.filled_size)
            return revenue - cost - self.actual_gas_cost_usd
        else:
            # Split $1 into YES+NO, sold both
            cost = self.target_amount + self.actual_gas_cost_usd
            revenue = (
                self.leg1.filled_price * self.leg1.filled_size
                + self.leg2.filled_price * self.leg2.filled_size
            )
            return revenue - cost

    @property
    def is_terminal(self) -> bool:
        """Return True if the trade is in a terminal state."""
        return self.state in {PairState.COMPLETED, PairState.FAILED, PairState.CANCELLED}

    @property
    def elapsed_seconds(self) -> float:
        """Seconds elapsed since creation."""
        end = self.completed_at or time.monotonic()
        return end - self.created_at


@dataclass
class ArbitrageSignal:
    """Signal detected by the opportunity scanner."""

    market_id: str
    condition_id: str
    direction: ArbitrageDirection
    yes_price: Decimal
    no_price: Decimal
    combined_price: Decimal
    margin: Decimal
    estimated_gas_cost_usd: Decimal
    expected_profit_usd: Decimal
    max_size: Decimal
    timestamp: float = field(default_factory=time.monotonic)


# ── Configuration ────────────────────────────────────────────────────


@dataclass
class CompleteSetConfig:
    """Configuration for the complete-set arbitrage strategy."""

    # Minimum profit in USD to execute a pair trade (after gas)
    min_profit_usd: Decimal = Decimal("0.50")

    # Estimated gas cost per merge/split in USD
    gas_cost_per_operation_usd: Decimal = Decimal("1.00")

    # Maximum gas price in Gwei before aborting
    gas_price_abort_gwei: Decimal = Decimal("100")

    # Fee rate for taker orders on CLOB (in BPS)
    clob_fee_bps: Decimal = Decimal("0")  # Polymarket has 0 taker fee currently

    # Maximum amount per pair trade (in USDC)
    max_trade_size_usd: Decimal = Decimal("500")

    # Minimum amount per pair trade
    min_trade_size_usd: Decimal = Decimal("10")

    # Slippage buffer: add to cost estimates to account for execution variance
    slippage_buffer_bps: Decimal = Decimal("10")

    # Maximum number of concurrent pair trades
    max_concurrent_trades: int = 3

    # Maximum time for a pair trade before auto-cancel (seconds)
    max_trade_duration_s: float = 300.0

    # Cooldown between scans (seconds)
    scan_interval_s: float = 5.0

    # Maximum retries for a failed leg
    max_leg_retries: int = 2

    # Strategy tag
    strategy_tag: str = "complete_set_v1"


# ── State Machine ────────────────────────────────────────────────────


class CompleteSetStrategy:
    """State machine for complete-set and reverse arbitrage trades.

    The strategy:
    1. Scans markets for arbitrage opportunities (YES+NO price divergence)
    2. Plans pair trades with breakeven calculation including gas
    3. Executes legs sequentially (buy YES, then buy NO for merge)
    4. Performs on-chain merge/split operation
    5. Tracks state transitions with proper error handling

    Usage::

        strategy = CompleteSetStrategy(config=CompleteSetConfig())

        # Scan for opportunities
        signal = strategy.evaluate(market_state)
        if signal is not None:
            trade = strategy.plan_trade(signal, market_state)

            # Advance through states as legs fill
            strategy.transition(trade.trade_id, PairState.LEG1_WORKING)
            # ... order placed ...
            strategy.on_leg_filled(trade.trade_id, leg=1, fill_price=..., fill_size=...)
            # ... continue through state machine ...
    """

    def __init__(self, config: CompleteSetConfig | None = None) -> None:
        self._config = config or CompleteSetConfig()
        self._active_trades: dict[UUID, PairTrade] = {}
        self._completed_trades: list[PairTrade] = []

    @property
    def config(self) -> CompleteSetConfig:
        """Return current configuration (read-only)."""
        return self._config

    @property
    def active_trades(self) -> dict[UUID, PairTrade]:
        """Return active (non-terminal) trades."""
        return dict(self._active_trades)

    @property
    def completed_trades(self) -> list[PairTrade]:
        """Return completed trades history."""
        return list(self._completed_trades)

    # ── Opportunity Detection ────────────────────────────────────

    def evaluate(
        self,
        state: MarketState,
        gas_cost_usd: Decimal | None = None,
    ) -> ArbitrageSignal | None:
        """Evaluate a market for complete-set or reverse arbitrage opportunity.

        Parameters
        ----------
        state:
            Current MarketState with best bid/ask for YES and NO.
        gas_cost_usd:
            Estimated gas cost in USD. If None, uses config default.

        Returns
        -------
        ArbitrageSignal | None
            Signal if opportunity is profitable, None otherwise.
        """
        c = self._config
        gas = gas_cost_usd if gas_cost_usd is not None else c.gas_cost_per_operation_usd

        # Check concurrent trade limit for this market
        active_for_market = sum(
            1 for t in self._active_trades.values()
            if t.market_id == state.market_id and not t.is_terminal
        )
        if active_for_market >= c.max_concurrent_trades:
            return None

        # Need valid prices on both sides
        if state.yes_ask <= _ZERO or state.no_ask <= _ZERO:
            return None
        if state.yes_bid <= _ZERO or state.no_bid <= _ZERO:
            return None

        # ── Check MERGE opportunity: buy YES + buy NO at asks ────
        merge_signal = self._check_merge_opportunity(state, gas)
        if merge_signal is not None:
            return merge_signal

        # ── Check SPLIT opportunity: sell YES + sell NO at bids ──
        split_signal = self._check_split_opportunity(state, gas)
        if split_signal is not None:
            return split_signal

        return None

    def _check_merge_opportunity(
        self,
        state: MarketState,
        gas_cost_usd: Decimal,
    ) -> ArbitrageSignal | None:
        """Check if buying YES+NO at asks and merging is profitable.

        Profit = 1.0 - yes_ask - no_ask - gas_cost - slippage
        """
        c = self._config
        yes_ask = state.yes_ask
        no_ask = state.no_ask
        combined = yes_ask + no_ask

        # Fee cost per unit
        fee_cost = combined * c.clob_fee_bps / _BPS_DIVISOR

        # Slippage buffer
        slippage = combined * c.slippage_buffer_bps / _BPS_DIVISOR

        # Size limited by depth at top of book
        max_size = min(state.depth_yes_ask, state.depth_no_ask, c.max_trade_size_usd)
        if max_size < c.min_trade_size_usd:
            return None

        # Per-pair cost
        gas_per_pair = gas_cost_usd / max_size if max_size > _ZERO else gas_cost_usd

        # Margin per pair
        margin = _ONE - combined - fee_cost - slippage - gas_per_pair

        # Total expected profit
        expected_profit = margin * max_size

        if expected_profit < c.min_profit_usd or margin <= _ZERO:
            return None

        logger.info(
            "complete_set.merge_opportunity",
            market_id=state.market_id,
            yes_ask=str(yes_ask),
            no_ask=str(no_ask),
            combined=str(combined),
            margin=str(margin),
            max_size=str(max_size),
            expected_profit=str(expected_profit),
        )

        return ArbitrageSignal(
            market_id=state.market_id,
            condition_id=state.condition_id,
            direction=ArbitrageDirection.MERGE,
            yes_price=yes_ask,
            no_price=no_ask,
            combined_price=combined,
            margin=margin,
            estimated_gas_cost_usd=gas_cost_usd,
            expected_profit_usd=expected_profit,
            max_size=max_size,
        )

    def _check_split_opportunity(
        self,
        state: MarketState,
        gas_cost_usd: Decimal,
    ) -> ArbitrageSignal | None:
        """Check if splitting and selling YES+NO at bids is profitable.

        Profit = yes_bid + no_bid - 1.0 - gas_cost - slippage
        """
        c = self._config
        yes_bid = state.yes_bid
        no_bid = state.no_bid
        combined = yes_bid + no_bid

        # Fee cost per unit
        fee_cost = combined * c.clob_fee_bps / _BPS_DIVISOR

        # Slippage buffer
        slippage = combined * c.slippage_buffer_bps / _BPS_DIVISOR

        # Size limited by depth at top of book
        max_size = min(state.depth_yes_bid, state.depth_no_bid, c.max_trade_size_usd)
        if max_size < c.min_trade_size_usd:
            return None

        # Per-pair cost
        gas_per_pair = gas_cost_usd / max_size if max_size > _ZERO else gas_cost_usd

        # Margin per pair
        margin = combined - _ONE - fee_cost - slippage - gas_per_pair

        # Total expected profit
        expected_profit = margin * max_size

        if expected_profit < c.min_profit_usd or margin <= _ZERO:
            return None

        logger.info(
            "complete_set.split_opportunity",
            market_id=state.market_id,
            yes_bid=str(yes_bid),
            no_bid=str(no_bid),
            combined=str(combined),
            margin=str(margin),
            max_size=str(max_size),
            expected_profit=str(expected_profit),
        )

        return ArbitrageSignal(
            market_id=state.market_id,
            condition_id=state.condition_id,
            direction=ArbitrageDirection.SPLIT,
            yes_price=yes_bid,
            no_price=no_bid,
            combined_price=combined,
            margin=margin,
            estimated_gas_cost_usd=gas_cost_usd,
            expected_profit_usd=expected_profit,
            max_size=max_size,
        )

    # ── Trade Planning ───────────────────────────────────────────

    def plan_trade(
        self,
        signal: ArbitrageSignal,
        state: MarketState,
    ) -> PairTrade:
        """Create a PairTrade from a detected arbitrage signal.

        Parameters
        ----------
        signal:
            Detected arbitrage signal.
        state:
            Current market state for token IDs.

        Returns
        -------
        PairTrade
            Planned trade ready to be executed.
        """
        c = self._config
        trade_size = min(signal.max_size, c.max_trade_size_usd)
        trade_size = max(trade_size, c.min_trade_size_usd)

        trade = PairTrade(
            market_id=signal.market_id,
            condition_id=signal.condition_id,
            token_id_yes=state.token_id_yes,
            token_id_no=state.token_id_no,
            neg_risk=state.neg_risk,
            direction=signal.direction,
            state=PairState.IDLE,
            target_amount=trade_size,
            expected_profit_usd=signal.expected_profit_usd,
            estimated_gas_cost_usd=signal.estimated_gas_cost_usd,
        )

        if signal.direction == ArbitrageDirection.MERGE:
            # Buy YES (leg1) then buy NO (leg2) — order by cheaper first
            if signal.yes_price <= signal.no_price:
                trade.leg1 = LegOrder(
                    token_side="YES", side="BUY",
                    target_price=signal.yes_price, target_size=trade_size,
                )
                trade.leg2 = LegOrder(
                    token_side="NO", side="BUY",
                    target_price=signal.no_price, target_size=trade_size,
                )
            else:
                trade.leg1 = LegOrder(
                    token_side="NO", side="BUY",
                    target_price=signal.no_price, target_size=trade_size,
                )
                trade.leg2 = LegOrder(
                    token_side="YES", side="BUY",
                    target_price=signal.yes_price, target_size=trade_size,
                )
        else:
            # Split first, then sell YES (leg1) and NO (leg2)
            # Sell the more expensive side first (higher bid = more liquid)
            if signal.yes_price >= signal.no_price:
                trade.leg1 = LegOrder(
                    token_side="YES", side="SELL",
                    target_price=signal.yes_price, target_size=trade_size,
                )
                trade.leg2 = LegOrder(
                    token_side="NO", side="SELL",
                    target_price=signal.no_price, target_size=trade_size,
                )
            else:
                trade.leg1 = LegOrder(
                    token_side="NO", side="SELL",
                    target_price=signal.no_price, target_size=trade_size,
                )
                trade.leg2 = LegOrder(
                    token_side="YES", side="SELL",
                    target_price=signal.yes_price, target_size=trade_size,
                )

        # Transition to PAIR_PLANNED
        self._transition(trade, PairState.PAIR_PLANNED)
        self._active_trades[trade.trade_id] = trade

        logger.info(
            "complete_set.trade_planned",
            trade_id=str(trade.trade_id),
            direction=trade.direction.value,
            market_id=trade.market_id,
            target_amount=str(trade.target_amount),
            expected_profit=str(trade.expected_profit_usd),
            leg1=f"{trade.leg1.side} {trade.leg1.token_side} @ {trade.leg1.target_price}",
            leg2=f"{trade.leg2.side} {trade.leg2.token_side} @ {trade.leg2.target_price}",
        )

        return trade

    # ── State Transitions ────────────────────────────────────────

    def transition(self, trade_id: UUID, new_state: PairState) -> PairTrade:
        """Manually transition a trade to a new state.

        Parameters
        ----------
        trade_id:
            UUID of the trade to transition.
        new_state:
            Target state.

        Returns
        -------
        PairTrade
            The updated trade.

        Raises
        ------
        InvalidTransitionError
            If the transition is not valid from the current state.
        KeyError
            If the trade_id is not found.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        self._transition(trade, new_state)
        return trade

    def on_leg_filled(
        self,
        trade_id: UUID,
        leg: int,
        fill_price: Decimal,
        fill_size: Decimal,
        client_order_id: str | None = None,
    ) -> PairTrade:
        """Record a leg fill and advance the state machine.

        Parameters
        ----------
        trade_id:
            UUID of the trade.
        leg:
            Leg number (1 or 2).
        fill_price:
            Average fill price.
        fill_size:
            Filled quantity.
        client_order_id:
            Optional order ID from the CLOB.

        Returns
        -------
        PairTrade
            The updated trade.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        leg_order = trade.leg1 if leg == 1 else trade.leg2
        leg_order.filled_price = fill_price
        leg_order.filled_size = fill_size
        leg_order.filled_at = time.monotonic()
        if client_order_id is not None:
            leg_order.client_order_id = client_order_id

        # Advance state based on which leg filled
        if leg == 1:
            self._transition(trade, PairState.LEG1_FILLED)
        elif leg == 2:
            if trade.direction == ArbitrageDirection.MERGE:
                self._transition(trade, PairState.BOTH_FILLED)
            else:
                self._transition(trade, PairState.BOTH_SOLD)

        logger.info(
            "complete_set.leg_filled",
            trade_id=str(trade_id),
            leg=leg,
            token_side=leg_order.token_side,
            fill_price=str(fill_price),
            fill_size=str(fill_size),
            new_state=trade.state.value,
        )

        return trade

    def on_merge_complete(
        self,
        trade_id: UUID,
        tx_hash: str,
        gas_cost_usd: Decimal,
    ) -> PairTrade:
        """Record merge completion and advance to MERGED.

        Parameters
        ----------
        trade_id:
            UUID of the trade.
        tx_hash:
            On-chain transaction hash.
        gas_cost_usd:
            Actual gas cost in USD.

        Returns
        -------
        PairTrade
            The updated trade.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        trade.merge_tx_hash = tx_hash
        trade.actual_gas_cost_usd = gas_cost_usd
        self._transition(trade, PairState.MERGED)
        self._transition(trade, PairState.COMPLETED)

        logger.info(
            "complete_set.merge_complete",
            trade_id=str(trade_id),
            tx_hash=tx_hash,
            gas_cost_usd=str(gas_cost_usd),
            actual_profit=str(trade.actual_profit_usd),
        )

        return trade

    def on_split_complete(
        self,
        trade_id: UUID,
        tx_hash: str,
        gas_cost_usd: Decimal,
    ) -> PairTrade:
        """Record split completion and advance to SPLIT_DONE.

        Parameters
        ----------
        trade_id:
            UUID of the trade.
        tx_hash:
            On-chain transaction hash.
        gas_cost_usd:
            Actual gas cost in USD.

        Returns
        -------
        PairTrade
            The updated trade.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        trade.split_tx_hash = tx_hash
        trade.actual_gas_cost_usd = gas_cost_usd
        self._transition(trade, PairState.SPLIT_DONE)

        logger.info(
            "complete_set.split_complete",
            trade_id=str(trade_id),
            tx_hash=tx_hash,
            gas_cost_usd=str(gas_cost_usd),
        )

        return trade

    def on_failure(
        self,
        trade_id: UUID,
        error: str,
    ) -> PairTrade:
        """Record a failure and move trade to FAILED state.

        Parameters
        ----------
        trade_id:
            UUID of the trade.
        error:
            Error description.

        Returns
        -------
        PairTrade
            The updated trade.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        trade.last_error = error
        self._transition(trade, PairState.FAILED)

        logger.error(
            "complete_set.trade_failed",
            trade_id=str(trade_id),
            error=error,
            state_before=trade.state_history[-2][0].value if len(trade.state_history) > 1 else "?",
        )

        return trade

    def cancel_trade(self, trade_id: UUID, reason: str = "manual") -> PairTrade:
        """Cancel an active trade.

        Parameters
        ----------
        trade_id:
            UUID of the trade.
        reason:
            Cancellation reason.

        Returns
        -------
        PairTrade
            The updated trade.
        """
        trade = self._active_trades.get(trade_id)
        if trade is None:
            raise KeyError(f"Trade {trade_id} not found in active trades")

        trade.last_error = f"Cancelled: {reason}"
        self._transition(trade, PairState.CANCELLED)

        logger.info(
            "complete_set.trade_cancelled",
            trade_id=str(trade_id),
            reason=reason,
        )

        return trade

    # ── Housekeeping ─────────────────────────────────────────────

    def cleanup_stale_trades(self) -> list[PairTrade]:
        """Cancel trades that exceed max_trade_duration_s.

        Returns
        -------
        list[PairTrade]
            List of trades that were cancelled due to timeout.
        """
        stale: list[PairTrade] = []
        for trade in list(self._active_trades.values()):
            if trade.is_terminal:
                continue
            if trade.elapsed_seconds > self._config.max_trade_duration_s:
                self.cancel_trade(trade.trade_id, reason="timeout")
                stale.append(trade)

        return stale

    def get_trade(self, trade_id: UUID) -> PairTrade | None:
        """Get a trade by ID (active or completed)."""
        trade = self._active_trades.get(trade_id)
        if trade is not None:
            return trade
        for t in self._completed_trades:
            if t.trade_id == trade_id:
                return t
        return None

    def get_pnl_summary(self) -> dict[str, Any]:
        """Compute aggregate PnL across all completed trades.

        Returns
        -------
        dict
            Summary with total_profit, num_trades, win_rate, etc.
        """
        completed = [
            t for t in self._completed_trades
            if t.state == PairState.COMPLETED
        ]
        if not completed:
            return {
                "total_profit_usd": _ZERO,
                "num_completed": 0,
                "num_failed": len([
                    t for t in self._completed_trades if t.state == PairState.FAILED
                ]),
                "avg_profit_usd": _ZERO,
                "win_rate": _ZERO,
            }

        profits = [t.actual_profit_usd for t in completed]
        wins = sum(1 for p in profits if p > _ZERO)
        total = sum(profits, _ZERO)

        return {
            "total_profit_usd": total,
            "num_completed": len(completed),
            "num_failed": len([
                t for t in self._completed_trades if t.state == PairState.FAILED
            ]),
            "avg_profit_usd": total / len(completed) if completed else _ZERO,
            "win_rate": Decimal(str(wins)) / Decimal(str(len(completed))) if completed else _ZERO,
        }

    # ── Internals ────────────────────────────────────────────────

    def _transition(self, trade: PairTrade, new_state: PairState) -> None:
        """Execute a state transition with validation.

        Raises
        ------
        InvalidTransitionError
            If the transition is not valid.
        """
        current = trade.state
        valid_next = VALID_TRANSITIONS.get(current, set())

        if new_state not in valid_next:
            raise InvalidTransitionError(
                f"Cannot transition from {current.value} to {new_state.value}. "
                f"Valid transitions: {[s.value for s in valid_next]}"
            )

        old_state = trade.state
        trade.state = new_state
        trade.state_history.append((new_state, time.monotonic()))

        # Move to completed when terminal
        if trade.is_terminal:
            trade.completed_at = time.monotonic()
            self._active_trades.pop(trade.trade_id, None)
            self._completed_trades.append(trade)

        logger.debug(
            "complete_set.state_transition",
            trade_id=str(trade.trade_id),
            from_state=old_state.value,
            to_state=new_state.value,
        )


# ── Exceptions ───────────────────────────────────────────────────────


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    pass
