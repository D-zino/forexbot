from flask import Flask
import os, requests, re, time, threading
import telebot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY")
CALLMEBOT_PHONE = os.environ.get("CALLMEBOT_PHONE", "none")
CALLMEBOT_APIKEY = os.environ.get("CALLMEBOT_APIKEY", "none")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
active_trades = {}

app = Flask(__name__)
@app.route('/')
def home():
    return "V5.1 TEST MODE - Reading ALL Channels"
def run_web():
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))
threading.Thread(target=run_web, daemon=True).start()

def send_alert(msg):
    print(msg)
    try:
        # send to anyone who DMs the bot
        @bot.message_handler(commands=['id'])
        def getid(m): bot.reply_to(m, f"Your ID: {m.chat.id}\nChannel ID: {m.chat.id}")
    except: pass
    try:
        # Try to send to all private chats that messaged bot
        bot.send_message(8778456045, msg) # TEMP
    except:
        pass
    print("ALERT: " + msg)

def get_candles(symbol, interval="5min", count=20):
    try:
        url = f"https://api.twelvedata.com/time_series?symbol={symbol}&interval={interval}&apikey={TWELVE_API_KEY}&outputsize={count}"
        r = requests.get(url, timeout=10).json()
        if "values" not in r: return []
        return r["values"][::-1]
    except: return []

def confirm_entry(symbol, bias):
    candles = get_candles(symbol.replace("/",""), "5min", 5)
    if len(candles) < 3: return False
    last = candles[-1]; prev = candles[-2]
    try:
        lc = float(last["close"]); lo = float(last["open"])
        if bias=="SELL": return lc < lo and lc < float(prev["low"])
        else: return lc > lo and lc > float(prev["high"])
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
                "sweep_done": False, "in_fvg_since": None
            })
    return trades

@bot.channel_post_handler(func=lambda m: True)
@bot.message_handler(func=lambda m: True)
def handle_all(m):
    text = m.text or m.caption or ""
    print(f"GOT MESSAGE from {m.chat.id} : {text[:100]}")
    if "Bias:" not in text:
        print("No Bias found, ignoring")
        return
    # NO ID CHECK - READS EVERYTHING FOR TEST
    trades = parse_brief(text)
    print(f"Parsed {len(trades)} trades")
    for t in trades:
        active_trades[t["pair"]] = t
        msg = f"✅ V5.1 AUTO BRIEF {t['pair']} {t['bias']}\nFVG {t['fvg_low']}-{t['fvg_high']}\nSweep {t['liq']}\nBreaker {t['breaker']}\nSL {t['sl_pips']} TP {t['tp1']}/{t['tp2']}"
        print(msg)
        try:
            bot.send_message(m.chat.id, msg)
        except Exception as e:
            print(f"Send error {e}")

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
                        bot.send_message(list(active_trades.keys()).__str__, f"❌ INVALIDATED {pair}") if False else None
                        print(f"INVALIDATED {pair}")
                        del active_trades[pair]; continue
                if data["liq"] and not data["sweep_done"]:
                    tol = 0.0015 if "XAU" not in pair else 3.0
                    if abs(price - data["liq"]) < tol or (data["bias"]=="SELL" and price >= data["liq"]-0.0002) or (data["bias"]=="BUY" and price <= data["liq"]+0.0002):
                        data["sweep_done"]=True
                        print(f"SWEEP DONE {pair}")
                    else: continue
                if data["fvg_low"] <= price <= data["fvg_high"]:
                    if not data["in_fvg_since"]:
                        data["in_fvg_since"]=time.time()
                        print(f"{pair} IN FVG {price}")
                        continue
                    if time.time() - data["in_fvg_since"] > 240:
                        if confirm_entry(pair, data["bias"]):
                            print(f"CONFIRMED ENTRY {pair}")
                            del active_trades[pair]
            except Exception as e:
                print(e)
        time.sleep(30)

threading.Thread(target=price_watcher, daemon=True).start()
bot.remove_webhook()
time.sleep(2)
bot.infinity_polling(skip_pending=True)
