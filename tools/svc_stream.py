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

import alpaca  # loads .env; provides FEED / CRYPTO_LOC for url_for()
from alpaca_ingest import UPSERT
from db import connect
from heartbeat import beat

KEY = os.getenv("ALPACA_API_KEY_ID", "")
SECRET = os.getenv("ALPACA_API_SECRET_KEY", "")
STREAM_BASE = os.getenv("ALPACA_STREAM_URL", "wss://stream.data.alpaca.markets")
FREE_STOCK_STREAM_CAP = 30  # Alpaca free tier: max symbols on the stock stream
WATCHLIST_POLL_S = 60  # how often to re-check the watchlist for changes


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


def _expect_ok(frame, what):
    """Raise if an Alpaca control frame is an error (e.g. bad auth, sub limit)."""
    msgs = json.loads(frame) if isinstance(frame, (str, bytes)) else frame
    for m in msgs:
        if m.get("T") == "error":
            raise RuntimeError(f"{what} failed: code={m.get('code')} {m.get('msg')}")
    return msgs


async def _watchdog(ws, market, symbols):
    """Heartbeat while connected; close the socket if the watchlist changed so
    the outer loop reconnects with the new symbol set."""
    while True:
        await asyncio.sleep(WATCHLIST_POLL_S)
        beat("ok", f"streaming {len(symbols)} {market} bars")
        try:
            current = await asyncio.to_thread(load_symbols, market)
        except Exception as exc:  # noqa: BLE001 - DB blip; keep streaming
            print(f"watchlist check failed (keeping stream): {exc}", flush=True)
            continue
        if set(current) != set(symbols):
            print(f"watchlist changed ({len(symbols)} -> {len(current)}), resubscribing", flush=True)
            await ws.close()
            return


async def run(market):
    if not KEY or not SECRET:
        sys.exit("Missing Alpaca credentials.")
    url = url_for(market)
    while True:
        try:
            symbols = load_symbols(market)
            if not symbols:
                beat("ok", f"no active {market} symbols")
                await asyncio.sleep(WATCHLIST_POLL_S)
                continue
            if market == "stock" and len(symbols) > FREE_STOCK_STREAM_CAP:
                print(f"WARNING: {len(symbols)} stock symbols exceeds the free-tier "
                      f"stream cap of {FREE_STOCK_STREAM_CAP}; Alpaca may reject the "
                      "subscription", flush=True)
            async with websockets.connect(url, ping_interval=20, max_size=2 ** 23) as ws:
                _expect_ok(await ws.recv(), "connect")
                await ws.send(json.dumps({"action": "auth", "key": KEY, "secret": SECRET}))
                _expect_ok(await ws.recv(), "auth")
                await ws.send(json.dumps({"action": "subscribe", "bars": symbols}))
                _expect_ok(await ws.recv(), "subscribe")
                beat("ok", f"subscribed {len(symbols)} {market} bars")
                print(f"streaming {len(symbols)} {market} bars from {url}", flush=True)
                wd = asyncio.create_task(_watchdog(ws, market, symbols))
                try:
                    async for raw in ws:
                        msgs = json.loads(raw)
                        rows = [_row(m) for m in msgs if m.get("T") == "b"]
                        if rows:
                            with connect() as conn, conn.cursor() as cur:
                                cur.executemany(UPSERT, rows)
                finally:
                    wd.cancel()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # 406 = another connection with these keys already holds the stream
            # (free tier: 1 per endpoint). Back off hard so we don't fight it.
            delay = 60 if ("connection limit" in msg or "406" in msg) else 5
            print(f"{market} stream error, reconnecting in {delay}s: {msg}", flush=True)
            beat("reconnecting", msg[:150])
            await asyncio.sleep(delay)


def main():
    p = argparse.ArgumentParser(description="Alpaca WebSocket bar streamer.")
    p.add_argument("--market", choices=["stock", "crypto"], required=True)
    args = p.parse_args()
    asyncio.run(run(args.market))


if __name__ == "__main__":
    main()
