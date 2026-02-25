"""EIP712Signer — off-main-thread EIP-712 signing for async order submission.

Signing is CPU-bound (elliptic-curve math), so we offload it to a
``ProcessPoolExecutor`` to avoid blocking the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import structlog

logger = structlog.get_logger("web3_infra.eip712_signer")


@dataclass(frozen=True)
class SignedOrder:
    """Result of signing an order with EIP-712."""

    order_hash: str
    signature: str
    order_data: dict[str, Any]


# ── Module-level signing function (must be picklable for multiprocessing) ──


def _sign_order_sync(
    order_data: dict[str, Any],
    private_key: str,
    domain_separator: str,
) -> SignedOrder:
    """Synchronous signing function executed in a worker process.

    In production this would use ``eth_account`` / ``py_ecc`` for real
    EIP-712 typed-data signing.  This implementation provides a
    structurally correct placeholder using HMAC-SHA256 so that the
    async infrastructure can be fully tested without heavy crypto deps.
    """
    # Deterministic order hash from canonical data representation
    canonical = _canonical_repr(order_data, domain_separator)
    order_hash = hashlib.sha256(canonical.encode()).hexdigest()

    # HMAC signature (placeholder for real secp256k1)
    sig = hmac.new(
        private_key.encode(),
        order_hash.encode(),
        hashlib.sha256,
    ).hexdigest()

    return SignedOrder(
        order_hash=f"0x{order_hash}",
        signature=f"0x{sig}",
        order_data=dict(order_data),
    )


def _canonical_repr(order_data: dict[str, Any], domain: str) -> str:
    """Produce a deterministic string from order data + domain."""
    parts = [domain]
    for key in sorted(order_data.keys()):
        parts.append(f"{key}={order_data[key]}")
    return "|".join(parts)


# ── Async signer class ──────────────────────────────────────────────


class EIP712Signer:
    """Async-safe EIP-712 order signer backed by a process pool.

    Parameters
    ----------
    private_key:
        Hex-encoded private key (without ``0x`` prefix is fine).
    domain_separator:
        EIP-712 domain separator string.
    max_workers:
        Number of processes in the signing pool.  Defaults to 2.
    """

    def __init__(
        self,
        private_key: str,
        domain_separator: str = "PolymarketCTFExchange",
        max_workers: int = 2,
    ) -> None:
        self._private_key = private_key
        self._domain_separator = domain_separator
        self._max_workers = max_workers
        self._pool: ProcessPoolExecutor | None = None

    # ── Lifecycle ────────────────────────────────────────────────

    def start(self) -> None:
        """Start the process pool.  Idempotent."""
        if self._pool is None:
            self._pool = ProcessPoolExecutor(max_workers=self._max_workers)
            logger.info(
                "eip712_signer.started",
                max_workers=self._max_workers,
            )

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the process pool."""
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            self._pool = None
            logger.info("eip712_signer.shutdown")

    # ── Signing ──────────────────────────────────────────────────

    async def sign_order(self, order_data: dict[str, Any]) -> SignedOrder:
        """Sign order data asynchronously (offloaded to process pool).

        Parameters
        ----------
        order_data:
            Dict with order fields (``token_id``, ``price``, ``size``,
            ``side``, ``maker``, ``nonce``, etc.).  All values must be
            picklable (use ``str`` for ``Decimal``/``UUID``).

        Returns
        -------
        SignedOrder
            Contains ``order_hash``, ``signature``, and the original data.

        Raises
        ------
        RuntimeError
            If the signer has not been started.
        """
        if self._pool is None:
            raise RuntimeError(
                "EIP712Signer not started — call start() first"
            )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            self._pool,
            _sign_order_sync,
            order_data,
            self._private_key,
            self._domain_separator,
        )

        logger.debug(
            "eip712_signer.signed",
            order_hash=result.order_hash,
        )
        return result

    # ── Context manager ──────────────────────────────────────────

    async def __aenter__(self) -> EIP712Signer:
        self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.shutdown(wait=True)
