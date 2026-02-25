"""FeatureVector — sinais extraídos do mercado para o quote engine."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class FeatureVector(BaseModel):
    """Vetor de features usado pelo QuoteEngine para gerar cotas."""

    # Identifiers
    market_id: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: UUID = Field(default_factory=uuid4)

    # Spread & book
    spread_bps: Decimal = Field(default=Decimal("0"), ge=0, description="Spread em basis points")
    book_imbalance: float = Field(
        default=0.0, ge=-1.0, le=1.0,
        description="Imbalance do book [-1, 1]: positivo = mais bids",
    )

    # Momentum & volatility
    micro_momentum: float = Field(
        default=0.0,
        description="Momentum de curto prazo (positivo = alta, negativo = queda)",
    )
    volatility_1m: float = Field(
        default=0.0, ge=0.0,
        description="Volatilidade realizada em 1 minuto",
    )

    # Liquidity & toxicity
    liquidity_score: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="Score de liquidez [0, 1]: 1 = muito líquido",
    )
    toxic_flow_score: float = Field(
        default=0.0, ge=0.0,
        description="Score de fluxo tóxico (z-score): > 2.5 = halt",
    )

    # Oracle
    oracle_delta: float = Field(
        default=0.0,
        description="Delta entre preço CLOB e oráculo externo",
    )

    # Execution estimates
    expected_fee_bps: Decimal = Field(
        default=Decimal("0"), ge=0,
        description="Fee estimada em basis points",
    )
    queue_position_estimate: float = Field(
        default=0.0, ge=0.0,
        description="Posição estimada na fila (0 = topo)",
    )

    # Quality
    data_quality_score: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Qualidade dos dados [0, 1]: < 0.5 = degradado",
    )
