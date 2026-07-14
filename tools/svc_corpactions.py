"""Microservice: corporate actions (dividends/splits) -> corporate_actions.

Read-only. Loops on --interval (default 86400s = daily). SERVICE_NAME=corpactions.
Queries only stock symbols from the watchlist (crypto has no corp actions).
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

import alpaca
from db import connect
from heartbeat import beat

DDL = (
    "CREATE TABLE IF NOT EXISTS corporate_actions (id TEXT PRIMARY KEY, symbol TEXT, "
    "ca_type TEXT, ex_date DATE, record_date DATE, payable_date DATE, "
    "rate DOUBLE PRECISION, old_rate DOUBLE PRECISION, "
    "updated_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    "CREATE INDEX IF NOT EXISTS idx_ca_symbol_exdate ON corporate_actions (symbol, ex_date DESC);"
)
UPSERT = """
INSERT INTO corporate_actions (id, symbol, ca_type, ex_date, record_date, payable_date, rate, old_rate, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (id) DO UPDATE SET symbol=EXCLUDED.symbol, ca_type=EXCLUDED.ca_type,
  ex_date=EXCLUDED.ex_date, record_date=EXCLUDED.record_date,
  payable_date=EXCLUDED.payable_date, rate=EXCLUDED.rate, old_rate=EXCLUDED.old_rate,
  updated_at=now();
"""
TYPES = "cash_dividend,forward_split,reverse_split,stock_dividend"


def ensure():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)


def stock_symbols():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol FROM watchlist WHERE asset_type='stock' AND active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


def _row(ca_type, a):
    rate = a.get("rate")
    if rate is None:
        rate = a.get("new_rate")
    return (a.get("id"), a.get("symbol"), ca_type, a.get("ex_date"),
            a.get("record_date"), a.get("payable_date"), rate, a.get("old_rate"))


def cycle():
    symbols = stock_symbols()
    if not symbols:
        beat("ok", "no active stock symbols")
        return
    now = datetime.now(timezone.utc)
    params = {
        "symbols": ",".join(symbols), "types": TYPES,
        "start": (now - timedelta(days=90)).strftime("%Y-%m-%d"),
        "end": (now + timedelta(days=30)).strftime("%Y-%m-%d"),
        "limit": 1000,
    }
    payload = alpaca.get(f"{alpaca.DATA_URL}/v1/corporate-actions", params)
    ca = payload.get("corporate_actions") or {}
    rows = [_row(ca_type, a) for ca_type, arr in ca.items() for a in (arr or [])]
    if rows:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
    detail = f"{len(rows)} actions for {len(symbols)} symbols"
    beat("ok", detail)
    print(f"[{now:%H:%M:%S}] {detail}", flush=True)


def main():
    p = argparse.ArgumentParser(description="Corporate actions service.")
    p.add_argument("--interval", type=float, default=86400.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    ensure()
    if args.once:
        cycle()
        return
    while True:
        t0 = time.monotonic()
        sleep_for = args.interval
        try:
            cycle()
        except Exception as exc:  # noqa: BLE001
            print(f"cycle error: {exc}", flush=True)
            beat("error", str(exc)[:200])
            # Don't wait a full day to retry after a failure.
            sleep_for = min(args.interval, 3600.0)
        time.sleep(max(0.0, sleep_for - (time.monotonic() - t0)))


if __name__ == "__main__":
    main()
