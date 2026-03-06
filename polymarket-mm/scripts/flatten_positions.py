#!/usr/bin/env python3
"""Official Polymarket inventory flatten utility."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path("/home/openclaw/.openclaw/workspace/polymarket-mm")
sys.path.insert(0, str(PROJECT_ROOT))

from execution.flatten_inventory import DEFAULT_WALLET_ADDRESS, flatten_positions

DEFAULT_REPORT = PROJECT_ROOT / "paper" / "data" / "flatten_report.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Flatten recoverable Polymarket CTF inventory")
    parser.add_argument("--execute", action="store_true", help="Perform merges/sells instead of dry-run")
    parser.add_argument("--wallet", type=str, default=DEFAULT_WALLET_ADDRESS, help="Wallet address to inspect")
    parser.add_argument("--report", type=str, default=str(DEFAULT_REPORT), help="Output JSON report path")
    parser.add_argument("--dust-threshold-shares", type=Decimal, default=Decimal("5"), help="Dust threshold in shares")
    parser.add_argument("--dust-threshold-usdc", type=Decimal, default=Decimal("1"), help="Dust threshold in USDC")
    parser.add_argument("--json", action="store_true", help="Print the final report JSON")
    args = parser.parse_args()

    report = asyncio.run(
        flatten_positions(
            execute=args.execute,
            report_path=Path(args.report),
            wallet_address=args.wallet,
            dust_threshold_shares=args.dust_threshold_shares,
            dust_threshold_usdc=args.dust_threshold_usdc,
        )
    )
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"flatten_report={args.report}")
        print(f"executed={str(args.execute).lower()}")
        print(f"free_collateral_usdc={report['free_collateral_usdc']}")
        print(f"recoverable_positions={len(report['wallet_state_after']['recoverable_positions'])}")
        print(f"dust_positions={len(report['wallet_state_after']['dust_positions'])}")


if __name__ == "__main__":
    main()
