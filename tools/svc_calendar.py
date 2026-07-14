"""Microservice: market clock + trading calendar -> market_clock, market_calendar.

Read-only. Loops on --interval (default 300s). SERVICE_NAME=calendar.
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

import alpaca
from db import connect
from heartbeat import beat

DDL = (
    "CREATE TABLE IF NOT EXISTS market_clock (id BOOLEAN PRIMARY KEY DEFAULT true "
    "CHECK (id), ts TIMESTAMPTZ, is_open BOOLEAN, next_open TIMESTAMPTZ, "
    "next_close TIMESTAMPTZ, updated_at TIMESTAMPTZ NOT NULL DEFAULT now());"
    "CREATE TABLE IF NOT EXISTS market_calendar (date DATE PRIMARY KEY, "
    '"open" TEXT, "close" TEXT, session_open TEXT, session_close TEXT, '
    "settlement_date DATE);"
)
CLOCK_UPSERT = """
INSERT INTO market_clock (id, ts, is_open, next_open, next_close, updated_at)
VALUES (true, %s, %s, %s, %s, now())
ON CONFLICT (id) DO UPDATE SET ts=EXCLUDED.ts, is_open=EXCLUDED.is_open,
  next_open=EXCLUDED.next_open, next_close=EXCLUDED.next_close, updated_at=now();
"""
CAL_UPSERT = """
INSERT INTO market_calendar (date, "open", "close", session_open, session_close, settlement_date)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (date) DO UPDATE SET "open"=EXCLUDED."open", "close"=EXCLUDED."close",
  session_open=EXCLUDED.session_open, session_close=EXCLUDED.session_close,
  settlement_date=EXCLUDED.settlement_date;
"""


def ensure():
    with connect() as conn, conn.cursor() as cur:
        cur.execute(DDL)


def cycle():
    clock = alpaca.get(f"{alpaca.TRADE_URL}/v2/clock")
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (now + timedelta(days=30)).strftime("%Y-%m-%d")
    cal = alpaca.get(f"{alpaca.TRADE_URL}/v2/calendar", {"start": start, "end": end})
    cal_rows = [
        (d["date"], d.get("open"), d.get("close"), d.get("session_open"),
         d.get("session_close"), d.get("settlement_date"))
        for d in cal
    ]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(CLOCK_UPSERT, (clock["timestamp"], clock["is_open"],
                                   clock["next_open"], clock["next_close"]))
        if cal_rows:
            cur.executemany(CAL_UPSERT, cal_rows)
    detail = f"is_open={clock['is_open']}, {len(cal_rows)} calendar days"
    beat("ok", detail)
    print(f"[{now:%H:%M:%S}] {detail}", flush=True)


def main():
    p = argparse.ArgumentParser(description="Market clock + calendar service.")
    p.add_argument("--interval", type=float, default=300.0)
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
