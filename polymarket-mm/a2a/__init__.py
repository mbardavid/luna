"""Polymarket MM — A2A (Agent-to-Agent) delegation package.

Provides the TaskSpec schema and CTFDelegate for delegating on-chain
operations (merge/split/bridge) to the Crypto-Sage agent, while the
quant engine retains fast-path CLOB operations (order signing, submission).
"""

from .ctf_delegate import CTFDelegate
from .task_spec import TaskSpec

__all__ = [
    "CTFDelegate",
    "TaskSpec",
]
