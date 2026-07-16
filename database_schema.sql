-- ============================================================
-- Swing Trading Dashboard - Database Schema
-- Engine target: SQLite for MVP (easy migration path to Postgres)
-- ============================================================

-- Table: ui_strings
-- Holds all static UI text so the frontend can switch he/en instantly
CREATE TABLE IF NOT EXISTS ui_strings (
    string_key   TEXT PRIMARY KEY,
    he           TEXT NOT NULL,
    en           TEXT NOT NULL
);

-- Seed a few example rows (extend as the UI grows)
INSERT OR IGNORE INTO ui_strings (string_key, he, en) VALUES
    ('price_label',        'מחיר',                 'Price'),
    ('swing_score_label',  'ציון עוצמה',            'Swing Score'),
    ('macro_hub_title',    'מוקד מאקרו וחדשות',     'Macro & Market Hub'),
    ('scanner_title',      'סורק מניות',            'Swing Scanner'),
    ('deep_dive_button',   'דוח מעמיק',             'Deep Dive');

-- Table: scanned_stocks
-- One row per flagged ticker per scan run (Pipeline A output)
CREATE TABLE IF NOT EXISTS scanned_stocks (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                   TEXT NOT NULL,
    trigger_text_he          TEXT,
    trigger_text_en          TEXT,
    swing_score              INTEGER CHECK (swing_score BETWEEN 0 AND 100),
    entry_price              REAL,
    support_level            REAL,
    resistance_targets        TEXT,   -- JSON array stored as text, e.g. "[105.2, 112.0]"
    social_volume_spike_pct  REAL,
    ai_summary_he            TEXT,
    ai_summary_en            TEXT,
    market_cap               REAL,
    avg_volume_20d           REAL,
    breakout_volume_pct      REAL,    -- % above 20d avg volume
    exchange                 TEXT,    -- NYSE / NASDAQ
    timestamp                DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scanned_stocks_ticker ON scanned_stocks(ticker);
CREATE INDEX IF NOT EXISTS idx_scanned_stocks_timestamp ON scanned_stocks(timestamp);

-- Table: macro_news
-- One row per high-impact macro event (Pipeline B output)
CREATE TABLE IF NOT EXISTS macro_news (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    category_tag   TEXT NOT NULL,   -- e.g. CPI, Interest Rate, Geo
    summary_he     TEXT,
    summary_en     TEXT,
    impact_level   TEXT CHECK (impact_level IN ('Medium','High','Critical')),
    source_url     TEXT,
    timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_macro_news_timestamp ON macro_news(timestamp);
CREATE INDEX IF NOT EXISTS idx_macro_news_tag ON macro_news(category_tag);

-- Table: analytics_events
-- Tracks what visitors click on (which tickers / news items interest them).
-- General traffic stats (visitor counts, referrers, devices, countries) are
-- NOT stored here - those come from a dedicated analytics tool (see
-- DEPLOYMENT_GUIDE.md). This table is only for site-specific "what do
-- people care about" data.
CREATE TABLE IF NOT EXISTS analytics_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type   TEXT NOT NULL,      -- 'view_stock' / 'view_macro' / 'page_view'
    entity_id    TEXT,               -- e.g. the ticker, or the macro news tag
    session_id   TEXT,               -- anonymous per-browser id, no personal data
    timestamp    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_analytics_type ON analytics_events(event_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_analytics_timestamp ON analytics_events(timestamp);
