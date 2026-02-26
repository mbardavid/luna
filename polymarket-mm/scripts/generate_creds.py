#!/usr/bin/env python3
"""Generate or derive Polymarket CLOB API credentials."""
from py_clob_client.client import ClobClient
import os
import sys

key = os.getenv("POLYMARKET_PRIVATE_KEY") or os.getenv("BASE_PRIVATE_KEY")
if not key:
    print("ERROR: No private key found in POLYMARKET_PRIVATE_KEY or BASE_PRIVATE_KEY", file=sys.stderr)
    sys.exit(1)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=key,
)

print("Deriving API credentials...", file=sys.stderr)
creds = client.create_or_derive_api_creds()
print(f"POLYMARKET_API_KEY={creds.api_key}")
print(f"POLYMARKET_SECRET={creds.api_secret}")
print(f"POLYMARKET_PASSPHRASE={creds.api_passphrase}")
print("Done!", file=sys.stderr)
