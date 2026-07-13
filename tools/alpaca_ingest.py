"""Ingest historical OHLCV bars from Alpaca into the market_ohlcv hypertable.

Read-only market data — this tool does NOT place orders or move funds.

Auth: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY in .env.
Free accounts must use the IEX feed (ALPACA_FEED=iex, the default); the SIP
feed requires a paid market-data subscription.

Usage:
    python tools/alpaca_ingest.py --symbols AAPL,MSFT --start 2024-01-01 --end 2024-02-01
    python tools/alpaca_ingest.py --symbols AAPL --timeframe 1Hour --start 2024-01-01

Docs: https://docs.alpaca.markets/reference/stockbars
"""

import argparse
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from db import connect  # noqa: E402  (local tool import)

load_dotenv()

DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")
FEED = os.getenv("ALPACA_FEED", "iex")

UPSERT = """
INSERT INTO market_ohlcv (time, symbol, open, high, low, close, volume)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (symbol, time) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume;
"""


def _headers() -> dict:
    if not KEY_ID or not SECRET:
        sys.exit(
            "Missing Alpaca credentials. Set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY in .env."
        )
    return {"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET}


def fetch_bars(symbols, timeframe, start, end):
    """Yield (symbol, bar) tuples, following pagination until exhausted."""
    url = f"{DATA_URL}/v2/stocks/bars"
    params = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": start,
        "feed": FEED,
        "limit": 10000,
    }
    if end:
        params["end"] = end

    while True:
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            sys.exit(f"Alpaca API {resp.status_code}: {resp.text}")
        payload = resp.json()
        for symbol, bars in (payload.get("bars") or {}).items():
            for bar in bars:
                yield symbol, bar
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token


def _row(symbol, bar):
    ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return (ts, symbol, bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"), bar.get("v"))


def ingest(symbols, timeframe, start, end):
    rows = [_row(s, b) for s, b in fetch_bars(symbols, timeframe, start, end)]
    if not rows:
        print("No bars returned.")
        return 0
    with connect() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT, rows)
    print(f"Upserted {len(rows)} bars for {', '.join(sorted(set(r[1] for r in rows)))}.")
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="Ingest Alpaca OHLCV bars into TimescaleDB.")
    p.add_argument("--symbols", required=True, help="Comma-separated, e.g. AAPL,MSFT")
    p.add_argument("--timeframe", default="1Day", help="e.g. 1Min, 1Hour, 1Day, 1Week")
    p.add_argument("--start", required=True, help="ISO date/time, e.g. 2024-01-01")
    p.add_argument("--end", default=None, help="ISO date/time (optional)")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    ingest(symbols, args.timeframe, args.start, args.end)


if __name__ == "__main__":
    main()
