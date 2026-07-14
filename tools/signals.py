"""Compute technical-indicator signals from market_ohlcv into market_signals.

Standard bundle per symbol: SMA(20/50), EMA(12/26), RSI(14), MACD(12/26/9),
Bollinger(20,2). A rule-based score across indicators yields BUY/SELL/HOLD.

Computed on whatever bars are stored (1-min live bars); indicators warm up as
history accumulates. Read-only market data — no order execution.

Usage:
    python tools/signals.py --once
    python tools/signals.py --interval 60
"""

import argparse
import math
import time
from datetime import datetime, timezone

import pandas as pd

from db import connect

LOOKBACK = 200  # bars pulled per symbol; enough to warm SMA50 / MACD.

UPSERT = """
INSERT INTO market_signals
  (time, symbol, close, sma20, sma50, ema12, ema26, rsi14,
   macd, macd_signal, macd_hist, bb_mid, bb_upper, bb_lower,
   score, signal, updated_at)
VALUES (%(time)s, %(symbol)s, %(close)s, %(sma20)s, %(sma50)s, %(ema12)s,
   %(ema26)s, %(rsi14)s, %(macd)s, %(macd_signal)s, %(macd_hist)s,
   %(bb_mid)s, %(bb_upper)s, %(bb_lower)s, %(score)s, %(signal)s, now())
ON CONFLICT (symbol, time) DO UPDATE SET
   close=EXCLUDED.close, sma20=EXCLUDED.sma20, sma50=EXCLUDED.sma50,
   ema12=EXCLUDED.ema12, ema26=EXCLUDED.ema26, rsi14=EXCLUDED.rsi14,
   macd=EXCLUDED.macd, macd_signal=EXCLUDED.macd_signal, macd_hist=EXCLUDED.macd_hist,
   bb_mid=EXCLUDED.bb_mid, bb_upper=EXCLUDED.bb_upper, bb_lower=EXCLUDED.bb_lower,
   score=EXCLUDED.score, signal=EXCLUDED.signal, updated_at=now()
WHERE market_signals.signal IS DISTINCT FROM EXCLUDED.signal
   OR market_signals.score  IS DISTINCT FROM EXCLUDED.score
   OR market_signals.rsi14  IS DISTINCT FROM EXCLUDED.rsi14;
"""


def _clean(x):
    """NaN/inf -> None so the DB stores NULL rather than a bad float."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return None
    return float(x)


def indicators(df):
    """Return the latest-bar indicator values from an OHLCV frame (time asc)."""
    close = df["close"].astype(float)
    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    rsi = 100 - 100 / (1 + rs)

    macd = ema12 - ema26
    macd_sig = macd.ewm(span=9, adjust=False).mean()
    macd_hist = macd - macd_sig

    bb_mid = sma20
    bb_std = close.rolling(20).std()
    bb_up = bb_mid + 2 * bb_std
    bb_lo = bb_mid - 2 * bb_std

    return {
        "close": close.iloc[-1],
        "sma20": sma20.iloc[-1], "sma50": sma50.iloc[-1],
        "ema12": ema12.iloc[-1], "ema26": ema26.iloc[-1],
        "rsi14": rsi.iloc[-1],
        "macd": macd.iloc[-1], "macd_signal": macd_sig.iloc[-1], "macd_hist": macd_hist.iloc[-1],
        "bb_mid": bb_mid.iloc[-1], "bb_upper": bb_up.iloc[-1], "bb_lower": bb_lo.iloc[-1],
    }


def score_signal(v):
    """Sum indicator votes into a score and a BUY/SELL/HOLD/NEUTRAL label."""
    votes, used = 0, 0

    def vote(cond_up, cond_dn):
        nonlocal votes, used
        used += 1
        votes += 1 if cond_up else (-1 if cond_dn else 0)

    c = v["close"]
    if v["sma20"] is not None and v["sma50"] is not None:
        vote(v["sma20"] > v["sma50"], v["sma20"] < v["sma50"])
    if v["ema12"] is not None and v["ema26"] is not None:
        vote(v["ema12"] > v["ema26"], v["ema12"] < v["ema26"])
    if v["rsi14"] is not None:
        vote(v["rsi14"] < 30, v["rsi14"] > 70)
    if v["macd_hist"] is not None:
        vote(v["macd_hist"] > 0, v["macd_hist"] < 0)
    if v["bb_lower"] is not None and v["bb_upper"] is not None and c is not None:
        vote(c < v["bb_lower"], c > v["bb_upper"])

    if used == 0:
        return None, "NEUTRAL"
    label = "BUY" if votes >= 2 else "SELL" if votes <= -2 else "HOLD"
    return votes, label


def compute_all():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM market_ohlcv")
        symbols = [r[0] for r in cur.fetchall()]

    rows = []
    for sym in symbols:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT time, close FROM market_ohlcv WHERE symbol=%s "
                "ORDER BY time DESC LIMIT %s",
                (sym, LOOKBACK),
            )
            data = cur.fetchall()
        if not data:
            continue
        df = pd.DataFrame(data, columns=["time", "close"]).iloc[::-1].reset_index(drop=True)
        vals = {k: _clean(x) for k, x in indicators(df).items()}
        score, label = score_signal(vals)
        rows.append({"time": df["time"].iloc[-1], "symbol": sym, "score": score,
                     "signal": label, **vals})

    if rows:
        with connect() as conn, conn.cursor() as cur:
            cur.executemany(UPSERT, rows)
    counts = {}
    for r in rows:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] signals for {len(rows)} symbols {counts}", flush=True)
    return len(rows)


def main():
    p = argparse.ArgumentParser(description="Compute indicator signals into market_signals.")
    p.add_argument("--interval", type=float, default=60.0)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    if args.once:
        compute_all()
        return
    try:
        while True:
            start = time.monotonic()
            compute_all()
            time.sleep(max(0.0, args.interval - (time.monotonic() - start)))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
