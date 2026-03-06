"""Wallet inventory discovery and flatten helpers for Polymarket CTF positions."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import httpx

from data.public_clob_quote_client import PublicClobQuoteClient
from data.rest_client import CLOBRestClient
from execution.ctf_merge import CTFMerger
from web3_infra.ctf_adapter import CTFAdapter, CTFAdapterConfig
from web3_infra.rpc_manager import RPCManager

PROJECT_ROOT = Path("/home/openclaw/.openclaw/workspace/polymarket-mm")
SYSTEMD_POLYMARKET_ENV = Path("/home/openclaw/.config/systemd/user/openclaw-gateway.service.d/polymarket-env.conf")

DEFAULT_WALLET_ADDRESS = "0xa1464EB4f86958823b0f24B3CF5Ac2b8134D6bb1"
DEFAULT_BLOCKSCOUT = "https://polygon.blockscout.com/api/v2"
DEFAULT_CTF_CONTRACT = "0x4d97dcd97ec945f40cf65f87097ace5ea0476045"
DEFAULT_RPC_URLS = (
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon-rpc.com",
)
MICRO = Decimal("1000000")
Q6 = Decimal("0.000001")
Q4 = Decimal("0.0001")
Q2 = Decimal("0.01")


def q2(value: Decimal) -> Decimal:
    return value.quantize(Q2, rounding=ROUND_HALF_UP)


def q4(value: Decimal) -> Decimal:
    return value.quantize(Q4, rounding=ROUND_HALF_UP)


def q6_down(value: Decimal) -> Decimal:
    return value.quantize(Q6, rounding=ROUND_DOWN)


def load_runtime_env(
    env_paths: list[Path] | None = None,
) -> None:
    paths = env_paths or [
        SYSTEMD_POLYMARKET_ENV,
        PROJECT_ROOT / ".env",
    ]
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line == "[Service]":
                continue
            if line.startswith("Environment="):
                line = line[len("Environment="):].strip().strip('"')
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip().strip('"')
            value = value.strip().strip('"')
            if key and not os.environ.get(key):
                os.environ[key] = value
    if os.environ.get("POLYMARKET_SECRET") and not os.environ.get("POLYMARKET_API_SECRET"):
        os.environ["POLYMARKET_API_SECRET"] = os.environ["POLYMARKET_SECRET"]
    if os.environ.get("POLYMARKET_PRIVATE_KEY") and not os.environ.get("POLYGON_PRIVATE_KEY"):
        os.environ["POLYGON_PRIVATE_KEY"] = os.environ["POLYMARKET_PRIVATE_KEY"]


@dataclass(slots=True)
class TokenPosition:
    condition_id: str
    question: str
    outcome: str
    token_id: str
    token_index: int
    token_id_yes: str | None
    token_id_no: str | None
    shares: Decimal
    neg_risk: bool
    active: bool = True
    closed: bool = False
    sell_price: Decimal = Decimal("0")
    last_trade_price: Decimal = Decimal("0")
    mark_value_usdc: Decimal = Decimal("0")

    def to_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "question": self.question,
            "outcome": self.outcome,
            "token_id": self.token_id,
            "token_index": self.token_index,
            "token_id_yes": self.token_id_yes,
            "token_id_no": self.token_id_no,
            "shares": str(self.shares),
            "neg_risk": self.neg_risk,
            "active": self.active,
            "closed": self.closed,
            "sell_price": str(self.sell_price),
            "last_trade_price": str(self.last_trade_price),
            "mark_value_usdc": str(self.mark_value_usdc),
        }


def _parse_json_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw or "[]")
    except Exception:
        return []


async def build_rest_client(
    *,
    proxy_url: str | None = None,
    rate_limit_rps: float = 5.0,
) -> CLOBRestClient:
    load_runtime_env()
    client = CLOBRestClient(
        private_key=os.environ.get("POLYGON_PRIVATE_KEY", "") or os.environ.get("POLYMARKET_PRIVATE_KEY", ""),
        api_key=os.environ.get("POLYMARKET_API_KEY", ""),
        api_secret=os.environ.get("POLYMARKET_API_SECRET", "") or os.environ.get("POLYMARKET_SECRET", ""),
        api_passphrase=os.environ.get("POLYMARKET_PASSPHRASE", ""),
        proxy_url=proxy_url or os.environ.get("POLYMARKET_PROXY", "socks5://127.0.0.1:9050"),
        rate_limit_rps=rate_limit_rps,
    )
    await client.connect()
    return client


async def build_merger(rest_client: CLOBRestClient) -> tuple[RPCManager, CTFMerger]:
    endpoints = [
        os.environ.get("POLYMARKET_RPC_PRIMARY", DEFAULT_RPC_URLS[0]),
        os.environ.get("POLYMARKET_RPC_SECONDARY", DEFAULT_RPC_URLS[1]),
    ]
    rpc = RPCManager([endpoint for endpoint in endpoints if endpoint])
    await rpc.start()
    adapter = CTFAdapter(
        rpc_manager=rpc,
        private_key=os.environ.get("POLYGON_PRIVATE_KEY", "") or os.environ.get("POLYMARKET_PRIVATE_KEY", ""),
        sender_address=str(rest_client.clob_client.get_address()),
        config=CTFAdapterConfig(max_gas_cost_usd=Decimal("0.25")),
    )
    return rpc, CTFMerger(ctf_adapter=adapter)


async def fetch_blockscout_holdings(
    wallet_address: str = DEFAULT_WALLET_ADDRESS,
    *,
    blockscout_base_url: str = DEFAULT_BLOCKSCOUT,
    ctf_contract: str = DEFAULT_CTF_CONTRACT,
) -> list[dict[str, Any]]:
    params: dict[str, str] = {"type": "ERC-1155"}
    rows: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        while True:
            response = await client.get(f"{blockscout_base_url}/addresses/{wallet_address}/tokens", params=params)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("items", []):
                token = item.get("token") or {}
                if str(token.get("address_hash", "")).lower() != ctf_contract:
                    continue
                shares = Decimal(str(item.get("value", "0"))) / MICRO
                if shares <= 0:
                    continue
                rows.append({
                    "token_id": str(item.get("token_id")),
                    "shares": q6_down(shares),
                })
            next_page = payload.get("next_page_params")
            if not next_page:
                break
            params = {"type": "ERC-1155", **{k: str(v) for k, v in next_page.items() if v not in (None, "")}}
    return rows


async def fetch_gamma_meta(token_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        response = await client.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": "5", "clob_token_ids": token_id},
        )
        response.raise_for_status()
        rows = response.json()
    if not rows:
        return {}
    row = rows[0]
    ids = [str(item) for item in _parse_json_list(row.get("clobTokenIds"))]
    outcomes = _parse_json_list(row.get("outcomes"))
    idx = ids.index(str(token_id)) if str(token_id) in ids else 0
    return {
        "condition_id": row.get("conditionId") or row.get("condition_id") or token_id,
        "question": row.get("question") or token_id,
        "outcome": str(outcomes[idx]) if idx < len(outcomes) else ("Yes" if idx == 0 else "No"),
        "token_index": idx,
        "token_id_yes": ids[0] if len(ids) > 0 else None,
        "token_id_no": ids[1] if len(ids) > 1 else None,
        "neg_risk": bool(row.get("negRisk")),
        "active": bool(row.get("active", True)),
        "closed": bool(row.get("closed", False)),
    }


async def discover_positions(
    *,
    wallet_address: str = DEFAULT_WALLET_ADDRESS,
) -> list[TokenPosition]:
    holdings = await fetch_blockscout_holdings(wallet_address)
    positions: list[TokenPosition] = []
    for holding in holdings:
        meta = await fetch_gamma_meta(holding["token_id"])
        positions.append(
            TokenPosition(
                condition_id=str(meta.get("condition_id") or holding["token_id"]),
                question=str(meta.get("question") or holding["token_id"]),
                outcome=str(meta.get("outcome") or "UNKNOWN"),
                token_id=str(holding["token_id"]),
                token_index=int(meta.get("token_index") or 0),
                token_id_yes=meta.get("token_id_yes"),
                token_id_no=meta.get("token_id_no"),
                shares=q6_down(holding["shares"]),
                neg_risk=bool(meta.get("neg_risk", False)),
                active=bool(meta.get("active", True)),
                closed=bool(meta.get("closed", False)),
            )
        )
    return positions


async def _fetch_private_token_balances(
    rest_client: CLOBRestClient,
    token_ids: list[str],
) -> dict[str, Decimal]:
    balances: dict[str, Decimal] = {}
    for token_id in token_ids:
        try:
            payload = await rest_client.get_balance_allowance("CONDITIONAL", token_id=token_id)
            balance = Decimal(str(payload.get("balance", "0"))) / MICRO
            balances[token_id] = q6_down(balance)
        except Exception:
            continue
    return balances


async def _enrich_prices(
    positions: list[TokenPosition],
    *,
    quote_client: PublicClobQuoteClient | None = None,
) -> None:
    quote_client = quote_client or PublicClobQuoteClient()

    async def _one(position: TokenPosition) -> None:
        try:
            executable = await quote_client.get_executable_quote(position.token_id, action="sell_token")
            position.sell_price = executable.price
        except Exception:
            position.sell_price = Decimal("0")
        try:
            position.last_trade_price = await quote_client.get_last_trade_price(position.token_id)
        except Exception:
            position.last_trade_price = Decimal("0")
        reference_price = position.sell_price if position.sell_price > 0 else position.last_trade_price
        position.mark_value_usdc = q4(position.shares * reference_price)

    await asyncio.gather(*[_one(position) for position in positions])


def _group_positions(positions: list[TokenPosition]) -> dict[str, list[TokenPosition]]:
    grouped: dict[str, list[TokenPosition]] = {}
    for position in positions:
        grouped.setdefault(position.condition_id, []).append(position)
    for rows in grouped.values():
        rows.sort(key=lambda item: (item.token_index, item.token_id))
    return grouped


def _position_label(position: TokenPosition) -> str:
    return f"{position.question} [{position.outcome}]"


async def collect_wallet_state(
    rest_client: CLOBRestClient,
    *,
    wallet_address: str = DEFAULT_WALLET_ADDRESS,
    dust_threshold_shares: Decimal = Decimal("5"),
    dust_threshold_usdc: Decimal = Decimal("1"),
    quote_client: PublicClobQuoteClient | None = None,
) -> dict[str, Any]:
    quote_client = quote_client or PublicClobQuoteClient()
    positions = await discover_positions(wallet_address=wallet_address)
    if positions:
        private_balances = await _fetch_private_token_balances(rest_client, [position.token_id for position in positions])
        for position in positions:
            if position.token_id in private_balances:
                position.shares = private_balances[position.token_id]
    await _enrich_prices(positions, quote_client=quote_client)

    balance_info = await rest_client.get_balance_allowance("COLLATERAL")
    free_collateral_usdc = q6_down(Decimal(str(balance_info.get("balance", "0"))) / MICRO)
    grouped = _group_positions(positions)

    recoverable_positions: list[dict[str, Any]] = []
    dust_positions: list[dict[str, Any]] = []
    unrecoverable_positions: list[dict[str, Any]] = []
    market_summaries: list[dict[str, Any]] = []

    recoverable_inventory_usdc = Decimal("0")
    dust_inventory_usdc = Decimal("0")
    unrecoverable_inventory_usdc = Decimal("0")

    for condition_id, rows in grouped.items():
        yes_row = next((row for row in rows if row.token_index == 0), None)
        no_row = next((row for row in rows if row.token_index == 1), None)
        mergeable_pairs = q6_down(min(yes_row.shares if yes_row else Decimal("0"), no_row.shares if no_row else Decimal("0")))
        merge_value = q4(mergeable_pairs)
        market_summary = {
            "condition_id": condition_id,
            "question": rows[0].question,
            "neg_risk": rows[0].neg_risk,
            "mergeable_pairs": str(mergeable_pairs),
            "mergeable_value_usdc": str(merge_value),
            "positions": [row.to_dict() for row in rows],
        }

        if mergeable_pairs > 0:
            target = (
                recoverable_positions
                if mergeable_pairs >= dust_threshold_shares or merge_value >= dust_threshold_usdc
                else dust_positions
            )
            target.append({
                "kind": "merge_pair",
                "condition_id": condition_id,
                "question": rows[0].question,
                "shares": str(mergeable_pairs),
                "estimated_value_usdc": str(merge_value),
                "neg_risk": rows[0].neg_risk,
            })
            if merge_value >= dust_threshold_usdc:
                recoverable_inventory_usdc += merge_value
            else:
                dust_inventory_usdc += merge_value

        for row in rows:
            residual_shares = q6_down(row.shares - mergeable_pairs)
            if residual_shares <= 0:
                continue
            value = q4(residual_shares * row.sell_price)
            position_entry = {
                "kind": "sell_token",
                "condition_id": row.condition_id,
                "question": row.question,
                "outcome": row.outcome,
                "token_id": row.token_id,
                "shares": str(residual_shares),
                "sell_price": str(row.sell_price),
                "last_trade_price": str(row.last_trade_price),
                "estimated_value_usdc": str(value),
            }
            if residual_shares < dust_threshold_shares:
                dust_positions.append(position_entry)
                dust_inventory_usdc += value
            elif row.sell_price > 0:
                recoverable_positions.append(position_entry)
                recoverable_inventory_usdc += value
            else:
                unrecoverable_positions.append(position_entry)
                unrecoverable_inventory_usdc += value
        market_summaries.append(market_summary)

    total_wallet_equity_usdc = q4(
        free_collateral_usdc
        + recoverable_inventory_usdc
        + dust_inventory_usdc
        + unrecoverable_inventory_usdc
    )
    return {
        "wallet": wallet_address,
        "free_collateral_usdc": str(free_collateral_usdc),
        "recoverable_inventory_usdc": str(q4(recoverable_inventory_usdc)),
        "dust_inventory_usdc": str(q4(dust_inventory_usdc)),
        "unrecoverable_inventory_usdc": str(q4(unrecoverable_inventory_usdc)),
        "total_wallet_equity_usdc": str(total_wallet_equity_usdc),
        "recoverable_positions": recoverable_positions,
        "dust_positions": dust_positions,
        "unrecoverable_without_new_trade": unrecoverable_positions,
        "market_positions": market_summaries,
        "dust_threshold_shares": str(dust_threshold_shares),
        "dust_threshold_usdc": str(dust_threshold_usdc),
    }


async def flatten_positions(
    *,
    execute: bool,
    report_path: Path,
    wallet_address: str = DEFAULT_WALLET_ADDRESS,
    dust_threshold_shares: Decimal = Decimal("5"),
    dust_threshold_usdc: Decimal = Decimal("1"),
) -> dict[str, Any]:
    load_runtime_env()
    rest_client = await build_rest_client()
    quote_client = PublicClobQuoteClient()
    rpc: RPCManager | None = None
    merger: CTFMerger | None = None
    executed_actions: list[dict[str, Any]] = []

    try:
        before_state = await collect_wallet_state(
            rest_client,
            wallet_address=wallet_address,
            dust_threshold_shares=dust_threshold_shares,
            dust_threshold_usdc=dust_threshold_usdc,
            quote_client=quote_client,
        )

        if execute:
            rpc, merger = await build_merger(rest_client)
            for item in before_state["recoverable_positions"]:
                if item["kind"] != "merge_pair":
                    continue
                result = await merger.merge_positions(
                    condition_id=item["condition_id"],
                    amount=Decimal(item["shares"]),
                    neg_risk=bool(item.get("neg_risk", False)),
                )
                executed_actions.append({
                    "type": "merge",
                    **result.to_dict(),
                })

            mid_state = await collect_wallet_state(
                rest_client,
                wallet_address=wallet_address,
                dust_threshold_shares=dust_threshold_shares,
                dust_threshold_usdc=dust_threshold_usdc,
                quote_client=quote_client,
            )

            for item in mid_state["recoverable_positions"]:
                if item["kind"] != "sell_token":
                    continue
                response = await rest_client.create_and_post_order(
                    token_id=item["token_id"],
                    price=float(Decimal(item["sell_price"])),
                    size=float(Decimal(item["shares"])),
                    side="SELL",
                    order_type="FOK",
                )
                executed_actions.append({
                    "type": "sell",
                    "condition_id": item["condition_id"],
                    "question": item["question"],
                    "outcome": item["outcome"],
                    "token_id": item["token_id"],
                    "shares": item["shares"],
                    "price": item["sell_price"],
                    "response": response,
                })

        after_state = await collect_wallet_state(
            rest_client,
            wallet_address=wallet_address,
            dust_threshold_shares=dust_threshold_shares,
            dust_threshold_usdc=dust_threshold_usdc,
            quote_client=quote_client,
        )

        report = {
            "executed": execute,
            "wallet_state_before": before_state,
            "wallet_state_after": after_state,
            "executed_actions": executed_actions,
            "free_collateral_usdc": after_state["free_collateral_usdc"],
            "residual_positions": after_state["dust_positions"] + after_state["unrecoverable_without_new_trade"],
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        return report
    finally:
        if rpc is not None:
            await rpc.stop()
        await rest_client.disconnect()
