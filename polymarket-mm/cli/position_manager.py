"""Position Manager CLI — standalone position management tool.

Provides command-line access to position status, unwinding, merging,
and dust reporting outside the production runner.

Usage:
    python3 -m cli.position_manager status
    python3 -m cli.position_manager unwind --strategy aggressive
    python3 -m cli.position_manager merge --market <condition_id>
    python3 -m cli.position_manager dust
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog

# Add project root
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from data.rest_client import CLOBRestClient
from execution.ctf_merge import CTFMerger
from execution.unwind import UnwindConfig, UnwindManager, UnwindStrategy
from models.position import Position

logger = structlog.get_logger("cli.position_manager")

# Default dust threshold
DUST_THRESHOLD = Decimal("5")


def _make_rest_client() -> CLOBRestClient:
    """Create a REST client from environment variables."""
    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    api_secret = os.environ.get("POLYMARKET_API_SECRET", "") or os.environ.get("POLYMARKET_SECRET", "")
    api_passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    private_key = os.environ.get("POLYGON_PRIVATE_KEY", "") or os.environ.get("POLYMARKET_PRIVATE_KEY", "")

    if not all([api_key, api_secret, api_passphrase, private_key]):
        print("ERROR: Missing required environment variables:")
        print("  POLYMARKET_API_KEY, POLYMARKET_API_SECRET,")
        print("  POLYMARKET_PASSPHRASE, POLYGON_PRIVATE_KEY")
        sys.exit(1)

    return CLOBRestClient(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        rate_limit_rps=5.0,
    )


async def cmd_status(args: argparse.Namespace) -> None:
    """Show current on-chain positions."""
    client = _make_rest_client()
    await client.connect()

    try:
        # Get USDC balance
        balance_info = await client.get_balance_allowance("COLLATERAL")
        raw_balance = Decimal(str(balance_info.get("balance", "0")))
        usdc_balance = raw_balance / Decimal("1000000")

        print(f"\n{'='*60}")
        print("  Polymarket Position Status")
        print(f"{'='*60}")
        print(f"  USDC Balance: ${usdc_balance:.4f}")

        # Get open orders
        open_orders = await client.get_open_orders()
        print(f"  Open Orders:  {len(open_orders)}")

        if open_orders:
            print(f"\n  Open Orders:")
            for o in open_orders[:10]:
                side = o.get("side", "?")
                price = o.get("price", "?")
                size = o.get("original_size", o.get("size", "?"))
                print(f"    {side} {size} @ {price}")

        print(f"{'='*60}\n")

    finally:
        await client.disconnect()


async def cmd_unwind(args: argparse.Namespace) -> None:
    """Close all positions using the specified strategy."""
    strategy = UnwindStrategy(args.strategy)
    client = _make_rest_client()
    await client.connect()

    try:
        config = UnwindConfig(
            strategy=strategy,
            max_time_seconds=args.timeout,
            dust_threshold_shares=Decimal(str(args.dust_threshold)),
        )

        merger = CTFMerger(ctf_adapter=None)  # No on-chain without adapter
        manager = UnwindManager(
            rest_client=client,
            ctf_merger=merger,
            config=config,
        )

        # We need positions — for CLI, we'd normally query on-chain
        # For now, print a message that this requires position data
        print(f"\nUnwinding positions with strategy: {strategy.value}")
        print(f"Max time: {args.timeout}s")
        print(f"Dust threshold: {args.dust_threshold} shares")

        # Cancel all open orders first
        print("Cancelling all open orders...")
        await client.cancel_all_orders()
        print("Done.")

        if strategy == UnwindStrategy.HOLD:
            print("Strategy is HOLD — positions will be kept.")
            return

        print("\nNote: Full position-aware unwind requires the production runner.")
        print("Use `--unwind-previous` flag when starting the runner to unwind")
        print("positions from a previous run.")

    finally:
        await client.disconnect()


async def cmd_merge(args: argparse.Namespace) -> None:
    """Merge YES+NO pairs for a specific market."""
    condition_id = args.market
    print(f"\nMerge YES+NO pairs for market: {condition_id}")
    print("Note: On-chain merge requires CTF adapter configuration.")
    print("This command is a placeholder — use the production runner for")
    print("automatic merge during unwind.")


async def cmd_dust(args: argparse.Namespace) -> None:
    """Report dust positions (below minimum order size)."""
    threshold = Decimal(str(args.threshold))
    print(f"\n{'='*60}")
    print(f"  Dust Position Report (threshold: {threshold} shares)")
    print(f"{'='*60}")
    print("  Note: Full dust report requires on-chain token balance queries.")
    print("  Use the production runner's live state for accurate position data.")
    print(f"{'='*60}\n")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        description="Polymarket Position Manager — manual position management CLI",
        prog="position_manager",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # status
    sub_status = subparsers.add_parser("status", help="Show current positions")

    # unwind
    sub_unwind = subparsers.add_parser("unwind", help="Close all positions")
    sub_unwind.add_argument(
        "--strategy",
        choices=["aggressive", "sweep", "hold"],
        default="aggressive",
        help="Unwind strategy (default: aggressive)",
    )
    sub_unwind.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Maximum time for unwind in seconds (default: 60)",
    )
    sub_unwind.add_argument(
        "--dust-threshold",
        type=float,
        default=5.0,
        help="Minimum shares to attempt selling (default: 5)",
    )

    # merge
    sub_merge = subparsers.add_parser("merge", help="Merge YES+NO pairs")
    sub_merge.add_argument(
        "--market",
        required=True,
        help="Condition ID of the market to merge",
    )

    # dust
    sub_dust = subparsers.add_parser("dust", help="Report dust positions")
    sub_dust.add_argument(
        "--threshold",
        type=float,
        default=5.0,
        help="Dust threshold in shares (default: 5)",
    )

    return parser


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cmd_map = {
        "status": cmd_status,
        "unwind": cmd_unwind,
        "merge": cmd_merge,
        "dust": cmd_dust,
    }

    handler = cmd_map.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    asyncio.run(handler(args))


if __name__ == "__main__":
    main()
