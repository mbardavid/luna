"""CTF Merge Operations — merge YES+NO token pairs into USDC.

When the market maker holds both YES and NO positions of the same market,
it's always more capital-efficient to merge them on-chain ($1/pair guaranteed)
than selling both sides on the orderbook (paying spread + fees twice).

Uses the existing CTFAdapter for on-chain transaction submission.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger("execution.ctf_merge")


@dataclass(frozen=True)
class MergeResult:
    """Result of a CTF merge operation."""

    condition_id: str
    amount_merged: Decimal
    usdc_received: Decimal  # always == amount_merged for binary markets
    gas_cost_usd: Decimal
    tx_hash: str
    success: bool
    error: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "amount_merged": str(self.amount_merged),
            "usdc_received": str(self.usdc_received),
            "gas_cost_usd": str(self.gas_cost_usd),
            "tx_hash": self.tx_hash,
            "success": self.success,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class CTFMerger:
    """Merge YES+NO conditional token pairs back to USDC.

    For binary markets, merging N pairs always returns exactly N USDC,
    regardless of market price. This is always better than selling
    both sides on the book.

    Parameters
    ----------
    ctf_adapter:
        CTFAdapter instance for on-chain merge transactions.
    """

    def __init__(self, ctf_adapter: Any | None = None) -> None:
        self._ctf = ctf_adapter

    async def merge_positions(
        self,
        condition_id: str,
        amount: Decimal,
        neg_risk: bool = False,
    ) -> MergeResult:
        """Merge YES+NO pairs into USDC.

        Parameters
        ----------
        condition_id:
            The condition ID (bytes32 hex string) of the market.
        amount:
            Number of pairs to merge (in token units).
        neg_risk:
            Whether this is a neg-risk market.

        Returns
        -------
        MergeResult
            Result with tx hash, gas cost, and success status.
        """
        if amount <= Decimal("0"):
            return MergeResult(
                condition_id=condition_id,
                amount_merged=Decimal("0"),
                usdc_received=Decimal("0"),
                gas_cost_usd=Decimal("0"),
                tx_hash="",
                success=False,
                error="Amount must be positive",
            )

        if self._ctf is None:
            return MergeResult(
                condition_id=condition_id,
                amount_merged=Decimal("0"),
                usdc_received=Decimal("0"),
                gas_cost_usd=Decimal("0"),
                tx_hash="",
                success=False,
                error="CTF adapter not configured",
            )

        logger.info(
            "ctf_merge.merging",
            condition_id=condition_id,
            amount=str(amount),
            neg_risk=neg_risk,
        )

        try:
            tx_result = await self._ctf.merge_positions(
                condition_id=condition_id,
                amount=amount,
                neg_risk=neg_risk,
            )

            success = tx_result.status.value == "CONFIRMED"
            return MergeResult(
                condition_id=condition_id,
                amount_merged=amount if success else Decimal("0"),
                usdc_received=amount if success else Decimal("0"),
                gas_cost_usd=tx_result.cost_usd,
                tx_hash=tx_result.tx_hash,
                success=success,
                error=tx_result.error,
            )

        except Exception as exc:
            logger.error(
                "ctf_merge.failed",
                condition_id=condition_id,
                amount=str(amount),
                error=str(exc),
            )
            return MergeResult(
                condition_id=condition_id,
                amount_merged=Decimal("0"),
                usdc_received=Decimal("0"),
                gas_cost_usd=Decimal("0"),
                tx_hash="",
                success=False,
                error=str(exc),
            )

    def calculate_mergeable(
        self,
        qty_yes: Decimal,
        qty_no: Decimal,
    ) -> Decimal:
        """Calculate how many pairs can be merged.

        Returns the minimum of YES and NO quantities (floor to integer).
        """
        mergeable = min(qty_yes, qty_no)
        # Floor to integer since CTF merge works in whole token units
        return Decimal(str(int(mergeable)))
