# Workflow: Ingest Alpaca market data

## Objective
Pull historical OHLCV bars from Alpaca and upsert them into `market_ohlcv`.
Read-only market data — no order execution.

## Inputs
- `.env` with `ALPACA_API_KEY_ID`, `ALPACA_API_SECRET_KEY` (from
  https://app.alpaca.markets/ -> API keys). Free accounts: keep `ALPACA_FEED=iex`.
- Symbols, timeframe, start/end date range.

## Tools
- `tools/alpaca_ingest.py` — fetch bars (paginated) and upsert.
- `tools/db.py` — connection + health check.
- TimescaleDB running (`docker compose up -d`).

## Steps
1. Ensure DB is up: `python tools/db.py`.
2. Add Alpaca keys to `.env`.
3. Run:
   `python tools/alpaca_ingest.py --symbols AAPL,MSFT --timeframe 1Day --start 2024-01-01 --end 2024-02-01`
4. Verify:
   `docker exec fin_timescaledb psql -U fin -d fin -c "SELECT symbol, count(*) FROM market_ohlcv GROUP BY symbol;"`

## Outputs
Bars in `market_ohlcv`, deduped on `(symbol, time)` via upsert.

## Edge cases
- 403 / "subscription" error → `feed=sip` needs a paid plan; use `iex`.
- 429 rate limit → free tier is 200 req/min; batch symbols in one call (the
  `symbols` param is comma-separated) rather than one call per symbol.
- Re-running the same range is safe (upsert overwrites, no duplicates).
- Times stored as UTC (`TIMESTAMPTZ`).
