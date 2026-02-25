"""Multi-channel alerter with rate limiting and EventBus integration.

Dispatches alerts to Discord webhook, Telegram Bot API, and email
(SMTP) with severity levels and per-channel rate limiting to avoid
flooding operators during incident storms.

Integrates with the project's ``EventBus`` to subscribe to system
events and automatically generate alerts.

Usage::

    alerter = Alerter(event_bus=bus, metrics=metrics)
    await alerter.start()
    await alerter.send("System started", severity=AlertSeverity.INFO)
"""

from __future__ import annotations

import asyncio
import smtplib
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.mime.text import MIMEText
from enum import Enum
from typing import Any

import httpx
import structlog

from config.settings import settings
from core.event_bus import EventBus

logger = structlog.get_logger("monitoring.alerter")

__all__ = ["Alerter", "AlertChannel", "AlertSeverity"]


class AlertSeverity(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertChannel(str, Enum):
    """Supported alert delivery channels."""

    DISCORD = "discord"
    TELEGRAM = "telegram"
    EMAIL = "email"


_DISCORD_COLOURS: dict[AlertSeverity, int] = {
    AlertSeverity.INFO: 0x3498DB,
    AlertSeverity.WARNING: 0xF39C12,
    AlertSeverity.CRITICAL: 0xE74C3C,
}

_SEVERITY_EMOJI: dict[AlertSeverity, str] = {
    AlertSeverity.INFO: "ℹ️",
    AlertSeverity.WARNING: "⚠️",
    AlertSeverity.CRITICAL: "🚨",
}


@dataclass
class AlertRecord:
    """Record of a dispatched alert."""

    title: str
    message: str
    severity: AlertSeverity
    channel: AlertChannel
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    success: bool = True
    error: str = ""


class Alerter:
    """Multi-channel alerter with rate limiting.

    Parameters
    ----------
    event_bus:
        EventBus to subscribe for automatic alerting.
    discord_webhook_url:
        Discord webhook URL. Empty = disabled.
    telegram_bot_token:
        Telegram bot token. Empty = disabled.
    telegram_chat_id:
        Telegram chat id. Empty = disabled.
    smtp_host:
        SMTP server host. Empty = disabled.
    smtp_port:
        SMTP server port.
    smtp_user:
        SMTP username.
    smtp_password:
        SMTP password.
    email_from:
        Sender email address.
    email_to:
        Recipient email address.
    rate_limit_seconds:
        Minimum seconds between alerts with the same title.
        Prevents flood during incident storms.
    http_timeout:
        HTTP request timeout in seconds.
    """

    def __init__(
        self,
        event_bus: EventBus | None = None,
        discord_webhook_url: str = "",
        telegram_bot_token: str = "",
        telegram_chat_id: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        email_from: str = "",
        email_to: str = "",
        rate_limit_seconds: float = 60.0,
        http_timeout: float = 10.0,
    ) -> None:
        self._event_bus = event_bus
        self._discord_url = discord_webhook_url or getattr(settings, "ALERT_DISCORD_WEBHOOK", "")
        self._tg_token = telegram_bot_token or getattr(settings, "ALERT_TELEGRAM_BOT_TOKEN", "")
        self._tg_chat_id = telegram_chat_id or getattr(settings, "ALERT_TELEGRAM_CHAT_ID", "")
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._email_from = email_from
        self._email_to = email_to
        self._rate_limit_seconds = rate_limit_seconds
        self._http_timeout = http_timeout

        self._client: httpx.AsyncClient | None = None
        self._history: list[AlertRecord] = []
        self._last_sent: dict[str, float] = defaultdict(float)  # title -> monotonic time
        self._subscriber_task: asyncio.Task[None] | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the alerter: open HTTP client and subscribe to EventBus."""
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._http_timeout))
        logger.info(
            "alerter.started",
            discord=bool(self._discord_url),
            telegram=bool(self._tg_token and self._tg_chat_id),
            email=bool(self._smtp_host and self._email_to),
        )

        if self._event_bus:
            self._subscriber_task = asyncio.create_task(self._event_bus_listener())

    async def stop(self) -> None:
        """Stop the alerter and clean up resources."""
        if self._subscriber_task and not self._subscriber_task.done():
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None

        if self._client:
            await self._client.aclose()
            self._client = None

        logger.info("alerter.stopped")

    # ── Public API ───────────────────────────────────────────────

    async def send(
        self,
        title: str,
        message: str = "",
        severity: AlertSeverity = AlertSeverity.INFO,
        details: dict[str, Any] | None = None,
        channels: list[AlertChannel] | None = None,
    ) -> list[AlertRecord]:
        """Send an alert to configured channels.

        Parameters
        ----------
        title:
            Alert title.
        message:
            Alert body.
        severity:
            Severity level.
        details:
            Optional key-value context.
        channels:
            Specific channels to send to. None = all configured.

        Returns
        -------
        list[AlertRecord]
            Records of each dispatch attempt.
        """
        # Rate limiting
        if not self._should_send(title):
            logger.debug("alerter.rate_limited", title=title)
            return []

        self._last_sent[title] = time.monotonic()

        target_channels = channels or self._configured_channels()
        if not target_channels:
            logger.debug("alerter.no_channels", title=title)
            return []

        records: list[AlertRecord] = []
        tasks: list[tuple[AlertChannel, asyncio.Task[None]]] = []

        for channel in target_channels:
            coro = self._dispatch(channel, title, message, severity, details)
            tasks.append((channel, asyncio.create_task(coro)))

        for channel, task in tasks:
            record = AlertRecord(
                title=title,
                message=message,
                severity=severity,
                channel=channel,
            )
            try:
                await task
                record.success = True
            except Exception as exc:
                record.success = False
                record.error = str(exc)
                logger.error("alerter.dispatch_failed", channel=channel.value, error=str(exc))

            records.append(record)

        self._history.extend(records)
        return records

    @property
    def history(self) -> list[AlertRecord]:
        """Return a copy of the alert history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear the alert history."""
        self._history.clear()

    # ── Rate limiting ────────────────────────────────────────────

    def _should_send(self, title: str) -> bool:
        """Check if enough time has passed since last alert with same title."""
        last = self._last_sent.get(title, 0.0)
        return (time.monotonic() - last) >= self._rate_limit_seconds

    def reset_rate_limits(self) -> None:
        """Clear rate limit state (useful for tests)."""
        self._last_sent.clear()

    # ── Channel discovery ────────────────────────────────────────

    def _configured_channels(self) -> list[AlertChannel]:
        """Return list of channels that have credentials configured."""
        channels: list[AlertChannel] = []
        if self._discord_url:
            channels.append(AlertChannel.DISCORD)
        if self._tg_token and self._tg_chat_id:
            channels.append(AlertChannel.TELEGRAM)
        if self._smtp_host and self._email_to:
            channels.append(AlertChannel.EMAIL)
        return channels

    # ── Dispatch ─────────────────────────────────────────────────

    async def _dispatch(
        self,
        channel: AlertChannel,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Route alert to the appropriate channel sender."""
        if channel == AlertChannel.DISCORD:
            await self._send_discord(title, message, severity, details)
        elif channel == AlertChannel.TELEGRAM:
            await self._send_telegram(title, message, severity, details)
        elif channel == AlertChannel.EMAIL:
            await self._send_email(title, message, severity, details)

    async def _send_discord(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Send Discord webhook embed."""
        assert self._client is not None, "Call start() first"

        fields: list[dict[str, Any]] = []
        if details:
            for key, value in details.items():
                fields.append({"name": key, "value": str(value), "inline": True})

        payload = {
            "embeds": [
                {
                    "title": f"{_SEVERITY_EMOJI.get(severity, '')} [{severity.value.upper()}] {title}",
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

    async def _send_telegram(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Send Telegram message via Bot API."""
        assert self._client is not None, "Call start() first"

        emoji = _SEVERITY_EMOJI.get(severity, "📢")
        text_parts = [
            f"{emoji} <b>[{severity.value.upper()}] {title}</b>",
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

    async def _send_email(
        self,
        title: str,
        message: str,
        severity: AlertSeverity,
        details: dict[str, Any] | None,
    ) -> None:
        """Send alert via SMTP email (runs in executor to avoid blocking)."""
        body_parts = [f"[{severity.value.upper()}] {title}", "", message]
        if details:
            body_parts.append("")
            for key, value in details.items():
                body_parts.append(f"{key}: {value}")

        body_parts.append("")
        body_parts.append(f"— {settings.APP_NAME} | {settings.APP_ENV}")

        body = "\n".join(body_parts)
        msg = MIMEText(body)
        msg["Subject"] = f"[{severity.value.upper()}] {title}"
        msg["From"] = self._email_from
        msg["To"] = self._email_to

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._smtp_send, msg)

    def _smtp_send(self, msg: MIMEText) -> None:
        """Blocking SMTP send — run in executor."""
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
            server.starttls()
            if self._smtp_user and self._smtp_password:
                server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)

    # ── EventBus listener ────────────────────────────────────────

    async def _event_bus_listener(self) -> None:
        """Subscribe to kill_switch events and auto-alert."""
        if not self._event_bus:
            return

        try:
            async for event in self._event_bus.subscribe("kill_switch"):
                action = event.payload.get("action", "unknown")
                trigger = event.payload.get("trigger", "unknown")

                if action in ("halt",):
                    severity = AlertSeverity.CRITICAL
                elif action in ("pause", "pause_market"):
                    severity = AlertSeverity.WARNING
                else:
                    severity = AlertSeverity.INFO

                await self.send(
                    title=f"Kill Switch: {action}",
                    message=f"Trigger: {trigger}",
                    severity=severity,
                    details=event.payload,
                )
        except asyncio.CancelledError:
            pass
