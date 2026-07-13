"""Simple local dashboard to monitor the fin pipeline.

Serves a single page showing DB health, TimescaleDB status, retention policy,
row/symbol counts, per-symbol latest bars, and a price chart. Read-only.

Usage:
    python tools/dashboard.py            # http://localhost:8000
    python tools/dashboard.py --port 8080

Requires the DB running (docker compose up -d).
"""

import argparse

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


@app.get("/api/status")
def status():
    try:
        pg = _q("SELECT version() AS v", one=True)["v"]
        ext = _q(
            "SELECT extversion AS v FROM pg_extension WHERE extname='timescaledb'",
            one=True,
        )
        ts = ext["v"] if ext else None
        ret = _q(
            "SELECT config->>'drop_after' AS drop_after FROM timescaledb_information.jobs "
            "WHERE proc_name='policy_retention' AND hypertable_name='market_ohlcv'",
            one=True,
        )
        totals = _q(
            "SELECT count(*) AS rows, count(DISTINCT symbol) AS symbols, "
            "max(time) AS latest, EXTRACT(EPOCH FROM now()-max(time)) AS latest_age_s "
            "FROM market_ohlcv",
            one=True,
        )
        latest = _q(
            "SELECT DISTINCT ON (symbol) symbol, time, open, high, low, close, "
            "volume, updated_at FROM market_ohlcv ORDER BY symbol, time DESC"
        )
        healthy = bool(totals and totals["rows"] and totals["rows"] > 0)
        return jsonify(
            {
                "ok": True,
                "healthy": healthy,
                "postgres": pg,
                "timescaledb": ts,
                "retention": ret["drop_after"] if ret else None,
                "rows": totals["rows"] if totals else 0,
                "symbols": totals["symbols"] if totals else 0,
                "latest": totals["latest"].isoformat() if totals and totals["latest"] else None,
                "latest_age_s": float(totals["latest_age_s"]) if totals and totals["latest_age_s"] is not None else None,
                "bars": [
                    {
                        "symbol": r["symbol"],
                        "time": r["time"].isoformat(),
                        "close": r["close"],
                        "volume": r["volume"],
                        "updated_at": r["updated_at"].isoformat(),
                    }
                    for r in latest
                ],
            }
        )
    except Exception as exc:  # noqa: BLE001 - surface DB errors to the page
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/series")
def series():
    symbol = request.args.get("symbol", "").upper()
    limit = min(int(request.args.get("limit", 200)), 2000)
    rows = _q(
        "SELECT time, close FROM market_ohlcv WHERE symbol=%s ORDER BY time DESC LIMIT %s",
        (symbol, limit),
    )
    rows.reverse()
    return jsonify(
        {"symbol": symbol, "points": [{"t": r["time"].isoformat(), "c": r["close"]} for r in rows]}
    )


@app.get("/")
def index():
    return PAGE


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>fin · pipeline monitor</title>
<style>
  :root{--bg:#0d1117;--card:#161b22;--line:#21262d;--fg:#e6edf3;--mut:#8b949e;
        --ok:#3fb950;--warn:#d29922;--bad:#f85149;--accent:#58a6ff}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}
  header{padding:18px 24px;border-bottom:1px solid var(--line);display:flex;
         align-items:center;gap:12px}
  header h1{font-size:16px;margin:0;font-weight:600}
  .dot{width:10px;height:10px;border-radius:50%;background:var(--mut)}
  .dot.ok{background:var(--ok);box-shadow:0 0 8px var(--ok)}
  .dot.bad{background:var(--bad);box-shadow:0 0 8px var(--bad)}
  .muted{color:var(--mut)}
  main{padding:24px;max-width:1100px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
  .card .k{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}
  .card .v{font-size:20px;font-weight:600;margin-top:6px;word-break:break-word}
  section{margin-top:24px}
  section h2{font-size:13px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em;margin:0 0 10px}
  table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
  th,td{text-align:right;padding:7px 10px;border-bottom:1px solid var(--line)}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--mut);font-weight:500;font-size:12px}
  tr{cursor:pointer}
  tr:hover td{background:#1c2230}
  tr.sel td{background:#1f2b3d}
  .fresh{color:var(--ok)}.stale{color:var(--warn)}.old{color:var(--bad)}
  #chartwrap{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
  svg text{fill:var(--mut);font-size:11px}
  .err{background:#2d1214;border:1px solid var(--bad);color:#ffb4ae;padding:12px;border-radius:8px}
</style></head>
<body>
<header><span id="dot" class="dot"></span><h1>fin · pipeline monitor</h1>
  <span class="muted" id="clock"></span></header>
<main>
  <div id="err"></div>
  <div class="cards" id="cards"></div>
  <section><h2>Latest bar per symbol <span class="muted">(click a row to chart)</span></h2>
    <table><thead><tr><th>Symbol</th><th>Close</th><th>Volume</th><th>Bar time (UTC)</th><th>Age</th></tr></thead>
    <tbody id="rows"></tbody></table></section>
  <section><h2 id="charttitle">Price</h2><div id="chartwrap"><svg id="chart" viewBox="0 0 900 260" width="100%"></svg></div></section>
</main>
<script>
const $=s=>document.querySelector(s);
let selected=null;

function age(s){if(s==null)return['—',''];const m=s/60;
  if(m<2)return[Math.round(s)+'s','fresh'];
  if(m<20)return[Math.round(m)+'m','fresh'];
  if(m<60)return[Math.round(m)+'m','stale'];
  const h=m/60; if(h<48)return[h.toFixed(1)+'h','stale'];
  return[(h/24).toFixed(1)+'d','old'];}

async function refresh(){
  let d; try{d=await (await fetch('/api/status')).json();}catch(e){d={ok:false,error:e.message};}
  const err=$('#err');
  if(!d.ok){err.innerHTML='<div class="err">DB unreachable: '+(d.error||'?')+'</div>';
    $('#dot').className='dot bad';return;}
  err.innerHTML='';
  $('#dot').className='dot '+(d.healthy?'ok':'bad');
  const [aTxt,aCls]=age(d.latest_age_s);
  const card=(k,v)=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`;
  $('#cards').innerHTML=
    card('Status', d.healthy?'<span class="fresh">● functional</span>':'<span class="old">● no data</span>')+
    card('TimescaleDB', d.timescaledb||'—')+
    card('Rows', (d.rows||0).toLocaleString())+
    card('Symbols', d.symbols||0)+
    card('Retention', d.retention||'—')+
    card('Latest bar age', `<span class="${aCls}">${aTxt}</span>`);
  const rows=(d.bars||[]).sort((a,b)=>a.symbol<b.symbol?-1:1).map(b=>{
    const secs=(Date.now()-new Date(b.time))/1000;const[t,c]=age(secs);
    return `<tr data-sym="${b.symbol}" class="${b.symbol===selected?'sel':''}">
      <td>${b.symbol}</td><td>${b.close!=null?b.close.toFixed(2):'—'}</td>
      <td>${b.volume!=null?b.volume.toLocaleString():'—'}</td>
      <td class="muted">${b.time.replace('T',' ').slice(0,19)}</td>
      <td class="${c}">${t}</td></tr>`;}).join('');
  $('#rows').innerHTML=rows||'<tr><td colspan="5" class="muted">No bars yet.</td></tr>';
  document.querySelectorAll('#rows tr[data-sym]').forEach(tr=>
    tr.onclick=()=>{selected=tr.dataset.sym;drawChart(selected);
      document.querySelectorAll('#rows tr').forEach(x=>x.classList.remove('sel'));tr.classList.add('sel');});
  if(!selected && d.bars && d.bars.length){selected=d.bars[0].symbol;drawChart(selected);}
  $('#clock').textContent='updated '+new Date().toLocaleTimeString();
}

async function drawChart(sym){
  $('#charttitle').textContent='Price · '+sym;
  const d=await (await fetch('/api/series?symbol='+encodeURIComponent(sym))).json();
  const pts=d.points||[];const svg=$('#chart');const W=900,H=260,pad=40;
  if(pts.length<2){svg.innerHTML='<text x="20" y="130">Not enough data to chart.</text>';return;}
  const xs=pts.map((p,i)=>i),cs=pts.map(p=>p.c);
  const min=Math.min(...cs),max=Math.max(...cs),rng=(max-min)||1;
  const X=i=>pad+i/(pts.length-1)*(W-pad*1.5);
  const Y=c=>H-pad-(c-min)/rng*(H-pad*1.7);
  let path='M'+xs.map(i=>X(i).toFixed(1)+','+Y(cs[i]).toFixed(1)).join(' L');
  const grid=[0,.5,1].map(f=>{const y=H-pad-f*(H-pad*1.7);const v=(min+f*rng);
    return `<line x1="${pad}" y1="${y}" x2="${W-pad*.5}" y2="${y}" stroke="#21262d"/>
            <text x="4" y="${y+3}">${v.toFixed(2)}</text>`;}).join('');
  svg.innerHTML=grid+`<path d="${path}" fill="none" stroke="#58a6ff" stroke-width="2"/>
    <text x="${pad}" y="${H-8}">${pts[0].t.replace('T',' ').slice(0,16)}</text>
    <text x="${W-pad*3}" y="${H-8}">${pts[pts.length-1].t.replace('T',' ').slice(0,16)}</text>`;
}
refresh();setInterval(refresh,15000);
</script></body></html>"""


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="fin pipeline dashboard")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args()
    app.run(host=args.host, port=args.port, debug=False)
