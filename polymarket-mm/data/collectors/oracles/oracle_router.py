"""OracleRouter — dispatches oracle queries by market type.

Routes:
- CRYPTO_* → CryptoOracleWS (Binance etc.)
- Everything else → CLOBSentimentCollector (volume/momentum from CLOB itself)

Extensible: add new oracles via ``register_oracle()``.
"""

from __future__ import annotations

from typing import Any, Protocol

import structlog

from core.event_bus import EventBus
from models.market_state import MarketType

logger = structlog.get_logger("data.collectors.oracles.oracle_router")


class OracleSource(Protocol):
    """Protocol that all oracle sources must satisfy."""

    async def start(self) -> None:
        """Start the oracle data source."""
        ...

    async def stop(self) -> None:
        """Stop the oracle data source."""
        ...

    async def get_delta(self, token_id: str) -> float:
        """Return the current oracle delta for the given token."""
        ...


class OracleRouter:
    """Routes oracle queries to the appropriate data source based on market type.

    Parameters
    ----------
    event_bus:
        EventBus to publish ``oracle_delta`` events.

    Usage::

        router = OracleRouter(event_bus)
        router.register_oracle(MarketType.CRYPTO_5M, crypto_ws)
        router.register_oracle(MarketType.SPORTS, sentiment)
        await router.start()
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._oracles: dict[MarketType, OracleSource] = {}
        self._default_oracle: OracleSource | None = None

    def register_oracle(
        self,
        market_type: MarketType,
        oracle: OracleSource,
    ) -> None:
        """Register an oracle source for a specific market type.

        Parameters
        ----------
        market_type:
            The ``MarketType`` this oracle handles.
        oracle:
            An object implementing the ``OracleSource`` protocol.
        """
        self._oracles[market_type] = oracle
        logger.info(
            "oracle_router.registered",
            market_type=market_type.value,
            oracle=type(oracle).__name__,
        )

    def set_default_oracle(self, oracle: OracleSource) -> None:
        """Set the fallback oracle used when no specific one is registered."""
        self._default_oracle = oracle
        logger.info(
            "oracle_router.default_set",
            oracle=type(oracle).__name__,
        )

    def get_oracle(self, market_type: MarketType) -> OracleSource | None:
        """Return the oracle for the given market type, or the default."""
        return self._oracles.get(market_type, self._default_oracle)

    async def get_delta(self, market_type: MarketType, token_id: str) -> float:
        """Get oracle delta for a market, dispatching to the right source.

        Returns 0.0 if no oracle is available.
        """
        oracle = self.get_oracle(market_type)
        if oracle is None:
            logger.debug(
                "oracle_router.no_oracle",
                market_type=market_type.value,
                token_id=token_id,
            )
            return 0.0

        delta = await oracle.get_delta(token_id)

        # Publish the delta to the EventBus
        await self._event_bus.publish(
            topic="oracle_delta",
            payload={
                "market_type": market_type.value,
                "token_id": token_id,
                "delta": delta,
            },
        )

        return delta

    async def start(self) -> None:
        """Start all registered oracles."""
        for mtype, oracle in self._oracles.items():
            try:
                await oracle.start()
                logger.info("oracle_router.oracle_started", market_type=mtype.value)
            except Exception as exc:
                logger.error(
                    "oracle_router.oracle_start_failed",
                    market_type=mtype.value,
                    error=str(exc),
                )

        if self._default_oracle:
            try:
                await self._default_oracle.start()
                logger.info("oracle_router.default_oracle_started")
            except Exception as exc:
                logger.error(
                    "oracle_router.default_oracle_start_failed",
                    error=str(exc),
                )

    async def stop(self) -> None:
        """Stop all registered oracles."""
        for mtype, oracle in self._oracles.items():
            try:
                await oracle.stop()
            except Exception as exc:
                logger.warning(
                    "oracle_router.oracle_stop_failed",
                    market_type=mtype.value,
                    error=str(exc),
                )

        if self._default_oracle:
            try:
                await self._default_oracle.stop()
            except Exception:
                pass

        logger.info("oracle_router.stopped")

    @property
    def registered_types(self) -> list[MarketType]:
        """Return list of market types with registered oracles."""
        return list(self._oracles.keys())
