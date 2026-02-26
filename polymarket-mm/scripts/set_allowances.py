#!/usr/bin/env python3
"""Set USDC allowances for Polymarket CTF Exchange contracts.

Requires MATIC/POL for gas on Polygon mainnet.
Run this AFTER funding the wallet with some MATIC for gas.

Usage:
    python scripts/set_allowances.py

Environment variables:
    POLYMARKET_PRIVATE_KEY  - Wallet private key
    POLYMARKET_API_KEY      - CLOB API key
    POLYMARKET_SECRET       - CLOB API secret
    POLYMARKET_PASSPHRASE   - CLOB API passphrase
"""
import json
import os
import sys

# Load creds from systemd if env vars not set
def _load_systemd_creds():
    conf_path = os.path.expanduser(
        "~/.config/systemd/user/openclaw-gateway.service.d/polymarket-env.conf"
    )
    if not os.path.exists(conf_path):
        return
    with open(conf_path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("Environment="):
                eq_part = line.split("=", 1)[1].strip().strip('"')
                key, _, value = eq_part.partition("=")
                value = value.strip('"')
                if key and value and not os.environ.get(key):
                    os.environ[key] = value

_load_systemd_creds()

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams

key = os.getenv("POLYMARKET_PRIVATE_KEY")
if not key:
    print("ERROR: POLYMARKET_PRIVATE_KEY not set")
    sys.exit(1)

creds = ApiCreds(
    api_key=os.getenv("POLYMARKET_API_KEY", ""),
    api_secret=os.getenv("POLYMARKET_SECRET", ""),
    api_passphrase=os.getenv("POLYMARKET_PASSPHRASE", ""),
)

client = ClobClient(
    host="https://clob.polymarket.com",
    chain_id=137,
    key=key,
    creds=creds,
)

print(f"Wallet address: {client.get_address()}")

# Check POL balance first
try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))
    address = client.get_address()
    pol_balance = w3.eth.get_balance(Web3.to_checksum_address(address))
    pol_ether = Web3.from_wei(pol_balance, "ether")
    print(f"POL balance: {pol_ether} POL")
    if pol_balance == 0:
        print("\nWARNING: 0 POL for gas! Allowance transactions will fail.")
        print("Fund this address with at least 0.1 POL first.")
        print(f"  Address: {address}")
        sys.exit(1)
except Exception as e:
    print(f"Could not check POL balance: {e}")

# Check current allowances
print("\n=== Current Allowances ===")
try:
    result = client.get_balance_allowance(BalanceAllowanceParams(asset_type="COLLATERAL"))
    print(json.dumps(result, indent=2))

    balance = result.get("balance", "0")
    allowances = result.get("allowances", {})

    has_allowance = any(int(v) > 0 for v in allowances.values())

    if has_allowance:
        print("\n✅ Allowances already set!")
    else:
        print("\n⏳ Setting COLLATERAL (USDC) allowance for CTF Exchange contracts...")
        try:
            set_result = client.update_balance_allowance(
                BalanceAllowanceParams(asset_type="COLLATERAL")
            )
            print(f"Result: {set_result}")
        except Exception as e:
            print(f"Error setting allowance: {e}")
            print("  Ensure you have POL for gas on Polygon mainnet.")

except Exception as e:
    print(f"Error: {e}")

# Verify
print("\n=== Verification ===")
try:
    result = client.get_balance_allowance(BalanceAllowanceParams(asset_type="COLLATERAL"))
    print(json.dumps(result, indent=2))
except Exception as e:
    print(f"Error: {e}")
