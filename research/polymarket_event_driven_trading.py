"""Directional Polymarket event-driven module (POC / skeleton).

This module intentionally avoids secrets and direct side effects.
It defines interfaces and pure-pseudocode flows for:

- ingest/normalization
- mispricing scoring
- Kelly-style sizing
- execution gating (cooldowns/risk)
- Supabase payload generation compatible with runner tables

It is not a production bot and should be wired into the existing runner
via an adapter before live use.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Mapping, Iterable, Protocol


# -----------------------------
# Data models
# -----------------------------

@dataclass(frozen=True)
class MarketSnapshot:
    market_id: str
    category: str
    price_yes: Decimal
    price_no: Decimal
    best_bid: Decimal
    best_ask: Decimal
    liquidity_usd: Decimal
    ttl_min: int
    spread_bps: Decimal
    resolved: bool = False
    close_ts: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SignalInput:
    market_id: str
    source: str
    probability_yes: Decimal
    confidence: Decimal
    captured_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    market_id: str
    category: str
    direction: str  # YES / NO
    model_prob_yes: Decimal
    market_prob_yes: Decimal
    edge: Decimal
    confidence: Decimal
    kelly_frac: Decimal
    stake_usdc: Decimal
    reason_blocked: str | None = None


@dataclass(frozen=True)
class RiskState:
    capital_total: Decimal
    capital_free: Decimal
    exposure_net: Decimal
    active_positions: dict[str, Decimal] = field(default_factory=dict)
    consecutive_losses: int = 0
    drawdown_1h: Decimal = Decimal("0")


@dataclass(frozen=True)
class StrategyConfig:
    # scoring
    min_abs_edge: Decimal = Decimal("0.025")
    min_confidence: Decimal = Decimal("0.25")
    max_spread_bps: Decimal = Decimal("15")

    # sizing
    max_kelly_fraction: Decimal = Decimal("0.15")
    max_stake_usdc: Decimal = Decimal("120")
    min_stake_usdc: Decimal = Decimal("25")
    operating_buffer_usdc: Decimal = Decimal("150")
    max_market_exposure_pct: Decimal = Decimal("0.12")
    max_total_exposure_pct: Decimal = Decimal("0.35")
    kelly_scale: Decimal = Decimal("0.25")

    # filters
    min_liquidity_usd: Decimal = Decimal("2000")
    min_ttl_min: int = 90
    max_data_age_sec: int = 90

    # cooldowns
    global_entry_cooldown_sec: int = 90
    category_cooldown_sec: int = 180
    market_rejects_cooldown_sec: int = 900
    max_consecutive_losses: int = 2

    # risk caps
    max_drawdown_1h: Decimal = Decimal("0.20")


# -----------------------------
# Protocols/abstractions
# -----------------------------

class MarketIngestor(Protocol):
    """Adapter contract for collecting market/signal snapshots."""

    def markets(self) -> Iterable[MarketSnapshot]: ...
    def news_signals(self, market_id: str) -> Iterable[SignalInput]: ...
    def sports_signals(self, market_id: str) -> Iterable[SignalInput]: ...


class RunnerAdapter(Protocol):
    """Minimal contract used by the strategy module."""

    async def place_limit_order(self, market_id: str, side: str, price: Decimal, size_usdc: Decimal) -> str:
        ...

    async def cancel_order(self, order_id: str) -> None:
        ...

    async def get_balance(self) -> Decimal:
        ...

    async def get_open_positions(self) -> Mapping[str, Decimal]:
        ...


# -----------------------------
# Helpers / core logic
# -----------------------------


def parse_price_pair(price_yes: Decimal, price_no: Decimal) -> None:
    total = (price_yes + price_no).quantize(Decimal("0.0001"))
    if abs(total - Decimal("1")) > Decimal("0.001"):
        raise ValueError(f"Invalid binary market prices: yes={price_yes} no={price_no}")


class FilterError(ValueError):
    ...


def classify_category(text: str) -> str:
    t = text.lower()
    if "fed" in t or "bc" in t or "cpi" in t or "macro" in t:
        return "macro"
    if "election" in t or "senate" in t or "vote" in t:
        return "politics"
    if "nfl" in t or "nba" in t or "mls" in t or "ufc" in t or "sports" in t:
        return "sports"
    return "niche"


@dataclass(frozen=True)
class CooldownState:
    global_until: datetime
    category_until: dict[str, datetime] = field(default_factory=dict)
    market_until: dict[str, datetime] = field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_in_cooldown(market_id: str, category: str, cds: CooldownState, *, now: datetime | None = None) -> str | None:
    """Return cooldown reason or None."""
    now = now or now_utc()
    if now < cds.global_until:
        return "global_cooldown"
    if now < cds.category_until.get(category, datetime.min.replace(tzinfo=timezone.utc)):
        return "category_cooldown"
    if now < cds.market_until.get(market_id, datetime.min.replace(tzinfo=timezone.utc)):
        return "market_cooldown"
    return None


def blend_probability(
    *,
    prior: Decimal,
    news: SignalInput | None,
    sports: Iterable[SignalInput],
    w_prior: Decimal,
    w_news: Decimal,
    w_sports: Decimal,
) -> tuple[Decimal, Decimal]:
    """Blend priors with external signals.

    NOTE: pseudo-implementation, intentionally conservative.
    """
    news_prob = Decimal("0")
    news_conf = Decimal("0")
    if news is not None:
        news_prob = news.probability_yes
        news_conf = news.confidence

    sports_list = list(sports)
    if sports_list:
        sports_prob = sum(s.probability_yes for s in sports_list) / Decimal(len(sports_list))
        sports_conf = min(max(s.confidence for s in sports_list), Decimal("1"))
    else:
        sports_prob = Decimal("0")
        sports_conf = Decimal("0")

    total_w = w_prior + w_news * news_conf + w_sports * sports_conf
    if total_w <= 0:
        return prior, Decimal("0")

    model = (
        prior * w_prior
        + news_prob * (w_news * news_conf)
        + sports_prob * (w_sports * sports_conf)
    ) / total_w
    conf = (news_conf * w_news + sports_conf * w_sports)
    return model, conf


def kelly_fraction(p_yes: Decimal, p_market: Decimal) -> Decimal:
    """Fractional Kelly for binary; returns >= 0."""
    if not (Decimal("0") < p_market < Decimal("1")):
        return Decimal("0")
    edge = p_yes - p_market
    q = Decimal("1") / p_market
    b = q - Decimal("1")
    if b <= 0:
        return Decimal("0")
    raw = (q * p_yes - Decimal("1")) / b
    return max(Decimal("0"), raw)


def size_stake(
    cfg: StrategyConfig,
    risk: RiskState,
    market_id: str,
    k_frac: Decimal,
) -> Decimal:
    """Translate Kelly fraction to bounded stake."""
    if k_frac <= 0:
        return Decimal("0")

    available = max(Decimal("0"), risk.capital_free - cfg.operating_buffer_usdc)
    if available <= 0:
        return Decimal("0")

    # Fractional Kelly and portfolio caps
    frac = min(cfg.max_kelly_fraction, k_frac * cfg.kelly_scale)
    stake = (available * frac).quantize(Decimal("0.01"))

    # Global and per-market exposure checks are handled outside this function.
    if stake < cfg.min_stake_usdc:
        return Decimal("0")
    if stake > cfg.max_stake_usdc:
        return cfg.max_stake_usdc
    return stake


def compute_decision(
    cfg: StrategyConfig,
    market: MarketSnapshot,
    news_signal: SignalInput | None,
    sports_signals: Iterable[SignalInput],
) -> Decision:
    """Compute model edge and suggested stake for a single market."""
    parse_price_pair(market.price_yes, market.price_no)

    prior = Decimal("0.50")
    if market.category in {"macro", "sports", "politics", "niche"}:
        # domain priors can be loaded via config; kept explicit for this skeleton
        prior = Decimal("0.50")

    p_model, conf = blend_probability(
        prior=prior,
        news=news_signal,
        sports=list(sports_signals),
        w_prior=Decimal("0.3"),
        w_news=Decimal("0.45"),
        w_sports=Decimal("0.25"),
    )

    # Direction chooses max value for either YES or NO
    edge_yes = p_model - market.best_ask
    edge_no = (Decimal("1") - p_model) - (Decimal("1") - market.best_ask)
    if abs(edge_yes) >= abs(edge_no):
        direction = "YES"
        edge = edge_yes
        market_prob = market.price_yes
    else:
        direction = "NO"
        edge = edge_no
        market_prob = market.price_no

    if abs(edge) < cfg.min_abs_edge:
        return Decision(
            market_id=market.market_id,
            category=market.category,
            direction=direction,
            model_prob_yes=p_model,
            market_prob_yes=market_prob,
            edge=edge,
            confidence=conf,
            kelly_frac=Decimal("0"),
            stake_usdc=Decimal("0"),
            reason_blocked="edge_below_threshold",
        )

    if conf < cfg.min_confidence:
        return Decision(
            market_id=market.market_id,
            category=market.category,
            direction=direction,
            model_prob_yes=p_model,
            market_prob_yes=market_prob,
            edge=edge,
            confidence=conf,
            kelly_frac=Decimal("0"),
            stake_usdc=Decimal("0"),
            reason_blocked="low_confidence",
        )

    if market.spread_bps > cfg.max_spread_bps:
        return Decision(
            market_id=market.market_id,
            category=market.category,
            direction=direction,
            model_prob_yes=p_model,
            market_prob_yes=market_prob,
            edge=edge,
            confidence=conf,
            kelly_frac=Decimal("0"),
            stake_usdc=Decimal("0"),
            reason_blocked="spread_too_wide",
        )

    # provisional kelly is computed with side-specific market entry price
    kf = kelly_fraction(p_model if direction == "YES" else (Decimal("1") - p_model),
                        market.best_ask if direction == "YES" else (Decimal("1") - market.best_ask))
    return Decision(
        market_id=market.market_id,
        category=market.category,
        direction=direction,
        model_prob_yes=p_model,
        market_prob_yes=market_prob,
        edge=edge,
        confidence=conf,
        kelly_frac=kf,
        stake_usdc=Decimal("0"),
        reason_blocked=None,
    )


def apply_risk_and_sizing(
    cfg: StrategyConfig,
    risk: RiskState,
    market: MarketSnapshot,
    decision: Decision,
) -> Decision:
    """Apply risk gates and sizing. Returns final Decision with stake or reason."""

    if decision.stake_usdc != Decimal("0") or decision.reason_blocked:
        return decision

    # drawdown / loss guard
    if risk.drawdown_1h >= cfg.max_drawdown_1h:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "drawdown_guard"}
        )
    if risk.consecutive_losses >= cfg.max_consecutive_losses:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "loss_streak_guard"}
        )

    # liquidity / TTL guard
    if market.liquidity_usd < cfg.min_liquidity_usd:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "low_liquidity"}
        )
    if market.ttl_min < cfg.min_ttl_min:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "too_close_to_resolution"}
        )

    stake = size_stake(cfg, risk, market.market_id, decision.kelly_frac)
    if stake <= 0:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "size_zero"}
        )

    # Exposure caps (simplified)
    max_exposure = risk.capital_total * cfg.max_total_exposure_pct
    current_exposure = sum(risk.active_positions.values(), Decimal("0"))
    if current_exposure + stake > max_exposure:
        return Decision(
            **{**decision.__dict__, "stake_usdc": Decimal("0"), "reason_blocked": "max_total_exposure_reached"}
        )

    per_market_cap = risk.capital_total * cfg.max_market_exposure_pct
    if stake > per_market_cap:
        stake = per_market_cap

    return Decision(**{**decision.__dict__, "stake_usdc": stake, "reason_blocked": None})


def build_supabase_order_payload(decision: Decision, order_id: str, side: str, run_id: str) -> dict:
    """Payload for existing runner table `pmm_orders`."""
    return {
        "run_id": run_id,
        "market_id": decision.market_id,
        "order_id": order_id,
        "side": side,
        "token_side": decision.direction,
        "price": float(decision.market_prob_yes),
        "size": float(decision.stake_usdc),
        "status": "submitted",
        "complement_routed": False,
    }


def build_supabase_run_payload(
    *,
    run_id: str,
    strategy: str,
    selection_count: int,
    selected_count: int,
    blocked_count: int,
    status: str,
) -> dict:
    """Payload for existing runner table `pmm_runs`. Keep config serialized in params_json."""
    return {
        "run_id": run_id,
        "mode": "paper",
        "status": status,
        "total_orders": selection_count,
        "total_fills": 0,
        "total_pnl": 0,
        "params_json": {
            "strategy": strategy,
            "selection_count": selection_count,
            "selected_count": selected_count,
            "blocked_count": blocked_count,
            "generated_at": now_utc().isoformat(),
        },
    }


async def run_tick(
    cfg: StrategyConfig,
    ingestor: MarketIngestor,
    runner: RunnerAdapter,
    risk: RiskState,
    cooldowns: CooldownState,
    run_id: str,
) -> tuple[list[Decision], list[str]]:
    """Single-cycle orchestration.

    Pseudocode:
      - ingest snapshots
      - score per market
      - apply risk/cooldown
      - submit at most top-N non-zero stake decisions
      - return decisions and order ids for caller.

    Returns
    -------
    tuple[decisions, order_ids]
    """
    decisions: list[Decision] = []
    orders: list[str] = []

    for snap in ingestor.markets():
        if snap.resolved:
            continue

        cooldown_reason = is_in_cooldown(snap.market_id, snap.category, cooldowns)
        if cooldown_reason:
            decisions.append(
                Decision(
                    market_id=snap.market_id,
                    category=snap.category,
                    direction="YES",
                    model_prob_yes=Decimal("0.5"),
                    market_prob_yes=snap.price_yes,
                    edge=Decimal("0"),
                    confidence=Decimal("0"),
                    kelly_frac=Decimal("0"),
                    stake_usdc=Decimal("0"),
                    reason_blocked=cooldown_reason,
                )
            )
            continue

        news = next(iter(ingestor.news_signals(snap.market_id)), None)
        sports = ingestor.sports_signals(snap.market_id)
        raw = compute_decision(cfg, snap, news, sports)
        final = apply_risk_and_sizing(cfg, risk, snap, raw)
        decisions.append(final)

        if final.stake_usdc > 0:
            side = "BUY"
            # For NO, runner still receives price/size and side of token; this is pseudocode.
            token_side = final.direction
            if token_side == "YES":
                px = snap.best_ask
            else:
                px = Decimal("1") - snap.best_bid
            order_id = await runner.place_limit_order(snap.market_id, side, px, final.stake_usdc)
            orders.append(order_id)

    return decisions, orders
