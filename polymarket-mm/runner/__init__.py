"""runner — Unified trading pipeline for paper and live modes.

Merges paper_runner and production_runner into a single package with
``--mode paper|live`` flag.  The old runners remain as deprecated shims.

Usage::

    python -m runner --mode paper --config paper/runs/p5-001.yaml
    python -m runner --mode live  --config paper/runs/prod-001.yaml
"""

from runner.config import UnifiedMarketConfig, load_markets, auto_select_markets
from runner.venue_adapter import VenueAdapter
from runner.wallet_adapter import WalletAdapter

__all__ = [
    "UnifiedMarketConfig",
    "VenueAdapter",
    "WalletAdapter",
    "load_markets",
    "auto_select_markets",
]
