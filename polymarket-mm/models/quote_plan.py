"""QuotePlan — plano de cotas bilaterais YES/NO com slices."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from .order import Order, OrderStatus, OrderType, Side


class QuoteSide(str, Enum):
    """Lado da cota."""

    BID = "BID"
    ASK = "ASK"


class TokenSide(str, Enum):
    """Token alvo da cota."""

    YES = "YES"
    NO = "NO"


class QuoteSlice(BaseModel):
    """Uma fatia individual do plano de cotas."""

    side: QuoteSide
    token: TokenSide
    price: Decimal = Field(..., gt=0, description="Preço da cota (> 0)")
    size: Decimal = Field(..., gt=0, description="Tamanho da cota (> 0)")
    ttl_ms: int = Field(default=30_000, ge=0, description="TTL em milissegundos")


class QuotePlan(BaseModel):
    """Plano de cotas bilaterais para um mercado."""

    market_id: str = Field(..., min_length=1)
    trace_id: UUID = Field(default_factory=uuid4, description="ID de rastreamento")

    slices: list[QuoteSlice] = Field(default_factory=list, description="Fatias do plano")

    strategy_tag: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Token IDs needed to convert slices to orders
    token_id_yes: Optional[str] = Field(default=None)
    token_id_no: Optional[str] = Field(default=None)

    def to_order_intents(self) -> list[Order]:
        """Converte slices em Order intents prontos para submissão.

        Cada slice gera um Order com:
        - BID → BUY, ASK → SELL
        - token YES → token_id_yes, token NO → token_id_no
        """
        orders: list[Order] = []
        for s in self.slices:
            # Map quote side to order side
            order_side = Side.BUY if s.side == QuoteSide.BID else Side.SELL

            # Map token side to token_id
            if s.token == TokenSide.YES:
                token_id = self.token_id_yes or f"{self.market_id}_YES"
            else:
                token_id = self.token_id_no or f"{self.market_id}_NO"

            order = Order(
                market_id=self.market_id,
                token_id=token_id,
                side=order_side,
                price=s.price,
                size=s.size,
                order_type=OrderType.GTC,
                status=OrderStatus.PENDING,
                maker_only=True,
                ttl_ms=s.ttl_ms,
                strategy_tag=self.strategy_tag,
            )
            orders.append(order)

        return orders
