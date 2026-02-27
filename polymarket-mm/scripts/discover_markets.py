#!/usr/bin/env python3
"""Polymarket Market Discovery — find high-liquidity markets for MM.

Fetches active markets from the Gamma API, applies selection criteria,
and outputs a ranked table suitable for market-making configuration.

Selection criteria:
  - Volume 24h > $50k
  - Spread < 5%  (based on bestBid/bestAsk if available)
  - Time until resolution > 7 days
  - Mid-range price (0.10–0.90) preferred for MM profitability
  - Accepting orders
  - Not negRisk group items with extreme prices (< 0.05 or > 0.95)

Usage:
    python scripts/discover_markets.py [--top N] [--yaml] [--min-volume MIN]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    import requests
except ImportError:
    # Fallback for environments without requests
    import urllib.request
    import urllib.error

    class _FakeResponse:
        def __init__(self, data: bytes, status: int):
            self._data = data
            self.status_code = status

        def json(self):
            return json.loads(self._data)

        def raise_for_status(self):
            if self.status_code >= 400:
                raise Exception(f"HTTP {self.status_code}")

    class requests:  # type: ignore
        @staticmethod
        def get(url: str, params: dict | None = None, timeout: int = 30) -> _FakeResponse:
            if params:
                qs = "&".join(f"{k}={v}" for k, v in params.items())
                url = f"{url}?{qs}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return _FakeResponse(resp.read(), resp.status)


GAMMA_API = "https://gamma-api.polymarket.com"
NOW = datetime.now(timezone.utc)


def fetch_markets(limit: int = 100, offset: int = 0) -> list[dict]:
    """Fetch active open markets ordered by 24h volume."""
    resp = requests.get(
        f"{GAMMA_API}/markets",
        params={
            "closed": "false",
            "active": "true",
            "order": "volume24hr",
            "ascending": "false",
            "limit": str(limit),
            "offset": str(offset),
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def parse_outcome_prices(s: str) -> list[float]:
    """Parse JSON-encoded outcome prices string."""
    try:
        return [float(x) for x in json.loads(s)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def parse_token_ids(s: str) -> list[str]:
    """Parse JSON-encoded CLOB token IDs string."""
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return []


def days_until_end(end_date_str: str | None) -> float:
    """Calculate days until market end date."""
    if not end_date_str:
        return 365.0  # No end date → treat as very long
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        delta = end - NOW
        return delta.total_seconds() / 86400
    except (ValueError, TypeError):
        return 365.0


def compute_spread_pct(market: dict) -> float | None:
    """Compute spread as percentage from bestBid/bestAsk or outcomePrices."""
    best_bid = market.get("bestBid")
    best_ask = market.get("bestAsk")

    if best_bid is not None and best_ask is not None:
        bid = float(best_bid)
        ask = float(best_ask)
        if bid > 0 and ask > 0 and ask > bid:
            mid = (bid + ask) / 2
            if mid > 0:
                return (ask - bid) / mid * 100
    # If spread field is provided
    spread_val = market.get("spread")
    if spread_val is not None:
        # spread is absolute, compute relative to mid
        prices = parse_outcome_prices(market.get("outcomePrices", "[]"))
        if prices:
            mid = prices[0]  # YES price as proxy
            if mid > 0.01:
                return float(spread_val) / mid * 100
    return None


def evaluate_market(m: dict, min_volume: float = 50_000) -> dict[str, Any] | None:
    """Evaluate a single market against selection criteria.

    Returns enriched dict if market passes, None otherwise.
    """
    # Must be accepting orders
    if not m.get("acceptingOrders", False):
        return None

    # Volume 24h check
    vol24 = m.get("volume24hr", 0) or 0
    if float(vol24) < min_volume:
        return None

    # Parse prices
    prices = parse_outcome_prices(m.get("outcomePrices", "[]"))
    if not prices or len(prices) < 2:
        return None
    yes_price = prices[0]

    # Skip extreme prices (not good for MM — very one-sided)
    if yes_price < 0.05 or yes_price > 0.95:
        return None

    # Time until resolution
    end_date = m.get("endDate")
    days_left = days_until_end(end_date)
    if days_left < 7:
        return None

    # Spread check
    spread_pct = compute_spread_pct(m)

    # Token IDs
    token_ids = parse_token_ids(m.get("clobTokenIds", "[]"))
    if len(token_ids) < 2:
        return None

    # Liquidity
    liquidity = float(m.get("liquidityNum", 0) or m.get("liquidity", 0) or 0)

    return {
        "id": m["id"],
        "question": m.get("question", ""),
        "slug": m.get("slug", ""),
        "condition_id": m.get("conditionId", ""),
        "token_id_yes": token_ids[0],
        "token_id_no": token_ids[1],
        "tick_size": m.get("orderPriceMinTickSize", 0.01),
        "min_order_size": m.get("orderMinSize", 5),
        "neg_risk": m.get("negRisk", False),
        "neg_risk_market_id": m.get("negRiskMarketID", ""),
        "yes_price": yes_price,
        "no_price": prices[1] if len(prices) > 1 else 1 - yes_price,
        "volume_24h": float(vol24),
        "volume_total": float(m.get("volumeNum", 0) or 0),
        "liquidity": liquidity,
        "spread_pct": spread_pct,
        "spread_abs": float(m.get("spread", 0) or 0),
        "best_bid": float(m.get("bestBid", 0) or 0),
        "best_ask": float(m.get("bestAsk", 0) or 0),
        "end_date": end_date,
        "days_left": days_left,
        "rewards_min_size": m.get("rewardsMinSize"),
        "rewards_max_spread": m.get("rewardsMaxSpread"),
        "has_rewards": bool(m.get("clobRewards")),
        "market_type": _classify_market(m),
        "event_title": (m.get("events", [{}])[0].get("title", "") if m.get("events") else ""),
        "competitive": float(m.get("competitive", 0) or 0),
    }


def _classify_market(m: dict) -> str:
    """Heuristic market type classification."""
    q = (m.get("question", "") + " " + m.get("slug", "")).lower()
    events = m.get("events", [])
    event_title = events[0].get("title", "").lower() if events else ""
    combined = q + " " + event_title

    if any(kw in combined for kw in ["btc", "bitcoin", "eth", "ethereum", "crypto", "solana", "sol "]):
        return "CRYPTO_5M"
    if any(kw in combined for kw in ["nba", "nfl", "mlb", "nhl", "fifa", "world cup", "premier league", "champion"]):
        return "SPORTS"
    if any(kw in combined for kw in ["trump", "biden", "election", "president", "congress", "senate", "governor", "fed chair"]):
        return "POLITICS"
    return "OTHER"


def discover(min_volume: float = 50_000, top_n: int = 20) -> list[dict]:
    """Run full discovery pipeline."""
    candidates = []

    # Fetch up to 200 markets (paginated)
    for offset in range(0, 200, 100):
        markets = fetch_markets(limit=100, offset=offset)
        if not markets:
            break
        for m in markets:
            evaluated = evaluate_market(m, min_volume=min_volume)
            if evaluated:
                candidates.append(evaluated)

    # Sort by: volume_24h (primary), liquidity (secondary)
    candidates.sort(key=lambda c: (-c["volume_24h"], -c["liquidity"]))

    return candidates[:top_n]


def format_table(candidates: list[dict]) -> str:
    """Format candidates as a readable table."""
    lines = []
    lines.append(f"{'#':>3} {'Question':<60} {'Yes$':>6} {'Vol24h':>12} {'Liq':>12} {'Spread%':>8} {'Days':>6} {'Type':<10} {'Rewards':>7}")
    lines.append("-" * 140)

    for i, c in enumerate(candidates, 1):
        spread_str = f"{c['spread_pct']:.1f}%" if c['spread_pct'] is not None else "N/A"
        rewards_str = "YES" if c['has_rewards'] else "no"
        lines.append(
            f"{i:>3} {c['question'][:60]:<60} {c['yes_price']:>6.3f} "
            f"${c['volume_24h']:>10,.0f} ${c['liquidity']:>10,.0f} "
            f"{spread_str:>8} {c['days_left']:>6.0f} {c['market_type']:<10} {rewards_str:>7}"
        )

    return "\n".join(lines)


def to_yaml_entry(c: dict, enabled: bool = True) -> str:
    """Generate a YAML market entry."""
    # Determine params based on market type
    type_params = {
        "CRYPTO_5M": {"spread_min_bps": 30, "max_pos": "500", "gamma": None, "rewards_agg": "0.5"},
        "CRYPTO_15M": {"spread_min_bps": 40, "max_pos": "300", "gamma": "0.5", "rewards_agg": "0.3"},
        "SPORTS": {"spread_min_bps": 60, "max_pos": "200", "gamma": None, "rewards_agg": "0.3"},
        "POLITICS": {"spread_min_bps": 80, "max_pos": "200", "gamma": "0.8", "rewards_agg": "0.2"},
        "OTHER": {"spread_min_bps": 50, "max_pos": "300", "gamma": None, "rewards_agg": "0.4"},
    }
    p = type_params.get(c["market_type"], type_params["OTHER"])

    gamma_str = f'"{p["gamma"]}"' if p["gamma"] else "null"
    neg_risk_id_line = ""
    if c["neg_risk"] and c.get("neg_risk_market_id"):
        neg_risk_id_line = f'\n      neg_risk_market_id: "{c["neg_risk_market_id"]}"'

    return f"""  - market_id: "{c['slug']}"
    condition_id: "{c['condition_id']}"
    token_id_yes: "{c['token_id_yes']}"
    token_id_no: "{c['token_id_no']}"
    market_type: "{c['market_type']}"
    description: "{c['question']}"
    enabled: {str(enabled).lower()}
    params:
      tick_size: "{c['tick_size']}"
      min_order_size: "{c['min_order_size']}"
      neg_risk: {str(c['neg_risk']).lower()}{neg_risk_id_line}
      spread_min_bps: {p['spread_min_bps']}
      max_position_size: "{p['max_pos']}"
      gamma_override: {gamma_str}
      rewards_aggressiveness: "{p['rewards_agg']}"
      param_group: "A"
"""


def generate_full_yaml(candidates: list[dict]) -> str:
    """Generate complete markets.yaml content."""
    lines = [
        "# Polymarket MM — Market Allowlist Configuration",
        "# Auto-generated by discover_markets.py",
        f"# Generated at: {NOW.isoformat()}",
        "#",
        "# Selection criteria:",
        "#   - Volume 24h > $50k",
        "#   - Spread < 5%",
        "#   - Time until resolution > 7 days",
        "#   - Mid-range price (0.10–0.90)",
        "#   - Accepting orders",
        "",
        "markets:",
    ]

    # Group by type
    by_type: dict[str, list] = {}
    for c in candidates:
        by_type.setdefault(c["market_type"], []).append(c)

    type_order = ["CRYPTO_5M", "CRYPTO_15M", "POLITICS", "SPORTS", "OTHER"]
    type_labels = {
        "CRYPTO_5M": "Crypto markets (short horizon)",
        "CRYPTO_15M": "Crypto markets (medium horizon)",
        "POLITICS": "Politics markets",
        "SPORTS": "Sports markets",
        "OTHER": "Other markets",
    }

    for mtype in type_order:
        markets_of_type = by_type.get(mtype, [])
        if not markets_of_type:
            continue
        lines.append(f"  # ── {type_labels.get(mtype, mtype)} {'─' * (50 - len(type_labels.get(mtype, mtype)))}")
        for c in markets_of_type:
            lines.append(to_yaml_entry(c))

    lines.append("")
    lines.append("# ── Global defaults (used when param_group overrides are null) ──")
    lines.append("defaults:")
    lines.append("  spread_min_bps: 50")
    lines.append('  max_position_size: "500"')
    lines.append('  gamma: "0.3"')
    lines.append('  rewards_aggressiveness: "0.5"')
    lines.append("  data_gap_tolerance_seconds: 8")
    lines.append("  reconciliation_interval_seconds: 60")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Discover Polymarket markets for MM")
    parser.add_argument("--top", type=int, default=20, help="Number of top markets to show")
    parser.add_argument("--min-volume", type=float, default=50_000, help="Minimum 24h volume in USD")
    parser.add_argument("--yaml", action="store_true", help="Output YAML config for selected markets")
    parser.add_argument("--select", type=str, default=None,
                        help="Comma-separated indices (1-based) of markets to include in YAML")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    print(f"🔍 Discovering markets from Gamma API...")
    print(f"   Criteria: vol24h > ${args.min_volume:,.0f}, spread < 5%, days_left > 7, price 0.10–0.90")
    print()

    candidates = discover(min_volume=args.min_volume, top_n=args.top)

    if not candidates:
        print("❌ No markets found matching criteria.")
        sys.exit(1)

    print(f"✅ Found {len(candidates)} qualifying markets:\n")
    print(format_table(candidates))
    print()

    if args.json:
        print(json.dumps(candidates, indent=2, default=str))

    if args.yaml:
        # Select specific markets or all
        if args.select:
            indices = [int(x.strip()) - 1 for x in args.select.split(",")]
            selected = [candidates[i] for i in indices if 0 <= i < len(candidates)]
        else:
            selected = candidates[:5]  # Default: top 5

        print(f"\n📝 Generating YAML for {len(selected)} markets:\n")
        yaml_content = generate_full_yaml(selected)
        print(yaml_content)


if __name__ == "__main__":
    main()
