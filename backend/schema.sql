CREATE TABLE IF NOT EXISTS feed_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    chain TEXT NOT NULL,
    token_address TEXT,
    pair_address TEXT,
    token_symbol TEXT,
    details TEXT DEFAULT '{}',
    severity TEXT DEFAULT 'info',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_feed_events_created ON feed_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_feed_events_type ON feed_events(event_type);
CREATE INDEX IF NOT EXISTS idx_feed_events_chain ON feed_events(chain);

CREATE TABLE IF NOT EXISTS token_cache (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS security_cache (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    goplus TEXT DEFAULT '{}',
    honeypot TEXT DEFAULT '{}',
    rugcheck TEXT DEFAULT '{}',
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    notes TEXT DEFAULT '',
    added_at TEXT DEFAULT (datetime('now')),
    UNIQUE(chain, address)
);

CREATE TABLE IF NOT EXISTS funding_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    rate REAL NOT NULL,
    next_funding_time INTEGER,
    fetched_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_funding_symbol ON funding_snapshots(symbol, exchange);
CREATE UNIQUE INDEX IF NOT EXISTS idx_funding_unique ON funding_snapshots(symbol, exchange, fetched_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '{}',
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analysis_cache (
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    data TEXT DEFAULT '{}',
    fetched_at TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (chain, address)
);

CREATE TABLE IF NOT EXISTS claude_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    session_id TEXT,
    prompt TEXT,
    result TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_derivatives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    close_price REAL,
    open_interest_usd REAL,
    funding_rate REAL,
    liquidations_long REAL DEFAULT 0,
    liquidations_short REAL DEFAULT 0,
    liquidations_delta REAL DEFAULT 0,
    volume_usd REAL,
    fetched_at TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_deriv_unique ON daily_derivatives(symbol, date);

CREATE TABLE IF NOT EXISTS derivatives_zscores (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    oi_zscore REAL, funding_zscore REAL, liq_zscore REAL, volume_zscore REAL,
    oi_percentile REAL, funding_percentile REAL, liq_percentile REAL, volume_percentile REAL,
    oi_change_24h_pct REAL, price_change_24h_pct REAL,
    PRIMARY KEY (symbol, date)
);

-- Deribit volatility (BTC, ETH only)
CREATE TABLE IF NOT EXISTS daily_volatility (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv_30d REAL,
    rv_30d REAL,
    skew_25d REAL,
    skew_25d_zscore REAL,
    vrp REAL,
    vrp_zscore REAL,
    close_price REAL,
    PRIMARY KEY (symbol, date)
);

-- Momentum indicator (all symbols)
CREATE TABLE IF NOT EXISTS daily_momentum (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    momentum_value REAL,
    cs_decile INTEGER,
    ts_decile INTEGER,
    rel_decile INTEGER,
    directional_intensity REAL,
    vol_regime REAL,
    relative_volume REAL,
    proximity_52w_high REAL,
    PRIMARY KEY (symbol, date)
);

-- RV for all 30 symbols (computed from prices)
CREATE TABLE IF NOT EXISTS daily_rv (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    rv_30d REAL,
    PRIMARY KEY (symbol, date)
);

-- Real-time liquidation events (from WS)
CREATE TABLE IF NOT EXISTS liquidation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    quantity REAL NOT NULL,
    usd_value REAL NOT NULL,
    exchange TEXT NOT NULL,
    timestamp INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_liq_sym_ts ON liquidation_events(symbol, timestamp);

-- Verified protocol contracts (seeded from evm_automation skill)
CREATE TABLE IF NOT EXISTS verified_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    address TEXT NOT NULL,
    symbol TEXT,
    name TEXT,
    category TEXT NOT NULL,
    protocol TEXT,
    added_at TEXT DEFAULT (datetime('now')),
    UNIQUE(chain, address)
);
CREATE INDEX IF NOT EXISTS idx_verified_chain ON verified_contracts(chain);
CREATE INDEX IF NOT EXISTS idx_verified_address ON verified_contracts(address);

-- Persistent alert cooldowns (survive restarts)
CREATE TABLE IF NOT EXISTS alert_cooldowns (
    key TEXT PRIMARY KEY,
    fired_at REAL NOT NULL
);

-- Forward tracking of alert results
CREATE TABLE IF NOT EXISTS alert_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    tier TEXT NOT NULL,
    confluence INTEGER,
    fired_at TEXT NOT NULL,
    entry_price REAL NOT NULL,
    expected_direction TEXT,
    price_1d REAL, price_3d REAL, price_7d REAL,
    return_1d REAL, return_3d REAL, return_7d REAL
);
CREATE INDEX IF NOT EXISTS idx_alert_tracking_fired ON alert_tracking(fired_at);
