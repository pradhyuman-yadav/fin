"""Local dashboard to monitor the fin pipeline.

Read-only status page: DB/TimescaleDB health, retention, ingest rate,
per-symbol latest bars with sparklines, and an OHLC candlestick + volume
chart. Auto-refreshes.

Usage:
    python tools/dashboard.py                      # http://localhost:8000
    python tools/dashboard.py --host 0.0.0.0 --port 8000   # for containers

Requires the DB running (docker compose up -d).
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
            "max(updated_at) AS last_write, "
            "count(*) FILTER (WHERE updated_at > now()-interval '1 hour') AS written_1h "
            "FROM market_ohlcv",
            one=True,
        )
        latest = _q(
            "SELECT DISTINCT ON (symbol) symbol, time, open, high, low, close, volume, updated_at "
            "FROM market_ohlcv ORDER BY symbol, time DESC"
        )
        sig = _q(
            "SELECT DISTINCT ON (symbol) symbol, signal, score, rsi14 "
            "FROM market_signals ORDER BY symbol, time DESC"
        )
        sigmap = {r["symbol"]: r for r in sig}
        latency = round((time.monotonic() - t0) * 1000, 1)
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
                "last_write": tot["last_write"].isoformat() if tot and tot["last_write"] else None,
                "written_1h": tot["written_1h"] if tot else 0,
                "db_latency_ms": latency,
                "bars": [
                    {
                        "symbol": r["symbol"],
                        "time": r["time"].isoformat(),
                        "open": r["open"], "high": r["high"], "low": r["low"],
                        "close": r["close"], "volume": r["volume"],
                        "updated_at": r["updated_at"].isoformat(),
                        "signal": (sigmap.get(r["symbol"]) or {}).get("signal"),
                        "score": (sigmap.get(r["symbol"]) or {}).get("score"),
                        "rsi": (sigmap.get(r["symbol"]) or {}).get("rsi14"),
                    }
                    for r in latest
                ],
            }
        )
    except Exception as exc:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(exc)}), 503


@app.get("/api/sparklines")
def sparklines():
    n = min(int(request.args.get("n", 30)), 200)
    rows = _q(
        "SELECT symbol, close FROM ("
        "  SELECT symbol, time, close, row_number() OVER "
        "         (PARTITION BY symbol ORDER BY time DESC) rn FROM market_ohlcv"
        ") s WHERE rn <= %s ORDER BY symbol, time",
        (n,),
    )
    out = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append(r["close"])
    return jsonify(out)


@app.get("/api/series")
def series():
    symbol = request.args.get("symbol", "").upper()
    limit = min(int(request.args.get("limit", 300)), 5000)
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
         align-items:center;gap:14px;position:sticky;top:0;background:var(--bg);z-index:5}
  header h1{font-size:15px;margin:0;font-weight:600;letter-spacing:.02em}
  .pill{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;border-radius:999px;
        font-size:12px;font-weight:600;border:1px solid var(--line)}
  .pill .dot{width:8px;height:8px;border-radius:50%;background:var(--mut)}
  .pill.ok{color:var(--ok)}.pill.ok .dot{background:var(--ok);box-shadow:0 0 8px var(--ok)}
  .pill.bad{color:var(--bad)}.pill.bad .dot{background:var(--bad);box-shadow:0 0 8px var(--bad)}
  .pill.warn{color:var(--warn)}.pill.warn .dot{background:var(--warn)}
  .spacer{flex:1}
  .muted{color:var(--mut)}
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
  th{color:var(--mut);font-weight:500;font-size:11px;text-transform:uppercase;letter-spacing:.04em}
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
  .fresh{color:var(--ok)}.stale{color:var(--warn)}.old{color:var(--bad)}
  .btns{display:flex;gap:6px}
  .btns button{background:var(--card);border:1px solid var(--line);color:var(--mut);
        border-radius:7px;padding:4px 10px;font-size:12px;cursor:pointer}
  .btns button.on{color:var(--fg);border-color:var(--accent)}
  svg text{fill:var(--mut);font-size:10px}
  .err{background:#2d1214;border:1px solid var(--bad);color:#ffb4ae;padding:12px;border-radius:10px}
  .spark{width:80px;height:22px;vertical-align:middle}
  #tt{position:fixed;pointer-events:none;background:#0b0f14;border:1px solid var(--line);
      border-radius:6px;padding:6px 8px;font-size:12px;display:none;z-index:9}
</style></head>
<body>
<header>
  <h1>fin · pipeline monitor</h1>
  <span id="statuspill" class="pill"><span class="dot"></span><span>—</span></span>
  <span id="market" class="pill"><span class="dot"></span><span>market —</span></span>
  <span class="spacer"></span>
  <span class="muted" id="clock"></span>
  <span class="muted">· refresh <span id="cd">15</span>s</span>
</header>
<main>
  <div id="err"></div>
  <div class="cards" id="cards"></div>

  <section class="grid2">
    <div>
      <div class="row"><h2>Symbols</h2><span class="spacer"></span>
        <input id="filter" placeholder="filter…" autocomplete="off"></div>
      <div class="panel" style="padding:4px 8px">
        <table><thead><tr><th>Sym</th><th>Last</th><th>Chg%</th><th>Signal</th>
          <th>RSI</th><th>Trend</th><th>Age</th></tr></thead><tbody id="rows"></tbody></table>
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
    </div>
  </section>
</main>
<div id="tt"></div>
<script>
const $=s=>document.querySelector(s);
let selected=null, sparks={}, limit=300, filter="", lastBars=[];

function age(s){if(s==null)return['—',''];const m=s/60;
  if(m<2)return[Math.round(s)+'s','fresh'];
  if(m<20)return[Math.round(m)+'m','fresh'];
  if(m<60)return[Math.round(m)+'m','stale'];
  const h=m/60; if(h<48)return[h.toFixed(1)+'h','stale'];
  return[(h/24).toFixed(1)+'d','old'];}

function marketState(){ // US equities regular session, approx (UTC 13:30–20:00 Mon–Fri)
  const d=new Date(), day=d.getUTCDay(), mins=d.getUTCHours()*60+d.getUTCMinutes();
  if(day===0||day===6) return['warn','market closed (weekend)'];
  if(mins>=810 && mins<1200) return['ok','market open'];
  return['warn','market closed'];}

function sparkSVG(vals){
  if(!vals||vals.length<2) return '<svg class="spark"></svg>';
  const min=Math.min(...vals),max=Math.max(...vals),r=(max-min)||1;
  const pts=vals.map((v,i)=>`${(i/(vals.length-1)*78+1).toFixed(1)},${(20-(v-min)/r*18+1).toFixed(1)}`).join(' ');
  const up=vals[vals.length-1]>=vals[0];
  return `<svg class="spark" viewBox="0 0 80 22"><polyline fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.5" points="${pts}"/></svg>`;}

async function refresh(){
  let d; try{d=await (await fetch('/api/status')).json();}catch(e){d={ok:false,error:e.message};}
  const err=$('#err');
  if(!d.ok){err.innerHTML='<div class="err">DB unreachable: '+(d.error||'?')+'</div>';
    $('#statuspill').className='pill bad';$('#statuspill').children[1].textContent='offline';return;}
  err.innerHTML='';
  const sp=$('#statuspill'); sp.className='pill '+(d.healthy?'ok':'bad');
  sp.children[1].textContent=d.healthy?'functional':'no data';
  const [mc,mt]=marketState(); const mp=$('#market'); mp.className='pill '+mc;
  mp.children[1].textContent=mt;

  try{sparks=await (await fetch('/api/sparklines?n=30')).json();}catch(e){sparks={};}

  const [aTxt,aCls]=age(d.latest_age_s);
  const fresh=(d.bars||[]).filter(b=>(Date.now()-new Date(b.time))/1000<1200).length;
  const card=(k,v,s)=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div>${s?`<div class="s">${s}</div>`:''}</div>`;
  $('#cards').innerHTML=
    card('Rows',(d.rows||0).toLocaleString(),d.symbols+' symbols')+
    card('Fresh',fresh+'/'+d.symbols,'< 20m old')+
    card('Latest bar',aTxt,'ago')+
    card('Ingested 1h',(d.written_1h||0).toLocaleString(),'writes')+
    card('Retention',d.retention||'—','drop older')+
    card('TimescaleDB',d.timescaledb||'—',(d.db_latency_ms??'?')+' ms')+
    card('DB',(d.postgres||'').replace('PostgreSQL','PG'),'query latency');

  lastBars=(d.bars||[]).slice().sort((a,b)=>a.symbol<b.symbol?-1:1);
  renderRows();
  if(!selected && lastBars.length){selected=lastBars[0].symbol;drawChart();}
  $('#clock').textContent=new Date().toLocaleTimeString();
}

function renderRows(){
  const f=filter.trim().toUpperCase();
  const rows=lastBars.filter(b=>!f||b.symbol.includes(f)).map(b=>{
    const secs=(Date.now()-new Date(b.time))/1000;const[t,c]=age(secs);
    const sv=sparks[b.symbol]||[];
    const chg=(sv.length>=2 && b.open)?((b.close-b.open)/b.open*100):null;
    const chgTxt=chg==null?'—':(chg>=0?'+':'')+chg.toFixed(2)+'%';
    const isC=b.symbol.includes('/');
    const sig=b.signal||'NEUTRAL';
    const rsi=b.rsi!=null?b.rsi.toFixed(0):'—';
    return `<tr data-sym="${b.symbol}" class="${b.symbol===selected?'sel':''}">
      <td><b>${b.symbol}</b><span class="tag ${isC?'c':'s'}">${isC?'CRYPTO':'STOCK'}</span></td>
      <td>${b.close!=null?b.close.toFixed(2):'—'}</td>
      <td class="chg ${chg>=0?'up':'down'}">${chgTxt}</td>
      <td><span class="sig ${sig}">${sig}</span></td>
      <td class="muted">${rsi}</td>
      <td>${sparkSVG(sv)}</td>
      <td class="${c}">${t}</td></tr>`;}).join('');
  $('#rows').innerHTML=rows||'<tr><td colspan="7" class="muted">No matching symbols.</td></tr>';
  document.querySelectorAll('#rows tr[data-sym]').forEach(tr=>
    tr.onclick=()=>{selected=tr.dataset.sym;
      document.querySelectorAll('#rows tr').forEach(x=>x.classList.remove('sel'));
      tr.classList.add('sel');drawChart();});
}

async function drawChart(){
  $('#ctitle').textContent='Chart · '+selected;
  const d=await (await fetch('/api/series?symbol='+encodeURIComponent(selected)+'&limit='+limit)).json();
  const p=d.points||[];const c=$('#chart'),vo=$('#vol');
  const W=560,H=240,pad=44,VH=70;
  if(p.length<1){c.innerHTML='<text x="16" y="120">No data.</text>';vo.innerHTML='';return;}
  if(p.length<2){c.innerHTML=`<text x="16" y="120">Only 1 bar for ${selected}. Candles appear once bars span multiple minutes.</text>`;vo.innerHTML='';return;}
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
  attachHover(c,p,X,Y);
}

function attachHover(svg,p,X,Y){
  const tt=$('#tt');svg.onmousemove=e=>{const r=svg.getBoundingClientRect();
    const rel=(e.clientX-r.left)/r.width*560;let bi=0,bd=1e9;
    p.forEach((b,i)=>{const dx=Math.abs(X(i)-rel);if(dx<bd){bd=dx;bi=i;}});
    const b=p[bi];tt.style.display='block';tt.style.left=(e.clientX+12)+'px';tt.style.top=(e.clientY+12)+'px';
    tt.innerHTML=`<b>${b.t.replace('T',' ').slice(5,16)}</b><br>O ${b.o} H ${b.h}<br>L ${b.l} C ${b.c}<br>V ${(b.v||0).toLocaleString()}`;};
  svg.onmouseleave=()=>$('#tt').style.display='none';}

$('#filter').oninput=e=>{filter=e.target.value;renderRows();};
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
    app.run(host=args.host, port=args.port, debug=False)
