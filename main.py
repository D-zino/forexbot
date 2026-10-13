from flask import Flask
import threading
import os, requests, re, time, threading
from datetime import datetime, timezone
import telebot

# === SAFE - reads from Koyeb Environment ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1004370772560"))
CHAT_ID = int(os.environ.get("CHAT_ID", "0"))
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "none")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "none")
# ===========================================

if not TELEGRAM_TOKEN:
    print("ERROR: TELEGRAM_TOKEN not set in Koyeb env!")
    exit(1)

bot = telebot.TeleBot(TELEGRAM_TOKEN)
active_trades = {}
last_brief_time = None

def send_alert(msg):
    print(msg)
    try:
        if CALLMEBOT_APIKEY!= "none":
            url = f"https://api.callmebot.com/whatsapp.php?phone={CALLMEBOT_PHONE}&text={requests.utils.quote(msg[:900])}&apikey={CALLMEBOT_APIKEY}"
            requests.get(url, timeout=10)
    except: pass
    try:
        if CHAT_ID!= 0:
            bot.send_message(CHAT_ID, msg)
    except Exception as e:
        print(f"Telegram send error: {e}")

def get_candles(symbol, interval="5min", count=20):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={TWELVE_API_KEY}&outputsize={count}"
        r = requests.get(url, timeout=10).json()
        if "values" not in r:
            print(f"Candle error: {r}")
            return []
        return r["values"][::-1]
    except Exception as e:
        print(f"get_candles error: {e}")
        return []

def confirm_entry(symbol, bias):
    candles = get_candles(symbol.replace("/",""), "5min", 5)
    if len(candles) < 3: return False
    last = candles[-1]; prev = candles[-2]
    try:
        lc = float(last["close"]); lo = float(last["open"])
        if bias=="SELL":
            return lc < lo and lc < float(prev["low"])
        else:
            return lc > lo and lc > float(prev["high"])
    except: return False

def parse_brief(text):
    trades=[]
    pattern = r"(EUR\/USD|GBP\/USD|USD\/CAD|XAU\/USD|GOLD).*?Bias:\s*(BUY|SELL)"
    for m in re.finditer(pattern, text, re.I|re.S):
        chunk = text[m.start(): m.start()+700]
        pair = m.group(1).replace("GOLD","XAU/USD").upper()
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
        send_alert(f"✅ V5.1 AUTO BRIEF {t['pair']} {t['bias']}\nFVG {t['fvg_low']}-{t['fvg_high']}\nSweep {t['liq']}\nBreaker {t['breaker']}\nSL {t['sl_pips']} TP {t['tp1']}/{t['tp2']}\nWaiting 5m confirmation")

def price_watcher():
    while True:
        for pair, data in list(active_trades.items()):
            try:
                sym = pair.replace("/","")
                if "XAU" in pair: sym = "XAU/USD"
                resp = requests.get(f"https://api.twelvedata.com/price?symbol={sym}&apikey={TWELVE_API_KEY}", timeout=10).json()
                if "price" not in resp: continue
                price = float(resp["price"])
                if data["breaker"]:
                    if (data["bias"]=="SELL" and price > data["breaker"]) or (data["bias"]=="BUY" and price < data["breaker"]):
                        send_alert(f"❌ INVALIDATED {pair} breaker {data['breaker']} broke @ {price}")
                        del active_trades[pair]; continue
                if data["liq"] and not data["sweep_done"]:
                    tol = 0.0015 if "XAU" not in pair else 3.0
                    if abs(price - data["liq"]) < tol or (data["bias"]=="SELL" and price >= data["liq"]-0.0002) or (data["bias"]=="BUY" and price <= data["liq"]+0.0002):
                        data["sweep_done"]=True
                        send_alert(f"✅ SWEEP DONE {pair} {price} target {data['liq']} — now wait FVG")
                    else: continue
                if data["fvg_low"] <= price <= data["fvg_high"]:
                    if not data["in_fvg_since"]:
                        data["in_fvg_since"]=time.time()
                        send_alert(f"👀 {pair} IN FVG {price} ({data['fvg_low']}-{data['fvg_high']}) — waiting 5m confirmation...")
                        continue
                    if time.time() - data["in_fvg_since"] > 240:
                        if confirm_entry(pair, data["bias"]):
                            send_alert(f"🔴 CONFIRMED ENTRY {pair} {data['bias']} @ {price}\nFVG {data['fvg_low']}-{data['fvg_high']}\nSL {data['sl_pips']} TP1 {data['tp1']} TP2 {data['tp2']}")
                            del active_trades[pair]
                        else:
                            send_alert(f"⚠️ {pair} FVG {price} no confirmation — resetting wait")
                            data["in_fvg_since"]=None
                else:
                    data["in_fvg_since"]=None
            except Exception as e:
                print(e)
        time.sleep(30)

threading.Thread(target=price_watcher, daemon=True).start()
print("V5.1 listening...")
bot.remove_webhook()
bot.infinity_polling()
# Keep Render happy - fake web server
app = Flask(__name__)
@app.route('/')
def home():
    return "Bot is alive!"

def run_web():
    app.run(host='0.0.0.0', port=10000)

threading.Thread(target=run_web).start()
