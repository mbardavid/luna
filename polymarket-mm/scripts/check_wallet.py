#!/usr/bin/env python3
"""Check wallet status: POL balance, USDC balance, allowances, API connectivity.

Usage:
    python scripts/check_wallet.py
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

address = client.get_address()
print(f"═══ Polymarket MM Wallet Status ═══")
print(f"Address: {address}")
print(f"Chain:   Polygon (137)")

# 1. API connectivity
print(f"\n─── API Connectivity ───")
try:
    ok = client.get_ok()
    server_time = client.get_server_time()
    print(f"  API Status: {ok}")
    print(f"  Server Time: {server_time}")
except Exception as e:
    print(f"  ❌ API Error: {e}")

# 2. On-chain balances
print(f"\n─── On-Chain Balances ───")
try:
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider("https://polygon-bor-rpc.publicnode.com"))

    # POL
    pol_balance = w3.eth.get_balance(Web3.to_checksum_address(address))
    pol_ether = float(Web3.from_wei(pol_balance, "ether"))
    status = "✅" if pol_ether > 0.01 else "❌"
    print(f"  {status} POL: {pol_ether:.6f}")

    # USDC.e (bridged, what Polymarket uses)
    usdc_addr = Web3.to_checksum_address("0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174")
    erc20_abi = [{"constant": True, "inputs": [{"name": "_owner", "type": "address"}],
                  "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}]
    usdc = w3.eth.contract(address=usdc_addr, abi=erc20_abi)
    usdc_bal = usdc.functions.balanceOf(Web3.to_checksum_address(address)).call()
    usdc_fmt = usdc_bal / 10**6
    print(f"  {'✅' if usdc_fmt > 0 else '⚠️ '} USDC.e: ${usdc_fmt:.2f}")

except Exception as e:
    print(f"  ⚠️  RPC Error: {e}")

# 3. CLOB balance & allowances
print(f"\n─── CLOB Balance & Allowances ───")
try:
    result = client.get_balance_allowance(BalanceAllowanceParams(asset_type="COLLATERAL"))
    clob_balance = float(result.get("balance", "0")) / 10**6
    print(f"  CLOB Balance: ${clob_balance:.2f}")

    allowances = result.get("allowances", {})
    for contract, allowance in allowances.items():
        status = "✅" if int(allowance) > 0 else "❌"
        print(f"  {status} Allowance {contract[:10]}...: {allowance}")
except Exception as e:
    print(f"  ❌ Error: {e}")

# 4. Open orders
print(f"\n─── Open Orders ───")
try:
    from py_clob_client.clob_types import OpenOrderParams
    orders = client.get_orders(OpenOrderParams())
    order_list = orders if isinstance(orders, list) else orders.get("data", [])
    print(f"  Open orders: {len(order_list)}")
except Exception as e:
    print(f"  ❌ Error: {e}")

# Summary
print(f"\n═══ Summary ═══")
issues = []
if pol_ether <= 0.01:
    issues.append("Need POL for gas (send >= 0.1 POL to wallet)")
if usdc_fmt <= 0:
    issues.append("Need USDC.e balance for trading")
if not any(int(v) > 0 for v in allowances.values()):
    issues.append("Need to set allowances (run scripts/set_allowances.py)")

if issues:
    print("  ⚠️  Issues found:")
    for issue in issues:
        print(f"    • {issue}")
else:
    print("  ✅ Ready to trade!")
