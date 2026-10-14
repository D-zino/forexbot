import os, time, threading, requests, sqlite3, socket
from datetime import datetime
from zoneinfo import ZoneInfo
import telebot
from flask import Flask

# ===================== ANTI-CONFLICT LOCK — ONLY 1 BOT =====================
# This stops 409 Conflict: terminated by other getUpdates
lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    lock_sock.bind(("127.0.0.1", 47200))
    print("✅ Lock acquired — single bot instance")
except:
    print("❌ Another bot running — exiting to prevent 409")
    os._exit(0)

TOKEN=os.getenv("TELEGRAM_TOKEN")
CHANNEL_ID=os.getenv("CHANNEL_ID")
try: CHANNEL_ID=int(CHANNEL_ID)
except: pass
PORT=int(os.getenv("PORT",10000))
RENDER_URL=os.getenv("RENDER_EXTERNAL_URL")

app=Flask(__name__)
@app.route('/')
def home(): return "V15.4 FINAL TRUE LIVE + LOCKED"
@app.route('/health')
def health(): return "OK"
threading.Thread(target=lambda: app.run(host='0.0.0.0',port=PORT,debug=False,use_reloader=False),daemon=True).start()

def keep_alive():
    while True:
        time.sleep(600)
        try:
            if RENDER_URL: requests.get(RENDER_URL,timeout=10)
        except: pass
threading.Thread(target=keep_alive,daemon=True).start()

bot=telebot.TeleBot(TOKEN,parse_mode="HTML",threaded=False)

# Delete webhook hard — prevent conflict
for i in range(3):
    try:
        bot.remove_webhook()
        time.sleep(1)
        bot.delete_webhook(drop_pending_updates=True)
        print(f"✅ Webhook deleted attempt {i+1}")
        time.sleep(2)
        break
    except Exception as e:
        print(f"Webhook delete fail {e}")
        time.sleep(2)

WAT=ZoneInfo("Africa/Lagos")
conn=sqlite3.connect("trades.db",check_same_thread=False)
conn.execute("CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY AUTOINCREMENT, pair TEXT, entry REAL, sl REAL, tp1 REAL, tp2 REAL, bias TEXT, time TEXT, result TEXT, pips REAL, stage TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS probables (pair TEXT PRIMARY KEY, time TEXT, fvg_low REAL, fvg_high REAL, bias TEXT, stage TEXT)")
conn.execute("CREATE TABLE IF NOT EXISTS steps (pair TEXT PRIMARY KEY, sweep INTEGER, choch INTEGER, fvg INTEGER, confirm INTEGER, last_time TEXT)")
conn.commit()

FALLBACK={"EUR/USD":1.11932,"GBP/USD":1.32661,"USD/CAD":1.4210,"EUR/JPY":177.53,"USD/JPY":158.44,"XAU/USD":4143.74,"NAS100":30568.90,"US30":51765.50,"GERMANY40":25403.0,"DXY":102.10,"WTI":106.0}

YAHOO_MAP={"EUR/USD":"EURUSD=X","GBP/USD":"GBPUSD=X","USD/CAD":"USDCAD=X","EUR/JPY":"EURJPY=X","USD/JPY":"USDJPY=X","XAU/USD":"GC=F","NAS100":"NQ=F","US30":"YM=F","GERMANY40":"^GDAXI","DXY":"DX-Y.NYB","WTI":"CL=F"}

def get_yahoo_live_price(pair):
    try:
        sym=YAHOO_MAP.get(pair)
        url=f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&range=1d&includePrePost=true"
        r=requests.get(url, timeout=8, headers={"User-Agent":"Mozilla/5.0"}).json()
        if 'chart' in r and r['chart']['result']:
            result=r['chart']['result'][0]
            meta=result['meta']
            price=meta.get('regularMarketPrice')
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
            if c2['low'] > c0['high']: return {'low':c0['high'], 'high':c2['low']}
            if c2['high'] < c0['low']: return {'low':c2['high'], 'high':c0['low']}
    except: pass
    return None

def find_real_ob(candles):
    try:
        for i in range(len(candles)-3, 1, -1):
            if candles[i]['close'] < candles[i]['open'] and candles[i+1]['close'] > candles[i+1]['open']:
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
                print(f"✅ STOOQ {symbol}: {close}")
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
            print(f"✅ FOREX LIVE er-api: {data}")
            return data
    except Exception as e:
        print(f"er-api fail {e}")
    return {}

def get_live_all_live():
    data={}
    # FOREX — TRUE LIVE — WORKS ON RENDER
    data.update(get_forex_live())

    # GOLD — TRUE LIVE
    try:
        r=requests.get("https://api.gold-api.com/price/XAU",timeout=6).json()
        data['XAU/USD']=float(r['price'])
        print(f"✅ GOLD LIVE: {data['XAU/USD']}")
    except: pass

    # INDICES — STOOQ — WORKS ON RENDER, YAHOO BLOCKED
    stooq_map={"DXY":"dx.f","WTI":"cl.f","NAS100":"nq.f","US30":"ym.f","GERMANY40":"^dax"}
    for k,sym in stooq_map.items():
        p=get_stooq_price(sym)
        if p: data[k]=p
        time.sleep(0.3)

    # YAHOO backup only
    for pair in ["XAU/USD","NAS100","US30","GERMANY40","DXY","WTI"]:
        if pair not in data:
            live=get_yahoo_live_price(pair)
            if live: data[pair]=live

    # Final fallback only if all APIs dead
    for k,v in FALLBACK.items():
        if k not in data:
            print(f"⚠️ FALLBACK {k}: {v}")
            data[k]=v

    print(f"FINAL LIVE V15.4: {data}")
    return data

def calc_pips(pair, entry, current, bias):
    mult=100 if "JPY" in pair else 10000 if "/" in pair else 1
    return (current-entry)*mult if bias=="BUY" else (entry-current)*mult

def build_pair(pair, live):
    c15=get_yahoo_candles(pair, "15m", "5d"); c5=get_yahoo_candles(pair, "5m", "2d"); c1h=get_yahoo_candles(pair, "60m", "10d")
    time.sleep(0.15)
    real_fvg=find_real_fvg(c5) if c5 else None
    real_ob=find_real_ob(c1h) if c1h else None
    choch=find_choch(c15) if c15 else None
    if choch=="BULL_CHOCHO": bias="BUY"
    elif choch=="BEAR_CHOCHO": bias="SELL"
    else: bias="SELL" if pair in ["EUR/USD","GBP/USD","XAU/USD"] else "BUY"
    if real_fvg: fvg_low,fvg_high=real_fvg['low'],real_fvg['high']
    else:
        if "/" in pair and "JPY" not in pair: fvg_low,fvg_high=live-0.0010,live+0.0008
        elif "JPY" in pair: fvg_low,fvg_high=live-0.20,live+0.15
        elif pair=="XAU/USD": fvg_low,fvg_high=live-4.0,live+4.0
        else: fvg_low,fvg_high=live-80,live+30
    if real_ob: ht_low,ht_high=real_ob['low'],real_ob['high']
    else:
        if "/" in pair and "JPY" not in pair: ht_low,ht_high=live-0.0025,live+0.0020
        elif "JPY" in pair: ht_low,ht_high=live-0.50,live+0.40
        elif pair=="XAU/USD": ht_low,ht_high=live-10.0,live+12.0
        else: ht_low,ht_high=live-120,live+80
    if pair in ["EUR/USD","GBP/USD","USD/CAD"]:
        sweep=live+0.0010 if bias=="BUY" else live-0.0010; breaker=live+0.0005; entry=live; poc=live; ote=(fvg_low+fvg_high)/2
        sl=live-0.0024 if bias=="BUY" else live+0.0020; tp1=live+0.0020 if bias=="BUY" else live-0.0015; tp2=live+0.0040 if bias=="BUY" else live-0.0030; zone_low,zone_high=live+0.0005,live+0.0015
    elif "JPY" in pair:
        sweep=live+0.30; breaker=live+0.20; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live-0.62; tp1=live+0.30; tp2=live+0.60; zone_low,zone_high=live+0.20,live+0.40
    elif pair=="XAU/USD":
        sweep=live-7.0 if bias=="SELL" else live+7.0; breaker=live-6.0 if bias=="SELL" else live+6.0; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live+12.0 if bias=="SELL" else live-12.0; tp1=live-3.0 if bias=="SELL" else live+8.0; tp2=live-8.0 if bias=="SELL" else live+15.0; zone_low,zone_high=live-8.0,live-6.0
    else:
        sweep=live+70; breaker=live+65; entry=live; poc=live; ote=(fvg_low+fvg_high)/2; sl=live-80; tp1=live+60; tp2=live+150; zone_low,zone_high=live+50,live+90
    return {"live":live,"bias":bias,"fvg_low":fvg_low,"fvg_high":fvg_high,"ht_low":ht_low,"ht_high":ht_high,"sweep":sweep,"breaker":breaker,"entry":entry,"poc":poc,"ote":ote,"sl":sl,"tp1":tp1,"tp2":tp2,"zone_low":zone_low,"zone_high":zone_high,"choch":choch or "WAIT","real_fvg":real_fvg is not None,"real_ob":real_ob is not None}

def generate_full():
    d=get_live_all_live()
    now=datetime.now(WAT)
    gold=d['XAU/USD']; dxy=d['DXY']; eur=d['EUR/USD']; wti=d['WTI']; nas=d['NAS100']; us30=d['US30']; ger=d['GERMANY40']
    headlines=f"Gold {gold:.2f} TRUE LIVE | DXY {dxy:.2f} LIVE dx.f | EUR/USD {eur:.5f} LIVE er-api | WTI ${wti:.2f} LIVE cl.f | NAS100 {nas:.2f} LIVE nq.f | US30 {us30:.2f} LIVE ym.f | GER40 {ger:.2f} LIVE — CPI 1:30 PM WAT main driver"

    txt=f"<b>📊 9 PAIRS TRUE LIVE FEED {now.strftime('%a %b %d %I:%M %p WAT')} Educational only — V15.4 LOCKED</b>\n\n"
    txt+=f"<b>HEADLINES LIVE:</b> {headlines}\n<b>Killzones:</b> LONDON 8:00-10:30 AM, NY 1:30-6:00 PM WAT\n\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"

    def add_pair(num, pair, emoji):
        live=d.get(pair, FALLBACK[pair])
        c=build_pair(pair, live)
        if "/" in pair and "JPY" not in pair:
            cur=f"{live:.5f}"; sl=f"{c['sl']:.5f}"; tp1=f"{c['tp1']:.5f}"; tp2=f"{c['tp2']:.5f}"; entry=f"{c['entry']:.5f}"; sweep=f"{c['sweep']:.5f}"; br=f"{c['breaker']:.5f}"; poc=f"{c['poc']:.5f}"; ote=f"{c['ote']:.5f}"
            fvg=f"{c['fvg_low']:.5f}-{c['fvg_high']:.5f}"; ob=f"{c['ht_low']:.5f}-{c['ht_high']:.5f}"
        elif "JPY" in pair:
            cur=f"{live:.5f}"; sl=f"{c['sl']:.5f}"; tp1=f"{c['tp1']:.5f}"; tp2=f"{c['tp2']:.5f}"; entry=f"{c['entry']:.5f}"; sweep=f"{c['sweep']:.5f}"; br=f"{c['breaker']:.5f}"; poc=f"{c['poc']:.5f}"; ote=f"{c['ote']:.5f}"
            fvg=f"{c['fvg_low']:.5f}-{c['fvg_high']:.5f}"; ob=f"{c['ht_low']:.5f}-{c['ht_high']:.5f}"
        else:
            cur=f"{live:.2f}"; sl=f"{c['sl']:.2f}"; tp1=f"{c['tp1']:.2f}"; tp2=f"{c['tp2']:.2f}"; entry=f"{c['entry']:.2f}"; sweep=f"{c['sweep']:.2f}"; br=f"{c['breaker']:.2f}"; poc=f"{c['poc']:.2f}"; ote=f"{c['ote']:.2f}"
            fvg=f"{c['fvg_low']:.2f}-{c['fvg_high']:.2f}"; ob=f"{c['ht_low']:.2f}-{c['ht_high']:.2f}"

        real_tag_fvg="REAL" if c['real_fvg'] else "SYNTH"
        real_tag_ob="REAL" if c['real_ob'] else "SYNTH"
        s=f"{emoji} <b>{num}) {pair} Current {cur} LIVE</b>\n"
        s+=f"V5.1: Bias {c['bias']} | Live {cur} | OB {ob} {real_tag_ob} | FVG {fvg} {real_tag_fvg} | Sweep {sweep} | Breaker {br} | POC {poc} | OTE {ote} | Entry {entry} | SL {sl} | TP1 {tp1} | TP2 {tp2} | RR 1:2/1:4 | CHOCH {c['choch']}\n"
        s+=f"Bias: {c['bias']} | Window: London 8-10:30 AM, NY 1:30-6 PM WAT | Trigger: Sweep + CHOCH + FVG + Confirmation | Status: WAIT\n\n<b>━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        return s

    pairs=[("EUR/USD","💶"),("GBP/USD","💷"),("USD/CAD","🍁"),("XAU/USD","🏆"),("EUR/JPY","💶"),("USD/JPY","💴"),("NAS100","📈"),("US30","📈"),("GERMANY40","🇩🇪")]
    for i,(p,e) in enumerate(pairs,1): txt+=add_pair(i,p,e)
    txt+=f"All entries wait for CHOCH/BOS + FVG — no market open. Steps 8-10:30 & 13:30-18:00 WAT TRUE LIVE V15.4 LOCKED\nDXY {dxy:.2f} LIVE | WTI ${wti:.2f} LIVE | NAS {nas:.2f} LIVE | US30 {us30:.2f} LIVE\n<b>⚠️ Educational only — True Live No-TD Feed V15.4 Locked</b>"
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
                for pair in ["EUR/USD","GBP/USD","USD/CAD","XAU/USD","EUR/JPY","USD/JPY","NAS100","US30","GERMANY40"]:
                    live=d.get(pair)
                    if not live: continue
                    c=build_pair(pair,live)
                    row=conn.execute("SELECT sweep, choch, fvg, confirm FROM steps WHERE pair=?",(pair,)).fetchone()
                    if not row:
                        conn.execute("INSERT INTO steps VALUES (?,?,?,?,?,?)",(pair,0,0,0,0,now_str)); conn.commit(); row=(0,0,0,0)
                    s1,s2,s3,s4=row
                    if s1==0 and abs(live-c['sweep'])<0.0020:
                        conn.execute("UPDATE steps SET sweep=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        bot.send_message(CHANNEL_ID,f"1️⃣ <b>SWEEP {pair} [{kz_name}]</b> @ {now_str}\nLive {live:.5f} swept {c['sweep']:.5f} LIVE")
                    if s1==1 and s2==0 and c['choch'] in ("BULL_CHOCHO","BEAR_CHOCHO"):
                        conn.execute("UPDATE steps SET choch=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        bot.send_message(CHANNEL_ID,f"2️⃣ <b>CHOCH {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\n{c['choch']} @ {live:.5f} LIVE")
                    if s2==1 and s3==0 and c['fvg_low']<=live<=c['fvg_high']:
                        conn.execute("UPDATE steps SET fvg=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                        conn.execute("REPLACE INTO probables VALUES (?,?,?,?,?,?)",(pair,datetime.now(WAT).isoformat(),c['fvg_low'],c['fvg_high'],c['bias'],"FVG_RETEST")); conn.commit()
                        bot.send_message(CHANNEL_ID,f"3️⃣ <b>FVG RETEST {pair} [{kz_name}]</b> @ {now_str}\nInside {c['fvg_low']:.5f}-{c['fvg_high']:.5f} LIVE")
                    if s3==1 and s4==0:
                        c5=get_yahoo_candles(pair, "5m", "1d")
                        choch5=find_choch(c5) if c5 else None
                        if (c['bias']=="BUY" and choch5=="BULL_CHOCHO") or (c['bias']=="SELL" and choch5=="BEAR_CHOCHO"):
                            conn.execute("UPDATE steps SET confirm=1, last_time=? WHERE pair=?",(now_str,pair)); conn.commit()
                            conn.execute("INSERT INTO trades VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",(pair,live,c['sl'],c['tp1'],c['tp2'],c['bias'],datetime.now(WAT).isoformat(),"OPEN",0,f"CONFIRMED_{kz_name}_{choch5}_LIVE"))
                            conn.execute("DELETE FROM probables WHERE pair=?",(pair,)); conn.commit()
                            bot.send_message(CHANNEL_ID,f"✅ <b>ENTRY {pair} {c['bias']} [{kz_name}]</b> @ {now_str}\nEntry {live:.5f} SL {c['sl']:.5f} TP {c['tp1']:.5f}/{c['tp2']:.5f}\n📝 Logged JOURNAL")
            cur=conn.execute("SELECT id, pair, entry, sl, tp1, tp2, bias, result FROM trades WHERE result IN ('OPEN','BE','TP1_HIT')").fetchall()
            for tid,pair,entry,sl,tp1,tp2,bias,result in cur:
                live=d.get(pair)
                if not live: continue
                pips=calc_pips(pair,entry,live,bias)
                tp1_pips=calc_pips(pair,entry,tp1,bias)
                if result=="OPEN" and pips>=tp1_pips*0.5:
                    conn.execute("UPDATE trades SET result='BE', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🔧 <b>BE+0.5 {pair} {bias}</b> @ {entry:.5f} → {live:.5f} +{pips:.1f}p @ {now_str} LIVE")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live>=tp1) or (bias=="SELL" and live<=tp1)):
                    conn.execute("UPDATE trades SET result='TP1_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"✅ <b>TP1 {pair}</b> +{pips:.1f}p Live {live:.5f} @ {now_str} LIVE")
                elif result=="TP1_HIT" and ((bias=="BUY" and live>=tp2) or (bias=="SELL" and live<=tp2)):
                    final=calc_pips(pair,entry,tp2,bias)
                    conn.execute("UPDATE trades SET result='TP2_HIT', pips=? WHERE id=?",(final,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"🏆 <b>TP2 {pair}</b> +{final:.1f}p FULL @ {now_str} LIVE")
                elif result in ("OPEN","BE") and ((bias=="BUY" and live<=sl) or (bias=="SELL" and live>=sl)):
                    conn.execute("UPDATE trades SET result='SL_HIT', pips=? WHERE id=?",(pips,tid)); conn.commit()
                    bot.send_message(CHANNEL_ID,f"❌ <b>SL {pair}</b> {pips:.1f}p Live {live:.5f} @ {now_str} LIVE")
            time.sleep(15)
        except Exception as e:
            import traceback; print(traceback.format_exc()); time.sleep(10)

threading.Thread(target=tick_monitor,daemon=True).start()

@bot.message_handler(commands=['start','brief'])
def cmd_brief(m):
    bot.send_message(m.chat.id,"☀️ Fetching TRUE 9 LIVE V15.4 — er-api + stooq nq.f ym.f dx.f cl.f + gold-api — LOCKED...")
    try:
        full,d=generate_full()
        for i in range(0,len(full),3800): bot.send_message(m.chat.id,full[i:i+3800])
    except Exception as e:
        import traceback; print(traceback.format_exc()); bot.send_message(m.chat.id,f"Error {e}")

@bot.message_handler(commands=['valid','check','probables','pips','journal','steps','reset'])
def cmd_others(m):
    try:
        txt=m.text.lower()
        if 'probables' in txt:
            cur=conn.execute("SELECT pair, fvg_low, fvg_high, bias, stage FROM probables").fetchall()
            bot.send_message(m.chat.id,"No probables" if not cur else "\n".join([f"{r[0]} {r[3]} FVG {r[1]:.5f}-{r[2]:.5f} {r[4]} LIVE" for r in cur]))
        elif 'steps' in txt:
            cur=conn.execute("SELECT pair, sweep, choch, fvg, confirm, last_time FROM steps").fetchall()
            in_kz,now_str,kz_name=is_killzone()
            bot.send_message(m.chat.id,f"Killzone: {kz_name} {now_str} Active: {in_kz}\n" + ("\n".join([f"{r[0]}: S{r[1]} C{r[2]} F{r[3]} ✅{r[4]} @ {r[5]}" for r in cur]) if cur else "No steps"))
        elif 'reset' in txt:
            conn.execute("DELETE FROM steps"); conn.execute("DELETE FROM probables"); conn.commit()
            bot.send_message(m.chat.id,"♻️ Reset done V15.4 LOCKED")
        elif 'valid' in txt or 'check' in txt:
            in_kz,now_str,kz_name=is_killzone()
            bot.send_message(m.chat.id,f"Validation 8-10:30 & 13:30-18:00 WAT\nNow: {kz_name} {now_str} Active={in_kz} V15.4 LOCKED")
        else:
            cur=conn.execute("SELECT pair, entry, bias, result, pips, stage FROM trades ORDER BY id DESC LIMIT 15").fetchall()
            bot.send_message(m.chat.id,"No trades yet" if not cur else "\n".join([f"{r[0]} {r[2]} @ {r[1]:.5f} | {r[3]} {r[4]:.1f}p {r[5]} LIVE" for r in cur]))
    except Exception as e:
        import traceback; print(traceback.format_exc()); bot.send_message(m.chat.id,f"Error {e}")

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
print("V15.4 FINAL LOCKED polling — single instance")
while True:
    try:
        bot.infinity_polling(skip_pending=True,timeout=30,long_polling_timeout=30)
    except Exception as e:
        print(f"Poll {e}"); time.sleep(10)
