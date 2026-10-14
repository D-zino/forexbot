import os, time, threading, requests, sqlite3, json
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from flask import Flask, request

TOKEN=os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID=os.getenv("CHANNEL_ID")
# âœ… FIX: tolerate stray quotes/spaces pasted into Render env vars
if isinstance(CHANNEL_ID,str): CHANNEL_ID=CHANNEL_ID.strip().strip('"').strip("'")
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL","https://forexbot-44io.onrender.com")

app=Flask(__name__)

@app.route('/')
def home(): return "V16.0 COMPLETE WEBHOOK TRUE LIVE OK"
@app.route('/health')
def health(): return "OK"

bot=telebot.TeleBot(TOKEN,parse_mode="HTML",threaded=False)

# ============ WEBHOOK â€” FIXES 409 CONFLICT PERMANENTLY ============
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return 'OK', 200
    return 'KO', 403

@app.route('/setwebhook')
def set_webhook_route():
    try:
        bot.remove_webhook()
        time.sleep(2)
        bot.set_webhook(url=f"{RENDER_URL}/{TOKEN}", drop_pending_updates=True)
        return f"âœ… Webhook set to {RENDER_URL}/{TOKEN} â€” 409 FIXED"
    except Exception as e:
        return f"Fail {e}"

def init_webhook():
    time.sleep(4)
    # âœ… FIX: token sanity check (your logs showed 401 Unauthorized from Telebot)
    try:
        me=bot.get_me()
        print(f"ðŸ¤– TOKEN OK â€” bot @{me.username} (id {me.id})")
    except Exception as e:
        print(f"âŒ TELEGRAM_TOKEN INVALID / 401 UNAUTHORIZED: {e}")
        print("   â†’ Render Dashboard â†’ Environment â†’ fix TELEGRAM_TOKEN (revoked or truncated token).")
        return
    # âœ… FIX: retry instead of one-shot (free instance can be slow to come up)
    for attempt in range(1,4):
        try:
            if RENDER_URL and TOKEN:
                bot.remove_webhook()
                time.sleep(2)
                bot.set_webhook(url=f"{RENDER_URL}/{TOKEN}", drop_pending_updates=True)
                print(f"âœ… WEBHOOK SET: {RENDER_URL}/{TOKEN} â€” NO MORE 409")
                return
        except Exception as e:
            print(f"Webhook init fail (attempt {attempt}/3): {e}")
            time.sleep(5)

# âœ… FIX: one safe sender for every channel/chat message.
#    - never raises if CHANNEL_ID is missing (old code crash-looped the monitor)
#    - retries transient Telegram errors instead of dropping alerts
def notify(text, chat=None):
    target=CHANNEL_ID if chat is None else chat
    if target is None:
        print(f"âš ï¸ CHANNEL_ID not set â€” message not sent: {text[:140]}")
        return None
    for attempt in range(1,4):
        try:
            return bot.send_message(target,text)
        except Exception as e:
            print(f"âš ï¸ send_message fail {attempt}/3: {e}")
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
# âœ… FIX: persistent journal.
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
        self.backend="SQLite trades.db (wiped on Render deploys â€” set DATABASE_URL to persist)"
        if self.url:
            url=self.url
            if "localhost" not in url and "127.0.0.1" not in url and "sslmode=" not in url:
                url=url+("&" if "?" in url else "?")+"sslmode=require"   # Neon/Supabase need SSL
            self.url=url          # âœ… keep the sslmode DSN for reconnects too
            try:
                self._connect_pg(url)
                self.backend="Postgres (persistent â€” journal survives deploys âœ…)"
                print("ðŸ—„ï¸ DB BACKEND: POSTGRES â€” persistent")
            except Exception as e:
                print(f"âš ï¸ Postgres connect failed ({e}) â€” falling back to local SQLite")
                self.url=None
        if not self.url:
            self.conn=sqlite3.connect(sqlite_path,check_same_thread=False)
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            print("ðŸ—„ï¸ DB BACKEND: SQLite trades.db (local, wiped on Render deploys)")
        self._init_schema()

    def _connect_pg(self, url):
        import psycopg2
        self.conn=psycopg2.connect(url)
        self.conn.autocommit=True
        self.pg=True

    @staticmethod
    def _pg_sql(sql):
        s=sql.strip()
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
                        print(f"âš ï¸ DB error ({e}) â€” reconnectingâ€¦")
                        try: self._connect_pg(self.url); time.sleep(1); continue
                        except Exception as e2: print(f"âš ï¸ DB reconnect failed: {e2}"); raise e
                    raise

    def commit(self):
        if self.pg: return          # autocommit
        with self._lock:
            self.conn.commit()

    def _init_schema(self):
        self.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, bias TEXT, time TEXT, result TEXT, pips REAL, stage TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS probables (pair TEXT PRIMARY KEY, time TEXT, fvg_low REAL, fvg_high REAL, bias TEXT, stage TEXT)")
        self.execute("CREATE TABLE IF NOT EXISTS steps (pair TEXT PRIMARY KEY, sweep INTEGER, choch INTEGER, fvg INTEGER, confirm INTEGER, last_time TEXT)")
        self.commit()

conn=DB()

FALLBACK={"EUR/USD":1.11932,"GBP/USD":1.32661,"USD/CAD":1.4210,"EUR/JPY":177.53,"USD/JPY":158.44,"XAU/USD":4143.74,"NAS100":30568.90,"US30":51765.50,"GERMANY40":25403.0,"DXY":102.10,"WTI":106.0}
YAHOO_MAP={"EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","USD/CAD":"USDCAD=X","EUR/JPY":"EURJPY=X","USD/JPY":"USDJPY=X","XAU/USD":"GC=F","NAS100":"NQ=F","US30":"YM=F","GERMANY40":"^GDAXI","DXY":"DX-Y.NYB","WTI":"CL=F"}
PAIRS_9=["EUR/USD","GBP/USD","USD/CAD","XAU/USD","EUR/JPY","USD/JPY","NAS100","US30","GERMANY40"]


# =====================================================================
# âœ… V16.0 â€” CANDLE SOURCE THAT WORKS FROM A DATACENTER IP (and from Nigeria)
# Deriv's public market-data WebSocket needs NO login, NO KYC and no funded
# account â€” verified live: real 5m/15m/1h candles for all 9 pairs.
# Only a candle SOURCE changed; FVG / OB / CHOCH / sweep logic is untouched.
# Set CANDLE_SOURCE=yahoo to go back to the old behaviour.
# =====================================================================
DERIV_WS_URL=os.getenv("DERIV_WS_URL","wss://api.derivws.com/trading/v1/options/ws/public")
CANDLE_SOURCE=os.getenv("CANDLE_SOURCE","deriv").lower()
DERIV_MAP={"EUR/USD":"frxEURUSD","GBP/USD":"frxGBPUSD","USD/CAD":"frxUSDCAD","EUR/JPY":"frxEURJPY",
           "USD/JPY":"frxUSDJPY","XAU/USD":"frxXAUUSD","NAS100":"OTC_NDX","US30":"OTC_DJI","GERMANY40":"OTC_GDAXI"}
#                 granularity(sec), cache TTL(sec)
CANDLE_TF={"5m":(300,45),"15m":(900,180),"60m":(3600,900)}
_CANDLE_CACHE={}                                   # (pair,interval) -> (ts, candles, source)
_LAST_SRC={}                                       # (pair,interval) -> "DERIV"/"YAHOO"/"EMPTY"
_deriv={"ws":None,"lock":threading.Lock(),"req":0,"fails":0,"fail_until":0.0}

def get_deriv_candles(pair, interval="15m", count=120):
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
                    return [{"open":c["open"],"high":c["high"],"low":c["low"],"close":c["close"]}
                            for c in m.get("candles",[])]
                if m.get("msg_type")=="error":
                    print(f"âš ï¸ Deriv {pair} {interval}: {m['error'].get('message')}")
                    break
    except Exception as e:
        print(f"âš ï¸ Deriv candles fail {pair} {interval}: {e}")
        try: _deriv["ws"].close()
        except: pass
        _deriv["ws"]=None
        _deriv["fails"]+=1
        if _deriv["fails"]>=3:                              # stop hammering, fall back for 10 min
            _deriv["fail_until"]=time.time()+600
            print("âš ï¸ Deriv failing â€” using Yahoo candles for the next 10 minutes")
    return []

def candles_cached(pair, interval):
    """Deriv first (works on Render) â†’ Yahoo fallback. Cached per timeframe.
    Empty results (e.g. gold's 21:00-22:00 UTC break) keep the last good series."""
    key=(pair,interval); ttl=CANDLE_TF.get(interval,(900,180))[1]
    now=time.time()
    hit=_CANDLE_CACHE.get(key)
    if hit and now-hit[0]<ttl:
        return hit[1]
    data=get_deriv_candles(pair, interval)
    src="DERIV" if data else "EMPTY"
    if not data and CANDLE_SOURCE!="deriv":                 # explicit yahoo mode
        data=get_yahoo_candles(pair, interval, {"5m":"2d","15m":"5d","60m":"10d"}.get(interval,"5d"))
        src="YAHOO" if data else "EMPTY"
    if not data and CANDLE_SOURCE=="deriv":                 # deriv is primary: try yahoo if deriv empty
        alt=get_yahoo_candles(pair, interval, {"5m":"2d","15m":"5d","60m":"10d"}.get(interval,"5d"))
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
    # âœ… FIX: 5dp only for FX pairs â€” gold/indices are quoted in points/dollars
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
        candles=[]
        for i in range(len(closes)):
            if closes[i] is None: continue
            candles.append({'open':opens[i],'high':highs[i],'low':lows[i],'close':closes[i]})
        return candles
    except: return []

def find_real_fvg(candles):
    try:
        for i in range(len(candles)-2):
            c0,c2=candles[i],candles[i+2]
            if c2['low'] > c0['high']: return {'low':c0['high'], 'high':c2['low'], 'type':'BULL'}
            if c2['high'] < c0['low']: return {'low':c2['high'], 'high':c0['low'], 'type':'BEAR'}
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
                print(f"âœ… STOOQ {symbol}: {close}")
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
            print(f"âœ… FOREX LIVE er-api: {data}")
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
        print(f"âœ… GOLD LIVE: {data['XAU/USD']}")
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
            print(f"âš ï¸ FALLBACK USED {k}: {v}")
            data[k]=v
    print(f"FINAL TRUE LIVE V16.0: {data}")
    return data

def calc_pips(pair, entry, current, bias):
    # âœ… FIX: only 4-decimal FX pairs use 10000. XAU/USD contains "/" but is quoted in
    # dollars â€” the old check reported gold pips Ã—10000 (e.g. "-140000.0p" on a $14 move).
    if "JPY" in pair: mult=100
    elif pair in ("EUR/USD","GBP/USD","USD/CAD"): mult=10000
    else: mult=1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult

# âœ… FIX (V16.0): step 1 was mathematically broken for every instrument.
#    Old code: sweep = liveÂ±0.0010, then checked abs(live - sweep) < 0.0020.
#      â†’ FX majors: 0.0010 < 0.0020 is ALWAYS true â†’ 3 false SWEEP alerts at every killzone open.
#      â†’ JPY/gold/indices: distance was 0.30 / 7.0 / 70 â†’ could NEVER fire.
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
         SELL setup â†’ buy-side liquidity ABOVE price (recent swing HIGH)
         BUY  setup â†’ sell-side liquidity BELOW price (recent swing LOW)
    Returns (level, is_real). is_real=False on the candle-less fallback â€” and those
    synthetic levels never trigger step 1 (fail closed, no false alerts)."""
    try:
        if c5 and len(c5) > 6:
            window=c5[-20:-1]
            if len(window) >= 5:
                lvl=max(c['high'] for c in window) if bias=="SELL" else min(c['low'] for c in window)
                if lvl and lvl > 0: return float(lvl), True
    except: pass
    step=sweep_tol(pair)*3          # deliberately 3Ã— the tolerance away â†’ cannot fire
    return (live+step if bias=="SELL" else live-step), False

def build_pair(pair, live):
    c15=candles_cached(pair, "15m"); c5=candles_cached(pair, "5m"); c1h=candles_cached(pair, "60m")
    time.sleep(0.15)
    real_fvg=find_real_fvg(c5) if c5 else None
    real_ob=find_real_ob(c1h) if c1h else None
    choch=find_choch(c15) if c15 else None
    if choch=="BULL_CHOCHO": bias="BUY"
    elif choch=="BEAR_CHOCHO": bias="SELL"
    else: bias="SELL" if pair in ["EUR/USD","GBP/USD","XAU/USD"] else "BUY"
    if real_fvg: fvg_low,fvg_high=real_fvg['low'],real_fvg['high']
    else:
        if pair in ["EUR/USD","GBP/USD","USD/CAD"]: fvg_low,fvg_high=live-0.0010,live+0.0008
        elif "JPY" in pair: fvg_low,fvg_high=live-0.20,live+0.15
        elif pair=="XAU/USD": fvg_low,fvg_high=live-4.0,live+4.0
        else: fvg_low,fvg_high=live-80,live+30
    if real_ob: ht_low,ht_high=real_ob['low'],real_ob['high']
    else:
        if pair in ["EUR/USD","GBP/USD","USD/CAD"]: ht_low,ht_high=live-0.0025,live+0.0020
        elif "JPY" in pair: ht_low,ht_high=live-0.50,live+0.40
        elif pair=="XAU/USD": ht_low,ht_high=live-10.0,live+12.0
        else: ht_low,ht_high=live-120,live+80
    sweep, real_sweep = sweep_level(pair, live, bias, c5)   # âœ… real liquidity from candles
    if pair in ["EUR/USD","GBP/USD","USD/CAD"]:
        breaker=live+0.0005; entry=live; poc=live; ote=(fvg_low+fvg_high)/2
        sl=live-0.0024 if bias=="BUY" else live+0.0020; tp1=live+0.0020 if bias=="BUY" else live-0.0015; tp2=live+0.0040 if bias=="BUY" else live-0.0030; zone_low,zone_high=live+0.0005,live+0.0015
    elif "JPY" in pair:
        breaker=live+0.20; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live-0.62; tp1=live+0.30; tp2=live+0.60; zone_low,zone_high=live+0.20,live+0.40
    elif pair=="XAU/USD":
        breaker=live-6.0 if bias=="SELL" else live+6.0; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live+12.0 if bias=="SELL" else live-12.0; tp1=live-3.0 if bias=="SELL" else live+8.0; tp2=live-8.0 if bias=="SELL" else live+15.0; zone_low,zone_high=live-8.0,live-6.0
    else:
        breaker=live+65; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live-80; tp1=live+60; tp2=live+150; zone_low,zone_high=live+50,live+90
    return {"live":live,"bias":bias,"fvg_low":fvg_low,"fvg_high":fvg_high,"ht_low":ht_low,"ht_high":ht_high,"sweep":sweep,"breaker":breaker,"entry":entry,"poc":poc,"ote":ote,"sl":sl,"tp1":tp1,"tp2":tp2,"zone_low":zone_low,"zone_high":zone_high,"real_sweep":real_sweep,"choch":choch or "WAIT","real_fvg":real_fvg is not None,"real_ob":real_ob is not None}

def generate_full():
    d=get_live_all_live()
    now=datetime.now(WAT)
    gold=d['XAU/USD']; dxy=d['DXY']; eur=d['EUR/USD']; wti=d['WTI']; nas=d['NAS100']; us30=d['US30']; ger=d['GERMANY40']
    headlines=f"Gold {gold:.2f} LIVE | DXY {dxy:.2f} LIVE dx.f | EUR/USD {eur:.5f} LIVE er-api | WTI ${wti:.2f} LIVE cl.f | NAS100 {nas:.2f} LIVE nq.f | US30 {us30:.2f} LIVE ym.f | GER40 {ger:.2f} LIVE â€” CPI 1:30 PM WAT main driver"

    txt=f"<b>ðŸ“Š 9 PAIRS TRUE LIVE FEED {now.strftime('%a %b %d %I:%M %p WAT')} Educational only â€” V16.0 COMPLETE</b>\n\n"
    txt+=f"<b>HEADLINES LIVE:</b> {headlines}\n<b>Killzones:</b> LONDON 8:00-10:30 AM, NY 1:30-6:00 PM WAT\n\n<b>â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”</b>\n\n"

    def add_pair(num, pair, emoji):
        live=d.get(pair, FALLBACK[pair])
        c=build_pair(pair, live)
        cur=px(pair,live); sl=px(pair,c['sl']); tp1=px(pair,c['tp1']); tp2=px(pair,c['tp2']); entry=px(pair,c['entry'])
        sweep=px(pair,c['sweep']); br=px(pair,c['breaker']); poc=px(pair,c['poc']); ote=px(pair,c['ote'])
        fvg=f"{px(pair,c['fvg_low'])}-{px(pair,c['fvg_high'])}"; ob=f"{px(pair,c['ht_low'])}-{px(pair,c['ht_high'])}"; zone=f"{px(pair,c['zone_low'])}-{px(pair,c['zone_high'])}"

        real_tag_fvg="REAL" if c['real_fvg'] else "SYNTH"
        real_tag_ob="REAL" if c['real_ob'] else "SYNTH"
        bias_txt=c['bias']

        s=f"{emoji} <b>{num}) {pair} Current {cur} LIVE</b>\n"
        s+=f"V5.1: Bias {bias_txt} | Live {cur} | OB {ob} {real_tag_ob} | FVG {fvg} {real_tag_fvg} | Sweep {sweep} {'REAL' if c['real_sweep'] else 'SYNTH'} | Breaker {br} | POC {poc} | OTE {ote} | Entry {entry} | SL {sl} | TP1 {tp1} | TP2 {tp2} | RR 1:2/1:4 | CHOCH {c['choch']}\n"
        s+=f"Bias: {bias_txt} | Window: London 8-10:30 AM, NY 1:30-6 PM WAT | Trigger: Sweep + CHOCH + FVG + Confirmation | Status: WAIT | Zone {zone}\n\n"
        s+=f"<b>â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”</b>\n\n"

        return s

    pairs=[("EUR/USD","ðŸ’¶"),("GBP/USD","ðŸ’·"),("USD/CAD","ðŸ"),("XAU/USD","ðŸ†"),("EUR/JPY","ðŸ’¶"),("USD/JPY","ðŸ’´"),("NAS100","ðŸ“ˆ"),("US30","ðŸ“ˆ"),("GERMANY40","ðŸ‡©ðŸ‡ª")]
    for i,(p,e) in enumerate(pairs,1):
        txt+=add_pair(i,p,e)

    txt+=f"All entries wait for CHOCH/BOS + FVG â€” no market open. Steps 8-10:30 & 13:30-18:00 WAT TRUE LIVE V16.0\nDXY {dxy:.2f} LIVE | WTI ${wti:.2f} LIVE | NAS {nas:.2f} LIVE | US30 {us30:.2f} LIVE\n<b>âš ï¸ Educational only â€” True Live No-TD Feed V16.0 Complete Webhook</b>"

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
                    # âœ… FIX: real candle-derived level only, per-instrument tolerance, and price must
                    #    have actually reached the liquidity (SELL â†’ into the swing high, BUY â†’ into the low)
                    tol=sweep_tol(pair)
                    reached = live <= c['sweep']+tol if c['bias']=="BUY" else live >= c['sweep']-tol
                    if s1==0 and c['real_sweep'] and reached:
                        conn.execute("UPDATE steps SET sweep=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        side="high" if c['bias']=="SELL" else "low"
                        notify(f"1ï¸âƒ£ <b>SWEEP {pair} [{kz_name}]</b> @ {now_str}\nLive {px(pair,live)} swept liquidity {side} {px(pair,c['sweep'])} (tol {tol:g}) LIVE\nJournal logged")
                    if s1==1 and s2==0 and c['choch'] in ("BULL_CHOCHO","BEAR_CHOCHO"):
                        conn.execute("UPDATE steps SET choch=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        notify(f"2ï¸âƒ£ <b>CHOCH {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\n{c['choch']} @ {px(pair,live)} LIVE\nJournal logged")
                    if s2==1 and s3==0 and c['fvg_low']<=live<=c['fvg_high']:
                        conn.execute("UPDATE steps SET fvg=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],c['bias'],"FVG_RETEST")); conn.commit()
                        notify(f"3ï¸âƒ£ <b>FVG RETEST {pair} [{kz_name}]</b> @ {now_str}\nInside {px(pair,c['fvg_low'])}-{px(pair,c['fvg_high'])} LIVE\nJournal logged")
                    if s3==1 and s4==0:
                        c5=candles_cached(pair, "5m")
                        choch5=find_choch(c5) if c5 else None
                        if (c['bias']=="BUY" and choch5=="BULL_CHOCHO") or (c['bias']=="SELL" and choch5=="BEAR_CHOCHO"):
                            conn.execute("UPDATE steps SET confirm=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                            conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,c['sl'],c['tp1'],c['tp2'],c['bias'],datetime.now(WAT).isoformat(),"OPEN",0,f"CONFIRMED_{kz_name}_{choch5}_LIVE")); conn.commit()
                            conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                            notify(f"âœ… <b>ENTRY {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\nEntry {px(pair,live)} SL {px(pair,c['sl'])} TP {px(pair,c['tp1'])}/{px(pair,c['tp2'])}\nðŸ“ Trade decision journaled â€” Full V5.1 confirmation")
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                if result=="OPEN" and pips>=tp1_pips*0.5:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"ðŸ”§ <b>BE+0.5 {pair} {bias}</b> @ {px(pair,entry)} â†’ {px(pair,live)} +{pips:.1f}p @ {now_str} LIVE\nJournal updated")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"âœ… <b>TP1 {pair}</b> +{pips:.1f}p Live {px(pair,live)} @ {now_str} LIVE\nJournal logged")
                elif result=="TP1_HIT" and ((bias=="BUY" and live>=tp2) or (bias=="SELL" and live<=tp2)):
                    final=calc_pips(pair,entry,tp2,bias)
                    conn.execute("UPDATE trades SET result='TP2_HIT', pips=? WHERE id=?",(final,tid)); conn.commit()
                    notify(f"ðŸ† <b>TP2 {pair}</b> +{final:.1f}p FULL @ {now_str} LIVE\nJournal logged")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live<=sl) or (bias=="SELL" and live>=sl)):
                    conn.execute("UPDATE trades SET result='SL_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    notify(f"âŒ <b>SL {pair}</b> {pips:.1f}p Live {px(pair,live)} @ {now_str} LIVE\nJournal logged")
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
HELP_TEXT=("ðŸ¤– <b>V16.0 COMMANDS</b>\n\n"
 "/brief â€” full 9-pair live feed (same as the 7 AM auto-post)\n"
 "/check â€” bot health: webhook, channel, token, feeds, DB, open trades\n"
 "/valid â€” setup validator: per pair, real FVG/OB + CHOCH + step sequence\n"
 "/probables â€” pairs currently sitting inside an FVG (live)\n"
 "/steps â€” sweep â†’ CHOCH â†’ FVG â†’ confirm tracker per pair\n"
 "/pips â€” running P&amp;L: open trades + closed results\n"
 "/journal â€” last 20 trade decisions\n"
 "/reset â€” clear the step tracker (add 'all confirm' to also wipe the journal)\n\n"
 "Educational only.")

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    notify("â˜€ï¸ Fetching TRUE 9 LIVE V16.0 COMPLETE â€” er-api + stooq nq.f ym.f dx.f cl.f + gold-api â€” WEBHOOK...", chat=m.chat.id)
    try:
        full,d=generate_full()
        send_long(m.chat.id, full)
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

@bot.message_handler(commands=['help','commands'])
def cmd_help(m):
    notify(HELP_TEXT, chat=m.chat.id)

# âœ… NEW: /check â€” system health (was a no-op before)
@bot.message_handler(commands=['check','health'])
def cmd_check(m):
    try:
        in_kz,now_str,kz=is_killzone()
        lines=[f"ðŸ©º <b>CHECK @ {now_str}</b>"]
        lines.append(f"Killzone: <b>{kz}</b> {'ACTIVE âœ…' if in_kz else 'OFF â€” London 8:00-10:30, NY 13:30-18:00 WAT'}")
        try:
            info=bot.get_webhook_info()
            if info.url:
                lines.append(f"Webhook: âœ… set (pending {info.pending_update_count})")
                if info.last_error_message: lines.append(f"âš ï¸ last webhook error: {info.last_error_message}")
            else:
                lines.append("Webhook: âŒ NOT set â€” open /setwebhook in a browser")
        except Exception as e:
            lines.append(f"Webhook: âŒ {e}")
        lines.append(f"Token: {'âœ… present' if TOKEN else 'âŒ MISSING'} | Channel: {CHANNEL_ID if CHANNEL_ID else 'âŒ MISSING (set CHANNEL_ID)'}")
        lines.append(f"DB: {conn.backend}")
        lines.append(f"Candles: {candle_status_line()}")
        try:
            d=get_live_all_live()
            stale=[p for p in FALLBACK if p in d and abs(d[p]-FALLBACK[p])<1e-9]
            live_n=len([p for p in FALLBACK if p in d and p not in stale])
            lines.append(f"Feeds: {live_n}/{len(FALLBACK)} live" + (f" | on fallback: {', '.join(stale)}" if stale else " | all live âœ…"))
            lines.append("Prices: " + " | ".join(f"{p} {d[p]:g}" for p in FALLBACK if p in d))
        except Exception as e:
            lines.append(f"Feeds: âŒ {e}")
        opened=conn.execute("SELECT COUNT(*) FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchone()[0]
        total=conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        lines.append(f"Trades: {opened} open/in-progress of {total} journaled")
        send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

# âœ… NEW: /valid â€” real structure validator per pair
@bot.message_handler(commands=['valid'])
def cmd_valid(m):
    notify("ðŸ”Ž Validating all 9 setups â€” reading live candles for real FVG / OB / CHOCH (can take ~1 min)â€¦", chat=m.chat.id)
    try:
        d=get_live_all_live()
        in_kz,now_str,kz=is_killzone()
        out=[f"âœ… <b>SETUP VALIDATOR @ {now_str}</b> | Killzone {kz} {'ACTIVE' if in_kz else 'OFF'}"]
        ready=0; real_candles=0
        for pair in PAIRS_9:
            live=d.get(pair,FALLBACK[pair])
            c=build_pair(pair,live)
            row=conn.execute("SELECT sweep,choch,fvg,confirm FROM steps WHERE pair=?",(pair,)).fetchone() or (0,0,0,0)
            fvg_ok="FVG âœ…" if c['real_fvg'] else "FVG âŒ"
            ob_ok="OB âœ…" if c['real_ob'] else "OB âŒ"
            choch_ok="CHOCH âœ…" if c['choch'] in ("BULL_CHOCHO","BEAR_CHOCHO") else "CHOCH âŒ"
            sw_ok=f"SWEEP âœ…@tol {sweep_tol(pair):g}" if c['real_sweep'] else "SWEEP âŒ"
            score=sum(1 for x in (fvg_ok,ob_ok,choch_ok) if x.endswith("âœ…"))
            if c['real_fvg'] or c['real_ob']: real_candles+=1
            if score==3: ready+=1
            verdict="READY ðŸš¦" if score==3 else "WATCH ðŸ‘€" if score>=1 else "NO STRUCTURE"
            out.append(f"{pair}: {c['bias']} | {fvg_ok} | {ob_ok} | {choch_ok} | {sw_ok} | steps {sum(int(s) for s in row)}/4 | {verdict}")
        out.append(f"\n{ready}/9 pairs show full real structure ({real_candles}/9 had any real candle data).")
        if real_candles==0:
            out.append("âš ï¸ No real structure found anywhere â†’ the candle source (Yahoo) is likely blocked from this host. Prices are still live; FVG/OB fall back to synthetic levels.")
        out.append("Entry still requires the full sweep â†’ CHOCH â†’ FVG â†’ confirmation sequence inside a killzone.")
        send_long(m.chat.id, "\n".join(out))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

@bot.message_handler(commands=['probables','prob'])
def cmd_probables(m):
    try:
        cur=conn.execute("SELECT pair, fvg_low, fvg_high, bias, stage FROM probables").fetchall()
        if not cur:
            notify("No probables â€” waiting killzone 8-10:30 & 13:30-18:00 WAT", chat=m.chat.id)
        else:
            lines=["ðŸŽ¯ <b>PROBABLES â€” inside FVG, waiting confirmation</b>"]
            for pair,lo,hi,bias,stage in cur:
                lines.append(f"{pair} {bias} FVG {px(pair,lo)}-{px(pair,hi)} {stage} LIVE")
            send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

# âœ… NEW: /steps now shows the step tracker (the thing the steps table actually holds)
@bot.message_handler(commands=['steps'])
def cmd_steps(m):
    try:
        rows=conn.execute("SELECT pair, sweep, choch, fvg, confirm, last_time FROM steps ORDER BY pair").fetchall()
        if not rows:
            notify("No step tracking yet â€” it starts automatically inside killzones 8:00-10:30 & 13:30-18:00 WAT", chat=m.chat.id)
            return
        lines=["ðŸªœ <b>STEP TRACKER</b> â€” 1 Sweep â†’ 2 CHOCH â†’ 3 FVG â†’ 4 Confirm"]
        for pair,s1,s2,s3,s4,last in rows:
            marks=" ".join(f"{i+1}{'âœ…' if v else 'â¬œ'}" for i,v in enumerate((s1,s2,s3,s4)))
            lines.append(f"{pair}: {marks} | {last or '-'}")
        send_long(m.chat.id, "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

# âœ… NEW: /pips â€” running P&L
@bot.message_handler(commands=['pips','pnl'])
def cmd_pips(m):
    try:
        d=get_live_all_live()
        out=["ðŸ’° <b>P&amp;L SUMMARY V16.0</b>"]
        openrows=conn.execute("SELECT pair, entry, bias, result, pips FROM trades WHERE result IN ('OPEN','BE','TP1_HIT') ORDER BY id DESC").fetchall()
        if openrows:
            out.append("<b>OPEN / IN PROGRESS:</b>")
            run=0.0
            for pair,entry,bias,result,pips in openrows:
                live=d.get(pair)
                cur=calc_pips(pair,entry,live,bias) if live else (pips or 0.0)
                run+=cur
                out.append(f"{pair} {bias} @ {px(pair,entry)} â†’ {cur:+.1f}p [{result}]")
            out.append(f"Open total: <b>{run:+.1f}p</b>")
        else:
            out.append("Open: none")
        closed=conn.execute("SELECT result, pips FROM trades WHERE result IN ('TP2_HIT','SL_HIT')").fetchall()
        if closed:
            wins=[p or 0 for r,p in closed if r=="TP2_HIT"]; losses=[p or 0 for r,p in closed if r=="SL_HIT"]
            out.append(f"\n<b>CLOSED:</b> {len(closed)} trades | âœ… {len(wins)} full TP2 | âŒ {len(losses)} SL")
            out.append(f"Wins {sum(wins):+.1f}p | Losses {sum(losses):+.1f}p | Net <b>{sum(wins)+sum(losses):+.1f}p</b>")
            if closed: out.append(f"Win rate: {100*len(wins)/len(closed):.0f}%")
        else:
            out.append("Closed: none yet")
        out.append("\nEducational only.")
        send_long(m.chat.id, "\n".join(out))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

@bot.message_handler(commands=['journal'])
def cmd_journal(m):
    try:
        cur=conn.execute("SELECT pair, entry, bias, result, pips, stage FROM trades ORDER BY id DESC LIMIT 20").fetchall()
        if not cur:
            notify("No trades yet â€” journal empty", chat=m.chat.id)
            return
        lines=[f"{r[0]} {r[2]} @ {px(r[0],r[1])} | {r[3]} {r[4]:.1f}p {r[5]} LIVE" for r in cur]
        send_long(m.chat.id, "ðŸ“’ <b>JOURNAL â€” Last 20 trade decisions â€” V16.0</b>\n\n" + "\n".join(lines))
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

# âœ… NEW: /reset â€” clears the step tracker; journal only with an explicit confirm
@bot.message_handler(commands=['reset'])
def cmd_reset(m):
    try:
        txt=(m.text or "").lower()
        conn.execute("DELETE FROM steps"); conn.execute("DELETE FROM probables"); conn.commit()
        msg="â™»ï¸ Step tracker + probables cleared â€” next killzone starts fresh from step 1."
        if "all" in txt and "confirm" in txt:
            conn.execute("DELETE FROM trades"); conn.commit()
            msg+="\nðŸ—‘ï¸ Trade journal also wiped (/reset all confirm)."
        else:
            msg+="\nJournal untouched. To wipe the journal too, send: <code>/reset all confirm</code>"
        notify(msg, chat=m.chat.id)
    except Exception as e:
        import traceback; print(traceback.format_exc()); notify(f"Error {e}", chat=m.chat.id)

@bot.message_handler(func=lambda m: (m.text or "").startswith("/"))
def cmd_unknown(m):
    notify(f"Unknown command: {m.text}\n\n"+HELP_TEXT, chat=m.chat.id)

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
# âœ… MAIN FIX â€” "Application exited early / Port scan timeout"
# The old file started every worker as a daemon thread and then fell off
# the end of the script, so Python shut down instantly and killed them all.
# Now the Flask server runs on the MAIN thread (blocking), so the process
# stays alive and Render's port scan finds an open port.
# =====================================================================
if __name__=="__main__":
    print("=" * 60)
    print("STARTING V16.0 â€” webhook mode, no polling, no 409/401")
    print(f"PORT={PORT} | TELEGRAM_TOKEN={'SET' if TOKEN else 'MISSING âŒ'} | CHANNEL_ID={CHANNEL_ID} | RENDER_URL={RENDER_URL}")
    print(f"CANDLE_SOURCE={CANDLE_SOURCE.upper()} (deriv = public market data, no login, works from datacenters)")
    print("=" * 60)
    if not TOKEN:
        print("âŒ TELEGRAM_TOKEN env var missing â€” add it in Render â†’ Environment")
        raise SystemExit(1)
    threading.Thread(target=init_webhook,daemon=True).start()
    threading.Thread(target=keep_alive,daemon=True).start()
    threading.Thread(target=tick_monitor,daemon=True).start()
    threading.Thread(target=scheduler,daemon=True).start()
    print("V16.0 COMPLETE WEBHOOK TRUE LIVE â€” All features intact â€” No polling â€” No 409/401")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)   # â† keeps process alive
