"""runner.config — Unified market configuration for paper and live modes.

Merges MarketConfig (paper) and ProdMarketConfig (production) into a single
``UnifiedMarketConfig`` dataclass.  Provides ``load_markets()`` for YAML-based
configs and ``auto_select_markets()`` for REST-based live market discovery.

Also provides ``RotationConfig`` for market rotation and capital recovery
settings (disabled by default for backward compatibility).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import structlog
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from models.market_state import MarketType

logger = structlog.get_logger("runner.config")


@dataclass
class UnifiedMarketConfig:
    """Unified market config used by both paper and live modes.

    This is a superset of MarketConfig (paper) and ProdMarketConfig (production).
    All fields that existed in either are present here.
    """

    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    description: str
    market_type: MarketType
    tick_size: Decimal
    min_order_size: Decimal
    neg_risk: bool
    spread_min_bps: int = 50
    max_position_size: Decimal = Decimal("500")
    enabled: bool = True
    execution_mode: str = "rewards_farming"
    reward_min_size_usdc: Decimal | None = None
    reward_max_spread_cents: Decimal | None = None
    expected_reward_yield_bps_day: float | None = None
    expected_fill_rate_pct: float | None = None
    max_inventory_per_side: Decimal | None = None
    order_size_override: Decimal | None = None
    half_spread_bps_override: int | None = None
    min_quote_lifetime_s: float | None = None
    max_requote_rate_per_min: float | None = None
    health_score_threshold: float | None = None
    directional_side: str | None = None
    entry_price_limit: Decimal | None = None
    model_probability: float | None = None
    market_implied_probability: float | None = None
    edge_bps: float | None = None
    confidence: float | None = None
    stake_usdc: Decimal | None = None
    max_slippage_bps: int | None = None
    ttl_seconds: int | None = None
    stop_rule: str | None = None
    take_profit_rule: str | None = None
    source_evidence_ids: list[str] = field(default_factory=list)
    disable_reason: str = ""


@dataclass
class RotationConfig:
    """Configuration for market rotation and capital recovery.

    All features are disabled by default for backward compatibility.
    Enable via YAML config or CLI flags.
    """

    # Market rotation
    market_rotation: bool = False
    rotation_cooldown_hours: float = 1.0
    min_market_health_score: float = 0.3

    # Health thresholds
    max_spread_bps: int = 500
    min_fill_rate_pct: float = 1.0
    fill_rate_window_hours: float = 2.0
    max_inventory_skew_pct: float = 80.0

    # Capital recovery
    capital_recovery: bool = False
    min_balance_for_recovery: Decimal = field(default_factory=lambda: Decimal("5"))

    # Blacklist persistence
    blacklist_path: Path = field(
        default_factory=lambda: PROJECT_ROOT / "paper" / "data" / "rotation_blacklist.json"
    )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RotationConfig":
        """Create from a dict (e.g. YAML section)."""
        kwargs: dict[str, Any] = {}
        if "market_rotation" in d:
            kwargs["market_rotation"] = bool(d["market_rotation"])
        if "rotation_cooldown_hours" in d:
            kwargs["rotation_cooldown_hours"] = float(d["rotation_cooldown_hours"])
        if "min_market_health_score" in d:
            kwargs["min_market_health_score"] = float(d["min_market_health_score"])
        if "max_spread_bps" in d:
            kwargs["max_spread_bps"] = int(d["max_spread_bps"])
        if "min_fill_rate_pct" in d:
            kwargs["min_fill_rate_pct"] = float(d["min_fill_rate_pct"])
        if "fill_rate_window_hours" in d:
            kwargs["fill_rate_window_hours"] = float(d["fill_rate_window_hours"])
        if "max_inventory_skew_pct" in d:
            kwargs["max_inventory_skew_pct"] = float(d["max_inventory_skew_pct"])
        if "capital_recovery" in d:
            kwargs["capital_recovery"] = bool(d["capital_recovery"])
        if "min_balance_for_recovery" in d:
            kwargs["min_balance_for_recovery"] = Decimal(str(d["min_balance_for_recovery"]))
        if "blacklist_path" in d:
            kwargs["blacklist_path"] = Path(d["blacklist_path"])
        return cls(**kwargs)


def load_rotation_blacklist(path: Path) -> set[str]:
    """Load rotation blacklist from file (persisted across restarts)."""
    try:
        if path.exists():
            with open(path) as f:
                data = json.load(f)
            return set(data.get("blacklisted_markets", []))
    except Exception as e:
        logger.warning("rotation_blacklist.load_error", error=str(e))
    return set()


def save_rotation_blacklist(path: Path, blacklist: set[str]) -> None:
    """Save rotation blacklist to file for persistence across restarts."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"blacklisted_markets": sorted(blacklist)}, f, indent=2)
        tmp.replace(path)
    except Exception as e:
        logger.warning("rotation_blacklist.save_error", error=str(e))


def load_markets(config_path: Path) -> list[UnifiedMarketConfig]:
    """Load market configs from a markets.yaml file.

    Compatible with the existing ``config/markets.yaml`` format used by
    both paper_runner and production_runner.
    """
    with open(config_path) as f:
        data = yaml.safe_load(f)

    markets: list[UnifiedMarketConfig] = []
    for m in data.get("markets", []):
        if not m.get("enabled", True):
            continue
        params = m.get("params", {})
        mt = m.get("market_type", "OTHER")
        markets.append(UnifiedMarketConfig(
            market_id=m["market_id"],
            condition_id=m["condition_id"],
            token_id_yes=m["token_id_yes"],
            token_id_no=m["token_id_no"],
            description=m.get("description", ""),
            market_type=MarketType(mt),
            tick_size=Decimal(str(params.get("tick_size", "0.01"))),
            min_order_size=Decimal(str(params.get("min_order_size", "5"))),
            neg_risk=params.get("neg_risk", False),
            spread_min_bps=int(params.get("spread_min_bps", 50)),
            max_position_size=Decimal(str(params.get("max_position_size", "500"))),
            enabled=m.get("enabled", True),
        ))
    return markets


async def auto_select_markets(
    rest_client: Any,
    max_markets: int = 1,
    blacklist: set[str] | None = None,
) -> list[UnifiedMarketConfig]:
    """Auto-select markets from Polymarket REST API.

    Criteria:
    - Active and not closed
    - Has valid token IDs
    - Not in blacklist
    - Price near 0.50 (maximizes entropy for market-making)

    Falls back to Gamma API via discover_markets.py if CLOB REST
    returns no viable candidates (common when price data is missing).
    """
    logger.info("auto_selecting_markets")
    blacklist = blacklist or set()

    # ── Primary: try Gamma API (has price data) ──
    try:
        selected = await _auto_select_via_gamma(max_markets, blacklist)
        if selected:
            return selected
    except Exception as e:
        logger.warning("auto_select.gamma_fallback_failed", error=str(e))

    # ── Fallback: CLOB REST API ──
    raw_markets = await rest_client.get_active_markets(max_pages=3)

    candidates = []
    for m in raw_markets:
        if not m.get("active") or m.get("closed"):
            continue
        if not m.get("token_id_yes") or not m.get("token_id_no"):
            continue
        cid = m.get("condition_id", "")
        if cid in blacklist:
            continue
        candidates.append(m)

    # Without price data from CLOB REST, just take the first candidates
    # (they're already filtered for validity)
    selected: list[UnifiedMarketConfig] = []
    for m in candidates[:max_markets]:
        selected.append(UnifiedMarketConfig(
            market_id=m["condition_id"],
            condition_id=m["condition_id"],
            token_id_yes=m["token_id_yes"],
            token_id_no=m["token_id_no"],
            description=m.get("question", m["condition_id"])[:80],
            market_type=MarketType.OTHER,
            tick_size=Decimal(str(m.get("tick_size", "0.01"))),
            min_order_size=Decimal(str(m.get("min_order_size", "5"))),
            neg_risk=m.get("neg_risk", False),
        ))
        logger.info("market_selected",
                     market_id=m["condition_id"],
                     question=m.get("question", "")[:60])

    return selected


async def _auto_select_via_gamma(
    max_markets: int,
    blacklist: set[str],
) -> list[UnifiedMarketConfig]:
    """Use Gamma API for market discovery (has price data, unlike CLOB REST)."""
    import asyncio

    # Import discover_markets inline to avoid hard dependency
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from discover_markets import discover

    # Run sync discover in executor
    loop = asyncio.get_running_loop()
    candidates = await loop.run_in_executor(
        None,
        lambda: discover(min_volume=10_000, top_n=max_markets * 5),
    )

    # Filter out blacklisted and select best candidates
    selected: list[UnifiedMarketConfig] = []
    for c in candidates:
        cid = c.get("condition_id", "")
        if cid in blacklist:
            continue
        yes_price = c.get("yes_price", 0.5)
        if yes_price < 0.20 or yes_price > 0.80:
            continue

        selected.append(UnifiedMarketConfig(
            market_id=cid,
            condition_id=cid,
            token_id_yes=c["token_id_yes"],
            token_id_no=c["token_id_no"],
            description=c.get("question", cid)[:80],
            market_type=MarketType(c.get("market_type", "OTHER")),
            tick_size=Decimal(str(c.get("tick_size", "0.01"))),
            min_order_size=Decimal(str(c.get("min_order_size", "5"))),
            neg_risk=c.get("neg_risk", False),
        ))
        logger.info("market_selected_gamma",
                     market_id=cid,
                     question=c.get("question", "")[:60],
                     yes_price=yes_price)
        if len(selected) >= max_markets:
            break

    return selected
