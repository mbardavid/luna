-- Initial schema for cold storage tables.
-- Applied automatically by ColdWriter on startup.

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price TEXT NOT NULL,
    size TEXT NOT NULL,
    notional_usd TEXT NOT NULL,
    fee_usd TEXT DEFAULT '0',
    strategy_tag TEXT,
    trace_id TEXT,
    filled_at TEXT NOT NULL,
    _inserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fills_market_id ON fills(market_id);
CREATE INDEX IF NOT EXISTS idx_fills_filled_at ON fills(filled_at);

CREATE TABLE IF NOT EXISTS pnl_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    cumulative_pnl_usd TEXT NOT NULL,
    daily_pnl_usd TEXT NOT NULL,
    total_exposure_usd TEXT NOT NULL,
    num_active_markets INTEGER DEFAULT 0,
    _inserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pnl_timestamp ON pnl_snapshots(timestamp);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    price TEXT NOT NULL,
    size TEXT NOT NULL,
    filled_qty TEXT DEFAULT '0',
    status TEXT NOT NULL,
    order_type TEXT NOT NULL,
    strategy_tag TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    _inserted_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_market_id ON orders(market_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);

CREATE TABLE IF NOT EXISTS kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    action TEXT NOT NULL,
    details TEXT,
    timestamp TEXT NOT NULL,
    _inserted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    message TEXT,
    severity TEXT NOT NULL,
    channels TEXT,
    success INTEGER DEFAULT 1,
    timestamp TEXT NOT NULL,
    _inserted_at TEXT NOT NULL
);
