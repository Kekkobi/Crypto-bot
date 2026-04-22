import asyncio
import logging
import schedule
import time
import requests
import pandas as pd
import ta
from datetime import datetime, timezone
from telegram import Bot
from telegram.constants import ParseMode

TELEGRAM_TOKEN = "IL_TUO_TOKEN_QUI"
CHAT_ID        = "IL_TUO_CHAT_ID_QUI"
SEND_HOUR      = 8
SEND_MINUTE    = 0
MIN_SCORE      = 80

SYMBOLS = [
    "BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT",
    "XRP/USDT","DOGE/USDT","ADA/USDT","AVAX/USDT",
    "MATIC/USDT","LINK/USDT",
]

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_ohlcv(symbol, timeframe, limit=200):
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True})
        raw = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        return df.set_index("ts")
    except Exception as e:
        logger.error(f"Errore fetch {symbol} {timeframe}: {e}")
        return None

def analyse(df):
    c,h,l = df["close"],df["high"],df["low"]
    rsi = ta.momentum.RSIIndicator(c,14).rsi().iloc[-1]
    macd_obj = ta.trend.MACD(c,26,12,9)
    macd = macd_obj.macd().iloc[-1]
    macd_sig = macd_obj.macd_signal().iloc[-1]
    macd_hist = macd_obj.macd_diff().iloc[-1]
    bb = ta.volatility.BollingerBands(c,20,2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    ema50 = ta.trend.EMAIndicator(c,50).ema_indicator().iloc[-1]
    ema200 = ta.trend.EMAIndicator(c,200).ema_indicator().iloc[-1]
    stoch = ta.momentum.StochasticOscillator(h,l,c,14,3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_d = stoch.stoch_signal().iloc[-1]
    vol_sma = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio = df["volume"].iloc[-1]/vol_sma if vol_sma>0 else 1.0
    return {"price":c.iloc[-1],"prev":c.iloc[-2],"rsi":rsi,"macd":macd,
            "macd_sig":macd_sig,"macd_hist":macd_hist,"bb_upper":bb_upper,
            "bb_lower":bb_lower,"ema50":ema50,"ema200":ema200,
            "stoch_k":stoch_k,"stoch_d":stoch_d,"vol_ratio":vol_ratio}

def candle_score(df):
    patterns,pts = [],0
    o,h,l,c = df["open"].iloc[-1],df["high"].iloc[-1],df["low"].iloc[-1],df["close"].iloc[-1]
    o2,c2 = df["open"].iloc[-2],df["close"].iloc[-2]
    o3,c3 = df["open"].iloc[-3],df["close"].iloc[-3]
    body = abs(c-o); rng = h-l or 0.0001
    lw = min(o,c)-l; uw = h-max(o,c)
    if body/rng<0.1: patterns.append("Doji")
    if lw>2*body and uw<body: patterns.append("Hammer"); pts+=8
    if uw>2*body and lw<body: patterns.append("Shooting Star"); pts-=8
    if body/rng>0.85:
        if c>o: patterns.append("Marubozu Bullish"); pts+=7
        else: patterns.append("Marubozu Bearish"); pts-=7
    if c2<o2 and c>o and c>o2 and o<c2: patterns.append("Bullish Engulfing"); pts+=10
    if c2>o2 and c<o and c<o2 and o>c2: patterns.append("Bearish Engulfing"); pts-=10
    if c3<o3 and body/rng<0.3 and c>(o3+c3)/2: patterns.append("Morning Star"); pts+=12
    if c3>o3 and body/rng<0.3 and c<(o3+c3)/2: patterns.append("Evening Star"); pts-=12
    return (patterns if patterns else ["Nessun pattern"]),pts

def score_tf(t, candle_pts):
    s=50
    if t["rsi"]<30: s+=14
    elif t["rsi"]>70: s-=14
    elif t["rsi"]<45: s+=4
    elif t["rsi"]>55: s-=4
    if t["macd"]>t["macd_sig"] and t["macd_hist"]>0: s+=10
    elif t["macd"]<t["macd_sig"] and t["macd_hist"]<0: s-=10
    if t["ema50"]>t["ema200"]: s+=10
    else: s-=10
    if t["price"]<t["bb_lower"]: s+=8
    elif t["price"]>t["bb_upper"]: s-=8
    if t["stoch_k"]<20 and t["stoch_k"]>t["stoch_d"]: s+=8
    elif t["stoch_k"]>80 and t["stoch_k"]<t["stoch_d"]: s-=8
    if t["vol_ratio"]>1.5: s+=5 if s>50 else -5
    s+=candle_pts
    return max(0,min(100,round(s)))

def combined_score(s4h,s1d):
    return round(s4h*0.35+s1d*0.65)

def compute_targets(t4h,t1d,bullish):
    price=t1d["price"]
    atr_4h=abs(t4h["bb_upper"]-t4h["bb_lower"])*0.25
    atr_1d=abs(t1d["bb_upper"]-t1d["bb_lower"])*0.45
    if bullish:
        tp1=round(price+atr_4h,4); tp2=round(price+atr_1d,4); sl=round(price-atr_4h*0.6,4)
    else:
        tp1=round(price-atr_4h,4); tp2=round(price-atr_1d,4); sl=round(price+atr_4h*0.6,4)
    return {"tp1":tp1,"pct1":round((tp1-price)/price*100,2),
            "tp2":tp2,"pct2":round((tp2-price)/price*100,2),
            "sl":sl,"pct_sl":round((sl-price)/price*100,2)}

def fetch_fear_greed():
    try:
        d=requests.get("https://api.alternative.me/fng/?limit=1",timeout=10).json()["data"][0]
        return {"value":int(d["value"]),"label":d["value_classification"]}
    except: return {"value":50,"label":"Neutral"}

def fetch_global():
    try:
        d=requests.get("https://api.coingecko.com/api/v3/global",timeout=10,
                       headers={"accept":"application/json"}).json()["data"]
        return {"btc_dom":round(d["market_cap_percentage"]["btc"],1),
                "eth_dom":round(d["market_cap_percentage"]["eth"],1),
                "mcap":round(d["total_market_cap"]["usd"]/1e12,3),
                "vol24h":round(d["total_volume"]["usd"]/1e9,1),
                "mcap_chg":round(d["market_cap_change_percentage_24h_usd"],2)}
    except: return {}

def bar(score,length=10):
    f=round(score/100*length)
    return "█"*f+"░"*(length-f)

def fg_emoji(v):
    if v<=25: return "Extreme Fear"
    if v<=45: return "Fear"
    if v<=55: return "Neutral"
    if v<=75: return "Greed"
    return "Extreme Greed"

def pct_str(now,prev):
    p=(now-prev)/prev*100
    return f"{'▲' if p>=0 else '▼'} {abs(p):.2f}%"

def build_signal_msg(sym,t4h,t1d,pats,score,s4h,s1d):
    bullish=score>=50
    tg=compute_targets(t4h,t1d,bullish)
    price=t1d["price"]
    name=sym.replace("/USDT","")
    header="STRONG BULLISH" if bullish else "STRONG BEARISH"
    sign="+" if bullish else ""
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines=[
        f"CRYPTO SIGNAL — {name}/USDT",
        f"{'STRONG BULLISH' if bullish else 'STRONG BEARISH'} ({score}% confidence)",
        f"",
        f"4h score: {s4h}%  |  1d score: {s1d}%",
        f"",
        f"Prezzo: ${price:,.4f}  {pct_str(price,t1d['prev'])}",
        f"Target 4h: ${tg['tp1']:,.4f} ({sign}{tg['pct1']}%)",
        f"Target 1d: ${tg['tp2']:,.4f} ({sign}{tg['pct2']}%)",
        f"Stop Loss: ${tg['sl']:,.4f} ({tg['pct_sl']}%)",
        f"Forza: {bar(score)} {score}%",
        f"",
        f"RSI: {t1d['rsi']:.1f}  |  MACD: {t1d['macd_hist']:.4f}",
        f"EMA50: {t1d['ema50']:.2f}  |  EMA200: {t1d['ema200']:.2f}",
        f"Stoch: {t1d['stoch_k']:.1f}/{t1d['stoch_d']:.1f}  |  Vol: {t1d['vol_ratio']:.2f}x",
        f"",
        f"Pattern candele:",
    ]
    for p in pats: lines.append(f"• {p}")
    lines.append(f"\n{now}")
    return "\n".join(lines)

def build_summary(fg,gl,n_signals,n_analysed):
    now=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    lines=[
        f"CRYPTO DAILY REPORT — {now}",
        f"",
        f"Fear & Greed: {fg['value']} — {fg_emoji(fg['value'])}",
    ]
    if gl:
        arrow="▲" if gl["mcap_chg"]>=0 else "▼"
        lines+=[
            f"Market Cap: ${gl['mcap']}T  {arrow}{abs(gl['mcap_chg'])}%",
            f"Volume 24h: ${gl['vol24h']}B",
            f"BTC Dom: {gl['btc_dom']}%  |  ETH Dom: {gl['eth_dom']}%",
        ]
    lines+=[
        f"",
        f"Asset analizzati: {n_analysed}",
        f"Segnali forti (80%+): {n_signals}",
    ]
    if n_signals==0:
        lines+=["","Nessun segnale forte oggi. Meglio aspettare."]
    return "\n".join(lines)

async def run_analysis():
    logger.info("Avvio analisi...")
    bot=Bot(token=TELEGRAM_TOKEN)
    fg=fetch_fear_greed()
    gl=fetch_global()
    strong=[]
    for sym in SYMBOLS:
        logger.info(f"Analizzando {sym}...")
        df4h=fetch_ohlcv(sym,"4h",200)
        df1d=fetch_ohlcv(sym,"1d",200)
        if df4h is None or df1d is None or len(df1d)<55: continue
        t4h=analyse(df4h)
        t1d=analyse(df1d)
        pats,cpts=candle_score(df1d)
        s4h=score_tf(t4h,0)
        s1d=score_tf(t1d,cpts)
        score=combined_score(s4h,s1d)
        if score>=MIN_SCORE or score<=(100-MIN_SCORE):
            strong.append({"sym":sym,"t4h":t4h,"t1d":t1d,"pats":pats,"score":score,"s4h":s4h,"s1d":s1d})
    summary=build_summary(fg,gl,len(strong),len(SYMBOLS))
    await bot.send_message(chat_id=CHAT_ID,text=summary)
    await asyncio.sleep(1)
    for sig in strong:
        msg=build_signal_msg(sig["sym"],sig["t4h"],sig["t1d"],sig["pats"],sig["score"],sig["s4h"],sig["s1d"])
        await bot.send_message(chat_id=CHAT_ID,text=msg)
        await asyncio.sleep(1)
    logger.info(f"Fatto. Segnali: {len(strong)}")

def job():
    asyncio.run(run_analysis())

if __name__=="__main__":
    send_time=f"{SEND_HOUR:02d}:{SEND_MINUTE:02d}"
    logger.info(f"Bot avviato — analisi alle {send_time} UTC ogni giorno")
    job()
    schedule.every().day.at(send_time).do(job)
    while True:
        schedule.run_pending()
        time.sleep(30)
