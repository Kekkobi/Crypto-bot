import os
import asyncio
import logging
import schedule
import time
import requests
import pandas as pd
import ta
from datetime import datetime, timezone
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
SEND_HOUR = 12
SEND_MINUTE = 30
MIN_SCORE = 80
sent_signals = {}

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def get_all_binance_symbols():
    try:
        r = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=15).json()
        symbols = [
            s["baseAsset"] + "/USDT"
            for s in r["symbols"]
            if s["quoteAsset"] == "USDT"
            and s["status"] == "TRADING"
            and s["isSpotTradingAllowed"]
        ]
        logger.info(f"Trovate {len(symbols)} coppie USDT su Binance")
        return symbols
    except Exception as e:
        logger.error(f"Errore fetch simboli: {e}")
        return ["BTC/USDT","ETH/USDT","BNB/USDT","SOL/USDT","XRP/USDT"]

def fetch_commodity(ticker, name):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=6mo"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}).json()
        prices = r["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        timestamps = r["chart"]["result"][0]["timestamp"]
        df = pd.DataFrame({"close": prices, "ts": pd.to_datetime(timestamps, unit="s")})
        df = df.dropna().set_index("ts")
        df["open"] = df["close"].shift(1)
        df["high"] = df["close"] * 1.005
        df["low"] = df["close"] * 0.995
        df["volume"] = 1000000
        return df
    except Exception as e:
        logger.error(f"Errore fetch {name}: {e}")
        return None

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

def fetch_fear_greed():
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10).json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except:
        return {"value": 50, "label": "Neutral"}

def fetch_global():
    try:
        d = requests.get("https://api.coingecko.com/api/v3/global", timeout=10,
                         headers={"accept": "application/json"}).json()["data"]
        return {
            "btc_dom": round(d["market_cap_percentage"]["btc"], 1),
            "eth_dom": round(d["market_cap_percentage"]["eth"], 1),
            "mcap": round(d["total_market_cap"]["usd"] / 1e12, 3),
            "vol24h": round(d["total_volume"]["usd"] / 1e9, 1),
            "mcap_chg": round(d["market_cap_change_percentage_24h_usd"], 2),
        }
    except:
        return {}

def analyse(df):
    c, h, l = df["close"], df["high"], df["low"]
    rsi = ta.momentum.RSIIndicator(c, 14).rsi().iloc[-1]
    macd_obj = ta.trend.MACD(c, 26, 12, 9)
    macd = macd_obj.macd().iloc[-1]
    macd_sig = macd_obj.macd_signal().iloc[-1]
    macd_hist = macd_obj.macd_diff().iloc[-1]
    bb = ta.volatility.BollingerBands(c, 20, 2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    ema50 = ta.trend.EMAIndicator(c, 50).ema_indicator().iloc[-1]
    ema200 = ta.trend.EMAIndicator(c, 200).ema_indicator().iloc[-1]
    stoch = ta.momentum.StochasticOscillator(h, l, c, 14, 3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_d = stoch.stoch_signal().iloc[-1]
    vol_sma = df["volume"].rolling(20).mean().iloc[-1]
    vol_ratio = df["volume"].iloc[-1] / vol_sma if vol_sma > 0 else 1.0
    return {
        "price": c.iloc[-1], "prev": c.iloc[-2],
        "rsi": rsi, "macd": macd, "macd_sig": macd_sig, "macd_hist": macd_hist,
        "bb_upper": bb_upper, "bb_lower": bb_lower,
        "ema50": ema50, "ema200": ema200,
        "stoch_k": stoch_k, "stoch_d": stoch_d,
        "vol_ratio": vol_ratio,
    }

def detect_rsi_divergence(df):
    try:
        c = df["close"]
        rsi_s = ta.momentum.RSIIndicator(c, 14).rsi()
        if len(rsi_s) < 10: return None
        if c.iloc[-1] > c.iloc[-10] and rsi_s.iloc[-1] < rsi_s.iloc[-10]: return "bearish"
        if c.iloc[-1] < c.iloc[-10] and rsi_s.iloc[-1] > rsi_s.iloc[-10]: return "bullish"
        return None
    except:
        return None

def find_sr(df):
    try:
        price = df["close"].iloc[-1]
        highs = df["high"].rolling(5, center=True).max().dropna()
        lows = df["low"].rolling(5, center=True).min().dropna()
        res = min([r for r in highs.nlargest(3).values if r > price], default=None)
        sup = max([s for s in lows.nsmallest(3).values if s < price], default=None)
        return {
            "resistance": round(res, 4) if res else None,
            "support": round(sup, 4) if sup else None,
        }
    except:
        return {"resistance": None, "support": None}

def candle_score(df):
    patterns, pts = [], 0
    o, h, l, c = df["open"].iloc[-1], df["high"].iloc[-1], df["low"].iloc[-1], df["close"].iloc[-1]
    o2, c2 = df["open"].iloc[-2], df["close"].iloc[-2]
    o3, c3 = df["open"].iloc[-3], df["close"].iloc[-3]
    body = abs(c - o)
    rng = h - l or 0.0001
    lw = min(o, c) - l
    uw = h - max(o, c)
    if body / rng < 0.1: patterns.append("Doji")
    if lw > 2 * body and uw < body: patterns.append("Hammer"); pts += 8
    if uw > 2 * body and lw < body: patterns.append("Shooting Star"); pts -= 8
    if body / rng > 0.85:
        if c > o: patterns.append("Marubozu Bullish"); pts += 7
        else: patterns.append("Marubozu Bearish"); pts -= 7
    if c2 < o2 and c > o and c > o2 and o < c2: patterns.append("Bullish Engulfing"); pts += 10
    if c2 > o2 and c < o and c < o2 and o > c2: patterns.append("Bearish Engulfing"); pts -= 10
    if c3 < o3 and body / rng < 0.3 and c > (o3 + c3) / 2: patterns.append("Morning Star"); pts += 12
    if c3 > o3 and body / rng < 0.3 and c < (o3 + c3) / 2: patterns.append("Evening Star"); pts -= 12
    return (patterns if patterns else ["Nessun pattern"]), pts

def score_tf(t, candle_pts):
    s = 50
    if t["rsi"] < 30: s += 14
    elif t["rsi"] > 70: s -= 14
    elif t["rsi"] < 45: s += 4
    elif t["rsi"] > 55: s -= 4
    if t["macd"] > t["macd_sig"] and t["macd_hist"] > 0: s += 10
    elif t["macd"] < t["macd_sig"] and t["macd_hist"] < 0: s -= 10
    if t["ema50"] > t["ema200"]: s += 10
    else: s -= 10
    if t["price"] < t["bb_lower"]: s += 8
    elif t["price"] > t["bb_upper"]: s -= 8
    if t["stoch_k"] < 20 and t["stoch_k"] > t["stoch_d"]: s += 8
    elif t["stoch_k"] > 80 and t["stoch_k"] < t["stoch_d"]: s -= 8
    if t["vol_ratio"] > 1.5: s += 5 if s > 50 else -5
    s += candle_pts
    return max(0, min(100, round(s)))

def combined_score(s1h, s4h, s1d):
    return round(s1h * 0.2 + s4h * 0.35 + s1d * 0.45)

def compute_targets(t4h, t1d, bullish):
    price = t1d["price"]
    atr_4h = abs(t4h["bb_upper"] - t4h["bb_lower"]) * 0.25
    atr_1d = abs(t1d["bb_upper"] - t1d["bb_lower"]) * 0.45
    if bullish:
        tp1 = round(price + atr_4h, 4)
        tp2 = round(price + atr_1d, 4)
        sl = round(price - atr_4h * 0.6, 4)
    else:
        tp1 = round(price - atr_4h, 4)
        tp2 = round(price - atr_1d, 4)
        sl = round(price + atr_4h * 0.6, 4)
    return {
        "tp1": tp1, "pct1": round((tp1 - price) / price * 100, 2),
        "tp2": tp2, "pct2": round((tp2 - price) / price * 100, 2),
        "sl": sl, "pct_sl": round((sl - price) / price * 100, 2),
    }

def bar(score, length=10):
    f = round(score / 100 * length)
    return "█" * f + "░" * (length - f)

def fg_emoji(v):
    if v <= 25: return "Extreme Fear"
    if v <= 45: return "Fear"
    if v <= 55: return "Neutral"
    if v <= 75: return "Greed"
    return "Extreme Greed"

def pct_str(now, prev):
    p = (now - prev) / prev * 100
    return f"{'▲' if p >= 0 else '▼'} {abs(p):.2f}%"

def build_signal_msg(sig, is_alert=False):
    bullish = sig["score"] >= 50
    tg = compute_targets(sig["t4h"], sig["t1d"], bullish)
    price = sig["t1d"]["price"]
    name = sig["sym"].replace("/USDT", "").replace("/USD", "")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    direction = "STRONG BULLISH" if bullish else "STRONG BEARISH"
    emoji = "🟢" if bullish else "🔴"
    sign = "+" if bullish else ""
    rr = round(abs(tg["tp1"] - price) / abs(tg["sl"] - price), 2) if abs(tg["sl"] - price) > 0 else 0
    header = "🚨 ALERT" if is_alert else "📊 SEGNALE"
    if "ORO" in sig["sym"]: tipo = "🥇 ORO"
    elif "PETROLIO" in sig["sym"]: tipo = "🛢 PETROLIO"
    else: tipo = "🪙 CRYPTO"

    msg = f"{header} {tipo} — {name}\n"
    msg += f"{emoji} {direction} ({sig['score']}% confidence)\n"
    msg += f"Forza: {bar(sig['score'])} {sig['score']}%\n\n"
    msg += f"1h: {sig['s1h']}% | 4h: {sig['s4h']}% | 1d: {sig['s1d']}%\n\n"
    msg += f"Prezzo: ${price:,.4f}  {pct_str(price, sig['t1d']['prev'])}\n"
    msg += f"Target 4h: ${tg['tp1']:,.4f} ({sign}{tg['pct1']}%)\n"
    msg += f"Target 1d: ${tg['tp2']:,.4f} ({sign}{tg['pct2']}%)\n"
    msg += f"Stop Loss: ${tg['sl']:,.4f} ({tg['pct_sl']}%)\n"
    msg += f"R/R: {rr}:1 {'✅' if rr >= 2 else '⚠️'}\n\n"
    msg += f"RSI: {sig['t1d']['rsi']:.1f} | MACD: {sig['t1d']['macd_hist']:.4f}\n"
    msg += f"EMA50: {sig['t1d']['ema50']:.2f} | EMA200: {sig['t1d']['ema200']:.2f}\n"
    msg += f"Stoch: {sig['t1d']['stoch_k']:.1f}/{sig['t1d']['stoch_d']:.1f} | Vol: {sig['t1d']['vol_ratio']:.2f}x\n"
    if sig["sr"]["resistance"]: msg += f"\nResistenza: ${sig['sr']['resistance']:,.4f}"
    if sig["sr"]["support"]: msg += f"\nSupporto: ${sig['sr']['support']:,.4f}"
    if sig["div"]: msg += f"\nDivergenza RSI: {sig['div'].upper()}"
    msg += f"\nPattern: {', '.join(sig['pats'])}"
    msg += f"\n\n{now}"
    return msg

def build_summary(fg, gl, n_signals, n_analysed):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    msg = f"📊 CRYPTO DAILY REPORT — {now}\n\n"
    msg += f"Fear & Greed: {fg['value']} — {fg_emoji(fg['value'])}\n"
    if gl:
        arrow = "▲" if gl["mcap_chg"] >= 0 else "▼"
        msg += f"Market Cap: ${gl['mcap']}T  {arrow}{abs(gl['mcap_chg'])}%\n"
        msg += f"Volume 24h: ${gl['vol24h']}B\n"
        msg += f"BTC Dom: {gl['btc_dom']}% | ETH Dom: {gl['eth_dom']}%\n"
    msg += f"\nAsset analizzati: {n_analysed}\n"
    msg += f"Segnali forti (80%+): {n_signals}\n"
    if n_signals == 0:
        msg += "\nNessun segnale forte. Meglio aspettare."
    return msg

async def scan_commodities(bot, is_alert=False):
    commodities = [("GC=F", "ORO/USD"), ("CL=F", "PETROLIO/USD")]
    strong = []
    for ticker, name in commodities:
        try:
            df = fetch_commodity(ticker, name)
            if df is None or len(df) < 55: continue
            t = analyse(df)
            pats, cpts = candle_score(df)
            s = score_tf(t, cpts)
            if s >= MIN_SCORE or s <= (100 - MIN_SCORE):
                signal_key = f"{name}_{'BULL' if s>=50 else 'BEAR'}"
                now_ts = time.time()
                last = sent_signals.get(signal_key)
                if not last or (now_ts - last) >= 86400:
                    sent_signals[signal_key] = now_ts
                    strong.append({
                        "sym": name, "t4h": t, "t1d": t, "pats": pats,
                        "score": s, "s1h": s, "s4h": s, "s1d": s,
                        "div": detect_rsi_divergence(df),
                        "sr": find_sr(df),
                    })
        except Exception as e:
            logger.error(f"Errore commodity {name}: {e}")
    for sig in strong:
        await bot.send_message(chat_id=CHAT_ID, text=build_signal_msg(sig, is_alert))
        await asyncio.sleep(1)
    return len(strong)

async def scan(is_daily=False):
    global sent_signals
    logger.info(f"Avvio scansione {'giornaliera' if is_daily else 'ogni 4 ore'}...")
    bot = Bot(token=TELEGRAM_TOKEN)
    strong = []
    all_symbols = get_all_binance_symbols()

    for sym in all_symbols:
        try:
            df1h = fetch_ohlcv(sym, "1h", 200)
            df4h = fetch_ohlcv(sym, "4h", 200)
            df1d = fetch_ohlcv(sym, "1d", 200)
            if any(x is None for x in [df1h, df4h, df1d]): continue
            if len(df1d) < 55: continue

            t1h = analyse(df1h)
            t4h = analyse(df4h)
            t1d = analyse(df1d)
            pats, cpts = candle_score(df1d)
            s1h = score_tf(t1h, 0)
            s4h = score_tf(t4h, 0)
            s1d = score_tf(t1d, cpts)
            score = combined_score(s1h, s4h, s1d)
            bullish = score >= 50

            if score < MIN_SCORE and score > (100 - MIN_SCORE): continue

            direction = "BULL" if bullish else "BEAR"
            signal_key = f"{sym}_{direction}"
            now_ts = time.time()
            last = sent_signals.get(signal_key)
            if last and (now_ts - last) < 86400: continue
            sent_signals[signal_key] = now_ts

            strong.append({
                "sym": sym, "t4h": t4h, "t1d": t1d, "pats": pats,
                "score": score, "s1h": s1h, "s4h": s4h, "s1d": s1d,
                "div": detect_rsi_divergence(df1d),
                "sr": find_sr(df1d),
            })
            logger.info(f"  SEGNALE {sym} score={score}%")
        except Exception as e:
            logger.error(f"Errore {sym}: {e}")
            continue

    n_commodities = await scan_commodities(bot, is_alert=not is_daily)

    if is_daily:
        fg = fetch_fear_greed()
        gl = fetch_global()
        summary = build_summary(fg, gl, len(strong) + n_commodities, len(all_symbols) + 2)
        await bot.send_message(chat_id=CHAT_ID, text=summary)
        await asyncio.sleep(1)

    for sig in strong:
        await bot.send_message(chat_id=CHAT_ID, text=build_signal_msg(sig, is_alert=not is_daily))
        await asyncio.sleep(1)

    logger.info(f"Completato. Segnali: {len(strong) + n_commodities}")

def daily_job():
    asyncio.run(scan(is_daily=True))

def scan_job():
    asyncio.run(scan(is_daily=False))

if __name__ == "__main__":
    send_time = f"{SEND_HOUR:02d}:{SEND_MINUTE:02d}"
    logger.info(f"Bot avviato - report giornaliero alle {send_time} UTC")
    logger.info(f"Scansione completa ogni 4 ore - soglia {MIN_SCORE}%")
    daily_job()
    schedule.every().day.at(send_time).do(daily_job)
    schedule.every(4).hours.do(scan_job)
    while True:
        schedule.run_pending()
        time.sleep(30)
