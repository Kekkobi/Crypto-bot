import os
import asyncio
import logging
import schedule
import time
import requests
import pandas as pd
import numpy as np
import ta
from datetime import datetime, timezone
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID        = os.environ.get("CHAT_ID")
SEND_HOUR      = 12
SEND_MINUTE    = 30
MIN_SCORE      = 80

sent_signals = {}

SYMBOLS = [
    "BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT","DOGE/USDT","ADA/USDT","AVAX/USDT","LINK/USDT","DOT/USDT","MATIC/USDT","UNI/USDT","LTC/USDT","BCH/USDT","NEAR/USDT","ICP/USDT","APT/USDT","ATOM/USDT","FIL/USDT","ARB/USDT","OP/USDT","AAVE/USDT","GRT/USDT","PEPE/USDT","FLOKI/USDT","BONK/USDT","WIF/USDT","ORDI/USDT","INJ/USDT","SUI/USDT","SEI/USDT","TIA/USDT","JUP/USDT","WLD/USDT","PENDLE/USDT","BLUR/USDT","CFX/USDT","MASK/USDT","GMX/USDT","FTM/USDT","SAND/USDT","MANA/USDT","AXS/USDT","THETA/USDT","EOS/USDT","XLM/USDT","ALGO/USDT","VET/USDT","HBAR/USDT","ROSE/USDT","CHZ/USDT","ENJ/USDT","ZIL/USDT","WAVES/USDT","DASH/USDT","XMR/USDT","COMP/USDT","SNX/USDT","CRV/USDT","1INCH/USDT","SUSHI/USDT","KAVA/USDT","OCEAN/USDT","ANKR/USDT","HOT/USDT","ZRX/USDT","BAND/USDT","SKL/USDT","COTI/USDT","RSR/USDT","CELR/USDT","DENT/USDT","WIN/USDT","FUN/USDT","MOVE/USDT","ENA/USDT","STRK/USDT","PYTH/USDT","MANTA/USDT","ALT/USDT","BOME/USDT","NEIRO/USDT","PNUT/USDT","ACT/USDT","TRUMP/USDT","PENGU/USDT","BERA/USDT","LAYER/USDT","IP/USDT","NOT/USDT","EIGEN/USDT","ETHFI/USDT","W/USDT","ZK/USDT","DOGS/USDT","HMSTR/USDT","CATI/USDT","SCR/USDT","GRASS/USDT","USUAL/USDT","VANA/USDT","SOLV/USDT","ANIME/USDT","INIT/USDT",
]

logging.basicConfig(format="%(asctime)s — %(levelname)s — %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  FETCH DATI
# ══════════════════════════════════════════════
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

def fetch_funding_rate(symbol):
    try:
        import ccxt
        ex = ccxt.binance({"enableRateLimit": True})
        sym = symbol.replace("/","")
        data = ex.fetch_funding_rate(symbol)
        return round(data["fundingRate"] * 100, 4)
    except:
        return None

def fetch_btc_trend():
    try:
        df = fetch_ohlcv("BTC/USDT", "1d", 10)
        if df is None: return "neutro"
        change = (df["close"].iloc[-1] - df["close"].iloc[-3]) / df["close"].iloc[-3] * 100
        if change > 3: return "bullish"
        if change < -3: return "bearish"
        return "neutro"
    except:
        return "neutro"

def fetch_news_sentiment(symbol):
    try:
        name = symbol.replace("/USDT","").lower()
        url = f"https://cryptopanic.com/api/v1/posts/?auth_token=public&currencies={name}&kind=news"
        r = requests.get(url, timeout=8).json()
        results = r.get("results", [])[:10]
        if not results: return 0
        positive = sum(1 for x in results if x.get("votes",{}).get("positive",0) > x.get("votes",{}).get("negative",0))
        negative = sum(1 for x in results if x.get("votes",{}).get("negative",0) > x.get("votes",{}).get("positive",0))
        return round((positive - negative) / max(len(results),1) * 100)
    except:
        return 0


# ══════════════════════════════════════════════
#  ANALISI TECNICA
# ══════════════════════════════════════════════
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
    return {
        "price":c.iloc[-1],"prev":c.iloc[-2],
        "rsi":rsi,"macd":macd,"macd_sig":macd_sig,"macd_hist":macd_hist,
        "bb_upper":bb_upper,"bb_lower":bb_lower,
        "ema50":ema50,"ema200":ema200,
        "stoch_k":stoch_k,"stoch_d":stoch_d,
        "vol_ratio":vol_ratio,
        "closes":c.values,"highs":h.values,"lows":l.values,
    }


# ══════════════════════════════════════════════
#  DIVERGENZE RSI
# ══════════════════════════════════════════════
def detect_rsi_divergence(df):
    try:
        c = df["close"]
        rsi_series = ta.momentum.RSIIndicator(c,14).rsi()
        p1_price = c.iloc[-10]; p2_price = c.iloc[-1]
        p1_rsi = rsi_series.iloc[-10]; p2_rsi = rsi_series.iloc[-1]
        if p2_price > p1_price and p2_rsi < p1_rsi:
            return "bearish"  # prezzo sale, RSI scende
        if p2_price < p1_price and p2_rsi > p1_rsi:
            return "bullish"  # prezzo scende, RSI sale
        return None
    except:
        return None


# ══════════════════════════════════════════════
#  SUPPORTI E RESISTENZE
# ══════════════════════════════════════════════
def find_support_resistance(df, n=20):
    try:
        highs = df["high"].rolling(5, center=True).max()
        lows = df["low"].rolling(5, center=True).min()
        resistance = sorted(highs.dropna().nlargest(3).values, reverse=True)
        support = sorted(lows.dropna().nsmallest(3).values)
        price = df["close"].iloc[-1]
        nearest_res = min([r for r in resistance if r > price], default=None)
        nearest_sup = max([s for s in support if s < price], default=None)
        return {
            "support": round(nearest_sup, 4) if nearest_sup else None,
            "resistance": round(nearest_res, 4) if nearest_res else None,
        }
    except:
        return {"support": None, "resistance": None}


# ══════════════════════════════════════════════
#  RISK/REWARD
# ══════════════════════════════════════════════
def calc_rr(price, tp, sl):
    try:
        reward = abs(tp - price)
        risk = abs(sl - price)
        if risk == 0: return 0
        return round(reward / risk, 2)
    except:
        return 0


# ══════════════════════════════════════════════
#  PATTERN CANDELE
# ══════════════════════════════════════════════
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


# ══════════════════════════════════════════════
#  SCORING MULTI-TIMEFRAME
# ══════════════════════════════════════════════
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

def combined_score(s15m, s1h, s4h, s1d):
    # Peso crescente verso timeframe più lunghi
    return round(s15m*0.1 + s1h*0.2 + s4h*0.3 + s1d*0.4)


# ══════════════════════════════════════════════
#  TARGET PRICE
# ══════════════════════════════════════════════
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


# ══════════════════════════════════════════════
#  SENTIMENT GLOBALE
# ══════════════════════════════════════════════
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


# ══════════════════════════════════════════════
#  UTILITY
# ══════════════════════════════════════════════
def bar(score,length=10):
    f=round(score/100*length)
    return "█"*f+"░"*(length-f)

def fg_emoji(v):
    if v<=25: return "😱 Extreme Fear"
    if v<=45: return "😨 Fear"
    if v<=55: return "😐 Neutral"
    if v<=75: return "😄 Greed"
    return "🤑 Extreme Greed"

def pct_str(now,prev):
    p=(now-prev)/prev*100
    return f"{'▲' if p>=0 else '▼'} {abs(p):.2f}%"


# ══════════════════════════════════════════════
#  MESSAGGIO SEGNALE
# ══════════════════════════════════════════════
def build_signal_msg(sym,t4h,t1d,pats,score,s15m,s1h,s4h,s1d,
                     divergence,sr,funding,news_sent,btc_trend,is_alert=False):
    bullish=score>=50
    tg=compute_targets(t4h,t1d,bullish)
    price=t1d["price"]
    name=sym.replace("/USDT","")
    now=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    direction="STRONG BULLISH 🟢" if bullish else "STRONG BEARISH 🔴"
    sign="+" if bullish else ""
    rr=calc_rr(price,tg["tp1"],tg["sl"])
    header="🚨 ALERT IN TEMPO REALE" if is_alert else "📊 REPORT GIORNALIERO"

    lines=[
        f"{header} — {name}/USDT",
        f"{direction} ({score}% confidence)",
        f"Forza: {bar(score)} {score}%",
        f"",
        f"⏱ Timeframe scores:",
        f"  15m: {s15m}%  |  1h: {s1h}%  |  4h: {s4h}%  |  1d: {s1d}%",
        f"",
        f"💰 Prezzo: ${price:,.4f}  {pct_str(price,t1d['prev'])}",
        f"🎯 Target 4h: ${tg['tp1']:,.4f} ({sign}{tg['pct1']}%)",
        f"🎯 Target 1d: ${tg['tp2']:,.4f} ({sign}{tg['pct2']}%)",
        f"🛑 Stop Loss: ${tg['sl']:,.4f} ({tg['pct_sl']}%)",
        f"⚖️ Risk/Reward: {rr}:1{'  ✅' if rr>=2 else '  ⚠️'}",
        f"",
        f"📐 Indicatori (1d):",
        f"  RSI: {t1d['rsi']:.1f}  |  MACD: {t1d['macd_hist']:.4f}",
        f"  EMA50: {t1d['ema50']:.2f}  |  EMA200: {t1d['ema200']:.2f}",
        f"  Stoch: {t1d['stoch_k']:.1f}/{t1d['stoch_d']:.1f}  |  Vol: {t1d['vol_ratio']:.2f}x",
    ]

    if sr["support"] or sr["resistance"]:
        lines.append(f"")
        lines.append(f"📏 Livelli chiave:")
        if sr["resistance"]: lines.append(f"  Resistenza: ${sr['resistance']:,.4f}")
        if sr["support"]:    lines.append(f"  Supporto:   ${sr['support']:,.4f}")

    if divergence:
        div_txt = "🔺 Divergenza BULLISH (prezzo scende, RSI sale)" if divergence=="bullish" else "🔻 Divergenza BEARISH (prezzo sale, RSI scende)"
        lines += [f"","⚡ {div_txt}"]

    lines += [f"","🕯 Pattern: {', '.join(pats)}"]

    lines += [f"","🌍 Contesto mercato:"]
    lines.append(f"  BTC trend: {btc_trend.upper()}")
    if funding is not None:
        fund_txt = "ipervenduto 🟢" if funding<-0.05 else "ipercomprato 🔴" if funding>0.05 else "neutro"
        lines.append(f"  Funding rate: {funding}% — {fund_txt}")
    if news_sent != 0:
        sent_txt = "positivo 🟢" if news_sent>0 else "negativo 🔴"
        lines.append(f"  News sentiment: {news_sent:+}% — {sent_txt}")

    lines += [f"",f"🕐 {now}"]
    return "\n".join(lines)


def build_summary(fg,gl,n_signals,n_analysed):
    now=datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    lines=[
        f"📊 CRYPTO DAILY REPORT — {now}",
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
    lines+=[f"","Asset analizzati: {n_analysed}",f"Segnali forti (80%+): {n_signals}"]
    if n_signals==0:
        lines+=["","🔕 Nessun segnale forte. Meglio aspettare."]
    return "\n".join(lines)


# ══════════════════════════════════════════════
#  SCANSIONE PRINCIPALE
# ══════════════════════════════════════════════
async def scan(is_daily=False):
    global sent_signals
    logger.info(f"Avvio scansione {'giornaliera' if is_daily else 'oraria'}...")
    bot=Bot(token=TELEGRAM_TOKEN)
    strong=[]
    btc_trend=fetch_btc_trend()

    for sym in SYMBOLS:
        try:
            df15m=fetch_ohlcv(sym,"15m",200)
            df1h =fetch_ohlcv(sym,"1h",200)
            df4h =fetch_ohlcv(sym,"4h",200)
            df1d =fetch_ohlcv(sym,"1d",200)
            if any(x is None for x in [df15m,df1h,df4h,df1d]): continue
            if len(df1d)<55: continue

            t15m=analyse(df15m)
            t1h =analyse(df1h)
            t4h =analyse(df4h)
            t1d =analyse(df1d)

            pats,cpts=candle_score(df1d)
            s15m=score_tf(t15m,0)
            s1h =score_tf(t1h,0)
            s4h =score_tf(t4h,0)
            s1d =score_tf(t1d,cpts)
            score=combined_score(s15m,s1h,s4h,s1d)
            bullish=score>=50
            direction="BULL" if bullish else "BEAR"

            # Filtra per BTC trend
            if btc_trend=="bearish" and bullish: continue
            if btc_trend=="bullish" and not bullish: continue

            is_strong = score>=MIN_SCORE or score<=(100-MIN_SCORE)
            if not is_strong: continue

            # Evita duplicati nelle ultime 24h
            signal_key=f"{sym}_{direction}"
            last_sent=sent_signals.get(signal_key)
            now_ts=time.time()
            if not is_daily and last_sent and (now_ts-last_sent)<86400:
                continue
            sent_signals[signal_key]=now_ts

            # Dati aggiuntivi
            divergence=detect_rsi_divergence(df1d)
            sr=find_support_resistance(df1d)
            funding=fetch_funding_rate(sym)
            news_sent=fetch_news_sentiment(sym)

            # Bonus score per divergenza confermata
            if divergence=="bullish" and bullish: score=min(100,score+5)
            if divergence=="bearish" and not bullish: score=min(100,score+5)

            strong.append({
                "sym":sym,"t4h":t4h,"t1d":t1d,"pats":pats,"score":score,
                "s15m":s15m,"s1h":s1h,"s4h":s4h,"s1d":s1d,
                "divergence":divergence,"sr":sr,"funding":funding,
                "news_sent":news_sent,"btc_trend":btc_trend,
            })
            logger.info(f"  ✅ {sym} score={score}% — SEGNALE FORTE")

        except Exception as e:
            logger.error(f"Errore {sym}: {e}")
            continue

    if is_daily:
        fg=fetch_fear_greed()
        gl=fetch_global()
        summary=build_summary(fg,gl,len(strong),len(SYMBOLS))
        await bot.send_message(chat_id=CHAT_ID,text=summary)
        await asyncio.sleep(1)

    for sig in strong:
        msg=build_signal_msg(
            sig["sym"],sig["t4h"],sig["t1d"],sig["pats"],sig["score"],
            sig["s15m"],sig["s1h"],sig["s4h"],sig["s1d"],
            sig["divergence"],sig["sr"],sig["funding"],
            sig["news_sent"],sig["btc_trend"],
            is_alert=not is_daily
        )
        await bot.send_message(chat_id=CHAT_ID,text=msg)
        await asyncio.sleep(1)

    logger.info(f"Scansione completata. Segnali forti: {len(strong)}")

def daily_job():
    asyncio.run(scan(is_daily=True))

def hourly_job():
    asyncio.run(scan(is_daily=False))

if __name__=="__main__":
    send_time=f"{SEND_HOUR:02d}:{SEND_MINUTE:02d}"
    logger.info(f"Bot avviato — report giornaliero alle {send_time} UTC")
    logger.info(f"Alert in tempo reale ogni ora — soglia {MIN_SCORE}%")
    daily_job()
    schedule.every().day.at(send_time).do(daily_job)
    schedule.every().hour.do(hourly_job)
    while True:
        schedule.run_pending()
        time.sleep(30)
