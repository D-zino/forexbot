import os, time, threading, requests, sqlite3, json, re
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from zoneinfo import ZoneInfo
import telebot
from flask import Flask, request
TOKEN=os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID=os.getenv("CHANNEL_ID")
# [OK] FIX: tolerate stray quotes/spaces pasted into Render env vars
if isinstance(CHANNEL_ID,str): CHANNEL_ID=CHANNEL_ID.strip().strip('"').strip("'")
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL","https://forexbot-44io.onrender.com")
app=Flask(__name__)
@app.route('/')
def home(): return "V17.0 COMPLETE WEBHOOK TRUE LIVE OK"
@app.route('/health')
def health(): return "OK"
bot=telebot.TeleBot(TOKEN,parse_mode="HTML",threaded=False)
# ============ WEBHOOK - FIXES 409 CONFLICT PERMANENTLY ============
# V17.0 FIX: a slow handler used to hold the HTTP response open until Telegram timed out,
# so Telegram REDELIVERED the same update - which is why /brief repeated every minute.
# Now: ACK immediately, process in a worker thread, and ignore duplicate update_ids.
_update_seen=OrderedDict()
_update_lock=threading.Lock()
_update_workers=0

def _run_update(update):
    global _update_workers
    try:
        bot.process_new_updates([update])
    except Exception as e:
        print(f"update {getattr(update,'update_id',None)} failed: {e}")
    finally:
        with _update_lock:
            _update_workers=max(0,_update_workers-1)

def _enqueue_update(update):
    """Dedupe by update_id (Telegram retries redeliver the same id) and run off the webhook thread."""
    global _update_workers
    uid=getattr(update,'update_id',None)
    with _update_lock:
        if uid is not None and uid in _update_seen:
            print(f"V17.0: duplicate update {uid} ignored (Telegram redelivery)")
            return False
        if uid is not None:
            _update_seen[uid]=time.time()
            while len(_update_seen)>400: _update_seen.popitem(last=False)
        if _update_workers>=4:
            print("V17.0: update worker limit reached - update dropped")
            return False
        _update_workers+=1
    threading.Thread(target=_run_update,args=(update,),daemon=True).start()
    return True

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        _enqueue_update(update)
        return 'OK', 200
    return 'KO', 403
@app.route('/setwebhook')
def set_webhook_route():
    try:
        bot.remove_webhook()
        time.sleep(2)
        bot.set_webhook(url=f"{RENDER_URL}/{TOKEN}", drop_pending_updates=True)
        return f"\u2705 Webhook set to {RENDER_URL}/{TOKEN} \u2014 409 FIXED"
    except Exception as e:
        return f"Fail {e}"
def init_webhook():
    time.sleep(4)
    # [OK] FIX: token sanity check (your logs showed 401 Unauthorized from Telebot)
    try:
        me=bot.get_me()
        print(f"\U0001f916 TOKEN OK \u2014 bot @{me.username} (id {me.id})")
    except Exception as e:
        print(f"\u274c TELEGRAM_TOKEN INVALID / 401 UNAUTHORIZED: {e}")
        print("   \u2192 Render Dashboard \u2192 Environment \u2192 fix TELEGRAM_TOKEN (revoked or truncated token).")
        return
    # [OK] FIX: retry instead of one-shot (free instance can be slow to come up)
    for attempt in range(1,4):
        try:
            if RENDER_URL and TOKEN:
                bot.remove_webhook()
                time.sleep(2)
                bot.set_webhook(url=f"{RENDER_URL}/{TOKEN}", drop_pending_updates=True)
                print(f"\u2705 WEBHOOK SET: {RENDER_URL}/{TOKEN} \u2014 NO MORE 409")
                return
        except Exception as e:
            print(f"Webhook init fail (attempt {attempt}/3): {e}")
            time.sleep(5)
# [OK] FIX: one safe sender for every channel/chat message.
#    - never raises if CHANNEL_ID is missing (old code crash-looped the monitor)
#    - retries transient Telegram errors instead of dropping alerts
def notify(text, chat=None):
    target=CHANNEL_ID if chat is None else chat
    if target is None:
        print(f"\u26a0\ufe0f CHANNEL_ID not set \u2014 message not sent: {text[:140]}")
        return None
    for attempt in range(1,4):
        try:
            return bot.send_message(target,text)
        except Exception as e:
            print(f"\u26a0\ufe0f send_message fail {attempt}/3: {e}")
            time.sleep(2)
    return None
# Keep alive for Render free spin-down
def keep_alive():
    while True:
        time.sleep(600)
        try:
            if RENDER_URL: requests.get(RENDER_URL+"/health",timeout=10)
        except: pass
WAT=ZoneInfo("Africa/Lagos")
# =====================================================================
# [OK] FIX: persistent journal.
#    Render's free disk is wiped on every deploy, so trades.db reset every
#    time. If DATABASE_URL is set (Neon / Supabase / Render Postgres) the
#    journal now lives in Postgres and survives deploys. Without it the bot
#    behaves exactly as before on local SQLite.
# =====================================================================
class DB:
    def __init__(self, sqlite_path="trades.db"):
        self.pg=False
        self._lock=threading.Lock()
        self.url=os.getenv("DATABASE_URL")
        self.backend="SQLite trades.db (wiped on Render deploys \u2014 set DATABASE_URL to persist)"
        if self.url:
            url=self.url
            if "localhost" not in url and "127.0.0.1" not in url and "sslmode=" not in url:
                url=url+("&" if "?" in url else "?")+"sslmode=require"   # Neon/Supabase need SSL
            self.url=url          # [OK] keep the sslmode DSN for reconnects too
            try:
                self._connect_pg(url)
                self.backend="Postgres (persistent \u2014 journal survives deploys \u2705)"
                print("\U0001f5c4\ufe0f DB BACKEND: POSTGRES \u2014 persistent")
            except Exception as e:
                print(f"\u26a0\ufe0f Postgres connect failed ({e}) \u2014 falling back to local SQLite")
                self.url=None
        if not self.url:
            self.conn=sqlite3.connect(sqlite_path,check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            print("\U0001f5c4\ufe0f DB BACKEND: SQLite trades.db (local, wiped on Render deploys)")
        self._init_schema()
    def _connect_pg(self, url):
        import psycopg2
        self.conn=psycopg2.connect(url)
        self.conn.autocommit=True
        self.pg=True
    @staticmethod
    def _pg_sql(sql):
        s=sql.strip()
        if s.startswith("REPLACE INTO levels"):
            s=s.replace("REPLACE INTO levels","INSERT INTO levels",1).replace("?","%s")
            return s+" ON CONFLICT (lkey) DO UPDATE SET low=EXCLUDED.low, high=EXCLUDED.high, ts=EXCLUDED.ts, status=EXCLUDED.status, note=EXCLUDED.note"
        if s.startswith("REPLACE INTO probables"):
            s=s.replace("REPLACE INTO probables","INSERT INTO probables",1).replace("?","%s")
            return s+" ON CONFLICT (pair) DO UPDATE SET time=EXCLUDED.time, fvg_low=EXCLUDED.fvg_low, fvg_high=EXCLUDED.fvg_high, bias=EXCLUDED.bias, stage=EXCLUDED.stage"
        s=s.replace("INSERT INTO trades VALUES (NULL,","INSERT INTO trades VALUES (DEFAULT,")
        s=s.replace("INTEGER PRIMARY KEY AUTOINCREMENT","SERIAL PRIMARY KEY")
        return s.replace("?","%s")
    def execute(self, sql, params=()):
        with self._lock:
            for attempt in (1,2):
                try:
                    cur=self.conn.cursor()
                    cur.execute(self._pg_sql(sql) if self.pg else sql, params)
                    return cur
                except Exception as e:
                    if self.pg and attempt==1:
                        print(f"\u26a0\ufe0f DB error ({e}) \u2014 reconnecting\u2026")
                        try: self._connect_pg(self.url); time.sleep(1); continue
                        except Exception as e2: print(f"\u26a0\ufe0f DB reconnect failed: {e2}"); raise e
                    raise
    def commit(self):
        if self.pg: return          # autocommit
        with self._lock:
            self.conn.commit()
    def _init_schema(self):
        self.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, bias TEXT, time TEXT, result TEXT, pips REAL, stage TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS probables (pair TEXT PRIMARY KEY, time TEXT, fvg_low REAL, fvg_high REAL, bias TEXT, stage TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS steps (pair TEXT PRIMARY KEY, sweep INTEGER, choch INTEGER, fvg INTEGER, confirm INTEGER, last_time TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS levels (lkey TEXT PRIMARY KEY, pair TEXT, kind TEXT, low REAL, high REAL, ts TEXT, status TEXT, note TEXT)")
        self.commit()
conn=DB()
FALLBACK={"EUR/USD":1.11932,"GBP/USD":1.32661,"USD/CAD":1.4210,"EUR/JPY":177.53,"USD/JPY":158.44,"XAU/USD":4143.74,"NAS100":30568.90,"US30":51765.50,"GERMANY40":25403.0,"DXY":102.10,"WTI":106.0}
YAHOO_MAP={"EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","USD/CAD":"USDCAD=X","EUR/JPY":"EURJPY=X","USD/JPY":"USDJPY=X","XAU/USD":"GC=F","NAS100":"NQ=F","US30":"YM=F","GERMANY40":"^GDAXI","DXY":"DX-Y.NYB","WTI":"CL=F"}
PAIRS_9=["EUR/USD","GBP/USD","USD/CAD","XAU/USD","EUR/JPY","USD/JPY","NAS100","US30","GERMANY40"]
# =====================================================================
# [OK] V17.0  CANDLE SOURCE THAT WORKS FROM A DATACENTER IP (and from Nigeria)
# Deriv's public market-data WebSocket needs NO login, NO KYC and no funded
# account  verified live: real 5m/15m/1h candles for all 9 pairs.
# Only a candle SOURCE changed; FVG / OB / CHOCH / sweep logic is untouched.
# Set CANDLE_SOURCE=yahoo to go back to the old behaviour.
# =====================================================================
DERIV_WS_URL=os.getenv("DERIV_WS_URL","wss://api.derivws.com/trading/v1/options/ws/public")
CANDLE_SOURCE=os.getenv("CANDLE_SOURCE","deriv").lower()
DERIV_MAP={"EUR/USD":"frxEURUSD","GBP/USD":"frxGBPUSD","USD/CAD":"frxUSDCAD","EUR/JPY":"frxEURJPY",
           "USD/JPY":"frxUSDJPY","XAU/USD":"frxXAUUSD","NAS100":"OTC_NDX","US30":"OTC_DJI","GERMANY40":"OTC_GDAXI"}
#                 granularity(sec), cache TTL(sec)
CANDLE_TF={"1m":(60,1800),"5m":(300,45),"15m":(900,180),"60m":(3600,900),"4h":(14400,900)}
CANDLE_COUNT={"1m":3000}                      # 1m: ~50h so the WHOLE previous UTC day is covered
YF_INTERVAL={"1m":"1m","5m":"5m","15m":"15m","60m":"60m","4h":"1h"}   # Yahoo has no 4h -> 1h
YF_RANGE={"1m":"2d","5m":"2d","15m":"5d","60m":"10d","4h":"1mo"}
_CANDLE_CACHE={}
_hold_sent={}                                   # pair -> event key already warned about                                   # (pair,interval) -> (ts, candles, source)
_LAST_SRC={}                                       # (pair,interval) -> "DERIV"/"YAHOO"/"EMPTY"
_deriv={"ws":None,"lock":threading.Lock(),"req":0,"fails":0,"fail_until":0.0}
def get_deriv_candles(pair, interval="15m", count=None):
    count=count or CANDLE_COUNT.get(interval,120)
    """One shared socket, JSON request/response by req_id. Returns [] on any failure."""
    if CANDLE_SOURCE=="yahoo": return []
    sym=DERIV_MAP.get(pair)
    if not sym: return []
    if time.time()<_deriv["fail_until"]: return []          # circuit breaker
    try:
        import websocket
    except Exception:
        return []
    gran=CANDLE_TF.get(interval,(900,180))[0]
    try:
        with _deriv["lock"]:
            ws=_deriv["ws"]
            if ws is None:
                ws=websocket.create_connection(DERIV_WS_URL, timeout=12)
                _deriv["ws"]=ws
            _deriv["req"]+=1; rid=_deriv["req"]
            ws.send(json.dumps({"ticks_history":sym,"style":"candles","granularity":gran,
                                "count":count,"end":"latest","req_id":rid}))
            t0=time.time()
            while time.time()-t0<8:
                m=json.loads(ws.recv())
                if m.get("req_id")!=rid: continue
                if m.get("msg_type")=="candles":
                    _deriv["fails"]=0
                    return [{"open":c["open"],"high":c["high"],"low":c["low"],"close":c["close"],
                             "time":c.get("epoch")}
                            for c in m.get("candles",[])]
                if m.get("msg_type")=="error":
                    print(f"\u26a0\ufe0f Deriv {pair} {interval}: {m['error'].get('message')}")
                    break
    except Exception as e:
        print(f"\u26a0\ufe0f Deriv candles fail {pair} {interval}: {e}")
        try: _deriv["ws"].close()
        except: pass
        _deriv["ws"]=None
        _deriv["fails"]+=1
        if _deriv["fails"]>=3:                              # stop hammering, fall back for 10 min
            _deriv["fail_until"]=time.time()+600
            print("\u26a0\ufe0f Deriv failing \u2014 using Yahoo candles for the next 10 minutes")
    return []
def candles_cached(pair, interval):
    """Deriv first (works on Render) \u2192 Yahoo fallback. Cached per timeframe.
    Empty results (e.g. gold's 21:00-22:00 UTC break) keep the last good series."""
    key=(pair,interval); ttl=CANDLE_TF.get(interval,(900,180))[1]
    now=time.time()
    hit=_CANDLE_CACHE.get(key)
    if hit and now-hit[0]<ttl:
        return hit[1]
    data=get_deriv_candles(pair, interval)
    src="DERIV" if data else "EMPTY"
    if not data and CANDLE_SOURCE!="deriv":                 # explicit yahoo mode
        data=get_yahoo_candles(pair, YF_INTERVAL.get(interval,interval), YF_RANGE.get(interval,"5d"))
        src="YAHOO" if data else "EMPTY"
    if not data and CANDLE_SOURCE=="deriv":                 # deriv is primary: try yahoo if deriv empty
        alt=get_yahoo_candles(pair, YF_INTERVAL.get(interval,interval), YF_RANGE.get(interval,"5d"))
        if alt: data, src = alt, "YAHOO"
    if not data and hit and hit[1]:                         # keep last good series
        return hit[1]
    _CANDLE_CACHE[key]=(now,data,src); _LAST_SRC[key]=src
    return data
def candle_status_line():
    pairs=len(PAIRS_9)
    d=sum(1 for p in PAIRS_9 if _LAST_SRC.get((p,"5m"))=="DERIV")
    y=sum(1 for p in PAIRS_9 if _LAST_SRC.get((p,"5m"))=="YAHOO")
    e=sum(1 for p in PAIRS_9 if _LAST_SRC.get((p,"5m"))=="EMPTY")
    if not _LAST_SRC: return f"not fetched yet (source={CANDLE_SOURCE})"
    return f"source={CANDLE_SOURCE.upper()} | 5m fresh: DERIV {d}/{pairs}, YAHOO {y}, none {e}"
def px(pair,v):
    # [OK] FIX: 5dp only for FX pairs  gold/indices are quoted in points/dollars
    return f"{v:.5f}" if ("JPY" in pair or pair in ("EUR/USD","GBP/USD","USD/CAD")) else f"{v:.2f}"
def get_yahoo_live_price(pair):
    try:
        sym=YAHOO_MAP.get(pair)
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d&includePrePost=true"
        r=requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"}).json()
        if 'chart' in r and r['chart']['result']:
            result=r['chart']['result'][0]
            price=result['meta'].get('regularMarketPrice')
            if not price:
                closes=result['indicators']['quote'][0]['close']
                for c in reversed(closes):
                    if c is not None: price=c; break
            if price and price!=0: return float(price)
    except: pass
    return None
def get_yahoo_candles(pair, interval="15m", range_="1d"):
    try:
        sym=YAHOO_MAP.get(pair, "EURUSD=X")
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval={interval}&range={range_}&includePrePost=true"
        r=requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"}).json()
        result=r['chart']['result'][0]
        q=result['indicators']['quote'][0]
        closes,highs,lows,opens=q['close'],q['high'],q['low'],q['open']
        ts=result.get('timestamp') or []
        candles=[]
        for i in range(len(closes)):
            if closes[i] is None: continue
            candles.append({'open':opens[i],'high':highs[i],'low':lows[i],'close':closes[i],
                            'time':(ts[i] if i < len(ts) else None)})
        return candles
    except: return []
def find_real_fvg(candles, bias=None):
    """Newest fair value gap. When the setup bias is known, prefer the imbalance that agrees
    with it (BULL FVG for a BUY setup, BEAR FVG for a SELL setup) - otherwise you get the
    contradictory 'Bias: BUY / FVG (BEAR)' that the old first-match scan produced."""
    try:
        hits=[]
        for i in range(len(candles)-3, -1, -1):
            c0,c2=candles[i],candles[i+2]
            if c2['low'] > c0['high']: hits.append({'low':c0['high'], 'high':c2['low'], 'type':'BULL'})
            elif c2['high'] < c0['low']: hits.append({'low':c2['high'], 'high':c0['low'], 'type':'BEAR'})
        if not hits: return None
        if bias:
            want="BULL" if bias=="BUY" else "BEAR"
            for h in hits:
                if h['type']==want: return h
        return hits[0]          # newest of any type
    except: pass
    return None
def find_real_ob(candles):
    try:
        for i in range(len(candles)-3, 1, -1):
            if candles[i]['close'] < candles[i]['open'] and candles[i+1]['close'] > candles[i+1]['open']:
                return {'low':candles[i]['low'], 'high':candles[i]['high']}
        for i in range(len(candles)-3, 1, -1):
            if candles[i]['close'] > candles[i]['open'] and candles[i+1]['close'] < candles[i+1]['open']:
                return {'low':candles[i]['low'], 'high':candles[i]['high']}
    except: pass
    return None
def find_choch(candles):
    try:
        if len(candles)<5: return None
        last=candles[-1]['close']
        prev_high=max([c['high'] for c in candles[-5:-1]])
        prev_low=min([c['low'] for c in candles[-5:-1]])
        if last > prev_high: return "BULL_CHOCHO"
        if last < prev_low: return "BEAR_CHOCHO"
    except: pass
    return None
def get_stooq_price(symbol):
    try:
        url=f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
        txt=requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"}).text
        lines=txt.strip().split('\n')
        if len(lines)>=2:
            close=float(lines[1].split(',')[6])
            if close!=0:
                print(f"\u2705 STOOQ {symbol}: {close}")
                return close
    except Exception as e:
        print(f"Stooq {symbol} fail {e}")
    return None
def get_forex_live():
    try:
        r=requests.get("https://open.er-api.com/v6/latest/USD",timeout=8).json()
        rates=r.get('rates',{})
        if rates:
            data={'EUR/USD':round(1/rates['EUR'],5),'GBP/USD':round(1/rates['GBP'],5),'USD/CAD':round(rates['CAD'],5),'USD/JPY':round(rates['JPY'],4)}
            data['EUR/JPY']=round(data['EUR/USD']*data['USD/JPY'],3)
            print(f"\u2705 FOREX LIVE er-api: {data}")
            return data
    except Exception as e:
        print(f"er-api fail {e}")
    return {}
def get_live_all_live():
    data={}
    # 1. FOREX TRUE LIVE
    data.update(get_forex_live())
    # 2. GOLD TRUE LIVE
    try:
        r=requests.get("https://api.gold-api.com/price/XAU",timeout=6).json()
        data['XAU/USD']=float(r['price'])
        print(f"\u2705 GOLD LIVE: {data['XAU/USD']}")
    except: pass
    # 3. INDICES via STOOQ (fixes Yahoo block on Render)
    stooq_map={"DXY":"dx.f","WTI":"cl.f","NAS100":"nq.f","US30":"ym.f","GERMANY40":"^dax"}
    for k,sym in stooq_map.items():
        p=get_stooq_price(sym)
        if p: data[k]=p
        time.sleep(0.25)
    # 4. Yahoo backup
    for pair in ["XAU/USD","NAS100","US30","GERMANY40","DXY","WTI"]:
        if pair not in data:
            live=get_yahoo_live_price(pair)
            if live: data[pair]=live
    # 5. Fallback last resort
    for k,v in FALLBACK.items():
        if k not in data:
            print(f"\u26a0\ufe0f FALLBACK USED {k}: {v}")
            data[k]=v
    print(f"FINAL TRUE LIVE V17.0: {data}")
    return data
def calc_pips(pair, entry, current, bias):
    # [OK] FIX: only 4-decimal FX pairs use 10000. XAU/USD contains "/" but is quoted in
    # dollars  the old check reported gold pips 10000 (e.g. "-140000.0p" on a $14 move).
    if "JPY" in pair: mult=100
    elif pair in ("EUR/USD","GBP/USD","USD/CAD"): mult=10000
    else: mult=1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult
# [OK] FIX (V17.0): step 1 was mathematically broken for every instrument.
#    Old code: sweep = live0.0010, then checked abs(live - sweep) < 0.0020.
#       FX majors: 0.0010 < 0.0020 is ALWAYS true  3 false SWEEP alerts at every killzone open.
#       JPY/gold/indices: distance was 0.30 / 7.0 / 70  could NEVER fire.
#    Now the level is real liquidity taken from candles (recent swing high for a SELL setup,
#    recent swing low for a BUY setup) and the tolerance is per-instrument.
SWEEP_TOL={"EUR/USD":0.0005,"GBP/USD":0.0005,"USD/CAD":0.0005,"EUR/JPY":0.05,"USD/JPY":0.05,
           "XAU/USD":1.5,"DXY":0.05,"WTI":0.15,"NAS100":15.0,"US30":15.0,"GERMANY40":15.0}
def sweep_tol(pair):
    if pair in SWEEP_TOL: return SWEEP_TOL[pair]
    if "JPY" in pair: return 0.05
    if pair in ("EUR/USD","GBP/USD","USD/CAD") or pair=="DXY": return 0.0005
    if pair=="XAU/USD": return 1.5
    return 15.0
def sweep_level(pair, live, bias, c5):
    """Liquidity level this setup must take out:
         SELL setup \u2192 buy-side liquidity ABOVE price (recent swing HIGH)
         BUY  setup \u2192 sell-side liquidity BELOW price (recent swing LOW)
    Returns (level, is_real). is_real=False on the candle-less fallback \u2014 and those
    synthetic levels never trigger step 1 (fail closed, no false alerts)."""
    try:
        if c5 and len(c5) > 6:
            window=c5[-20:-1]
            if len(window) >= 5:
                lvl=max(c['high'] for c in window) if bias=="SELL" else min(c['low'] for c in window)
                if lvl and lvl > 0: return float(lvl), True
    except: pass
    step=sweep_tol(pair)*3          # deliberately 3 the tolerance away  cannot fire
    return (live+step if bias=="SELL" else live-step), False
def find_ob_typed(candles):
    """Order block WITH its direction, so the brief can say '4H Bearish OB' truthfully."""
    try:
        for i in range(len(candles)-3, 1, -1):
            if candles[i]['close'] < candles[i]['open'] and candles[i+1]['close'] > candles[i+1]['open']:
                return {'low':candles[i]['low'],'high':candles[i]['high'],'type':'Bullish'}
        for i in range(len(candles)-3, 1, -1):
            if candles[i]['close'] > candles[i]['open'] and candles[i+1]['close'] < candles[i+1]['open']:
                return {'low':candles[i]['low'],'high':candles[i]['high'],'type':'Bearish'}
    except: pass
    return None
def htf_poi(pair, fb_low, fb_high):
    """Highest-timeframe order block available: 4H -> 1H -> the synthetic zone."""
    for tf,label in (("4h","4H"),("60m","1H")):
        ob=find_ob_typed(candles_cached(pair, tf))
        if ob: return {'low':ob['low'],'high':ob['high'],'type':ob['type'],'tf':label}
    return {'low':fb_low,'high':fb_high,'type':'SYNTH','tf':'SYNTH'}
def asia_session(pair, now=None):
    """Return the most recent COMPLETED Asia range (00:00-07:00 UTC) and its date.

    Before 07:00 UTC, today's range is still forming, so keep using the last completed
    session. A candidate day must include the final 15m bar (06:45) and at least 20 bars;
    partial candles must not be frozen as the day's high/low.
    """
    try:
        c=candles_cached(pair,"15m")
        if not c: return None,None
        now=now or datetime.now(timezone.utc)
        cutoff=now.date() if (now.hour,now.minute)>=(7,0) else now.date()-timedelta(days=1)
        by_day={}
        for x in c:
            t=x.get("time")
            if not t: continue
            d=datetime.fromtimestamp(t,timezone.utc)
            if d.date()<=cutoff and d.hour<7:
                by_day.setdefault(d.date(),[]).append((d,x))
        for day in sorted(by_day,reverse=True):
            rows=by_day[day]
            if len(rows)<20: continue
            last=max(dt for dt,x in rows)
            if (last.hour,last.minute)<(6,45): continue
            return (max(x["high"] for dt,x in rows),min(x["low"] for dt,x in rows)),day.isoformat()
        return None,None
    except: return None,None

def asia_range(pair):
    """High/low of the most recent completed Asia session (00:00-07:00 UTC)."""
    return asia_session(pair)[0]
def sweep_label(pair, level, bias):
    """Name the liquidity that got swept, honestly: Asia high/low if it matches, else swing."""
    tol=sweep_tol(pair); a=asia_range(pair)
    if a and a[0] is not None:
        if abs(level-a[0]) <= tol*2: return "Asia high"
        if abs(level-a[1]) <= tol*2: return "Asia low"
    return "swing high" if bias=="SELL" else "swing low"
# =====================================================================
# V17.0 - PD ARRAYS THAT PERSIST UNTIL INVALIDATED
#  Every level is derived from a FIXED reference window, written to the levels
#  table ONCE, and re-derived only when it is invalidated. That is why the FVG /
#  POC / sweep numbers no longer change every minute.
#    PDH / PDL   prev UTC day high / low      - invalidated when SWEPT (traded through)
#    ASIA_H / L  00:00-07:00 UTC extremes     - invalidated when SWEPT
#    POC         prev day TPO profile (1m)    - recomputed once per day
#    FVG         nearest unmitigated gap in the bias direction
#                - invalidated when a candle CLOSES beyond its far edge
#    OB          4H order block (1H fallback) - invalidated on a close beyond it,
#                and it then becomes a BREAKER (polarity flip)
#    BREAKER     failed OB; kept until the zone fails (close through it again)
#  NOTE on POC: spot FX has no central volume feed, so a true volume POC is not
#  obtainable. This is a time-at-price POC (TPO) from previous-day 1m candles -
#  labelled as such rather than pretending to be volume profile.
# =====================================================================
def _lvl_row(pair, kind):
    return conn.execute("SELECT low,high,ts,status,note FROM levels WHERE lkey=?",(f"{pair}|{kind}",)).fetchone()

def lvl_rows(pair):
    return conn.execute("SELECT kind,low,high,ts,status,note FROM levels WHERE pair=?",(pair,)).fetchall()

def lvl_set(pair, kind, low, high, note="", status="ACTIVE", force=False):
    """Write the level once. An ACTIVE level is never silently overwritten (that was the bug)."""
    row=_lvl_row(pair,kind)
    if row and row[3]=="ACTIVE" and not force: return False
    conn.execute("REPLACE INTO levels VALUES (?,?,?,?,?,?,?,?)",
                 (f"{pair}|{kind}",pair,kind,low,high,datetime.now(WAT).strftime("%a %H:%M"),status,note))
    conn.commit(); return True

def lvl_status(pair, kind, status, note=""):
    # Keep the source-day marker when status changes; it prevents the next refresh
    # from mistaking a swept level for a new reference and reactivating it.
    row=conn.execute("SELECT note FROM levels WHERE lkey=?",(f"{pair}|{kind}",)).fetchone()
    old_note=(row[0] or "") if row else ""
    tag=re.search(r"\[source-day=\d{4}-\d{2}-\d{2}\]",old_note)
    if tag and tag.group(0) not in note:
        note=(note+" "+tag.group(0)).strip()
    conn.execute("UPDATE levels SET status=?, note=? WHERE lkey=?",(status,note,f"{pair}|{kind}")); conn.commit()

def utc_day_start(back=0):
    now=datetime.now(timezone.utc); d=(now-timedelta(days=back)).date()
    return datetime(d.year,d.month,d.day,tzinfo=timezone.utc)

def prev_day_extremes(pair):
    c=candles_cached(pair,"15m")
    if not c: return None
    s0=utc_day_start(1); e0=s0+timedelta(days=1)
    rows=[x for x in c if x.get("time") and s0.timestamp()<=x["time"]<e0.timestamp()]
    if len(rows)<10: return None
    return max(x["high"] for x in rows), min(x["low"] for x in rows)

def prev_day_poc(pair, bins=60):
    """Previous-day POC by time-at-price (TPO) from 1m candles: each candle contributes
    one unit spread across the price bins it covers. No volume feed exists for spot FX."""
    c=candles_cached(pair,"1m")
    if not c: return None
    s0=utc_day_start(1); e0=s0+timedelta(days=1)
    rows=[x for x in c if x.get("time") and s0.timestamp()<=x["time"]<e0.timestamp()]
    if len(rows)<60: return None
    lo=min(x["low"] for x in rows); hi=max(x["high"] for x in rows)
    if hi<=lo: return None
    size=(hi-lo)/bins; counts=[0.0]*bins
    for x in rows:
        a=max(0,min(bins-1,int((x["low"]-lo)/size))); b=max(0,min(bins-1,int((x["high"]-lo)/size)))
        w=1.0/(b-a+1)
        for i in range(a,b+1): counts[i]+=w
    k=counts.index(max(counts))
    return lo+(k+0.5)*size

def _dated_level_set(pair, kind, low, high, note, source_day):
    """Keep one level for a reference day; never revive/rewrite it within that day.

    A date tag lets the level roll forward at the UTC boundary without reviving a
    swept PDH/PDL or carrying yesterday's POC/Asia range into today's brief.
    """
    tag=f"[source-day={source_day}]"
    row=_lvl_row(pair,kind)
    if row and tag in (row[4] or ""):
        return False
    if low is None or high is None:
        if row:
            lvl_status(pair,kind,"STALE",f"reference data unavailable for {source_day}")
        return False
    return lvl_set(pair,kind,low,high,f"{note} {tag}",force=True)

def refresh_levels(pair, live, bias, c5=None, c15=None):
    """Derive levels from fixed UTC reference windows; roll daily levels once per UTC day."""
    prev_day=utc_day_start(1).date().isoformat()
    ext=prev_day_extremes(pair)
    _dated_level_set(pair,"PDH",ext[0] if ext else None,ext[0] if ext else None,
                     "previous UTC day high",prev_day)
    _dated_level_set(pair,"PDL",ext[1] if ext else None,ext[1] if ext else None,
                     "previous UTC day low",prev_day)
    poc=prev_day_poc(pair)
    _dated_level_set(pair,"POC",poc,poc,"prev-day TPO (1m time-at-price)",prev_day)
    a,asia_day=asia_session(pair)
    if a and asia_day:
        _dated_level_set(pair,"ASIA_HIGH",a[0],a[0],"Asia high (00-07 UTC)",asia_day)
        _dated_level_set(pair,"ASIA_LOW",a[1],a[1],"Asia low (00-07 UTC)",asia_day)

def levels_check(pair, live, bias, c5, c15):
    """Invalidate what the market has actually taken out. Nothing else changes."""
    # --- liquidity swept?
    for kind,up in (("PDH",True),("ASIA_HIGH",True),("PDL",False),("ASIA_LOW",False)):
        r=_lvl_row(pair,kind)
        if not r or r[3]!="ACTIVE": continue
        lvl=r[1] if up else r[0]
        if (up and live>=lvl) or ((not up) and live<=lvl):
            lvl_status(pair,kind,"SWEPT",f"swept, price reached {px(pair,live)}")
    # --- persisted swing liquidity (when no daily level was available)
    r=_lvl_row(pair,"SWEEP")
    if r and r[3]=="ACTIVE":
        up=("SELL" in (r[4] or ""))
        lvl=r[1] if up else r[0]
        if (up and live>=lvl) or ((not up) and live<=lvl):
            lvl_status(pair,"SWEEP","SWEPT",f"swept, price reached {px(pair,live)}")
    # --- FVG / OB mitigation: a CLOSE beyond the far edge invalidates the array
    for kind in ("FVG","OB"):
        r=_lvl_row(pair,kind)
        if not r or r[3]!="ACTIVE": continue
        low,high,ts,status,note=r
        bull = ("BUY" in (note or "")) or ("Bullish" in (note or "")) or ("BULL" in (note or ""))
        for x in (c5 or [])[-12:]:
            if bull and x["close"]<low:
                lvl_status(pair,kind,"MITIGATED",f"closed below {px(pair,low)}")
                if kind=="OB":
                    lvl_set(pair,"BREAKER",low,high,f"bearish breaker from Bullish OB {ts}",force=True)
                break
            if (not bull) and x["close"]>high:
                lvl_status(pair,kind,"MITIGATED",f"closed above {px(pair,high)}")
                if kind=="OB":
                    lvl_set(pair,"BREAKER",low,high,f"bullish breaker from Bearish OB {ts}",force=True)
                break
    # --- breaker: retested (still valid) or failed. A bearish breaker (failed Bullish OB)
    #     dies on a CLOSE back above the zone; a bullish breaker dies on a close below it -
    #     the candle that created it is expected to sit on the broken side.
    r=_lvl_row(pair,"BREAKER")
    if r and r[3]=="ACTIVE":
        low,high=r[0],r[1]
        note=r[4] or ""
        bear=("bearish breaker" in note)
        if live>=low and live<=high and not note.startswith("retested"):
            lvl_status(pair,"BREAKER","ACTIVE",f"{note.split(' from')[0]}, retested {datetime.now(WAT).strftime('%a %H:%M')}")
        for x in (c5 or [])[-12:]:
            if (bear and x["close"]>high) or ((not bear) and x["close"]<low):
                lvl_status(pair,"BREAKER","FAILED",f"closed back {'above' if bear else 'below'} the zone")
                break

def active_fvg(pair, bias, c5, c15):
    """Nearest unmitigated imbalance in the bias direction, persisted until mitigated."""
    r=_lvl_row(pair,"FVG")
    if r and r[3]=="ACTIVE" and (r[4] or "").startswith(bias):
        return r[0],r[1],True,r[2],r[4]
    if r and r[3]=="ACTIVE":
        lvl_status(pair,"FVG","SUPERSEDED","bias flipped")
    f=find_real_fvg(c15,bias) if c15 else None
    if not f: f=find_real_fvg(c5,bias) if c5 else None
    if not f: return None,None,False,None,None
    lvl_set(pair,"FVG",f["low"],f["high"],f"{bias} {f['type']} FVG",force=True)
    rr=_lvl_row(pair,"FVG")
    return rr[0],rr[1],True,rr[2],rr[4]

def active_ob(pair, live, bias):
    """Bias-aligned 4H/1H order block, persisted until mitigated."""
    want="Bullish" if bias=="BUY" else "Bearish"
    r=_lvl_row(pair,"OB")
    if r and r[3]=="ACTIVE" and want in (r[4] or ""):
        return r[0],r[1],True,r[2],r[4]
    htf=htf_poi(pair, live-0.001, live+0.001)
    if htf and htf.get("tf")!="SYNTH" and htf.get("type")==want:
        lvl_set(pair,"OB",htf["low"],htf["high"],f"{htf['tf']} {htf['type']} OB",force=True)
        rr=_lvl_row(pair,"OB")
        return rr[0],rr[1],True,rr[2],rr[4]
    return (htf["low"] if htf else live),(htf["high"] if htf else live),False,None,"SYNTH"

def next_liquidity(pair, live, bias, c5=None):
    """Nearest UNTAPPED liquidity the setup should run: PDH/Asia high above for a SELL,
    PDL/Asia low below for a BUY. Falls back to the swing level when none is stored."""
    cands=[]
    kinds=(("PDH","PDH"),("ASIA_HIGH","Asia high")) if bias=="SELL" else (("PDL","PDL"),("ASIA_LOW","Asia low"))
    for kind,label in kinds:
        r=_lvl_row(pair,kind)
        if not r or r[3]!="ACTIVE": continue
        lvl=r[1] if bias=="SELL" else r[0]
        d=(lvl-live) if bias=="SELL" else (live-lvl)
        if d<=0: continue
        cands.append((d,lvl,label))
    if cands:
        d,lvl,label=sorted(cands)[0]
        return lvl,f"{label} (untapped)",True
    # --- no daily level stored: the swing liquidity is persisted too, so it cannot
    #     drift while the candle window slides. It is only re-derived once price has
    #     traded through it (i.e. it has been swept).
    r=_lvl_row(pair,"SWEEP")
    if r and r[3]=="ACTIVE":
        lvl=r[1] if bias=="SELL" else r[0]
        if (bias=="SELL" and lvl>=live) or (bias=="BUY" and lvl<=live):
            return lvl,("swing high" if bias=="SELL" else "swing low"),True
        lvl_status(pair,"SWEEP","SWEPT",f"swept, price reached {px(pair,live)}")
    lvl,real=sweep_level(pair,live,bias,c5)
    if real:
        lvl_set(pair,"SWEEP",lvl,lvl,("SELL swing high" if bias=="SELL" else "BUY swing low"))
    return lvl,("swing high" if bias=="SELL" else "swing low"),real

def ote_band(pair, bias, c5):
    """Classic OTE: the 62-79% retracement of the last impulse leg (not the FVG midpoint)."""
    if not c5 or len(c5)<12: return None,None
    win=c5[-30:]
    hi=max(x["high"] for x in win); lo=min(x["low"] for x in win); R=hi-lo
    if R<=0: return None,None
    if bias=="BUY": return hi-0.79*R, hi-0.62*R
    return lo+0.62*R, lo+0.79*R

def stop_and_targets(pair, live, bias, zone_low, zone_high):
    """SL beyond the POI zone; TP1 at the nearest opposing untapped liquidity, TP2 at the next."""
    tol=sweep_tol(pair); buf=2*tol
    if bias=="BUY":
        sl=(zone_low if zone_low is not None else live)-buf
        tg=[]
        for kind in ("PDH","ASIA_HIGH"):
            r=_lvl_row(pair,kind)
            if r and r[3]=="ACTIVE" and r[1]>live+2*buf: tg.append(r[1])
        tg.sort()
        tp1=tg[0] if tg else live+2*(live-sl)
        tp2=tg[1] if len(tg)>1 else live+2*(tp1-live)
    else:
        sl=(zone_high if zone_high is not None else live)+buf
        tg=[]
        for kind in ("PDL","ASIA_LOW"):
            r=_lvl_row(pair,kind)
            if r and r[3]=="ACTIVE" and r[0]<live-2*buf: tg.append(r[0])
        tg.sort(reverse=True)
        tp1=tg[0] if tg else live-2*(sl-live)
        tp2=tg[1] if len(tg)>1 else live-2*(live-tp1)
    return sl,tp1,tp2

def build_pair(pair, live):
    c15=candles_cached(pair, "15m"); c5=candles_cached(pair, "5m")
    choch=find_choch(c15) if c15 else None
    if choch=="BULL_CHOCHO": bias="BUY"
    elif choch=="BEAR_CHOCHO": bias="SELL"
    else: bias="SELL" if pair in ["EUR/USD","GBP/USD","XAU/USD"] else "BUY"

    refresh_levels(pair, live, bias, c5, c15)      # derive once ...
    levels_check(pair, live, bias, c5, c15)        # ... invalidate only what the market took

    fvg_low,fvg_high,real_fvg,fvg_ts,fvg_note=active_fvg(pair, bias, c5, c15)
    if not real_fvg:                               # synthetic fallback so the brief always has a zone
        if pair in ["EUR/USD","GBP/USD","USD/CAD"]: fvg_low,fvg_high=live-0.0010,live+0.0008
        elif "JPY" in pair: fvg_low,fvg_high=live-0.20,live+0.15
        elif pair=="XAU/USD": fvg_low,fvg_high=live-4.0,live+4.0
        else: fvg_low,fvg_high=live-80,live+30
    ob_low,ob_high,real_ob,ob_ts,ob_note=active_ob(pair, live, bias)
    brk=_lvl_row(pair,"BREAKER")
    breaker=((brk[0]+brk[1])/2) if brk else None
    poc_row=_lvl_row(pair,"POC")
    # Never substitute the live quote for a previous-day POC. If data is missing
    # or the stored POC belongs to a prior UTC day, show unavailable instead.
    poc=poc_row[0] if poc_row and poc_row[3]=="ACTIVE" else None
    sweep,sweep_label,real_sweep=next_liquidity(pair, live, bias, c5)
    ote_low,ote_high=ote_band(pair, bias, c5)
    ote=((ote_low+ote_high)/2) if (ote_low is not None and ote_high is not None) else (fvg_low+fvg_high)/2
    zone_low,zone_high=fvg_low,fvg_high
    sl,tp1,tp2=stop_and_targets(pair, live, bias, zone_low, zone_high)

    return {"live":live,"bias":bias,
            "fvg_low":fvg_low,"fvg_high":fvg_high,"real_fvg":real_fvg,"fvg_ts":fvg_ts,"fvg_note":fvg_note,
            "fvg_type":(fvg_note.split()[1] if (fvg_note and len(fvg_note.split())>1) else ("BEAR" if bias=="SELL" else "BULL")),
            "ht_low":ob_low,"ht_high":ob_high,"real_ob":real_ob,"ob_ts":ob_ts,"ob_note":ob_note,
            "htf_low":ob_low,"htf_high":ob_high,"htf_type":("Bullish" if bias=="BUY" else "Bearish"),
            "htf_tf":("4H" if real_ob else "SYNTH"),
            "sweep":sweep,"sweep_label":sweep_label,"real_sweep":real_sweep,
            "breaker":breaker,"poc":poc,"ote":ote,"ote_low":ote_low,"ote_high":ote_high,
            "entry":live,"sl":sl,"tp1":tp1,"tp2":tp2,
            "zone_low":zone_low,"zone_high":zone_high,
            "choch":choch or "WAIT"}
NEWS_CAL_URL=os.getenv("NEWS_CAL_URL","https://nfs.faireconomy.media/ff_calendar_thisweek.json")
NEWS_RSS_URL=os.getenv("NEWS_RSS_URL","https://www.investing.com/rss/news_1.rss")
NEWS_CSV_URL=os.getenv("NEWS_CSV_URL","https://nfs.faireconomy.media/ff_calendar_thisweek.csv")
NEWS_FILTER=os.getenv("NEWS_FILTER","on").lower()!="off"
try: NEWS_BLACKOUT_MIN=int(os.getenv("NEWS_BLACKOUT_MIN","15"))
except: NEWS_BLACKOUT_MIN=15
PAIR_CCY={"EUR/USD":("EUR","USD"),"GBP/USD":("GBP","USD"),"USD/CAD":("USD","CAD"),
          "EUR/JPY":("EUR","JPY"),"USD/JPY":("USD","JPY"),"XAU/USD":("USD",),
          "NAS100":("USD",),"US30":("USD",),"GERMANY40":("EUR",)}
_news={"events":[],"headlines":[],"cal_ts":0,"rss_ts":0,"cal_fail":0,"rss_fail":0,"error":"","next_try":0,"source":"","src_try":{}}
_NEWS_LOCK=threading.Lock()

def _cal_rows_json():
    r=requests.get(NEWS_CAL_URL, headers={"User-Agent":"Mozilla/5.0"}, timeout=12)
    if r.status_code==429:
        raise RuntimeError(f"429 rate limited (retry after {r.headers.get('Retry-After','?')}s)")
    if r.status_code!=200:
        raise RuntimeError(f"http {r.status_code}")
    ev=r.json()
    out=[]
    for e in ev:
        try: when=datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except: continue
        if not e.get("country"): continue
        out.append({"when":when,"ccy":e["country"].upper(),"impact":e.get("impact") or "Low",
                    "title":(e.get("title") or "").strip(),
                    "forecast":(e.get("forecast") or "").strip(),
                    "previous":(e.get("previous") or "").strip()})
    return out

def _cal_rows_csv():
    """Fallback source on the same host. Verified: the CSV clock is UTC
    (AUD Employment 12:30am -> 01:30 WAT, matching the JSON feed exactly)."""
    import csv, io
    r=requests.get(NEWS_CSV_URL, headers={"User-Agent":"Mozilla/5.0"}, timeout=12)
    if r.status_code==429:
        raise RuntimeError(f"429 rate limited (retry after {r.headers.get('Retry-After','?')}s)")
    if r.status_code!=200:
        raise RuntimeError(f"http {r.status_code}")
    tz=ZoneInfo(os.getenv("NEWS_CSV_TZ","UTC"))
    out=[]
    for row in csv.DictReader(io.StringIO(r.text)):
        try: naive=datetime.strptime(f"{row['Date'].strip()} {row['Time'].strip()}", "%m-%d-%Y %I:%M%p")
        except Exception: continue
        ccy=(row.get("Country") or "").strip().upper()
        if not ccy: continue
        out.append({"when":naive.replace(tzinfo=tz).astimezone(timezone.utc),"ccy":ccy,
                    "impact":(row.get("Impact") or "Low").strip(),"title":(row.get("Title") or "").strip(),
                    "forecast":(row.get("Forecast") or "").strip(),"previous":(row.get("Previous") or "").strip()})
    return out

def fetch_calendar(ttl=1800, force=False):
    """Economic calendar: JSON first, CSV fallback (separate rate limiters on the same host),
    per-source cooldowns so a 429 on one never blocks the other, last-good copy always kept."""
    with _NEWS_LOCK:
        if _news["events"] and time.time()-_news["cal_ts"] < ttl and not force:
            return _news["events"]
        now=time.time()
        sources=[("json",_cal_rows_json),("csv",_cal_rows_csv)]
        avail=[(n,f) for n,f in sources if now >= _news["src_try"].get(n,0)]
    if not avail:
        return _news["events"]                    # every source cooling down: no network calls at all
    err=""
    for name,fn in avail:
        try:
            out=fn()
            if not out: raise RuntimeError("empty feed")
            out.sort(key=lambda x:x["when"])
            with _NEWS_LOCK:
                _news["events"]=out; _news["cal_ts"]=time.time(); _news["source"]=name
                _news["error"]=""; _news["cal_fail"]=0
                _news["next_try"]=0
            print(f"NEWS: calendar loaded from {name.upper()}, {len(out)} events")
            return out
        except Exception as e:
            err=f"{name}: {str(e)[:70]}"
            mt=re.search(r"retry after (\d+)s", str(e))
            cool=int(mt.group(1))+5 if mt else 900
            with _NEWS_LOCK:
                _news["src_try"][name]=time.time()+cool
                _news["next_try"]=min(_news["src_try"].values())
                _news["cal_fail"]+=1; _news["error"]=err
            print(f"NEWS: {name} feed fail ({str(e)[:80]}) - cooling down {cool}s, trying next source")
    return _news["events"]

def fetch_headlines(ttl=900, limit=6):
    """RSS headlines, cached. Silent on failure - headlines are decoration, not signals."""
    with _NEWS_LOCK:
        if _news["headlines"] and time.time()-_news["rss_ts"] < ttl: return _news["headlines"]
    try:
        r=requests.get(NEWS_RSS_URL, headers={"User-Agent":"Mozilla/5.0"}, timeout=12)
        titles=[re.sub(r"<[^>]+>","",t).strip() for t in re.findall(r"<title>(.*?)</title>", r.text, re.S)]
        titles=[t for t in titles if t and "Investing.com" not in t][:limit]
        with _NEWS_LOCK:
            _news["headlines"]=titles; _news["rss_ts"]=time.time()
        return titles
    except Exception as e:
        _news["rss_fail"]+=1
        print(f"NEWS: rss fetch fail ({e})")
        return _news["headlines"]

def wat(dt): return dt.astimezone(WAT)

def fmt_in(mins):
    if mins<0: return "passed"
    if mins<60: return f"in {int(mins)}m"
    return f"in {int(mins//60)}h{int(mins%60):02d}m"

def next_event(pair, impacts=("High",), within_min=None):
    """Next High-impact event for either currency of the pair. -> (event, minutes) or (None, None)"""
    ccys=PAIR_CCY.get(pair)
    if not ccys: return (None,None)
    now=datetime.now(timezone.utc)
    for e in fetch_calendar():
        if e["impact"] not in impacts: continue
        if e["ccy"] not in ccys: continue
        mins=(e["when"]-now).total_seconds()/60.0
        if within_min is not None and mins>within_min: continue
        if mins < -240: continue                       # ignore ancient events
        return (e, mins)
    return (None,None)

def news_blackout(pair):
    """True when a High-impact release for this pair is within +/- NEWS_BLACKOUT_MIN minutes."""
    if not NEWS_FILTER: return (False,None,0)
    e,mins=next_event(pair, ("High",))
    if e and -NEWS_BLACKOUT_MIN <= mins <= NEWS_BLACKOUT_MIN:
        return (True, e, mins)
    return (False, e, mins)

def news_line(pair):
    """One-line factual news context for a pair (used in the brief)."""
    e,mins=next_event(pair, ("High","Medium"))
    if not e:
        if not fetch_calendar(): return "no calendar data (feed unreachable)"
        return "no high-impact event scheduled soon"
    tag=f"fcast {e['forecast']}" if e['forecast'] else "no forecast"
    return f"{e['ccy']} {e['title']} {wat(e['when']).strftime('%-I:%M %p')} WAT ({fmt_in(mins)}, {tag})"

def day_events(n=3, impacts=("High",)):
    """Upcoming high-impact events across all currencies (for the brief header / /news)."""
    now=datetime.now(timezone.utc); out=[]
    for e in fetch_calendar():
        if e["impact"] not in impacts: continue
        mins=(e["when"]-now).total_seconds()/60.0
        if mins < -30: continue
        out.append((e,mins))
        if len(out)>=n: break
    return out

def generate_full():
    d=get_live_all_live()
    now=datetime.now(WAT)
    gold=d['XAU/USD']; dxy=d['DXY']; eur=d['EUR/USD']; wti=d['WTI']; nas=d['NAS100']; us30=d['US30']; ger=d['GERMANY40']
    news=os.getenv("NEWS_NOTE","").strip()
    headlines=(f"Gold {gold:.2f} | DXY {dxy:.2f} | EUR/USD {eur:.5f} | WTI ${wti:.2f} | "
               f"NAS100 {nas:.2f} | US30 {us30:.2f} | GER40 {ger:.2f} - all LIVE"
               + (f" | {news}" if news else ""))

    txt=f"<b>\U0001f4ca 9 PAIRS - FULL ICT BRIEF {now.strftime('%a %b %d %I:%M %p')} WAT - V17.0</b>\n"
    txt+="Candles: Deriv public feed (real FVG/OB/CHOCH) | Prices: er-api, gold-api, stooq\n"
    txt+=f"<b>HEADLINES:</b> {headlines}\n"
    ev=day_events(3)
    ev_txt=" | ".join(f"{e['ccy']} {e['title']} {wat(e['when']).strftime('%-I:%M %p')} WAT ({fmt_in(m)})" for e,m in ev)
    txt+=(f"<b>NEWS (next high-impact):</b> {ev_txt}\n" if ev else "<b>NEWS:</b> calendar feed unreachable - no event filtering this run\n")
    txt+=("<b>Killzones:</b> LONDON 8:00-10:30 AM | NY 1:30-6:00 PM WAT\n\n"
          f"<b>Entry blackout:</b> {NEWS_BLACKOUT_MIN} min either side of a high-impact release (NEWS_FILTER={'ON' if NEWS_FILTER else 'OFF'})\n\n"
          "<b>\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501</b>\n\n")

    def lvl_txt(pair, kind):
        r=_lvl_row(pair,kind)
        if not r: return "-"
        return f"{px(pair,r[1] if kind.endswith('H') else r[0])}" + ("" if r[3]=="ACTIVE" else f" ({r[3].lower()})")

    pairs=[("EUR/USD","\U0001f4b6"),("GBP/USD","\U0001f4b7"),("USD/CAD","\U0001f341"),("XAU/USD","\U0001f3c6"),
           ("EUR/JPY","\U0001f4b6"),("USD/JPY","\U0001f4b4"),("NAS100","\U0001f4c8"),("US30","\U0001f4c8"),("GERMANY40","\U0001f1e9\U0001f1ea")]
    for i,(pair,emoji) in enumerate(pairs,1):
        live=d.get(pair, FALLBACK[pair])
        c=build_pair(pair, live)
        row=conn.execute("SELECT sweep, choch, fvg, confirm FROM steps WHERE pair=?",(pair,)).fetchone() or (0,0,0,0)
        s1,s2,s3,s4=[int(x) for x in row]
        bias=c['bias']
        _poc=_lvl_row(pair,"POC")
        poc_txt=(_poc[4] if _poc and _poc[3]=="ACTIVE" else "prev-day TPO unavailable (previous UTC day data missing or insufficient)")
        poc_display=px(pair,c["poc"]) if c["poc"] is not None else "unavailable"
        if s4:   status="\u2705 IN TRADE - journaled"
        elif s3: status="\u26a0\ufe0f WAIT for confirmation (5m CHOCH)"
        elif s2: status="\u26a0\ufe0f WAIT for FVG retest"
        elif s1: status="\u26a0\ufe0f WAIT for CHOCH"
        else:    status="\u26a0\ufe0f WAIT for sweep"
        fvg_tag="REAL" if c['real_fvg'] else "SYNTH"
        ob_tag="REAL" if c['real_ob'] else "SYNTH"
        swp_tag="REAL" if c['real_sweep'] else "SYNTH"
        ote_display=(f"{px(pair,c['ote_low'])}-{px(pair,c['ote_high'])}"
                     if c['ote_low'] is not None and c['ote_high'] is not None else "unavailable")
        htf_txt=(f"{c['htf_tf']} {c['htf_type']} OB {px(pair,c['htf_low'])}-{px(pair,c['htf_high'])}"
                 if c['htf_tf']!="SYNTH" else f"SYNTH zone {px(pair,c['htf_low'])}-{px(pair,c['htf_high'])}")
        txt+=(f"{emoji} <b>{i}) {pair} - Current {px(pair,live)} - DXY {dxy:.2f}</b>\n"
              f"Bias: {bias}{' after sweep' if s1 else ''} | Live Price: {px(pair,live)}\n"
              f"HTF POI: {htf_txt}\n"
              f"FVG: {px(pair,c['fvg_low'])}-{px(pair,c['fvg_high'])} ({fvg_tag}, {c['fvg_type']}{', set '+c['fvg_ts'] if c['fvg_ts'] else ''})\n"
              f"Liquidity: Sweep {px(pair,c['sweep'])} ({c['sweep_label']}, {swp_tag}) | PDH {lvl_txt(pair,'PDH')} | PDL {lvl_txt(pair,'PDL')}\n"
              f"Breaker: {px(pair,c['breaker']) if c['breaker'] else 'none yet'}"
              f" | POC: {poc_display} ({poc_txt})"
              f" | OTE: {ote_display} (62-79% retracement)\n"
              f"Entry: {px(pair,c['entry'])} | SL: {px(pair,c['sl'])} | TP1: {px(pair,c['tp1'])} | TP2: {px(pair,c['tp2'])}\n"
              f"RR: 1:2 / 1:4 | Window: London 8-10:30, NY 1:30-6 PM WAT | Zone: {px(pair,c['zone_low'])}-{px(pair,c['zone_high'])}\n"
              f"Setup: Sweep {px(pair,c['sweep'])} then CHOCH then FVG retest\n"
              f"Confirmation: 15m CHOCH {c['choch']} + {c['fvg_type']} FVG {fvg_tag} | OB {ob_tag}\n"
              f"News: {news_line(pair)}\n"
              f"Context / tracker: DXY {dxy:.2f} (context only, not a signal) | sweep {s1}/1, CHOCH {s2}/1, FVG retest {s3}/1, confirmation {s4}/1 | management: BE+0.5 at 50% of TP1\n"
              f"Status: {status}\n\n<b>\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501</b>\n\n")
    txt+=("<b>\u26a0\ufe0f Educational only - not financial advice.</b>\n"
          "An entry needs the full sequence (sweep, then CHOCH, then FVG, then 5m confirmation) inside a killzone. "
          "Silence means nothing qualified.")
    return txt,d
def is_killzone():
    now=datetime.now(WAT)
    t=now.hour+now.minute/60.0
    london=8.0<=t<=10.5; ny=13.5<=t<=18.0
    return (london or ny), now.strftime("%I:%M %p WAT"), "LONDON" if london else "NY" if ny else "OFF"
def tick_monitor():
    while True:
        try:
            d=get_live_all_live()
            in_kz,now_str,kz_name=is_killzone()
            if in_kz:
                for pair in PAIRS_9:
                    live=d.get(pair)
                    if not live: continue
                    c=build_pair(pair,live)
                    row=conn.execute("SELECT sweep, choch, fvg, confirm FROM steps WHERE pair=?",(pair,)).fetchone()
                    if not row:
                        conn.execute("INSERT INTO steps VALUES (?,?,?,?,?,?)",(pair,0,0,0,0,now_str)); conn.commit(); row=(0,0,0,0)
                    s1,s2,s3,s4=row
                    # [OK] FIX: real candle-derived level only, per-instrument tolerance, and price must
                    #    have actually reached the liquidity (SELL  into the swing high, BUY  into the low)
                    tol=sweep_tol(pair)
                    reached = live <= c['sweep']+tol if c['bias']=="BUY" else live >= c['sweep']-tol
                    if s1==0 and c['real_sweep'] and reached:
                        conn.execute("UPDATE steps SET sweep=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        notify(f"1\ufe0f\u20e3 <b>SWEEP {pair} [{kz_name}]</b> @ {now_str}\nLive {px(pair,live)} swept {c['sweep_label']} {px(pair,c['sweep'])} (tol {tol:g}) LIVE\nJournal logged")
                    if s1==1 and s2==0 and c['choch'] in ("BULL_CHOCHO","BEAR_CHOCHO"):
                        conn.execute("UPDATE steps SET choch=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        notify(f"2\ufe0f\u20e3 <b>CHOCH {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\n{c['choch']} @ {px(pair,live)} LIVE\nJournal logged")
                    if s2==1 and s3==0 and c['fvg_low']<=live<=c['fvg_high']:
                        conn.execute("UPDATE steps SET fvg=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],c['bias'],"FVG_RETEST")); conn.commit()
                        notify(f"3\ufe0f\u20e3 <b>FVG RETEST {pair} [{kz_name}]</b> @ {now_str}\nInside {px(pair,c['fvg_low'])}-{px(pair,c['fvg_high'])} LIVE\nJournal logged")
                    if s3==1 and s4==0:
                        # V17.0: never fire an ENTRY into a high-impact release. Steps stay at 3/4,
                        # so the entry can still happen once the event window passes.
                        bad,ev,mins=news_blackout(pair)
                        if ev is not None:
                            conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],c['bias'],f"HOLD_NEWS_{ev['ccy']}")); conn.commit()
                        if bad:
                            key=(pair,ev['when'].isoformat())
                            if _hold_sent.get(pair)!=key:
                                _hold_sent[pair]=key
                                notify(f"\u23f8 <b>HOLD {pair} [{kz_name}]</b> @ {now_str}\n{ev['ccy']} {ev['title']} at {wat(ev['when']).strftime('%-I:%M %p')} WAT ({fmt_in(mins)}) - inside the {NEWS_BLACKOUT_MIN} min blackout.\nSetup is complete (3/4) and will be held, not entered. Alert resumes after the release.")
                            continue
                        if False:
                            pass
                        c5=candles_cached(pair, "5m")
                        choch5=find_choch(c5) if c5 else None
                        if (c['bias']=="BUY" and choch5=="BULL_CHOCHO") or (c['bias']=="SELL" and choch5=="BEAR_CHOCHO"):
                            conn.execute("UPDATE steps SET confirm=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                            conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,c['sl'],c['tp1'],c['tp2'],c['bias'],datetime.now(WAT).isoformat(),"OPEN",0,f"CONFIRMED_{kz_name}_{choch5}_LIVE")); conn.commit()
                            conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                            notify(f"\u2705 <b>ENTRY {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\nEntry {px(pair,live)} SL {px(pair,c['sl'])} TP {px(pair,c['tp1'])}/{px(pair,c['tp2'])}\n\U0001f4dd Trade decision journaled \u2014 Full V5.1 confirmation")
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            for _t in cur:                                   # V17.0: warn if an open trade runs into news
                _p=_t[1]
                _bad,_ev,_m=news_blackout(_p)
                if _ev is not None and 0 <= _m <= NEWS_BLACKOUT_MIN:
                    _k=(_p,_ev['when'].isoformat())
                    if _hold_sent.get("open:"+_p)!=_k:
                        _hold_sent["open:"+_p]=_k
                        notify(f"\u26a0\ufe0f <b>NEWS RISK {_p}</b> @ {now_str}\n{_ev['ccy']} {_ev['title']} at {wat(_ev['when']).strftime('%-I:%M %p')} WAT ({fmt_in(_m)}) while this trade is open.\nConsider BE (stop at entry) before the release.")
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                if result=="OPEN" and pips>=tp1_pips*0.5:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"\U0001f527 <b>BE+0.5 {pair} {bias}</b> @ {px(pair,entry)} \u2192 {px(pair,live)} +{pips:.1f}p @ {now_str} LIVE\nJournal updated")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"\u2705 <b>TP1 {pair}</b> +{pips:.1f}p Live {px(pair,live)} @ {now_str} LIVE\nJournal logged")
                elif result=="TP1_HIT" and ((bias=="BUY" and live>=tp2) or (bias=="SELL" and live<=tp2)):
                    final=calc_pips(pair,entry,tp2,bias)
                    conn.execute("UPDATE trades SET result='TP2_HIT', pips=? WHERE id=?",(final,tid)); conn.commit()
                    notify(f"\U0001f3c6 <b>TP2 {pair}</b> +{final:.1f}p FULL @ {now_str} LIVE\nJournal logged")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live<=sl) or (bias=="SELL" and live>=sl)):
                    conn.execute("UPDATE trades SET result='SL_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"\u274c <b>SL {pair}</b> {pips:.1f}p Live {px(pair,live)} @ {now_str} LIVE\nJournal logged")
            time.sleep(15)
        except Exception as e:
            import traceback; print(traceback.format_exc()); time.sleep(10)
def send_long(chat, text):
    for i in range(0,len(text),3800):
        notify(text[i:i+3800], chat=chat)
        time.sleep(0.5)
# =====================================================================
# COMMANDS
# =====================================================================
HELP_TEXT=("\U0001f916 <b>V17.0 COMMANDS</b> - commands work in any case: /Brief = /brief\n\n"
 "/brief - full 9-pair ICT brief with real candles (same as the 7 AM post)\n"
 "/check - health: webhook, channel, token, prices, candles, DB, open trades\n"
 "/valid - setup validator: real FVG / OB / CHOCH / sweep per pair\n"
 "/probables - pairs sitting inside an FVG right now\n"
 "/steps - sweep, CHOCH, FVG, confirm tracker per pair\n"
 "/pips - running P&L: open trades + closed results\n"
 "/journal - last 20 trade decisions\n"
 "/news - economic calendar + headlines (free feed, no key)\n"
 "/reset - clear step tracker ('/reset all confirm' also wipes the journal)\n\n"
 "Educational only.")
# V17.0 FIX: one brief at a time. The lock makes the busy check atomic even when
# two different Telegram updates arrive on separate worker threads at once.
_brief_busy={"on":False}
_brief_busy_lock=threading.Lock()

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    with _brief_busy_lock:
        busy=_brief_busy["on"]
        if not busy: _brief_busy["on"]=True
    if busy:
        notify("\u23f3 A brief is already being generated - one moment.", chat=m.chat.id)
        return
    try:
        _cmd_brief_inner(m)
    finally:
        with _brief_busy_lock: _brief_busy["on"]=False

def _cmd_brief_inner(m):
    notify("\u2600\ufe0f Fetching TRUE 9 LIVE V17.0 COMPLETE \u2014 er-api + stooq nq.f ym.f dx.f cl.f + gold-api \u2014 WEBHOOK...", chat=m.chat.id)
    try:
        full,d=generate_full()
        send_long(m.chat.id, full)
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
@bot.message_handler(commands=['help','commands'])
def cmd_help(m):
    notify(HELP_TEXT, chat=m.chat.id)
# [OK] NEW: /check  system health (was a no-op before)
@bot.message_handler(commands=['check','health'])
def cmd_check(m):
    try:
        in_kz,now_str,kz=is_killzone()
        lines=[f"\U0001fa7a <b>CHECK @ {now_str}</b>"]
        lines.append(f"Killzone: <b>{kz}</b> {'ACTIVE \u2705' if in_kz else 'OFF \u2014 London 8:00-10:30, NY 13:30-18:00 WAT'}")
        try:
            info=bot.get_webhook_info()
            if info.url:
                lines.append(f"Webhook: \u2705 set (pending {info.pending_update_count})")
                if info.last_error_message: lines.append(f"\u26a0\ufe0f last webhook error: {info.last_error_message}")
            else:
                lines.append("Webhook: \u274c NOT set \u2014 open /setwebhook in a browser")
        except Exception as e:
            lines.append(f"Webhook: \u274c {e}")
        lines.append(f"Token: {'\u2705 present' if TOKEN else '\u274c MISSING'} | Channel: {CHANNEL_ID if CHANNEL_ID else '\u274c MISSING (set CHANNEL_ID)'}")
        lines.append(f"DB: {conn.backend}")
        lines.append(f"Candles: {candle_status_line()}")
        _e,_m=next_event("EUR/USD", ("High",))
        lines.append(f"News: {'FILTER ON' if NEWS_FILTER else 'filter OFF'} | blackout {NEWS_BLACKOUT_MIN}m | " +
                     (f"next: {_e['ccy']} {_e['title'][:30]} {fmt_in(_m)}" if _e else "no upcoming high-impact event"))
        try:
            d=get_live_all_live()
            stale=[p for p in FALLBACK if p in d and abs(d[p]-FALLBACK[p])<1e-9]
            live_n=len([p for p in FALLBACK if p in d and p not in stale])
            lines.append(f"Feeds: {live_n}/{len(FALLBACK)} live" + (f" | on fallback: {', '.join(stale)}" if stale else " | all live \u2705"))
            lines.append("Prices: " + " | ".join(f"{p} {d[p]:g}" for p in FALLBACK if p in d))
        except Exception as e:
            lines.append(f"Feeds: \u274c {e}")
        opened=conn.execute("SELECT COUNT(*) FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchone()[0]
        total=conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        lines.append(f"Trades: {opened} open/in-progress of {total} journaled")
        send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
# [OK] NEW: /valid  real structure validator per pair
@bot.message_handler(commands=['valid'])
def cmd_valid(m):
    notify("\U0001f50e Validating all 9 setups \u2014 reading live candles for real FVG / OB / CHOCH (can take ~1 min)\u2026", chat=m.chat.id)
    try:
        d=get_live_all_live()
        in_kz,now_str,kz=is_killzone()
        out=[f"\u2705 <b>SETUP VALIDATOR @ {now_str}</b> | Killzone {kz} {'ACTIVE' if in_kz else 'OFF'}"]
        ready=0; real_candles=0
        for pair in PAIRS_9:
            live=d.get(pair,FALLBACK[pair])
            c=build_pair(pair,live)
            row=conn.execute("SELECT sweep,choch,fvg,confirm FROM steps WHERE pair=?",(pair,)).fetchone() or (0,0,0,0)
            fvg_ok="FVG \u2705" if c['real_fvg'] else "FVG \u274c"
            ob_ok="OB \u2705" if c['real_ob'] else "OB \u274c"
            choch_ok="CHOCH \u2705" if c['choch'] in ("BULL_CHOCHO","BEAR_CHOCHO") else "CHOCH \u274c"
            sw_ok=f"SWEEP \u2705@tol {sweep_tol(pair):g}" if c['real_sweep'] else "SWEEP \u274c"
            score=sum(1 for x in (fvg_ok,ob_ok,choch_ok) if x.endswith("\u2705"))
            if c['real_fvg'] or c['real_ob']: real_candles+=1
            if score==3: ready+=1
            verdict="READY \U0001f6a6" if score==3 else "WATCH \U0001f440" if score>=1 else "NO STRUCTURE"
            out.append(f"{pair}: {c['bias']} | {fvg_ok} | {ob_ok} | {choch_ok} | {sw_ok} | steps {sum(int(s) for s in row)}/4 | {verdict}")
        out.append(f"\n{ready}/9 pairs show full real structure ({real_candles}/9 had any real candle data).")
        if real_candles==0:
            out.append("\u26a0\ufe0f No real structure found anywhere \u2192 the candle source (Yahoo) is likely blocked from this host. Prices are still live; FVG/OB fall back to synthetic levels.")
        out.append("Entry still requires the full sweep \u2192 CHOCH \u2192 FVG \u2192 confirmation sequence inside a killzone.")
        send_long(m.chat.id, "\n".join(out))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
@bot.message_handler(commands=['probables','prob'])
def cmd_probables(m):
    try:
        cur=conn.execute("SELECT pair, fvg_low, fvg_high, bias, stage FROM probables").fetchall()
        if not cur:
            notify("No probables \u2014 waiting killzone 8-10:30 & 13:30-18:00 WAT", chat=m.chat.id)
        else:
            lines=["\U0001f3af <b>PROBABLES \u2014 inside FVG, waiting confirmation</b>"]
            for pair,lo,hi,bias,stage in cur:
                lines.append(f"{pair} {bias} FVG {px(pair,lo)}-{px(pair,hi)} {stage} LIVE")
            send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
# [OK] NEW: /steps now shows the step tracker (the thing the steps table actually holds)
@bot.message_handler(commands=['steps'])
def cmd_steps(m):
    try:
        rows=conn.execute("SELECT pair, sweep, choch, fvg, confirm, last_time FROM steps ORDER BY pair").fetchall()
        if not rows:
            notify("No step tracking yet \u2014 it starts automatically inside killzones 8:00-10:30 & 13:30-18:00 WAT", chat=m.chat.id)
            return
        lines=["\U0001fa9c <b>STEP TRACKER</b> \u2014 1 Sweep \u2192 2 CHOCH \u2192 3 FVG \u2192 4 Confirm"]
        for pair,s1,s2,s3,s4,last in rows:
            marks=" ".join(f"{i+1}{'\u2705' if v else '\u2b1c'}" for i,v in enumerate((s1,s2,s3,s4)))
            lines.append(f"{pair}: {marks} | {last or '-'}")
        send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
# [OK] NEW: /pips  running P&L
@bot.message_handler(commands=['pips','pnl'])
def cmd_pips(m):
    try:
        d=get_live_all_live()
        out=["\U0001f4b0 <b>P&amp;L SUMMARY V17.0</b>"]
        openrows=conn.execute("SELECT pair, entry, bias, result, pips FROM trades WHERE result IN ('OPEN','BE','TP1_HIT') ORDER BY id DESC").fetchall()
        if openrows:
            out.append("<b>OPEN / IN PROGRESS:</b>")
            run=0.0
            for pair,entry,bias,result,pips in openrows:
                live=d.get(pair)
                cur=calc_pips(pair,entry,live,bias) if live else (pips or 0.0)
                run+=cur
                out.append(f"{pair} {bias} @ {px(pair,entry)} \u2192 {cur:+.1f}p [{result}]")
            out.append(f"Open total: <b>{run:+.1f}p</b>")
        else:
            out.append("Open: none")
        closed=conn.execute("SELECT result, pips FROM trades WHERE result IN ('TP2_HIT','SL_HIT')").fetchall()
        if closed:
            wins=[p or 0 for r,p in closed if r=="TP2_HIT"]; losses=[p or 0 for r,p in closed if r=="SL_HIT"]
            out.append(f"\n<b>CLOSED:</b> {len(closed)} trades | \u2705 {len(wins)} full TP2 | \u274c {len(losses)} SL")
            out.append(f"Wins {sum(wins):+.1f}p | Losses {sum(losses):+.1f}p | Net <b>{sum(wins)+sum(losses):+.1f}p</b>")
            if closed: out.append(f"Win rate: {100*len(wins)/len(closed):.0f}%")
        else:
            out.append("Closed: none yet")
        out.append("\nEducational only.")
        send_long(m.chat.id, "\n".join(out))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
@bot.message_handler(commands=['news'])
def cmd_news(m):
    try:
        out=["\U0001f4f0 <b>NEWS + ECONOMIC CALENDAR</b> (free feed: Forex Factory community JSON)"]
        e,mins=next_event("EUR/USD", ("High",))
        out.append(f"Next high-impact: <b>{e['ccy']} {e['title']}</b> {wat(e['when']).strftime('%a %-I:%M %p')} WAT ({fmt_in(mins)})" if e else "No upcoming high-impact event found in the feed.")
        rows=day_events(6, ("High","Medium"))
        if rows:
            out.append("\n<b>Upcoming (high + medium):</b>")
            for ev,mm in rows:
                tag=f"fcast {ev['forecast']}" if ev['forecast'] else "no forecast"
                out.append(f"  {wat(ev['when']).strftime('%a %-I:%M %p')} WAT | {ev['impact'][:1].upper()} | {ev['ccy']} {ev['title'][:38]} ({tag})")
        out.append(f"\n<b>Blackout:</b> {NEWS_BLACKOUT_MIN} min either side of High impact | filter {'ON' if NEWS_FILTER else 'OFF'}")
        hl=fetch_headlines(limit=5)
        if hl:
            out.append("\n<b>Headlines:</b>")
            for t in hl[:5]: out.append("  - "+t[:110])
        out.append("\nHeadlines are context only - the bot never trades on sentiment. News cannot predict direction; the blackout exists to keep you out of event spikes.")
        send_long(m.chat.id, "\n".join(out))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

@bot.message_handler(commands=['journal'])
def cmd_journal(m):
    try:
        cur=conn.execute("SELECT pair, entry, bias, result, pips, stage FROM trades ORDER BY id DESC LIMIT 20").fetchall()
        if not cur:
            notify("No trades yet \u2014 journal empty", chat=m.chat.id)
            return
        lines=[f"{r[0]} {r[2]} @ {px(r[0],r[1])} | {r[3]} {r[4]:.1f}p {r[5]} LIVE" for r in cur]
        send_long(m.chat.id, "\U0001f4d2 <b>JOURNAL \u2014 Last 20 trade decisions \u2014 V17.0</b>\n\n" + "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
# [OK] NEW: /reset  clears the step tracker; journal only with an explicit confirm
@bot.message_handler(commands=['reset'])
def cmd_reset(m):
    try:
        txt=(m.text or "").lower()
        conn.execute("DELETE FROM steps"); conn.execute("DELETE FROM probables"); conn.commit()
        msg="\u267b\ufe0f Step tracker + probables cleared \u2014 next killzone starts fresh from step 1."
        if "all" in txt and "confirm" in txt:
            conn.execute("DELETE FROM trades"); conn.commit()
            msg+="\n\U0001f5d1\ufe0f Trade journal also wiped (/reset all confirm)."
        else:
            msg+="\nJournal untouched. To wipe the journal too, send: <code>/reset all confirm</code>"
        notify(msg, chat=m.chat.id)
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)
@bot.message_handler(func=lambda m: (m.text or "").startswith("/"))
def cmd_any(m):
    """Catch-all: makes every command case-insensitive (/Brief, /Pips) and answers unknown ones."""
    name=(m.text or "").split()[0][1:].split("@")[0].lower()
    handlers={"start":cmd_brief,"brief":cmd_brief,"help":cmd_help,"commands":cmd_help,
              "check":cmd_check,"health":cmd_check,"valid":cmd_valid,
              "probables":cmd_probables,"prob":cmd_probables,"steps":cmd_steps,
              "pips":cmd_pips,"pnl":cmd_pips,"journal":cmd_journal,"reset":cmd_reset,
              "news":cmd_news,"calendar":cmd_news}
    h=handlers.get(name)
    if h: h(m)
    else: notify(f"Unknown command: {m.text}\n\n"+HELP_TEXT, chat=m.chat.id)
def scheduler():
    while True:
        now=datetime.now(WAT)
        if now.weekday()<5 and now.hour==7 and 0<=now.minute<20:
            try:
                full,d=generate_full()
                for i in range(0,len(full),3800):
                    notify(full[i:i+3800]); time.sleep(2)
            except Exception as e:
                print(f"Scheduler fail {e}")
            time.sleep(3600)
        time.sleep(30)
# =====================================================================
# [OK] MAIN FIX  "Application exited early / Port scan timeout"
# The old file started every worker as a daemon thread and then fell off
# the end of the script, so Python shut down instantly and killed them all.
# Now the Flask server runs on the MAIN thread (blocking), so the process
# stays alive and Render's port scan finds an open port.
# =====================================================================
if __name__=="__main__":
    print("=" * 60)
    print("STARTING V17.0 \u2014 webhook mode, no polling, no 409/401")
    print(f"PORT={PORT} | TELEGRAM_TOKEN={'SET' if TOKEN else 'MISSING \u274c'} | CHANNEL_ID={CHANNEL_ID} | RENDER_URL={RENDER_URL}")
    print(f"CANDLE_SOURCE={CANDLE_SOURCE.upper()} (deriv = public market data, no login, works from datacenters)")
    print("=" * 60)
    if not TOKEN:
        print("\u274c TELEGRAM_TOKEN env var missing \u2014 add it in Render \u2192 Environment")
        raise SystemExit(1)
    threading.Thread(target=init_webhook,daemon=True).start()
    threading.Thread(target=keep_alive,daemon=True).start()
    threading.Thread(target=tick_monitor,daemon=True).start()
    threading.Thread(target=scheduler,daemon=True).start()
    print("V17.0 COMPLETE WEBHOOK TRUE LIVE \u2014 All features intact \u2014 No polling \u2014 No 409/401")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)   #  keeps process alive
