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
- KPI cards: rows, fresh count, latest-bar age, bars ingested in the last hour,
  retention window, TimescaleDB version + query latency, Postgres version.
- Per-symbol table: last price, intraday change %, sparkline, volume, freshness.
  Filter box; click a row to chart. Approx US market open/closed indicator.
- OHLC candlestick + volume chart with range buttons (60/120/300/all) and hover.

## Endpoints
- `GET /api/status` — health, KPIs, latest bar per symbol (JSON).
- `GET /api/sparklines?n=30` — last N closes per symbol.
- `GET /api/series?symbol=AAPL&limit=300` — recent OHLCV for candlesticks.

## Edge cases
- "DB unreachable" banner → start the container / check `DATABASE_URL`.
- Chart "not enough data" → fewer than 2 distinct-time bars for that symbol;
  runs full once the poller has collected bars across several minutes.
