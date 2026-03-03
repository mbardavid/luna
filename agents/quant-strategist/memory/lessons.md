# Quant Strategist — Lessons Learned
*Every mistake documented once, never repeated.*

## 2026-03-03: PMM Kill Switch Crash Loop (8+ hours undetected)

### Incident
PMM (prod-003) entered an infinite crash-restart loop for 8+ hours. Every 15 minutes: start → kill switch fires → halt → die → auto-recovery restarts → repeat.

### Root Cause Chain

**1. Missing `initial_balance` in config → wrong drawdown calculation**
- `prod-003.yaml` did NOT have an `initial_balance` field
- `RunConfig.from_yaml()` defaults to `Decimal("500")` when field is absent
- Actual on-chain USDC balance: $222.15
- Computed drawdown: `(500 - 222.15) / 500 = 55.57%`
- Kill switch threshold: `15%`
- Result: kill switch fires IMMEDIATELY on every startup, before any trade

**2. Auto-recovery picks wrong config via `ls -t`**
- `pmm-status-updater.sh` selected config with `ls -t *.yaml | head -1`
- All YAML files had identical mtime (from `git checkout`) → `ls -t` tiebreak picked `p5-001.yaml`
- `p5-001.yaml` is a PAPER trading config with different markets (`us-strikes-iran`, `will-axiom-insider-trading`)
- Those markets had zero orderbook data → 0 quotes, 0 orders, just WebSocket noise
- 7 of 10 restarts used the wrong config

**3. Auto-recovery fights kill switch in infinite loop**
- Kill switch correctly halts the bot to protect capital
- Auto-recovery (pmm-status-updater.sh every 15min) incorrectly restarts it
- Neither system knows about the other → deadlock loop
- No circuit breaker on the recovery side to detect "bot keeps dying immediately"

### Fixes Applied
1. **`prod-003.yaml`**: Added `initial_balance: 222` (actual on-chain balance)
2. **`pmm-status-updater.sh`**: Fixed config selection — hardcoded `prod-003.yaml` instead of `ls -t`. Also fixed runner command from `paper.production_runner` to `runner --mode live`
3. **`pmm-status-updater.sh`**: Fixed pgrep pattern to detect the unified runner (`runner --mode live`)

### Rules (permanent)

1. **EVERY production config MUST have explicit `initial_balance`**. Never rely on defaults. The default of 500 is dangerous when actual balance differs.

2. **Config selection must be deterministic, not filesystem-based**. `ls -t` is unreliable when mtimes are equal (git checkout, rsync, cp). Use explicit config path in recovery scripts.

3. **Auto-recovery MUST have a crash-loop breaker**. If bot dies N times in M minutes, STOP restarting and ALERT. The heartbeat-v3 `check_pmm_health` already has this (max 3/hour), but `pmm-status-updater.sh` didn't.

4. **Kill switch + auto-recovery = potential deadlock**. When both exist, they need coordination. If kill switch fires, recovery should check WHY before restarting. A kill switch trigger is not a random crash — it's a deliberate safety halt.

5. **Validate config at startup before trading**. Assert: `initial_balance <= on_chain_balance` (or close to it). If initial_balance >> actual balance, log critical error and refuse to start.

6. **Paper configs and production configs should NOT be in the same directory**, or at minimum production configs should have a distinct naming convention that recovery scripts can filter on (e.g., `prod-*.yaml` only).

---

## Cross-pollinated from Luna (2026-03-02): Binary Market Complement Consistency
**Domain:** Polymarket / Trading
**Pattern:** In binary markets (YES/NO), prices must satisfy `price_yes + price_no = 1.0`. When quoting one side, always verify the complement is consistent. Off-by-one-cent errors in complement routing caused phantom PnL and incorrect order placement.
**Action:** Always assert complement consistency in tests: `assert abs(price_yes + price_no - 1.0) < 1e-9`.

## Cross-pollinated from Luna (2026-03-02): Balance Exhaustion in MM Bots
**Domain:** Trading / Market Making
**Pattern:** prod-002 did 2 fills then 7390 rejections because $25 capital was exhausted. Complement routing (BUY YES + BUY NO) doubles capital usage. The bot kept quoting BIDs it couldn't fill, wasting API calls.
**Action:** Implement balance-aware quoting: check `available_balance < min_balance_to_quote` BEFORE generating BID quotes. Keep ASK quotes alive for existing positions. Add position recycling to auto-sell profitable positions and reclaim capital. Both features must be backward-compatible (disabled by default).

## Cross-pollinated from Luna (2026-03-02): Sync Fill State vs Async Events
**Domain:** Trading / Order Management
**Pattern:** Order fill events can arrive out of order or be delayed. Position state built from fill events must handle: (a) duplicate fills (same fill_id), (b) fills arriving after cancel confirmation, (c) partial fills that sum to more than order quantity due to race conditions.
**Action:** Always deduplicate fills by fill_id. Use idempotent position updates. Test with out-of-order event sequences.

## Cross-pollinated from Luna (2026-03-02): USDC Micro-Units
**Domain:** DeFi / API Integration
**Pattern:** USDC balance from Polymarket API is returned in micro-units (1e6). Failing to divide causes positions to appear 1,000,000x larger than reality, which cascades into incorrect risk calculations and order sizing. Also applies to reward balances and fee calculations.
**Action:** Normalize ALL token amounts at API boundary, not just balance. Include assertion in tests: `assert normalize_usdc(1_000_000) == Decimal("1.0")`.

## Cross-pollinated from Luna (2026-03-02): Inventory Management Failure
**Domain:** Trading / Market Making
**Pattern:** PMM acumulou $227 em posições direcionais sem reciclar, esgotou capital, e morreu. prod-002 não tinha position recycling → acumulou inventário → capital zerou → bot parou.
**Action:** Bot MM deve ter 3 guardas automáticas:
1. **Inventory guard:** Se net_inventory > max_position, reciclar automaticamente (position_recycling)
2. **Capital guard:** Se USDC < min_balance, parar de quotar e vender posições pra liberar capital
3. **Market rotation:** Se spread > X ou volume < Y por N horas, fechar posições e migrar pra mercado melhor

## Cross-pollinated from Luna (2026-03-02): DNS Resolution Failures in External APIs
**Domain:** Infrastructure / Network
**Pattern:** `quote-api.jup.ag` intermittently fails DNS resolution (`ENOTFOUND`). Single-provider dependencies cause hard failures in swap execution.
**Action:** Implement fallback chains with retry/backoff: primary → lite endpoint → alternative provider. Never depend on a single external endpoint without a fallback path.

## Cross-pollinated from Luna (2026-03-01): Gateway Memory Pressure Affects PMM
**Domain:** Infrastructure / Resource Management
**Pattern:** Agentes Python secundários dentro do cgroup (paper runner ~116MB + dashboard ~85MB) contribuem para o MemoryCurrent do systemd. Quando o gateway chega perto do MemoryHigh (900MB), processos filhos incluindo o PMM podem ser afetados.
**Action:** Monitorar individualmente o consumo de memória do PMM runner. Se gateway RSS > 600MB, considerar que PMM pode ser impactado. O sentinel alerta em 812MB mas o ideal é não chegar lá.

---
*Last updated: 2026-03-03*

---

## Cláusula Pétrea: Wakeup Instantâneo via `gateway call agent` (2026-03-03)

### Padrão Arquitetural Obrigatório
`openclaw gateway call agent --json --params '{"message":"...","idempotencyKey":"unique-key"}'` cria um turno AI imediatamente na sessão principal. Scripts bash podem despertar agentes AI em segundos, sem esperar heartbeat.

### 3 Camadas de Uptime
1. **Wakeup instantâneo** — `gateway call agent` para eventos críticos (~3s)
2. **Heartbeat built-in** (2min) — safety net automático
3. **Bash detection** (*/5 cron) — detecção determinística, dispara camada 1

### Regras
- Scripts de detecção DEVEM chamar `gateway call agent` após gerar queue item
- `idempotencyKey` único por evento (timestamp ou task_id)
- Timeout 15-20s, non-fatal (heartbeat é backup)
- Padrão obrigatório para toda automação futura em todos os agentes

