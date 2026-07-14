"""Poll latest 1-minute OHLCV bars from Alpaca and upsert into market_ohlcv.

Read-only market data — no order execution. Retention (drop > 365 days) is
handled by the TimescaleDB policy, not this tool.

Free tier: IEX feed, 200 requests/min. A 1Min bar only updates once per minute,
so polling faster than ~60s re-fetches the same bar and wastes quota. The
default interval is 60s; --max-rpm caps request rate regardless.

Usage:
    python tools/alpaca_live.py --once                 # one cycle, then exit
    python tools/alpaca_live.py                         # loop forever, 60s
    python tools/alpaca_live.py --interval 30           # loop every 30s
    python tools/alpaca_live.py --watchlist config/watchlist.txt

Docs: https://docs.alpaca.markets/reference/stocklatestbars
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from alpaca_ingest import UPSERT  # reuse the upsert SQL
from db import connect
from heartbeat import beat

load_dotenv()

DATA_URL = os.getenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")
FEED = os.getenv("ALPACA_FEED", "iex")
# Crypto data location: us (Alpaca US), us-1/eu-1 (Kraken). Crypto is free, 24/7.
CRYPTO_LOC = os.getenv("ALPACA_CRYPTO_LOC", "us")

# Free tier: 200 req/min. Alpaca caps symbols/request; batch to stay well under.
SYMBOLS_PER_REQUEST = 1000
DEFAULT_MAX_RPM = 200


def _headers():
    if not KEY_ID or not SECRET:
        sys.exit("Missing Alpaca credentials. Set ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY in .env.")
    return {"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET}


def load_watchlist_file(path):
    with open(path, encoding="utf-8") as fh:
        syms = []
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                syms.append(line.upper())
    if not syms:
        sys.exit(f"Watchlist {path} is empty.")
    return syms


def load_watchlist_db():
    """Read active symbols from the DB watchlist table (single source of truth)."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT symbol FROM watchlist WHERE active ORDER BY symbol")
        return [r[0] for r in cur.fetchall()]


def load_watchlist(path=None):
    """Prefer the DB watchlist; fall back to a file if a path is given or the
    DB table is unavailable/empty."""
    if path:
        return load_watchlist_file(path)
    try:
        syms = load_watchlist_db()
        if syms:
            print(f"watchlist: {len(syms)} symbols from DB", flush=True)
            return syms
        print("watchlist DB empty; falling back to config/watchlist.txt", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"watchlist DB read failed ({exc}); using config/watchlist.txt", flush=True)
    return load_watchlist_file("config/watchlist.txt")


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _get_bars(url, symbols, extra, limiter):
    """Fetch latest bars from one endpoint, batched + rate-limited."""
    out = {}
    for batch in _chunks(symbols, SYMBOLS_PER_REQUEST):
        params = {"symbols": ",".join(batch), **extra}
        limiter.wait()
        resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", "5"))
            print(f"  429 rate-limited, sleeping {retry}s", flush=True)
            time.sleep(retry)
            limiter.wait()
            resp = requests.get(url, headers=_headers(), params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  API {resp.status_code}: {resp.text[:200]}", flush=True)
            continue
        out.update(resp.json().get("bars") or {})
    return out


def fetch_latest(symbols, limiter):
    """Return {symbol: bar} for the latest 1Min bar of each symbol.

    Symbols containing '/' (e.g. BTC/USD) are routed to the crypto endpoint;
    the rest go to the stock endpoint.
    """
    stocks = [s for s in symbols if "/" not in s]
    crypto = [s for s in symbols if "/" in s]
    out = {}
    if stocks:
        out.update(_get_bars(f"{DATA_URL}/v2/stocks/bars/latest", stocks, {"feed": FEED}, limiter))
    if crypto:
        url = f"{DATA_URL}/v1beta3/crypto/{CRYPTO_LOC}/latest/bars"
        out.update(_get_bars(url, crypto, {}, limiter))
    return out


def _row(symbol, bar):
    ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return (ts, symbol, bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c"), bar.get("v"))


def cycle(symbols, limiter):
    bars = fetch_latest(symbols, limiter)
    rows = [_row(s, b) for s, b in bars.items() if b]
    if rows:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    beat("ok", f"{len(rows)}/{len(symbols)} symbols")
    print(f"[{stamp}] upserted {len(rows)}/{len(symbols)} symbols", flush=True)
    return len(rows)


class RateLimiter:
    """Simple min-interval limiter to stay under max requests/min."""

    def __init__(self, max_rpm):
        self.min_gap = 60.0 / max_rpm if max_rpm > 0 else 0.0
        self._last = 0.0

    def wait(self):
        gap = time.monotonic() - self._last
        if gap < self.min_gap:
            time.sleep(self.min_gap - gap)
        self._last = time.monotonic()


def main():
    p = argparse.ArgumentParser(description="Poll Alpaca latest 1Min bars into TimescaleDB.")
    p.add_argument("--watchlist", default=None,
                   help="File path to read symbols from. Omit to read the DB watchlist table.")
    p.add_argument("--interval", type=float, default=60.0, help="Seconds between cycles.")
    p.add_argument("--max-rpm", type=int, default=DEFAULT_MAX_RPM, help="Max requests/min.")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    args = p.parse_args()

    symbols = load_watchlist(args.watchlist)
    limiter = RateLimiter(args.max_rpm)
    print(f"Polling {len(symbols)} symbols, feed={FEED}, interval={args.interval}s", flush=True)

    if args.once:
        cycle(symbols, limiter)
        return
    try:
        while True:
            start = time.monotonic()
            try:
                cycle(symbols, limiter)
            except Exception as exc:  # noqa: BLE001 - keep polling through blips
                print(f"cycle error: {exc}", flush=True)
                beat("error", str(exc)[:200])
            elapsed = time.monotonic() - start
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
