"""AlertManager — webhook-based alerting for Discord and Telegram.

Dispatches structured alerts to multiple channels when kill switch
triggers, reconciliation mismatches, or other critical events occur.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import httpx
import structlog

from config.settings import settings

logger = structlog.get_logger("core.alert_manager")


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    FATAL = "FATAL"


class AlertChannel(str, Enum):
    """Supported notification channels."""

    DISCORD = "DISCORD"
    TELEGRAM = "TELEGRAM"


# Severity → Discord embed colour mapping
_DISCORD_COLOURS: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0x3498DB,       # blue
    AlertSeverity.WARNING: 0xF39C12,    # amber
    AlertSeverity.CRITICAL: 0xE74C3C,   # red
    AlertSeverity.FATAL: 0x8B0000,      # dark red
}

# Severity → emoji prefix for Telegram
_TELEGRAM_EMOJI: dict[AlertSeverity, str] = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.CRITICAL: "🔴",
    AlertSeverity.FATAL: "🚨",
}


class AlertManager:
    """Sends alerts to configured webhook channels.

    Parameters
    ----------
    discord_webhook_url:
        Discord webhook URL.  Empty string disables Discord alerts.
    telegram_bot_token:
        Telegram bot token.  Empty string disables Telegram alerts.
    telegram_chat_id:
        Telegram chat ID for sending messages.
    timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        discord_webhook_url: str = "",
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        timeout: float = 10.0,
    ) -> None:
        self._discord_url = discord_webhook_url or settings.ALERT_DISCORD_WEBHOOK
        self._tg_token = telegram_bot_token or settings.ALERT_TELEGRAM_BOT_TOKEN
        self._tg_chat_id = telegram_chat_id or settings.ALERT_TELEGRAM_CHAT_ID
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Create the underlying HTTP client."""
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        logger.info(
            "alert_manager.started",
            discord_configured=bool(self._discord_url),
            telegram_configured=bool(self._tg_token and self._tg_chat_id),
        )

    async def stop(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("alert_manager.stopped")

    # ── Public API ───────────────────────────────────────────────

    async def send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.WARNING,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send an alert to all configured channels.

        Parameters
        ----------
        title:
            Short alert title (e.g. ``"Kill Switch Activated"``).
        message:
            Detailed description of what happened.
        severity:
            Alert severity level.
        details:
            Optional dict of extra key/value pairs to include.
        """
        tasks: list[asyncio.Task[None]] = []

        if self._discord_url:
            tasks.append(
                asyncio.create_task(
                    self._send_discord(title, message, severity, details)
                )
            )

        if self._tg_token and self._tg_chat_id:
            tasks.append(
                asyncio.create_task(
                    self._send_telegram(title, message, severity, details)
                )
            )

        if not tasks:
            logger.debug("alert_manager.no_channels_configured", title=title)
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "alert_manager.send_failed",
                    error=str(result),
                    title=title,
                )

    # ── Discord ──────────────────────────────────────────────────

    async def _send_discord(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Send a rich embed to Discord via webhook."""
        assert self._client is not None, "Call start() first"

        fields: list[dict[str, Any]] = []
        if details:
            for key, value in details.items():
                fields.append({"name": key, "value": str(value), "inline": True})

        payload = {
            "embeds": [
                {
                    "title": f"[{severity.value}] {title}",
                    "description": message,
                    "color": _DISCORD_COLOURS.get(severity, 0x95A5A6),
                    "fields": fields,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "footer": {"text": f"{settings.APP_NAME} | {settings.APP_ENV}"},
                }
            ]
        }

        resp = await self._client.post(self._discord_url, json=payload)
        resp.raise_for_status()
        logger.debug("alert_manager.discord_sent", title=title, status=resp.status_code)

    # ── Telegram ─────────────────────────────────────────────────

    async def _send_telegram(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Send a formatted message to Telegram via Bot API."""
        assert self._client is not None, "Call start() first"

        emoji = _TELEGRAM_EMOJI.get(severity, "📢")
        text_parts = [
            f"{emoji} <b>[{severity.value}] {title}</b>",
            "",
            message,
        ]

        if details:
            text_parts.append("")
            for key, value in details.items():
                text_parts.append(f"<b>{key}:</b> {value}")

        text_parts.append("")
        text_parts.append(
            f"<i>{settings.APP_NAME} | {settings.APP_ENV} | "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>"
        )

        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        payload = {
            "chat_id": self._tg_chat_id,
            "text": "\n".join(text_parts),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        resp = await self._client.post(url, json=payload)
        resp.raise_for_status()
        logger.debug("alert_manager.telegram_sent", title=title, status=resp.status_code)
