import os, time, threading, requests, sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import telebot

TOKEN = os.getenv("TELEGRAM_TOKEN")
TD_KEY = os.getenv("TWELVEDATA_API_KEY")
CHANNEL_ID = os.getenv("CHANNEL_ID")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")
bot.remove_webhook()
WAT = ZoneInfo("Africa/Lagos")

PAIRS = {
    "EUR/USD": "EUR/USD",
    "EUR/JPY": "EUR/JPY",
    "USD/JPY": "USD/JPY",
    "NAS100": "NDX",
    "US30": "DJI",
    "GERMANY40": "DAX",
    "XAU/USD": "XAU/USD"
}

print("🚀 V6.1 FINAL - 7 PAIRS 7AM AUTO")

conn = sqlite3.connect("trades.db", check_same_thread=False)
conn.execute("""CREATE TABLE IF NOT EXISTS trades (
id INTEGER PRIMARY KEY AUTOINCREMENT,
date TEXT, pair TEXT, bias TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL,
result TEXT, pips REAL, status TEXT, brief TEXT)""")
conn.commit()

def get_price(pair):
    try:
        td = PAIRS.get(pair, pair)
        r = requests.get(f"https://api.twelvedata.com/price?symbol={td}&apikey={TD_KEY}", timeout=10).json()
        return float(r['price'])
    except: return None

def get_candles(pair, interval, limit=100):
    try:
        td = PAIRS.get(pair, pair)
        url = f"https://api.twelvedata.com/time_series?symbol={td}&interval={interval}&outputsize={limit}&apikey={TD_KEY}"
        j = requests.get(url, timeout=15).json()
        if 'values' not in j: return []
        vals = j['values'][::-1]
        return [{"high":float(v['high']),"low":float(v['low']),"open":float(v['open']),"close":float(v['close'])} for v in vals]
    except: return []

def detect_ob(candles):
    if len(candles)<5: return 1.1650, 1.1670, "Bearish"
    for i in range(len(candles)-4,0,-1):
        c=candles[i]; nxt=candles[i+1]
        if c['close']<c['open'] and nxt['close']>c['high']:
            return round(c['low'],5), round(c['high'],5), "Bearish"
        if c['close']>c['open'] and nxt['close']<c['low']:
            return round(c['low'],5), round(c['high'],5), "Bullish"
    c=candles[-2]; return round(c['low'],5), round(c['high'],5), "Bearish"

def detect_fvg(candles):
    if len(candles)<3: return 1.1605, 1.1620, "Bullish"
    for i in range(len(candles)-2,0,-1):
        p,n=candles[i-1],candles[i+1]
        if n['low']>p['high']: return round(p['high'],5), round(n['low'],5), "Bullish"
        if n['high']<p['low']: return round(n['high'],5), round(p['low'],5), "Bearish"
    c=candles[-2]; return round(c['low']+0.0005,5), round(c['low']+0.0020,5), "Bullish"

def get_asia_session(pair):
    candles=get_candles(pair,"1h",24)
    if len(candles)<8: return 1.1585, 1.1620
    asia=candles[-8:-2]
    return round(min(c['low'] for c in asia),5), round(max(c['high'] for c in asia),5)

def auto_generate_complete_brief(pair_display):
    td_symbol = PAIRS.get(pair_display, pair_display)
    price = get_price(pair_display) or 1.16
    c4h=get_candles(pair_display,"4h",50)
    c15=get_candles(pair_display,"15min",100)
    asia_low, asia_high=get_asia_session(pair_display)
    ob_low, ob_high, ob_type=detect_ob(c4h)
    fvg_low, fvg_high, fvg_type=detect_fvg(c15)

    bias="SELL" if price<ob_low else "BUY"
    if "EUR/USD" in pair_display and price<(ob_low+ob_high)/2: bias="SELL"

    sweep=asia_low if bias=="SELL" else asia_high
    sweep_label="Asia low" if bias=="SELL" else "Asia high"
    is_gold="XAU" in pair_display
    is_index=pair_display in ["NAS100","US30","GERMANY40"]
    is_jpy="JPY" in pair_display

    breaker=round(sweep+0.0005 if bias=="SELL" else sweep-0.0005,5)
    poc=round((fvg_low+fvg_high)/2,5)
    entry=poc

    if is_gold:
        sl=ob_high if bias=="SELL" else ob_low
        tp1=entry-3.0 if bias=="SELL" else entry+3.0
        tp2=entry-8.0 if bias=="SELL" else entry+8.0
    elif is_index:
        sl=entry+80 if bias=="SELL" else entry-80
        tp1=entry-60 if bias=="SELL" else entry+60
        tp2=entry-150 if bias=="SELL" else entry+150
    elif is_jpy:
        sl=round(ob_high+0.15 if bias=="SELL" else ob_low-0.15,3)
        tp1=round(entry-0.30 if bias=="SELL" else entry+0.30,3)
        tp2=round(entry-0.60 if bias=="SELL" else entry+0.60,3)
    else:
        sl=ob_high if bias=="SELL" else ob_low
        tp1=round(entry-0.0020 if bias=="SELL" else entry+0.0020,5)
        tp2=round(entry-0.0040 if bias=="SELL" else entry+0.0040,5)

    zone_low=round(sweep-0.0005,5) if not is_jpy and not is_index and not is_gold else sweep-0.5
    zone_high=round(sweep+0.0005,5) if not is_jpy and not is_index and not is_gold else sweep+0.5
    if is_gold: zone_low, zone_high=sweep-1, sweep+1
    if is_index: zone_low, zone_high=sweep-20, sweep+20
    if is_jpy: zone_low, zone_high=round(sweep-0.10,3), round(sweep+0.10,3)

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
    if not rows: return "📓 WEEK JOURNAL: No valid trades this week - No sweep = No forced trades"
    pair_stats={}
    for d,pair,bias,res,pips in rows:
        if pair not in pair_stats: pair_stats[pair]={"trades":0,"wins":0,"pips":0}
        pair_stats[pair]["trades"]+=1; pair_stats[pair]["pips"]+=pips
        if "TP" in res or "TRAIL" in res or "BE" in res: pair_stats[pair]["wins"]+=1
    sorted_pairs=sorted(pair_stats.items(), key=lambda x: x[1]['pips'], reverse=True)
    best=sorted_pairs[0][0] if sorted_pairs else "None"
    lines=[]
    for pair, s in sorted_pairs:
        wr=s['wins']/s['trades']*100 if s['trades']>0 else 0
        lines.append(f"{'🏆' if pair==best else '▫️'} {pair}: {s['trades']}t | {s['wins']}w {wr:.0f}% | {s['pips']:+.1f}p")
    detail=[f"{'✅' if 'TP' in r[3] or 'TRAIL' in r[3] or 'BE' in r[3] else '❌'} {r[0][5:]} {r[1]} {r[2]} {r[3]} {r[4]:+.1f}" for r in rows]
    total=len(rows); total_pips=sum(r[4] for r in rows); wins=len([r for r in rows if "TP" in r[3] or "TRAIL" in r[3] or "BE" in r[3]])
    return f"""📓 <b>WEEKEND JOURNAL - PER-PAIR PROFITABILITY</b>
📅 {datetime.now(WAT).strftime('%b %d')} | 7 Pairs

<b>🏆 RANKING MOST PROFITABLE:</b>
{chr(10).join(lines)}

Best: <b>{best}</b> {pair_stats[best]['pips']:+.1f} pips

Total: {total} | Wins:{wins} | {total_pips:+.1f} pips

<b>TRADES:</b>
{chr(10).join(detail)}"""

def manage_trade(tid,data,chat):
    pair,bias,entry,sl,tp1,tp2=data['pair'],data['bias'],data['entry'],data['sl'],data['tp1'],data['tp2']
    is_gold="XAU" in pair; is_index=pair in ["NAS100","US30","GERMANY40"]; is_jpy="JPY" in pair
    BE_PLUS=0.05 if is_gold else 5 if is_index else 0.00005 if not is_jpy else 0.005
    TRAIL_DIST=0.8 if is_gold else 25 if is_index else 0.0015 if not is_jpy else 0.15
    trailing=False; cur_sl=sl
    bot.send_message(chat,f"📈 Managing #{tid} {pair} {bias}\nBE+0.5 + Trailing active")
    while True:
        price=get_price(pair)
        if not price: time.sleep(30); continue
        if bias=="SELL" and price<=tp1 and not trailing:
            trailing=True; cur_sl=entry-BE_PLUS
            bot.send_message(chat,f"✅ #{tid} {pair} TP1 HIT\n🔒 SL → BE+0.5 = {cur_sl} | +0.5 locked")
        if bias=="BUY" and price>=tp1 and not trailing:
            trailing=True; cur_sl=entry+BE_PLUS
            bot.send_message(chat,f"✅ #{tid} {pair} TP1 HIT\n🔒 SL → BE+0.5 = {cur_sl}")
        if trailing:
            if bias=="SELL" and entry-price> TRAIL_DIST:
                ns=price+TRAIL_DIST
                if ns<cur_sl: cur_sl=ns; bot.send_message(chat,f"🔄 #{tid} TRAIL → {cur_sl} Locked {(entry-cur_sl):.2f}")
            if bias=="BUY" and price-entry> TRAIL_DIST:
                ns=price-TRAIL_DIST
                if ns>cur_sl: cur_sl=ns; bot.send_message(chat,f"🔄 #{tid} TRAIL → {cur_sl}")
        if bias=="SELL" and price>=cur_sl:
            p=entry-cur_sl; update_trade(tid,f"BE+0.5 +{p:.1f}",p); bot.send_message(chat,f"🔒 #{tid} Closed BE+0.5/Trail {cur_sl}"); return
        if bias=="BUY" and price<=cur_sl:
            p=cur_sl-entry; update_trade(tid,f"BE+0.5 +{p:.1f}",p); bot.send_message(chat,f"🔒 #{tid} Closed BE+0.5/Trail {cur_sl}"); return
        if (bias=="SELL" and price<=tp2) or (bias=="BUY" and price>=tp2):
            p=abs(price-entry); update_trade(tid,"TP2",p); bot.send_message(chat,f"✅✅ #{tid} TP2 HIT +{p} CLOSED"); return
        time.sleep(45)

def monitor_sweep(data,chat):
    pair,bias,zone=data['pair'],data['bias'],data['zone']
    bot.send_message(chat,f"🔍 {pair} {bias} Watching sweep Zone {zone[0]}-{zone[1]} till 10 AM WAT\n{data['text']}")
    end=datetime.now(WAT).replace(hour=10,minute=0,second=0)
    if datetime.now(WAT).hour>=10: end=datetime.now(WAT)+timedelta(hours=1)
    while datetime.now(WAT)<end:
        price=get_price(pair)
        if not price: time.sleep(60); continue
        low,high=zone
        if bias=="SELL" and price>high+ (3 if pair in ["NAS100","US30","GERMANY40"] else 0.003):
            bot.send_message(chat,f"ℹ️ {pair} Opposite sweep {price} - flipping watch"); return
        if low<=price<=high:
            tid=log_trade(data)
            bot.send_message(chat,f"✅ <b>VALID TRADE #{tid} {pair} {bias}</b>\nSweep {price} confirmed {datetime.now(WAT).strftime('%H:%M')}\nEntry {data['entry']} SL {data['sl']} TP1 {data['tp1']} TP2 {data['tp2']}")
            threading.Thread(target=manage_trade, args=(tid,data,chat), daemon=True).start(); return
        time.sleep(60)
    bot.send_message(chat,f"⚠️ {pair} No sweep {zone[0]}-{zone[1]} - Will NOT force trade")

def scheduler():
    weekend_sent=False
    while True:
        now=datetime.now(WAT)
        if now.weekday()<5 and now.hour==7 and now.minute<10:
            bot.send_message(CHANNEL_ID,f"☀️ <b>7 AM WAT - DAILY ANALYSIS 7 PAIRS</b> Generating...")
            for display in PAIRS.keys():
                try:
                    data=auto_generate_complete_brief(display)
                    bot.send_message(CHANNEL_ID, data['text'])
                    threading.Thread(target=monitor_sweep, args=(data, CHANNEL_ID), daemon=True).start()
                    time.sleep(4)
                except Exception as e: print(e)
            time.sleep(3600)
        if now.weekday()>=5 and now.hour==20 and now.minute<10 and not weekend_sent:
            try: bot.send_message(CHANNEL_ID, get_week_journal_text()); weekend_sent=True
            except: pass
        if now.hour==0: weekend_sent=False
        time.sleep(30)

@bot.message_handler(commands=['brief','week','journal'])
def cmds(m):
    if 'brief' in m.text:
        bot.send_message(m.chat.id,"☀️ Generating 7 pairs...")
        for display in PAIRS.keys():
            data=auto_generate_complete_brief(display)
            bot.send_message(m.chat.id, data['text'])
    elif 'week' in m.text: bot.send_message(m.chat.id, get_week_journal_text())
    else:
        rows=conn.execute("SELECT id,date,pair,bias,result,pips FROM trades ORDER BY id DESC LIMIT 15").fetchall()
        txt="\n".join([f"#{r[0]} {r[2]} {r[3]} {r[4]} {r[5]:+.1f}" for r in rows]) or "No trades"
        bot.send_message(m.chat.id, f"Last 15:\n{txt}")

threading.Thread(target=scheduler, daemon=True).start()
bot.infinity_polling(timeout=60)
