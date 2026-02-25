"""Order — representação de uma ordem no CLOB."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class Side(str, Enum):
    """Lado da ordem."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Tipo de ordem por tempo de vida."""

    GTC = "GTC"  # Good-Til-Cancelled
    GTD = "GTD"  # Good-Til-Date
    FOK = "FOK"  # Fill-Or-Kill


class OrderStatus(str, Enum):
    """Status do ciclo de vida da ordem."""

    PENDING = "PENDING"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class Order(BaseModel):
    """Ordem individual enviada ao CLOB."""

    client_order_id: UUID = Field(default_factory=uuid4, description="ID local único")
    market_id: str = Field(..., min_length=1)
    token_id: str = Field(..., min_length=1)

    side: Side
    price: Decimal = Field(..., gt=0, description="Preço da ordem (> 0)")
    size: Decimal = Field(..., gt=0, description="Tamanho da ordem (> 0)")
    filled_qty: Decimal = Field(default=Decimal("0"), ge=0, description="Quantidade preenchida")

    order_type: OrderType = Field(default=OrderType.GTC)
    status: OrderStatus = Field(default=OrderStatus.PENDING)
    maker_only: bool = Field(default=True, description="Ordem maker-only (nunca cruza)")

    ttl_ms: Optional[int] = Field(default=None, ge=0, description="TTL em milissegundos")
    strategy_tag: Optional[str] = Field(default=None, description="Tag da estratégia que gerou")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("filled_qty")
    @classmethod
    def filled_lte_size(cls, v: Decimal, info) -> Decimal:
        """filled_qty não pode exceder size."""
        size = info.data.get("size")
        if size is not None and v > size:
            raise ValueError("filled_qty cannot exceed size")
        return v
