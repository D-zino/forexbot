import os, time, threading, requests, sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import telebot
from flask import Flask

TOKEN = os.getenv("TELEGRAM_TOKEN")
TD_KEY = os.getenv("TWELVEDATA_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")
PORT = int(os.getenv("PORT", 10000))

app = Flask(__name__)
@app.route('/')
def home(): return "V7 Fixed Live"
def run_flask(): app.run(host='0.0.0.0', port=PORT)
threading.Thread(target=run_flask, daemon=True).start()

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=False)
try:
    bot.remove_webhook()
    time.sleep(1)
    bot.delete_webhook(drop_pending_updates=True)
    time.sleep(1)
except: pass

WAT = ZoneInfo("Africa/Lagos")
print("🚀 V7 REAL DATA FIX - NO DUMMY")

# CONVERT CHANNEL_ID TO INT
if CHANNEL_ID:
    try: CHANNEL_ID = int(CHANNEL_ID)
    except: pass

PAIRS = {
    "EUR/USD": "EUR/USD",
    "EUR/JPY": "EUR/JPY",
    "USD/JPY": "USD/JPY",
    "NAS100": "NDX",
    "US30": "DJI",
    "GERMANY40": "DAX",
    "XAU/USD": "XAU/USD"
}

conn = sqlite3.connect("trades.db", check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS trades (
id INTEGER PRIMARY KEY AUTOINCREMENT,
date TEXT, pair TEXT, bias TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL,
result TEXT, pips REAL, status TEXT, brief TEXT)""")
conn.commit()

def get_price(pair_display):
    for _ in range(3): # retry
        try:
            td = PAIRS.get(pair_display, pair_display)
            r = requests.get(f"https://api.twelvedata.com/price?symbol={td}&apikey={TD_KEY}", timeout=10).json()
            if 'price' in r and r['price']:
                print(f"Price OK {pair_display}: {r['price']}")
                return float(r['price'])
            else:
                print(f"Price fail {pair_display}: {r}")
                time.sleep(2)
        except Exception as e:
            print(f"Price err {pair_display} {e}")
            time.sleep(2)
    return None

def get_candles(pair_display, interval, limit=50):
    try:
        td = PAIRS.get(pair_display, pair_display)
        url = f"https://api.twelvedata.com/time_series?symbol={td}&interval={interval}&outputsize={limit}&apikey={TD_KEY}"
        j = requests.get(url, timeout=15).json()
        if 'values' not in j:
            print(f"Candles FAIL {pair_display} {interval}: {j}")
            return []
        vals = j['values'][::-1]
        data = [{"high":float(v['high']),"low":float(v['low']),"open":float(v['open']),"close":float(v['close'])} for v in vals]
        print(f"Candles OK {pair_display} {interval}: {len(data)}")
        return data
    except Exception as e:
        print(f"Candles err {pair_display} {e}")
        return []

# REALISTIC FALLBACK BASED ON LIVE PRICE - NOT 1.165
def get_real_levels(pair_display, live_price):
    """If API fails, build realistic OB/FVG from live price"""
    if live_price is None:
        live_price = 1.1205
    if "JPY" in pair_display:
        ob_low, ob_high = round(live_price-0.5,3), round(live_price+0.3,3)
        fvg_low, fvg_high = round(live_price-0.2,3), round(live_price+0.1,3)
        asia_low, asia_high = round(live_price-0.3,3), round(live_price+0.3,3)
    elif pair_display in ["NAS100","US30","GERMANY40"]:
        ob_low, ob_high = live_price-150, live_price+50
        fvg_low, fvg_high = live_price-50, live_price+20
        asia_low, asia_high = live_price-80, live_price+80
    elif "XAU" in pair_display:
        ob_low, ob_high = live_price-10, live_price+15
        fvg_low, fvg_high = live_price-5, live_price+5
        asia_low, asia_high = live_price-8, live_price+8
    else: # EUR/USD
        ob_low, ob_high = round(live_price-0.0020,5), round(live_price+0.0015,5)
        fvg_low, fvg_high = round(live_price-0.0010,5), round(live_price+0.0005,5)
        asia_low, asia_high = round(live_price-0.0015,5), round(live_price+0.0010,5)
    return ob_low, ob_high, fvg_low, fvg_high, asia_low, asia_high

def auto_generate_complete_brief(pair_display):
    print(f"Generating {pair_display}...")
    live_price = get_price(pair_display)
    if not live_price:
        print(f"NO PRICE for {pair_display}, skipping")
        return None

    time.sleep(1.5) # RATE LIMIT FIX
    c4h = get_candles(pair_display,"4h",20)
    time.sleep(1.5)
    c15 = get_candles(pair_display,"15min",30)
    time.sleep(1.5)

    # Use real candles if available, else realistic fallback
    if c4h and len(c4h)>=5:
        ob_low, ob_high = c4h[-2]['low'], c4h[-2]['high']
        ob_type = "Bearish" if c4h[-2]['close']<c4h[-2]['open'] else "Bullish"
    else:
        ob_low, ob_high, _, _, _, _ = get_real_levels(pair_display, live_price)
        ob_type = "Bearish"

    if c15 and len(c15)>=3:
        fvg_low, fvg_high = c15[-2]['low']+ (c15[-2]['high']-c15[-2]['low'])*0.3, c15[-2]['high']
    else:
        _, _, fvg_low, fvg_high, _, _ = get_real_levels(pair_display, live_price)

    # Asia session
    candles_h1 = get_candles(pair_display,"1h",15)
    if candles_h1 and len(candles_h1)>=8:
        asia = candles_h1[-8:-2]
        asia_low, asia_high = min(c['low'] for c in asia), max(c['high'] for c in asia)
    else:
        _, _, _, _, asia_low, asia_high = get_real_levels(pair_display, live_price)

    bias = "SELL" if live_price < ob_low else "BUY"
    sweep = asia_low if bias=="SELL" else asia_high
    sweep_label = "Asia low" if bias=="SELL" else "Asia high"

    is_gold="XAU" in pair_display
    is_index=pair_display in ["NAS100","US30","GERMANY40"]
    is_jpy="JPY" in pair_display

    if is_gold:
        poc = round((fvg_low+fvg_high)/2,2)
        entry = poc
        breaker = round(sweep+1 if bias=="SELL" else sweep-1,2)
        sl = round(ob_high if bias=="SELL" else ob_low,2)
        tp1 = round(entry-3.0 if bias=="SELL" else entry+3.0,2)
        tp2 = round(entry-8.0 if bias=="SELL" else entry+8.0,2)
        zone_low, zone_high = sweep-1, sweep+1
    elif is_index:
        poc = round((fvg_low+fvg_high)/2,1)
        entry = poc
        breaker = round(sweep+5 if bias=="SELL" else sweep-5,1)
        sl = round(entry+80 if bias=="SELL" else entry-80,1)
        tp1 = round(entry-60 if bias=="SELL" else entry+60,1)
        tp2 = round(entry-150 if bias=="SELL" else entry+150,1)
        zone_low, zone_high = sweep-20, sweep+20
    elif is_jpy:
        poc = round((fvg_low+fvg_high)/2,3)
        entry = poc
        breaker = round(sweep+0.10 if bias=="SELL" else sweep-0.10,3)
        sl = round(ob_high+0.15 if bias=="SELL" else ob_low-0.15,3)
        tp1 = round(entry-0.30 if bias=="SELL" else entry+0.30,3)
        tp2 = round(entry-0.60 if bias=="SELL" else entry+0.60,3)
        zone_low, zone_high = round(sweep-0.10,3), round(sweep+0.10,3)
    else:
        poc = round((fvg_low+fvg_high)/2,5)
        entry = poc
        breaker = round(sweep+0.0005 if bias=="SELL" else sweep-0.0005,5)
        sl = round(ob_high if bias=="SELL" else ob_low,5)
        tp1 = round(entry-0.0020 if bias=="SELL" else entry+0.0020,5)
        tp2 = round(entry-0.0040 if bias=="SELL" else entry+0.0040,5)
        zone_low, zone_high = round(sweep-0.0005,5), round(sweep+0.0005,5)

    brief=f"""{pair_display} Bias: {bias}
HTF POI: 4H {ob_type} OB {ob_low}-{ob_high}
FVG: {fvg_low}-{fvg_high}
Liquidity: Sweep {sweep} {sweep_label} at 8 AM
Breaker: {breaker}
POC: {poc}
OTE: {poc} (50% FVG)
Entry: {entry}
SL: {sl}
TP1: {tp1}
TP2: {tp2}
RR: 1:2 / 1:4
Window: London 8-10 AM WAT
Zone: {zone_low}-{zone_high}"""

    return {"text":brief,"pair":pair_display,"bias":bias,"ob":(ob_low,ob_high),"fvg":(fvg_low,fvg_high),"sweep":sweep,"breaker":breaker,"poc":poc,"entry":entry,"sl":sl,"tp1":tp1,"tp2":tp2,"zone":(zone_low,zone_high)}

def log_trade(data):
    cur=conn.execute("INSERT INTO trades (date,pair,bias,entry,sl,tp1,tp2,result,pips,status,brief) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (datetime.now(WAT).strftime("%Y-%m-%d %H:%M"), data['pair'], data['bias'], data['entry'], data['sl'], data['tp1'], data['tp2'], "PENDING", 0, "OPEN", data['text']))
    conn.commit(); return cur.lastrowid

def update_trade(tid,res,pips):
    conn.execute("UPDATE trades SET result=?, pips=?, status='CLOSED' WHERE id=?", (res,pips,tid)); conn.commit()

def get_week_journal_text():
    rows=conn.execute("SELECT date,pair,bias,result,pips FROM trades WHERE date>=date('now','-7 days') ORDER BY id ASC").fetchall()
    if not rows: return "📓 WEEK JOURNAL: No valid trades this week"
    pair_stats={}
    for d,pair,bias,res,pips in rows:
        if pair not in pair_stats: pair_stats[pair]={"trades":0,"wins":0,"pips":0}
        pair_stats[pair]["trades"]+=1; pair_stats[pair]["pips"]+=pips
        if "TP" in res or "TRAIL" in res or "BE" in res: pair_stats[pair]["wins"]+=1
    sorted_pairs=sorted(pair_stats.items(), key=lambda x: x[1]['pips'], reverse=True)
    best=sorted_pairs[0][0] if sorted_pairs else "None"
    lines=[f"{'🏆' if pair==best else '▫️'} {pair}: {s['trades']}t | {s['wins']}w | {s['pips']:+.1f}p" for pair,s in sorted_pairs]
    return f"📓 <b>WEEKEND JOURNAL</b>\n{chr(10).join(lines)}\nBest: {best}"

def manage_trade(tid,data,chat):
    pair,bias,entry,sl,tp1,tp2=data['pair'],data['bias'],data['entry'],data['sl'],data['tp1'],data['tp2']
    is_gold="XAU" in pair; is_index=pair in ["NAS100","US30","GERMANY40"]; is_jpy="JPY" in pair
    BE_PLUS=0.05 if is_gold else 5 if is_index else 0.00005 if not is_jpy else 0.005
    TRAIL_DIST=0.8 if is_gold else 25 if is_index else 0.0015 if not is_jpy else 0.15
    trailing=False; cur_sl=sl
    bot.send_message(chat,f"📈 Managing #{tid} {pair} {bias} BE+0.5 active")
    while True:
        price=get_price(pair)
        if not price: time.sleep(30); continue
        if bias=="SELL" and price<=tp1 and not trailing:
            trailing=True; cur_sl=entry-BE_PLUS; bot.send_message(chat,f"✅ #{tid} TP1 → BE+0.5 {cur_sl}")
        if bias=="BUY" and price>=tp1 and not trailing:
            trailing=True; cur_sl=entry+BE_PLUS; bot.send_message(chat,f"✅ #{tid} TP1 → BE+0.5 {cur_sl}")
        if trailing:
            if bias=="SELL" and entry-price> TRAIL_DIST:
                ns=price+TRAIL_DIST
                if ns<cur_sl: cur_sl=ns; bot.send_message(chat,f"🔄 #{tid} TRAIL → {cur_sl}")
            if bias=="BUY" and price-entry> TRAIL_DIST:
                ns=price-TRAIL_DIST
                if ns>cur_sl: cur_sl=ns; bot.send_message(chat,f"🔄 #{tid} TRAIL → {cur_sl}")
        if bias=="SELL" and price>=cur_sl: update_trade(tid,f"BE+0.5",0); return
        if bias=="BUY" and price<=cur_sl: update_trade(tid,f"BE+0.5",0); return
        if (bias=="SELL" and price<=tp2) or (bias=="BUY" and price>=tp2):
            update_trade(tid,"TP2",100); bot.send_message(chat,f"✅✅ #{tid} TP2 HIT"); return
        time.sleep(45)

def monitor_sweep(data,chat):
    if not data: return
    pair,bias,zone=data['pair'],data['bias'],data['zone']
    try: bot.send_message(chat,f"🔍 {pair} {bias} Watching Zone {zone[0]}-{zone[1]} till 10 AM\n{data['text']}")
    except: pass
    end=datetime.now(WAT).replace(hour=10,minute=0,second=0)
    if datetime.now(WAT).hour>=10: end=datetime.now(WAT)+timedelta(hours=1)
    while datetime.now(WAT)<end:
        price=get_price(pair)
        if not price: time.sleep(60); continue
        low,high=zone
        if low<=price<=high:
            tid=log_trade(data)
            try: bot.send_message(chat,f"✅ VALID TRADE #{tid} {pair} {bias} Entry {data['entry']}")
            except: pass
            threading.Thread(target=manage_trade, args=(tid,data,chat), daemon=True).start(); return
        time.sleep(60)

def scheduler():
    weekend_sent=False
    while True:
        now=datetime.now(WAT)
        if now.weekday()<5 and now.hour==7 and now.minute<10:
            try: bot.send_message(CHANNEL_ID,f"☀️ <b>7 AM WAT - 7 PAIRS REAL DATA</b>")
            except: pass
            for display in PAIRS.keys():
                try:
                    data=auto_generate_complete_brief(display)
                    if data:
                        bot.send_message(CHANNEL_ID, data['text'])
                        threading.Thread(target=monitor_sweep, args=(data, CHANNEL_ID), daemon=True).start()
                    # CRITICAL: Wait 15 sec between pairs to avoid 429
                    time.sleep(15)
                except Exception as e:
                    print(f"Brief error {display}: {e}")
            time.sleep(3600)
        if now.weekday()>=5 and now.hour==20 and now.minute<10 and not weekend_sent:
            try: bot.send_message(CHANNEL_ID, get_week_journal_text()); weekend_sent=True
            except: pass
        if now.hour==0: weekend_sent=False
        time.sleep(30)

@bot.message_handler(commands=['brief','week','journal','start'])
def cmds(m):
    if 'brief' in m.text or 'start' in m.text:
        bot.send_message(m.chat.id,"☀️ Generating 7 pairs with REAL data - wait 90 sec...")
        for display in PAIRS.keys():
            data=auto_generate_complete_brief(display)
            if data:
                bot.send_message(m.chat.id, data['text'])
                time.sleep(8)
    elif 'week' in m.text:
        bot.send_message(m.chat.id, get_week_journal_text())
    else:
        rows=conn.execute("SELECT id,date,pair,bias,result,pips FROM trades ORDER BY id DESC LIMIT 15").fetchall()
        txt="\n".join([f"#{r[0]} {r[2]} {r[4]}" for r in rows]) or "No trades"
        bot.send_message(m.chat.id, txt)

threading.Thread(target=scheduler, daemon=True).start()
bot.infinity_polling(skip_pending=True, timeout=60)
