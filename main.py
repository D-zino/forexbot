import os, time, threading, requests, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from flask import Flask

TOKEN=os.getenv("TELEGRAM_TOKEN")
TD_KEY=os.getenv("TWELVEDATA_API_KEY")
CHANNEL_ID=os.getenv("CHANNEL_ID")
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL") # ADD THIS ENV VAR
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))

app=Flask(__name__)
@app.route('/')
def home(): return "V11.3 MONITOR + KEEPALIVE"

threading.Thread(target=lambda: app.run(host='0.0.0.0',port=PORT),daemon=True).start()

# --- KEEP ALIVE SELF PING ---
def keep_alive():
    while True:
        time.sleep(600)
        try:
            if RENDER_URL:
                requests.get(RENDER_URL, timeout=10)
                print(f"Self ping OK {RENDER_URL}")
        except Exception as e:
            print(f"Self ping fail {e}")

threading.Thread(target=keep_alive,daemon=True).start()

bot=telebot.TeleBot(TOKEN,parse_mode="HTML",threaded=False)
try: bot.remove_webhook(); time.sleep(1); bot.delete_webhook(drop_pending_updates=True)
except: pass
WAT=ZoneInfo("Africa/Lagos")
FALLBACK={"EUR/USD":1.1205,"GBP/USD":1.3270,"USD/CAD":1.3865,"EUR/JPY":177.34,"USD/JPY":158.31,"NAS100":27203,"US30":51677,"XAU/USD":4155.52,"DXY":98.8,"WTI":94.70}

conn=sqlite3.connect("trades.db",check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, bias TEXT, time TEXT, result TEXT, pips REAL, stage TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS probables (pair TEXT PRIMARY KEY, time TEXT, fvg_low REAL, fvg_high REAL, bias TEXT)")
conn.commit()

def get_price(sym):
    try:
        r=requests.get(f"https://api.twelvedata.com/price?symbol={sym}&apikey={TD_KEY}",timeout=10).json()
        if 'price' in r: return float(r['price'])
    except: pass
    return None
def get_candles(sym, interval="15min", count=10):
    try:
        r=requests.get(f"https://api.twelvedata.com/time_series?symbol={sym}&interval={interval}&outputsize={count}&apikey={TD_KEY}",timeout=10).json()
        return r.get('values',[])
    except: return []
def get_all():
    mapping={"EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","USD/CAD":"USD/CAD","EUR/JPY":"EUR/JPY","USD/JPY":"USD/JPY","XAU/USD":"XAU/USD","NAS100":"NDX","US30":"DJI","DXY":"DXY","WTI":"WTI/USD"}
    data={}
    for k,td in mapping.items():
        p=get_price(td)
        data[k]=p if p else FALLBACK[k]
        time.sleep(1.3)
    data['CPI']="1:30 PM WAT"
    return data
def is_choch_bullish(candles):
    try:
        closes=[float(c['close']) for c in candles[:5]]
        return closes[0] > max(closes[1:3])
    except: return False
def is_choch_bearish(candles):
    try:
        closes=[float(c['close']) for c in candles[:5]]
        return closes[0] < min(closes[1:3])
    except: return False
def find_bull_fvg(candles):
    try:
        for i in range(len(candles)-2):
            if float(candles[i+2]['high']) < float(candles[i]['low']):
                return {'low':float(candles[i+2]['high']), 'high':float(candles[i]['low'])}
        return None
    except: return None
def find_bear_fvg(candles):
    try:
        for i in range(len(candles)-2):
            if float(candles[i+2]['low']) > float(candles[i]['high']):
                return {'low':float(candles[i]['high']), 'high':float(candles[i+2]['low'])}
        return None
    except: return None
def calc_pips(pair, entry, current, bias):
    mult=100 if "JPY" in pair else 10000 if "/" in pair else 1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult
def generate_full():
    d=get_all()
    now=datetime.now(WAT)
    h=f"Gold {d['XAU/USD']:.0f} ahead CPI {d['CPI']}, DXY {d['DXY']:.1f}, EUR/USD {d['EUR/USD']:.4f}, WTI ${d['WTI']:.2f}"
    body="".join([f"{p}: {d[p]} — Wait sweep+CHOCH+5m FVG retest\n" for p in ["EUR/USD","GBP/USD","USD/CAD","XAU/USD","EUR/JPY","USD/JPY","NAS100","US30"]])
    return f"Full ICT brief {now.strftime('%a %b %d %I:%M %p WAT')}\nHEADLINES {h}\n\n{body}", d
def check_trades():
    d=get_all(); msgs=[]
    for pair in ["EUR/USD","GBP/USD","USD/CAD","XAU/USD"]:
        live=d[pair]
        td_sym={"EUR/USD":"EUR/USD","GBP/USD":"GBP/USD","USD/CAD":"USD/CAD","XAU/USD":"XAU/USD"}[pair]
        c15=get_candles(td_sym,"15min",10); c5=get_candles(td_sym,"5min",20); time.sleep(1.2)
        cur=conn.execute("SELECT * FROM probables WHERE pair=?",(pair,)).fetchone()
        bullish=is_choch_bullish(c15); bearish=is_choch_bearish(c15)
        bull_fvg=find_bull_fvg(c5); bear_fvg=find_bear_fvg(c5)
        if not cur:
            if pair in ["EUR/USD","GBP/USD"] and bullish and bull_fvg:
                conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),bull_fvg['low'],bull_fvg['high'],"BUY")); conn.commit()
                msgs.append(f"⚠️ PROBABLE {pair} BUY @ {live} — waiting FVG {bull_fvg['low']:.4f}-{bull_fvg['high']:.4f} retest")
            elif pair in ["USD/CAD","XAU/USD"] and bearish and bear_fvg:
                conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),bear_fvg['low'],bear_fvg['high'],"SELL")); conn.commit()
                msgs.append(f"⚠️ PROBABLE {pair} SELL @ {live} — waiting FVG retest")
        else:
            fvg_low=cur[2]; fvg_high=cur[3]; bias=cur[4]
            if fvg_low <= live <= fvg_high:
                if pair=="EUR/USD": sl=1.1185; tp1=1.1225; tp2=1.1240
                elif pair=="GBP/USD": sl=1.3230; tp1=1.3300; tp2=1.3325
                elif pair=="USD/CAD": sl=1.3905; tp1=1.3830; tp2=1.3800
                else: sl=4175; tp1=4145; tp2=4135
                conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,sl,tp1,tp2,bias,datetime.now(WAT).isoformat(),"OPEN",0,"VALID_RETEST"))
                conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                msgs.append(f"✅ VALID {pair} {bias} @ {live} — CONFIRMED AFTER RETEST\nSL {sl} TP1 {tp1} TP2 {tp2}")
    return msgs
def monitor_trades():
    while True:
        try:
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            if not cur: time.sleep(120); continue
            d=get_all()
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                be_trigger=tp1_pips*0.5
                if result=="OPEN" and pips>=be_trigger:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🔧 BE+0.5 {pair} {bias} @ {entry} → Live {live} +{pips:.1f}p")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"✅ TP1 HIT {pair} {bias} @ {entry} → {live} +{pips:.1f}p")
                elif result=="TP1_HIT" and ((bias=="BUY" and live>=tp2) or (bias=="SELL" and live<=tp2)):
                    final=calc_pips(pair,entry,tp2,bias)
                    conn.execute("UPDATE trades SET result='TP2_HIT', pips=? WHERE id=?",(final,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🏆 TP2 HIT {pair} {bias} @ {entry} → {tp2} +{final:.1f}p")
                elif (bias=="BUY" and live<=sl) or (bias=="SELL" and live>=sl):
                    if result in ("BE","TP1_HIT"):
                        conn.execute("UPDATE trades SET result='BE_HIT', pips=? WHERE id=?",(2,tid)); conn.commit()
                        bot.send_message(CHANNEL_ID,f"⚠️ BE HIT {pair} +2p")
                    else:
                        loss=calc_pips(pair,entry,sl,bias)
                        conn.execute("UPDATE trades SET result='SL_HIT', pips=? WHERE id=?",(loss,tid)); conn.commit()
                        bot.send_message(CHANNEL_ID,f"❌ SL HIT {pair} {loss:.1f}p")
            time.sleep(120)
        except Exception as e:
            print(f"monitor {e}"); time.sleep(30)

threading.Thread(target=monitor_trades,daemon=True).start()

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    bot.send_message(m.chat.id,"☀️ Fetching...")
    full,d=generate_full()
    for i in range(0,len(full),3800): bot.send_message(m.chat.id,full[i:i+3800])

@bot.message_handler(commands=['valid','check'])
def cmd_valid(m):
    msgs=check_trades()
    if not msgs: bot.send_message(m.chat.id,"No VALID after retest — /probables")
    else:
        for msg in msgs: bot.send_message(m.chat.id,msg)

@bot.message_handler(commands=['probables'])
def cmd_prob(m):
    cur=conn.execute("SELECT * FROM probables").fetchall()
    if not cur: bot.send_message(m.chat.id,"No probables")
    else:
        txt="Probables waiting retest:\n"
        for r in cur: txt+=f"{r[0]} {r[4]} {r[2]}-{r[3]}\n"
        bot.send_message(m.chat.id,txt)

@bot.message_handler(commands=['pips','journal'])
def cmd_journal(m):
    cur=conn.execute("SELECT pair, entry, bias, time, result, pips, stage FROM trades ORDER BY id DESC LIMIT 20").fetchall()
    if not cur: bot.send_message(m.chat.id,"📒 Empty")
    else:
        txt="📒 Journal:\n"
        for r in cur: txt+=f"{r[0]} {r[2]} @ {r[1]} | {r[4]} {r[5]:.1f}p | {r[6]}\n"
        bot.send_message(m.chat.id,txt)

def scheduler():
    while True:
        now=datetime.now(WAT)
        if now.weekday()<5 and now.hour==7 and 0<=now.minute<20:
            try:
                full,d=generate_full()
                for i in range(0,len(full),3800): bot.send_message(CHANNEL_ID,full[i:i+3800]); time.sleep(2)
            except: pass
            time.sleep(3600)
        if now.weekday()<5 and 8<=now.hour<=16 and now.minute%15==0:
            try:
                for msg in check_trades(): bot.send_message(CHANNEL_ID,msg)
            except: pass
            time.sleep(60)
        time.sleep(30)

threading.Thread(target=scheduler,daemon=True).start()
print("V11.3 KEEPALIVE polling")
bot.infinity_polling(skip_pending=True)
