"""MarketState — snapshot do estado de um mercado no CLOB."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator


class MarketType(str, Enum):
    """Categoria do mercado para roteamento de oráculos."""

    CRYPTO_5M = "CRYPTO_5M"
    CRYPTO_15M = "CRYPTO_15M"
    SPORTS = "SPORTS"
    POLITICS = "POLITICS"
    OTHER = "OTHER"


class MarketState(BaseModel):
    """Snapshot completo do estado de um mercado bilateral YES/NO."""

    # Identifiers
    market_id: str = Field(..., min_length=1, description="ID único do mercado")
    condition_id: str = Field(..., min_length=1, description="Condition ID do CTF")
    token_id_yes: str = Field(..., min_length=1, description="Token ID do outcome YES")
    token_id_no: str = Field(..., min_length=1, description="Token ID do outcome NO")

    # Market params
    tick_size: Decimal = Field(..., gt=0, description="Tamanho mínimo de tick")
    min_order_size: Decimal = Field(..., gt=0, description="Tamanho mínimo de ordem")
    neg_risk: bool = Field(default=False, description="Se o mercado é negRisk")

    # Best prices
    yes_bid: Decimal = Field(default=Decimal("0"), ge=0, description="Melhor bid YES")
    yes_ask: Decimal = Field(default=Decimal("0"), ge=0, description="Melhor ask YES")
    no_bid: Decimal = Field(default=Decimal("0"), ge=0, description="Melhor bid NO")
    no_ask: Decimal = Field(default=Decimal("0"), ge=0, description="Melhor ask NO")

    # Depth at top of book
    depth_yes_bid: Decimal = Field(default=Decimal("0"), ge=0)
    depth_yes_ask: Decimal = Field(default=Decimal("0"), ge=0)
    depth_no_bid: Decimal = Field(default=Decimal("0"), ge=0)
    depth_no_ask: Decimal = Field(default=Decimal("0"), ge=0)

    # Volume
    volume_1m: Decimal = Field(default=Decimal("0"), ge=0, description="Volume 1 minuto")
    volume_5m: Decimal = Field(default=Decimal("0"), ge=0, description="Volume 5 minutos")

    # Classification
    market_type: MarketType = Field(default=MarketType.OTHER)

    # Meta
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    book_hash: Optional[str] = Field(default=None, description="Hash do snapshot do book")

    model_config = {"arbitrary_types_allowed": True}

    @computed_field  # type: ignore[prop-decorator]
    @property
    def mid_price(self) -> Decimal:
        """Preço médio entre best bid e best ask YES."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 2
        return Decimal("0")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread_yes(self) -> Decimal:
        """Spread absoluto do book YES."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return self.yes_ask - self.yes_bid
        return Decimal("0")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def spread_no(self) -> Decimal:
        """Spread absoluto do book NO."""
        if self.no_bid > 0 and self.no_ask > 0:
            return self.no_ask - self.no_bid
        return Decimal("0")

    @field_validator("yes_ask")
    @classmethod
    def yes_ask_gte_bid(cls, v: Decimal, info) -> Decimal:
        """Ask YES deve ser >= bid YES (quando ambos > 0)."""
        yes_bid = info.data.get("yes_bid", Decimal("0"))
        if v > 0 and yes_bid > 0 and v < yes_bid:
            raise ValueError("yes_ask must be >= yes_bid when both are positive")
        return v

    @field_validator("no_ask")
    @classmethod
    def no_ask_gte_bid(cls, v: Decimal, info) -> Decimal:
        """Ask NO deve ser >= bid NO (quando ambos > 0)."""
        no_bid = info.data.get("no_bid", Decimal("0"))
        if v > 0 and no_bid > 0 and v < no_bid:
            raise ValueError("no_ask must be >= no_bid when both are positive")
        return v
