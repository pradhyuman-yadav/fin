-- Watchlist: single source of truth for which symbols to ingest.
-- Small config table (not a hypertable). Services read active rows.

CREATE TABLE IF NOT EXISTS watchlist (
    symbol      TEXT PRIMARY KEY,
    asset_type  TEXT NOT NULL CHECK (asset_type IN ('stock', 'crypto')),
    active      BOOLEAN NOT NULL DEFAULT true,
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO watchlist (symbol, asset_type) VALUES
    ('AAPL','stock'), ('MSFT','stock'), ('GOOGL','stock'), ('AMZN','stock'),
    ('NVDA','stock'), ('META','stock'), ('TSLA','stock'), ('AMD','stock'),
    ('NFLX','stock'), ('INTC','stock'), ('JPM','stock'), ('BAC','stock'),
    ('V','stock'), ('MA','stock'), ('DIS','stock'), ('KO','stock'),
    ('PEP','stock'), ('WMT','stock'), ('XOM','stock'), ('CVX','stock'),
    ('SPY','stock'), ('QQQ','stock'),
    ('BTC/USD','crypto'), ('ETH/USD','crypto'), ('SOL/USD','crypto'),
    ('LTC/USD','crypto'), ('DOGE/USD','crypto'), ('AVAX/USD','crypto')
ON CONFLICT (symbol) DO NOTHING;
