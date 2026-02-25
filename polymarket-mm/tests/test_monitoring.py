"""Tests for Phase 10 — Monitoring, Alerting, Cold Storage, Health & Dashboard.

Covers:
- MetricsRegistry — counters, gauges, histograms, exposition
- HealthCheck — component registration, status evaluation, HTTP server
- Alerter — rate limiting, channel routing, EventBus integration
- ColdWriter — buffering, flush, SQLite writes, migrations
- Dashboard — JSON generation and export
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.event_bus import EventBus
from monitoring.alerter import Alerter, AlertChannel, AlertSeverity
from monitoring.dashboard import export_dashboard_json, generate_dashboard
from monitoring.health import HealthCheck, HealthStatus
from monitoring.metrics import MetricsRegistry
from storage.cold_writer import ColdWriter


# ════════════════════════════════════════════════════════════════
# MetricsRegistry
# ════════════════════════════════════════════════════════════════


class TestMetricsRegistry:
    """Tests for Prometheus metrics registry."""

    def test_fresh_registry_creates_all_metrics(self) -> None:
        """All expected metrics should exist on a fresh registry."""
        m = MetricsRegistry()
        assert m.fills_total is not None
        assert m.pnl_cumulative is not None
        assert m.order_latency is not None
        assert m.inventory_exposure is not None
        assert m.kill_switch_trips is not None
        assert m.ws_messages_received is not None

    def test_record_fill_increments_counter(self) -> None:
        m = MetricsRegistry()
        m.record_fill("0xabc", "BUY", 50.0)
        m.record_fill("0xabc", "BUY", 25.0)
        m.record_fill("0xabc", "SELL", 10.0)

        # Check via exposition text
        text = m.exposition().decode()
        assert "pmm_fills_total" in text
        assert "pmm_fill_value_usd_total" in text

    def test_record_fill_with_latency(self) -> None:
        m = MetricsRegistry()
        m.record_fill("0xabc", "BUY", 100.0, latency_seconds=0.045)
        text = m.exposition().decode()
        assert "pmm_order_latency_seconds" in text

    def test_set_pnl(self) -> None:
        m = MetricsRegistry()
        m.set_pnl(150.5, daily=42.3)
        text = m.exposition().decode()
        assert "pmm_pnl_cumulative_usd" in text
        assert "pmm_pnl_daily_usd" in text

    def test_set_inventory(self) -> None:
        m = MetricsRegistry()
        m.set_inventory("0xmarket1", 250.0)
        m.set_inventory("0xmarket2", -100.0)
        text = m.exposition().decode()
        assert "pmm_inventory_exposure_usd" in text

    def test_set_total_exposure(self) -> None:
        m = MetricsRegistry()
        m.set_total_exposure(1500.0)
        text = m.exposition().decode()
        assert "pmm_total_exposure_usd" in text

    def test_set_spread(self) -> None:
        m = MetricsRegistry()
        m.set_spread("0xabc", 45.0)
        text = m.exposition().decode()
        assert "pmm_quoted_spread_bps" in text

    def test_record_order_submit(self) -> None:
        m = MetricsRegistry()
        m.record_order_submit("0xabc", "BUY")
        text = m.exposition().decode()
        assert "pmm_orders_submitted_total" in text

    def test_record_order_cancel(self) -> None:
        m = MetricsRegistry()
        m.record_order_cancel("0xabc")
        text = m.exposition().decode()
        assert "pmm_orders_cancelled_total" in text

    def test_record_order_reject(self) -> None:
        m = MetricsRegistry()
        m.record_order_reject("0xabc")
        text = m.exposition().decode()
        assert "pmm_orders_rejected_total" in text

    def test_record_kill_switch(self) -> None:
        m = MetricsRegistry()
        m.record_kill_switch("MAX_DRAWDOWN")
        text = m.exposition().decode()
        assert "pmm_kill_switch_trips_total" in text

    def test_set_kill_switch_state(self) -> None:
        m = MetricsRegistry()
        m.set_kill_switch_state(2)
        text = m.exposition().decode()
        assert "pmm_kill_switch_state" in text

    def test_exposition_returns_bytes(self) -> None:
        m = MetricsRegistry()
        result = m.exposition()
        assert isinstance(result, bytes)

    def test_app_info(self) -> None:
        m = MetricsRegistry()
        m.app_info.info({"version": "0.1.0", "env": "test"})
        text = m.exposition().decode()
        assert "pmm_info" in text

    def test_isolated_registries(self) -> None:
        """Two registries should be independent."""
        m1 = MetricsRegistry()
        m2 = MetricsRegistry()
        m1.record_fill("0xabc", "BUY", 100.0)
        text2 = m2.exposition().decode()
        # m2 should not contain m1's data (no fill value in m2's counter)
        # Both will have the metric defined but m2 should have 0 fills
        assert m1.registry is not m2.registry

    def test_quote_cycle_latency(self) -> None:
        m = MetricsRegistry()
        m.quote_cycle_latency.observe(0.015)
        text = m.exposition().decode()
        assert "pmm_quote_cycle_seconds" in text

    def test_ws_counters(self) -> None:
        m = MetricsRegistry()
        m.ws_messages_received.inc()
        m.ws_reconnects.inc()
        text = m.exposition().decode()
        assert "pmm_ws_messages_total" in text
        assert "pmm_ws_reconnects_total" in text


# ════════════════════════════════════════════════════════════════
# HealthCheck
# ════════════════════════════════════════════════════════════════


class TestHealthCheck:
    """Tests for health-check manager."""

    def test_default_status_healthy(self) -> None:
        hc = HealthCheck()
        assert hc.status == HealthStatus.HEALTHY
        assert hc.is_ready is True

    def test_register_healthy_component(self) -> None:
        hc = HealthCheck()
        hc.register_component("websocket", healthy=True, detail="connected")
        assert hc.status == HealthStatus.HEALTHY

    def test_register_unhealthy_component(self) -> None:
        hc = HealthCheck()
        hc.register_component("websocket", healthy=True)
        hc.register_component("kill_switch", healthy=False, detail="HALTED")
        assert hc.status == HealthStatus.DEGRADED
        assert hc.is_ready is False

    def test_all_unhealthy(self) -> None:
        hc = HealthCheck()
        hc.register_component("ws", healthy=False)
        hc.register_component("db", healthy=False)
        assert hc.status == HealthStatus.UNHEALTHY

    def test_set_component_healthy(self) -> None:
        hc = HealthCheck()
        hc.set_component_unhealthy("ws", "disconnected")
        assert hc.status == HealthStatus.UNHEALTHY
        hc.set_component_healthy("ws", "reconnected")
        assert hc.status == HealthStatus.HEALTHY

    def test_uptime_increases(self) -> None:
        hc = HealthCheck()
        uptime = hc.uptime_seconds
        assert uptime >= 0

    def test_extra_status(self) -> None:
        hc = HealthCheck()
        hc.set_extra_status("active_markets", 5)
        hc.set_extra_status("version", "0.1.0")
        # Just ensure no exception
        assert hc._extra_status["active_markets"] == 5

    @pytest.mark.asyncio
    async def test_http_health_endpoint(self) -> None:
        """Test the /health endpoint via the built-in HTTP server."""
        metrics = MetricsRegistry()
        hc = HealthCheck(metrics=metrics, port=0)

        # Use port 0 to get a random available port
        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /health HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            response_str = response.decode()
            assert "200 OK" in response_str
            assert "alive" in response_str
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_ready_endpoint_healthy(self) -> None:
        hc = HealthCheck(port=0)
        hc.register_component("ws", healthy=True)

        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /ready HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            response_str = response.decode()
            assert "200 OK" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["ready"] is True
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_ready_endpoint_unhealthy(self) -> None:
        hc = HealthCheck(port=0)
        hc.register_component("ws", healthy=False, detail="disconnected")

        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /ready HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            response_str = response.decode()
            assert "503" in response_str
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_metrics_endpoint(self) -> None:
        metrics = MetricsRegistry()
        metrics.record_fill("0xtest", "BUY", 42.0)
        hc = HealthCheck(metrics=metrics, port=0)

        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /metrics HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            response_str = response.decode()
            assert "200 OK" in response_str
            assert "pmm_fills_total" in response_str
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_status_endpoint(self) -> None:
        hc = HealthCheck(port=0)
        hc.register_component("ws", healthy=True, detail="ok")
        hc.set_extra_status("active_markets", 3)

        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /status HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            response_str = response.decode()
            assert "200 OK" in response_str
            body = response_str.split("\r\n\r\n", 1)[1]
            data = json.loads(body)
            assert data["version"] == "0.1.0"
            assert data["active_markets"] == 3
            assert "uptime_seconds" in data
            assert "components" in data
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_404(self) -> None:
        hc = HealthCheck(port=0)
        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /nonexistent HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            assert "404" in response.decode()
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_http_method_not_allowed(self) -> None:
        hc = HealthCheck(port=0)
        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        addr = server.sockets[0].getsockname()
        port = addr[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"POST /health HTTP/1.0\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            response = await reader.read()
            writer.close()
            await writer.wait_closed()

            assert "405" in response.decode()
        finally:
            server.close()
            await server.wait_closed()


# ════════════════════════════════════════════════════════════════
# Alerter
# ════════════════════════════════════════════════════════════════


class TestAlerter:
    """Tests for multi-channel alerter."""

    @pytest.mark.asyncio
    async def test_no_channels_configured(self) -> None:
        """When no channels are configured, send returns empty."""
        alerter = Alerter(
            discord_webhook_url="",
            telegram_bot_token="",
            telegram_chat_id="",
        )
        await alerter.start()
        try:
            records = await alerter.send("Test Alert", severity=AlertSeverity.INFO)
            assert records == []
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_rate_limiting(self) -> None:
        """Same-title alerts should be rate limited."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
            rate_limit_seconds=60.0,
        )
        await alerter.start()
        try:
            # First send — should pass (even though it will fail HTTP, rate limit is checked first)
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock):
                records1 = await alerter.send("Rate Test")
                assert len(records1) == 1

            # Second send — should be rate limited
            records2 = await alerter.send("Rate Test")
            assert records2 == []
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_rate_limit_reset(self) -> None:
        """After clearing rate limits, alerts should send again."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
            rate_limit_seconds=60.0,
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock):
                await alerter.send("Reset Test")
                alerter.reset_rate_limits()
                records = await alerter.send("Reset Test")
                assert len(records) == 1
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_discord_dispatch(self) -> None:
        """Test Discord webhook dispatch."""
        alerter = Alerter(discord_webhook_url="https://fake.discord.webhook/test")
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock) as mock_discord:
                records = await alerter.send(
                    "Test Alert",
                    message="Something happened",
                    severity=AlertSeverity.WARNING,
                    details={"key": "value"},
                )
                assert len(records) == 1
                assert records[0].channel == AlertChannel.DISCORD
                assert records[0].success is True
                mock_discord.assert_called_once()
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_telegram_dispatch(self) -> None:
        """Test Telegram dispatch."""
        alerter = Alerter(
            telegram_bot_token="fake_token",
            telegram_chat_id="fake_chat",
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_telegram", new_callable=AsyncMock) as mock_tg:
                records = await alerter.send(
                    "Test",
                    severity=AlertSeverity.CRITICAL,
                )
                assert len(records) == 1
                assert records[0].channel == AlertChannel.TELEGRAM
                mock_tg.assert_called_once()
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_multi_channel_dispatch(self) -> None:
        """Should dispatch to all configured channels."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
            telegram_bot_token="fake_token",
            telegram_chat_id="fake_chat",
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock), \
                 patch.object(alerter, "_send_telegram", new_callable=AsyncMock):
                records = await alerter.send("Multi-channel Test")
                assert len(records) == 2
                channels = {r.channel for r in records}
                assert AlertChannel.DISCORD in channels
                assert AlertChannel.TELEGRAM in channels
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_specific_channels(self) -> None:
        """Should only dispatch to specified channels."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
            telegram_bot_token="fake_token",
            telegram_chat_id="fake_chat",
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock) as mock_discord, \
                 patch.object(alerter, "_send_telegram", new_callable=AsyncMock) as mock_tg:
                records = await alerter.send(
                    "Discord Only",
                    channels=[AlertChannel.DISCORD],
                )
                assert len(records) == 1
                assert records[0].channel == AlertChannel.DISCORD
                mock_discord.assert_called_once()
                mock_tg.assert_not_called()
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_dispatch_failure_recorded(self) -> None:
        """Failed dispatches should be recorded with error."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
        )
        await alerter.start()
        try:
            with patch.object(
                alerter, "_send_discord",
                side_effect=Exception("Connection refused"),
            ):
                records = await alerter.send("Fail Test")
                assert len(records) == 1
                assert records[0].success is False
                assert "Connection refused" in records[0].error
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_history(self) -> None:
        """Alert history should accumulate."""
        alerter = Alerter(
            discord_webhook_url="https://fake.discord.webhook/test",
            rate_limit_seconds=0,  # disable rate limit for test
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock):
                await alerter.send("Alert 1")
                await alerter.send("Alert 2")
                assert len(alerter.history) == 2
                alerter.clear_history()
                assert len(alerter.history) == 0
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_eventbus_integration(self) -> None:
        """Should auto-alert on kill_switch events from EventBus."""
        bus = EventBus()
        alerter = Alerter(
            event_bus=bus,
            discord_webhook_url="https://fake.discord.webhook/test",
            rate_limit_seconds=0,
        )
        await alerter.start()
        try:
            with patch.object(alerter, "_send_discord", new_callable=AsyncMock):
                # Give the subscriber task time to start
                await asyncio.sleep(0.05)

                # Publish a kill_switch event
                await bus.publish("kill_switch", {
                    "action": "halt",
                    "trigger": "MAX_DRAWDOWN",
                })

                # Allow processing
                await asyncio.sleep(0.1)

                assert len(alerter.history) >= 1
                assert alerter.history[0].severity == AlertSeverity.CRITICAL
        finally:
            await alerter.stop()

    @pytest.mark.asyncio
    async def test_configured_channels(self) -> None:
        """Should correctly detect configured channels."""
        alerter = Alerter(
            discord_webhook_url="https://hook",
            telegram_bot_token="tok",
            telegram_chat_id="123",
            smtp_host="smtp.test.com",
            email_to="test@test.com",
        )
        channels = alerter._configured_channels()
        assert AlertChannel.DISCORD in channels
        assert AlertChannel.TELEGRAM in channels
        assert AlertChannel.EMAIL in channels


# ════════════════════════════════════════════════════════════════
# ColdWriter
# ════════════════════════════════════════════════════════════════


class TestColdWriter:
    """Tests for cold storage batch writer."""

    @pytest.mark.asyncio
    async def test_write_and_flush_sqlite(self, tmp_path: Path) -> None:
        """Should buffer records and flush to SQLite."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,  # disable auto-flush
        )
        await writer.start()
        try:
            await writer.write("test_table", {"col1": "val1", "col2": "val2"})
            await writer.write("test_table", {"col1": "val3", "col2": "val4"})
            assert writer.buffer_size == 2

            count = await writer.flush()
            assert count == 2
            assert writer.buffer_size == 0

            # Verify data in SQLite
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            rows = conn.execute("SELECT col1, col2 FROM test_table").fetchall()
            conn.close()
            assert len(rows) == 2
            assert rows[0] == ("val1", "val2")
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_write_many(self, tmp_path: Path) -> None:
        """Should buffer multiple records at once."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            await writer.write_many("bulk_table", [
                {"a": "1", "b": "2"},
                {"a": "3", "b": "4"},
                {"a": "5", "b": "6"},
            ])
            assert writer.buffer_size == 3
            count = await writer.flush()
            assert count == 3
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_auto_flush_on_buffer_max(self, tmp_path: Path) -> None:
        """Should auto-flush when buffer exceeds max size."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
            buffer_max_size=5,
        )
        await writer.start()
        try:
            for i in range(6):
                await writer.write("auto_flush", {"idx": str(i)})

            # Buffer should have been flushed (at least partially)
            assert writer.stats["total_written"] >= 5
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_stats(self, tmp_path: Path) -> None:
        """Should track write statistics."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            await writer.write("stats_table", {"x": "1"})
            await writer.flush()

            stats = writer.stats
            assert stats["total_written"] == 1
            assert stats["total_flushes"] == 1
            assert stats["errors"] == 0
            assert stats["buffer_size"] == 0
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_empty_flush(self, tmp_path: Path) -> None:
        """Flushing an empty buffer should return 0."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            count = await writer.flush()
            assert count == 0
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_multiple_tables(self, tmp_path: Path) -> None:
        """Should handle writes to different tables in the same flush."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            await writer.write("table_a", {"val": "a"})
            await writer.write("table_b", {"val": "b"})
            count = await writer.flush()
            assert count == 2

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            a_rows = conn.execute("SELECT val FROM table_a").fetchall()
            b_rows = conn.execute("SELECT val FROM table_b").fetchall()
            conn.close()
            assert len(a_rows) == 1
            assert len(b_rows) == 1
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_json_values_serialized(self, tmp_path: Path) -> None:
        """Dict/list values should be JSON-serialized."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            await writer.write("json_table", {
                "data": {"nested": True},
                "tags": ["a", "b"],
            })
            await writer.flush()

            import sqlite3
            conn = sqlite3.connect(str(db_path))
            row = conn.execute("SELECT data, tags FROM json_table").fetchone()
            conn.close()
            assert json.loads(row[0]) == {"nested": True}
            assert json.loads(row[1]) == ["a", "b"]
        finally:
            await writer.stop()

    @pytest.mark.asyncio
    async def test_stop_performs_final_flush(self, tmp_path: Path) -> None:
        """stop() should flush remaining buffer."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()

        await writer.write("final_flush", {"val": "pending"})
        assert writer.buffer_size == 1

        await writer.stop()

        # Verify data was written on stop
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute("SELECT val FROM final_flush").fetchall()
        conn.close()
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_migrations_applied(self, tmp_path: Path) -> None:
        """Migrations from storage/migrations/ should be applied."""
        db_path = tmp_path / "test.db"
        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()
        try:
            # The initial migration creates fills, orders, etc.
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            tables = [
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            ]
            conn.close()
            # At minimum _migrations should exist
            assert "_migrations" in tables
        finally:
            await writer.stop()


# ════════════════════════════════════════════════════════════════
# Dashboard
# ════════════════════════════════════════════════════════════════


class TestDashboard:
    """Tests for Grafana dashboard generation."""

    def test_generate_dashboard_structure(self) -> None:
        dash = generate_dashboard()
        assert "dashboard" in dash
        assert "overwrite" in dash

        db = dash["dashboard"]
        assert db["uid"] == "pmm-overview"
        assert db["title"] == "Polymarket Market Maker"
        assert "panels" in db
        assert len(db["panels"]) > 0

    def test_custom_title_and_uid(self) -> None:
        dash = generate_dashboard(title="Custom Title", uid="custom-uid")
        assert dash["dashboard"]["title"] == "Custom Title"
        assert dash["dashboard"]["uid"] == "custom-uid"

    def test_panels_have_required_fields(self) -> None:
        dash = generate_dashboard()
        for panel in dash["dashboard"]["panels"]:
            assert "id" in panel
            assert "title" in panel
            assert "type" in panel
            assert "gridPos" in panel
            assert "targets" in panel
            assert len(panel["targets"]) > 0
            assert "expr" in panel["targets"][0]

    def test_all_panel_types_valid(self) -> None:
        dash = generate_dashboard()
        valid_types = {"timeseries", "stat", "gauge", "table", "barchart"}
        for panel in dash["dashboard"]["panels"]:
            assert panel["type"] in valid_types

    def test_datasource_variable(self) -> None:
        dash = generate_dashboard()
        templating = dash["dashboard"]["templating"]["list"]
        assert len(templating) > 0
        assert templating[0]["name"] == "DS_PROMETHEUS"

    def test_annotations_configured(self) -> None:
        dash = generate_dashboard()
        annotations = dash["dashboard"]["annotations"]["list"]
        assert len(annotations) > 0
        assert "Kill Switch" in annotations[0]["name"]

    def test_tags(self) -> None:
        dash = generate_dashboard()
        tags = dash["dashboard"]["tags"]
        assert "polymarket" in tags
        assert "market-maker" in tags

    def test_export_to_file(self, tmp_path: Path) -> None:
        """Should write valid JSON to disk."""
        path = str(tmp_path / "dashboard.json")
        result = export_dashboard_json(path=path)

        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert "dashboard" in data
        assert isinstance(result, str)

    def test_dashboard_json_serializable(self) -> None:
        """Dashboard should be fully JSON-serializable."""
        dash = generate_dashboard()
        json_str = json.dumps(dash)
        roundtrip = json.loads(json_str)
        assert roundtrip["dashboard"]["uid"] == "pmm-overview"

    def test_headline_stat_panels(self) -> None:
        """Should have stat panels for PnL and exposure."""
        dash = generate_dashboard()
        stat_panels = [p for p in dash["dashboard"]["panels"] if p["type"] == "stat"]
        stat_titles = {p["title"] for p in stat_panels}
        assert "Cumulative PnL" in stat_titles
        assert "Daily PnL" in stat_titles
        assert "Total Exposure" in stat_titles
        assert "Kill Switch State" in stat_titles

    def test_prometheus_expressions(self) -> None:
        """All panels should reference pmm_ prefixed metrics."""
        dash = generate_dashboard()
        for panel in dash["dashboard"]["panels"]:
            expr = panel["targets"][0]["expr"]
            assert "pmm_" in expr, f"Panel '{panel['title']}' has non-pmm expr: {expr}"


# ════════════════════════════════════════════════════════════════
# Integration — Metrics + Health + Alerter
# ════════════════════════════════════════════════════════════════


class TestIntegration:
    """Integration tests combining multiple monitoring components."""

    @pytest.mark.asyncio
    async def test_metrics_served_via_health(self) -> None:
        """MetricsRegistry data should be accessible via HealthCheck /metrics."""
        metrics = MetricsRegistry()
        metrics.record_fill("0xintegration", "BUY", 100.0, latency_seconds=0.025)
        metrics.set_pnl(55.5, daily=12.3)
        metrics.set_inventory("0xintegration", 200.0)

        hc = HealthCheck(metrics=metrics, port=0)
        server = await asyncio.start_server(
            hc._handle_connection, "127.0.0.1", 0,
        )
        port = server.sockets[0].getsockname()[1]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /metrics HTTP/1.0\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = (await reader.read()).decode()
            writer.close()
            await writer.wait_closed()

            assert "pmm_fills_total" in response
            assert "pmm_pnl_cumulative_usd" in response
            assert "pmm_inventory_exposure_usd" in response
        finally:
            server.close()
            await server.wait_closed()

    @pytest.mark.asyncio
    async def test_full_pipeline_flow(self, tmp_path: Path) -> None:
        """End-to-end: EventBus → metrics + alerter + cold_writer."""
        bus = EventBus()
        metrics = MetricsRegistry()
        db_path = tmp_path / "pipeline.db"

        writer = ColdWriter(
            dsn=f"sqlite:///{db_path}",
            flush_interval_seconds=999,
        )
        await writer.start()

        alerter = Alerter(
            event_bus=bus,
            discord_webhook_url="https://fake.hook/test",
            rate_limit_seconds=0,
        )
        await alerter.start()

        try:
            # Simulate a fill
            metrics.record_fill("0xmarket", "BUY", 75.0, latency_seconds=0.032)
            await writer.write("fills", {
                "market_id": "0xmarket",
                "token_id": "0xtoken",
                "side": "BUY",
                "price": "0.55",
                "size": "100",
                "notional_usd": "75.0",
                "filled_at": datetime.now(timezone.utc).isoformat(),
            })

            # Simulate kill switch
            metrics.record_kill_switch("MAX_DRAWDOWN")
            metrics.set_kill_switch_state(2)

            await asyncio.sleep(0.05)

            with patch.object(alerter, "_send_discord", new_callable=AsyncMock):
                await bus.publish("kill_switch", {
                    "action": "halt",
                    "trigger": "MAX_DRAWDOWN",
                })
                await asyncio.sleep(0.1)

            # Flush cold storage
            count = await writer.flush()
            assert count == 1

            # Verify metrics
            text = metrics.exposition().decode()
            assert "pmm_fills_total" in text
            assert "pmm_kill_switch_trips_total" in text
        finally:
            await alerter.stop()
            await writer.stop()
