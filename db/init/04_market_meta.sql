-- Market metadata tables: clock, calendar, corporate actions, news.
-- Populated by the n8n workflows (which also CREATE these IF NOT EXISTS, so a
-- central DB provisioned before this file existed still gets them).

-- Latest market clock (single row).
CREATE TABLE IF NOT EXISTS market_clock (
    id          BOOLEAN PRIMARY KEY DEFAULT true CHECK (id),
    ts          TIMESTAMPTZ,
    is_open     BOOLEAN,
    next_open   TIMESTAMPTZ,
    next_close  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Trading calendar (one row per trading day). Column names match the Alpaca
-- response keys so n8n auto-mapping works.
CREATE TABLE IF NOT EXISTS market_calendar (
    date            DATE PRIMARY KEY,
    "open"          TEXT,
    "close"         TEXT,
    session_open    TEXT,
    session_close   TEXT,
    settlement_date DATE
);

-- Corporate actions (dividends, splits). Scalar columns only; type-specific
-- extras collapse to rate/old_rate.
CREATE TABLE IF NOT EXISTS corporate_actions (
    id           TEXT PRIMARY KEY,
    symbol       TEXT,
    ca_type      TEXT,
    ex_date      DATE,
    record_date  DATE,
    payable_date DATE,
    rate         DOUBLE PRECISION,
    old_rate     DOUBLE PRECISION,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ca_symbol_exdate ON corporate_actions (symbol, ex_date DESC);

-- News headlines.
CREATE TABLE IF NOT EXISTS news (
    id          BIGINT PRIMARY KEY,
    created_at  TIMESTAMPTZ,
    updated_at  TIMESTAMPTZ,
    headline    TEXT,
    summary     TEXT,
    author      TEXT,
    source      TEXT,
    url         TEXT,
    symbols     TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_created ON news (created_at DESC);
