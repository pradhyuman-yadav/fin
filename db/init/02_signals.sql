-- Technical-indicator signals, one row per symbol per bar time.
-- Populated by tools/signals.py from market_ohlcv.

CREATE TABLE IF NOT EXISTS market_signals (
    time        TIMESTAMPTZ NOT NULL,
    symbol      TEXT        NOT NULL,
    close       DOUBLE PRECISION,
    sma20       DOUBLE PRECISION,
    sma50       DOUBLE PRECISION,
    ema12       DOUBLE PRECISION,
    ema26       DOUBLE PRECISION,
    rsi14       DOUBLE PRECISION,
    macd        DOUBLE PRECISION,
    macd_signal DOUBLE PRECISION,
    macd_hist   DOUBLE PRECISION,
    bb_mid      DOUBLE PRECISION,
    bb_upper    DOUBLE PRECISION,
    bb_lower    DOUBLE PRECISION,
    score       INTEGER,          -- summed indicator votes (-5..+5)
    signal      TEXT,             -- BUY / SELL / HOLD / NEUTRAL
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

SELECT create_hypertable('market_signals', 'time', if_not_exists => TRUE);

CREATE UNIQUE INDEX IF NOT EXISTS uq_signals_symbol_time
    ON market_signals (symbol, time);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time
    ON market_signals (symbol, time DESC);

SELECT add_retention_policy('market_signals', INTERVAL '365 days', if_not_exists => TRUE);
