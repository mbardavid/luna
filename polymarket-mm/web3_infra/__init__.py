"""Polymarket MM — web3_infra package.

Post-refactor: only fast-path components remain here.
- EIP712Signer: off-thread EIP-712 signing for CLOB orders (fast-path)

On-chain operations (merge/split) are now delegated via A2A.
See ``a2a/`` package for CTFDelegate and TaskSpec.
"""

from .eip712_signer import EIP712Signer, SignedOrder

__all__ = [
    "EIP712Signer",
    "SignedOrder",
]
