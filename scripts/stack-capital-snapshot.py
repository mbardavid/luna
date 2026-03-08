#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

WORKSPACE = Path("/home/openclaw/.openclaw/workspace")
DATA_DIR = WORKSPACE / "polymarket-mm" / "paper" / "data"
OUTPUT_PATH = DATA_DIR / "stack_capital_latest.json"
CRYPTO_SAGE_DIR = Path("/home/openclaw/.openclaw/workspace-crypto-sage")
SYSTEMD_ENV = Path("/home/openclaw/.config/systemd/user/openclaw-gateway.service.d/crypto-sage-env.conf")


def read_json(path: Path, default: Any):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _run_crypto_sage_saldo() -> dict[str, Any]:
    shell = (
        "set -a\n"
        f"source <(sed -n 's/^Environment=\\\"\\([^=][^=]*\\)=\\(.*\\)\\\"$/\\1=\\2/p' {SYSTEMD_ENV})\n"
        f"cd {CRYPTO_SAGE_DIR}\n"
        "node src/cli.mjs execute --instruction '/saldo' --dry-run\n"
    )
    proc = subprocess.run(["bash", "-lc", shell], text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "crypto-sage saldo failed")
    stdout = proc.stdout.strip()
    try:
        return json.loads(stdout)
    except Exception:
        pass
    start = stdout.find('{')
    end = stdout.rfind('}')
    if start != -1 and end != -1 and end > start:
        return json.loads(stdout[start:end + 1])
    raise RuntimeError("unable to parse crypto-sage saldo output")


def _load_pmm_diagnosis() -> dict[str, Any]:
    diagnosis = read_json(DATA_DIR / "quant_diagnosis_latest.json", {})
    wallet = (((diagnosis.get("analysis") or {}).get("post_trade_diagnosis") or {}).get("wallet_state") or {})
    return {
        "free_collateral_usdc": float(wallet.get("free_collateral_usdc", 0) or 0),
        "recoverable_inventory_usdc": float(wallet.get("recoverable_inventory_usdc", 0) or 0),
        "dust_inventory_usdc": float(wallet.get("dust_inventory_usdc", 0) or 0),
        "total_usd": float(wallet.get("total_wallet_equity_usdc", 0) or 0),
        "wallet": wallet.get("wallet"),
    }


def collect() -> dict[str, Any]:
    saldo = _run_crypto_sage_saldo()
    snapshot = ((saldo.get("result") or {}).get("snapshot") or {})
    wallets = snapshot.get("wallets") or []
    chain_totals = {}
    for wallet in wallets:
        network = str(wallet.get("network") or "unknown")
        chain_totals[network] = float(wallet.get("subtotalUsd") or 0)
    pmm = _load_pmm_diagnosis()
    total_usd = float(snapshot.get("totalUsd") or 0)
    return {
        "generated_at_ts": int(time.time()),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stack": {
            "total_usd": total_usd,
            "chain_totals": chain_totals,
            "snapshot_utc": snapshot.get("snapshotUtc"),
        },
        "pmm": pmm,
        "delta_vs_pmm_usd": total_usd - float(pmm.get("total_usd") or 0),
    }


def main() -> int:
    payload = collect()
    write_json(OUTPUT_PATH, payload)
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
