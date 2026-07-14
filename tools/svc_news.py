"""Microservice: market news headlines -> news.

Read-only. Loops on --interval (default 900s = 15m). SERVICE_NAME=news.
Queries stock symbols from the watchlist.
"""

import argparse
import time
from datetime import datetime, timezone

import alpaca
from db import connect
from heartbeat import beat

DDL = (
    "CREATE TABLE IF NOT EXISTS news (id BIGINT PRIMARY KEY, created_at TIMESTAMPTZ, "
    "updated_at TIMESTAMPTZ, headline TEXT, summary TEXT, author TEXT, source TEXT, "
    "url TEXT, symbols TEXT);"
    "CREATE INDEX IF NOT EXISTS idx_news_created ON news (created_at DESC);"
)
UPSERT = """
INSERT INTO news (id, created_at, updated_at, headline, summary, author, source, url, symbols)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (id) DO UPDATE SET created_at=EXCLUDED.created_at, updated_at=EXCLUDED.updated_at,
  headline=EXCLUDED.headline, summary=EXCLUDED.summary, author=EXCLUDED.author,
  source=EXCLUDED.source, url=EXCLUDED.url, symbols=EXCLUDED.symbols;
"""


def ensure():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)


def stock_symbols():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol FROM watchlist WHERE asset_type='stock' AND active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


def _row(n):
    syms = n.get("symbols")
    return (n.get("id"), n.get("created_at"), n.get("updated_at"), n.get("headline"),
            n.get("summary"), n.get("author"), n.get("source"), n.get("url"),
            ",".join(syms) if isinstance(syms, list) else None)


def cycle():
    symbols = stock_symbols()
    if not symbols:
        beat("ok", "no active stock symbols")
        return
    params = {"symbols": ",".join(symbols), "limit": 50, "sort": "desc",
              "exclude_contentless": "true"}
    payload = alpaca.get(f"{alpaca.DATA_URL}/v1beta1/news", params)
    rows = [_row(n) for n in (payload.get("news") or [])]
    if rows:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
    detail = f"{len(rows)} headlines"
    beat("ok", detail)
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}] {detail}", flush=True)


def main():
    p = argparse.ArgumentParser(description="News service.")
    p.add_argument("--interval", type=float, default=900.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    ensure()
    if args.once:
        cycle()
        return
    while True:
        t0 = time.monotonic()
        try:
            cycle()
        except Exception as exc:  # noqa: BLE001
            print(f"cycle error: {exc}", flush=True)
            beat("error", str(exc)[:200])
        time.sleep(max(0.0, args.interval - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
