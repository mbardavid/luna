"""CTFDelegate — delegates CTF on-chain operations to Crypto-Sage via A2A TaskSpec.

Instead of executing merge/split transactions directly on Polygon
(which was the old CTFAdapter's responsibility), this delegate
generates a TaskSpec payload and publishes it on the EventBus for
the Crypto-Sage agent to pick up and execute.

The result comes back asynchronously on the callback_topic.

Fast-path operations (EIP-712 order signing, CLOB submission) remain
local — only slow-path on-chain work is delegated.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import structlog

from a2a.task_spec import RiskClassification, TaskSpec

logger = structlog.get_logger("a2a.ctf_delegate")

# ── Constants (same as the old ctf_adapter.py, kept for reference) ──

DEFAULT_CTF_EXCHANGE_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
DEFAULT_NEG_RISK_CTF_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
DEFAULT_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# USDC has 6 decimals on Polygon
USDC_DECIMALS = 6

# ── Callback topics ─────────────────────────────────────────────────

TOPIC_MERGE_RESULT = "a2a.ctf.merge.result"
TOPIC_SPLIT_RESULT = "a2a.ctf.split.result"
TOPIC_BRIDGE_RESULT = "a2a.bridge.deposit.result"


class CTFDelegate:
    """Delegates CTF (merge/split) operations to Crypto-Sage via TaskSpec.

    Instead of submitting on-chain transactions directly, this class
    produces a TaskSpec dict/JSON that can be sent to the Crypto-Sage
    agent through any A2A transport (EventBus, HTTP, message queue).

    Usage::

        delegate = CTFDelegate()
        task = await delegate.request_merge(
            market_id="market-123",
            condition_id="0xabc...",
            qty=Decimal("100"),
            token_id_yes="tok_yes",
            token_id_no="tok_no",
        )
        # task is a dict with the full TaskSpec payload
        # Send it to Crypto-Sage via EventBus / A2A transport

    Parameters
    ----------
    default_gas_ceiling_gwei : int
        Default maximum gas price the Crypto-Sage should respect.
    """

    def __init__(
        self,
        default_gas_ceiling_gwei: int = 100,
    ) -> None:
        self._default_gas_ceiling = default_gas_ceiling_gwei

    async def request_merge(
        self,
        market_id: str,
        condition_id: str,
        qty: Decimal,
        token_id_yes: str,
        token_id_no: str,
        neg_risk: bool = False,
        requires_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Generate a TaskSpec for a CTF merge operation.

        Merge burns equal amounts of YES and NO conditional tokens
        and returns the underlying collateral (USDC).

        Parameters
        ----------
        market_id:
            Polymarket market identifier.
        condition_id:
            CTF condition ID (bytes32 hex string).
        qty:
            Number of token pairs to merge (in USDC units).
        token_id_yes:
            Token ID for the YES outcome.
        token_id_no:
            Token ID for the NO outcome.
        neg_risk:
            Whether this is a neg-risk market.
        requires_confirmation:
            If True, Crypto-Sage should confirm before executing.

        Returns
        -------
        dict
            Complete TaskSpec as a dict, ready for A2A transport.
        """
        handoff_id = str(uuid4())

        ctf_address = (
            DEFAULT_NEG_RISK_CTF_ADDRESS if neg_risk
            else DEFAULT_CTF_EXCHANGE_ADDRESS
        )

        spec = TaskSpec(
            handoff_id=handoff_id,
            operation="ctf.merge",
            params={
                "market_id": market_id,
                "condition_id": condition_id,
                "amount_usdc": str(qty),
                "amount_raw": self._to_raw_amount(qty),
                "token_id_yes": token_id_yes,
                "token_id_no": token_id_no,
                "neg_risk": neg_risk,
                "ctf_address": ctf_address,
                "usdc_address": DEFAULT_USDC_ADDRESS,
            },
            risk=RiskClassification(
                classification="medium",
                requires_confirmation=requires_confirmation,
                max_gas_gwei=self._default_gas_ceiling,
            ),
            callback_topic=TOPIC_MERGE_RESULT,
        )

        logger.info(
            "ctf_delegate.merge_requested",
            handoff_id=handoff_id,
            market_id=market_id,
            condition_id=condition_id,
            amount=str(qty),
            neg_risk=neg_risk,
        )

        return spec.to_dict()

    async def request_split(
        self,
        market_id: str,
        condition_id: str,
        qty_usd: Decimal,
        neg_risk: bool = False,
        requires_confirmation: bool = False,
    ) -> dict[str, Any]:
        """Generate a TaskSpec for a CTF split operation.

        Split locks USDC collateral and mints equal amounts of YES
        and NO conditional tokens.

        Parameters
        ----------
        market_id:
            Polymarket market identifier.
        condition_id:
            CTF condition ID (bytes32 hex string).
        qty_usd:
            Amount of USDC to split.
        neg_risk:
            Whether this is a neg-risk market.
        requires_confirmation:
            If True, Crypto-Sage should confirm before executing.

        Returns
        -------
        dict
            Complete TaskSpec as a dict, ready for A2A transport.
        """
        handoff_id = str(uuid4())

        ctf_address = (
            DEFAULT_NEG_RISK_CTF_ADDRESS if neg_risk
            else DEFAULT_CTF_EXCHANGE_ADDRESS
        )

        spec = TaskSpec(
            handoff_id=handoff_id,
            operation="ctf.split",
            params={
                "market_id": market_id,
                "condition_id": condition_id,
                "amount_usdc": str(qty_usd),
                "amount_raw": self._to_raw_amount(qty_usd),
                "neg_risk": neg_risk,
                "ctf_address": ctf_address,
                "usdc_address": DEFAULT_USDC_ADDRESS,
            },
            risk=RiskClassification(
                classification="medium",
                requires_confirmation=requires_confirmation,
                max_gas_gwei=self._default_gas_ceiling,
            ),
            callback_topic=TOPIC_SPLIT_RESULT,
        )

        logger.info(
            "ctf_delegate.split_requested",
            handoff_id=handoff_id,
            market_id=market_id,
            condition_id=condition_id,
            amount=str(qty_usd),
            neg_risk=neg_risk,
        )

        return spec.to_dict()

    async def request_bridge_deposit(
        self,
        amount_usd: Decimal,
        source_chain: str = "ethereum",
        dest_chain: str = "polygon",
        requires_confirmation: bool = True,
    ) -> dict[str, Any]:
        """Generate a TaskSpec for a bridge deposit operation.

        Parameters
        ----------
        amount_usd:
            Amount of USDC to bridge.
        source_chain:
            Source chain name.
        dest_chain:
            Destination chain name.
        requires_confirmation:
            If True, Crypto-Sage should confirm before executing.

        Returns
        -------
        dict
            Complete TaskSpec as a dict, ready for A2A transport.
        """
        handoff_id = str(uuid4())

        spec = TaskSpec(
            handoff_id=handoff_id,
            operation="bridge.deposit",
            params={
                "amount_usdc": str(amount_usd),
                "amount_raw": self._to_raw_amount(amount_usd),
                "source_chain": source_chain,
                "dest_chain": dest_chain,
            },
            risk=RiskClassification(
                classification="high",
                requires_confirmation=requires_confirmation,
                max_gas_gwei=self._default_gas_ceiling,
            ),
            callback_topic=TOPIC_BRIDGE_RESULT,
        )

        logger.info(
            "ctf_delegate.bridge_requested",
            handoff_id=handoff_id,
            amount=str(amount_usd),
            source_chain=source_chain,
            dest_chain=dest_chain,
        )

        return spec.to_dict()

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _to_raw_amount(amount: Decimal) -> int:
        """Convert USDC amount to raw integer (6 decimals)."""
        return int(amount * (10 ** USDC_DECIMALS))
