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
- KPI cards: rows, fresh count, latest-bar age, ingest rate, retention,
  DB latency, TimescaleDB + Postgres versions.
- Per-symbol table: last price, per-bar change %, signal + RSI, sparkline,
  freshness. Filter box + signal tuner; click a row to chart + filter news.
- OHLC candlestick + volume chart with range buttons and hover tooltip.
- Latest headlines panel (from the news service), filtered by selected symbol.
- Market open/closed pill driven by the live Alpaca clock (market_clock table),
  falling back to an approximate schedule if stale.
- Built-in explainer: the "? how this works" button opens in-page documentation
  covering the data flow, every panel, and the signal-vote logic.

Served by waitress (production WSGI) when installed; Flask dev server otherwise.

## Endpoints
- `GET /api/status` — health, KPIs, latest bar per symbol (JSON).
- `GET /api/sparklines?n=30` — last N closes per symbol.
- `GET /api/series?symbol=AAPL&limit=300` — recent OHLCV for candlesticks.

## Edge cases
- "DB unreachable" banner → start the container / check `DATABASE_URL`.
- Chart "not enough data" → fewer than 2 distinct-time bars for that symbol;
  runs full once the poller has collected bars across several minutes.
