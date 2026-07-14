"""Microservice: real-time bar streaming from Alpaca WebSocket -> market_ohlcv.

One connection per endpoint (Alpaca free tier allows 1 each), so run this once
per market:  --market stock  and  --market crypto  as separate services.

Read-only market data. SERVICE_NAME=bars_stock / bars_crypto.
Symbols come from the watchlist table (asset_type filter).
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import websockets

import alpaca  # noqa: F401 - triggers load_dotenv and exposes FEED / CRYPTO_LOC
from alpaca_ingest import UPSERT
from db import connect
from heartbeat import beat

KEY = os.getenv("ALPACA_API_KEY_ID", "")
SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")
STREAM_BASE = os.getenv("ALPACA_STREAM_URL", "wss://stream.data.alpaca.markets")


def url_for(market):
    if market == "stock":
        return f"{STREAM_BASE}/v2/{alpaca.FEED}"
    return f"{STREAM_BASE}/v1beta3/crypto/{alpaca.CRYPTO_LOC}"


def load_symbols(market):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM watchlist WHERE active AND asset_type=%s ORDER BY symbol",
            (market,),
        )
        return [r[0] for r in cur.fetchall()]


def _row(m):
    ts = datetime.fromisoformat(m["t"].replace("Z", "+00:00")).astimezone(timezone.utc)
    return (ts, m["S"], m.get("o"), m.get("h"), m.get("l"), m.get("c"), m.get("v"))


async def _keepalive(market, n):
    """Heartbeat while the socket is connected (bars may be sparse after hours)."""
    while True:
        await asyncio.sleep(30)
        beat("ok", f"streaming {n} {market} bars")


async def run(market):
    if not KEY or not SECRET:
        sys.exit("Missing Alpaca credentials.")
    symbols = load_symbols(market)
    if not symbols:
        while True:
            beat("ok", f"no active {market} symbols")
            await asyncio.sleep(30)
    url = url_for(market)
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=2 ** 23) as ws:
                await ws.recv()  # {"T":"success","msg":"connected"}
                await ws.send(json.dumps({"action": "auth", "key": KEY, "secret": SECRET}))
                auth = json.loads(await ws.recv())
                if not (auth and auth[0].get("T") == "success"):
                    raise RuntimeError(f"auth failed: {auth}")
                await ws.send(json.dumps({"action": "subscribe", "bars": symbols}))
                await ws.recv()  # subscription confirmation
                beat("ok", f"subscribed {len(symbols)} {market} bars")
                print(f"streaming {len(symbols)} {market} bars from {url}", flush=True)
                ka = asyncio.create_task(_keepalive(market, len(symbols)))
                try:
                    async for raw in ws:
                        msgs = json.loads(raw)
                        rows = [_row(m) for m in msgs if m.get("T") == "b"]
                        if rows:
                            with connect() as conn, conn.cursor() as cur:
                                cur.executemany(UPSERT, rows)
                finally:
                    ka.cancel()
        except Exception as exc:  # noqa: BLE001
            print(f"{market} stream error, reconnecting in 5s: {exc}", flush=True)
            beat("reconnecting", str(exc)[:150])
            await asyncio.sleep(5)


def main():
    p = argparse.ArgumentParser(description="Alpaca WebSocket bar streamer.")
    p.add_argument("--market", choices=["stock", "crypto"], required=True)
    args = p.parse_args()
    asyncio.run(run(args.market))


if __name__ == "__main__":
    main()
