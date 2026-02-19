#!/usr/bin/env python3
"""Bridge entre Node e hyperliquid-python-sdk para assinatura/execução de ações live.

Entrada: JSON via stdin
Saída: JSON via stdout
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional


def _emit(payload: Dict[str, Any], exit_code: int = 0) -> None:
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.exit(exit_code)


def _fail(code: str, message: str, details: Optional[Dict[str, Any]] = None, exit_code: int = 1) -> None:
    _emit(
        {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "details": details or {},
            },
        },
        exit_code=exit_code,
    )


def _require_dict(obj: Any, field: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        _fail("HL_BRIDGE_INPUT_INVALID", f"Campo {field} deve ser objeto", {"field": field})
    return obj


def _require_str(obj: Dict[str, Any], field: str) -> str:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        _fail("HL_BRIDGE_INPUT_INVALID", f"Campo {field} é obrigatório", {"field": field})
    return value.strip()


def _optional_str(obj: Dict[str, Any], field: str) -> Optional[str]:
    value = obj.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail("HL_BRIDGE_INPUT_INVALID", f"Campo {field} deve ser string", {"field": field})
    return value.strip()


def _optional_int(obj: Dict[str, Any], field: str) -> Optional[int]:
    value = obj.get(field)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        _fail("HL_BRIDGE_INPUT_INVALID", f"Campo {field} deve ser inteiro", {"field": field, "value": value})


def _optional_float(obj: Dict[str, Any], field: str) -> Optional[float]:
    value = obj.get(field)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        _fail("HL_BRIDGE_INPUT_INVALID", f"Campo {field} deve ser número", {"field": field, "value": value})


def _normalize_private_key(raw_key: str) -> str:
    return raw_key if raw_key.startswith("0x") else f"0x{raw_key}"


def _build_order_action(payload: Dict[str, Any], info, exchange, cloid_cls):
    order = _require_dict(payload.get("order"), "order")

    market = _require_str(order, "market")
    side = _require_str(order, "side").lower()
    if side not in ["buy", "sell"]:
        _fail("HL_BRIDGE_INPUT_INVALID", "side inválido", {"side": side})

    sz = _optional_float(order, "size")
    if sz is None or sz <= 0:
        _fail("HL_BRIDGE_INPUT_INVALID", "size deve ser > 0", {"size": order.get("size")})

    reduce_only = bool(order.get("reduceOnly", False))
    cloid_raw = _optional_str(order, "cloid")

    price_raw = order.get("price")
    tif = _optional_str(order, "tif")

    is_buy = side == "buy"

    if isinstance(price_raw, str) and price_raw.lower() == "market":
        slippage_bps = _optional_float(order, "slippageBps")
        if slippage_bps is None:
            slippage_bps = 50.0
        if slippage_bps < 0:
            _fail("HL_BRIDGE_INPUT_INVALID", "slippageBps não pode ser negativo", {"slippageBps": slippage_bps})

        reference_px = _optional_float(order, "referencePrice")
        limit_px = exchange._slippage_price(market, is_buy, slippage_bps / 10_000.0, reference_px)
        tif_effective = "Ioc"
    else:
        try:
            limit_px = float(price_raw)
        except (TypeError, ValueError):
            _fail("HL_BRIDGE_INPUT_INVALID", "price inválido", {"price": price_raw})
        if limit_px <= 0:
            _fail("HL_BRIDGE_INPUT_INVALID", "price deve ser > 0", {"price": limit_px})
        tif_effective = tif or "Gtc"

    if tif_effective not in ["Alo", "Ioc", "Gtc"]:
        _fail("HL_BRIDGE_INPUT_INVALID", "tif inválido", {"tif": tif_effective})

    order_request = {
        "coin": market,
        "is_buy": is_buy,
        "sz": sz,
        "limit_px": limit_px,
        "order_type": {"limit": {"tif": tif_effective}},
        "reduce_only": reduce_only,
    }

    if cloid_raw:
        order_request["cloid"] = cloid_cls.from_str(cloid_raw)

    asset = info.name_to_asset(market)

    from hyperliquid.utils.signing import order_request_to_order_wire, order_wires_to_order_action

    order_wire = order_request_to_order_wire(order_request, asset)
    action = order_wires_to_order_action([order_wire], None, "na")

    return action


def _build_cancel_action(payload: Dict[str, Any], info, cloid_cls):
    cancel = _require_dict(payload.get("cancel"), "cancel")
    market = _require_str(cancel, "market")
    oid = _optional_int(cancel, "oid")
    cloid_raw = _optional_str(cancel, "cloid")

    if oid is None and not cloid_raw:
        _fail("HL_BRIDGE_INPUT_INVALID", "Informe oid ou cloid para cancel", {"cancel": cancel})

    asset = info.name_to_asset(market)

    if cloid_raw:
        return {
            "type": "cancelByCloid",
            "cancels": [
                {
                    "asset": asset,
                    "cloid": cloid_cls.from_str(cloid_raw).to_raw(),
                }
            ],
        }

    if oid is None or oid <= 0:
        _fail("HL_BRIDGE_INPUT_INVALID", "oid inválido", {"oid": oid})

    return {
        "type": "cancel",
        "cancels": [
            {
                "a": asset,
                "o": oid,
            }
        ],
    }


def _build_modify_action(payload: Dict[str, Any], info, exchange, cloid_cls):
    modify = _require_dict(payload.get("modify"), "modify")
    order_payload = _require_dict(modify.get("order"), "modify.order")

    market = _require_str(order_payload, "market")
    side = _require_str(order_payload, "side").lower()
    if side not in ["buy", "sell"]:
        _fail("HL_BRIDGE_INPUT_INVALID", "side inválido em modify", {"side": side})

    sz = _optional_float(order_payload, "size")
    if sz is None or sz <= 0:
        _fail("HL_BRIDGE_INPUT_INVALID", "size inválido em modify", {"size": order_payload.get("size")})

    reduce_only = bool(order_payload.get("reduceOnly", False))
    new_cloid_raw = _optional_str(order_payload, "cloid")

    price_raw = order_payload.get("price")
    tif = _optional_str(order_payload, "tif")

    is_buy = side == "buy"

    if isinstance(price_raw, str) and price_raw.lower() == "market":
        slippage_bps = _optional_float(order_payload, "slippageBps")
        if slippage_bps is None:
            slippage_bps = 50.0
        if slippage_bps < 0:
            _fail("HL_BRIDGE_INPUT_INVALID", "slippageBps inválido em modify", {"slippageBps": slippage_bps})

        reference_px = _optional_float(order_payload, "referencePrice")
        limit_px = exchange._slippage_price(market, is_buy, slippage_bps / 10_000.0, reference_px)
        tif_effective = "Ioc"
    else:
        try:
            limit_px = float(price_raw)
        except (TypeError, ValueError):
            _fail("HL_BRIDGE_INPUT_INVALID", "price inválido em modify", {"price": price_raw})
        if limit_px <= 0:
            _fail("HL_BRIDGE_INPUT_INVALID", "price deve ser > 0 em modify", {"price": limit_px})
        tif_effective = tif or "Gtc"

    if tif_effective not in ["Alo", "Ioc", "Gtc"]:
        _fail("HL_BRIDGE_INPUT_INVALID", "tif inválido em modify", {"tif": tif_effective})

    oid = _optional_int(modify, "oid")
    cloid_ref = _optional_str(modify, "cloid")
    if oid is None and not cloid_ref:
        _fail("HL_BRIDGE_INPUT_INVALID", "modify requer oid ou cloid", {"modify": modify})

    asset = info.name_to_asset(market)

    from hyperliquid.utils.signing import order_request_to_order_wire

    order_request = {
        "coin": market,
        "is_buy": is_buy,
        "sz": sz,
        "limit_px": limit_px,
        "order_type": {"limit": {"tif": tif_effective}},
        "reduce_only": reduce_only,
    }

    if new_cloid_raw:
        order_request["cloid"] = cloid_cls.from_str(new_cloid_raw)

    order_wire = order_request_to_order_wire(order_request, asset)

    oid_payload: Any = oid
    if cloid_ref:
        oid_payload = cloid_cls.from_str(cloid_ref).to_raw()

    return {
        "type": "modify",
        "oid": oid_payload,
        "order": order_wire,
    }




def _build_deposit_action(payload: Dict[str, Any]):
    deposit = _require_dict(payload.get("deposit"), "deposit")
    amount = _optional_float(deposit, "amount")
    if amount is None or amount <= 0:
        _fail("HL_BRIDGE_INPUT_INVALID", "deposit.amount deve ser > 0", {"amount": deposit.get("amount")})

    to_perp = deposit.get("toPerp")
    if to_perp is None:
        to_perp = True
    if not isinstance(to_perp, bool):
        _fail("HL_BRIDGE_INPUT_INVALID", "deposit.toPerp deve ser boolean", {"toPerp": to_perp})

    return {
        "type": "usdClassTransfer",
        "amount": str(amount),
        "toPerp": to_perp,
    }
def _main() -> None:
    try:
        request_payload = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        _fail("HL_BRIDGE_INPUT_INVALID", "Falha ao ler JSON de entrada", {"message": str(exc)})

    operation = request_payload.get("operation")
    if operation == "ping":
        _emit({"ok": True, "bridge": "hyperliquid_live_bridge", "python": sys.version.split()[0]})

    try:
        import eth_account
        from hyperliquid.api import API
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils.constants import MAINNET_API_URL
        from hyperliquid.utils.signing import sign_l1_action
        from hyperliquid.utils.types import Cloid
    except Exception as exc:  # noqa: BLE001
        _fail(
            "HL_SDK_IMPORT_ERROR",
            "Não foi possível importar hyperliquid-python-sdk",
            {
                "message": str(exc),
                "hint": "python3 -m pip install -r requirements-hyperliquid.txt",
            },
        )

    api_url = _optional_str(request_payload, "apiUrl") or MAINNET_API_URL
    account_address = _optional_str(request_payload, "accountAddress")
    vault_address = _optional_str(request_payload, "vaultAddress")

    raw_key = _optional_str(request_payload, "apiWalletPrivateKey")
    if not raw_key:
        _fail(
            "HL_API_WALLET_KEY_MISSING",
            "apiWalletPrivateKey é obrigatório para execução live",
        )

    private_key = _normalize_private_key(raw_key)

    nonce = _optional_int(request_payload, "nonce")
    if nonce is None or nonce <= 0:
        _fail("HL_BRIDGE_INPUT_INVALID", "nonce inválido", {"nonce": request_payload.get("nonce")})

    expires_after = _optional_int(request_payload, "expiresAfter")

    try:
        wallet = eth_account.Account.from_key(private_key)
    except Exception as exc:  # noqa: BLE001
        _fail("HL_API_WALLET_KEY_INVALID", "Falha ao carregar api wallet private key", {"message": str(exc)})

    try:
        info = Info(api_url, skip_ws=True)
        # Exchange usado para cálculo consistente de preço agressivo de market order.
        exchange = Exchange(
            wallet,
            base_url=api_url,
            vault_address=vault_address,
            account_address=account_address,
            meta=info.meta(),
            spot_meta=info.spot_meta(),
        )
        api = API(api_url)

        if operation == "order":
            action = _build_order_action(request_payload, info, exchange, Cloid)
        elif operation == "cancel":
            action = _build_cancel_action(request_payload, info, Cloid)
        elif operation == "modify":
            action = _build_modify_action(request_payload, info, exchange, Cloid)
        elif operation == "deposit":
            action = _build_deposit_action(request_payload)
        else:
            _fail("HL_BRIDGE_OPERATION_UNSUPPORTED", "operation não suportada", {"operation": operation})

        is_mainnet = api_url.rstrip("/") == MAINNET_API_URL.rstrip("/")
        signature = sign_l1_action(
            wallet,
            action,
            vault_address,
            nonce,
            expires_after,
            is_mainnet,
        )

        exchange_payload: Dict[str, Any] = {
            "action": action,
            "nonce": nonce,
            "signature": signature,
        }

        if action.get("type") not in ["usdClassTransfer", "sendAsset"] and vault_address:
            exchange_payload["vaultAddress"] = vault_address

        if expires_after is not None:
            exchange_payload["expiresAfter"] = expires_after

        response = api.post("/exchange", exchange_payload)

        _emit(
            {
                "ok": True,
                "operation": operation,
                "signerAddress": wallet.address.lower(),
                "nonce": nonce,
                "expiresAfter": expires_after,
                "actionType": action.get("type"),
                "response": response,
            }
        )
    except Exception as exc:  # noqa: BLE001
        _fail(
            "HL_BRIDGE_EXECUTION_ERROR",
            "Falha na execução via hyperliquid-python-sdk",
            {
                "message": str(exc),
                "operation": operation,
            },
        )


if __name__ == "__main__":
    _main()
