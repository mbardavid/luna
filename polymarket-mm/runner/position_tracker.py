from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import structlog

logger = structlog.get_logger("runner.position_tracker")

_CTF_CONTRACT = os.environ.get(
    "POLYMARKET_CTF_CONTRACT",
    "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
).lower()
_BLOCKSCOUT_BASE_URL = os.environ.get(
    "POLYMARKET_BLOCKSCOUT_URL",
    "https://polygon.blockscout.com/api/v2",
).rstrip("/")
_MICRO_UNITS = Decimal("1000000")
_ZERO = Decimal("0")
_COMPLEMENT_TOLERANCE = Decimal("0.000001")
_DEFAULT_RPC_URLS = [
    os.environ.get("POLYMARKET_RPC_PRIMARY", "https://polygon-bor-rpc.publicnode.com"),
    os.environ.get("POLYMARKET_RPC_SECONDARY", "https://polygon-rpc.com"),
]
_ERC1155_BALANCE_ABI = [
    {
        "inputs": [
            {"internalType": "address", "name": "account", "type": "address"},
            {"internalType": "uint256", "name": "id", "type": "uint256"},
        ],
        "name": "balanceOf",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


@dataclass(slots=True)
class PositionTrackerSnapshot:
    source: str
    market_positions: dict[str, dict[str, Any]] = field(default_factory=dict)
    discovered_positions: list[dict[str, Any]] = field(default_factory=list)
    unmatched_positions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class PositionTracker:
    def __init__(
        self,
        rest_client: Any,
        market_configs: list[Any],
        *,
        blockscout_base_url: str = _BLOCKSCOUT_BASE_URL,
        ctf_contract: str = _CTF_CONTRACT,
        rpc_urls: list[str] | None = None,
        timeout_s: float = 12.0,
    ) -> None:
        self._rest = rest_client
        self._markets = market_configs
        self._blockscout_base_url = blockscout_base_url.rstrip("/")
        self._ctf_contract = ctf_contract.lower()
        self._rpc_urls = [url for url in (rpc_urls or _DEFAULT_RPC_URLS) if url]
        self._timeout_s = timeout_s
        self._token_index: dict[str, dict[str, str]] = {}

        for market in self._markets:
            self._token_index[str(market.token_id_yes)] = {
                "market_id": market.market_id,
                "side": "yes",
                "description": getattr(market, "description", market.market_id),
            }
            self._token_index[str(market.token_id_no)] = {
                "market_id": market.market_id,
                "side": "no",
                "description": getattr(market, "description", market.market_id),
            }

    async def collect(self, wallet_address: str) -> PositionTrackerSnapshot:
        warnings: list[str] = []
        source = "blockscout"

        try:
            holdings = await self._fetch_blockscout_holdings(wallet_address)
        except Exception as exc:
            source = "rpc"
            warnings.append(f"blockscout_unavailable:{exc}")
            logger.warning("position_tracker.blockscout_failed", error=str(exc))
            holdings = await asyncio.to_thread(self._fetch_rpc_holdings, wallet_address)

        market_positions = self._empty_market_positions()
        token_ids_for_price = set(holdings.keys())
        for token_id in list(holdings.keys()):
            mapping = self._token_index.get(token_id)
            if not mapping:
                continue
            market_id = mapping["market_id"]
            market_state = market_positions.get(market_id)
            if not market_state:
                continue
            token_ids_for_price.add(str(market_state["token_id_yes"]))
            token_ids_for_price.add(str(market_state["token_id_no"]))

        prices = await self._fetch_prices(sorted(token_ids_for_price))
        snapshot = PositionTrackerSnapshot(source=source, market_positions=market_positions)

        for token_id, shares in sorted(holdings.items()):
            if shares <= _ZERO:
                continue
            mapping = self._token_index.get(token_id)
            price = prices.get(token_id, _ZERO)
            value_usd = shares * price if price > _ZERO else _ZERO
            position_row = {
                "token_id": token_id,
                "shares": shares,
                "price": price,
                "value_usd": value_usd,
                "source": source,
                "market_id": mapping.get("market_id") if mapping else None,
                "outcome": mapping.get("side", "unknown").upper() if mapping else "UNKNOWN",
                "description": mapping.get("description") if mapping else None,
            }
            snapshot.discovered_positions.append(position_row)

            if mapping:
                market_state = snapshot.market_positions[mapping["market_id"]]
                side = mapping["side"]
                market_state[f"{side}_shares"] = shares
                market_state[f"{side}_price"] = price
                market_state[f"{side}_value_usd"] = value_usd
            else:
                snapshot.unmatched_positions.append(position_row)

        for market_id, market_state in snapshot.market_positions.items():
            yes_token = str(market_state["token_id_yes"])
            no_token = str(market_state["token_id_no"])
            market_state["yes_price"] = prices.get(yes_token, market_state["yes_price"])
            market_state["no_price"] = prices.get(no_token, market_state["no_price"])
            market_state["yes_value_usd"] = market_state["yes_shares"] * market_state["yes_price"]
            market_state["no_value_usd"] = market_state["no_shares"] * market_state["no_price"]

            if market_state["yes_price"] > _ZERO and market_state["no_price"] > _ZERO:
                complement_sum = market_state["yes_price"] + market_state["no_price"]
                if abs(complement_sum - Decimal("1")) > _COMPLEMENT_TOLERANCE:
                    snapshot.warnings.append(
                        f"market={market_id}: complement price mismatch yes+no={complement_sum}"
                    )

        snapshot.warnings.extend(warnings)
        return snapshot

    async def _fetch_blockscout_holdings(self, wallet_address: str) -> dict[str, Decimal]:
        holdings: dict[str, Decimal] = {}
        url = f"{self._blockscout_base_url}/addresses/{wallet_address}/tokens"
        params: dict[str, Any] = {"type": "ERC-1155"}
        timeout = httpx.Timeout(self._timeout_s, connect=min(5.0, self._timeout_s))

        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("items", []) if isinstance(payload, dict) else []

                for item in items:
                    token = item.get("token") or {}
                    address_hash = str(token.get("address_hash", "")).lower()
                    if address_hash != self._ctf_contract:
                        continue
                    token_id = str(item.get("token_id", "")).strip()
                    if not token_id:
                        continue
                    raw_balance = Decimal(str(item.get("value", "0")))
                    shares = raw_balance / _MICRO_UNITS
                    if shares <= _ZERO:
                        continue
                    holdings[token_id] = shares

                next_page = payload.get("next_page_params") if isinstance(payload, dict) else None
                if not next_page:
                    break
                params = {"type": "ERC-1155"}
                params.update({k: str(v) for k, v in next_page.items() if v not in (None, "")})

        logger.info(
            "position_tracker.blockscout_holdings",
            wallet_address=wallet_address,
            positions=len(holdings),
        )
        return holdings

    def _fetch_rpc_holdings(self, wallet_address: str) -> dict[str, Decimal]:
        if not self._rpc_urls:
            raise RuntimeError("no_rpc_urls_configured")

        try:
            from web3 import Web3
        except Exception as exc:
            raise RuntimeError(f"web3_unavailable:{exc}") from exc

        checksum_wallet = Web3.to_checksum_address(wallet_address)
        last_error: Exception | None = None

        for rpc_url in self._rpc_urls:
            try:
                provider = Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": self._timeout_s})
                web3 = Web3(provider)
                contract = web3.eth.contract(
                    address=Web3.to_checksum_address(self._ctf_contract),
                    abi=_ERC1155_BALANCE_ABI,
                )
                holdings: dict[str, Decimal] = {}
                for token_id in sorted(self._token_index.keys()):
                    raw_balance = contract.functions.balanceOf(
                        checksum_wallet,
                        int(token_id),
                    ).call()
                    shares = Decimal(str(raw_balance)) / _MICRO_UNITS
                    if shares > _ZERO:
                        holdings[token_id] = shares
                logger.info(
                    "position_tracker.rpc_holdings",
                    wallet_address=wallet_address,
                    rpc_url=rpc_url,
                    positions=len(holdings),
                )
                return holdings
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "position_tracker.rpc_failed",
                    rpc_url=rpc_url,
                    error=str(exc),
                )

        raise RuntimeError(f"rpc_fallback_failed:{last_error}")

    async def _fetch_prices(self, token_ids: list[str]) -> dict[str, Decimal]:
        if not token_ids:
            return {}

        async def _one(token_id: str) -> tuple[str, Decimal]:
            try:
                price = await self._rest.get_price(token_id, side="sell")
                return token_id, Decimal(str(price))
            except Exception as exc:
                logger.warning("position_tracker.price_failed", token_id=token_id, error=str(exc))
                return token_id, _ZERO

        pairs = await asyncio.gather(*[_one(token_id) for token_id in token_ids])
        return {token_id: price for token_id, price in pairs}

    def _empty_market_positions(self) -> dict[str, dict[str, Any]]:
        positions: dict[str, dict[str, Any]] = {}
        for market in self._markets:
            positions[market.market_id] = {
                "yes_shares": _ZERO,
                "no_shares": _ZERO,
                "token_id_yes": str(market.token_id_yes),
                "token_id_no": str(market.token_id_no),
                "yes_price": _ZERO,
                "no_price": _ZERO,
                "yes_value_usd": _ZERO,
                "no_value_usd": _ZERO,
                "description": getattr(market, "description", market.market_id),
            }
        return positions
