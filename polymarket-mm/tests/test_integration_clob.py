"""Integration tests for Polymarket CLOB real API connections.

These tests connect to the LIVE Polymarket CLOB API.
They require valid API credentials in environment variables.

Tests are marked with ``@pytest.mark.integration`` and skipped
if credentials are not available.

Run with: pytest tests/test_integration_clob.py -v -s
"""

from __future__ import annotations

import asyncio
import os
import json
import time
from decimal import Decimal

import pytest

# ── Credential loading ──────────────────────────────────────────

def _load_creds_from_systemd():
    """Load creds from systemd drop-in if env vars not set."""
    conf_path = os.path.expanduser(
        "~/.config/systemd/user/openclaw-gateway.service.d/polymarket-env.conf"
    )
    if not os.path.exists(conf_path):
        return

    with open(conf_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("Environment="):
                # Parse Environment="KEY=VALUE"
                eq_part = line.split("=", 1)[1].strip().strip('"')
                key, _, value = eq_part.partition("=")
                value = value.strip('"')
                if key and value and not os.environ.get(key):
                    os.environ[key] = value


_load_creds_from_systemd()

API_KEY = os.environ.get("POLYMARKET_API_KEY", "")
API_SECRET = os.environ.get("POLYMARKET_SECRET", "")
API_PASSPHRASE = os.environ.get("POLYMARKET_PASSPHRASE", "")
PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")

HAS_CREDS = bool(API_KEY and API_SECRET and API_PASSPHRASE and PRIVATE_KEY)

skip_no_creds = pytest.mark.skipif(
    not HAS_CREDS,
    reason="Polymarket API credentials not available (set POLYMARKET_* env vars)",
)


# ── Helper: get a known active token_id ─────────────────────────

async def _get_active_token_id() -> str:
    """Fetch a token_id from a market with an active orderbook."""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://clob.polymarket.com/sampling-markets",
            params={"next_cursor": "MA=="},
        )
        data = resp.json()
        markets = data if isinstance(data, list) else data.get("data", [])

        for m in markets[:20]:
            tokens = m.get("tokens", [])
            if len(tokens) >= 2:
                tid = tokens[0]["token_id"]
                # Quick check that orderbook exists
                resp2 = await client.get(
                    f"https://clob.polymarket.com/book?token_id={tid}"
                )
                if resp2.status_code == 200:
                    ob = resp2.json()
                    if ob.get("bids") or ob.get("asks"):
                        return tid

    pytest.skip("No active market found for testing")
    return ""


# =====================================================================
# Test 1: REST client — connectivity & server time
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_connectivity():
    """Test basic REST client connectivity and server time."""
    from data.rest_client import CLOBRestClient

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        server_time = await client.get_server_time()
        assert isinstance(server_time, (int, str))
        # Server time should be within 60s of local time
        now = int(time.time())
        assert abs(int(server_time) - now) < 60, f"Server time drift: {server_time} vs {now}"
    finally:
        await client.disconnect()


# =====================================================================
# Test 2: REST client — orderbook
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_orderbook():
    """Test fetching a real orderbook via REST."""
    from data.rest_client import CLOBRestClient

    token_id = await _get_active_token_id()

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        ob = await client.get_orderbook(token_id)

        assert "bids" in ob
        assert "asks" in ob
        assert isinstance(ob["bids"], list)
        assert isinstance(ob["asks"], list)

        # At least one side should have data
        total_levels = len(ob["bids"]) + len(ob["asks"])
        assert total_levels > 0, "Orderbook is completely empty"

        # Check Decimal types
        if ob["bids"]:
            assert isinstance(ob["bids"][0]["price"], Decimal)
            assert isinstance(ob["bids"][0]["size"], Decimal)

        # Check metadata
        assert "hash" in ob
        assert "tick_size" in ob
    finally:
        await client.disconnect()


# =====================================================================
# Test 3: REST client — balance/allowance (L2 auth)
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_balance():
    """Test GET /balance-allowance with L2 auth (HMAC-SHA256)."""
    from data.rest_client import CLOBRestClient

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        result = await client.get_balance_allowance("COLLATERAL")

        assert "balance" in result, f"Unexpected response: {result}"
        assert "allowances" in result, f"Missing allowances: {result}"

        balance = Decimal(str(result["balance"]))
        assert balance >= 0, f"Negative balance: {balance}"

        # Validate we got allowance entries for known contracts
        allowances = result["allowances"]
        assert isinstance(allowances, dict)
    finally:
        await client.disconnect()


# =====================================================================
# Test 4: REST client — open orders (L2 auth)
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_open_orders():
    """Test fetching open orders (L2 auth)."""
    from data.rest_client import CLOBRestClient

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        orders = await client.get_open_orders()
        assert isinstance(orders, list)
        # New account should have 0 orders
    finally:
        await client.disconnect()


# =====================================================================
# Test 5: WebSocket — receive real orderbook
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_ws_receive_orderbook():
    """Test receiving a real orderbook snapshot via WebSocket."""
    from core.event_bus import EventBus
    from data.ws_client import CLOBWebSocketClient

    token_id = await _get_active_token_id()

    event_bus = EventBus()
    ws_client = CLOBWebSocketClient(
        event_bus=event_bus,
        token_ids=[token_id],
    )

    received_events: list = []

    async def _collect_events():
        async for event in event_bus.subscribe("book"):
            received_events.append(event)
            if len(received_events) >= 1:
                break

    await ws_client.start()

    try:
        # Wait for at least 1 book event (timeout 15s)
        collector_task = asyncio.create_task(_collect_events())
        try:
            await asyncio.wait_for(collector_task, timeout=15.0)
        except asyncio.TimeoutError:
            pass

        assert len(received_events) >= 1, "No orderbook events received within 15s"

        # Validate the event
        event = received_events[0]
        assert event.topic == "book"
        payload = event.payload
        assert "bids" in payload
        assert "asks" in payload
        assert "token_id" in payload
        assert payload["token_id"] == token_id

        # Check Decimal types
        if payload["bids"]:
            assert isinstance(payload["bids"][0]["price"], Decimal)
            assert isinstance(payload["bids"][0]["size"], Decimal)

    finally:
        await ws_client.stop()


# =====================================================================
# Test 6: REST client — tick size and neg risk
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_tick_size():
    """Test fetching tick size for a token."""
    from data.rest_client import CLOBRestClient

    token_id = await _get_active_token_id()

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        tick_size = await client.get_tick_size(token_id)
        assert tick_size in ("0.1", "0.01", "0.001", "0.0001"), f"Unexpected tick size: {tick_size}"

        neg_risk = await client.get_neg_risk(token_id)
        assert isinstance(neg_risk, bool)
    finally:
        await client.disconnect()


# =====================================================================
# Test 7: REST client — midpoint and spread
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_rest_client_midpoint():
    """Test fetching midpoint price."""
    from data.rest_client import CLOBRestClient

    token_id = await _get_active_token_id()

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        mid = await client.get_midpoint(token_id)
        assert isinstance(mid, Decimal)
        assert Decimal("0") <= mid <= Decimal("1"), f"Midpoint out of range: {mid}"
    finally:
        await client.disconnect()


# =====================================================================
# Test 8: End-to-end: Create and cancel a limit order
# =====================================================================

@skip_no_creds
@pytest.mark.asyncio
async def test_create_and_cancel_order():
    """Test creating and immediately cancelling a limit order.

    Places a tiny BUY order at $0.01 (far from market) to avoid fills.
    Requires USDC balance and allowances set.

    NOTE: This test is expected to fail if:
    - No USDC balance on the wallet
    - No allowances set (needs POL for gas)
    """
    from data.rest_client import CLOBRestClient

    token_id = await _get_active_token_id()

    client = CLOBRestClient(
        private_key=PRIVATE_KEY,
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=API_PASSPHRASE,
    )
    await client.connect()

    try:
        # Check balance first
        bal = await client.get_balance_allowance("COLLATERAL")
        balance = Decimal(str(bal.get("balance", "0")))

        if balance < Decimal("1"):
            pytest.skip(f"Insufficient USDC balance ({balance}) for order test")

        # Get tick size for the market
        tick_size = await client.get_tick_size(token_id)
        neg_risk = await client.get_neg_risk(token_id)

        # Create a limit buy at very low price (maker-only, won't fill)
        result = await client.create_and_post_order(
            token_id=token_id,
            price=0.01,  # Very low price
            size=5.0,    # Minimum size
            side="BUY",
            order_type="GTC",
            post_only=True,
            tick_size=tick_size,
            neg_risk=neg_risk,
        )

        assert result is not None
        order_id = None
        if isinstance(result, dict):
            order_id = result.get("orderID") or result.get("id")
            error = result.get("error") or result.get("errorMsg")
            if error:
                # Order might be rejected due to insufficient allowance
                pytest.skip(f"Order rejected (likely need allowances): {error}")

        # Cancel the order
        if order_id:
            cancelled = await client.cancel_order(order_id)
            assert cancelled, f"Failed to cancel order {order_id}"

    finally:
        # Cleanup: cancel all just in case
        await client.cancel_all_orders()
        await client.disconnect()
