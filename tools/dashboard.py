"""Local dashboard to monitor the fin pipeline.

Read-only status page: service health, DB/TimescaleDB status, per-symbol
latest bars with calculated indicators (RSI, MACD, SMA/EMA, Bollinger),
OHLC candlestick + volume chart, market session info, corporate actions,
news headlines, and a built-in "how it works" explainer.

Usage:
    python tools/dashboard.py                      # http://localhost:8000
    python tools/dashboard.py --host 0.0.0.0 --port 8000   # for containers

Serves via waitress when installed (production WSGI); falls back to the
Flask dev server. Requires the DB running (docker compose up -d).
"""

import argparse
import time

from flask import Flask, jsonify, request

from db import connect

app = Flask(__name__)


def _q(sql, params=None, one=False):
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        cols = [c.name for c in cur.description]
    dicts = [dict(zip(cols, r)) for r in rows]
    return (dicts[0] if dicts else None) if one else dicts


def _int_arg(name, default, maximum):
    """Parse an int query param defensively; bad input falls back to default."""
    try:
        return max(1, min(int(request.args.get(name, default)), maximum))
    except (TypeError, ValueError):
        return default


def _active_symbol_filter():
    """SQL WHERE fragment limiting to active watchlist symbols. Empty string
    (no filter) if the watchlist table is missing or has no active rows, so
    the dashboard still works on a DB without a watchlist."""
    try:
        r = _q("SELECT count(*) AS n FROM watchlist WHERE active", one=True)
        if r and r["n"]:
            return "WHERE symbol IN (SELECT symbol FROM watchlist WHERE active)"
    except Exception:  # noqa: BLE001
        pass
    return ""


def _clock():
    """Latest market clock from the calendar service; None if unavailable."""
    try:
        r = _q(
            "SELECT is_open, next_open, next_close, "
            "EXTRACT(EPOCH FROM now()-updated_at) AS age_s FROM market_clock WHERE id",
            one=True,
        )
        if not r:
            return None
        return {
            "is_open": r["is_open"],
            "next_open": r["next_open"].isoformat() if r["next_open"] else None,
            "next_close": r["next_close"].isoformat() if r["next_close"] else None,
            "age_s": float(r["age_s"]) if r["age_s"] is not None else None,
        }
    except Exception:  # noqa: BLE001
        return None


def _session():
    """Today's trading-calendar row (ET trading day); None if unavailable."""
    try:
        r = _q(
            'SELECT "open" AS o, "close" AS c, session_open, session_close, settlement_date '
            "FROM market_calendar WHERE date=(now() AT TIME ZONE 'America/New_York')::date",
            one=True,
        )
        if not r:
            return {"trading_day": False}
        return {
            "trading_day": True, "open": r["o"], "close": r["c"],
            "session_open": r["session_open"], "session_close": r["session_close"],
            "settlement": r["settlement_date"].isoformat() if r["settlement_date"] else None,
        }
    except Exception:  # noqa: BLE001
        return None


@app.get("/api/status")
def status():
    t0 = time.monotonic()
    try:
        pg = _q("SELECT version() AS v", one=True)["v"]
        ext = _q("SELECT extversion AS v FROM pg_extension WHERE extname='timescaledb'", one=True)
        ret = _q(
            "SELECT config->>'drop_after' AS d FROM timescaledb_information.jobs "
            "WHERE proc_name='policy_retention' AND hypertable_name='market_ohlcv'",
            one=True,
        )
        tot = _q(
            "SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols, "
            "max(time) AS latest, EXTRACT(EPOCH FROM now()-max(time)) AS latest_age_s, "
            "count(*) FILTER (WHERE updated_at > now()-interval '1 hour') AS written_1h "
            "FROM market_ohlcv",
            one=True,
        )
        flt = _active_symbol_filter()
        latest = _q(
            "SELECT DISTINCT ON (symbol) symbol, time, open, high, low, close, volume, updated_at "
            f"FROM market_ohlcv {flt} ORDER BY symbol, time DESC"
        )
        sig = _q(
            "SELECT DISTINCT ON (symbol) symbol, rsi14, sma20, sma50, ema12, ema26, "
            "macd, macd_signal, macd_hist, bb_upper, bb_mid, bb_lower "
            "FROM market_signals ORDER BY symbol, time DESC"
        )
        sigmap = {r["symbol"]: r for r in sig}
        latency = round((time.monotonic() - t0) * 1000, 1)

        def bar(r):
            s = sigmap.get(r["symbol"]) or {}
            return {
                "symbol": r["symbol"], "time": r["time"].isoformat(),
                "open": r["open"], "high": r["high"], "low": r["low"],
                "close": r["close"], "volume": r["volume"],
                "updated_at": r["updated_at"].isoformat(),
                "rsi": s.get("rsi14"),
                "sma20": s.get("sma20"), "sma50": s.get("sma50"),
                "ema12": s.get("ema12"), "ema26": s.get("ema26"),
                "macd": s.get("macd"), "macd_signal": s.get("macd_signal"),
                "macd_hist": s.get("macd_hist"),
                "bb_upper": s.get("bb_upper"), "bb_mid": s.get("bb_mid"),
                "bb_lower": s.get("bb_lower"),
            }

        return jsonify(
            {
                "ok": True,
                "healthy": bool(tot and tot["rows"]),
                "postgres": pg.split(" on ")[0] if pg else None,
                "timescaledb": ext["v"] if ext else None,
                "retention": ret["d"] if ret else None,
                "rows": tot["rows"] if tot else 0,
                "symbols": tot["symbols"] if tot else 0,
                "latest": tot["latest"].isoformat() if tot and tot["latest"] else None,
                "latest_age_s": float(tot["latest_age_s"]) if tot and tot["latest_age_s"] is not None else None,
                "written_1h": tot["written_1h"] if tot else 0,
                "db_latency_ms": latency,
                "clock": _clock(),
                "session": _session(),
                "bars": [bar(r) for r in latest],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/health")
def health_svc():
    try:
        rows = _q(
            "SELECT service, status, detail, max_age_s, "
            "EXTRACT(EPOCH FROM now()-last_run) AS age_s FROM service_health ORDER BY service"
        )
        return jsonify({"ok": True, "services": [
            {"service": r["service"], "status": r["status"], "detail": r["detail"],
             "max_age_s": float(r["max_age_s"]) if r.get("max_age_s") is not None else None,
             "age_s": float(r["age_s"]) if r["age_s"] is not None else None}
            for r in rows
        ]})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/news")
def news_api():
    limit = _int_arg("limit", 30, 100)
    try:
        rows = _q(
            "SELECT id, created_at, headline, source, url, symbols FROM news "
            "ORDER BY created_at DESC NULLS LAST LIMIT %s",
            (limit,),
        )
        return jsonify({"ok": True, "news": [
            {"id": r["id"], "t": r["created_at"].isoformat() if r["created_at"] else None,
             "headline": r["headline"], "source": r["source"], "url": r["url"],
             "symbols": r["symbols"]}
            for r in rows
        ]})
    except Exception:  # noqa: BLE001 - news table optional; panel degrades
        return jsonify({"ok": False, "news": []})


@app.get("/api/corpactions")
def corpactions_api():
    limit = _int_arg("limit", 20, 100)
    try:
        rows = _q(
            "SELECT symbol, ca_type, ex_date, payable_date, rate, old_rate "
            "FROM corporate_actions WHERE ex_date >= CURRENT_DATE - INTERVAL '21 days' "
            "ORDER BY ex_date DESC LIMIT %s",
            (limit,),
        )
        return jsonify({"ok": True, "actions": [
            {"symbol": r["symbol"], "type": r["ca_type"],
             "ex_date": r["ex_date"].isoformat() if r["ex_date"] else None,
             "payable_date": r["payable_date"].isoformat() if r["payable_date"] else None,
             "rate": r["rate"], "old_rate": r["old_rate"]}
            for r in rows
        ]})
    except Exception:  # noqa: BLE001 - table optional; panel degrades
        return jsonify({"ok": False, "actions": []})


@app.get("/api/sparklines")
def sparklines():
    n = _int_arg("n", 30, 200)
    flt = _active_symbol_filter()
    rows = _q(
        "SELECT symbol, close FROM ("
        "  SELECT symbol, time, close, row_number() OVER "
        f"         (PARTITION BY symbol ORDER BY time DESC) rn FROM market_ohlcv {flt}"
        ") s WHERE rn <= %s ORDER BY symbol, time",
        (n,),
    )
    out = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(r["close"])
    return jsonify(out)


@app.get("/api/series")
def series():
    symbol = request.args.get("symbol", "").upper()[:20]
    limit = _int_arg("limit", 300, 5000)
    rows = _q(
        "SELECT time, open, high, low, close, volume FROM market_ohlcv "
        "WHERE symbol=%s ORDER BY time DESC LIMIT %s",
        (symbol, limit),
    )
    rows.reverse()
    return jsonify(
        {
            "symbol": symbol,
            "points": [
                {"t": r["time"].isoformat(), "o": r["open"], "h": r["high"],
                 "l": r["low"], "c": r["close"], "v": r["volume"]}
                for r in rows
            ],
        }
    )


@app.get("/")
def index():
    return PAGE


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fin · monitor</title>
<style>
  :root{--bg:#0b0f14;--card:#141a22;--line:#222b36;--fg:#e6edf3;--mut:#8b98a9;
        --ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#58a6ff;--up:#3fb950;--down:#f85149}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
  header{padding:14px 22px;border-bottom:1px solid var(--line);display:flex;
         align-items:center;gap:14px;position:sticky;top:0;background:var(--bg);z-index:5;flex-wrap:wrap}
  header h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
  .pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
        font-size:12px;font-weight:600;border:1px solid var(--line)}
  .pill .dot{width:8px;height:8px;border-radius:50%;background:var(--mut)}
  .pill.ok{color:var(--ok)}.pill.ok .dot{background:var(--ok);box-shadow:0 0 8px var(--ok)}
  .pill.bad{color:var(--bad)}.pill.bad .dot{background:var(--bad);box-shadow:0 0 8px var(--bad)}
  .pill.warn{color:var(--warn)}.pill.warn .dot{background:var(--warn)}
  .spacer{flex:1}
  .muted{color:var(--mut)}
  .helpbtn{background:var(--card);border:1px solid var(--line);color:var(--mut);border-radius:8px;
        padding:4px 12px;font-size:12px;cursor:pointer;font-weight:600}
  .helpbtn.on{color:var(--accent);border-color:var(--accent)}
  main{padding:22px;max-width:1240px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .card .k{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em}
  .card .v{font-size:22px;font-weight:700;margin-top:6px;word-break:break-word}
  .card .s{font-size:12px;color:var(--mut);margin-top:2px}
  section{margin-top:22px}
  .row{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
  h2{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;margin:0}
  input#filter{background:var(--card);border:1px solid var(--line);color:var(--fg);
        border-radius:8px;padding:6px 10px;font-size:13px;outline:none;width:160px}
  input#filter:focus{border-color:var(--accent)}
  .panel{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
  .grid2{display:grid;grid-template-columns:1.2fr .8fr;gap:16px}
  @media(max-width:900px){.grid2{grid-template-columns:1fr}}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th,td{text-align:right;padding:6px 7px;border-bottom:1px solid var(--line);font-size:13px}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.04em;cursor:help}
  tbody tr{cursor:pointer}
  tbody tr:hover td{background:#1a2230}
  tbody tr.sel td{background:#1e2a3d}
  .chg.up{color:var(--up)}.chg.down{color:var(--down)}
  .tag{font-size:9px;padding:1px 5px;border-radius:5px;font-weight:700;
       letter-spacing:.04em;margin-left:6px;vertical-align:middle}
  .tag.s{background:#16283f;color:#58a6ff}
  .tag.c{background:#2b2140;color:#bd8cff}
  .fresh{color:var(--ok)}.stale{color:var(--warn)}.old{color:var(--bad)}
  .btns{display:flex;gap:6px}
  .btns button{background:var(--card);border:1px solid var(--line);color:var(--mut);
        border-radius:7px;padding:4px 10px;font-size:12px;cursor:pointer}
  .btns button.on{color:var(--fg);border-color:var(--accent)}
  svg text{fill:var(--mut);font-size:10px}
  .err{background:#2d1214;border:1px solid var(--bad);color:#ffb4ae;padding:12px;border-radius:10px}
  .svcs{display:flex;gap:10px;flex-wrap:wrap}
  .svc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px 12px;min-width:120px}
  .svc .n{font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px}
  .svc .d{font-size:11px;color:var(--mut);margin-top:3px}
  .svc .dot{width:8px;height:8px;border-radius:50%}
  .svc.ok .dot{background:var(--ok);box-shadow:0 0 6px var(--ok)}
  .svc.warn .dot{background:var(--warn)}
  .svc.bad .dot{background:var(--bad);box-shadow:0 0 6px var(--bad)}
  .spark{width:72px;height:22px;vertical-align:middle}
  #tt{position:fixed;pointer-events:none;background:#0b0f14;border:1px solid var(--line);
      border-radius:6px;padding:6px 8px;font-size:12px;display:none;z-index:9}
  #docs{display:none;background:var(--card);border:1px solid var(--line);border-radius:12px;
        padding:18px 22px;margin-top:16px;font-size:13px;line-height:1.65}
  #docs.open{display:block}
  #docs h3{font-size:12px;color:var(--accent);text-transform:uppercase;letter-spacing:.06em;
        margin:14px 0 4px}
  #docs h3:first-child{margin-top:0}
  #docs p{margin:4px 0;color:var(--fg)}
  #docs .flow{font-family:ui-monospace,Consolas,monospace;font-size:12px;color:var(--mut);
        background:#0b0f14;border:1px solid var(--line);border-radius:8px;padding:10px 12px;
        overflow-x:auto;white-space:pre}
  #docs .disc{color:var(--warn);font-size:12px;margin-top:12px}
  #docs td,#docs th{font-size:12px;text-align:left;padding:3px 10px 3px 0;border:none}
  .news-item{padding:7px 0;border-bottom:1px solid var(--line);font-size:12.5px}
  .news-item:last-child{border-bottom:none}
  .news-item a{color:var(--fg);text-decoration:none}
  .news-item a:hover{color:var(--accent)}
  .news-item .m{color:var(--mut);font-size:11px;margin-top:1px}
  .ind{display:grid;grid-template-columns:repeat(auto-fit,minmax(90px,1fr));gap:8px;margin-top:10px}
  .ind .i{background:#0e131a;border:1px solid var(--line);border-radius:8px;padding:7px 9px}
  .ind .i .k{font-size:10px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
  .ind .i .v{font-size:14px;font-weight:600;margin-top:2px}
  .ca-item{display:flex;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--line);font-size:12.5px}
  .ca-item:last-child{border-bottom:none}
  .ca-item .sym{font-weight:700;min-width:52px}
  .ca-item .typ{color:var(--accent);font-size:11px}
  .ca-item .fut{color:var(--ok);font-size:10px;font-weight:700;letter-spacing:.04em}
</style></head>
<body>
<header>
  <h1>fin · pipeline monitor</h1>
  <span id="statuspill" class="pill" title="Green when the DB is reachable and holds bar data"><span class="dot"></span><span>—</span></span>
  <span id="market" class="pill"><span class="dot"></span><span>market —</span></span>
  <span class="spacer"></span>
  <button class="helpbtn" id="helpbtn">? how this works</button>
  <span class="muted" id="clock"></span>
  <span class="muted">· refresh <span id="cd">15</span>s</span>
</header>
<main>
  <div id="docs">
    <h3>What is this?</h3>
    <p>A read-only monitor for the <b>fin</b> market-data pipeline. It reads the TimescaleDB
    that the ingestion microservices write to. Nothing on this page places trades.</p>

    <h3>Data flow</h3>
    <div class="flow">Alpaca WebSocket ──▶ bars_stock / bars_crypto ──▶ market_ohlcv (1-min OHLCV bars)
Alpaca REST      ──▶ calendar · corpactions · news ─▶ market_clock/calendar · corporate_actions · news
market_ohlcv     ──▶ signals service ──▶ market_signals (calculated indicators)
this dashboard   ──▶ reads the DB only (no writes)</div>

    <h3>Services strip</h3>
    <p>One card per microservice. Every service writes a heartbeat after each cycle, with its
    own expected cadence (streams ≈1&nbsp;min, calendar 5&nbsp;min, news 15&nbsp;min, corporate actions daily).
    <span class="fresh">green</span> = on schedule · <span class="stale">amber</span> = late or reconnecting ·
    <span class="old">red</span> = errored or 2× overdue. "reconnecting · connection limit" on a stream
    means another process holds the single Alpaca stream slot for these API keys — the free tier
    allows one connection per endpoint, so only one deployment may stream at a time.</p>

    <h3>KPI cards</h3>
    <table>
      <tr><th>Rows</th><td>1-min bars stored (retention drops bars older than 365 days)</td></tr>
      <tr><th>Fresh</th><td>symbols whose latest bar is under 20 min old — crypto streams 24/7, stocks only during market hours</td></tr>
      <tr><th>Latest bar</th><td>age of the newest bar across all symbols</td></tr>
      <tr><th>Ingested 1h</th><td>bar rows written in the last hour</td></tr>
      <tr><th>Session</th><td>today's regular trading hours from the exchange calendar (ET)</td></tr>
      <tr><th>Latency</th><td>how long this page's DB status query took</td></tr>
    </table>

    <h3>Symbols table — calculated indicators</h3>
    <table>
      <tr><th>Last</th><td>close of the newest 1-min bar</td></tr>
      <tr><th>Chg%</th><td>that bar's close vs its own open (per-bar move, not daily change)</td></tr>
      <tr><th>RSI</th><td>RSI(14) momentum oscillator: below ~30 = oversold, above ~70 = overbought</td></tr>
      <tr><th>MACD</th><td>MACD(12,26,9) histogram: positive = short-term momentum above its signal line, negative = below</td></tr>
      <tr><th>SMA20 / SMA50</th><td>simple moving averages of the last 20 / 50 one-minute closes; SMA20 above SMA50 suggests a short-term uptrend</td></tr>
      <tr><th>Trend</th><td>sparkline of the last 30 closes</td></tr>
      <tr><th>Age</th><td>bar freshness: <span class="fresh">&lt;20m</span> · <span class="stale">&lt;48h</span> · <span class="old">older</span>. Stocks show a muted age while the market is closed — that is normal, not a fault.</td></tr>
    </table>
    <p>STOCK = IEX feed (free tier: 15-min delayed, partial market volume). CRYPTO = 24/7 stream.
    Click a row to load its chart, full indicator readout, and filtered headlines.
    Indicators warm up as bars accumulate — SMA50 needs 50 minutes of data; blank means
    not enough bars yet.</p>

    <h3>Indicator readout (right panel)</h3>
    <p>All calculated values for the selected symbol: RSI(14), MACD line / signal / histogram,
    SMA(20/50), EMA(12/26), and Bollinger(20,2) upper / middle / lower bands. Values are
    computed by the signals service each minute from stored bars.</p>

    <h3>Dividends &amp; splits / Headlines</h3>
    <p>Corporate actions (from the daily corpactions service) show recent and upcoming
    dividends/splits for watchlist stocks — <span class="fresh">UPCOMING</span> marks ex-dates
    from today onward. Headlines come from the news service (15-min cycle); selecting a stock
    filters them.</p>

    <h3>Good to know</h3>
    <p>All timestamps are UTC unless marked ET. Bars are keyed by their exchange timestamp, so
    re-fetches never duplicate. If a stream service restarts, the minutes it was down are not
    auto-backfilled. The market pill uses Alpaca's live clock when fresh, else an approximate
    schedule.</p>

    <p class="disc">⚠ Indicators are mechanical calculations for monitoring — not investment advice.</p>
  </div>

  <div id="err"></div>
  <section><h2>Services</h2><div class="svcs" id="services"></div></section>
  <div class="cards" id="cards"></div>

  <section class="grid2">
    <div>
      <div class="row"><h2>Symbols</h2><span class="spacer"></span>
        <input id="filter" placeholder="filter…" autocomplete="off"></div>
      <div class="panel" style="padding:4px 8px">
        <table><thead><tr>
          <th title="Symbol. STOCK = IEX feed, CRYPTO = 24/7 stream. Click a row for chart + indicators.">Sym</th>
          <th title="Close of the newest 1-min bar">Last</th>
          <th title="Latest bar's close vs its own open (per-bar move)">Chg%</th>
          <th title="RSI(14): below ~30 oversold, above ~70 overbought">RSI</th>
          <th title="MACD(12,26,9) histogram: + momentum above signal line, − below">MACD</th>
          <th title="Simple moving average of last 20 closes">SMA20</th>
          <th title="Simple moving average of last 50 closes">SMA50</th>
          <th title="Last 30 closes">Trend</th>
          <th title="Age of the newest bar. Stocks pause outside market hours (muted).">Age</th>
        </tr></thead><tbody id="rows"></tbody></table>
      </div>
    </div>
    <div>
      <div class="row"><h2 id="ctitle">Chart</h2><span class="spacer"></span>
        <div class="btns" id="ranges">
          <button data-n="60">60</button><button data-n="120">120</button>
          <button data-n="300" class="on">300</button><button data-n="5000">all</button></div>
      </div>
      <div class="panel">
        <svg id="chart" viewBox="0 0 560 240" width="100%"></svg>
        <svg id="vol" viewBox="0 0 560 70" width="100%"></svg>
        <div class="ind" id="ind"></div>
      </div>
      <section>
        <div class="row"><h2>Dividends &amp; splits</h2></div>
        <div class="panel" id="corp"><span class="muted">loading…</span></div>
      </section>
      <section>
        <div class="row"><h2 id="ntitle">Headlines</h2></div>
        <div class="panel" id="news"><span class="muted">loading…</span></div>
      </section>
    </div>
  </section>
</main>
<div id="tt"></div>
<script>
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let selected=null, sparks={}, limit=300, filter="", lastBars=[], newsCache=[], corpCache=[], lastClock=null;

function fmt(v){if(v==null)return '—';
  const a=Math.abs(v);
  if(a>=1000)return v.toFixed(0);
  if(a>=1)return v.toFixed(2);
  if(a===0)return '0';
  return v.toPrecision(3);}

function age(s){if(s==null)return['—',''];const m=s/60;
  if(m<2)return[Math.round(s)+'s','fresh'];
  if(m<20)return[Math.round(m)+'m','fresh'];
  if(m<60)return[Math.round(m)+'m','stale'];
  const h=m/60; if(h<48)return[h.toFixed(1)+'h','stale'];
  return[(h/24).toFixed(1)+'d','old'];}

function approxMarket(){ // fallback when the live clock is unavailable
  const d=new Date(), day=d.getUTCDay(), mins=d.getUTCHours()*60+d.getUTCMinutes();
  if(day===0||day===6) return['warn','market closed (approx)'];
  if(mins>=810 && mins<1200) return['ok','market open (approx)'];
  return['warn','market closed (approx)'];}

function marketOpen(){ // best-known market state for row muting
  if(lastClock && lastClock.age_s!=null && lastClock.age_s<900 && lastClock.is_open!=null)
    return lastClock.is_open;
  return approxMarket()[0]==='ok';}

function marketPill(clock){
  const mp=$('#market');
  if(clock && clock.age_s!=null && clock.age_s<900 && clock.is_open!=null){
    mp.className='pill '+(clock.is_open?'ok':'warn');
    mp.children[1].textContent=clock.is_open?'market open':'market closed';
    const fmtT=x=>x?x.replace('T',' ').slice(5,16)+' UTC':'?';
    mp.title=clock.is_open?('closes '+fmtT(clock.next_close)):('opens '+fmtT(clock.next_open));
  }else{
    const [mc,mt]=approxMarket(); mp.className='pill '+mc;
    mp.children[1].textContent=mt; mp.title='live clock unavailable; approximate schedule';
  }
}

function sparkSVG(vals){
  vals=(vals||[]).filter(v=>v!=null);
  if(vals.length<2) return '<svg class="spark"></svg>';
  const min=Math.min(...vals),max=Math.max(...vals),r=(max-min)||1;
  const pts=vals.map((v,i)=>`${(i/(vals.length-1)*70+1).toFixed(1)},${(20-(v-min)/r*18+1).toFixed(1)}`).join(' ');
  const up=vals[vals.length-1]>=vals[0];
  return `<svg class="spark" viewBox="0 0 72 22"><polyline fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.5" points="${pts}"/></svg>`;}

async function renderHealth(){
  let h; try{h=await (await fetch('/api/health')).json();}catch(e){h={ok:false};}
  const box=$('#services');
  if(!h.ok || !h.services || !h.services.length){box.innerHTML='<div class="svc"><div class="n muted">no service heartbeats yet</div></div>';return;}
  box.innerHTML=h.services.map(s=>{
    const [t]=age(s.age_s);
    const maxA=s.max_age_s||600;
    let cls='ok';
    if(s.status==='error') cls='bad';
    else if(s.status==='reconnecting') cls='warn';
    else if(s.age_s!=null && s.age_s>2*maxA) cls='bad';
    else if(s.age_s!=null && s.age_s>maxA) cls='warn';
    return `<div class="svc ${cls}" title="expected heartbeat every ~${Math.round(maxA/2)}s">
      <div class="n"><span class="dot"></span>${esc(s.service)}</div>
      <div class="d">${esc(s.status||'?')} · ${t} ago</div>
      <div class="d">${esc((s.detail||'').slice(0,42))}</div></div>`;}).join('');
}

function renderNews(){
  const box=$('#news');
  if(!newsCache.length){box.innerHTML='<span class="muted">No headlines yet (news service warms up on its next cycle).</span>';return;}
  let items=newsCache, label='latest';
  if(selected && !selected.includes('/')){
    const f=newsCache.filter(n=>(n.symbols||'').split(',').includes(selected));
    if(f.length){items=f;label=selected;}
  }
  $('#ntitle').textContent='Headlines · '+label;
  box.innerHTML=items.slice(0,8).map(n=>`<div class="news-item">
    <a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.headline)}</a>
    <div class="m">${n.t?esc(n.t.replace('T',' ').slice(5,16)):''} UTC · ${esc(n.source||'')} · ${esc((n.symbols||'').split(',').slice(0,4).join(' '))}</div>
  </div>`).join('');
}

function renderCorp(){
  const box=$('#corp');
  if(!corpCache.length){box.innerHTML='<span class="muted">No dividends/splits in the last 3 weeks for watchlist stocks.</span>';return;}
  const today=new Date().toISOString().slice(0,10);
  box.innerHTML=corpCache.slice(0,7).map(a=>{
    const typ=(a.type||'').replace(/_/g,' ').replace(/s$/,'');
    const fut=a.ex_date && a.ex_date>=today;
    const amt=a.type&&a.type.includes('dividend')?('$'+fmt(a.rate)):
      (a.old_rate!=null?`${fmt(a.old_rate)}→${fmt(a.rate)}`:fmt(a.rate));
    return `<div class="ca-item"><span class="sym">${esc(a.symbol)}</span>
      <span class="typ">${esc(typ)}</span><span>${amt}</span>
      <span class="spacer"></span>
      ${fut?'<span class="fut">UPCOMING</span>':''}
      <span class="muted">ex ${esc(a.ex_date||'?')}</span></div>`;}).join('');
}

function renderIndicators(){
  const box=$('#ind');
  const b=lastBars.find(x=>x.symbol===selected);
  if(!b){box.innerHTML='';return;}
  const item=(k,v,tip)=>`<div class="i" title="${tip}"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  box.innerHTML=
    item('RSI 14',b.rsi!=null?b.rsi.toFixed(1):'—','momentum: <30 oversold, >70 overbought')+
    item('MACD',fmt(b.macd),'MACD line (EMA12 − EMA26)')+
    item('Signal',fmt(b.macd_signal),'9-period EMA of the MACD line')+
    item('Hist',fmt(b.macd_hist),'MACD − signal: momentum direction')+
    item('SMA 20',fmt(b.sma20),'20-bar simple moving average')+
    item('SMA 50',fmt(b.sma50),'50-bar simple moving average')+
    item('EMA 12',fmt(b.ema12),'12-bar exponential moving average')+
    item('EMA 26',fmt(b.ema26),'26-bar exponential moving average')+
    item('BB up',fmt(b.bb_upper),'Bollinger upper band (SMA20 + 2σ)')+
    item('BB mid',fmt(b.bb_mid),'Bollinger middle band (SMA20)')+
    item('BB low',fmt(b.bb_lower),'Bollinger lower band (SMA20 − 2σ)');
}

async function refresh(){
  renderHealth();
  let d; try{d=await (await fetch('/api/status')).json();}catch(e){d={ok:false,error:e.message};}
  const err=$('#err');
  if(!d.ok){err.innerHTML='<div class="err">DB unreachable: '+esc(d.error||'?')+'</div>';
    $('#statuspill').className='pill bad';$('#statuspill').children[1].textContent='offline';return;}
  err.innerHTML='';
  const sp=$('#statuspill'); sp.className='pill '+(d.healthy?'ok':'bad');
  sp.children[1].textContent=d.healthy?'functional':'no data';
  lastClock=d.clock;
  marketPill(d.clock);

  try{sparks=await (await fetch('/api/sparklines?n=30')).json();}catch(e){sparks={};}
  try{const nr=await (await fetch('/api/news?limit=30')).json();newsCache=nr.news||[];}catch(e){newsCache=[];}
  try{const cr=await (await fetch('/api/corpactions?limit=20')).json();corpCache=cr.actions||[];}catch(e){corpCache=[];}

  const [aTxt]=age(d.latest_age_s);
  const bars=d.bars||[];
  const fresh=bars.filter(b=>(Date.now()-new Date(b.time))/1000<1200).length;
  const ses=d.session;
  const sesV=ses==null?'—':(ses.trading_day?((ses.open||'?')+'–'+(ses.close||'?')+' ET'):'closed');
  const sesS=ses==null?'calendar unavailable':(ses.trading_day?'regular hours today':'not a trading day');
  const card=(k,v,s,tip)=>`<div class="card" ${tip?`title="${tip}"`:''}><div class="k">${k}</div><div class="v">${v}</div>${s?`<div class="s">${s}</div>`:''}</div>`;
  $('#cards').innerHTML=
    card('Rows',(d.rows||0).toLocaleString(),d.symbols+' symbols','1-min bars stored (365-day retention)')+
    card('Fresh',fresh+'/'+bars.length,'< 20m old','symbols with a recent bar; stocks pause after market close')+
    card('Latest bar',aTxt,'ago','age of the newest bar across all symbols')+
    card('Ingested 1h',(d.written_1h||0).toLocaleString(),'writes','bar rows written in the last hour')+
    card('Session',sesV,sesS,"today's regular trading hours (exchange calendar)")+
    card('Latency',(d.db_latency_ms??'—')+' ms','status query','time this page took to query the DB')+
    card('Database',d.timescaledb?('TS '+d.timescaledb):'—',(d.postgres||'').replace('PostgreSQL','PG'),'TimescaleDB extension + PostgreSQL server versions');

  lastBars=bars.slice().sort((a,b)=>a.symbol<b.symbol?-1:1);
  renderRows();
  if(!selected && lastBars.length){selected=lastBars[0].symbol;drawChart();}
  renderIndicators();
  renderNews();
  renderCorp();
  $('#clock').textContent=new Date().toLocaleTimeString();
}

function renderRows(){
  const f=filter.trim().toUpperCase();
  const open=marketOpen();
  const rows=lastBars.filter(b=>!f||b.symbol.includes(f)).map(b=>{
    const secs=(Date.now()-new Date(b.time))/1000;let [t,c]=age(secs);
    const sv=sparks[b.symbol]||[];
    const chg=(sv.length>=2 && b.open)?((b.close-b.open)/b.open*100):null;
    const chgTxt=chg==null?'—':(chg>=0?'+':'')+chg.toFixed(2)+'%';
    const chgCls=chg==null?'':(chg>=0?'up':'down');
    const isC=b.symbol.includes('/');
    let ageTitle='';
    if(!isC && !open && c!=='fresh'){c='muted';ageTitle='market closed — stock bars resume at next open';}
    const mh=b.macd_hist;
    return `<tr data-sym="${esc(b.symbol)}" class="${b.symbol===selected?'sel':''}">
      <td><b>${esc(b.symbol)}</b><span class="tag ${isC?'c':'s'}">${isC?'CRYPTO':'STOCK'}</span></td>
      <td>${b.close!=null?fmt(b.close):'—'}</td>
      <td class="chg ${chgCls}">${chgTxt}</td>
      <td class="muted">${b.rsi!=null?b.rsi.toFixed(0):'—'}</td>
      <td class="chg ${mh==null?'':(mh>=0?'up':'down')}">${fmt(mh)}</td>
      <td class="muted">${fmt(b.sma20)}</td>
      <td class="muted">${fmt(b.sma50)}</td>
      <td>${sparkSVG(sv)}</td>
      <td class="${c}" title="${ageTitle}">${t}</td></tr>`;}).join('');
  $('#rows').innerHTML=rows||'<tr><td colspan="9" class="muted">No symbols match.</td></tr>';
  document.querySelectorAll('#rows tr[data-sym]').forEach(tr=>
    tr.onclick=()=>{selected=tr.dataset.sym;
      document.querySelectorAll('#rows tr').forEach(x=>x.classList.remove('sel'));
      tr.classList.add('sel');drawChart();renderIndicators();renderNews();});
}

async function drawChart(){
  $('#ctitle').textContent='Chart · '+selected;
  let d; try{d=await (await fetch('/api/series?symbol='+encodeURIComponent(selected)+'&limit='+limit)).json();}
  catch(e){$('#chart').innerHTML='<text x="16" y="120">Chart unavailable.</text>';return;}
  const p=d.points||[];const c=$('#chart'),vo=$('#vol');
  const W=560,H=240,pad=44,VH=70;
  if(p.length<1){c.innerHTML='<text x="16" y="120">No data.</text>';vo.innerHTML='';return;}
  if(p.length<2){c.innerHTML=`<text x="16" y="120">Only 1 bar so far. Candles appear once bars span multiple minutes.</text>`;vo.innerHTML='';return;}
  const his=p.map(x=>x.h),los=p.map(x=>x.l);
  const min=Math.min(...los),max=Math.max(...his),rng=(max-min)||1;
  const n=p.length, bw=Math.max(2,(W-pad-8)/n*0.7);
  const X=i=>pad+(i+0.5)/n*(W-pad-8);
  const Y=v=>H-24-(v-min)/rng*(H-48);
  let g='';[0,.25,.5,.75,1].forEach(f=>{const y=H-24-f*(H-48);const v=min+f*rng;
    g+=`<line x1="${pad}" y1="${y}" x2="${W-4}" y2="${y}" stroke="#1c2430"/><text x="4" y="${y+3}">${v.toFixed(2)}</text>`;});
  let candles='';
  p.forEach((b,i)=>{const up=b.c>=b.o;const col=up?'#3fb950':'#f85149';const x=X(i);
    candles+=`<line x1="${x}" y1="${Y(b.h)}" x2="${x}" y2="${Y(b.l)}" stroke="${col}"/>`+
      `<rect x="${x-bw/2}" y="${Y(Math.max(b.o,b.c))}" width="${bw}" height="${Math.max(1,Math.abs(Y(b.o)-Y(b.c)))}" fill="${col}"/>`;});
  const t0=p[0].t.replace('T',' ').slice(5,16),t1=p[p.length-1].t.replace('T',' ').slice(5,16);
  c.innerHTML=g+candles+`<text x="${pad}" y="${H-6}">${t0}</text><text x="${W-70}" y="${H-6}">${t1}</text>`;
  const vmax=Math.max(...p.map(x=>x.v||0))||1;
  let bars='';p.forEach((b,i)=>{const h=(b.v||0)/vmax*(VH-16);const x=X(i);
    bars+=`<rect x="${x-bw/2}" y="${VH-6-h}" width="${bw}" height="${h}" fill="${b.c>=b.o?'#265c33':'#5c2626'}"/>`;});
  vo.innerHTML=bars+`<text x="4" y="10">vol</text>`;
  attachHover(c,p,X);
}

function attachHover(svg,p,X){
  const tt=$('#tt');svg.onmousemove=e=>{const r=svg.getBoundingClientRect();
    const rel=(e.clientX-r.left)/r.width*560;let bi=0,bd=1e9;
    p.forEach((b,i)=>{const dx=Math.abs(X(i)-rel);if(dx<bd){bd=dx;bi=i;}});
    const b=p[bi];tt.style.display='block';tt.style.left=(e.clientX+12)+'px';tt.style.top=(e.clientY+12)+'px';
    tt.innerHTML=`<b>${b.t.replace('T',' ').slice(5,16)}</b><br>O ${b.o} H ${b.h}<br>L ${b.l} C ${b.c}<br>V ${(b.v||0).toLocaleString()}`;};
  svg.onmouseleave=()=>$('#tt').style.display='none';}

$('#filter').oninput=e=>{filter=e.target.value;renderRows();};
$('#helpbtn').onclick=()=>{const d=$('#docs');d.classList.toggle('open');
  $('#helpbtn').classList.toggle('on',d.classList.contains('open'));};
document.querySelectorAll('#ranges button').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('#ranges button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');limit=+b.dataset.n;drawChart();});

let cd=15;
setInterval(()=>{cd--;if(cd<=0){cd=15;refresh();}$('#cd').textContent=cd;},1000);
refresh();
</script></body></html>"""


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="fin pipeline dashboard")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    try:
        from waitress import serve
        print(f"Serving on http://{args.host}:{args.port} (waitress)", flush=True)
        serve(app, host=args.host, port=args.port, threads=8)
    except ImportError:
        print("waitress not installed; using Flask dev server", flush=True)
        app.run(host=args.host, port=args.port, debug=False)
