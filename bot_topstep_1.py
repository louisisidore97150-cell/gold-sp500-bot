"""
Gold & SP500 Bot - Topstep Edition
Lancement en une seule commande - auto-installation
"""

import subprocess
import sys
import os

# ─── AUTO-INSTALLATION ────────────────────────────────────────────────────────
PACKAGES = ["flask", "yfinance", "pandas", "numpy"]

print("=" * 50)
print("  Gold & SP500 Bot — Topstep Edition")
print("=" * 50)
print("\n[1/2] Installation des modules necessaires...")

for pkg in PACKAGES:
    try:
        __import__(pkg.lower().replace("metaTrader5", "MetaTrader5"))
    except ImportError:
        print(f"  Installation de {pkg}...")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

print("[1/2] Modules installes.\n")

# ─── IMPORTS APRES INSTALLATION ───────────────────────────────────────────────
import time, threading, json
from datetime import datetime, timedelta

try:
    import MetaTrader5 as mt5
    MT5_OK = True
except: MT5_OK = False

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
    TA_OK = True
except: TA_OK = False

from flask import Flask, jsonify, request

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CFG = {
    "mt5_login": 0, "mt5_password": "", "mt5_server": "Rithmic-TopstepTrader",
    "gold_symbol": "GC", "sp500_symbol": "ES",
    "capital": 50000, "risk_pct": 0.5, "max_loss_pct": 2.0,
    "max_trades": 3, "min_rr": 2.0, "adx_min": 25,
    "simulation": True, "auto_trade": True,
}

STATE = {
    "gold":  {"price":0,"signal":"WAIT","rsi":50,"macd":0,"ema20":0,"ema50":0,"atr":0,"adx":0,"sl":0,"tp":0,"rr":0,"score":0},
    "sp500": {"price":0,"signal":"WAIT","rsi":50,"macd":0,"ema20":0,"ema50":0,"atr":0,"adx":0,"sl":0,"tp":0,"rr":0,"score":0},
    "macro": {"dxy":104.2,"vix":21.4,"rate_10y":4.52},
    "trades_today":0,"daily_pnl":0.0,"mt5_connected":False,
    "last_update":"","log":[],"open_trades":[],"closed_trades":[],
}

# ─── INDICATEURS ──────────────────────────────────────────────────────────────
def rsi(c, p=14):
    if not TA_OK or len(c)<p+1: return 50.0
    s=pd.Series(c); d=s.diff(); g=d.clip(lower=0).rolling(p).mean(); l=(-d.clip(upper=0)).rolling(p).mean()
    return round(float((100-(100/(1+g/l.replace(0,1e-10)))).iloc[-1]),2)

def macd(c, f=12, s=26, sig=9):
    if not TA_OK or len(c)<s+sig: return 0.0,0.0
    p=pd.Series(c); ef=p.ewm(span=f,adjust=False).mean(); es=p.ewm(span=s,adjust=False).mean()
    ml=ef-es; sl=ml.ewm(span=sig,adjust=False).mean()
    return round(float(ml.iloc[-1]),4), round(float(sl.iloc[-1]),4)

def ema(c,p):
    if not TA_OK or len(c)<p: return c[-1] if c else 0
    return round(float(pd.Series(c).ewm(span=p,adjust=False).mean().iloc[-1]),2)

def atr(h,l,c,p=14):
    if not TA_OK or len(c)<p+1: return 1.0
    tr=pd.concat([pd.Series(h)-pd.Series(l),(pd.Series(h)-pd.Series(c).shift()).abs(),(pd.Series(l)-pd.Series(c).shift()).abs()],axis=1).max(axis=1)
    return round(float(tr.rolling(p).mean().iloc[-1]),4)

def adx(h,l,c,p=14):
    if not TA_OK or len(c)<p*2: return 20.0
    H=pd.Series(h); L=pd.Series(l); C=pd.Series(c)
    tr=pd.concat([H-L,(H-C.shift()).abs(),(L-C.shift()).abs()],axis=1).max(axis=1)
    dp=H.diff().clip(lower=0); dn=(-L.diff()).clip(lower=0)
    at=tr.rolling(p).mean(); dip=100*dp.rolling(p).mean()/at.replace(0,1e-10); din=100*dn.rolling(p).mean()/at.replace(0,1e-10)
    dx=100*(dip-din).abs()/(dip+din).replace(0,1e-10)
    return round(float(dx.rolling(p).mean().iloc[-1]),2)

# ─── FETCH ────────────────────────────────────────────────────────────────────
def fetch(ticker):
    if not TA_OK: return None
    try:
        df=yf.download(ticker,start=datetime.now()-timedelta(days=30),end=datetime.now(),interval="1h",progress=False,auto_adjust=True)
        return df if not df.empty else None
    except: return None

def fetch_macro():
    if not TA_OK: return
    for sym,key in [("DX-Y.NYB","dxy"),("^VIX","vix"),("^TNX","rate_10y")]:
        try:
            df=yf.download(sym,period="2d",interval="1h",progress=False,auto_adjust=True)
            if not df.empty: STATE["macro"][key]=round(float(df["Close"].iloc[-1]),2)
        except: pass

# ─── SIGNAL ───────────────────────────────────────────────────────────────────
NEWS_HOURS=[(8,30),(9,0),(14,30),(15,0),(15,30),(16,0),(16,30),(18,0),(19,0)]

def news_blackout():
    now=datetime.utcnow()
    for h,m in NEWS_HOURS:
        ev=now.replace(hour=h,minute=m,second=0,microsecond=0)
        if abs((now-ev).total_seconds())<900: return True
    return False

def analyze(ticker, key):
    df=fetch(ticker)
    if df is None or len(df)<30: return log(f"[WARN] Pas assez de donnees {key}")
    c=df["Close"].values.flatten().tolist()
    h=df["High"].values.flatten().tolist()
    l=df["Low"].values.flatten().tolist()
    price=c[-1]
    r=rsi(c); mv,ms=macd(c); e20=ema(c,20); e50=ema(c,50); e200=ema(c,200)
    at=atr(h,l,c); ad=adx(h,l,c)
    sc=0
    if r<30: sc+=2
    elif r>70: sc-=2
    elif r<45: sc+=1
    elif r>55: sc-=1
    if mv>ms: sc+=2
    else: sc-=2
    if price>e20>e50: sc+=2
    elif price<e20<e50: sc-=2
    if price>e200: sc+=1
    else: sc-=1
    if news_blackout(): sig="WAIT"
    elif ad<CFG["adx_min"]: sig="WAIT"
    elif sc>=4: sig="BUY"
    elif sc<=-4: sig="SELL"
    else: sig="WAIT"
    sld=at*1.5; tpd=at*1.5*CFG["min_rr"]
    sl_p=round(price-sld,2) if sig=="BUY" else round(price+sld,2) if sig=="SELL" else 0
    tp_p=round(price+tpd,2) if sig=="BUY" else round(price-tpd,2) if sig=="SELL" else 0
    rr_v=round(tpd/sld,2) if sld>0 else 0
    lot=round(CFG["capital"]*CFG["risk_pct"]/100/sld,4) if sld>0 else 0
    STATE[key]={"price":round(price,2),"signal":sig,"rsi":r,"macd":round(mv,4),"ema20":e20,"ema50":e50,"ema200":round(e200,2),"atr":round(at,4),"adx":ad,"score":sc,"sl":sl_p,"tp":tp_p,"rr":rr_v,"lot":lot}
    log(f"[{key.upper()}] {price:.2f} | RSI={r} ADX={ad} Score={sc} → {sig}")

# ─── MT5 ──────────────────────────────────────────────────────────────────────
def connect_mt5():
    if not MT5_OK: return
    if not mt5.initialize(): return log("[MT5] Echec init — ouvre MetaTrader 5 d'abord")
    if CFG["mt5_login"]>0:
        if not mt5.login(CFG["mt5_login"],CFG["mt5_password"],CFG["mt5_server"]):
            return log(f"[MT5] Echec login: {mt5.last_error()}")
    STATE["mt5_connected"]=True; log("[MT5] Connecte!")

def execute(asset, sig):
    d=STATE[asset]; sym=CFG["gold_symbol"] if asset=="gold" else CFG["sp500_symbol"]
    if STATE["trades_today"]>=CFG["max_trades"]: return log("[STOP] Limite trades/jour atteinte")
    if abs(STATE["daily_pnl"])>=CFG["capital"]*CFG["max_loss_pct"]/100: return log("[STOP] Limite perte Topstep atteinte!")
    tr={"id":len(STATE["open_trades"])+len(STATE["closed_trades"])+1,"symbol":sym,"asset":asset,"dir":sig,"entry":d["price"],"sl":d["sl"],"tp":d["tp"],"lot":d["lot"],"rr":d["rr"],"time":datetime.now().strftime("%H:%M:%S"),"status":"open","pnl":0}
    if CFG["simulation"] or not MT5_OK:
        log(f"[SIM] {sig} {sym} @ {d['price']} | SL={d['sl']} TP={d['tp']} Lot={d['lot']}")
        STATE["open_trades"].append(tr); STATE["trades_today"]+=1; return
    ot=mt5.ORDER_TYPE_BUY if sig=="BUY" else mt5.ORDER_TYPE_SELL
    px=mt5.symbol_info_tick(sym).ask if sig=="BUY" else mt5.symbol_info_tick(sym).bid
    res=mt5.order_send({"action":mt5.TRADE_ACTION_DEAL,"symbol":sym,"volume":d["lot"],"type":ot,"price":px,"sl":d["sl"],"tp":d["tp"],"deviation":10,"magic":20240101,"comment":"GoldSP500Bot","type_time":mt5.ORDER_TIME_GTC,"type_filling":mt5.ORDER_FILLING_IOC})
    if res.retcode==mt5.TRADE_RETCODE_DONE:
        log(f"[MT5] Ordre execute ticket={res.order}"); STATE["open_trades"].append(tr); STATE["trades_today"]+=1
    else: log(f"[MT5] Erreur: {res.retcode} {res.comment}")

# ─── BOUCLE ───────────────────────────────────────────────────────────────────
def log(msg):
    e=f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"; print(e)
    STATE["log"].insert(0,e)
    if len(STATE["log"])>100: STATE["log"]=STATE["log"][:100]

def loop():
    log("[BOT] Demarrage..."); connect_mt5(); last_day=datetime.now().day
    while True:
        try:
            if datetime.now().day!=last_day:
                STATE["trades_today"]=0; STATE["daily_pnl"]=0.0; last_day=datetime.now().day; log("[BOT] Reset journalier")
            log("[ANALYSE] Mise a jour...")
            analyze("GC=F","gold"); analyze("ES=F","sp500"); fetch_macro()
            STATE["last_update"]=datetime.now().strftime("%H:%M:%S")
            if CFG["auto_trade"]:
                for asset in ["gold","sp500"]:
                    sig=STATE[asset]["signal"]
                    if sig in ["BUY","SELL"] and not any(t["asset"]==asset for t in STATE["open_trades"]):
                        log(f"[AUTO] Signal {sig} sur {asset.upper()} — execution...")
                        execute(asset, sig)
            time.sleep(300)
        except Exception as ex: log(f"[ERR] {ex}"); time.sleep(60)

# ─── FLASK ────────────────────────────────────────────────────────────────────
app=Flask(__name__)

HTML = r"""<!DOCTYPE html><html lang="fr"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"><title>Gold & SP500 Bot</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0f0f0f;color:#e0e0e0}.hdr{background:#1a1a1a;border-bottom:1px solid #2a2a2a;padding:14px 18px;display:flex;justify-content:space-between;align-items:center;position:sticky;top:0;z-index:9}.htitle{font-size:15px;font-weight:600;color:#fff}.hsub{font-size:11px;color:#666;margin-top:2px}.srow{display:flex;align-items:center;gap:10px}.dot{width:8px;height:8px;border-radius:50%;background:#ef4444}.dot.on{background:#22c55e;box-shadow:0 0 6px #22c55e80}.stxt{font-size:12px;color:#888}.sbadge{font-size:11px;padding:3px 9px;border-radius:20px;background:#854F0B40;color:#f59e0b;border:1px solid #854F0B}.main{padding:14px;max-width:860px;margin:0 auto}.g2{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:10px}.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:12px;padding:14px}.csm{background:#141414;border:1px solid #222;border-radius:8px;padding:10px}.lbl{font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}.val{font-size:20px;font-weight:600;color:#fff}.vsm{font-size:14px;font-weight:500}.an{font-size:12px;color:#aaa;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center}.pb{font-size:26px;font-weight:700;color:#fff;margin-bottom:10px}.sbx{display:inline-flex;align-items:center;padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600}.sb{background:#15803d30;color:#22c55e;border:1px solid #15803d}.ss{background:#dc262630;color:#ef4444;border:1px solid #dc2626}.sw{background:#37415130;color:#94a3b8;border:1px solid #374151}.ir{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #222;font-size:12px}.ir:last-child{border-bottom:none}.in{color:#777}.pr{height:4px;border-radius:2px;background:#222;overflow:hidden;margin:8px 0}.pf{height:100%;border-radius:2px;transition:width .5s}.btn{padding:7px 14px;border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;border:1px solid #333;background:#1f1f1f;color:#e0e0e0}.btn:hover{background:#2a2a2a}.bg{background:#15803d30;color:#22c55e;border-color:#15803d}.br{background:#dc262630;color:#ef4444;border-color:#dc2626}.ba{background:#92400e30;color:#f59e0b;border-color:#92400e}.brow{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.log{background:#0a0a0a;border:1px solid #1f1f1f;border-radius:8px;padding:10px;max-height:200px;overflow-y:auto;font-family:monospace;font-size:11px;color:#6b7280}.le{padding:2px 0;border-bottom:1px solid #111}.le:last-child{border-bottom:none}.tr{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;background:#141414;border-radius:8px;margin-bottom:6px;font-size:12px}.tabs{display:flex;gap:2px;border-bottom:1px solid #2a2a2a;margin-bottom:14px}.tab{padding:9px 16px;font-size:12px;cursor:pointer;border-bottom:2px solid transparent;color:#666;background:none;border-left:none;border-right:none;border-top:none}.tab.active{color:#fff;border-bottom-color:#3b82f6}.tp{display:none}.tp.active{display:block}.inp{background:#141414;border:1px solid #2a2a2a;border-radius:8px;padding:7px 10px;color:#e0e0e0;font-size:12px;width:100%}.fld{margin-bottom:8px}.fld label{font-size:11px;color:#666;display:block;margin-bottom:3px}.tgl{position:relative;width:38px;height:20px}.tgl input{opacity:0;width:0;height:0}.slid{position:absolute;cursor:pointer;inset:0;background:#374151;border-radius:10px;transition:.2s}.slid:before{content:'';position:absolute;width:14px;height:14px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.2s}input:checked+.slid{background:#2563eb}input:checked+.slid:before{transform:translateX(18px)}.trow{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid #1f1f1f}.warn{margin-top:12px;padding:10px;background:#1f1a00;border-radius:8px;border:1px solid #854F0B;font-size:11px;color:#f59e0b;line-height:1.6}.grn{color:#22c55e}.red{color:#ef4444}.amb{color:#f59e0b}@media(max-width:600px){.g2,.g4{grid-template-columns:1fr}}</style></head>
<body>
<div class="hdr"><div><div class="htitle">Gold & SP500 — Topstep Bot</div><div class="hsub" id="upd">Initialisation...</div></div><div class="srow"><div id="sbadge" class="sbadge">SIMULATION</div><div class="dot" id="dot"></div><span class="stxt" id="mst">MT5 deconnecte</span></div></div>
<div class="main">
<div class="tabs"><button class="tab active" onclick="st('db')">Dashboard</button><button class="tab" onclick="st('tr')">Trades</button><button class="tab" onclick="st('cf')">Config</button><button class="tab" onclick="st('lg')">Journal</button></div>

<div id="tdb" class="tp active">
<div class="g2">
<div class="card"><div class="an"><span>GOLD — GC (Topstep)</span><span id="gs" class="sbx sw">WAIT</span></div><div class="pb" id="gp">—</div><div class="pr"><div id="gb" class="pf" style="width:50%;background:#374151"></div></div><div style="margin-top:10px"><div class="ir"><span class="in">RSI (14)</span><span id="gr">—</span></div><div class="ir"><span class="in">MACD</span><span id="gm">—</span></div><div class="ir"><span class="in">EMA 20/50</span><span id="ge">—</span></div><div class="ir"><span class="in">ATR</span><span id="ga">—</span></div><div class="ir"><span class="in">ADX</span><span id="gd">—</span></div><div class="ir"><span class="in">Stop Loss</span><span id="gsl" class="red">—</span></div><div class="ir"><span class="in">Take Profit</span><span id="gtp" class="grn">—</span></div><div class="ir"><span class="in">Ratio R/R</span><span id="grr">—</span></div></div><div class="brow"><button class="btn bg" onclick="mt('gold','BUY')">Long manuel</button><button class="btn br" onclick="mt('gold','SELL')">Short manuel</button></div></div>
<div class="card"><div class="an"><span>SP500 — ES (Topstep)</span><span id="ss" class="sbx sw">WAIT</span></div><div class="pb" id="sp">—</div><div class="pr"><div id="sb2" class="pf" style="width:50%;background:#374151"></div></div><div style="margin-top:10px"><div class="ir"><span class="in">RSI (14)</span><span id="sr">—</span></div><div class="ir"><span class="in">MACD</span><span id="sm">—</span></div><div class="ir"><span class="in">EMA 20/50</span><span id="se">—</span></div><div class="ir"><span class="in">ATR</span><span id="sa">—</span></div><div class="ir"><span class="in">ADX</span><span id="sd">—</span></div><div class="ir"><span class="in">Stop Loss</span><span id="ssl" class="red">—</span></div><div class="ir"><span class="in">Take Profit</span><span id="stp" class="grn">—</span></div><div class="ir"><span class="in">Ratio R/R</span><span id="srr">—</span></div></div><div class="brow"><button class="btn bg" onclick="mt('sp500','BUY')">Long manuel</button><button class="btn br" onclick="mt('sp500','SELL')">Short manuel</button></div></div>
</div>
<div class="g4"><div class="csm"><div class="lbl">DXY</div><div class="vsm" id="dxy">—</div></div><div class="csm"><div class="lbl">VIX</div><div class="vsm" id="vix">—</div></div><div class="csm"><div class="lbl">Taux 10Y</div><div class="vsm" id="r10">—</div></div><div class="csm"><div class="lbl">Trades / jour</div><div class="vsm" id="ttd">—</div></div></div>
<div class="csm"><div class="lbl">P&L journalier</div><div class="vsm" id="pnl">—</div></div>
</div>

<div id="ttr" class="tp"><div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Trades ouverts</div><div id="ot">Aucun trade ouvert</div><div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin:14px 0 8px">Trades fermes</div><div id="ct">Aucun trade ferme</div></div>

<div id="tcf" class="tp">
<div class="card" style="margin-bottom:10px"><div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:10px">Connexion MT5 Topstep</div>
<div class="fld"><label>Login MT5</label><input class="inp" id="cl" type="number" placeholder="Numero de compte"></div>
<div class="fld"><label>Mot de passe</label><input class="inp" id="cp" type="password" placeholder="••••••••"></div>
<div class="fld"><label>Serveur</label><input class="inp" id="cs" value="Rithmic-TopstepTrader"></div>
<div class="fld"><label>Capital ($)</label><input class="inp" id="cc" type="number" value="50000"></div>
<div class="fld"><label>Risque par trade (%)</label><input class="inp" id="cr" type="number" value="0.5" step="0.1"></div>
<button class="btn ba" onclick="sc()" style="margin-top:6px">Sauvegarder</button></div>
<div class="card"><div class="trow"><span style="font-size:13px">Mode simulation</span><label class="tgl"><input type="checkbox" id="tsim" checked onchange="tsim()"><span class="slid"></span></label></div><div class="trow"><span style="font-size:13px">Trading automatique</span><label class="tgl"><input type="checkbox" id="taut" checked><span class="slid"></span></label></div><div class="warn"><strong>Topstep :</strong> Garde la simulation activee pendant au moins 1 semaine. La limite de perte journaliere Topstep est surveillee automatiquement.</div></div>
</div>

<div id="tlg" class="tp"><div style="font-size:11px;color:#666;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Logs systeme</div><div class="log" id="lg">...</div></div>
</div>

<script>
function st(n){document.querySelectorAll('.tab').forEach((t,i)=>{const nm=['db','tr','cf','lg'];t.classList.toggle('active',nm[i]===n)});document.querySelectorAll('.tp').forEach(p=>p.classList.remove('active'));document.getElementById('t'+n).classList.add('active')}
function sc2(v){return v==='BUY'?'sb':v==='SELL'?'ss':'sw'}
function sl(v){return v==='BUY'?'LONG':v==='SELL'?'SHORT':'ATTENDRE'}
function rc(v){return v>70?'#ef4444':v<30?'#22c55e':'#f59e0b'}
function upd(d){
  document.getElementById('dot').className='dot'+(d.mt5_connected?' on':'');
  document.getElementById('mst').textContent=d.mt5_connected?'MT5 connecte':'MT5 deconnecte';
  document.getElementById('upd').textContent='Maj : '+(d.last_update||'—');
  const g=d.gold,s=d.sp500,m=d.macro;
  document.getElementById('gp').textContent=g.price?g.price.toFixed(2):'—';
  const gsEl=document.getElementById('gs');gsEl.textContent=sl(g.signal);gsEl.className='sbx '+sc2(g.signal);
  document.getElementById('gr').textContent=g.rsi||'—';document.getElementById('gr').style.color=rc(g.rsi);
  document.getElementById('gm').textContent=g.macd>0?'+ Haussier':'- Baissier';document.getElementById('gm').style.color=g.macd>0?'#22c55e':'#ef4444';
  document.getElementById('ge').textContent=(g.ema20||'—')+' / '+(g.ema50||'—');
  document.getElementById('ga').textContent=g.atr||'—';document.getElementById('gd').textContent=g.adx||'—';
  document.getElementById('gsl').textContent=g.sl||'—';document.getElementById('gtp').textContent=g.tp||'—';
  document.getElementById('grr').textContent=g.rr?g.rr+'x':'—';
  const gp=Math.min(100,Math.max(0,50+(g.score||0)*10));
  document.getElementById('gb').style.width=gp+'%';document.getElementById('gb').style.background=g.signal==='BUY'?'#22c55e':g.signal==='SELL'?'#ef4444':'#374151';
  document.getElementById('sp').textContent=s.price?s.price.toFixed(2):'—';
  const ssEl=document.getElementById('ss');ssEl.textContent=sl(s.signal);ssEl.className='sbx '+sc2(s.signal);
  document.getElementById('sr').textContent=s.rsi||'—';document.getElementById('sr').style.color=rc(s.rsi);
  document.getElementById('sm').textContent=s.macd>0?'+ Haussier':'- Baissier';document.getElementById('sm').style.color=s.macd>0?'#22c55e':'#ef4444';
  document.getElementById('se').textContent=(s.ema20||'—')+' / '+(s.ema50||'—');
  document.getElementById('sa').textContent=s.atr||'—';document.getElementById('sd').textContent=s.adx||'—';
  document.getElementById('ssl').textContent=s.sl||'—';document.getElementById('stp').textContent=s.tp||'—';
  document.getElementById('srr').textContent=s.rr?s.rr+'x':'—';
  document.getElementById('dxy').textContent=m.dxy||'—';document.getElementById('vix').textContent=m.vix||'—';
  document.getElementById('r10').textContent=(m.rate_10y||'—')+'%';
  document.getElementById('ttd').textContent=(d.trades_today||0)+' / 3';
  const pnl=d.daily_pnl||0;const pe=document.getElementById('pnl');
  pe.textContent=(pnl>=0?'+':'')+pnl.toFixed(2)+' $';pe.className='vsm '+(pnl>0?'grn':pnl<0?'red':'');
  const ot=document.getElementById('ot');
  ot.innerHTML=d.open_trades&&d.open_trades.length?d.open_trades.map(t=>`<div class="tr"><div><b>${t.symbol}</b> <span class="sbx ${sc2(t.dir)}" style="font-size:10px;padding:2px 8px">${t.dir}</span></div><div style="font-size:11px;color:#888">${t.entry} | SL:<span class="red">${t.sl}</span> TP:<span class="grn">${t.tp}</span></div><button class="btn ba" style="font-size:10px;padding:3px 8px" onclick="ct2(${t.id})">Fermer</button></div>`).join(''):'<div style="color:#555;font-size:12px;padding:8px 0">Aucun trade ouvert</div>';
  const ctr=document.getElementById('ct');
  ctr.innerHTML=d.closed_trades&&d.closed_trades.length?d.closed_trades.slice(0,10).map(t=>`<div class="tr"><div><b>${t.symbol}</b> ${t.dir}</div><div style="font-size:11px;color:#888">Entree: ${t.entry}</div><div class="${t.pnl>=0?'grn':'red'}" style="font-weight:600">${t.pnl>=0?'+':''}${t.pnl.toFixed(2)}$</div></div>`).join(''):'<div style="color:#555;font-size:12px;padding:8px 0">Aucun trade ferme</div>';
  if(d.log&&d.log.length)document.getElementById('lg').innerHTML=d.log.map(l=>`<div class="le">${l}</div>`).join('');
}
async function fs(){try{const r=await fetch('/api/state');upd(await r.json())}catch(e){}}
async function mt(a,s){if(!confirm(`Ordre ${s} sur ${a.toUpperCase()} ?`))return;await fetch('/api/trade/manual',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({asset:a,signal:s})});fs()}
async function ct2(id){if(!confirm('Fermer ce trade ?'))return;await fetch('/api/trade/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});fs()}
async function tsim(){await fetch('/api/sim/toggle',{method:'POST'});fs()}
async function sc(){await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mt5_login:parseInt(document.getElementById('cl').value)||0,mt5_password:document.getElementById('cp').value,mt5_server:document.getElementById('cs').value,capital:parseFloat(document.getElementById('cc').value)||50000,risk_pct:parseFloat(document.getElementById('cr').value)||0.5})});alert('Sauvegarde!')}
fs();setInterval(fs,30000);
</script></body></html>"""

@app.route("/")
def index(): return HTML

@app.route("/api/state")
def api_state(): return jsonify(STATE)

@app.route("/api/config", methods=["POST"])
def api_config():
    for k,v in request.json.items():
        if k in CFG: CFG[k]=v
    return jsonify({"ok":True})

@app.route("/api/trade/manual", methods=["POST"])
def api_manual():
    d=request.json; execute(d.get("asset","gold"), d.get("signal","BUY"))
    return jsonify({"ok":True})

@app.route("/api/trade/close", methods=["POST"])
def api_close():
    tid=request.json.get("id")
    for t in STATE["open_trades"]:
        if t["id"]==tid:
            t["status"]="closed"; t["pnl"]=round((STATE[t["asset"]]["price"]-t["entry"])*(1 if t["dir"]=="BUY" else -1)*t["lot"]*100,2)
            STATE["daily_pnl"]+=t["pnl"]; STATE["closed_trades"].append(t); STATE["open_trades"].remove(t)
            return jsonify({"ok":True,"pnl":t["pnl"]})
    return jsonify({"ok":False})

@app.route("/api/sim/toggle", methods=["POST"])
def api_sim():
    CFG["simulation"]=not CFG["simulation"]; log(f"[MODE] {'SIMULATION' if CFG['simulation'] else 'REEL'}")
    return jsonify({"simulation":CFG["simulation"]})

# ─── LANCEMENT ────────────────────────────────────────────────────────────────
if __name__=="__main__":
    print("\n[2/2] Lancement du bot...")
    threading.Thread(target=loop, daemon=True).start()

    # Trouve l'IP locale pour l'acces mobile
    import socket
    try:
        ip=socket.gethostbyname(socket.gethostname())
    except: ip="TON_IP_LOCALE"

    print("\n" + "="*50)
    print("  BOT DEMARRE !")
    print("="*50)
    print(f"  PC     : http://localhost:5000")
    print(f"  Mobile : http://{ip}:5000")
    print("="*50)
    print("  Appuie sur CTRL+C pour arreter\n")

    import webbrowser
    webbrowser.open("http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)
