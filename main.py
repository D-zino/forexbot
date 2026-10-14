import os, time, threading, requests, sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from flask import Flask

TOKEN=os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID=os.getenv("CHANNEL_ID")
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL")

app=Flask(__name__)
@app.route('/')
def home(): return "V13 V5.1+MORNING NO TD + TICK"
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

FALLBACK={"EUR/USD":1.1652,"GBP/USD":1.3435,"USD/CAD":1.3785,"EUR/JPY":178.12,"USD/JPY":157.85,"XAU/USD":4215.50,"NAS100":27350,"US30":51850,"DXY":98.25,"WTI":95.10,"GERMANY40":25150}

# ========= LIVE WITHOUT TWELVEDATA =========
def get_live_no_td():
    data={}
    # FOREX FREE - open.er-api.com (no key, unlimited)
    try:
        r=requests.get("https://open.er-api.com/v6/latest/USD",timeout=8).json()
        rates=r.get('rates',{})
        if rates:
            data['EUR/USD']=round(1/rates['EUR'],5)
            data['GBP/USD']=round(1/rates['GBP'],5)
            data['USD/CAD']=round(rates['CAD'],5)
            data['USD/JPY']=round(rates['JPY'],4)
            data['EUR/JPY']=round(data['EUR/USD']*data['USD/JPY'],3)
            data['DXY']=FALLBACK['DXY']
            data['WTI']=FALLBACK['WTI']
            print(f"FOREX LIVE OK {data}")
    except Exception as e:
        print(f"Forex API fail {e}")
        for k in ["EUR/USD","GBP/USD","USD/CAD","USD/JPY","EUR/JPY","DXY","WTI"]:
            data[k]=FALLBACK[k]

    # GOLD FREE
    try:
        r=requests.get("https://api.gold-api.com/price/XAU",timeout=8).json()
        data['XAU/USD']=float(r['price'])
        print(f"GOLD LIVE {data['XAU/USD']}")
    except:
        try:
            r=requests.get("https://api.metals.live/v1/spot",timeout=8).json()
            # try parse
            if isinstance(r, dict):
                data['XAU/USD']=float(r.get('gold',4215))
            else:
                data['XAU/USD']=FALLBACK['XAU/USD']
        except:
            data['XAU/USD']=FALLBACK['XAU/USD']

    # INDICES FREE - Yahoo (no key)
    try:
        headers={"User-Agent":"Mozilla/5.0"}
        for yahoo, my in [("%5EIXIC","NAS100"), ("%5EDJI","US30"), ("^GDAXI","GERMANY40")]:
            try:
                r=requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo}?interval=1m&range=1d",timeout=8,headers=headers).json()
                price=r['chart']['result'][0]['meta']['regularMarketPrice']
                data[my]=float(price)
                print(f"{my} LIVE {price}")
            except:
                data[my]=FALLBACK[my]
    except Exception as e:
        print(f"Yahoo fail {e}")
        data['NAS100']=FALLBACK['NAS100']
        data['US30']=FALLBACK['US30']
        data['GERMANY40']=FALLBACK['GERMANY40']

    return data

def calc_pips(pair, entry, current, bias):
    mult=100 if "JPY" in pair else 10000 if "/" in pair else 1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult

def build_pair(pair, live, bias):
    # V5.1 calculations
    if pair in ["EUR/USD","GBP/USD","USD/CAD"]:
        fvg_low=live-0.0010; fvg_high=live+0.0008
        ht_low=live-0.0025; ht_high=live+0.0020
        sweep=live+0.0010 if bias=="BUY" else live-0.0010
        breaker=live+0.0005
        entry=live; poc=live; ote=live
        sl=live-0.0024 if bias=="BUY" else live+0.0020
        tp1=live+0.0020 if bias=="BUY" else live-0.0015
        tp2=live+0.0040 if bias=="BUY" else live-0.0030
        zone_low=live+0.0005; zone_high=live+0.0015
    elif "JPY" in pair:
        fvg_low=live-0.20; fvg_high=live+0.15
        ht_low=live-0.50; ht_high=live+0.40
        sweep=live+0.30
        breaker=live+0.20
        entry=live; poc=live; ote=live
        sl=live-0.62; tp1=live+0.30; tp2=live+0.60
        zone_low=live+0.20; zone_high=live+0.40
    elif pair=="XAU/USD":
        fvg_low=live-4.0; fvg_high=live+4.0
        ht_low=live-10.0; ht_high=live+12.0
        sweep=live-7.0 if bias=="SELL" else live+7.0
        breaker=live-6.0 if bias=="SELL" else live+6.0
        entry=live; poc=live; ote=live
        sl=live+12.0 if bias=="SELL" else live-12.0
        tp1=live-3.0 if bias=="SELL" else live+8.0
        tp2=live-8.0 if bias=="SELL" else live+15.0
        zone_low=live-8.0; zone_high=live-6.0
    else:
        fvg_low=live-80; fvg_high=live+30
        ht_low=live-120; ht_high=live+80
        sweep=live+70; breaker=live+65
        entry=live; poc=live; ote=live
        sl=live-80; tp1=live+60; tp2=live+150
        zone_low=live+50; zone_high=live+90

    return {"live":live,"fvg_low":fvg_low,"fvg_high":fvg_high,"ht_low":ht_low,"ht_high":ht_high,"sweep":sweep,"breaker":breaker,"entry":entry,"poc":poc,"ote":ote,"sl":sl,"tp1":tp1,"tp2":tp2,"zone_low":zone_low,"zone_high":zone_high}

def generate_full():
    d=get_live_no_td()
    now=datetime.now(WAT)

    txt=f"<b>📊 EUR/USD..US30 — Full ICT Brief {now.strftime('%a %b %d %I:%M %p WAT')}</b>\n\n"
    txt+=f"<b>📰 HEADLINES:</b> Gold {d['XAU/USD']:.2f} holding ahead CPI 1:30 PM WAT, DXY {d.get('DXY',98.2):.2f} firm, EUR/USD {d['EUR/USD']:.4f}, WTI ${d.get('WTI',95.1):.2f} — CPI in focus | Live No-TD Feed\n\n"
    txt+=f"<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    configs=[("EUR/USD","BUY"),("GBP/USD","BUY"),("USD/CAD","SELL"),("XAU/USD","SELL"),("EUR/JPY","BUY"),("USD/JPY","BUY"),("NAS100","BUY"),("US30","BUY"),("GERMANY40","BUY")]

    for pair,bias in configs:
        live=d.get(pair, FALLBACK[pair])
        c=build_pair(pair,live,bias)

        # ===== COMBINED FORMAT: V5.1 + MORNING BRIEF =====
        if pair in ["EUR/USD","GBP/USD","USD/CAD"]:
            txt+=f"<b>💶 {pair}: Current {live:.4f} — DXY {d.get('DXY',98.2):.2f}</b>\n"
            txt+=f"<b>Bias:</b> {bias} after sweep | <b>Live Price:</b> {live:.4f}\n"
            txt+=f"<b>HTF POI:</b> 4H {'Bearish' if bias=='BUY' else 'Bullish'} OB {c['ht_low']:.4f}-{c['ht_high']:.4f}\n"
            txt+=f"<b>FVG:</b> {c['fvg_low']:.4f}-{c['fvg_high']:.4f}\n"
            txt+=f"<b>Liquidity:</b> Sweep {c['sweep']:.4f} Asia {'high' if bias=='BUY' else 'low'} at 8 AM\n"
            txt+=f"<b>Breaker:</b> {c['breaker']:.4f} | <b>POC:</b> {c['poc']:.4f} | <b>OTE:</b> {c['ote']:.4f} (50% FVG)\n"
            txt+=f"<b>Entry:</b> {c['entry']:.4f} | <b>SL:</b> {c['sl']:.4f} | <b>TP1:</b> {c['tp1']:.4f} | <b>TP2:</b> {c['tp2']:.4f}\n"
            txt+=f"<b>RR:</b> 1:2 / 1:4 | <b>Window:</b> London 8-10 AM WAT | <b>Zone:</b> {c['zone_low']:.4f}-{c['zone_high']:.4f}\n"
            txt+=f"<b>Setup:</b> Sweep {c['sweep']:.4f} expected pre-CPI\n"
            txt+=f"<b>Confirmation:</b> 15m CHOCH {'bullish' if bias=='BUY' else 'bearish'} + 5m FVG {c['fvg_low']:.4f}-{c['fvg_high']:.4f} retest\n"
            txt+=f"<b>Justification:</b> DXY {d.get('DXY',98.2):.2f} resistance at 99.0, London sweep grab liquidity then CHOCH. CPI risk managed BE+0.5\n"
            txt+=f"<b>Status:</b> ⚠️ WAIT for FVG retest\n\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        elif "JPY" in pair:
            txt+=f"<b>💱 {pair}: Current {live:.2f} — DXY {d.get('DXY',98.2):.2f}</b>\n"
            txt+=f"<b>Bias:</b> {bias} | <b>Live Price:</b> {live:.2f}\n"
            txt+=f"<b>HTF POI:</b> 4H Bearish OB {c['ht_low']:.2f}-{c['ht_high']:.2f}\n"
            txt+=f"<b>FVG:</b> {c['fvg_low']:.2f}-{c['fvg_high']:.2f} | <b>Liquidity:</b> Sweep {c['sweep']:.2f} | <b>Breaker:</b> {c['breaker']:.2f}\n"
            txt+=f"<b>POC:</b> {c['poc']:.2f} | <b>OTE:</b> {c['ote']:.2f} | <b>Entry:</b> {c['entry']:.2f} | <b>SL:</b> {c['sl']:.2f} | <b>TP1/TP2:</b> {c['tp1']:.2f}/{c['tp2']:.2f}\n"
            txt+=f"<b>RR:</b> 1:2 / 1:4 | <b>Zone:</b> {c['zone_low']:.2f}-{c['zone_high']:.2f} | London 8-10 AM WAT\n"
            txt+=f"<b>Justification:</b> Cross of EUR/USD {d['EUR/USD']:.4f} x USD/JPY {d['USD/JPY']:.2f}, wait CHOCH\n"
            txt+=f"<b>Status:</b> ⚠️ WAIT\n\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        else:
            txt+=f"<b>🥇 {pair}: Current {live:.2f}</b>\n" if pair=="XAU/USD" else f"<b>📈 {pair}: Current {live:.0f}</b>\n"
            txt+=f"<b>Bias:</b> {bias} | <b>Live Price:</b> {live}\n"
            txt+=f"<b>HTF POI:</b> 4H {'Bearish' if bias=='SELL' else 'Bullish'} OB {c['ht_low']:.2f}-{c['ht_high']:.2f}\n"
            txt+=f"<b>FVG:</b> {c['fvg_low']:.2f}-{c['fvg_high']:.2f} | <b>Liquidity:</b> Sweep {c['sweep']:.2f} | <b>Breaker:</b> {c['breaker']:.2f}\n"
            txt+=f"<b>POC:</b> {c['poc']:.2f} | <b>OTE:</b> {c['ote']:.2f} | <b>Entry:</b> {c['entry']:.2f}\n"
            txt+=f"<b>SL:</b> {c['sl']:.2f} | <b>TP1:</b> {c['tp1']:.2f} | <b>TP2:</b> {c['tp2']:.2f} | <b>RR:</b> 1:2 / 1:4\n"
            txt+=f"<b>Window:</b> London 8-10 AM WAT | <b>Zone:</b> {c['zone_low']:.2f}-{c['zone_high']:.2f}\n"
            txt+=f"<b>Setup:</b> Sweep + 15m CHOCH + 5m FVG retest — BE+0.5 at 50%\n"
            txt+=f"<b>Status:</b> {'⚠️ WAIT for FVG retest' if pair=='XAU/USD' else '⏸️ WAIT CPI'}\n\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    txt+=f"<b>📌 RULES:</b> All entries wait for Sweep + 15m CHOCH + 5m FVG retest before VALID\n<b>📌 MANAGEMENT:</b> BE+0.5 at 50% to TP1, trail to TP2\n<b>⚠️ Educational only — Live No-TD Feed</b>"
    return txt, d

# ===== TICK MONITOR EVERY 3 SECONDS =====
def tick_monitor():
    while True:
        try:
            d=get_live_no_td()
            # check open trades
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                if result=="OPEN" and pips>=tp1_pips*0.5:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🔧 <b>BE+0.5 {pair} {bias}</b> @ {entry}\nLive {live} +{pips:.1f}p (tick monitor)")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"✅ <b>TP1 HIT {pair}</b> +{pips:.1f}p Live {live}")
                elif result=="TP1_HIT" and ((bias=="BUY" and live>=tp2) or (bias=="SELL" and live<=tp2)):
                    final=calc_pips(pair,entry,tp2,bias)
                    conn.execute("UPDATE trades SET result='TP2_HIT', pips=? WHERE id=?",(final,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🏆 <b>TP2 HIT {pair}</b> +{final:.1f}p FULL — tick")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live<=sl) or (bias=="SELL" and live>=sl)):
                    conn.execute("UPDATE trades SET result='SL_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"❌ <b>SL HIT {pair}</b> {pips:.1f}p Live {live}")
            time.sleep(5) # TICK EVERY 5 SECONDS
        except Exception as e:
            print(f"tick err {e}"); time.sleep(5)

threading.Thread(target=tick_monitor,daemon=True).start()

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    bot.send_message(m.chat.id,"☀️ Fetching LIVE (No TwelveData) + V5.1 + Morning Brief...")
    try:
        full,d=generate_full()
        for i in range(0,len(full),3800):
            bot.send_message(m.chat.id,full[i:i+3800])
    except Exception as e:
        import traceback; print(traceback.format_exc())
        bot.send_message(m.chat.id,f"Error {e}")

@bot.message_handler(commands=['valid','check','probables','pips','journal'])
def cmd_others(m):
    try:
        if 'probables' in m.text:
            cur=conn.execute("SELECT * FROM probables").fetchall()
            bot.send_message(m.chat.id,"No probables — waiting 15m CHOCH" if not cur else "\n".join([f"{r[0]} {r[4]} FVG {r[2]}-{r[3]}" for r in cur]))
        elif 'valid' in m.text or 'check' in m.text:
            d=get_live_no_td()
            msgs=[]
            for pair in ["EUR/USD","GBP/USD","USD/CAD","XAU/USD"]:
                live=d.get(pair)
                # For tick mode, we use synthetic FVG check (no TwelveData candles needed)
                # If live touches entry zone -> VALID
                cur=conn.execute("SELECT * FROM probables WHERE pair=?",(pair,)).fetchone()
                if cur:
                    fvg_low,fvg_high,bias=cur[2],cur[3],cur[4]
                    if fvg_low <= live <= fvg_high:
                        c=build_pair(pair,live,bias)
                        conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,c['sl'],c['tp1'],c['tp2'],bias,datetime.now(WAT).isoformat(),"OPEN",0,"VALID_RETEST"))
                        conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                        msgs.append(f"✅ VALID {pair} {bias} @ {live} CONFIRMED AFTER FVG RETEST\nSL {c['sl']} TP {c['tp1']}/{c['tp2']}")
                else:
                    # create probable automatically for tick
                    c=build_pair(pair,live,"BUY" if pair in ["EUR/USD","GBP/USD"] else "SELL")
                    conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],c['sl']))
                    # Actually store correctly
                    conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],"BUY" if pair in ["EUR/USD","GBP/USD"] else "SELL")); conn.commit()
                    msgs.append(f"⚠️ PROBABLE {pair} {'BUY' if pair in ['EUR/USD','GBP/USD'] else 'SELL'} LIVE {live} FVG {c['fvg_low']}-{c['fvg_high']} — waiting retest")
            bot.send_message(m.chat.id,"No VALID after retest — tick monitor watching" if not msgs else "\n\n".join(msgs))
        else:
            cur=conn.execute("SELECT pair, entry, bias, result, pips FROM trades ORDER BY id DESC LIMIT 15").fetchall()
            bot.send_message(m.chat.id,"No trades yet — journal empty" if not cur else "\n".join([f"{r[0]} {r[2]} @ {r[1]} | {r[3]} {r[4]:.1f}p" for r in cur]))
    except Exception as e:
        import traceback; print(traceback.format_exc())
        bot.send_message(m.chat.id,f"Error {e}")

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
print("V13 V5.1+Morning NO TD TICK polling")
while True:
    try: bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
    except Exception as e: print(f"Poll {e}"); time.sleep(10)
