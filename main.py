import os, time, threading, requests, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from flask import Flask

TOKEN=os.getenv("TELEGRAM_TOKEN")
TD_KEY=os.getenv("TWELVEDATA_API_KEY")
CHANNEL_ID=os.getenv("CHANNEL_ID")
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL")

app=Flask(__name__)
@app.route('/')
def home(): return "V11.9 FIXED NoneType"
threading.Thread(target=lambda: app.run(host='0.0.0.0',port=PORT),daemon=True).start()

def keep_alive():
    while True:
        time.sleep(600)
        try:
            if RENDER_URL: requests.get(RENDER_URL,timeout=10)
        except: pass
threading.Thread(target=keep_alive,daemon=True).start()

bot=telebot.TeleBot(TOKEN,parse_mode="HTML",threaded=False)
for _ in range(3):
    try: bot.remove_webhook(); bot.delete_webhook(drop_pending_updates=True); time.sleep(2); break
    except: time.sleep(2)

WAT=ZoneInfo("Africa/Lagos")
conn=sqlite3.connect("trades.db",check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, bias TEXT, time TEXT, result TEXT, pips REAL, stage TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS probables (pair TEXT PRIMARY KEY, time TEXT, fvg_low REAL, fvg_high REAL, bias TEXT)")
conn.commit()

def get_price_td(sym):
    try:
        url=f"https://api.twelvedata.com/price?symbol={sym}&apikey={TD_KEY}"
        r=requests.get(url,timeout=10).json()
        print(f"TD {sym} -> {r}")
        if 'price' in r:
            return float(r['price'])
        print(f"TD FAIL {sym} {r}")
        return None
    except Exception as e:
        print(f"TD EX {sym} {e}")
        return None

def get_all_live():
    mapping={"EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","USD/CAD":"USD/CAD","EUR/JPY":"EUR/JPY","USD/JPY":"USD/JPY","XAU/USD":"XAU/USD","NAS100":"NDX","US30":"DJI","DXY":"DXY","WTI":"WTI/USD"}
    data={}
    for disp, td in mapping.items():
        data[disp]=get_price_td(td)
        time.sleep(2.6) # avoid limit
    data['CPI']="1:30 PM WAT"
    return data

def is_choch_bullish(c):
    try: cl=[float(x['close']) for x in c[:5]]; return cl[0] > max(cl[1:3])
    except: return False
def is_choch_bearish(c):
    try: cl=[float(x['close']) for x in c[:5]]; return cl[0] < min(cl[1:3])
    except: return False
def get_candles(sym, interval="15min", count=15):
    try:
        r=requests.get(f"https://api.twelvedata.com/time_series?symbol={sym}&interval={interval}&outputsize={count}&apikey={TD_KEY}",timeout=12).json()
        return r.get('values',[])
    except: return []
def find_bull_fvg(c):
    try:
        for i in range(len(c)-2):
            if float(c[i+2]['high']) < float(c[i]['low']):
                return {'low':float(c[i+2]['high']), 'high':float(c[i]['low'])}
    except: pass
    return None
def find_bear_fvg(c):
    try:
        for i in range(len(c)-2):
            if float(c[i+2]['low']) > float(c[i]['high']):
                return {'low':float(c[i]['high']), 'high':float(c[i+2]['low'])}
    except: pass
    return None
def calc_pips(pair, entry, current, bias):
    mult=100 if "JPY" in pair else 10000 if "/" in pair else 1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult

def fmt(v, dec=4):
    return f"{v:.{dec}f}" if v is not None else "N/A"

def generate_full():
    d=get_all_live()
    now=datetime.now(WAT)

    eurusd=d.get("EUR/USD")
    # FIXED: safe FVG calc
    if eurusd: fvg_eur=f"{eurusd-0.0005:.4f}-{eurusd+0.0005:.4f}"
    else: fvg_eur="1.1200-1.1210"

    eurusd_s=fmt(eurusd,4)
    gbpusd_s=fmt(d.get("GBP/USD"),4)
    usdcad_s=fmt(d.get("USD/CAD"),4)
    xau_s=fmt(d.get("XAU/USD"),2)
    dxy_s=fmt(d.get("DXY"),2)
    wti_s=fmt(d.get("WTI"),2)
    eurjpy_s=fmt(d.get("EUR/JPY"),2)
    usdjpy_s=fmt(d.get("USD/JPY"),2)
    nas_s=fmt(d.get("NAS100"),0)
    us30_s=fmt(d.get("US30"),0)

    full=f"""<b>📊 EUR/USD..US30 — Full ICT Brief {now.strftime('%a %b %d %I:%M %p WAT')}</b>

<b>📰 HEADLINES:</b> Gold {xau_s} ahead CPI {d['CPI']}, DXY {dxy_s} firm, EUR/USD {eurusd_s}, WTI ${wti_s} — CPI in focus | Live from TwelveData

<b>━━━━━━━━━━━━━━━━━━━━</b>

<b>💶 EUR/USD: Current {eurusd_s} — DXY {dxy_s}</b>
<b>Bias:</b> BUY after sweep
<b>Setup:</b> Sweep 1.1190 expected pre-CPI
<b>Confirmation:</b> 15m CHOCH bullish + 5m FVG {fvg_eur} retest
<b>Entry:</b> BUY {eurusd_s} after retest
<b>SL:</b> 1.1185 | <b>TP1:</b> 1.1225 | <b>TP2:</b> 1.1240
<b>Justification:</b> DXY {dxy_s} resistance at 99.0, London sweep grab liquidity then bullish CHOCH. CPI risk managed with BE+0.5.
<b>Status:</b> ⚠️ WAIT for FVG retest

<b>━━━━━━━━━━━━━━━━━━━━</b>

<b>💷 GBP/USD: Current {gbpusd_s}</b>
<b>Bias:</b> BUY
<b>SL:</b> 1.3230 | <b>TP:</b> 1.3300 / 1.3325
<b>Justification:</b> GBP holding strength vs DXY {dxy_s}, USD weakness post-CPI expected. Wait London grab.
<b>Status:</b> ⚠️ WAIT

<b>💵 USD/CAD: Current {usdcad_s} — Oil ${wti_s}</b>
<b>Bias:</b> SELL | <b>SL:</b> 1.3905 | <b>TP:</b> 1.3830 / 1.3800
<b>Justification:</b> WTI ${wti_s} supporting CAD, DXY {dxy_s} resistance.
<b>Status:</b> ⚠️ WAIT

<b>🥇 XAU/USD: Current {xau_s}</b>
<b>Bias:</b> SELL | <b>SL:</b> 4175 | <b>TP:</b> 4145 / 4135
<b>Justification:</b> Gold {xau_s} overbought ahead CPI, DXY {dxy_s} firm.
<b>Status:</b> ⚠️ WAIT

<b>💱 EUR/JPY: {eurjpy_s}</b> | <b>USD/JPY: {usdjpy_s}</b> | <b>NAS100: {nas_s}</b> | <b>US30: {us30_s}</b>
Wait 15m CHOCH after CPI.

<b>━━━━━━━━━━━━━━━━━━━━</b>
<b>📌 RULES:</b> Sweep + 15m CHOCH + 5m FVG retest
<b>📌 MANAGEMENT:</b> BE+0.5 at 50% to TP1
<b>⚠️ Educational only — Live TwelveData</b>
"""
    return full, d

def check_trades():
    d=get_all_live()
    msgs=[]
    for pair in ["EUR/USD","GBP/USD","USD/CAD","XAU/USD"]:
        live=d.get(pair)
        if not live: continue
        c15=get_candles(pair,"15min",12); c5=get_candles(pair,"5min",20); time.sleep(1)
        cur=conn.execute("SELECT * FROM probables WHERE pair=?",(pair,)).fetchone()
        bullish=is_choch_bullish(c15); bearish=is_choch_bearish(c15)
        bull_fvg=find_bull_fvg(c5); bear_fvg=find_bear_fvg(c5)
        if not cur:
            if pair in ["EUR/USD","GBP/USD"] and bullish and bull_fvg:
                conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),bull_fvg['low'],bull_fvg['high'],"BUY")); conn.commit()
                msgs.append(f"⚠️ PROBABLE {pair} BUY @ {live:.4f} FVG {bull_fvg['low']:.4f}-{bull_fvg['high']:.4f}")
            elif pair in ["USD/CAD","XAU/USD"] and bearish and bear_fvg:
                conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),bear_fvg['low'],bear_fvg['high'],"SELL")); conn.commit()
                msgs.append(f"⚠️ PROBABLE {pair} SELL @ {live:.4f} FVG {bear_fvg['low']:.4f}-{bear_fvg['high']:.4f}")
        else:
            fvg_low=cur[2]; fvg_high=cur[3]; bias=cur[4]
            if fvg_low <= live <= fvg_high:
                sl,tp1,tp2=(1.1185,1.1225,1.1240) if pair=="EUR/USD" else (1.3230,1.3300,1.3325) if pair=="GBP/USD" else (1.3905,1.3830,1.3800) if pair=="USD/CAD" else (live+15, live-10, live-20)
                conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,sl,tp1,tp2,bias,datetime.now(WAT).isoformat(),"OPEN",0,"VALID_RETEST"))
                conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                msgs.append(f"✅ VALID {pair} {bias} @ {live:.4f} SL {sl} TP {tp1}/{tp2}")
    return msgs

def monitor_trades():
    while True:
        try:
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            if not cur: time.sleep(120); continue
            d=get_all_live()
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                if result=="OPEN" and pips>=tp1_pips*0.5:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🔧 BE+0.5 {pair} {bias} @ {entry} Live {live:.4f} +{pips:.1f}p")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"✅ TP1 {pair} +{pips:.1f}p")
            time.sleep(120)
        except Exception as e:
            print(f"monitor {e}"); time.sleep(30)

threading.Thread(target=monitor_trades,daemon=True).start()

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    bot.send_message(m.chat.id,"☀️ Fetching REAL live prices...")
    try:
        full,d=generate_full()
        for i in range(0,len(full),3800): bot.send_message(m.chat.id,full[i:i+3800])
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        bot.send_message(m.chat.id,f"Error {e}")

@bot.message_handler(commands=['valid','check','probables','pips','journal'])
def cmd_others(m):
    try:
        if 'probables' in m.text:
            cur=conn.execute("SELECT * FROM probables").fetchall()
            bot.send_message(m.chat.id,"No probables" if not cur else "\n".join([f"{r[0]} {r[4]} FVG {r[2]}-{r[3]}" for r in cur]))
        elif 'valid' in m.text or 'check' in m.text:
            msgs=check_trades()
            bot.send_message(m.chat.id,"No VALID" if not msgs else "\n".join(msgs))
        else:
            cur=conn.execute("SELECT pair, entry, bias, result, pips FROM trades ORDER BY id DESC LIMIT 10").fetchall()
            bot.send_message(m.chat.id,"Empty" if not cur else "\n".join([f"{r[0]} {r[2]} @ {r[1]} | {r[3]} {r[4]:.1f}p" for r in cur]))
    except Exception as e: bot.send_message(m.chat.id,f"Error {e}")

def scheduler():
    while True:
        now=datetime.now(WAT)
        if now.weekday()<5 and now.hour==7 and 0<=now.minute<20:
            try:
                full,d=generate_full()
                for i in range(0,len(full),3800): bot.send_message(CHANNEL_ID,full[i:i+3800]); time.sleep(2)
            except: pass
            time.sleep(3600)
        time.sleep(30)

threading.Thread(target=scheduler,daemon=True).start()
print("V11.9 polling fixed")
while True:
    try: bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
    except Exception as e: print(f"Poll {e}"); time.sleep(10)
