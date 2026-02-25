"""Grafana dashboard JSON model generator.

Produces pre-configured dashboard definitions importable into Grafana
via the dashboard provisioning API or the UI import dialog.

Each dashboard panel targets the Prometheus metrics exposed by
``monitoring.metrics.MetricsRegistry``.

Usage::

    from monitoring.dashboard import generate_dashboard
    dash = generate_dashboard()
    with open("grafana-dashboard.json", "w") as f:
        json.dump(dash, f, indent=2)
"""

from __future__ import annotations

import json
from typing import Any

__all__ = ["generate_dashboard", "export_dashboard_json"]


def _panel(
    title: str,
    expr: str,
    *,
    panel_id: int,
    grid_x: int = 0,
    grid_y: int = 0,
    width: int = 12,
    height: int = 8,
    panel_type: str = "timeseries",
    unit: str = "",
    legend_format: str = "{{market_id}}",
    description: str = "",
    thresholds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a single Grafana panel definition."""
    panel: dict[str, Any] = {
        "id": panel_id,
        "title": title,
        "type": panel_type,
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "gridPos": {"h": height, "w": width, "x": grid_x, "y": grid_y},
        "targets": [
            {
                "expr": expr,
                "legendFormat": legend_format,
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": thresholds or [
                        {"color": "green", "value": None},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "tooltip": {"mode": "multi"},
            "legend": {"displayMode": "table", "placement": "bottom"},
        },
    }
    if description:
        panel["description"] = description
    return panel


def _stat_panel(
    title: str,
    expr: str,
    *,
    panel_id: int,
    grid_x: int = 0,
    grid_y: int = 0,
    width: int = 6,
    height: int = 4,
    unit: str = "",
    thresholds: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a stat/gauge panel."""
    return {
        "id": panel_id,
        "title": title,
        "type": "stat",
        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
        "gridPos": {"h": height, "w": width, "x": grid_x, "y": grid_y},
        "targets": [
            {
                "expr": expr,
                "legendFormat": "",
                "refId": "A",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": thresholds or [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": -50},
                        {"color": "red", "value": -200},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "background",
        },
    }


def generate_dashboard(
    title: str = "Polymarket Market Maker",
    uid: str = "pmm-overview",
    refresh: str = "10s",
) -> dict[str, Any]:
    """Generate a complete Grafana dashboard JSON model.

    Parameters
    ----------
    title:
        Dashboard title.
    uid:
        Stable UID for the dashboard (idempotent import).
    refresh:
        Auto-refresh interval.

    Returns
    -------
    dict
        A Grafana dashboard JSON model ready for import.
    """
    panels: list[dict[str, Any]] = []

    # ── Row 0: Headline stats ───────────────────────────────────
    panels.append(_stat_panel(
        "Cumulative PnL",
        "pmm_pnl_cumulative_usd",
        panel_id=1, grid_x=0, grid_y=0, width=6, unit="currencyUSD",
    ))
    panels.append(_stat_panel(
        "Daily PnL",
        "pmm_pnl_daily_usd",
        panel_id=2, grid_x=6, grid_y=0, width=6, unit="currencyUSD",
    ))
    panels.append(_stat_panel(
        "Total Exposure",
        "pmm_total_exposure_usd",
        panel_id=3, grid_x=12, grid_y=0, width=6, unit="currencyUSD",
        thresholds=[
            {"color": "green", "value": None},
            {"color": "yellow", "value": 2000},
            {"color": "red", "value": 4000},
        ],
    ))
    panels.append(_stat_panel(
        "Kill Switch State",
        "pmm_kill_switch_state",
        panel_id=4, grid_x=18, grid_y=0, width=6,
        thresholds=[
            {"color": "green", "value": None},
            {"color": "yellow", "value": 1},
            {"color": "red", "value": 2},
        ],
    ))

    # ── Row 1: Fills ────────────────────────────────────────────
    panels.append(_panel(
        "Fills / sec",
        'rate(pmm_fills_total[1m])',
        panel_id=10, grid_x=0, grid_y=4, width=12,
        unit="ops", legend_format="{{market_id}} {{side}}",
        description="Fill rate per market/side, 1-minute window",
    ))
    panels.append(_panel(
        "Cumulative Fill Value (USD)",
        "pmm_fill_value_usd_total",
        panel_id=11, grid_x=12, grid_y=4, width=12,
        unit="currencyUSD", legend_format="{{market_id}} {{side}}",
    ))

    # ── Row 2: Latency ──────────────────────────────────────────
    panels.append(_panel(
        "Order Latency p50 / p99",
        'histogram_quantile(0.99, rate(pmm_order_latency_seconds_bucket[5m]))',
        panel_id=20, grid_x=0, grid_y=12, width=12,
        unit="s", legend_format="p99 {{market_id}}",
        description="99th-percentile order round-trip latency",
    ))
    panels.append(_panel(
        "Quote Cycle Duration",
        'histogram_quantile(0.99, rate(pmm_quote_cycle_seconds_bucket[5m]))',
        panel_id=21, grid_x=12, grid_y=12, width=12,
        unit="s", legend_format="p99",
    ))

    # ── Row 3: Inventory & Spreads ──────────────────────────────
    panels.append(_panel(
        "Inventory Exposure by Market",
        "pmm_inventory_exposure_usd",
        panel_id=30, grid_x=0, grid_y=20, width=12,
        unit="currencyUSD",
    ))
    panels.append(_panel(
        "Quoted Half-Spread (bps)",
        "pmm_quoted_spread_bps",
        panel_id=31, grid_x=12, grid_y=20, width=12,
        unit="none", legend_format="{{market_id}}",
    ))

    # ── Row 4: Orders ───────────────────────────────────────────
    panels.append(_panel(
        "Order Submissions / min",
        'rate(pmm_orders_submitted_total[1m]) * 60',
        panel_id=40, grid_x=0, grid_y=28, width=8,
        unit="ops", legend_format="{{market_id}} {{side}}",
    ))
    panels.append(_panel(
        "Cancellations / min",
        'rate(pmm_orders_cancelled_total[1m]) * 60',
        panel_id=41, grid_x=8, grid_y=28, width=8,
        unit="ops",
    ))
    panels.append(_panel(
        "Rejections / min",
        'rate(pmm_orders_rejected_total[1m]) * 60',
        panel_id=42, grid_x=16, grid_y=28, width=8,
        unit="ops",
        thresholds=[
            {"color": "green", "value": None},
            {"color": "red", "value": 1},
        ],
    ))

    # ── Row 5: Kill Switch & WS ─────────────────────────────────
    panels.append(_panel(
        "Kill Switch Trips",
        'increase(pmm_kill_switch_trips_total[1h])',
        panel_id=50, grid_x=0, grid_y=36, width=8,
        legend_format="{{trigger}}",
    ))
    panels.append(_panel(
        "WebSocket Messages / sec",
        'rate(pmm_ws_messages_total[1m])',
        panel_id=51, grid_x=8, grid_y=36, width=8,
        unit="ops", legend_format="",
    ))
    panels.append(_panel(
        "WS Reconnections",
        'increase(pmm_ws_reconnects_total[1h])',
        panel_id=52, grid_x=16, grid_y=36, width=8,
        legend_format="",
    ))

    dashboard: dict[str, Any] = {
        "dashboard": {
            "uid": uid,
            "title": title,
            "tags": ["polymarket", "market-maker", "trading"],
            "timezone": "utc",
            "refresh": refresh,
            "time": {"from": "now-6h", "to": "now"},
            "fiscalYearStartMonth": 0,
            "editable": True,
            "graphTooltip": 1,  # shared crosshair
            "panels": panels,
            "templating": {
                "list": [
                    {
                        "name": "DS_PROMETHEUS",
                        "type": "datasource",
                        "query": "prometheus",
                        "current": {"text": "default", "value": "default"},
                    }
                ]
            },
            "annotations": {
                "list": [
                    {
                        "name": "Kill Switch Events",
                        "datasource": {"type": "prometheus", "uid": "${DS_PROMETHEUS}"},
                        "expr": 'increase(pmm_kill_switch_trips_total[1m]) > 0',
                        "tagKeys": "trigger",
                        "titleFormat": "Kill Switch: {{trigger}}",
                        "enable": True,
                    }
                ]
            },
            "schemaVersion": 39,
            "version": 1,
        },
        "overwrite": True,
    }

    return dashboard


def export_dashboard_json(
    path: str = "grafana-dashboard.json",
    **kwargs: Any,
) -> str:
    """Generate and write dashboard JSON to a file.

    Parameters
    ----------
    path:
        Output file path.
    **kwargs:
        Forwarded to ``generate_dashboard()``.

    Returns
    -------
    str
        The JSON string written.
    """
    dashboard = generate_dashboard(**kwargs)
    json_str = json.dumps(dashboard, indent=2)
    with open(path, "w") as f:
        f.write(json_str)
    return json_str
