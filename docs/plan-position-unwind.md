# Plan: Position Unwind & Exit Management Module

**Status:** DRAFT — awaiting Matheus review
**Created:** 2026-03-01
**Context:** prod-001 revealed that positions are orphaned when the runner stops. No unwind, no exit management, no position lifecycle.

---

## 1. Problem Statement

When the production runner stops (graceful shutdown, kill switch trigger, crash, or manual stop), open positions remain on-chain with no management. This creates:

- **Orphan risk**: Positions sit unmanaged until market expiry
- **Capital lock**: USDC tied up in positions that could be recovered
- **Directional exposure**: MM should be neutral, but shutdown leaves us exposed
- **No visibility**: No way to see/manage positions outside the runner

---

## 2. Scope

### 2.1 Graceful Unwind on Shutdown

When the runner stops (any reason), it should attempt to close all open positions before exiting.

```
Runner receives SIGTERM/SIGINT or kill switch triggers
  → Cancel all open orders
  → For each position with > 0 shares:
      → Submit market sell order (aggressive pricing)
      → Wait for fill (with timeout)
      → If partial fill, retry at worse price
      → If timeout, log orphaned position and alert
  → Log final portfolio state
  → Exit
```

**Key considerations:**
- Polymarket has no "market order" — must use limit orders at aggressive price
- Min order size = 5 shares. Positions < 5 shares can't be sold (dust)
- Unwind should be **best-effort** — never block shutdown indefinitely
- Max unwind time: 60 seconds (configurable)

### 2.2 Unwind Strategies

| Strategy | When | How |
|----------|------|-----|
| **Aggressive sell** | Normal shutdown | Sell at best_bid - 1 tick (YES) or best_ask + 1 tick (NO) |
| **CTF Merge** | Both YES+NO positions | Call `mergePositions` on-chain → recover $1 per pair. Gas ~$0.01 |
| **Sweep** | Kill switch / emergency | Sell at mid - 5% (take the loss, get out fast) |
| **Hold to expiry** | Dust positions < 5 shares | Let Polymarket resolve at expiry ($1 or $0) |

### 2.3 CTF Merge Optimization

If we hold both YES and NO shares of the same market, we can merge them on-chain for $1/pair instead of selling on the book. This is **always better** than selling both sides.

```python
# Example: 50 YES + 30 NO
# Merge 30 pairs → receive 30 USDC (guaranteed $1/pair)
# Remaining: 20 YES → sell on book
mergeable = min(yes_shares, no_shares)
if mergeable >= 1:
    merge_positions(condition_id, amount=mergeable)  # on-chain tx
    # Then sell remaining side on book
```

### 2.4 Position Manager CLI

Standalone script for manual position management outside the runner:

```bash
# View current positions
python3 -m paper.position_manager status

# Close all positions
python3 -m paper.position_manager unwind --strategy aggressive

# Close specific position
python3 -m paper.position_manager unwind --token YES --market <condition_id>

# Merge YES+NO pairs
python3 -m paper.position_manager merge --market <condition_id>

# Dust cleanup report
python3 -m paper.position_manager dust
```

### 2.5 Post-Crash Recovery

If the runner crashes (no graceful shutdown):
1. On next start, check for orphaned positions from previous run
2. Log warning with position details
3. Option: `--unwind-previous` flag to close inherited positions before starting new run
4. Option: `--adopt-positions` flag to incorporate into new run's tracker

---

## 3. Architecture

### 3.1 New Files

```
polymarket-mm/
├── src/
│   ├── execution/
│   │   ├── unwind.py          # UnwindManager class
│   │   └── ctf_merge.py       # CTF merge/split operations
│   └── cli/
│       └── position_manager.py # CLI for manual management
├── tests/
│   ├── test_unwind.py
│   ├── test_ctf_merge.py
│   └── test_position_manager.py
```

### 3.2 UnwindManager Class

```python
class UnwindManager:
    """Manages position unwinding on shutdown."""
    
    def __init__(self, clob_client, rest_client, config: UnwindConfig):
        self.clob_client = clob_client
        self.rest_client = rest_client
        self.config = config  # max_time_s, strategies, dust_threshold
    
    async def unwind_all(self, positions: dict, reason: str) -> UnwindReport:
        """Main entry point. Called on shutdown."""
        report = UnwindReport(reason=reason)
        
        # 1. Cancel all open orders
        await self._cancel_all_orders()
        
        # 2. Check for mergeable pairs
        for market_id, pos in positions.items():
            merged = await self._try_merge(market_id, pos)
            report.merged += merged
            
        # 3. Sell remaining positions
        for market_id, pos in positions.items():
            if pos.net_shares > self.config.dust_threshold:
                result = await self._sell_position(market_id, pos)
                report.add(result)
            else:
                report.dust.append((market_id, pos.net_shares))
        
        # 4. Log and alert
        report.finalize()
        return report
    
    async def _sell_position(self, market_id, pos) -> SellResult:
        """Sell a position with progressive pricing."""
        for attempt in range(3):
            price = self._get_unwind_price(pos, attempt)
            order = await self.clob_client.create_and_post_order(...)
            if order.filled:
                return SellResult(success=True, ...)
            await asyncio.sleep(5)  # Wait between attempts
        return SellResult(success=False, orphaned=True, ...)
    
    def _get_unwind_price(self, pos, attempt: int) -> Decimal:
        """Progressive pricing: each attempt is more aggressive."""
        # Attempt 0: best_bid (YES) or best_ask (NO)  
        # Attempt 1: mid - 2%
        # Attempt 2: mid - 5% (take the loss)
        ...
```

### 3.3 Integration Points

**Production Runner (`production_runner.py`):**
```python
# In signal handler
async def shutdown(self, reason: str):
    self.logger.warning(f"Shutdown initiated: {reason}")
    
    # Cancel quote loop
    self._running = False
    
    # Unwind positions
    unwind = UnwindManager(self.clob_client, self.rest_client, self.config.unwind)
    report = await unwind.unwind_all(self.positions, reason=reason)
    
    # Log report
    self.logger.info(f"Unwind complete: {report}")
    report.save(f"paper/data/unwind_{self.run_id}_{datetime.now():%Y%m%d_%H%M%S}.json")
```

**Kill Switch (`kill_switch.py`):**
```python
# When kill switch triggers
async def _execute_halt(self, reason: str):
    # Use sweep strategy (most aggressive)
    self.unwind_manager.config.strategy = UnwindStrategy.SWEEP
    report = await self.unwind_manager.unwind_all(self.positions, reason=f"KILL_SWITCH: {reason}")
```

---

## 4. Configuration

```yaml
# In prod-001.yaml
unwind:
  enabled: true
  max_time_seconds: 60
  strategies:
    normal_shutdown: aggressive    # best_bid/ask
    kill_switch: sweep             # mid - 5%
    crash_recovery: hold           # don't unwind, adopt
  dust_threshold_shares: 5         # min order size on Polymarket
  merge_enabled: true              # try CTF merge before selling
  progressive_pricing:
    attempt_1_offset_pct: 0        # at market
    attempt_2_offset_pct: 2        # 2% worse
    attempt_3_offset_pct: 5        # 5% worse (last resort)
  alert_on_orphan: true            # notify if unwind fails
```

---

## 5. Test Plan

| Test | Description |
|------|-------------|
| `test_unwind_yes_position` | Sell YES shares at aggressive price |
| `test_unwind_no_position` | Sell NO shares at aggressive price |
| `test_unwind_both_sides_merges` | YES+NO → merge pairs first, sell remainder |
| `test_unwind_dust_skipped` | Positions < 5 shares → logged, not sold |
| `test_unwind_timeout` | Unwind takes > max_time → exits with orphan report |
| `test_unwind_progressive_pricing` | 3 attempts with worsening prices |
| `test_unwind_kill_switch_sweep` | Kill switch uses sweep strategy |
| `test_unwind_crash_recovery` | Next run detects orphaned positions |
| `test_ctf_merge` | Merge 30 YES + 30 NO → 30 USDC |
| `test_position_manager_cli` | CLI status/unwind/merge commands |
| `test_unwind_report_saved` | JSON report written to disk |
| `test_cancel_orders_before_unwind` | All open orders cancelled first |

---

## 6. Acceptance Criteria

- [ ] Runner gracefully unwinds all positions on SIGTERM/SIGINT
- [ ] Kill switch triggers sweep unwind before halting
- [ ] CTF merge used when both YES+NO held (saves spread cost)
- [ ] Positions < 5 shares (dust) logged but not attempted
- [ ] Unwind report saved as JSON with full details
- [ ] CLI tool works standalone for manual position management
- [ ] All existing tests still pass (no regressions)
- [ ] 12+ new tests covering unwind scenarios
- [ ] Crash recovery detects orphaned positions on restart

---

## 7. Estimated Effort

- **UnwindManager + tests**: 3-4 hours (Luan)
- **CTF Merge integration**: 2 hours (Luan)
- **Position Manager CLI**: 1-2 hours (Luan)
- **Runner integration + signal handlers**: 1 hour (Luan)
- **Total**: ~8 hours of agent time

---

## Next Action
Matheus approves → Luna spawns Luan with this spec.
