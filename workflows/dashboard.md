# Workflow: Pipeline dashboard

## Objective
Visualise pipeline health and stored data to confirm the platform is working.

## Inputs
- DB running (`docker compose up -d`) with data in `market_ohlcv`.
- `.env` with `DATABASE_URL`.

## Tools
- `tools/dashboard.py` — Flask app (read-only) on http://localhost:8000.

## Steps (Docker — recommended)
1. Keys in `.env` (for the poller).
2. `docker compose up -d --build` — starts db + poller + dashboard.
3. Open http://localhost:8000. Auto-refreshes every 15s.
4. `docker compose ps` / `docker compose logs -f poller` to watch.

## Steps (standalone, no container)
1. `pip install -r requirements.txt`.
2. `python tools/dashboard.py` (optionally `--port 8080`).
3. Open http://localhost:8000.

## What it shows
- Services strip: per-microservice heartbeat (green/amber/red against each
  service's own expected cadence via HEARTBEAT_MAX_AGE).
- KPI cards: rows, fresh count, latest-bar age, ingest rate, today's trading
  session (from market_calendar), DB latency, TimescaleDB + Postgres versions.
- Per-symbol table of calculated indicators: last price, per-bar change %,
  RSI(14), MACD histogram, SMA20, SMA50, sparkline, freshness. Stock rows show
  a muted age while the market is closed (expected, not a fault).
- Indicator readout for the selected symbol: RSI, MACD line/signal/hist,
  SMA(20/50), EMA(12/26), Bollinger upper/mid/lower.
- OHLC candlestick + volume chart with range buttons and hover tooltip.
- Dividends & splits panel (corporate_actions), with UPCOMING markers.
- Latest headlines panel (news), filtered by selected symbol.
- Market open/closed pill driven by the live Alpaca clock (market_clock table),
  falling back to an approximate schedule if stale.
- Built-in explainer: the "? how this works" button opens in-page documentation
  covering the data flow and every panel/column.

The BUY/SELL/HOLD signal column was removed from the UI in favor of raw
indicator values; the signals service still stores signal/score in
market_signals for later backtesting.

Served by waitress (production WSGI) when installed; Flask dev server otherwise.

## Streaming note (free tier)
Alpaca allows ONE stream connection per endpoint per account. The central
Portainer deployment owns it; local bars_stock/bars_crypto are behind the
`stream` compose profile (`docker compose --profile stream up -d`) and must
not run while the central deployment streams, or both loop on
"auth failed: code=406 connection limit exceeded".

## Endpoints
- `GET /api/status` — health, KPIs, latest bar per symbol (JSON).
- `GET /api/sparklines?n=30` — last N closes per symbol.
- `GET /api/series?symbol=AAPL&limit=300` — recent OHLCV for candlesticks.

## Edge cases
- "DB unreachable" banner → start the container / check `DATABASE_URL`.
- Chart "not enough data" → fewer than 2 distinct-time bars for that symbol;
  runs full once the poller has collected bars across several minutes.
