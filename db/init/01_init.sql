-- TimescaleDB init. Runs once on first container start (empty data volume).
-- Enable the extension and create a sample financial hypertable.

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- OHLCV market data, one row per symbol per interval.
CREATE TABLE IF NOT EXISTS market_ohlcv (
    time    TIMESTAMPTZ      NOT NULL,
    symbol  TEXT             NOT NULL,
    open    DOUBLE PRECISION,
    high    DOUBLE PRECISION,
    low     DOUBLE PRECISION,
    close   DOUBLE PRECISION,
    volume  DOUBLE PRECISION,
    -- Audit stamp (microsecond precision). Bumped only when a bar's values
    -- actually change on re-ingest; identical re-fetches are a no-op.
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Turn it into a hypertable partitioned on time.
SELECT create_hypertable('market_ohlcv', 'time', if_not_exists => TRUE);

-- Lookups are almost always by symbol over a time range.
CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol_time
    ON market_ohlcv (symbol, time DESC);

-- One bar per (symbol, time). Enables idempotent upserts on re-ingest.
-- Note: a unique index on a hypertable must include the partition column (time).
CREATE UNIQUE INDEX IF NOT EXISTS uq_ohlcv_symbol_time
    ON market_ohlcv (symbol, time);

-- Retention: keep 365 days, drop older chunks automatically (background job).
SELECT add_retention_policy('market_ohlcv', INTERVAL '365 days', if_not_exists => TRUE);
