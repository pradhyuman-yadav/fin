# Workflow: Set up TimescaleDB

## Objective
Run a local TimescaleDB instance for time-series (financial) data.

## Inputs
- `.env` with `POSTGRES_*` and `DATABASE_URL` (copy from `.env.example`).

## Tools
- `docker-compose.yml` — TimescaleDB service (image `timescale/timescaledb:latest-pg16`).
- `db/init/01_init.sql` — enables extension, creates `market_ohlcv` hypertable.
- `tools/db.py` — connection helper + health check.

## Steps
1. `cp .env.example .env` (edit password before any non-local use).
2. Start Docker Desktop, then: `docker compose up -d`.
3. Wait for healthy: `docker compose ps`.
4. Verify: `pip install -r requirements.txt` then `python tools/db.py`.

## Outputs
Running DB on `localhost:5432`, db `fin`, with `market_ohlcv` hypertable.

## Edge cases
- Port 5432 in use → change `POSTGRES_PORT` in `.env`.
- Init SQL only runs on a fresh volume. To re-run: `docker compose down -v` (DESTROYS data).
- Daemon not running → start Docker Desktop first.
