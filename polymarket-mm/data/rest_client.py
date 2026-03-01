"""CLOBRestClient — Real REST client for Polymarket CLOB API.

Wraps py-clob-client ClobClient for:
- Orderbook snapshots via REST
- Active markets fetch with metadata (token_ids, tick_size, neg_risk)
- Balance & allowance queries
- Order lifecycle (create, cancel, list)
- L2 auth via HMAC-SHA256 (handled by py-clob-client)
- Rate limiting with token bucket
- Retry with exponential backoff
"""

from __future__ import annotations

import asyncio
import functools
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import structlog

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    BalanceAllowanceParams,
    BookParams,
    OpenOrderParams,
    OrderArgs,
    OrderType as ClobOrderType,
    PartialCreateOrderOptions,
    TradeParams,
)
from py_clob_client.order_builder.constants import BUY, SELL

logger = structlog.get_logger("data.rest_client")

# Default Polymarket CLOB endpoints
_DEFAULT_BASE_URL = "https://clob.polymarket.com"


class _RateLimiter:
    """Simple token-bucket rate limiter.

    Parameters
    ----------
    rate:
        Max requests per second.
    burst:
        Maximum burst size (tokens in bucket).
    """

    def __init__(self, rate: float = 10.0, burst: int = 20) -> None:
        self._rate = rate
        self._burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


class CLOBRestClient:
    """Async REST client for the Polymarket CLOB API.

    Wraps py-clob-client (sync) by running calls in an executor.
    All public methods are async-safe and rate-limited.

    Parameters
    ----------
    base_url:
        CLOB REST API base URL.
    chain_id:
        Polygon chain ID (137 for mainnet).
    private_key:
        Hex-encoded private key for signing.
    api_key:
        L2 API key.
    api_secret:
        L2 API secret (base64).
    api_passphrase:
        L2 API passphrase.
    rate_limit_rps:
        Max requests per second (default 10).
    max_retries:
        Maximum number of retries on transient errors (default 3).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        chain_id: int = 137,
        private_key: str = "",
        api_key: str = "",
        api_secret: str = "",
        api_passphrase: str = "",
        rate_limit_rps: float = 10.0,
        max_retries: int = 3,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._chain_id = chain_id
        self._private_key = private_key
        self._api_key = api_key
        self._api_secret = api_secret
        self._api_passphrase = api_passphrase
        self._max_retries = max_retries
        self._rate_limiter = _RateLimiter(rate=rate_limit_rps, burst=int(rate_limit_rps * 2))
        self._client: ClobClient | None = None
        self._connected = False

    @property
    def clob_client(self) -> ClobClient:
        """Return the underlying ClobClient (for direct use if needed)."""
        if self._client is None:
            raise RuntimeError("Call connect() before using the client")
        return self._client

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the ClobClient with L2 auth credentials."""
        creds = ApiCreds(
            api_key=self._api_key,
            api_secret=self._api_secret,
            api_passphrase=self._api_passphrase,
        )

        self._client = ClobClient(
            host=self._base_url,
            chain_id=self._chain_id,
            key=self._private_key,
            creds=creds,
        )

        # Verify connectivity
        ok = await self._run_sync(self._client.get_ok)
        logger.info(
            "rest_client.connected",
            base_url=self._base_url,
            address=self._client.get_address(),
            ok=ok,
        )
        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect the client."""
        self._client = None
        self._connected = False
        logger.info("rest_client.disconnected")

    # ── Public API: Markets ──────────────────────────────────────

    async def get_active_markets(
        self, max_pages: int = 5
    ) -> list[dict[str, Any]]:
        """Fetch active markets with metadata by paginating through the API.

        Returns a list of dicts with::

            {
                "market_id": str (condition_id),
                "condition_id": str,
                "question": str,
                "token_id_yes": str,
                "token_id_no": str,
                "tick_size": Decimal,
                "min_order_size": Decimal,
                "neg_risk": bool,
            }
        """
        assert self._client is not None, "Call connect() first"

        all_markets: list[dict[str, Any]] = []
        cursor = "MA=="

        for page in range(max_pages):
            await self._rate_limiter.acquire()
            raw = await self._run_sync(
                self._client.get_sampling_simplified_markets,
                next_cursor=cursor,
            )

            data = raw if isinstance(raw, list) else raw.get("data", [])
            next_cursor = raw.get("next_cursor", "") if isinstance(raw, dict) else ""

            for m in data:
                tokens = m.get("tokens", [])
                if len(tokens) < 2:
                    continue

                yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), tokens[0])
                no_token = next((t for t in tokens if t.get("outcome") == "No"), tokens[-1])

                all_markets.append({
                    "market_id": m.get("condition_id", ""),
                    "condition_id": m.get("condition_id", ""),
                    "question": m.get("question", ""),
                    "token_id_yes": yes_token.get("token_id", ""),
                    "token_id_no": no_token.get("token_id", ""),
                    "tick_size": Decimal(str(m.get("minimum_tick_size", "0.01"))),
                    "min_order_size": Decimal(str(m.get("minimum_order_size", "5"))),
                    "neg_risk": m.get("neg_risk", False),
                    "active": m.get("active", True),
                    "closed": m.get("closed", False),
                })

            if not next_cursor or next_cursor == "LTE=":
                break
            cursor = next_cursor

        logger.info("rest_client.get_active_markets", count=len(all_markets))
        return all_markets

    async def get_market_info(self, condition_id: str) -> dict[str, Any]:
        """Fetch detailed market info including tick_size and token IDs."""
        assert self._client is not None, "Call connect() first"

        await self._rate_limiter.acquire()
        raw = await self._run_sync(self._client.get_market, condition_id)

        tokens = raw.get("tokens", [])
        yes_token = next((t for t in tokens if t.get("outcome") == "Yes"), tokens[0] if tokens else {})
        no_token = next((t for t in tokens if t.get("outcome") == "No"), tokens[-1] if tokens else {})

        return {
            "market_id": raw.get("condition_id", condition_id),
            "condition_id": raw.get("condition_id", condition_id),
            "question": raw.get("question", ""),
            "token_id_yes": yes_token.get("token_id", ""),
            "token_id_no": no_token.get("token_id", ""),
            "tick_size": Decimal(str(raw.get("minimum_tick_size", "0.01"))),
            "min_order_size": Decimal(str(raw.get("minimum_order_size", "5"))),
            "neg_risk": raw.get("neg_risk", False),
        }

    # ── Public API: Orderbook ────────────────────────────────────

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Fetch a full orderbook snapshot for a single token.

        Returns dict with ``bids``, ``asks``, ``timestamp``, ``hash``,
        ``tick_size``, ``min_order_size``, ``neg_risk``.
        """
        assert self._client is not None, "Call connect() first"

        await self._rate_limiter.acquire()
        ob = await self._run_sync(self._client.get_order_book, token_id)

        # OrderBookSummary → dict
        bids_raw = ob.bids if hasattr(ob, "bids") and ob.bids else []
        asks_raw = ob.asks if hasattr(ob, "asks") and ob.asks else []

        def _parse_level(lvl: Any) -> dict[str, Decimal]:
            if hasattr(lvl, "price"):
                return {"price": Decimal(str(lvl.price)), "size": Decimal(str(lvl.size))}
            return {"price": Decimal(str(lvl["price"])), "size": Decimal(str(lvl["size"]))}

        result = {
            "asset_id": getattr(ob, "asset_id", token_id),
            "market": getattr(ob, "market", ""),
            "bids": [_parse_level(b) for b in bids_raw],
            "asks": [_parse_level(a) for a in asks_raw],
            "timestamp": datetime.now(timezone.utc),
            "hash": getattr(ob, "hash", None),
            "tick_size": Decimal(str(getattr(ob, "tick_size", "0.01") or "0.01")),
            "min_order_size": Decimal(str(getattr(ob, "min_order_size", "5") or "5")),
            "neg_risk": getattr(ob, "neg_risk", False),
            "last_trade_price": getattr(ob, "last_trade_price", None),
        }

        logger.debug(
            "rest_client.get_orderbook",
            token_id=token_id[:20] + "...",
            bids=len(result["bids"]),
            asks=len(result["asks"]),
        )
        return result

    async def get_midpoint(self, token_id: str) -> Decimal:
        """Get the midpoint price for a token."""
        assert self._client is not None
        await self._rate_limiter.acquire()
        mid = await self._run_sync(self._client.get_midpoint, token_id)
        # API returns {"mid": "0.50"} or a plain value
        if isinstance(mid, dict):
            mid = mid.get("mid", "0")
        return Decimal(str(mid))

    async def get_spread(self, token_id: str) -> Decimal:
        """Get the spread for a token."""
        assert self._client is not None
        await self._rate_limiter.acquire()
        spread = await self._run_sync(self._client.get_spread, token_id)
        # API returns {"spread": "0.02"} or a plain value
        if isinstance(spread, dict):
            spread = spread.get("spread", "0")
        return Decimal(str(spread))

    # ── Public API: Balances ─────────────────────────────────────

    async def get_balance_allowance(
        self, asset_type: str = "COLLATERAL", token_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch balance and allowance for the wallet.

        Parameters
        ----------
        asset_type:
            "COLLATERAL" for USDC or "CONDITIONAL" for CT tokens.
        token_id:
            Required when asset_type is "CONDITIONAL".

        Returns dict with ``balance`` and ``allowances``.
        """
        assert self._client is not None, "Call connect() first"

        await self._rate_limiter.acquire()
        if token_id:
            from py_clob_client.clob_types import AssetType as _AT
            at = _AT.CONDITIONAL if asset_type == "CONDITIONAL" else _AT.COLLATERAL
            params = BalanceAllowanceParams(asset_type=at, token_id=token_id)
        else:
            params = BalanceAllowanceParams(asset_type=asset_type)
        result = await self._run_sync(self._client.get_balance_allowance, params)

        logger.info(
            "rest_client.balance_allowance",
            asset_type=asset_type,
            balance=result.get("balance", "0"),
        )
        return result

    async def get_price(
        self, token_id: str, side: str = "sell",
    ) -> Decimal:
        """Fetch live price for a token from CLOB.

        Returns the price as Decimal, or 0 if unavailable.
        """
        assert self._client is not None, "Call connect() first"
        await self._rate_limiter.acquire()
        try:
            result = await self._run_sync(
                self._client.get_last_trade_price, token_id)
            return Decimal(str(result.get("price", "0")))
        except Exception:
            return Decimal("0")

    # ── Public API: Orders ───────────────────────────────────────

    async def create_and_post_order(
        self,
        token_id: str,
        price: float,
        size: float,
        side: str,
        order_type: str = "GTC",
        post_only: bool = True,
        tick_size: Optional[str] = None,
        neg_risk: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Create, sign, and post an order in one step.

        Parameters
        ----------
        token_id:
            The conditional token ID.
        price:
            Order price (e.g., 0.50).
        size:
            Order size in shares.
        side:
            "BUY" or "SELL".
        order_type:
            "GTC", "FOK", "GTD", or "FAK".
        post_only:
            If True, order will only be maker (default True for MM).
        tick_size:
            Tick size for the market (auto-detected if None).
        neg_risk:
            Whether this is a neg-risk market (auto-detected if None).
        """
        assert self._client is not None, "Call connect() first"

        clob_side = BUY if side.upper() == "BUY" else SELL
        clob_order_type = getattr(ClobOrderType, order_type.upper(), ClobOrderType.GTC)

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=clob_side,
        )

        options = None
        if tick_size is not None or neg_risk is not None:
            options = PartialCreateOrderOptions(
                tick_size=tick_size,
                neg_risk=neg_risk,
            )

        await self._rate_limiter.acquire()

        # create_and_post_order handles signing + posting
        result = await self._run_sync(
            self._client.create_and_post_order,
            order_args,
            options,
        )

        logger.info(
            "rest_client.order_posted",
            token_id=token_id[:20] + "...",
            side=side,
            price=price,
            size=size,
            order_type=order_type,
            result=str(result)[:200],
        )
        return result

    async def post_order(
        self,
        signed_order: Any,
        order_type: str = "GTC",
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Post a pre-signed order.

        Parameters
        ----------
        signed_order:
            A signed order object from ``create_order()``.
        order_type:
            "GTC", "FOK", etc.
        post_only:
            Maker-only flag.
        """
        assert self._client is not None

        clob_type = getattr(ClobOrderType, order_type.upper(), ClobOrderType.GTC)
        await self._rate_limiter.acquire()
        result = await self._run_sync(
            self._client.post_order, signed_order, clob_type, post_only,
        )
        return result

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order by its exchange order ID.

        Returns True if successfully cancelled.
        """
        assert self._client is not None, "Call connect() first"

        await self._rate_limiter.acquire()
        try:
            result = await self._run_sync(self._client.cancel, order_id)
            logger.info("rest_client.order_cancelled", order_id=order_id, result=result)
            return True
        except Exception as exc:
            logger.warning(
                "rest_client.cancel_failed",
                order_id=order_id,
                error=str(exc),
            )
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        assert self._client is not None

        await self._rate_limiter.acquire()
        try:
            result = await self._run_sync(self._client.cancel_all)
            logger.info("rest_client.all_orders_cancelled", result=result)
            return True
        except Exception as exc:
            logger.warning("rest_client.cancel_all_failed", error=str(exc))
            return False

    async def get_open_orders(
        self,
        market: str = "",
        asset_id: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch open orders, optionally filtered by market or asset.

        Returns a list of order dicts from the exchange.
        """
        assert self._client is not None, "Call connect() first"

        params = OpenOrderParams(market=market or None, asset_id=asset_id or None)
        await self._rate_limiter.acquire()
        result = await self._run_sync(self._client.get_orders, params)

        orders = result if isinstance(result, list) else result.get("data", [])
        logger.debug("rest_client.open_orders", count=len(orders))
        return orders

    async def get_trades(
        self,
        market: str = "",
        asset_id: str = "",
    ) -> list[dict[str, Any]]:
        """Fetch trade history."""
        assert self._client is not None

        params = TradeParams(market=market or None, asset_id=asset_id or None)
        await self._rate_limiter.acquire()
        result = await self._run_sync(self._client.get_trades, params)
        return result if isinstance(result, list) else result.get("data", [])

    # ── Public API: Utility ──────────────────────────────────────

    async def get_server_time(self) -> int:
        """Fetch server unix timestamp."""
        assert self._client is not None
        await self._rate_limiter.acquire()
        return await self._run_sync(self._client.get_server_time)

    async def get_tick_size(self, token_id: str) -> str:
        """Get tick size for a token (e.g. '0.01', '0.001')."""
        assert self._client is not None
        await self._rate_limiter.acquire()
        return await self._run_sync(self._client.get_tick_size, token_id)

    async def get_neg_risk(self, token_id: str) -> bool:
        """Check if a token belongs to a neg-risk market."""
        assert self._client is not None
        await self._rate_limiter.acquire()
        return await self._run_sync(self._client.get_neg_risk, token_id)

    # ── Internal: run sync in executor ───────────────────────────

    async def _run_sync(self, fn, *args, **kwargs) -> Any:
        """Run a synchronous py-clob-client function in a thread executor.

        Retries on transient errors with exponential backoff.
        """
        loop = asyncio.get_running_loop()
        last_exc: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                result = await loop.run_in_executor(
                    None,
                    functools.partial(fn, *args, **kwargs),
                )
                return result
            except Exception as exc:
                error_str = str(exc)
                # Retry on server errors / rate limits / connection errors
                is_retryable = any(
                    indicator in error_str.lower()
                    for indicator in [
                        "429", "500", "502", "503", "504",
                        "timeout", "connection", "rate",
                    ]
                )
                if is_retryable and attempt < self._max_retries:
                    delay = 2 ** attempt
                    logger.warning(
                        "rest_client.retrying",
                        fn=fn.__name__,
                        attempt=attempt,
                        delay=delay,
                        error=error_str[:200],
                    )
                    last_exc = exc
                    await asyncio.sleep(delay)
                    continue
                raise

        raise RuntimeError(
            f"REST call failed after {self._max_retries} retries"
        ) from last_exc
