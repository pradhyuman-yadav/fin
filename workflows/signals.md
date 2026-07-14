# Workflow: Technical-indicator signals

## Objective
Compute standard indicators per symbol from `market_ohlcv` and derive a
BUY/SELL/HOLD signal, stored in `market_signals`.

## Inputs
- `market_ohlcv` populated (poller running).
- DB up; `.env` with `DATABASE_URL`.

## Tools
- `tools/signals.py` — compute + upsert (loops, or `--once`).
- `db/init/02_signals.sql` — `market_signals` hypertable (365-day retention).

## Indicators (standard bundle)
SMA(20/50), EMA(12/26), RSI(14), MACD(12/26/9), Bollinger(20,2).

## Signal rule
Each indicator casts a vote (+1 bullish / -1 bearish / 0):
- SMA20 vs SMA50, EMA12 vs EMA26 (trend)
- RSI < 30 buy / > 70 sell (mean reversion)
- MACD histogram sign (momentum)
- Close vs Bollinger band (breakout/reversion)

`score` = sum of votes → **BUY** (>= +2), **SELL** (<= -2), **HOLD** (between),
**NEUTRAL** (no indicators warmed up yet).

## Steps
- Docker: runs as the `signals` service (`docker compose up -d`).
- Standalone: `python tools/signals.py --interval 60` (or `--once`).

## Outputs
Latest signal per symbol in `market_signals`; shown on the dashboard
(Signal + RSI columns).

## Edge cases
- Indicators are computed on the stored 1-min bars; they warm up as history
  grows (SMA50 needs 50 bars, RSI ~14, etc). Sparse symbols show NULLs / NEUTRAL.
- Not investment advice — a mechanical indicator score, nothing more.
- updated_at bumps only when signal/score/rsi actually change.
