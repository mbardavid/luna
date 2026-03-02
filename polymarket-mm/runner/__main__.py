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
from decimal import Decimal
from pathlib import Path

import structlog
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.event_bus import EventBus
from paper.paper_runner import RunConfig
from runner.config import RotationConfig, UnifiedMarketConfig, auto_select_markets, load_markets
from runner.pipeline import UnifiedTradingPipeline

logger = structlog.get_logger("runner.__main__")


async def _run_paper(args, run_config: RunConfig | None) -> None:
    """Paper mode: simulated venue with PaperVenueAdapter + PaperWalletAdapter."""
    from paper.paper_venue import FeeConfig, MarketSimConfig, PaperVenue
    from runner.paper_venue_adapter import PaperVenueAdapter
    from runner.paper_wallet import PaperWalletAdapter

    # Load markets from YAML
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
    order_size = Decimal(str(params.get("order_size",
                                         params.get("default_order_size", "50"))))
    half_spread_bps = int(params.get("half_spread_bps",
                                      params.get("default_half_spread_bps", 50)))
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
    )

    # Handle signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(pipeline.stop()))

    await pipeline.start()


async def _run_live(args, run_config: RunConfig | None) -> None:
    """Live mode: real CLOB via LiveVenueAdapter + ProductionWalletAdapter."""
    from data.rest_client import CLOBRestClient
    from execution.live_execution import LiveExecution
    from paper.production_runner import ProductionWallet
    from paper.startup_reconciler import StartupReconciler, StartupReconciliationConfig
    from runner.live_venue_adapter import LiveVenueAdapter
    from runner.production_wallet import ProductionWalletAdapter

    # SOCKS5 proxy setup (same as production_runner)
    try:
        import httpx as _httpx
        import py_clob_client.http_helpers.helpers as _clob_helpers
        _PROXY_URL = os.environ.get("POLYMARKET_PROXY", "socks5://127.0.0.1:9050")
        _clob_helpers._http_client = _httpx.Client(
            http2=True, proxy=_PROXY_URL, timeout=30.0
        )
    except Exception:
        pass

    params = run_config.params if run_config else {}
    initial_balance = run_config.initial_balance if run_config else Decimal("25")
    order_size = Decimal(str(params.get("default_order_size", "5")))
    half_spread_bps = int(params.get("default_half_spread_bps", 50))
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
        rate_limit_rps=5.0,
    )
    await rest_client.connect()

    # Get markets from config or auto-select
    market_ids = params.get("markets", []) if run_config else []
    if market_ids:
        markets: list[UnifiedMarketConfig] = []
        for mid in market_ids:
            try:
                from models.market_state import MarketType
                info = await rest_client.get_market_info(mid)
                markets.append(UnifiedMarketConfig(
                    market_id=info["condition_id"],
                    condition_id=info["condition_id"],
                    token_id_yes=info["token_id_yes"],
                    token_id_no=info["token_id_no"],
                    description=info.get("question", info["condition_id"])[:80],
                    market_type=MarketType.OTHER,
                    tick_size=Decimal(str(info.get("tick_size", "0.01"))),
                    min_order_size=Decimal(str(info.get("min_order_size", "5"))),
                    neg_risk=info.get("neg_risk", False),
                ))
            except Exception as e:
                logger.warning("market_fetch_failed", market_id=mid, error=str(e))
    else:
        markets = await auto_select_markets(rest_client, max_markets=1)

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

    if args.mode == "paper":
        await _run_paper(args, run_config)
    elif args.mode == "live":
        await _run_live(args, run_config)
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
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
