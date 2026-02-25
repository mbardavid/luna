"""CTFAdapter — on-chain interaction with the Conditional Token Framework.

Provides async methods for:
- ``merge_positions()`` — merge YES+NO tokens back to collateral (USDC)
- ``split_position()`` — split collateral (USDC) into YES+NO tokens

Uses the CTF Exchange contract on Polygon mainnet. All gas estimation,
ABI encoding, and transaction submission are handled internally.

Integration with the existing ``RPCManager`` for resilient RPC access.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

import structlog
from web3 import AsyncWeb3
from web3.types import TxReceipt

logger = structlog.get_logger("web3_infra.ctf_adapter")

# ── Constants ────────────────────────────────────────────────────────

# Polymarket CTF Exchange contract on Polygon mainnet
DEFAULT_CTF_EXCHANGE_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# Polymarket Neg Risk CTF Exchange (for negRisk markets)
DEFAULT_NEG_RISK_CTF_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"

# USDC on Polygon
DEFAULT_USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Conditional Tokens Framework contract
DEFAULT_CT_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"

# ── ABI fragments for CTF operations ────────────────────────────────

# Minimal ABI for the merge/split operations on the ConditionalTokens contract
CTF_ABI = [
    {
        "name": "mergePositions",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "splitPosition",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "partition", "type": "uint256[]"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [],
    },
    {
        "name": "getPositionId",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "collectionId", "type": "bytes32"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
    {
        "name": "balanceOf",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "id", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# ERC20 ABI fragment for USDC approval
ERC20_APPROVE_ABI = [
    {
        "name": "approve",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "outputs": [{"name": "", "type": "bool"}],
    },
    {
        "name": "allowance",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "outputs": [{"name": "", "type": "uint256"}],
    },
]

# Binary market partition: [1, 2] = [YES, NO]
BINARY_PARTITION = [1, 2]

# Null parent collection (top-level condition)
NULL_PARENT = b"\x00" * 32


class TxStatus(str, Enum):
    """Transaction submission status."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REVERTED = "REVERTED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class CTFTxResult:
    """Result of a CTF on-chain transaction."""

    tx_hash: str
    status: TxStatus
    gas_used: int
    gas_price_gwei: Decimal
    cost_usd: Decimal
    block_number: int
    error: str | None = None


@dataclass
class CTFAdapterConfig:
    """Configuration for the CTF adapter."""

    # Contract addresses
    ctf_address: str = DEFAULT_CTF_EXCHANGE_ADDRESS
    neg_risk_ctf_address: str = DEFAULT_NEG_RISK_CTF_ADDRESS
    usdc_address: str = DEFAULT_USDC_ADDRESS
    ct_address: str = DEFAULT_CT_ADDRESS

    # Gas settings
    gas_limit_merge: int = 300_000
    gas_limit_split: int = 300_000
    gas_limit_approve: int = 100_000
    max_gas_price_gwei: Decimal = Decimal("100")
    gas_price_multiplier: Decimal = Decimal("1.2")  # 20% buffer over estimated

    # USDC has 6 decimals on Polygon
    usdc_decimals: int = 6

    # Max priority fee in Gwei (EIP-1559)
    max_priority_fee_gwei: Decimal = Decimal("30")

    # Transaction confirmation timeout in seconds
    tx_confirmation_timeout_s: float = 120.0

    # Number of confirmations to wait for
    required_confirmations: int = 3

    # Estimated MATIC price in USD for gas cost calculation
    matic_price_usd: Decimal = Decimal("0.50")


class CTFAdapter:
    """Adapter for on-chain CTF merge/split operations on Polygon.

    Provides:
    - ``merge_positions()``: merge YES+NO tokens → collateral (USDC)
    - ``split_position()``: split collateral → YES+NO tokens
    - Gas estimation and abort on spike
    - Proper ABI encoding for the CTF contract

    Usage::

        from web3_infra.rpc_manager import RPCManager

        rpc = RPCManager(endpoints=["https://polygon-rpc.com"])
        await rpc.start()

        adapter = CTFAdapter(
            rpc_manager=rpc,
            private_key="0xabc...",
            sender_address="0x123...",
        )

        result = await adapter.merge_positions(
            condition_id="0xdef...",
            amount=Decimal("100"),
        )
        print(result.tx_hash, result.cost_usd)
    """

    def __init__(
        self,
        rpc_manager: Any,  # RPCManager — typed as Any to avoid circular imports
        private_key: str,
        sender_address: str,
        config: CTFAdapterConfig | None = None,
    ) -> None:
        self._rpc = rpc_manager
        self._private_key = private_key
        self._sender_address = AsyncWeb3.to_checksum_address(sender_address)
        self._config = config or CTFAdapterConfig()

    @property
    def config(self) -> CTFAdapterConfig:
        """Return current configuration (read-only)."""
        return self._config

    # ── Public API ───────────────────────────────────────────────

    async def merge_positions(
        self,
        condition_id: str,
        amount: Decimal,
        neg_risk: bool = False,
    ) -> CTFTxResult:
        """Merge YES+NO tokens back to collateral (USDC).

        Burns equal amounts of YES and NO conditional tokens for a given
        condition and returns the underlying collateral (USDC).

        Parameters
        ----------
        condition_id:
            The condition ID (bytes32 hex string) of the market.
        amount:
            Amount of token pairs to merge (in USDC units, e.g. 100 = $100).
        neg_risk:
            If True, use the neg-risk CTF Exchange address.

        Returns
        -------
        CTFTxResult
            Transaction result with hash, gas used, and cost.

        Raises
        ------
        GasAbortError
            If the current gas price exceeds the configured maximum.
        CTFTransactionError
            If the transaction reverts or fails.
        """
        logger.info(
            "ctf_adapter.merge_positions",
            condition_id=condition_id,
            amount=str(amount),
            neg_risk=neg_risk,
        )

        ctf_address = (
            self._config.neg_risk_ctf_address if neg_risk
            else self._config.ctf_address
        )
        amount_raw = self._to_raw_amount(amount)

        async def _build_and_send(w3: AsyncWeb3) -> CTFTxResult:
            # Check gas price
            await self._check_gas_price(w3)

            # Build contract instance
            contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(ctf_address),
                abi=CTF_ABI,
            )

            # Encode the merge transaction
            tx = await contract.functions.mergePositions(
                AsyncWeb3.to_checksum_address(self._config.usdc_address),
                NULL_PARENT,
                bytes.fromhex(condition_id.replace("0x", "")),
                BINARY_PARTITION,
                amount_raw,
            ).build_transaction(
                await self._base_tx_params(w3, self._config.gas_limit_merge)
            )

            return await self._sign_and_send(w3, tx)

        return await self._rpc.execute(_build_and_send)

    async def split_position(
        self,
        condition_id: str,
        amount: Decimal,
        neg_risk: bool = False,
    ) -> CTFTxResult:
        """Split collateral (USDC) into YES+NO conditional tokens.

        Locks USDC and mints equal amounts of YES and NO conditional
        tokens for the given condition.

        Parameters
        ----------
        condition_id:
            The condition ID (bytes32 hex string) of the market.
        amount:
            Amount of USDC to split (e.g. 100 = $100).
        neg_risk:
            If True, use the neg-risk CTF Exchange address.

        Returns
        -------
        CTFTxResult
            Transaction result with hash, gas used, and cost.

        Raises
        ------
        GasAbortError
            If the current gas price exceeds the configured maximum.
        CTFTransactionError
            If the transaction reverts or fails.
        """
        logger.info(
            "ctf_adapter.split_position",
            condition_id=condition_id,
            amount=str(amount),
            neg_risk=neg_risk,
        )

        ctf_address = (
            self._config.neg_risk_ctf_address if neg_risk
            else self._config.ctf_address
        )
        amount_raw = self._to_raw_amount(amount)

        async def _build_and_send(w3: AsyncWeb3) -> CTFTxResult:
            # Check gas price
            await self._check_gas_price(w3)

            # Ensure USDC approval
            await self._ensure_approval(w3, ctf_address, amount_raw)

            # Build contract instance
            contract = w3.eth.contract(
                address=AsyncWeb3.to_checksum_address(ctf_address),
                abi=CTF_ABI,
            )

            # Encode the split transaction
            tx = await contract.functions.splitPosition(
                AsyncWeb3.to_checksum_address(self._config.usdc_address),
                NULL_PARENT,
                bytes.fromhex(condition_id.replace("0x", "")),
                BINARY_PARTITION,
                amount_raw,
            ).build_transaction(
                await self._base_tx_params(w3, self._config.gas_limit_split)
            )

            return await self._sign_and_send(w3, tx)

        return await self._rpc.execute(_build_and_send)

    async def get_gas_price_gwei(self) -> Decimal:
        """Fetch current gas price from the network.

        Returns
        -------
        Decimal
            Current gas price in Gwei.
        """

        async def _get_price(w3: AsyncWeb3) -> Decimal:
            gas_price_wei = await w3.eth.gas_price
            return Decimal(str(gas_price_wei)) / Decimal("1000000000")

        return await self._rpc.execute(_get_price)

    async def estimate_merge_cost_usd(self) -> Decimal:
        """Estimate the USD cost of a merge transaction at current gas prices.

        Returns
        -------
        Decimal
            Estimated cost in USD.
        """
        gas_gwei = await self.get_gas_price_gwei()
        gas_eth = gas_gwei * Decimal(str(self._config.gas_limit_merge)) / Decimal("1000000000")
        return gas_eth * self._config.matic_price_usd

    async def estimate_split_cost_usd(self) -> Decimal:
        """Estimate the USD cost of a split transaction at current gas prices.

        Returns
        -------
        Decimal
            Estimated cost in USD.
        """
        gas_gwei = await self.get_gas_price_gwei()
        gas_eth = gas_gwei * Decimal(str(self._config.gas_limit_split)) / Decimal("1000000000")
        return gas_eth * self._config.matic_price_usd

    # ── Internals ────────────────────────────────────────────────

    def _to_raw_amount(self, amount: Decimal) -> int:
        """Convert USDC amount to raw integer (6 decimals)."""
        multiplier = 10 ** self._config.usdc_decimals
        return int(amount * multiplier)

    async def _base_tx_params(self, w3: AsyncWeb3, gas_limit: int) -> dict[str, Any]:
        """Build base transaction parameters."""
        nonce = await w3.eth.get_transaction_count(self._sender_address)
        gas_price = await w3.eth.gas_price
        adjusted_gas_price = int(
            gas_price * int(self._config.gas_price_multiplier * 100) // 100
        )

        return {
            "from": self._sender_address,
            "nonce": nonce,
            "gas": gas_limit,
            "gasPrice": adjusted_gas_price,
            "chainId": 137,  # Polygon mainnet
        }

    async def _check_gas_price(self, w3: AsyncWeb3) -> None:
        """Abort if gas price exceeds configured maximum.

        Raises
        ------
        GasAbortError
            If gas price is too high.
        """
        gas_price_wei = await w3.eth.gas_price
        gas_price_gwei = Decimal(str(gas_price_wei)) / Decimal("1000000000")

        if gas_price_gwei > self._config.max_gas_price_gwei:
            raise GasAbortError(
                f"Gas price {gas_price_gwei} Gwei exceeds maximum "
                f"{self._config.max_gas_price_gwei} Gwei"
            )

        logger.debug(
            "ctf_adapter.gas_check_ok",
            gas_price_gwei=str(gas_price_gwei),
            max_gwei=str(self._config.max_gas_price_gwei),
        )

    async def _ensure_approval(
        self,
        w3: AsyncWeb3,
        spender: str,
        amount_raw: int,
    ) -> None:
        """Ensure USDC approval for the given spender if needed."""
        usdc = w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(self._config.usdc_address),
            abi=ERC20_APPROVE_ABI,
        )

        current_allowance = await usdc.functions.allowance(
            self._sender_address,
            AsyncWeb3.to_checksum_address(spender),
        ).call()

        if current_allowance >= amount_raw:
            logger.debug("ctf_adapter.approval_sufficient", allowance=current_allowance)
            return

        # Approve max uint256 for convenience (common pattern)
        max_uint256 = 2**256 - 1
        tx = await usdc.functions.approve(
            AsyncWeb3.to_checksum_address(spender),
            max_uint256,
        ).build_transaction(
            await self._base_tx_params(w3, self._config.gas_limit_approve)
        )

        result = await self._sign_and_send(w3, tx)
        if result.status != TxStatus.CONFIRMED:
            raise CTFTransactionError(
                f"USDC approval failed: {result.error}",
                tx_hash=result.tx_hash,
            )

        logger.info(
            "ctf_adapter.usdc_approved",
            spender=spender,
            tx_hash=result.tx_hash,
        )

    async def _sign_and_send(self, w3: AsyncWeb3, tx: dict[str, Any]) -> CTFTxResult:
        """Sign a transaction and send it, waiting for confirmation.

        Returns
        -------
        CTFTxResult
            Result containing tx hash, gas used, and cost.

        Raises
        ------
        CTFTransactionError
            If the transaction reverts.
        """
        # Sign
        signed = w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash = await w3.eth.send_raw_transaction(signed.raw_transaction)
        tx_hash_hex = tx_hash.hex()

        logger.info(
            "ctf_adapter.tx_sent",
            tx_hash=tx_hash_hex,
        )

        # Wait for confirmation
        try:
            receipt: TxReceipt = await asyncio.wait_for(
                w3.eth.wait_for_transaction_receipt(
                    tx_hash,
                    timeout=self._config.tx_confirmation_timeout_s,
                ),
                timeout=self._config.tx_confirmation_timeout_s + 10,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            logger.error(
                "ctf_adapter.tx_timeout",
                tx_hash=tx_hash_hex,
                error=str(exc),
            )
            return CTFTxResult(
                tx_hash=tx_hash_hex,
                status=TxStatus.FAILED,
                gas_used=0,
                gas_price_gwei=Decimal("0"),
                cost_usd=Decimal("0"),
                block_number=0,
                error=f"Transaction confirmation timeout: {exc}",
            )

        gas_used = receipt.get("gasUsed", 0)
        effective_gas_price = receipt.get("effectiveGasPrice", tx.get("gasPrice", 0))
        gas_price_gwei = Decimal(str(effective_gas_price)) / Decimal("1000000000")
        gas_cost_matic = gas_price_gwei * Decimal(str(gas_used)) / Decimal("1000000000")
        cost_usd = gas_cost_matic * self._config.matic_price_usd

        status = receipt.get("status", 0)
        if status == 1:
            tx_status = TxStatus.CONFIRMED
            logger.info(
                "ctf_adapter.tx_confirmed",
                tx_hash=tx_hash_hex,
                gas_used=gas_used,
                cost_usd=str(cost_usd),
                block=receipt.get("blockNumber", 0),
            )
        else:
            tx_status = TxStatus.REVERTED
            logger.error(
                "ctf_adapter.tx_reverted",
                tx_hash=tx_hash_hex,
                gas_used=gas_used,
            )

        result = CTFTxResult(
            tx_hash=tx_hash_hex,
            status=tx_status,
            gas_used=gas_used,
            gas_price_gwei=gas_price_gwei,
            cost_usd=cost_usd,
            block_number=receipt.get("blockNumber", 0),
            error="Transaction reverted" if tx_status == TxStatus.REVERTED else None,
        )

        if tx_status == TxStatus.REVERTED:
            raise CTFTransactionError(
                "Transaction reverted on-chain",
                tx_hash=tx_hash_hex,
                result=result,
            )

        return result


# ── Exceptions ───────────────────────────────────────────────────────


class GasAbortError(Exception):
    """Raised when gas price exceeds configured maximum."""
    pass


class CTFTransactionError(Exception):
    """Raised when a CTF on-chain transaction fails."""

    def __init__(
        self,
        message: str,
        tx_hash: str | None = None,
        result: CTFTxResult | None = None,
    ) -> None:
        super().__init__(message)
        self.tx_hash = tx_hash
        self.result = result
