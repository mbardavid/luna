"""CLOBRestClient — REST client for Polymarket CLOB API.

Provides:
- Orderbook snapshots via REST
- Active markets fetch with metadata (token_ids, tick_size, neg_risk)
- Rate limiting with token bucket
- httpx.AsyncClient with automatic retry
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
import structlog

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

    Parameters
    ----------
    base_url:
        CLOB REST API base URL.
    api_key:
        Optional API key for authenticated endpoints.
    rate_limit_rps:
        Max requests per second (default 10).
    max_retries:
        Maximum number of retries on transient errors (default 3).
    timeout:
        Request timeout in seconds (default 10).
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE_URL,
        api_key: str = "",
        rate_limit_rps: float = 10.0,
        max_retries: int = 3,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_retries = max_retries
        self._timeout = timeout
        self._rate_limiter = _RateLimiter(rate=rate_limit_rps, burst=int(rate_limit_rps * 2))
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        """Create the httpx AsyncClient."""
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(self._timeout),
            follow_redirects=True,
        )
        logger.info("rest_client.connected", base_url=self._base_url)

    async def disconnect(self) -> None:
        """Close the httpx AsyncClient."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("rest_client.disconnected")

    # ── Public API ───────────────────────────────────────────────

    async def get_active_markets(self) -> list[dict[str, Any]]:
        """Fetch active markets with metadata.

        Returns a list of dicts with at least::

            {
                "market_id": str,
                "condition_id": str,
                "token_id_yes": str,
                "token_id_no": str,
                "tick_size": Decimal,
                "min_order_size": Decimal,
                "neg_risk": bool,
            }

        NOTE: This is a stub — returns sample data in dev mode.
        In production, parses real API response from ``/markets``.
        """
        # STUB: In production, call GET /markets and parse
        # data = await self._request("GET", "/markets", params={"active": "true"})
        logger.info("rest_client.get_active_markets")

        # Return stub data for development
        return [
            {
                "market_id": "market-btc-100k",
                "condition_id": "0xabc123",
                "token_id_yes": "tok-btc-100k-yes",
                "token_id_no": "tok-btc-100k-no",
                "tick_size": Decimal("0.01"),
                "min_order_size": Decimal("5"),
                "neg_risk": False,
                "market_type": "CRYPTO_5M",
                "description": "Will BTC reach $100k?",
            }
        ]

    async def get_orderbook(self, token_id: str) -> dict[str, Any]:
        """Fetch a full orderbook snapshot for a single token.

        Parameters
        ----------
        token_id:
            The token ID to fetch the book for.

        Returns
        -------
        dict with ``bids``, ``asks``, ``timestamp``, ``hash``.

        NOTE: Stub — in production, calls GET ``/book?token_id=...``
        """
        # STUB: In production:
        # data = await self._request("GET", "/book", params={"token_id": token_id})
        logger.info("rest_client.get_orderbook", token_id=token_id)

        return {
            "bids": [
                {"price": Decimal("0.45"), "size": Decimal("100")},
                {"price": Decimal("0.44"), "size": Decimal("200")},
                {"price": Decimal("0.43"), "size": Decimal("150")},
            ],
            "asks": [
                {"price": Decimal("0.55"), "size": Decimal("100")},
                {"price": Decimal("0.56"), "size": Decimal("180")},
                {"price": Decimal("0.57"), "size": Decimal("120")},
            ],
            "timestamp": datetime.now(timezone.utc),
            "hash": None,
        }

    async def get_market_info(self, market_id: str) -> dict[str, Any]:
        """Fetch detailed market info including tick_size and token IDs.

        NOTE: Stub — in production calls GET ``/markets/{market_id}``
        """
        logger.info("rest_client.get_market_info", market_id=market_id)
        return {
            "market_id": market_id,
            "condition_id": f"cond-{market_id}",
            "token_id_yes": f"{market_id}-yes",
            "token_id_no": f"{market_id}-no",
            "tick_size": Decimal("0.01"),
            "min_order_size": Decimal("5"),
            "neg_risk": False,
        }

    # ── Internal: HTTP with retry ────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an HTTP request with rate limiting and retry.

        Retries on 429, 500, 502, 503, 504 with exponential backoff.
        """
        assert self._client is not None, "Call connect() before making requests"

        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            await self._rate_limiter.acquire()

            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                )

                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "2"))
                    logger.warning(
                        "rest_client.rate_limited",
                        path=path,
                        retry_after=retry_after,
                        attempt=attempt,
                    )
                    await asyncio.sleep(retry_after)
                    continue

                response.raise_for_status()
                return response.json()  # type: ignore[no-any-return]

            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in {500, 502, 503, 504}:
                    delay = 2 ** attempt
                    logger.warning(
                        "rest_client.server_error",
                        status=exc.response.status_code,
                        path=path,
                        delay=delay,
                        attempt=attempt,
                    )
                    last_exc = exc
                    await asyncio.sleep(delay)
                    continue
                raise

            except (httpx.ConnectError, httpx.ReadTimeout) as exc:
                delay = 2 ** attempt
                logger.warning(
                    "rest_client.connection_error",
                    error=str(exc),
                    path=path,
                    delay=delay,
                    attempt=attempt,
                )
                last_exc = exc
                await asyncio.sleep(delay)
                continue

        raise RuntimeError(
            f"REST request failed after {self._max_retries} retries: {path}"
        ) from last_exc
