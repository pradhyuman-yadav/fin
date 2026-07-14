"""Local dashboard to monitor the fin pipeline.

Read-only status page: service health, DB/TimescaleDB status, per-symbol
latest bars with signals + sparklines, OHLC candlestick + volume chart,
latest news headlines, and a built-in "how it works" explainer.

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
            "SELECT DISTINCT ON (symbol) symbol, signal, score, rsi14, "
            "sma20, sma50, ema12, ema26, macd_hist, bb_upper, bb_lower "
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
                "signal": s.get("signal"), "score": s.get("score"), "rsi": s.get("rsi14"),
                "sma20": s.get("sma20"), "sma50": s.get("sma50"),
                "ema12": s.get("ema12"), "ema26": s.get("ema26"),
                "macd_hist": s.get("macd_hist"),
                "bb_upper": s.get("bb_upper"), "bb_lower": s.get("bb_lower"),
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
  main{padding:22px;max-width:1200px;margin:0 auto}
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
  .grid2{display:grid;grid-template-columns:1.15fr .85fr;gap:16px}
  @media(max-width:900px){.grid2{grid-template-columns:1fr}}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
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
  .sig{font-size:10px;font-weight:700;padding:2px 7px;border-radius:6px;letter-spacing:.03em}
  .sig.BUY{background:#0f2e1a;color:#3fb950}
  .sig.SELL{background:#2d1214;color:#f85149}
  .sig.HOLD{background:#2a2410;color:#d29922}
  .sig.NEUTRAL{background:#1c2430;color:#8b98a9}
  .ctl{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:10px}
  .ctl label{font-size:11px;color:var(--mut);display:flex;align-items:center;gap:4px}
  .ctl input[type=number]{width:52px;background:var(--card);border:1px solid var(--line);
        color:var(--fg);border-radius:6px;padding:4px 6px;font-size:12px;outline:none}
  .ctl input:focus{border-color:var(--accent)}
  .ctl select{background:var(--card);border:1px solid var(--line);color:var(--fg);
        border-radius:6px;padding:4px 8px;font-size:12px;outline:none}
  .ctl .reset{background:none;border:1px solid var(--line);color:var(--mut);
        border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer}
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
  .spark{width:80px;height:22px;vertical-align:middle}
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
Alpaca REST      ──▶ calendar · corpactions · news ─▶ market_clock · corporate_actions · news
market_ohlcv     ──▶ signals service ──▶ market_signals (indicators + BUY/SELL/HOLD)
this dashboard   ──▶ reads the DB only (no writes)</div>

    <h3>Services strip</h3>
    <p>One card per microservice. Every service writes a heartbeat after each cycle, with its
    own expected cadence (streams ≈1&nbsp;min, calendar 5&nbsp;min, news 15&nbsp;min, corporate actions daily).
    <span class="fresh">green</span> = on schedule · <span class="stale">amber</span> = late or reconnecting ·
    <span class="old">red</span> = errored or 2× overdue.</p>

    <h3>KPI cards</h3>
    <table>
      <tr><th>Rows</th><td>1-min bars stored (retention drops bars older than 365 days)</td></tr>
      <tr><th>Fresh</th><td>symbols whose latest bar is under 20 min old — crypto streams 24/7, stocks only during market hours, so stocks going grey after the close is normal</td></tr>
      <tr><th>Latest bar</th><td>age of the newest bar across all symbols</td></tr>
      <tr><th>Ingested 1h</th><td>bar rows written in the last hour</td></tr>
      <tr><th>Latency</th><td>how long this page's DB status query took</td></tr>
    </table>

    <h3>Symbols table</h3>
    <table>
      <tr><th>Last</th><td>close of the newest 1-min bar</td></tr>
      <tr><th>Chg%</th><td>that bar's close vs its own open (per-bar move, not daily change)</td></tr>
      <tr><th>Signal / RSI</th><td>from the signals service (see below)</td></tr>
      <tr><th>Trend</th><td>sparkline of the last 30 closes</td></tr>
      <tr><th>Age</th><td>bar freshness: <span class="fresh">&lt;20m</span> · <span class="stale">&lt;48h</span> · <span class="old">older</span></td></tr>
    </table>
    <p>STOCK = IEX feed (free tier: 15-min delayed, partial market volume). CRYPTO = 24/7 stream.
    Click a row to load its candlestick chart and filter headlines.</p>

    <h3>Signal logic</h3>
    <p>Five mechanical votes, each +1 (bullish) / −1 (bearish) / 0: SMA20 vs SMA50 ·
    EMA12 vs EMA26 · RSI(14) below oversold / above overbought · MACD histogram sign ·
    close outside Bollinger(20,2) bands. <b>score</b> = sum. BUY at ≥ +2, SELL at ≤ −2,
    otherwise HOLD (NEUTRAL until enough bars accumulate — SMA50 needs 50 minutes of data).</p>
    <p>The tuner above the table re-scores <i>in your browser only</i> (saved in localStorage).
    Stored signals in the DB keep the default thresholds.</p>

    <h3>Good to know</h3>
    <p>All timestamps are UTC. Bars are keyed by their exchange timestamp, so re-fetches never
    duplicate. If a stream service restarts, the minutes it was down are not auto-backfilled.
    The market pill uses Alpaca's live clock when fresh, else an approximate schedule.</p>

    <p class="disc">⚠ Signals are a mechanical indicator score for monitoring — not investment advice.</p>
  </div>

  <div id="err"></div>
  <section><h2>Services</h2><div class="svcs" id="services"></div></section>
  <div class="cards" id="cards"></div>

  <section class="grid2">
    <div>
      <div class="row"><h2>Symbols</h2><span class="spacer"></span>
        <input id="filter" placeholder="filter…" autocomplete="off"></div>
      <div class="ctl">
        <label>signal
          <select id="sigfilter">
            <option value="ALL">all</option><option value="BUY">buy</option>
            <option value="SELL">sell</option><option value="HOLD">hold</option>
            <option value="NEUTRAL">neutral</option></select></label>
        <span class="spacer"></span>
        <label title="RSI below this = oversold = bullish vote">RSI oversold <input type="number" id="rsiLo" min="1" max="99"></label>
        <label title="RSI above this = overbought = bearish vote">overbought <input type="number" id="rsiHi" min="1" max="99"></label>
        <label title="score at or above this = BUY">BUY&ge; <input type="number" id="buyCut" min="1" max="5"></label>
        <label title="score at or below this = SELL">SELL&le; <input type="number" id="sellCut" min="-5" max="-1"></label>
        <button class="reset" id="reset" title="restore default thresholds (30/70, +2/-2)">reset</button>
      </div>
      <div class="panel" style="padding:4px 8px">
        <table><thead><tr>
          <th title="Symbol. STOCK = IEX feed, CRYPTO = 24/7 stream. Click a row to chart it.">Sym</th>
          <th title="Close of the newest 1-min bar">Last</th>
          <th title="Latest bar's close vs its own open (per-bar move)">Chg%</th>
          <th title="Mechanical indicator score: BUY / SELL / HOLD. See 'how this works'.">Signal</th>
          <th title="RSI(14): <30 oversold, >70 overbought (defaults)">RSI</th>
          <th title="Last 30 closes">Trend</th>
          <th title="Age of the newest bar. Stocks pause outside market hours.">Age</th>
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
      </div>
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
let selected=null, sparks={}, limit=300, filter="", lastBars=[], newsCache=[];

// Client-side signal tuner — re-scores from raw indicators, no server round-trip.
const DEF={rsiLo:30,rsiHi:70,buy:2,sell:-2,filter:'ALL'};
let cfg=Object.assign({},DEF,JSON.parse(localStorage.getItem('sigcfg')||'{}'));
function saveCfg(){localStorage.setItem('sigcfg',JSON.stringify(cfg));}

function recompute(b){
  let votes=0,used=0;
  const v=(up,dn)=>{used++;votes+=up?1:(dn?-1:0);};
  if(b.sma20!=null&&b.sma50!=null) v(b.sma20>b.sma50,b.sma20<b.sma50);
  if(b.ema12!=null&&b.ema26!=null) v(b.ema12>b.ema26,b.ema12<b.ema26);
  if(b.rsi!=null) v(b.rsi<cfg.rsiLo,b.rsi>cfg.rsiHi);
  if(b.macd_hist!=null) v(b.macd_hist>0,b.macd_hist<0);
  if(b.bb_lower!=null&&b.bb_upper!=null&&b.close!=null) v(b.close<b.bb_lower,b.close>b.bb_upper);
  if(used===0) return{score:null,label:'NEUTRAL'};
  return{score:votes,label:votes>=cfg.buy?'BUY':votes<=cfg.sell?'SELL':'HOLD'};
}

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

function marketPill(clock){
  const mp=$('#market');
  if(clock && clock.age_s!=null && clock.age_s<900 && clock.is_open!=null){
    mp.className='pill '+(clock.is_open?'ok':'warn');
    mp.children[1].textContent=clock.is_open?'market open':'market closed';
    const fmt=x=>x?x.replace('T',' ').slice(5,16)+' UTC':'?';
    mp.title=clock.is_open?('closes '+fmt(clock.next_close)):('opens '+fmt(clock.next_open));
  }else{
    const [mc,mt]=approxMarket(); mp.className='pill '+mc;
    mp.children[1].textContent=mt; mp.title='live clock unavailable; approximate schedule';
  }
}

function sparkSVG(vals){
  vals=(vals||[]).filter(v=>v!=null);
  if(vals.length<2) return '<svg class="spark"></svg>';
  const min=Math.min(...vals),max=Math.max(...vals),r=(max-min)||1;
  const pts=vals.map((v,i)=>`${(i/(vals.length-1)*78+1).toFixed(1)},${(20-(v-min)/r*18+1).toFixed(1)}`).join(' ');
  const up=vals[vals.length-1]>=vals[0];
  return `<svg class="spark" viewBox="0 0 80 22"><polyline fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.5" points="${pts}"/></svg>`;}

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

async function renderNews(){
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

async function refresh(){
  renderHealth();
  let d; try{d=await (await fetch('/api/status')).json();}catch(e){d={ok:false,error:e.message};}
  const err=$('#err');
  if(!d.ok){err.innerHTML='<div class="err">DB unreachable: '+esc(d.error||'?')+'</div>';
    $('#statuspill').className='pill bad';$('#statuspill').children[1].textContent='offline';return;}
  err.innerHTML='';
  const sp=$('#statuspill'); sp.className='pill '+(d.healthy?'ok':'bad');
  sp.children[1].textContent=d.healthy?'functional':'no data';
  marketPill(d.clock);

  try{sparks=await (await fetch('/api/sparklines?n=30')).json();}catch(e){sparks={};}
  try{const nr=await (await fetch('/api/news?limit=30')).json();newsCache=nr.news||[];}catch(e){newsCache=[];}

  const [aTxt]=age(d.latest_age_s);
  const bars=d.bars||[];
  const fresh=bars.filter(b=>(Date.now()-new Date(b.time))/1000<1200).length;
  const card=(k,v,s,tip)=>`<div class="card" ${tip?`title="${tip}"`:''}><div class="k">${k}</div><div class="v">${v}</div>${s?`<div class="s">${s}</div>`:''}</div>`;
  $('#cards').innerHTML=
    card('Rows',(d.rows||0).toLocaleString(),d.symbols+' symbols','1-min bars stored (365-day retention)')+
    card('Fresh',fresh+'/'+bars.length,'< 20m old','symbols with a recent bar; stocks pause after market close')+
    card('Latest bar',aTxt,'ago','age of the newest bar across all symbols')+
    card('Ingested 1h',(d.written_1h||0).toLocaleString(),'writes','bar rows written in the last hour')+
    card('Retention',d.retention||'—','drop older','TimescaleDB policy drops bars past this age')+
    card('Latency',(d.db_latency_ms??'—')+' ms','status query','time this page took to query the DB')+
    card('Database',d.timescaledb?('TS '+d.timescaledb):'—',(d.postgres||'').replace('PostgreSQL','PG'),'TimescaleDB extension + PostgreSQL server versions');

  lastBars=bars.slice().sort((a,b)=>a.symbol<b.symbol?-1:1);
  renderRows();
  if(!selected && lastBars.length){selected=lastBars[0].symbol;drawChart();}
  renderNews();
  $('#clock').textContent=new Date().toLocaleTimeString();
}

function renderRows(){
  const f=filter.trim().toUpperCase();
  const rows=lastBars.filter(b=>!f||b.symbol.includes(f)).map(b=>{
    const secs=(Date.now()-new Date(b.time))/1000;const[t,c]=age(secs);
    const sv=sparks[b.symbol]||[];
    const chg=(sv.length>=2 && b.open)?((b.close-b.open)/b.open*100):null;
    const chgTxt=chg==null?'—':(chg>=0?'+':'')+chg.toFixed(2)+'%';
    const chgCls=chg==null?'':(chg>=0?'up':'down');
    const isC=b.symbol.includes('/');
    const {label:sig,score}=recompute(b);
    const rsi=b.rsi!=null?b.rsi.toFixed(0):'—';
    const title=score!=null?`score ${score} of 5 votes`:'indicators still warming up';
    return {sig, html:`<tr data-sym="${esc(b.symbol)}" class="${b.symbol===selected?'sel':''}">
      <td><b>${esc(b.symbol)}</b><span class="tag ${isC?'c':'s'}">${isC?'CRYPTO':'STOCK'}</span></td>
      <td>${b.close!=null?b.close.toFixed(2):'—'}</td>
      <td class="chg ${chgCls}">${chgTxt}</td>
      <td><span class="sig ${sig}" title="${title}">${sig}</span></td>
      <td class="muted">${rsi}</td>
      <td>${sparkSVG(sv)}</td>
      <td class="${c}">${t}</td></tr>`};})
    .filter(r=>cfg.filter==='ALL'||r.sig===cfg.filter)
    .map(r=>r.html).join('');
  $('#rows').innerHTML=rows||'<tr><td colspan="7" class="muted">No symbols match.</td></tr>';
  document.querySelectorAll('#rows tr[data-sym]').forEach(tr=>
    tr.onclick=()=>{selected=tr.dataset.sym;
      document.querySelectorAll('#rows tr').forEach(x=>x.classList.remove('sel'));
      tr.classList.add('sel');drawChart();renderNews();});
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

// Signal tuner wiring
function syncInputs(){$('#rsiLo').value=cfg.rsiLo;$('#rsiHi').value=cfg.rsiHi;
  $('#buyCut').value=cfg.buy;$('#sellCut').value=cfg.sell;$('#sigfilter').value=cfg.filter;}
function bindNum(id,key,lo,hi){$('#'+id).onchange=e=>{
  let n=parseInt(e.target.value,10);if(isNaN(n))n=DEF[key];
  n=Math.max(lo,Math.min(hi,n));cfg[key]=n;e.target.value=n;saveCfg();renderRows();};}
bindNum('rsiLo','rsiLo',1,99);bindNum('rsiHi','rsiHi',1,99);
bindNum('buyCut','buy',1,5);bindNum('sellCut','sell',-5,-1);
$('#sigfilter').onchange=e=>{cfg.filter=e.target.value;saveCfg();renderRows();};
$('#reset').onclick=()=>{cfg=Object.assign({},DEF);saveCfg();syncInputs();renderRows();};
syncInputs();
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
