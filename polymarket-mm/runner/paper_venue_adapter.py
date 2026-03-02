"""runner.paper_venue_adapter — VenueAdapter wrapping PaperVenue.

Drains fill events from EventBus for ``process_fills()``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from core.event_bus import EventBus
from models.order import Order
from paper.paper_venue import PaperVenue
from runner.venue_adapter import VenueAdapter

logger = structlog.get_logger("runner.paper_venue_adapter")


class PaperVenueAdapter(VenueAdapter):
    """VenueAdapter backed by the simulated PaperVenue.

    Fill events are collected via an internal EventBus subscription
    and returned from ``process_fills()``.
    """

    def __init__(self, venue: PaperVenue, event_bus: EventBus) -> None:
        self._venue = venue
        self._event_bus = event_bus
        self._pending_fills: list[dict] = []
        self._fill_counter = 0

    @property
    def mode(self) -> str:
        return "paper"

    @property
    def venue(self) -> PaperVenue:
        """Expose underlying venue for direct access when needed."""
        return self._venue

    async def connect(self) -> None:
        await self._venue.connect()

    async def disconnect(self) -> None:
        await self._venue.disconnect()

    async def submit_order(self, order: Order) -> Order:
        result = await self._venue.submit_order(order)
        return result

    async def cancel_order(self, client_order_id: UUID) -> bool:
        try:
            await self._venue.cancel_order(client_order_id)
            return True
        except Exception:
            return False

    async def cancel_all_orders(self) -> None:
        open_orders = await self._venue.get_open_orders()
        for oo in open_orders:
            try:
                await self._venue.cancel_order(oo.client_order_id)
            except Exception:
                pass

    async def cancel_market_orders(self, market_id: str) -> None:
        open_orders = await self._venue.get_open_orders()
        for oo in open_orders:
            if oo.market_id == market_id:
                try:
                    await self._venue.cancel_order(oo.client_order_id)
                except Exception:
                    pass

    async def get_open_orders(self) -> list[Order]:
        return await self._venue.get_open_orders()

    def drain_fill_event(self, payload: dict) -> None:
        """Called externally (from fill_event_loop) to queue a fill.

        This method is invoked by the pipeline's EventBus subscriber
        whenever a 'fill' event arrives from the PaperVenue.
        """
        self._fill_counter += 1
        fill = {
            "market_id": payload.get("market_id", ""),
            "token_id": payload.get("token_id", ""),
            "side": payload.get("side", ""),
            "fill_price": Decimal(str(payload.get("fill_price", "0"))),
            "fill_qty": Decimal(str(payload.get("fill_qty", "0"))),
            "fee": Decimal(str(payload.get("fee", "0"))),
            "fill_id": f"paper-{self._fill_counter}",
        }
        self._pending_fills.append(fill)

    async def process_fills(self) -> list[dict]:
        """Return accumulated fills and clear the buffer."""
        fills = list(self._pending_fills)
        self._pending_fills.clear()
        return fills
