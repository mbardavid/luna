"""QueueTracker — FIFO queue position estimation for resting orders.

Estimates where each of our resting orders sits in the CLOB's
price-time priority queue, enabling informed reprice decisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

from models.order import Order, OrderStatus, Side

logger = structlog.get_logger("execution.queue_tracker")


@dataclass
class _QueueEntry:
    """Estimated position of one of our orders at a price level."""

    client_order_id: Any  # UUID
    side: Side
    price: Decimal
    size: Decimal
    estimated_ahead: Decimal  # total size ahead of us in the queue


class QueueTracker:
    """Estimates FIFO queue position for resting orders.

    The tracker maintains an estimate of how much size is ahead of
    each of our orders at their price level.  Book deltas are used
    to refine the estimate: size removed from our level is assumed
    to come from orders ahead of us (filled in FIFO order).

    Parameters
    ----------
    reprice_threshold:
        Fraction of remaining queue advantage (0‑1).  When the
        estimated queue position at the *current* price, expressed as
        a ratio of original position, exceeds this threshold **after**
        a potential reprice, we suggest repricing.  Lower values make
        repricing more aggressive.
    """

    def __init__(self, reprice_threshold: Decimal = Decimal("0.5")) -> None:
        self._entries: dict[Any, _QueueEntry] = {}  # client_order_id -> entry
        self._reprice_threshold = reprice_threshold

    # ── Order registration ───────────────────────────────────────

    def register_order(self, order: Order, depth_ahead: Decimal) -> None:
        """Register a new resting order with the estimated size ahead.

        Parameters
        ----------
        order:
            The resting order.
        depth_ahead:
            Total size at this price level that was submitted before us.
        """
        entry = _QueueEntry(
            client_order_id=order.client_order_id,
            side=order.side,
            price=order.price,
            size=order.size,
            estimated_ahead=max(Decimal("0"), depth_ahead),
        )
        self._entries[order.client_order_id] = entry
        logger.debug(
            "queue_tracker.register",
            client_order_id=str(order.client_order_id),
            price=str(order.price),
            depth_ahead=str(depth_ahead),
        )

    def unregister_order(self, client_order_id: Any) -> None:
        """Remove an order from tracking (e.g. after cancel or fill)."""
        self._entries.pop(client_order_id, None)

    # ── Book delta processing ────────────────────────────────────

    def update(self, book_delta: dict[str, Any]) -> None:
        """Update queue estimates from a book delta event.

        Expected ``book_delta`` keys:

        - ``side``: ``"BUY"`` or ``"SELL"``
        - ``price``: ``Decimal`` — the price level that changed
        - ``old_size``: ``Decimal`` — previous total depth at this level
        - ``new_size``: ``Decimal`` — current total depth at this level

        Size decreases at a level are attributed to fills of orders
        ahead of us (FIFO assumption).
        """
        try:
            side_str = book_delta.get("side", "")
            price = Decimal(str(book_delta.get("price", "0")))
            old_size = Decimal(str(book_delta.get("old_size", "0")))
            new_size = Decimal(str(book_delta.get("new_size", "0")))
        except Exception:
            logger.warning("queue_tracker.invalid_delta", delta=book_delta)
            return

        delta = old_size - new_size  # positive = size removed

        if delta <= Decimal("0"):
            # Size was added — doesn't help our position estimate
            # (new orders go behind us in FIFO)
            return

        # Attribute removed size to orders ahead of our entries at this price
        for entry in self._entries.values():
            if str(entry.side.value) != side_str:
                continue
            if entry.price != price:
                continue
            entry.estimated_ahead = max(
                Decimal("0"), entry.estimated_ahead - delta
            )

    # ── Query ────────────────────────────────────────────────────

    def estimated_position(self, order: Order) -> int:
        """Return the estimated queue position (0-based) for *order*.

        Position 0 means we are next in line.  Returns -1 if the order
        is not tracked.
        """
        entry = self._entries.get(order.client_order_id)
        if entry is None:
            return -1
        # Position approximated as ahead_size / our_size (integer)
        if entry.size <= Decimal("0"):
            return 0
        return int(entry.estimated_ahead / entry.size)

    def estimated_ahead(self, order: Order) -> Decimal:
        """Return the estimated size ahead of *order* in the queue.

        Returns ``Decimal("-1")`` if the order is not tracked.
        """
        entry = self._entries.get(order.client_order_id)
        if entry is None:
            return Decimal("-1")
        return entry.estimated_ahead

    def should_reprice(self, order: Order, new_price: Decimal) -> bool:
        """Decide whether repricing is worthwhile.

        Repricing means losing our current queue position entirely
        (going to the back of the queue at the new price).  This
        method returns True if the benefit of the new price outweighs
        the cost of losing queue priority.

        Heuristic: reprice when the fraction of queue consumed
        (i.e. how close we are to the front) is below the threshold,
        meaning we haven't earned much queue priority yet.
        """
        entry = self._entries.get(order.client_order_id)
        if entry is None:
            # Not tracked — repricing has no queue cost
            return True

        if new_price == entry.price:
            # Same price — no reprice needed
            return False

        # If we're already at the front (or very close), don't reprice
        # unless it's a price improvement.
        initial_ahead = entry.estimated_ahead + entry.size  # rough total at level
        if initial_ahead <= Decimal("0"):
            return True

        # fraction_consumed = how much of the queue ahead has been cleared
        fraction_consumed = Decimal("1") - (entry.estimated_ahead / initial_ahead)

        # If we haven't consumed much of the queue (< threshold),
        # it's cheap to reprice.
        return fraction_consumed < self._reprice_threshold
