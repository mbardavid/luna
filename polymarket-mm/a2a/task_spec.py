"""TaskSpec — A2A handoff schema for inter-agent operation delegation.

Defines the payload structure that polymarket-mm sends to Crypto-Sage
(or any A2A-capable agent) when delegating slow-path on-chain operations
such as CTF merge/split or bridge deposits.

The Crypto-Sage receives a TaskSpec JSON, executes the operation, and
publishes the result back on the EventBus topic specified in
``callback_topic``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class RiskClassification(BaseModel):
    """Risk metadata attached to every A2A task."""

    classification: str = "medium"  # low | medium | high | critical
    requires_confirmation: bool = False  # True → Crypto-Sage must confirm with operator
    max_gas_gwei: int | None = None  # Optional gas ceiling
    deadline_seconds: int | None = None  # Optional TTL for the operation


class TaskSpec(BaseModel):
    """Schema for A2A inter-agent operation handoff.

    Every slow-path operation delegated from polymarket-mm to Crypto-Sage
    is serialised as a TaskSpec JSON payload.

    Fields
    ------
    version : str
        Schema version for forward compatibility.
    handoff_id : str
        Unique UUID identifying this specific delegation.
    operation : str
        Canonical operation name.  Current values:
        - ``"ctf.merge"`` — merge YES+NO tokens into collateral
        - ``"ctf.split"`` — split collateral into YES+NO tokens
        - ``"bridge.deposit"`` — bridge funds to Polygon
    params : dict
        Operation-specific parameters (amounts, token IDs, etc.).
    risk : RiskClassification
        Risk classification and constraints.
    callback_topic : str
        EventBus topic where the result should be published.
    source_agent : str
        Identifier of the requesting agent.
    """

    version: str = "1.0"
    handoff_id: str = Field(default_factory=lambda: str(uuid4()))
    operation: str
    params: dict[str, Any]
    risk: RiskClassification = Field(default_factory=RiskClassification)
    callback_topic: str
    source_agent: str = "polymarket-mm"

    def to_json(self) -> str:
        """Serialise to compact JSON for A2A transport."""
        return self.model_dump_json()

    def to_dict(self) -> dict[str, Any]:
        """Serialise to dict."""
        return self.model_dump()
