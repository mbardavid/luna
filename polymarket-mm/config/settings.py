"""Pydantic BaseSettings — all monetary values as Decimal, never float."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from env / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Application ─────────────────────────────────────────────
    APP_ENV: Literal["dev", "paper", "prod"] = "dev"
    APP_NAME: str = "polymarket-mm"
    LOG_LEVEL: str = "INFO"

    # ── Risk Limits (all Decimal) ───────────────────────────────
    MAX_EXPOSURE_PER_MARKET_USD: Decimal = Field(default=Decimal("500"))
    MAX_TOTAL_EXPOSURE_USD: Decimal = Field(default=Decimal("5000"))
    MAX_DAILY_LOSS_USD: Decimal = Field(default=Decimal("200"))
    MAX_POSITION_SIZE_PER_MARKET: Decimal = Field(default=Decimal("1000"))
    MAX_ORDER_SIZE_USD: Decimal = Field(default=Decimal("100"))
    MAX_DRAWDOWN_PCT: Decimal = Field(default=Decimal("0.05"))

    # ── Spread / Quoting ────────────────────────────────────────
    MIN_SPREAD_BPS: Decimal = Field(default=Decimal("30"))
    DEFAULT_HALF_SPREAD_BPS: Decimal = Field(default=Decimal("50"))
    REWARDS_AGGRESSIVENESS: Decimal = Field(default=Decimal("0.5"))

    # ── Inventory Skew (Avellaneda-Stoikov) ─────────────────────
    GAMMA_RISK_AVERSION: Decimal = Field(default=Decimal("0.3"))
    TIME_HORIZON_HOURS: Decimal = Field(default=Decimal("24"))

    # ── Toxic Flow ──────────────────────────────────────────────
    TOXIC_FLOW_ZSCORE_THRESHOLD: Decimal = Field(default=Decimal("2.5"))
    TOXIC_FLOW_WINDOW_SECONDS: int = 60

    # ── Complete-Set Arbitrage ──────────────────────────────────
    COMPLETE_SET_MIN_PROFIT_USD: Decimal = Field(default=Decimal("0.50"))
    GAS_COST_PER_MERGE_USD: Decimal = Field(default=Decimal("1.00"))
    # NOTE: GAS_PRICE_ABORT_GWEI removed — gas management is now
    # handled by Crypto-Sage via A2A delegation (see a2a/ package).

    # ── Timing / Heartbeat ──────────────────────────────────────
    HEARTBEAT_INTERVAL_SECONDS: int = 5
    ENGINE_RESTART_TOLERANCE_SECONDS: int = 30
    ENGINE_RESTART_BACKOFF_BASE_SECONDS: int = 2
    ENGINE_RESTART_BACKOFF_MAX_SECONDS: int = 120
    DATA_GAP_TOLERANCE_SECONDS: int = 8
    QUOTE_CYCLE_INTERVAL_SECONDS: Decimal = Field(default=Decimal("0.5"))
    RECONCILIATION_INTERVAL_SECONDS: int = 60

    # ── Network / API ───────────────────────────────────────────
    CLOB_REST_BASE_URL: str = "https://clob.polymarket.com"
    CLOB_WS_URL: str = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    # NOTE: POLYGON_RPC_URL and POLYGON_RPC_FALLBACK_URL removed —
    # RPC management is now handled by Crypto-Sage via A2A delegation.

    # ── Credentials (never commit real values) ──────────────────
    POLYMARKET_API_KEY: str = ""
    POLYMARKET_SECRET: str = ""
    POLYMARKET_PASSPHRASE: str = ""
    POLYMARKET_PRIVATE_KEY: str = ""
    PRIVATE_KEY: str = ""  # Alias for POLYMARKET_PRIVATE_KEY

    # ── Database ────────────────────────────────────────────────
    POSTGRES_DSN: str = "postgresql://mm:mm_dev_pass@localhost:5432/polymarket_mm"
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Monitoring ──────────────────────────────────────────────
    METRICS_PORT: int = 8080
    HEALTH_HOST: str = "0.0.0.0"
    ALERT_DISCORD_WEBHOOK: str = ""
    ALERT_TELEGRAM_BOT_TOKEN: str = ""
    ALERT_TELEGRAM_CHAT_ID: str = ""
    ALERT_RATE_LIMIT_SECONDS: int = 60

    # ── Cold Storage ────────────────────────────────────────────
    COLD_STORAGE_DSN: str = "sqlite:///data/cold_storage.db"
    COLD_FLUSH_INTERVAL_SECONDS: int = 10
    COLD_BUFFER_MAX_SIZE: int = 5000

    # ── Email Alerts (optional) ─────────────────────────────────
    ALERT_SMTP_HOST: str = ""
    ALERT_SMTP_PORT: int = 587
    ALERT_SMTP_USER: str = ""
    ALERT_SMTP_PASSWORD: str = ""
    ALERT_EMAIL_FROM: str = ""
    ALERT_EMAIL_TO: str = ""


settings = Settings()
