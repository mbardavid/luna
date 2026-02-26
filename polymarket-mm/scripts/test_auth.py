#!/usr/bin/env python3
"""Test Polymarket CLOB API authentication by fetching balances."""
from py_clob_client.client import ClobClient
import os, sys, json

key = os.getenv("POLYMARKET_PRIVATE_KEY") or os.getenv("BASE_PRIVATE_KEY")
api_key = os.getenv("POLYMARKET_API_KEY") or "bc9ad5a1-c5ef-6466-13ee-d31a46003a8d"
api_secret = os.getenv("POLYMARKET_SECRET") or "CSrhiGtKwcEJ3Te_nwrkECyvNJm8gEizo1nwmra_-z0="
api_passphrase = os.getenv("POLYMARKET_PASSPHRASE") or "1edc70d3000ded068f25d52599f8825676ecfde47b67bf46311423ede71673a4"

from py_clob_client.clob_types import ApiCreds

creds = ApiCreds(
    api_key=api_key,
    api_secret=api_secret,
    api_passphrase=api_passphrase,
)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=key,
    creds=creds,
)

# Test 1: Get balances (requires L2 auth)
print("=== Testing GET /balances (L2 auth) ===")
try:
    # The client may not have a direct get_balances method, try raw
    import requests
    from py_clob_client.headers.headers import create_level_2_headers
    
    headers = create_level_2_headers(client.signer, creds)
    resp = requests.get("https://clob.polymarket.com/balances", headers=headers)
    print(f"Status: {resp.status_code}")
    print(f"Response: {json.dumps(resp.json(), indent=2)[:500]}")
except Exception as e:
    print(f"Error: {e}")

# Test 2: Server time (no auth needed, just connectivity)
print("\n=== Testing GET /time ===")
try:
    import requests
    resp = requests.get("https://clob.polymarket.com/time")
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.text[:200]}")
except Exception as e:
    print(f"Error: {e}")
