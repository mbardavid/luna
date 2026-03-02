"""Tests for UnwindManager and related components.

Tests use mocked clients — no real orders or on-chain transactions.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from execution.ctf_merge import CTFMerger, MergeResult
from execution.unwind import (
    SellResult,
    UnwindConfig,
    UnwindManager,
    UnwindReport,
    UnwindStrategy,
)
from models.position import Position


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def mock_rest_client():
    """Mock CLOBRestClient with common methods."""
    client = AsyncMock()
    client.cancel_all_orders = AsyncMock(return_value=True)
    client.get_midpoint = AsyncMock(return_value=Decimal("0.50"))
    client.get_price = AsyncMock(return_value=Decimal("0.50"))
    client.create_and_post_order = AsyncMock(return_value={"orderID": "test-123"})
    client.get_open_orders = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mock_ctf_merger():
    """Mock CTFMerger."""
    merger = MagicMock(spec=CTFMerger)
    merger.calculate_mergeable = CTFMerger.calculate_mergeable.__get__(merger)
    merger.merge_positions = AsyncMock(return_value=MergeResult(
        condition_id="test-cond",
        amount_merged=Decimal("30"),
        usdc_received=Decimal("30"),
        gas_cost_usd=Decimal("0.01"),
        tx_hash="0xabc123",
        success=True,
    ))
    return merger


@pytest.fixture
def default_config():
    return UnwindConfig(
        max_time_seconds=60.0,
        dust_threshold_shares=Decimal("5"),
        merge_enabled=True,
    )


@pytest.fixture
def yes_position():
    """Position with only YES shares."""
    return Position(
        market_id="market-001",
        token_id_yes="tok-yes-001",
        token_id_no="tok-no-001",
        qty_yes=Decimal("50"),
        qty_no=Decimal("0"),
    )


@pytest.fixture
def no_position():
    """Position with only NO shares."""
    return Position(
        market_id="market-002",
        token_id_yes="tok-yes-002",
        token_id_no="tok-no-002",
        qty_yes=Decimal("0"),
        qty_no=Decimal("40"),
    )


@pytest.fixture
def both_sides_position():
    """Position with both YES and NO shares."""
    return Position(
        market_id="market-003",
        token_id_yes="tok-yes-003",
        token_id_no="tok-no-003",
        qty_yes=Decimal("50"),
        qty_no=Decimal("30"),
    )


@pytest.fixture
def dust_position():
    """Position below dust threshold."""
    return Position(
        market_id="market-004",
        token_id_yes="tok-yes-004",
        token_id_no="tok-no-004",
        qty_yes=Decimal("3"),
        qty_no=Decimal("2"),
    )


# ── Test: Unwind YES Position ────────────────────────────────────────


class TestUnwindYesPosition:
    @pytest.mark.asyncio
    async def test_unwind_yes_position(self, mock_rest_client, default_config):
        """Sell YES shares at aggressive price."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_shutdown")

        # Should have cancelled orders
        mock_rest_client.cancel_all_orders.assert_called_once()

        # Should have attempted to sell YES
        assert len(report.sells) > 0
        assert report.sells[0]["token_side"] == "YES"
        assert Decimal(report.sells[0]["shares_to_sell"]) == Decimal("50")

    @pytest.mark.asyncio
    async def test_unwind_yes_position_proceeds(self, mock_rest_client, default_config):
        """Verify proceeds are calculated on successful sell."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test")
        assert report.total_proceeds > Decimal("0")


# ── Test: Unwind NO Position ────────────────────────────────────────


class TestUnwindNoPosition:
    @pytest.mark.asyncio
    async def test_unwind_no_position(self, mock_rest_client, default_config):
        """Sell NO shares at aggressive price."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-002": Position(
                market_id="market-002",
                token_id_yes="tok-yes-002",
                token_id_no="tok-no-002",
                qty_yes=Decimal("0"),
                qty_no=Decimal("40"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_shutdown")

        assert len(report.sells) > 0
        assert report.sells[0]["token_side"] == "NO"
        assert Decimal(report.sells[0]["shares_to_sell"]) == Decimal("40")


# ── Test: Unwind Both Sides with Merge ──────────────────────────────


class TestUnwindBothSidesMerges:
    @pytest.mark.asyncio
    async def test_unwind_both_sides_merges(
        self, mock_rest_client, mock_ctf_merger, default_config
    ):
        """When holding YES+NO, merge pairs first, then sell remainder."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            ctf_merger=mock_ctf_merger,
            config=default_config,
        )

        # 50 YES + 30 NO → merge 30, sell 20 YES
        positions = {
            "market-003": Position(
                market_id="market-003",
                token_id_yes="tok-yes-003",
                token_id_no="tok-no-003",
                qty_yes=Decimal("50"),
                qty_no=Decimal("30"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_merge")

        # Should have merged 30 pairs
        assert len(report.merges) > 0
        assert report.total_merged_usdc == Decimal("30")

        # Should have sold remaining 20 YES
        assert len(report.sells) > 0
        yes_sell = [s for s in report.sells if s["token_side"] == "YES"]
        assert len(yes_sell) > 0
        assert Decimal(yes_sell[0]["shares_to_sell"]) == Decimal("20")

    @pytest.mark.asyncio
    async def test_merge_disabled(self, mock_rest_client, mock_ctf_merger):
        """When merge_enabled=False, skip merge and sell both sides."""
        config = UnwindConfig(merge_enabled=False)
        manager = UnwindManager(
            rest_client=mock_rest_client,
            ctf_merger=mock_ctf_merger,
            config=config,
        )

        positions = {
            "market-003": Position(
                market_id="market-003",
                token_id_yes="tok-yes-003",
                token_id_no="tok-no-003",
                qty_yes=Decimal("50"),
                qty_no=Decimal("30"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test")

        assert len(report.merges) == 0
        # Should sell both YES and NO
        assert len(report.sells) == 2


# ── Test: Dust Skipped ──────────────────────────────────────────────


class TestUnwindDustSkipped:
    @pytest.mark.asyncio
    async def test_unwind_dust_skipped(self, mock_rest_client, default_config):
        """Positions below dust threshold should be logged but not sold."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-004": Position(
                market_id="market-004",
                token_id_yes="tok-yes-004",
                token_id_no="tok-no-004",
                qty_yes=Decimal("3"),
                qty_no=Decimal("2"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_dust")

        assert len(report.dust_skipped) == 2
        assert len(report.sells) == 0
        assert report.success is True  # No orphans = success

    @pytest.mark.asyncio
    async def test_dust_threshold_boundary(self, mock_rest_client):
        """Position exactly at threshold should be attempted."""
        config = UnwindConfig(dust_threshold_shares=Decimal("5"))
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-005": Position(
                market_id="market-005",
                token_id_yes="tok-yes-005",
                token_id_no="tok-no-005",
                qty_yes=Decimal("5"),  # exactly at threshold
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test")
        # 5 >= 5, so it should attempt to sell (not dust)
        assert len(report.sells) > 0
        assert len(report.dust_skipped) == 0


# ── Test: Unwind Timeout ────────────────────────────────────────────


class TestUnwindTimeout:
    @pytest.mark.asyncio
    async def test_unwind_timeout(self, mock_rest_client):
        """Unwind exceeding max_time should exit with timeout flag."""
        config = UnwindConfig(max_time_seconds=0.1)  # Very short timeout

        # Make the REST client slow
        async def slow_cancel():
            await asyncio.sleep(0.5)
            return True

        mock_rest_client.cancel_all_orders = slow_cancel

        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_timeout")
        assert report.timed_out is True


# ── Test: Progressive Pricing ───────────────────────────────────────


class TestUnwindProgressivePricing:
    @pytest.mark.asyncio
    async def test_unwind_progressive_pricing(self, mock_rest_client):
        """Multiple attempts at progressively worse prices."""
        # First two attempts fail, third succeeds
        call_count = 0

        async def mock_order(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"error": "insufficient_liquidity"}
            return {"orderID": "filled-123"}

        mock_rest_client.create_and_post_order = mock_order

        config = UnwindConfig(
            max_time_seconds=30.0,
            progressive_pricing=[Decimal("0"), Decimal("2"), Decimal("5")],
        )
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_pricing")

        # Should have attempted multiple times
        assert call_count >= 2
        assert len(report.sells) > 0
        sell = report.sells[0]
        assert sell["attempts"] >= 2

    @pytest.mark.asyncio
    async def test_pricing_offsets_applied(self, mock_rest_client):
        """Verify that pricing offsets are actually different."""
        prices_seen = []

        async def capture_price(*args, **kwargs):
            price = kwargs.get("price")
            if price is not None:
                prices_seen.append(price)
            return {"error": "no_fill"}  # Always fail to see all attempts

        mock_rest_client.create_and_post_order = capture_price

        config = UnwindConfig(
            max_time_seconds=10.0,
            progressive_pricing=[Decimal("0"), Decimal("2"), Decimal("5")],
        )
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test")
        # Should have attempted at multiple price levels
        assert len(prices_seen) >= 2
        # Prices should be different (each attempt worse)
        if len(prices_seen) >= 2:
            assert prices_seen[0] != prices_seen[1]


# ── Test: Kill Switch Sweep ─────────────────────────────────────────


class TestUnwindKillSwitchSweep:
    @pytest.mark.asyncio
    async def test_unwind_kill_switch_sweep(self, mock_rest_client):
        """Kill switch uses sweep strategy (most aggressive offset)."""
        prices_seen = []

        async def capture_price(*args, **kwargs):
            price = kwargs.get("price") or args[1] if len(args) > 1 else None
            if price is not None:
                prices_seen.append(price)
            return {"orderID": "sweep-filled"}

        mock_rest_client.create_and_post_order = capture_price

        config = UnwindConfig()
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(
            positions,
            reason="KILL_SWITCH",
            strategy=UnwindStrategy.SWEEP,
        )

        assert report.strategy == "sweep"
        # Sweep should only attempt once at 5% offset
        assert len(report.sells) > 0

    @pytest.mark.asyncio
    async def test_sweep_uses_single_attempt(self, mock_rest_client):
        """Sweep strategy should use single aggressive attempt."""
        attempts = 0

        async def count_attempts(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            return {"orderID": "sweep-ok"}

        mock_rest_client.create_and_post_order = count_attempts

        config = UnwindConfig()
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        await manager.unwind_all(
            positions, reason="kill_switch", strategy=UnwindStrategy.SWEEP
        )
        # Sweep should succeed in 1 attempt
        assert attempts == 1


# ── Test: Crash Recovery ────────────────────────────────────────────


class TestUnwindCrashRecovery:
    @pytest.mark.asyncio
    async def test_unwind_crash_recovery_hold(self, mock_rest_client):
        """Crash recovery with HOLD strategy should not sell anything."""
        config = UnwindConfig(strategy=UnwindStrategy.HOLD)
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="crash_recovery")

        assert report.strategy == "hold"
        assert len(report.sells) == 0
        assert len(report.merges) == 0
        mock_rest_client.cancel_all_orders.assert_not_called()

    @pytest.mark.asyncio
    async def test_crash_recovery_with_override(self, mock_rest_client):
        """Can override crash recovery to aggressive."""
        config = UnwindConfig(strategy=UnwindStrategy.HOLD)
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(
            positions,
            reason="crash_recovery",
            strategy=UnwindStrategy.AGGRESSIVE,
        )

        assert report.strategy == "aggressive"
        assert len(report.sells) > 0


# ── Test: CTF Merge ─────────────────────────────────────────────────


class TestCTFMerge:
    @pytest.mark.asyncio
    async def test_ctf_merge(self):
        """Merge YES+NO pairs returns correct USDC amount."""
        mock_adapter = AsyncMock()
        mock_adapter.merge_positions = AsyncMock(return_value=MagicMock(
            status=MagicMock(value="CONFIRMED"),
            cost_usd=Decimal("0.01"),
            tx_hash="0xmerge123",
            error=None,
        ))

        merger = CTFMerger(ctf_adapter=mock_adapter)

        result = await merger.merge_positions(
            condition_id="0xtest_condition",
            amount=Decimal("30"),
        )

        assert result.success is True
        assert result.amount_merged == Decimal("30")
        assert result.usdc_received == Decimal("30")
        assert result.tx_hash == "0xmerge123"
        assert result.gas_cost_usd == Decimal("0.01")

    @pytest.mark.asyncio
    async def test_ctf_merge_no_adapter(self):
        """Merge without adapter returns error."""
        merger = CTFMerger(ctf_adapter=None)

        result = await merger.merge_positions(
            condition_id="0xtest",
            amount=Decimal("30"),
        )

        assert result.success is False
        assert result.error == "CTF adapter not configured"

    @pytest.mark.asyncio
    async def test_ctf_merge_zero_amount(self):
        """Merge with zero amount returns error."""
        merger = CTFMerger(ctf_adapter=AsyncMock())

        result = await merger.merge_positions(
            condition_id="0xtest",
            amount=Decimal("0"),
        )

        assert result.success is False

    def test_calculate_mergeable(self):
        """Calculate mergeable pairs from YES+NO quantities."""
        merger = CTFMerger()

        assert merger.calculate_mergeable(Decimal("50"), Decimal("30")) == Decimal("30")
        assert merger.calculate_mergeable(Decimal("30"), Decimal("50")) == Decimal("30")
        assert merger.calculate_mergeable(Decimal("0"), Decimal("50")) == Decimal("0")
        assert merger.calculate_mergeable(Decimal("50"), Decimal("0")) == Decimal("0")
        assert merger.calculate_mergeable(Decimal("10.5"), Decimal("8.3")) == Decimal("8")


# ── Test: Position Manager CLI ──────────────────────────────────────


class TestPositionManagerCLI:
    def test_cli_parser_status(self):
        """CLI parser handles 'status' command."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_cli_parser_unwind(self):
        """CLI parser handles 'unwind' command with strategy."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["unwind", "--strategy", "aggressive"])
        assert args.command == "unwind"
        assert args.strategy == "aggressive"

    def test_cli_parser_unwind_sweep(self):
        """CLI parser handles sweep strategy."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["unwind", "--strategy", "sweep"])
        assert args.strategy == "sweep"

    def test_cli_parser_merge(self):
        """CLI parser handles 'merge' command."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["merge", "--market", "0xabc123"])
        assert args.command == "merge"
        assert args.market == "0xabc123"

    def test_cli_parser_dust(self):
        """CLI parser handles 'dust' command."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["dust", "--threshold", "10"])
        assert args.command == "dust"
        assert args.threshold == 10.0

    def test_cli_parser_defaults(self):
        """CLI parser has sensible defaults."""
        from cli.position_manager import build_parser

        parser = build_parser()
        args = parser.parse_args(["unwind"])
        assert args.strategy == "aggressive"
        assert args.timeout == 60.0
        assert args.dust_threshold == 5.0


# ── Test: Unwind Report Saved ───────────────────────────────────────


class TestUnwindReportSaved:
    @pytest.mark.asyncio
    async def test_unwind_report_saved(self, mock_rest_client, default_config):
        """JSON report should be written to disk."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test_save")

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False, mode="w"
        ) as f:
            tmp_path = Path(f.name)

        report.save(tmp_path)

        assert tmp_path.exists()
        with open(tmp_path) as f:
            data = json.load(f)

        assert data["reason"] == "test_save"
        assert "started_at" in data
        assert "finished_at" in data
        assert "sells" in data
        assert "dust_skipped" in data
        assert "total_proceeds" in data

        # Cleanup
        tmp_path.unlink()

    def test_report_to_dict(self):
        """UnwindReport.to_dict includes all fields."""
        report = UnwindReport(reason="test")
        report.add_dust("m1", "YES", Decimal("3"))
        report.finalize()

        d = report.to_dict()
        assert d["reason"] == "test"
        assert len(d["dust_skipped"]) == 1
        assert d["success"] is True
        assert d["timed_out"] is False
        assert "duration_seconds" in d

    def test_report_tracks_orphaned(self):
        """Report tracks orphaned positions."""
        report = UnwindReport(reason="test")
        report.add_sell(SellResult(
            market_id="m1",
            token_side="YES",
            shares_to_sell=Decimal("50"),
            shares_sold=Decimal("0"),
            avg_price=Decimal("0"),
            proceeds=Decimal("0"),
            attempts=3,
            success=False,
            error="Could not fill",
        ))
        report.finalize()

        assert len(report.orphaned) == 1
        assert report.success is False


# ── Test: Cancel Orders Before Unwind ───────────────────────────────


class TestCancelOrdersBeforeUnwind:
    @pytest.mark.asyncio
    async def test_cancel_orders_before_unwind(self, mock_rest_client, default_config):
        """All open orders should be cancelled before selling positions."""
        call_order = []

        original_cancel = mock_rest_client.cancel_all_orders

        async def track_cancel():
            call_order.append("cancel")
            return await original_cancel()

        async def track_sell(*args, **kwargs):
            call_order.append("sell")
            return {"orderID": "sold"}

        mock_rest_client.cancel_all_orders = track_cancel
        mock_rest_client.create_and_post_order = track_sell

        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        await manager.unwind_all(positions, reason="test_cancel_first")

        # Cancel should happen before any sell
        assert "cancel" in call_order
        assert "sell" in call_order
        cancel_idx = call_order.index("cancel")
        sell_idx = call_order.index("sell")
        assert cancel_idx < sell_idx, "Orders should be cancelled before selling"

    @pytest.mark.asyncio
    async def test_cancel_failure_continues(self, mock_rest_client, default_config):
        """If cancel fails, unwind should still attempt to sell."""
        mock_rest_client.cancel_all_orders = AsyncMock(
            side_effect=Exception("cancel failed")
        )

        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "market-001": Position(
                market_id="market-001",
                token_id_yes="tok-yes-001",
                token_id_no="tok-no-001",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="test")
        # Should still attempt sells even if cancel failed
        assert len(report.sells) > 0


# ── Test: UnwindConfig ──────────────────────────────────────────────


class TestUnwindConfig:
    def test_config_from_dict(self):
        """UnwindConfig.from_dict parses YAML-like dict."""
        d = {
            "enabled": True,
            "max_time_seconds": 45,
            "strategies": {
                "normal_shutdown": "aggressive",
                "kill_switch": "sweep",
                "crash_recovery": "hold",
            },
            "dust_threshold_shares": 5,
            "merge_enabled": True,
            "progressive_pricing": {
                "attempt_1_offset_pct": 0,
                "attempt_2_offset_pct": 2,
                "attempt_3_offset_pct": 5,
            },
            "alert_on_orphan": True,
        }

        config = UnwindConfig.from_dict(d)
        assert config.enabled is True
        assert config.max_time_seconds == 45.0
        assert config.strategy == UnwindStrategy.AGGRESSIVE
        assert config.dust_threshold_shares == Decimal("5")
        assert config.merge_enabled is True
        assert len(config.progressive_pricing) == 3
        assert config.progressive_pricing[0] == Decimal("0")
        assert config.progressive_pricing[1] == Decimal("2")
        assert config.progressive_pricing[2] == Decimal("5")

    def test_config_defaults(self):
        """Default UnwindConfig has sensible values."""
        config = UnwindConfig()
        assert config.enabled is True
        assert config.max_time_seconds == 60.0
        assert config.strategy == UnwindStrategy.AGGRESSIVE
        assert config.dust_threshold_shares == Decimal("5")
        assert config.merge_enabled is True

    def test_config_from_empty_dict(self):
        """from_dict with empty dict uses defaults."""
        config = UnwindConfig.from_dict({})
        assert config.enabled is True
        assert config.max_time_seconds == 60.0


# ── Test: Hold Strategy ─────────────────────────────────────────────


class TestHoldStrategy:
    @pytest.mark.asyncio
    async def test_hold_does_nothing(self, mock_rest_client):
        """Hold strategy should not cancel orders or sell anything."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=UnwindConfig(strategy=UnwindStrategy.HOLD),
        )

        positions = {
            "m1": Position(
                market_id="m1",
                token_id_yes="ty1",
                token_id_no="tn1",
                qty_yes=Decimal("100"),
                qty_no=Decimal("50"),
            ),
        }

        report = await manager.unwind_all(positions, reason="hold_test")
        assert report.strategy == "hold"
        assert len(report.sells) == 0
        assert len(report.merges) == 0
        mock_rest_client.cancel_all_orders.assert_not_called()


# ── Test: Disabled Unwind ───────────────────────────────────────────


class TestDisabledUnwind:
    @pytest.mark.asyncio
    async def test_disabled_unwind(self, mock_rest_client):
        """When unwind is disabled, nothing happens."""
        config = UnwindConfig(enabled=False)
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=config,
        )

        positions = {
            "m1": Position(
                market_id="m1",
                token_id_yes="ty1",
                token_id_no="tn1",
                qty_yes=Decimal("100"),
            ),
        }

        report = await manager.unwind_all(positions, reason="disabled_test")
        assert len(report.sells) == 0
        mock_rest_client.cancel_all_orders.assert_not_called()


# ── Test: Empty Positions ───────────────────────────────────────────


class TestEmptyPositions:
    @pytest.mark.asyncio
    async def test_empty_positions(self, mock_rest_client, default_config):
        """Unwind with no positions should succeed cleanly."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        report = await manager.unwind_all({}, reason="empty_test")
        assert report.success is True
        assert len(report.sells) == 0
        assert len(report.dust_skipped) == 0

    @pytest.mark.asyncio
    async def test_zero_qty_positions(self, mock_rest_client, default_config):
        """Positions with zero quantities should be skipped."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "m1": Position(
                market_id="m1",
                token_id_yes="ty1",
                token_id_no="tn1",
                qty_yes=Decimal("0"),
                qty_no=Decimal("0"),
            ),
        }

        report = await manager.unwind_all(positions, reason="zero_test")
        assert report.success is True
        assert len(report.sells) == 0


# ── Test: Multiple Markets ──────────────────────────────────────────


class TestMultipleMarkets:
    @pytest.mark.asyncio
    async def test_unwind_multiple_markets(self, mock_rest_client, default_config):
        """Unwind across multiple markets."""
        manager = UnwindManager(
            rest_client=mock_rest_client,
            config=default_config,
        )

        positions = {
            "m1": Position(
                market_id="m1",
                token_id_yes="ty1",
                token_id_no="tn1",
                qty_yes=Decimal("50"),
                qty_no=Decimal("0"),
            ),
            "m2": Position(
                market_id="m2",
                token_id_yes="ty2",
                token_id_no="tn2",
                qty_yes=Decimal("0"),
                qty_no=Decimal("30"),
            ),
        }

        report = await manager.unwind_all(positions, reason="multi_test")
        # Should have sold both positions
        assert len(report.sells) == 2
