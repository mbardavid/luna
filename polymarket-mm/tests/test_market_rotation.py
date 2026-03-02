"""Tests for market rotation flow — config, blacklist, and pipeline integration."""

from __future__ import annotations

import json
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from models.market_state import MarketType
from models.position import Position
from runner.config import (
    RotationConfig,
    UnifiedMarketConfig,
    load_rotation_blacklist,
    save_rotation_blacklist,
)

MARKET_ID = "rot-test-market"
TOKEN_YES = "tok-yes-rot"
TOKEN_NO = "tok-no-rot"


class TestRotationConfig:
    """RotationConfig dataclass and YAML loading."""

    def test_defaults_disabled(self) -> None:
        """All features off by default for backward compatibility."""
        cfg = RotationConfig()
        assert cfg.market_rotation is False
        assert cfg.capital_recovery is False

    def test_from_dict_full(self) -> None:
        cfg = RotationConfig.from_dict({
            "market_rotation": True,
            "rotation_cooldown_hours": 2.0,
            "min_market_health_score": 0.5,
            "max_spread_bps": 300,
            "min_fill_rate_pct": 2.0,
            "fill_rate_window_hours": 4.0,
            "max_inventory_skew_pct": 60.0,
            "capital_recovery": True,
            "min_balance_for_recovery": "15",
        })
        assert cfg.market_rotation is True
        assert cfg.rotation_cooldown_hours == 2.0
        assert cfg.min_market_health_score == 0.5
        assert cfg.max_spread_bps == 300
        assert cfg.min_fill_rate_pct == 2.0
        assert cfg.fill_rate_window_hours == 4.0
        assert cfg.max_inventory_skew_pct == 60.0
        assert cfg.capital_recovery is True
        assert cfg.min_balance_for_recovery == Decimal("15")

    def test_from_dict_partial(self) -> None:
        """Partial dict should use defaults for missing keys."""
        cfg = RotationConfig.from_dict({"market_rotation": True})
        assert cfg.market_rotation is True
        assert cfg.rotation_cooldown_hours == 1.0  # default
        assert cfg.capital_recovery is False  # default

    def test_from_dict_empty(self) -> None:
        cfg = RotationConfig.from_dict({})
        assert cfg.market_rotation is False
        assert cfg.capital_recovery is False


class TestRotationBlacklist:
    """Blacklist persistence across restarts."""

    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "blacklist.json"
            blacklist = {"market-a", "market-b"}
            save_rotation_blacklist(path, blacklist)
            loaded = load_rotation_blacklist(path)
            assert loaded == blacklist

    def test_load_nonexistent(self) -> None:
        path = Path("/tmp/nonexistent_blacklist_12345.json")
        result = load_rotation_blacklist(path)
        assert result == set()

    def test_load_corrupted(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json")
            path = Path(f.name)
        result = load_rotation_blacklist(path)
        assert result == set()
        path.unlink()

    def test_persistence_roundtrip(self) -> None:
        """Blacklist should survive save→load cycle exactly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bl.json"
            original = {"m1", "m2", "m3"}
            save_rotation_blacklist(path, original)

            # Verify file content
            with open(path) as f:
                data = json.load(f)
            assert set(data["blacklisted_markets"]) == original

            loaded = load_rotation_blacklist(path)
            assert loaded == original


class TestAutoSelectMarketsBlacklist:
    """auto_select_markets should respect blacklist."""

    @pytest.mark.asyncio
    async def test_blacklist_excludes_markets(self) -> None:
        """Markets in blacklist should be excluded from auto selection."""
        from runner.config import auto_select_markets

        mock_client = AsyncMock()
        mock_client.get_active_markets.return_value = [
            {
                "active": True,
                "closed": False,
                "condition_id": "cond-a",
                "token_id_yes": "ty-a",
                "token_id_no": "tn-a",
                "question": "Market A",
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": False,
            },
            {
                "active": True,
                "closed": False,
                "condition_id": "cond-b",
                "token_id_yes": "ty-b",
                "token_id_no": "tn-b",
                "question": "Market B",
                "tick_size": "0.01",
                "min_order_size": "5",
                "neg_risk": False,
            },
        ]

        # Gamma fallback will fail (no network), so it falls back to REST
        with patch("runner.config._auto_select_via_gamma", side_effect=Exception("no network")):
            result = await auto_select_markets(
                mock_client,
                max_markets=2,
                blacklist={"cond-a"},
            )

        # cond-a should be excluded
        market_ids = {m.condition_id for m in result}
        assert "cond-a" not in market_ids
        assert "cond-b" in market_ids


class TestPipelineRotationConfig:
    """Pipeline accepts RotationConfig and creates monitors."""

    def test_pipeline_without_rotation(self) -> None:
        """Pipeline should work without rotation config (backward compat)."""
        from core.event_bus import EventBus
        from runner.pipeline import UnifiedTradingPipeline

        market_cfg = UnifiedMarketConfig(
            market_id=MARKET_ID,
            condition_id=MARKET_ID,
            token_id_yes=TOKEN_YES,
            token_id_no=TOKEN_NO,
            description="Test",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

        venue = MagicMock()
        venue.mode = "paper"
        wallet = MagicMock()
        wallet.available_balance = Decimal("100")
        wallet.locked_balance = Decimal("0")
        wallet.initial_balance = Decimal("100")
        wallet.total_equity.return_value = Decimal("100")
        wallet.get_position.return_value = None
        wallet.wallet_snapshot.return_value = {}
        wallet.total_fees = Decimal("0")
        event_bus = EventBus()

        # No rotation_config → should not crash
        pipeline = UnifiedTradingPipeline(
            market_configs=[market_cfg],
            venue=venue,
            wallet=wallet,
            event_bus=event_bus,
        )
        assert pipeline._health_monitor is None
        assert pipeline._capital_recovery is None

    def test_pipeline_with_rotation_enabled(self) -> None:
        """Pipeline with rotation enabled should create monitors."""
        from core.event_bus import EventBus
        from runner.pipeline import UnifiedTradingPipeline

        market_cfg = UnifiedMarketConfig(
            market_id=MARKET_ID,
            condition_id=MARKET_ID,
            token_id_yes=TOKEN_YES,
            token_id_no=TOKEN_NO,
            description="Test",
            market_type=MarketType.OTHER,
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("5"),
            neg_risk=False,
        )

        venue = MagicMock()
        venue.mode = "paper"
        wallet = MagicMock()
        wallet.available_balance = Decimal("100")
        wallet.locked_balance = Decimal("0")
        wallet.initial_balance = Decimal("100")
        wallet.total_equity.return_value = Decimal("100")
        wallet.get_position.return_value = None
        wallet.wallet_snapshot.return_value = {}
        wallet.total_fees = Decimal("0")
        event_bus = EventBus()

        rot_config = RotationConfig(
            market_rotation=True,
            capital_recovery=True,
            min_balance_for_recovery=Decimal("5"),
        )

        pipeline = UnifiedTradingPipeline(
            market_configs=[market_cfg],
            venue=venue,
            wallet=wallet,
            event_bus=event_bus,
            rotation_config=rot_config,
        )

        assert pipeline._health_monitor is not None
        assert pipeline._capital_recovery is not None
