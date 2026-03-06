"""Direct public CLOB market-data client.

This client is intentionally separate from the authenticated py-clob-client
path so public reads stay direct while private traffic can remain proxy-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import httpx

_DEFAULT_BASE_URL = "https://clob.polymarket.com"


def _decimal(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


@dataclass(frozen=True, slots=True)
class ExecutableQuote:
    token_id: str
    action: Literal["sell_token", "buy_token"]
    price: Decimal
    source: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "action": self.action,
            "price": str(self.price),
            "source": self.source,
            "timestamp": self.timestamp,
        }


class PublicClobQuoteClient:
    """Direct HTTP client for public Polymarket CLOB endpoints."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0))

    async def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        async with httpx.AsyncClient(timeout=self._timeout, http2=True, follow_redirects=True) as client:
            response = await client.get(f"{self._base_url}{path}", params=params)
            response.raise_for_status()
            return response.json()

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        payload = await self._get_json("/book", params={"token_id": token_id})
        bids = payload.get("bids", []) if isinstance(payload, dict) else []
        asks = payload.get("asks", []) if isinstance(payload, dict) else []

        def _parse(level: Any) -> dict[str, Decimal]:
            if isinstance(level, dict):
                return {
                    "price": _decimal(level.get("price")),
                    "size": _decimal(level.get("size")),
                }
            return {
                "price": _decimal(getattr(level, "price", 0)),
                "size": _decimal(getattr(level, "size", 0)),
            }

        return {
            "asset_id": payload.get("asset_id", token_id) if isinstance(payload, dict) else token_id,
            "market": payload.get("market", "") if isinstance(payload, dict) else "",
            "bids": [_parse(level) for level in bids],
            "asks": [_parse(level) for level in asks],
            "timestamp": datetime.now(timezone.utc),
            "hash": payload.get("hash") if isinstance(payload, dict) else None,
            "tick_size": _decimal((payload or {}).get("tick_size", "0.01")),
            "min_order_size": _decimal((payload or {}).get("min_order_size", "5")),
            "neg_risk": bool((payload or {}).get("neg_risk", False)),
            "last_trade_price": (payload or {}).get("last_trade_price"),
        }

    async def get_midpoint(self, token_id: str) -> Decimal:
        payload = await self._get_json("/midpoint", params={"token_id": token_id})
        if isinstance(payload, dict):
            return _decimal(payload.get("mid", "0"))
        return _decimal(payload)

    async def get_spread(self, token_id: str) -> Decimal:
        payload = await self._get_json("/spread", params={"token_id": token_id})
        if isinstance(payload, dict):
            return _decimal(payload.get("spread", "0"))
        return _decimal(payload)

    async def get_last_trade_price(self, token_id: str) -> Decimal:
        payload = await self._get_json("/last-trade-price", params={"token_id": token_id})
        if isinstance(payload, dict):
            return _decimal(payload.get("price", "0"))
        return _decimal(payload)

    async def get_executable_quote(
        self,
        token_id: str,
        *,
        action: Literal["sell_token", "buy_token"],
    ) -> ExecutableQuote:
        quote_side = "buy" if action == "sell_token" else "sell"
        payload = await self._get_json("/price", params={"token_id": token_id, "side": quote_side})
        if isinstance(payload, dict):
            price = _decimal(payload.get("price", "0"))
        else:
            price = _decimal(payload)
        return ExecutableQuote(
            token_id=token_id,
            action=action,
            price=price,
            source=f"clob.price?side={quote_side}",
        )

    async def get_market(self, condition_id: str) -> dict[str, Any]:
        payload = await self._get_json(f"/markets/{condition_id}")
        return payload if isinstance(payload, dict) else {}

    async def get_sampling_simplified_markets(self, next_cursor: str = "MA==") -> Any:
        return await self._get_json("/sampling-simplified-markets", params={"next_cursor": next_cursor})
