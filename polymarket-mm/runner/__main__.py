"""runner.__main__ — CLI entrypoint for the unified trading pipeline.

Usage::

    python -m runner --mode paper --config paper/runs/p5-001.yaml --duration-hours 0.01
    python -m runner --mode live  --config paper/runs/prod-001.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.event_bus import EventBus
from models.market_state import MarketType
from paper.paper_runner import RunConfig
from runner.config import RotationConfig, UnifiedMarketConfig, auto_select_markets, load_markets
from runner.decision_envelope import DecisionEnvelope, EventDrivenMarketDecision, RewardMarketDecision
from runner.pipeline import UnifiedTradingPipeline
from runner.transport import (
    DEFAULT_PROXY_URL,
    TransportLatencyRecorder,
    apply_py_clob_transport,
    choose_transport,
    probe_transport_set,
)

logger = structlog.get_logger("runner.__main__")


def _resolve_decision_envelope_path(args, run_config: RunConfig | None) -> Path | None:
    raw = getattr(args, "decision_envelope", None)
    if not raw and run_config:
        raw = run_config.params.get("decision_envelope_path")
    if not raw:
        raw = os.environ.get("PMM_DECISION_ENVELOPE")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def _load_decision_envelope(args, run_config: RunConfig | None, *, required: bool) -> DecisionEnvelope | None:
    path = _resolve_decision_envelope_path(args, run_config)
    if path is None:
        if required:
            raise SystemExit("live mode requires --decision-envelope or PMM_DECISION_ENVELOPE")
        return None
    envelope = DecisionEnvelope.load(path)
    envelope.require_live_ready()
    logger.info(
        "decision_envelope.loaded",
        path=str(path),
        decision_id=envelope.decision_id,
        expires_at=envelope.expires_at.isoformat(),
        trading_state=envelope.trading_state,
        decision_scope=envelope.decision_scope,
        markets=len(envelope.markets),
    )
    return envelope


def _market_type(value: str) -> MarketType:
    try:
        return MarketType(value)
    except Exception:
        return MarketType.OTHER


def _market_config_from_decision(
    decision_market: RewardMarketDecision | EventDrivenMarketDecision,
) -> UnifiedMarketConfig:
    kwargs = {
        "market_id": decision_market.condition_id,
        "condition_id": decision_market.condition_id,
        "token_id_yes": decision_market.token_id_yes,
        "token_id_no": decision_market.token_id_no,
        "description": getattr(decision_market, "description", "") or decision_market.market_id,
        "market_type": _market_type(getattr(decision_market, "market_type", "OTHER")),
        "tick_size": getattr(decision_market, "tick_size", Decimal("0.01")),
        "min_order_size": getattr(decision_market, "min_order_size", Decimal("5")),
        "neg_risk": getattr(decision_market, "neg_risk", False),
        "execution_mode": decision_market.mode,
        "disable_reason": getattr(decision_market, "disable_reason", ""),
    }

    if isinstance(decision_market, RewardMarketDecision):
        kwargs.update(
            spread_min_bps=decision_market.half_spread_bps * 2,
            max_position_size=decision_market.max_inventory_per_side,
            reward_min_size_usdc=decision_market.reward_min_size_usdc,
            reward_max_spread_cents=decision_market.reward_max_spread_cents,
            expected_reward_yield_bps_day=decision_market.expected_reward_yield_bps_day,
            expected_fill_rate_pct=decision_market.expected_fill_rate_pct,
            max_inventory_per_side=decision_market.max_inventory_per_side,
            order_size_override=decision_market.order_size,
            half_spread_bps_override=decision_market.half_spread_bps,
            min_quote_lifetime_s=decision_market.min_quote_lifetime_s,
            max_requote_rate_per_min=decision_market.max_requote_rate_per_min,
            health_score_threshold=decision_market.health_score_threshold,
        )
    else:
        kwargs.update(
            spread_min_bps=50,
            max_position_size=decision_market.stake_usdc,
            directional_side=decision_market.side,
            entry_price_limit=decision_market.entry_price_limit,
            model_probability=decision_market.model_probability,
            market_implied_probability=decision_market.market_implied_probability,
            edge_bps=decision_market.edge_bps,
            confidence=decision_market.confidence,
            stake_usdc=decision_market.stake_usdc,
            max_slippage_bps=decision_market.max_slippage_bps,
            ttl_seconds=decision_market.ttl_seconds,
            stop_rule=decision_market.stop_rule,
            take_profit_rule=decision_market.take_profit_rule,
            source_evidence_ids=list(decision_market.source_evidence_ids),
        )

    return UnifiedMarketConfig(**kwargs)


def _markets_from_envelope(envelope: DecisionEnvelope) -> list[UnifiedMarketConfig]:
    markets: list[UnifiedMarketConfig] = []
    rewards_count = 0
    directional_count = 0

    for item in envelope.enabled_markets():
        if isinstance(item, RewardMarketDecision):
            if not envelope.mode_allocations.rewards_enabled:
                continue
            if rewards_count >= envelope.risk_limits.max_active_rewards_markets:
                continue
            rewards_count += 1
        elif isinstance(item, EventDrivenMarketDecision):
            if not envelope.mode_allocations.directional_enabled:
                continue
            if directional_count >= envelope.risk_limits.max_active_directional_markets:
                continue
            directional_count += 1
        markets.append(_market_config_from_decision(item))
    return markets


async def _validate_live_capital(rest_client, envelope: DecisionEnvelope) -> Decimal:
    balance_info = await rest_client.get_balance_allowance("COLLATERAL")
    raw_balance = Decimal(str(balance_info.get("balance", "0")))
    balance_usdc = raw_balance / Decimal("1000000")

    allowance_raw = balance_info.get("allowance")
    if allowance_raw is None and isinstance(balance_info.get("allowances"), dict):
        allowance_candidates = [v for v in balance_info["allowances"].values() if v is not None]
        allowance_raw = allowance_candidates[0] if allowance_candidates else None
    if envelope.risk_limits.enforce_balance_allowance_check and allowance_raw is not None:
        allowance = Decimal(str(allowance_raw))
        if allowance <= 0:
            raise SystemExit("live mode aborted: collateral allowance is zero")

    required_capital = envelope.capital_policy.total_capital_usdc
    if balance_usdc + Decimal("0.000001") < required_capital:
        raise SystemExit(
            f"live mode aborted: on-chain USDC {balance_usdc} is below required total_capital_usdc {required_capital}"
        )
    logger.info(
        "live.capital_validated",
        on_chain_usdc=str(balance_usdc),
        required_capital=str(required_capital),
    )
    return required_capital


def _configure_transport(envelope: DecisionEnvelope) -> tuple[Any, TransportLatencyRecorder]:
    proxy_url = os.environ.get("POLYMARKET_PROXY", DEFAULT_PROXY_URL)
    direct_samples, proxy_samples = probe_transport_set(proxy_url=proxy_url)
    selection = choose_transport(
        envelope.transport_policy,
        direct_samples,
        proxy_samples,
        proxy_url=proxy_url,
    )
    apply_py_clob_transport(selection)
    recorder = TransportLatencyRecorder(selection=selection, proxy_url=proxy_url)
    for sample in direct_samples + proxy_samples:
        recorder.record_probe(sample)
    recorder.write_summary()
    logger.info(
        "transport.selected",
        selected_transport=selection.selected_transport,
        rewards_live_ok=selection.rewards_live_ok,
        directional_live_ok=selection.directional_live_ok,
        reason=selection.reason,
    )
    return selection, recorder


async def _run_paper(
    args,
    run_config: RunConfig | None,
    decision_envelope: DecisionEnvelope | None = None,
) -> None:
    """Paper mode: simulated venue with PaperVenueAdapter + PaperWalletAdapter."""
    from paper.paper_venue import FeeConfig, MarketSimConfig, PaperVenue
    from runner.paper_venue_adapter import PaperVenueAdapter
    from runner.paper_wallet import PaperWalletAdapter

    # Load markets from YAML or DecisionEnvelope
    if decision_envelope:
        markets = _markets_from_envelope(decision_envelope)
    else:
        config_path = PROJECT_ROOT / "config" / "markets.yaml"
        markets = load_markets(config_path)

    # Filter by run config if specified
    if run_config and run_config.params.get("markets"):
        market_ids = set(run_config.params["markets"])
        filtered = [m for m in markets if m.market_id in market_ids]
        if filtered:
            markets = filtered
        else:
            logger.warning("No markets matched config filter, using all")

    logger.info("paper.markets_loaded", count=len(markets))
    for m in markets:
        logger.info("paper.market", market_id=m.market_id, description=m.description)

    # Extract params
    params = run_config.params if run_config else {}
    fill_probability = float(params.get("fill_probability",
                                         params.get("fill_probability_override", 0.5)))
    default_market = next((m for m in markets if m.execution_mode == "rewards_farming"), markets[0])
    order_size = Decimal(str(
        params.get("order_size", params.get("default_order_size", default_market.order_size_override or "50"))
    ))
    half_spread_bps = int(
        params.get("half_spread_bps", params.get("default_half_spread_bps", default_market.half_spread_bps_override or 50))
    )
    gamma = float(params.get("gamma", params.get("gamma_risk_aversion", 0.3)))
    initial_balance = run_config.initial_balance if run_config else Decimal("500")
    adv_sel_bps = int(params.get("adverse_selection_bps", 0))
    maker_fee = int(params.get("maker_fee_bps", 0))
    fill_decay = bool(params.get("fill_distance_decay", False))
    bal_aware = bool(params.get("balance_aware_quoting", False))
    min_bal = Decimal(str(params.get("min_balance_to_quote", 5)))
    pos_recycling = bool(params.get("position_recycling", False))
    recycle_threshold = Decimal(str(params.get("recycle_profit_threshold", "0.02")))
    ks_max_dd = float(params.get("kill_switch_max_drawdown_pct", 25.0))
    ks_alert = float(params.get("kill_switch_alert_pct", 15.0))

    # Create PaperVenue
    event_bus = EventBus()
    venue_configs = [
        MarketSimConfig(
            market_id=m.market_id,
            condition_id=m.condition_id,
            token_id_yes=m.token_id_yes,
            token_id_no=m.token_id_no,
            tick_size=m.tick_size,
            min_order_size=m.min_order_size,
            neg_risk=m.neg_risk,
            market_type=m.market_type,
            initial_yes_mid=Decimal("0.50"),
            volatility=Decimal("0.005"),
            fill_probability=fill_probability,
            adverse_selection_bps=adv_sel_bps,
            fill_distance_decay=fill_decay,
        )
        for m in markets
    ]

    fee_config = FeeConfig(maker_fee_bps=maker_fee)
    venue = PaperVenue(
        event_bus=event_bus,
        configs=venue_configs,
        fill_latency_ms=50.0,
        partial_fill_probability=0.5,
        initial_balance=initial_balance,
        fee_config=fee_config,
    )

    venue_adapter = PaperVenueAdapter(venue=venue, event_bus=event_bus)
    wallet_adapter = PaperWalletAdapter(venue=venue)

    # Load rotation config
    rotation_config = RotationConfig()
    if params.get("rotation"):
        rotation_config = RotationConfig.from_dict(params["rotation"])

    pipeline = UnifiedTradingPipeline(
        market_configs=markets,
        venue=venue_adapter,
        wallet=wallet_adapter,
        event_bus=event_bus,
        duration_hours=args.duration_hours,
        quote_interval_s=args.quote_interval,
        run_config=run_config,
        order_size=order_size,
        half_spread_bps=half_spread_bps,
        gamma=gamma,
        kill_switch_max_drawdown_pct=ks_max_dd,
        kill_switch_alert_pct=ks_alert,
        balance_aware_quoting=bal_aware,
        min_balance_to_quote=min_bal,
        position_recycling=pos_recycling,
        recycle_profit_threshold=recycle_threshold,
        rotation_config=rotation_config,
        decision_envelope=decision_envelope,
    )

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()


async def _run_live(
    args,
    run_config: RunConfig | None,
    decision_envelope: DecisionEnvelope,
) -> None:
    """Live mode: real CLOB via LiveVenueAdapter + ProductionWalletAdapter."""
    from data.rest_client import CLOBRestClient
    from execution.live_execution import LiveExecution
    from paper.production_runner import ProductionWallet
    from paper.startup_reconciler import StartupReconciler, StartupReconciliationConfig
    from runner.live_venue_adapter import LiveVenueAdapter
    from runner.production_wallet import ProductionWalletAdapter

    params = run_config.params if run_config else {}
    transport_selection, latency_recorder = _configure_transport(decision_envelope)
    initial_balance = decision_envelope.capital_policy.total_capital_usdc
    reward_markets = [m for m in decision_envelope.enabled_markets("rewards_farming")]
    primary_rewards = reward_markets[0] if reward_markets else None
    order_size = Decimal(str(params.get("default_order_size", primary_rewards.order_size if primary_rewards else "5")))
    half_spread_bps = int(params.get("default_half_spread_bps", primary_rewards.half_spread_bps if primary_rewards else 50))
    gamma = float(params.get("gamma_risk_aversion", 0.3))
    quote_interval = float(params.get("quote_interval_s", 5.0))
    ks_max_dd = float(params.get("kill_switch_max_drawdown_pct", 20.0))
    ks_alert = float(params.get("kill_switch_alert_pct", 10.0))
    complement_routing = bool(params.get("complement_routing", True))
    max_position_per_side = Decimal(str(params.get("max_position_per_side", 100)))
    supabase_logging = bool(params.get("supabase_logging", False))

    # Initialize REST client
    api_key = os.environ.get("POLYMARKET_API_KEY", "")
    api_secret = os.environ.get("POLYMARKET_API_SECRET", "") or os.environ.get("POLYMARKET_SECRET", "")
    api_passphrase = os.environ.get("POLYMARKET_PASSPHRASE", "")
    private_key = os.environ.get("POLYGON_PRIVATE_KEY", "") or os.environ.get("POLYMARKET_PRIVATE_KEY", "")

    if not all([api_key, api_secret, api_passphrase, private_key]):
        print("ERROR: Missing required environment variables:")
        print("  POLYMARKET_API_KEY, POLYMARKET_API_SECRET,")
        print("  POLYMARKET_PASSPHRASE, POLYGON_PRIVATE_KEY")
        sys.exit(1)

    rest_client = CLOBRestClient(
        private_key=private_key,
        api_key=api_key,
        api_secret=api_secret,
        api_passphrase=api_passphrase,
        proxy_url=transport_selection.selected_proxy_url or os.environ.get("POLYMARKET_PROXY", DEFAULT_PROXY_URL),
        rate_limit_rps=5.0,
    )
    await rest_client.connect()
    initial_balance = await _validate_live_capital(rest_client, decision_envelope)

    markets = _markets_from_envelope(decision_envelope)

    if not markets:
        logger.error("no_markets_found")
        sys.exit(1)

    logger.info("live.markets_ready", count=len(markets))

    # Create adapters
    event_bus = EventBus()
    execution = LiveExecution(
        rest_client=rest_client,
        default_tick_size="0.01",
        default_neg_risk=False,
        latency_recorder=latency_recorder,
        decision_id=decision_envelope.decision_id,
    )
    prod_wallet = ProductionWallet(initial_balance=initial_balance)
    wallet_adapter = ProductionWalletAdapter(wallet=prod_wallet)

    venue_adapter = LiveVenueAdapter(
        execution=execution,
        rest_client=rest_client,
        market_configs=markets,
        complement_routing=complement_routing,
        max_position_per_side=max_position_per_side,
        wallet_adapter=wallet_adapter,
    )

    # Supabase logger
    supa_logger = None
    if supabase_logging:
        from paper.db.supabase_logger import SupabaseLogger
        supa_logger = SupabaseLogger(
            run_id=run_config.run_id if run_config else "live-run",
            enabled=True,
        )

    # Load rotation config
    rotation_config_live = RotationConfig()
    if params.get("rotation"):
        rotation_config_live = RotationConfig.from_dict(params["rotation"])

    pipeline = UnifiedTradingPipeline(
        market_configs=markets,
        venue=venue_adapter,
        wallet=wallet_adapter,
        event_bus=event_bus,
        duration_hours=args.duration_hours,
        quote_interval_s=quote_interval,
        run_config=run_config,
        order_size=order_size,
        half_spread_bps=half_spread_bps,
        gamma=gamma,
        kill_switch_max_drawdown_pct=ks_max_dd,
        kill_switch_alert_pct=ks_alert,
        rest_client=rest_client,
        supabase_logger=supa_logger,
        rotation_config=rotation_config_live,
        decision_envelope=decision_envelope,
        transport_selection=transport_selection,
        latency_recorder=latency_recorder,
    )

    # Startup reconciliation (live mode only)
    do_reconciliation = not getattr(args, "skip_reconciliation", False)
    reconciliation_yaml: dict = {}

    if run_config:
        sr_flag = run_config.params.get("startup_reconciliation")
        if sr_flag is False:
            do_reconciliation = False
        elif isinstance(sr_flag, dict):
            reconciliation_yaml = sr_flag
        try:
            config_file_path = run_config.params.get("config_path", "")
            if config_file_path:
                with open(config_file_path) as _cf:
                    _raw = yaml.safe_load(_cf) or {}
                sr_section = _raw.get("startup_reconciliation")
                if isinstance(sr_section, dict):
                    reconciliation_yaml = sr_section
                elif sr_section is False:
                    do_reconciliation = False
        except Exception:
            pass

    if do_reconciliation:
        recon_config = (
            StartupReconciliationConfig.from_dict(reconciliation_yaml)
            if reconciliation_yaml
            else StartupReconciliationConfig(
                max_position_per_side=max_position_per_side,
                min_balance_to_quote=Decimal(str(params.get("min_balance_to_quote", "5"))),
            )
        )

        reconciler = StartupReconciler(
            rest_client=rest_client,
            market_configs=markets,
            config=recon_config,
            supabase_logger=supa_logger,
        )

        recon_result = await reconciler.reconcile()

        if not recon_result.passed:
            print(f"STARTUP RECONCILIATION FAILED: {recon_result.reason}")
            print("Use --skip-reconciliation to bypass (DANGEROUS).")
            sys.exit(1)

        reconciler.apply_to_wallet(prod_wallet, recon_result)
        logger.info("reconciliation.applied",
                     usdc_balance=str(recon_result.usdc_balance),
                     positions=len(recon_result.positions))

    if decision_envelope.mode_allocations.directional_enabled and not transport_selection.directional_live_ok:
        logger.warning(
            "directional.live_disabled",
            reason="selected transport does not satisfy directional gate",
            selected_transport=transport_selection.selected_transport,
        )

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(pipeline.stop(reason=f"signal_{s.name}")),
        )

    await pipeline.start()


async def async_main(args) -> None:
    """Main async entrypoint."""
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    # Load run config
    run_config = None
    if args.config:
        config_file = Path(args.config)
        if not config_file.is_absolute():
            config_file = PROJECT_ROOT / config_file
        run_config = RunConfig.from_yaml(config_file)

        # Override duration from config unless explicitly set via CLI
        if args.duration_hours is None:
            args.duration_hours = run_config.duration_hours
        if args.quote_interval is None:
            qi = run_config.params.get("quote_interval", run_config.params.get("quote_interval_s"))
            if qi:
                args.quote_interval = float(qi)

        run_config.params["config_path"] = str(config_file)
        logger.info("config.loaded", run_id=run_config.run_id,
                     hypothesis=run_config.hypothesis)

    # Apply defaults
    if args.duration_hours is None:
        args.duration_hours = 4.0
    if args.quote_interval is None:
        args.quote_interval = 2.0 if args.mode == "paper" else 5.0

    decision_envelope = _load_decision_envelope(
        args,
        run_config,
        required=(args.mode == "live"),
    )

    if args.mode == "paper":
        await _run_paper(args, run_config, decision_envelope)
    elif args.mode == "live":
        assert decision_envelope is not None
        await _run_live(args, run_config, decision_envelope)
    else:
        print(f"ERROR: Unknown mode '{args.mode}'. Use 'paper' or 'live'.")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified Trading Pipeline")
    parser.add_argument("--mode", type=str, required=True,
                        choices=["paper", "live"],
                        help="Trading mode: paper (simulated) or live (real CLOB)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to run config YAML (e.g., paper/runs/p5-001.yaml)")
    parser.add_argument("--duration-hours", type=float, default=None,
                        help="Duration in hours (overrides config)")
    parser.add_argument("--quote-interval", type=float, default=None,
                        help="Quote cycle interval in seconds")
    parser.add_argument("--skip-reconciliation", action="store_true",
                        help="Skip startup reconciliation (live mode only)")
    parser.add_argument("--decision-envelope", type=str, default=None,
                        help="Path to DecisionEnvelope JSON emitted by Quant")
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
