import requests, re, time, threading
from datetime import datetime, timezone
import telebot

# ==== EDIT THESE 6 ====
TELEGRAM_TOKEN = "8778456045:AAGwdyjbuAQjeeW0MMqmavglLhLCTPiSwSU"
CHANNEL_ID = -1004370772560
CHAT_ID = 7128883071
TWELVE_API_KEY = "e426cee06e51469785f4a32d0b04793d"
CALLMEBOT_PHONE = "2349168678870"
CALLMEBOT_APIKEY = "PASTE_CALLMEBOT_APIKEY"
# =====================

bot = telebot.TeleBot(TELEGRAM_TOKEN)
active_trades = {}
last_brief_time = None

def send_alert(msg):
    print(msg)
    try:
        url = f"https://api.callmebot.com/whatsapp.php?phone={CALLMEBOT_PHONE}&text={requests.utils.quote(msg[:900])}&apikey={CALLMEBOT_APIKEY}"
        requests.get(url, timeout=10)
    except: pass
    try: bot.send_message(CHAT_ID, msg)
    except: pass

def get_candles(symbol, interval="5min", count=20):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={TWELVE_API_KEY}&outputsize={count}"
        r = requests.get(url, timeout=10).json()
        return r["values"][::-1] # oldest first
    except: return []

def detect_fvg_auto(symbol):
    # 15m FVG auto backup
    candles = get_candles(symbol, "15min", 30)
    if len(candles) < 5: return None
    for i in range(2, len(candles)-1):
        # bearish FVG: high[i-2] < low[i]
        c1 = candles[i-2]; c3 = candles[i]
        try:
            h1 = float(c1["high"]); l3 = float(c3["low"])
            if h1 < l3:
                # found bullish? actually bearish FVG is gap up
                fvg_low = h1; fvg_high = l3
                # only fresh FVG not yet touched in last 3 candles
                recent_high = max(float(x["high"]) for x in candles[-3:])
                if recent_high < fvg_low: # not touched
                    return (fvg_low, fvg_high, "SELL" if float(c3["close"])>float(c3["open"]) else "BUY")
        except: continue
    return None

def confirm_entry(symbol, bias):
    candles = get_candles(symbol.replace("/",""), "5min", 5)
    if len(candles) < 3: return False
    last = candles[-1]; prev = candles[-2]
    try:
        lc = float(last["close"]); lo = float(last["open"])
        pc = float(prev["close"])
        if bias=="SELL":
            # bearish close + break of prev low
            return lc < lo and lc < float(prev["low"])
        else:
            return lc > lo and lc > float(prev["high"])
    except: return False

def parse_brief(text):
    trades=[]
    pattern = r"(EUR\/USD|GBP\/USD|USD\/CAD|XAU\/USD|GOLD).*?Bias:\s*(BUY|SELL)"
    for m in re.finditer(pattern, text, re.I|re.S):
        chunk = text[m.start(): m.start()+700]
        pair = m.group(1).replace("GOLD","XAU/USD")
        bias = m.group(2).upper()
        fvg = re.search(r"FVG.*?(\$?[\d\.]+)\s*-\s*(\$?[\d\.]+)", chunk, re.I)
        liq = re.search(r"Liquidity.*?Sweep\s*(\$?[\d\.]+)", chunk, re.I)
        breakr = re.search(r"Breaker.*?(\$?[\d\.]+)", chunk, re.I)
        sl = re.search(r"SL\s*(\d+)", chunk, re.I)
        tp1 = re.search(r"TP1\s*(\d+)", chunk, re.I)
        tp2 = re.search(r"TP2\s*(\d+)", chunk, re.I)
        if fvg:
            trades.append({
                "pair":pair,"bias":bias,
                "fvg_low":float(fvg.group(1).replace("$","")),"fvg_high":float(fvg.group(2).replace("$","")),
                "liq": float(liq.group(1).replace("$","")) if liq else None,
                "breaker": float(breakr.group(1).replace("$","")) if breakr else None,
                "sl_pips": int(sl.group(1)) if sl else 25,
                "tp1": int(tp1.group(1)) if tp1 else 35,
                "tp2": int(tp2.group(1)) if tp2 else 70,
                "sweep_done": False, "in_fvg_since": None, "source":"BRIEF"
            })
    return trades

@bot.channel_post_handler(func=lambda m: True)
@bot.message_handler(func=lambda m: True)
def handle_all(m):
    global last_brief_time
    text = m.text or m.caption or ""
    if "Bias:" not in text: return
    if m.chat.id!=CHANNEL_ID and m.chat.id!=CHAT_ID: return
    trades = parse_brief(text)
    last_brief_time = datetime.now()
    for t in trades:
        active_trades[t["pair"]] = t
        send_alert(f"✅ V5.1 AUTO BRIEF {t['pair']} {t['bias']}\nFVG {t['fvg_low']}-{t['fvg_high']}\nSweep Zone {t['liq']}\nBreaker {t['breaker']}\nSL {t['sl_pips']} TP {t['tp1']}/{t['tp2']}\nWill wait for 5m confirmation before entry")

def price_watcher():
    while True:
        # BACKUP: if no brief by 8:15 WAT, auto detect
        global last_brief_time
        hour_wat = datetime.now(timezone.utc).hour + 1
        if not last_brief_time or (datetime.now() - last_brief_time).seconds > 3600*12:
            if hour_wat == 8: # 8 AM backup scan
                for p in ["EUR/USD","GBP/USD","XAU/USD","USD/CAD"]:
                    fvg = detect_fvg_auto(p.replace("/",""))
                    if fvg and p not in active_trades:
                        low, high, bias = fvg
                        active_trades[p] = {"pair":p,"bias":bias,"fvg_low":min(low,high),"fvg_high":max(low,high),"liq":None,"breaker":None,"sl_pips":25,"tp1":35,"tp2":70,"sweep_done":True,"in_fvg_since":None,"source":"AUTO"}
                        send_alert(f"🤖 AUTO-DETECTED {p} {bias} FVG {low}-{high} (no brief today) — watching for confirmation")

        for pair, data in list(active_trades.items()):
            try:
                sym = pair.replace("/","")
                price = float(requests.get(f"https://api.twelvedata.com/price?symbol={sym}&apikey={TWELVE_API_KEY}", timeout=10).json()["price"])
                # breaker
                if data["breaker"]:
                    if (data["bias"]=="SELL" and price > data["breaker"]) or (data["bias"]=="BUY" and price < data["breaker"]):
                        send_alert(f"❌ INVALIDATED {pair} breaker {data['breaker']} broke @ {price}")
                        del active_trades[pair]; continue
                # sweep zone check ±15 pips / $3 gold
                if data["liq"] and not data["sweep_done"]:
                    tol = 0.0015 if "XAU" not in pair else 3.0
                    if abs(price - data["liq"]) < tol or (data["bias"]=="SELL" and price > data["liq"]) or (data["bias"]=="BUY" and price < data["liq"]):
                        data["sweep_done"]=True
                        send_alert(f"✅ SWEEP DONE {pair} {price} target {data['liq']} — now wait FVG")
                    else: continue
                # FVG entry with confirmation wait
                if data["fvg_low"] <= price <= data["fvg_high"]:
                    if not data["in_fvg_since"]:
                        data["in_fvg_since"]=time.time()
                        send_alert(f"👀 {pair} IN FVG {price} ({data['fvg_low']}-{data['fvg_high']}) — waiting 5m confirmation that trend continues...")
                        continue
                    # wait 4 mins for candle close
                    if time.time() - data["in_fvg_since"] > 240:
                        if confirm_entry(pair, data["bias"]):
                            send_alert(f"🔴 CONFIRMED ENTRY {pair} {data['bias']} @ {price}\nFVG {data['fvg_low']}-{data['fvg_high']}\nSL {data['sl_pips']} TP1 {data['tp1']} TP2 {data['tp2']}\nSource: {data['source']}")
                            del active_trades[pair]
                        else:
                            send_alert(f"⚠️ {pair} FVG {price} no confirmation (still pushing) — resetting wait")
                            data["in_fvg_since"]=None
                else:
                    data["in_fvg_since"]=None
            except Exception as e:
                print(e)
        time.sleep(30)

threading.Thread(target=price_watcher, daemon=True).start()
print("V5.1 listening...")
bot.infinity_polling()
