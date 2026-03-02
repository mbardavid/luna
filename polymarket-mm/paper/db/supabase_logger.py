"""Fire-and-forget Supabase logger for PMM production data.

Writes to Supabase REST API using a dedicated httpx.AsyncClient
(separate from the CLOB client to avoid Tor SOCKS5 proxy conflicts).

All public methods are fire-and-forget: they schedule an asyncio task
and return immediately. Failures are logged but never propagated to
the caller — the trading loop must never block on DB writes.

Usage:
    logger = SupabaseLogger(run_id="prod-abc123")
    await logger.start()   # creates the httpx client
    logger.log_fill(...)   # fire-and-forget, returns immediately
    await logger.stop()    # closes the httpx client
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

_log = structlog.get_logger("supabase.logger")


class _DecimalEncoder(json.JSONEncoder):
    """JSON encoder that serializes Decimal as string."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseLogger:
    """Async fire-and-forget logger that writes to Supabase REST API.

    Parameters
    ----------
    run_id : str
        Unique identifier for this trading run.
    enabled : bool
        Master switch. When False, all methods are no-ops.
    """

    # ── Construction ────────────────────────────────────────────

    def __init__(self, run_id: str = "unknown", enabled: bool = True) -> None:
        self._run_id = run_id
        self._enabled = enabled

        self._supabase_url = os.environ.get("SUPABASE_URL", "")
        self._supabase_key = os.environ.get("SUPABASE_SERVICE_KEY", "")

        # If env vars missing → disable silently
        if not self._supabase_url or not self._supabase_key:
            self._enabled = False
            if enabled:  # only warn if user explicitly enabled
                _log.warning(
                    "supabase_logger.disabled",
                    reason="SUPABASE_URL or SUPABASE_SERVICE_KEY not set",
                )

        self._client: Any = None  # httpx.AsyncClient, lazy
        self._base_url = f"{self._supabase_url}/rest/v1" if self._supabase_url else ""
        self._headers = {
            "apikey": self._supabase_key,
            "Authorization": f"Bearer {self._supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

        # Track background tasks to avoid GC
        self._pending_tasks: set[asyncio.Task] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Lifecycle ───────────────────────────────────────────────

    async def start(self) -> None:
        """Create the httpx client. Call once at pipeline startup."""
        if not self._enabled:
            return
        try:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0),
                # No proxy — this is a direct HTTPS call to Supabase,
                # separate from the CLOB client's Tor proxy.
            )
            _log.info("supabase_logger.started", run_id=self._run_id)
        except Exception as e:
            _log.warning("supabase_logger.start_failed", error=str(e))
            self._enabled = False

    async def stop(self) -> None:
        """Close the httpx client. Call at pipeline shutdown."""
        # Wait briefly for pending tasks
        if self._pending_tasks:
            done, _ = await asyncio.wait(
                self._pending_tasks, timeout=3.0,
            )
            for t in done:
                self._pending_tasks.discard(t)

        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    # ── Public fire-and-forget methods ──────────────────────────

    def log_order(
        self,
        *,
        market_id: str,
        order_id: str = "",
        side: str,
        token_side: str = "",
        price: float | str | Decimal,
        size: float | str | Decimal,
        status: str = "submitted",
        complement_routed: bool = False,
    ) -> None:
        """Log an order submission (fire-and-forget)."""
        if not self._enabled:
            return
        payload = {
            "run_id": self._run_id,
            "market_id": market_id,
            "order_id": order_id or None,
            "side": side,
            "token_side": token_side or None,
            "price": float(Decimal(str(price))),
            "size": float(Decimal(str(size))),
            "status": status,
            "complement_routed": complement_routed,
        }
        self._fire("pmm_orders", payload)

    def log_fill(
        self,
        *,
        market_id: str,
        trade_id: str = "",
        order_id: str = "",
        side: str,
        token_side: str = "",
        price: float | str | Decimal,
        size: float | str | Decimal,
        fee: float | str | Decimal = 0,
    ) -> None:
        """Log a fill (fire-and-forget)."""
        if not self._enabled:
            return
        payload = {
            "run_id": self._run_id,
            "market_id": market_id,
            "trade_id": trade_id or None,
            "order_id": order_id or None,
            "side": side,
            "token_side": token_side or None,
            "price": float(Decimal(str(price))),
            "size": float(Decimal(str(size))),
            "fee": float(Decimal(str(fee))),
        }
        self._fire("pmm_fills", payload)

    def log_exit(
        self,
        *,
        market_id: str,
        token_side: str = "",
        entry_price: float | str | Decimal = 0,
        exit_price: float | str | Decimal = 0,
        quantity: float | str | Decimal = 0,
        pnl: float | str | Decimal = 0,
        reason: str = "",
    ) -> None:
        """Log a position exit (fire-and-forget)."""
        if not self._enabled:
            return
        payload = {
            "run_id": self._run_id,
            "market_id": market_id,
            "token_side": token_side or None,
            "entry_price": float(Decimal(str(entry_price))),
            "exit_price": float(Decimal(str(exit_price))),
            "quantity": float(Decimal(str(quantity))),
            "pnl": float(Decimal(str(pnl))),
            "reason": reason or None,
        }
        self._fire("pmm_exits", payload)

    def log_run_start(self, config: dict | None = None) -> None:
        """Log run start (fire-and-forget)."""
        if not self._enabled:
            return
        # Serialize config safely
        safe_config = None
        if config:
            try:
                safe_config = json.loads(json.dumps(config, cls=_DecimalEncoder))
            except Exception:
                safe_config = {"error": "config_not_serializable"}
        payload = {
            "run_id": self._run_id,
            "config": safe_config,
            "mode": "production",
            "status": "running",
        }
        self._fire("pmm_runs", payload)

    def log_run_end(
        self,
        *,
        total_pnl: float | str | Decimal = 0,
        total_fills: int = 0,
        total_orders: int = 0,
        status: str = "completed",
    ) -> None:
        """Log run end by updating the existing run record (fire-and-forget)."""
        if not self._enabled:
            return
        payload = {
            "ended_at": _now_iso(),
            "total_pnl": float(Decimal(str(total_pnl))),
            "total_fills": total_fills,
            "total_orders": total_orders,
            "status": status,
        }
        self._fire_update("pmm_runs", f"run_id=eq.{self._run_id}", payload)

    # ── Internal plumbing ───────────────────────────────────────

    def _fire(self, table: str, payload: dict) -> None:
        """Schedule a fire-and-forget POST to Supabase."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop — skip silently
        task = loop.create_task(self._post(table, payload))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    def _fire_update(self, table: str, filter_query: str, payload: dict) -> None:
        """Schedule a fire-and-forget PATCH to Supabase."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._patch(table, filter_query, payload))
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

    async def _post(self, table: str, payload: dict, _retry: int = 1) -> None:
        """POST to Supabase REST API with retry."""
        if not self._client:
            return
        url = f"{self._base_url}/{table}"
        body = json.dumps(payload, cls=_DecimalEncoder)
        for attempt in range(_retry + 1):
            try:
                resp = await self._client.post(
                    url, content=body, headers=self._headers,
                )
                if resp.status_code < 300:
                    return
                _log.debug(
                    "supabase_logger.post_error",
                    table=table,
                    status=resp.status_code,
                    body=resp.text[:200],
                    attempt=attempt,
                )
            except Exception as e:
                _log.debug(
                    "supabase_logger.post_exception",
                    table=table,
                    error=str(e),
                    attempt=attempt,
                )
            if attempt < _retry:
                await asyncio.sleep(1)

    async def _patch(self, table: str, filter_query: str, payload: dict, _retry: int = 1) -> None:
        """PATCH to Supabase REST API with retry."""
        if not self._client:
            return
        url = f"{self._base_url}/{table}?{filter_query}"
        body = json.dumps(payload, cls=_DecimalEncoder)
        for attempt in range(_retry + 1):
            try:
                resp = await self._client.patch(
                    url, content=body, headers=self._headers,
                )
                if resp.status_code < 300:
                    return
                _log.debug(
                    "supabase_logger.patch_error",
                    table=table,
                    status=resp.status_code,
                    body=resp.text[:200],
                    attempt=attempt,
                )
            except Exception as e:
                _log.debug(
                    "supabase_logger.patch_exception",
                    table=table,
                    error=str(e),
                    attempt=attempt,
                )
            if attempt < _retry:
                await asyncio.sleep(1)
