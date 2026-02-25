# A2A — Agent-to-Agent Delegation Layer

## Architecture: Fast-Path vs Slow-Path

The Polymarket MM separates operations into two execution paths:

### ⚡ Fast-Path (Local — polymarket-mm)

Operations that require **sub-second latency** and run directly inside the quant engine:

| Operation | Module | Notes |
|-----------|--------|-------|
| EIP-712 order signing | `web3_infra/eip712_signer.py` | CPU-bound, offloaded to process pool |
| CLOB order submission | `execution/order_manager.py` | REST/WS to Polymarket CLOB |
| Order book processing | `data/ws_client.py` | Real-time WebSocket feed |
| Quote generation | `strategy/quote_engine.py` | Core MM logic |
| Opportunity detection | `strategy/complete_set.py` | State machine, signal scanning |

### 🐢 Slow-Path (Delegated → Crypto-Sage via A2A)

Operations that interact with the **blockchain** and can tolerate seconds of latency:

| Operation | TaskSpec Operation | Notes |
|-----------|-------------------|-------|
| Merge YES+NO → USDC | `ctf.merge` | Burns tokens, returns collateral |
| Split USDC → YES+NO | `ctf.split` | Locks collateral, mints tokens |
| Bridge deposit | `bridge.deposit` | Cross-chain fund transfer |

These operations are delegated to the **Crypto-Sage** agent, which handles:
- RPC endpoint management and failover
- Gas estimation and gas-price abort logic
- Transaction signing, submission, and confirmation
- USDC approval management

## TaskSpec Format

Every delegated operation is wrapped in a `TaskSpec` JSON payload:

```json
{
  "version": "1.0",
  "handoff_id": "550e8400-e29b-41d4-a716-446655440000",
  "operation": "ctf.merge",
  "params": {
    "market_id": "market-123",
    "condition_id": "0xabc...",
    "amount_usdc": "100.00",
    "amount_raw": 100000000,
    "token_id_yes": "tok_yes",
    "token_id_no": "tok_no",
    "neg_risk": false,
    "ctf_address": "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045",
    "usdc_address": "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
  },
  "risk": {
    "classification": "medium",
    "requires_confirmation": false,
    "max_gas_gwei": 100,
    "deadline_seconds": null
  },
  "callback_topic": "a2a.ctf.merge.result",
  "source_agent": "polymarket-mm"
}
```

### Fields

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Schema version for forward compatibility |
| `handoff_id` | string (UUID) | Unique identifier for this delegation |
| `operation` | string | Canonical operation name |
| `params` | object | Operation-specific parameters |
| `risk` | object | Risk classification and constraints |
| `callback_topic` | string | EventBus topic for the result |
| `source_agent` | string | Identifier of the requesting agent |

### Risk Classification

| Level | Description | Example |
|-------|-------------|---------|
| `low` | Routine, no operator attention needed | Read-only queries |
| `medium` | Standard on-chain operation | Merge/split |
| `high` | Significant value at risk | Bridge deposits |
| `critical` | Requires explicit operator approval | Large withdrawals |

## Callback Topics

Results are published back to the EventBus on these topics:

| Topic | Operation | Payload |
|-------|-----------|---------|
| `a2a.ctf.merge.result` | Merge | `{handoff_id, tx_hash, status, gas_cost_usd, error}` |
| `a2a.ctf.split.result` | Split | `{handoff_id, tx_hash, status, gas_cost_usd, error}` |
| `a2a.bridge.deposit.result` | Bridge | `{handoff_id, tx_hash, status, error}` |

## Flow Example: Merge Arbitrage

```
polymarket-mm                          Crypto-Sage
     │                                      │
     │  1. Detect arb opportunity            │
     │  2. Buy YES on CLOB (fast-path)       │
     │  3. Buy NO on CLOB (fast-path)        │
     │  4. Both legs filled                  │
     │                                       │
     │── TaskSpec(ctf.merge) ───────────────→│
     │                                       │  5. Check gas price
     │                                       │  6. Build merge tx
     │                                       │  7. Sign & submit
     │                                       │  8. Wait confirmation
     │←── EventBus(merge.result) ────────────│
     │                                       │
     │  9. Update state machine → COMPLETED  │
```

## File Structure

```
a2a/
├── __init__.py          # Package exports
├── task_spec.py         # TaskSpec + RiskClassification schemas
├── ctf_delegate.py      # CTFDelegate — generates TaskSpec payloads
├── rpc_manager_ref.py   # Reference copy of RPCManager (for Crypto-Sage)
└── README.md            # This file
```

## Migration from web3_infra/

| Old (web3_infra/) | New (a2a/) | Status |
|-------------------|------------|--------|
| `ctf_adapter.py` → CTFAdapter | `ctf_delegate.py` → CTFDelegate | Replaced |
| `eip712_signer.py` → EIP712Signer | Stays in `web3_infra/` | Kept (fast-path) |
| `rpc_manager.py` → RPCManager | `rpc_manager_ref.py` (reference) | Moved to Crypto-Sage |
