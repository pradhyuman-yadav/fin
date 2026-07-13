# Workflow: Pipeline dashboard

## Objective
Visualise pipeline health and stored data to confirm the platform is working.

## Inputs
- DB running (`docker compose up -d`) with data in `market_ohlcv`.
- `.env` with `DATABASE_URL`.

## Tools
- `tools/dashboard.py` — Flask app (read-only) on http://localhost:8000.

## Steps
1. `pip install -r requirements.txt`.
2. `python tools/dashboard.py` (optionally `--port 8080`).
3. Open http://localhost:8000. Auto-refreshes every 15s.

## What it shows
- Status cards: functional flag, TimescaleDB version, row/symbol counts,
  retention window, age of the latest bar.
- Per-symbol latest bar (close, volume, bar time, freshness). Click a row to chart.
- Price line chart for the selected symbol.

## Endpoints
- `GET /api/status` — health + latest bar per symbol (JSON).
- `GET /api/series?symbol=AAPL&limit=200` — recent closes for charting.

## Edge cases
- "DB unreachable" banner → start the container / check `DATABASE_URL`.
- Chart "not enough data" → fewer than 2 distinct-time bars for that symbol;
  runs full once the poller has collected bars across several minutes.
