#!/usr/bin/env python3
"""Validate markets.yaml — load config, build MarketStates, run QuoteEngine.

Verifies that:
  1. markets.yaml parses correctly
  2. All required fields are present and valid
  3. QuoteEngine generates sensible quotes for each market
  4. Prices are within [price_floor, price_ceiling]
  5. Spread and skew produce reasonable bid/ask levels

Usage:
    python scripts/validate_markets.py [--config CONFIG_PATH]
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import yaml

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from models.feature_vector import FeatureVector
from models.market_state import MarketState, MarketType
from models.position import Position
from models.quote_plan import QuoteSide, TokenSide
from strategy.quote_engine import QuoteEngine, QuoteEngineConfig


def load_markets_yaml(config_path: str = "config/markets.yaml") -> dict:
    """Load and parse markets.yaml."""
    path = PROJECT_ROOT / config_path
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f)


def validate_market_entry(entry: dict) -> list[str]:
    """Validate a single market entry. Returns list of errors."""
    errors = []
    required_fields = [
        "market_id", "condition_id", "token_id_yes", "token_id_no",
        "market_type", "description",
    ]
    for field in required_fields:
        if not entry.get(field):
            errors.append(f"Missing required field: {field}")

    params = entry.get("params", {})
    required_params = ["tick_size", "min_order_size"]
    for field in required_params:
        if not params.get(field):
            errors.append(f"Missing required param: {field}")

    # Validate condition_id format (should be hex)
    cid = entry.get("condition_id", "")
    if cid and not cid.startswith("0x"):
        errors.append(f"condition_id should start with 0x: {cid}")

    # Validate token_ids are numeric strings
    for tid_name in ["token_id_yes", "token_id_no"]:
        tid = entry.get(tid_name, "")
        if tid:
            try:
                int(tid)
            except ValueError:
                errors.append(f"{tid_name} should be a numeric string: {tid[:30]}...")

    # Validate tick_size is valid decimal
    try:
        ts = Decimal(str(params.get("tick_size", "0")))
        if ts <= 0:
            errors.append(f"tick_size must be > 0, got {ts}")
    except Exception as e:
        errors.append(f"Invalid tick_size: {e}")

    # Validate market_type
    valid_types = {"CRYPTO_5M", "CRYPTO_15M", "SPORTS", "POLITICS", "OTHER"}
    mt = entry.get("market_type", "")
    if mt not in valid_types:
        errors.append(f"Invalid market_type: {mt}")

    return errors


def build_market_state(entry: dict, simulated_prices: dict | None = None) -> MarketState:
    """Build a MarketState from a market entry with simulated book data."""
    params = entry.get("params", {})
    
    # Use provided prices or defaults based on market type
    if simulated_prices:
        yes_bid = Decimal(str(simulated_prices.get("yes_bid", "0.40")))
        yes_ask = Decimal(str(simulated_prices.get("yes_ask", "0.42")))
    else:
        # Default simulation: mid around 0.50 with 2% spread
        yes_bid = Decimal("0.49")
        yes_ask = Decimal("0.51")

    no_bid = Decimal("1") - yes_ask
    no_ask = Decimal("1") - yes_bid

    return MarketState(
        market_id=entry["market_id"],
        condition_id=entry["condition_id"],
        token_id_yes=entry["token_id_yes"],
        token_id_no=entry["token_id_no"],
        tick_size=Decimal(str(params.get("tick_size", "0.01"))),
        min_order_size=Decimal(str(params.get("min_order_size", "5"))),
        neg_risk=params.get("neg_risk", False),
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
        no_ask=no_ask,
        depth_yes_bid=Decimal("1000"),
        depth_yes_ask=Decimal("1000"),
        depth_no_bid=Decimal("1000"),
        depth_no_ask=Decimal("1000"),
        volume_1m=Decimal("500"),
        volume_5m=Decimal("2500"),
        market_type=MarketType(entry.get("market_type", "OTHER")),
    )


def build_features(market_id: str) -> FeatureVector:
    """Build a reasonable FeatureVector for testing."""
    return FeatureVector(
        market_id=market_id,
        spread_bps=Decimal("50"),
        book_imbalance=0.0,
        micro_momentum=0.0,
        volatility_1m=0.02,
        liquidity_score=0.7,
        toxic_flow_score=0.5,
        oracle_delta=0.0,
        expected_fee_bps=Decimal("0"),
        queue_position_estimate=1.0,
        data_quality_score=0.9,
    )


# Simulated live prices based on Gamma API snapshot (2026-02-26)
SIMULATED_PRICES = {
    "will-axiom-be-accused-of-insider-trading": {
        "yes_bid": "0.400", "yes_ask": "0.402",
    },
    "us-strikes-iran-by-march-31-2026": {
        "yes_bid": "0.530", "yes_ask": "0.540",
    },
    "will-the-colorado-avalanche-win-the-2026-nhl-stanley-cup": {
        "yes_bid": "0.235", "yes_ask": "0.238",
    },
    "will-barcelona-win-the-202526-la-liga": {
        "yes_bid": "0.590", "yes_ask": "0.600",
    },
    "will-jd-vance-win-the-2028-us-presidential-election": {
        "yes_bid": "0.225", "yes_ask": "0.226",
    },
}


def validate_quote_plan(entry: dict, engine: QuoteEngine) -> tuple[bool, str]:
    """Run QuoteEngine on a market and validate the plan is sensible."""
    market_id = entry["market_id"]
    prices = SIMULATED_PRICES.get(market_id)
    state = build_market_state(entry, prices)
    features = build_features(market_id)
    position = Position(
        market_id=market_id,
        token_id_yes=entry["token_id_yes"],
        token_id_no=entry["token_id_no"],
    )

    plan = engine.generate_quotes(state=state, features=features, position=position)

    lines = []
    lines.append(f"  Mid price: {state.mid_price}")
    lines.append(f"  YES spread: {state.spread_yes}")
    lines.append(f"  Slices generated: {len(plan.slices)}")

    if not plan.slices:
        return False, "\n".join(lines) + "\n  ❌ No slices generated!"

    # Validate each slice
    for s in plan.slices:
        label = f"    {s.side.value} {s.token.value}"
        price = s.price
        size = s.size

        # Price bounds
        if price < Decimal("0.01") or price > Decimal("0.99"):
            return False, "\n".join(lines) + f"\n  ❌ {label} price {price} out of bounds!"

        # Size must be positive
        if size <= 0:
            return False, "\n".join(lines) + f"\n  ❌ {label} size {size} <= 0!"

        # Check tick alignment
        tick = state.tick_size
        remainder = price % tick
        if remainder != 0:
            lines.append(f"    ⚠️  {label} price {price} not tick-aligned (tick={tick})")

        lines.append(f"    {label}: price={price} size={size} ttl={s.ttl_ms}ms")

    # Check bilateral structure: should have YES bids, YES asks, NO bids, NO asks
    yes_bids = [s for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.BID]
    yes_asks = [s for s in plan.slices if s.token == TokenSide.YES and s.side == QuoteSide.ASK]
    no_bids = [s for s in plan.slices if s.token == TokenSide.NO and s.side == QuoteSide.BID]
    no_asks = [s for s in plan.slices if s.token == TokenSide.NO and s.side == QuoteSide.ASK]

    lines.append(f"  Structure: {len(yes_bids)} YES bids, {len(yes_asks)} YES asks, "
                 f"{len(no_bids)} NO bids, {len(no_asks)} NO asks")

    if not (yes_bids and yes_asks and no_bids and no_asks):
        lines.append("  ⚠️  Missing some sides (may be OK if price is near boundary)")

    # Check bid < ask for YES
    if yes_bids and yes_asks:
        if yes_bids[0].price >= yes_asks[0].price:
            return False, "\n".join(lines) + "\n  ❌ YES bid >= YES ask (crossed book)!"

    # Check bid < ask for NO
    if no_bids and no_asks:
        if no_bids[0].price >= no_asks[0].price:
            return False, "\n".join(lines) + "\n  ❌ NO bid >= NO ask (crossed book)!"

    # Check complement relationship: YES_ask + NO_bid ≈ 1.0
    if yes_asks and no_bids:
        complement_sum = yes_asks[0].price + no_bids[0].price
        if abs(complement_sum - Decimal("1")) > Decimal("0.1"):
            lines.append(f"  ⚠️  Complement check: YES_ask({yes_asks[0].price}) + NO_bid({no_bids[0].price}) = {complement_sum} (expected ≈1.0)")

    # Convert to order intents
    orders = plan.to_order_intents()
    lines.append(f"  Order intents: {len(orders)}")

    return True, "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate markets.yaml")
    parser.add_argument("--config", default="config/markets.yaml", help="Path to markets.yaml")
    args = parser.parse_args()

    print("=" * 70)
    print("🔍 Polymarket MM — Markets Configuration Validator")
    print("=" * 70)
    print()

    # Step 1: Load YAML
    print("📂 Loading config...")
    try:
        config = load_markets_yaml(args.config)
        print(f"   ✅ Parsed successfully")
    except Exception as e:
        print(f"   ❌ Failed to parse: {e}")
        sys.exit(1)

    markets = config.get("markets", [])
    defaults = config.get("defaults", {})
    print(f"   Markets: {len(markets)}")
    print(f"   Defaults: {defaults}")
    print()

    # Step 2: Validate each entry
    print("📋 Validating market entries...")
    all_valid = True
    enabled_markets = []

    for entry in markets:
        mid = entry.get("market_id", "<unknown>")
        enabled = entry.get("enabled", False)
        status = "🟢" if enabled else "⚪"

        errors = validate_market_entry(entry)
        if errors:
            print(f"  {status} {mid}: ❌ INVALID")
            for err in errors:
                print(f"      - {err}")
            all_valid = False
        else:
            print(f"  {status} {mid}: ✅ Valid ({entry.get('market_type', '?')})")
            if enabled:
                enabled_markets.append(entry)
    print()

    if not all_valid:
        print("❌ Some entries have validation errors. Fix before proceeding.")
        print()

    # Step 3: Run QuoteEngine on enabled markets
    if not enabled_markets:
        print("⚠️  No enabled markets to test with QuoteEngine.")
        sys.exit(0)

    print(f"⚙️  Running QuoteEngine on {len(enabled_markets)} enabled markets...")
    print()

    engine = QuoteEngine(config=QuoteEngineConfig(
        default_order_size=Decimal("50"),
        num_levels=1,
        level_spacing=Decimal("0.005"),
        default_ttl_ms=30_000,
        price_floor=Decimal("0.01"),
        price_ceiling=Decimal("0.99"),
    ))

    all_quotes_ok = True
    for entry in enabled_markets:
        mid = entry["market_id"]
        print(f"📊 {mid}")
        try:
            ok, details = validate_quote_plan(entry, engine)
            print(details)
            if ok:
                print(f"  ✅ Quotes are sensible\n")
            else:
                print(f"  ❌ Quote validation failed\n")
                all_quotes_ok = False
        except Exception as e:
            print(f"  ❌ Exception: {e}\n")
            all_quotes_ok = False

    # Summary
    print("=" * 70)
    if all_valid and all_quotes_ok:
        print("✅ ALL VALIDATIONS PASSED")
        print(f"   {len(enabled_markets)} markets ready for trading")
    else:
        print("⚠️  SOME VALIDATIONS FAILED — review output above")
        sys.exit(1)
    print("=" * 70)


if __name__ == "__main__":
    main()
