"""Position — posição agregada em um mercado bilateral."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, Field, computed_field, field_validator


class Position(BaseModel):
    """Posição bilateral YES/NO em um mercado."""

    market_id: str = Field(..., min_length=1)
    token_id_yes: str = Field(..., min_length=1)
    token_id_no: str = Field(..., min_length=1)

    # Quantities
    qty_yes: Decimal = Field(default=Decimal("0"), ge=0, description="Quantidade YES")
    qty_no: Decimal = Field(default=Decimal("0"), ge=0, description="Quantidade NO")

    # Average entries
    avg_entry_yes: Decimal = Field(default=Decimal("0"), ge=0, description="Preço médio de entrada YES")
    avg_entry_no: Decimal = Field(default=Decimal("0"), ge=0, description="Preço médio de entrada NO")

    # PnL
    net_exposure_usd: Decimal = Field(default=Decimal("0"), description="Exposição líquida em USD")
    unrealized_pnl: Decimal = Field(default=Decimal("0"), description="PnL não realizado")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="PnL realizado")

    # Meta
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def can_merge(self) -> bool:
        """True se há pares YES+NO que podem ser merged on-chain."""
        return min(self.qty_yes, self.qty_no) > 0
