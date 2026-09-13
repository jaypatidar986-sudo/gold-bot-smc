import requests, time, os
import pandas as pd
from datetime import datetime, timezone

TG = os.environ.get("TG_TOKEN")
CI = os.environ.get("TG_CHAT")
DK = os.environ.get("TD_KEY")

def get_session():
    h = datetime.now(timezone.utc).hour
    if h >= 22 or h < 4:
        return "MORNING"
    if 8 <= h < 12:
        return "LONDON"
    if 12 <= h < 17:
        return "NY_OVERLAP"
    if 17 <= h < 21:
        return "NY"
    return "ASIAN"

def is_news_time():
    now = datetime.now(timezone.utc)
    h = now.hour
    m = now.minute
    dow = now.weekday()
    if dow == 4 and h == 12 and 25 <= m <= 35:
        return "NFP"
    if dow in [0, 1, 2, 3, 4] and h == 18 and 55 <= m <= 65:
        return "FED"
    if dow in [0, 1, 2, 3, 4] and h == 12 and 25 <= m <= 35:
        return "CPI"
    return None

def is_pre_news():
    now = datetime.now(timezone.utc)
    h = now.hour
    m = now.minute
    dow = now.weekday()
    if dow == 4 and h == 12 and 10 <= m <= 24:
        return "NFP in " + str(25 - m) + " min"
    if dow in [0, 1, 2, 3, 4] and h == 18 and 40 <= m <= 54:
        return "FED in " + str(55 - m) + " min"
    if dow in [0, 1, 2, 3, 4] and h == 12 and 10 <= m <= 24:
        return "CPI in " + str(25 - m) + " min"
    return None

def get_news_day():
    now = datetime.now(timezone.utc)
    dow = now.weekday()
    d = now.day
    w = []
    if dow == 4 and 1 <= d <= 7:
        w.append("NFP Friday")
    if dow == 2 and 10 <= d <= 14:
        w.append("CPI Day")
    if dow == 3 and 15 <= d <= 21:
        w.append("FOMC Possible")
    if dow == 4:
        w.append("Friday Weekend Close")
    return w

def fetch(i, s):
    u = "https://api.twelvedata.com/time_series"
    p = {"symbol": "XAU/USD", "interval": i, "outputsize": s, "apikey": DK}
    r = requests.get(u, params=p, timeout=30).json()
    if "values" not in r:
        return None
    d = pd.DataFrame(r["values"])
    d["dt"] = pd.to_datetime(d["datetime"])
    for c in ["open", "high", "low", "close"]:
        d[c] = d[c].astype(float)
    return d.sort_values("dt").reset_index(drop=True)

def ema(a, p):
    k = 2.0 / (p + 1)
    e = a[0]
    for x in a[1:]:
        e = x * k + e * (1 - k)
    return e

def sma(a, p):
    if len(a) < p:
        return a[-1]
    return sum(a[-p:]) / p

def rsi(a, p=14):
    g = 0
    l = 0
    for i in range(len(a) - p, len(a)):
        d = a[i] - a[i - 1]
        if d > 0:
            g += d
        else:
            l -= d
    if l == 0:
        return 100
    return 100 - 100 / (1 + (g / p) / (l / p))

def atr(df, p=14):
    if len(df) < p + 1:
        return 0
    tr = []
    for i in range(len(df) - p, len(df)):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        pc = df["close"].iloc[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(tr) / p

def swings(df, w=3):
    H = []
    L = []
    for i in range(w, len(df) - w):
        if df["high"].iloc[i] == df["high"].iloc[i-w:i+w+1].max():
            H.append(df["high"].iloc[i])
        if df["low"].iloc[i] == df["low"].iloc[i-w:i+w+1].min():
            L.append(df["low"].iloc[i])
    return H, L

def find_fvg(df):
    out = []
    for i in range(2, len(df)):
        c1h = df["high"].iloc[i-2]
        c1l = df["low"].iloc[i-2]
        c3h = df["high"].iloc[i]
        c3l = df["low"].iloc[i]
        if c1h < c3l:
            out.append({"t": "B", "ce": (c1h + c3l) / 2})
        if c1l > c3h:
            out.append({"t": "S", "ce": (c1l + c3h) / 2})
    return out

def find_ob(df):
    b_ob = None
    s_ob = None
    for i in range(len(df) - 3, max(len(df) - 30, 0), -1):
        c = df.iloc[i]
        n1 = df.iloc[i + 1]
        n2 = df.iloc[i + 2]
        if b_ob is None and c["close"] < c["open"] and n1["close"] > n1["open"] and n2["close"] > n2["open"]:
            b_ob = {"top": c["open"], "bot": c["close"]}
        if s_ob is None and c["close"] > c["open"] and n1["close"] < n1["open"] and n2["close"] < n2["open"]:
            s_ob = {"top": c["close"], "bot": c["open"]}
    return b_ob, s_ob

def check_choch(df):
    H, L = swings(df)
    if len(H) < 3 or len(L) < 3:
        return None
    if H[-1] > H[-2] and L[-1] > L[-2]:
        return "BULL"
    if H[-1] < H[-2] and L[-1] < L[-2]:
        return "BEAR"
    return None

def check_displacement(df, atr_val):
    if len(df) < 3 or atr_val == 0:
        return None
    c = df.iloc[-1]
    body = abs(c["close"] - c["open"])
    if body > atr_val * 1.5:
        if c["close"] > c["open"]:
            return "BULL"
        else:
            return "BEAR"
    return None

def check_equal_levels(levels, tol=5):
    if len(levels) < 2:
        return False
    for i in range(len(levels) - 1):
        if abs(levels[i] - levels[i + 1]) < tol:
            return True
    return False

def run():
    news = is_news_time()
    if news:
        print("News time: " + news + " - skip")
        return

    pre = is_pre_news()
    session = get_session()
    news_days = get_news_day()

    data = {}
    tfs = [("daily", "1day", 200), ("h4", "4h", 200), ("h1", "1h", 250),
           ("m15", "15min", 200), ("m5", "5min", 200), ("m1", "1min", 200)]
    for n, i, s in tfs:
        data[n] = fetch(i, s)
        time.sleep(8)

    if any(v is None for v in data.values()):
        print("Fetch failed")
        return

    daily = data["daily"]
    h4 = data["h4"]
    h1 = data["h1"]
    m15 = data["m15"]
    m5 = data["m5"]
    m1 = data["m1"]
    cur = m5["close"].iloc[-1]

    bull = []
    bear = []

    d_hi = daily["high"].max()
    d_lo = daily["low"].min()
    d_rng = d_hi - d_lo
    d_mid = (d_hi + d_lo) / 2
    lvl25 = d_lo + d_rng * 0.25
    lvl618 = d_lo + d_rng * 0.618
    lvl786 = d_lo + d_rng * 0.786
    lvl75 = d_lo + d_rng * 0.75
    if cur > d_mid:
        bull.append("Above Daily 50%")
    else:
        bear.append("Below Daily 50%")
    if cur < lvl25:
        bull.append("Deep Discount")
    if cur > lvl75:
        bear.append("Deep Premium")
    if lvl618 <= cur <= lvl786:
        bear.append("At Fib 0.618-0.786")

    adr = (daily.tail(30)["high"] - daily.tail(30)["low"]).mean()
    atr_h1 = atr(h1)

    if h4.iloc[-1]["close"] > h4["high"].iloc[-12:-1].max():
        bull.append("4H BOS UP")
    if h4.iloc[-1]["close"] < h4["low"].iloc[-12:-1].min():
        bear.append("4H BOS DN")

    if h1.iloc[-1]["close"] > h1["high"].iloc[-15:-1].max():
        bull.append("1H MSS UP")
    if h1.iloc[-1]["close"] < h1["low"].iloc[-15:-1].min():
        bear.append("1H MSS DN")

    if m15.iloc[-1]["close"] > m15["high"].iloc[-10:-1].max():
        bull.append("15M MSS UP")
    if m15.iloc[-1]["close"] < m15["low"].iloc[-10:-1].min():
        bear.append("15M MSS DN")

    choch_h1 = check_choch(h1)
    if choch_h1 == "BULL":
        bull.append("1H CHoCH Bull")
    if choch_h1 == "BEAR":
        bear.append("1H CHoCH Bear")

    choch_h4 = check_choch(h4)
    if choch_h4 == "BULL":
        bull.append("4H CHoCH Bull")
    if choch_h4 == "BEAR":
        bear.append("4H CHoCH Bear")

    disp = check_displacement(h1, atr_h1)
    if disp == "BULL":
        bull.append("Bull Displacement")
    if disp == "BEAR":
        bear.append("Bear Displacement")

    c1_m5 = m5.iloc[-1]
    c2_m5 = m5.iloc[-2]
    body_m5 = abs(c1_m5["close"] - c1_m5["open"])
    if c1_m5["close"] > c1_m5["open"] and body_m5 > adr * 0.05:
        bull.append("5M Bull Candle")
    if c1_m5["close"] < c1_m5["open"] and body_m5 > adr * 0.05:
        bear.append("5M Bear Candle")
    if c2_m5["close"] < c2_m5["open"] and c1_m5["close"] > c1_m5["open"] and c1_m5["close"] > c2_m5["open"]:
        bull.append("5M Bull Engulf")
    if c2_m5["close"] > c2_m5["open"] and c1_m5["close"] < c1_m5["open"] and c1_m5["close"] < c2_m5["open"]:
        bear.append("5M Bear Engulf")

    fvgs = find_fvg(m15)[-15:]
    nb = [f for f in fvgs if f["t"] == "B" and abs(cur - f["ce"]) < adr * 0.3]
    ns = [f for f in fvgs if f["t"] == "S" and abs(cur - f["ce"]) < adr * 0.3]
    if nb:
        bull.append("Bull FVG @ " + str(round(nb[0]["ce"], 2)))
    if ns:
        bear.append("Bear FVG @ " + str(round(ns[0]["ce"], 2)))

    b_ob, s_ob = find_ob(h1)
    if b_ob and b_ob["bot"] - 5 <= cur <= b_ob["top"] + 5:
        bull.append("At Bull OB")
    if s_ob and s_ob["bot"] - 5 <= cur <= s_ob["top"] + 5:
        bear.append("At Bear OB")

    H1, L1 = swings(h1)
    bsl = sorted(H1, reverse=True)[:5]
    ssl = sorted(L1)[:5]
    nB = next((p for p in bsl if p > cur), None)
    nS = next((p for p in ssl if p < cur), None)
    if nB and nB - cur < adr * 0.3:
        bear.append("Near BSL " + str(round(nB, 2)))
    if nS and cur - nS < adr * 0.3:
        bull.append("Near SSL " + str(round(nS, 2)))

    if check_equal_levels(L1):
        bull.append("Equal Lows")
    if check_equal_levels(H1):
        bear.append("Equal Highs")

    if len(H1) > 1 and cur > H1[-2] and cur < H1[-1]:
        bear.append("Above Inducement")
    if len(L1) > 1 and cur < L1[-2] and cur > L1[-1]:
        bull.append("Below Inducement")

    closes = h1["close"].tolist()
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)
    if cur > e50 and e50 > e200:
        bull.append("EMA50>200 UP")
    if cur < e50 and e50 < e200:
        bear.append("EMA50<200 DN")
    if cur > e200:
        bull.append("Above EMA200")
    else:
        bear.append("Below EMA200")

    r = rsi(closes)
    if r < 30:
        bull.append("RSI Oversold " + str(round(r, 1)))
    if r > 70:
        bear.append("RSI Overbought " + str(round(r, 1)))
    if 50 < r < 70:
        bull.append("RSI Bullish " + str(round(r, 1)))
    if 30 < r < 50:
        bear.append("RSI Bearish " + str(round(r, 1)))

    m_now = ema(closes[-50:], 12) - ema(closes[-50:], 26)
    m_prev = ema(closes[-51:-1], 12) - ema(closes[-51:-1], 26)
    if m_now > 0 and m_prev <= 0:
        bull.append("MACD Cross UP")
    if m_now < 0 and m_prev >= 0:
        bear.append("MACD Cross DN")
    if m_now > 0:
        bull.append("MACD Positive")
    else:
        bear.append("MACD Negative")

    ma20 = sma(closes, 20)
    std20 = (sum([(x - ma20) ** 2 for x in closes[-20:]]) / 20) ** 0.5
    bb_up = ma20 + 2 * std20
    bb_dn = ma20 - 2 * std20
    if cur <= bb_dn:
        bull.append("Below BB Lower")
    if cur >= bb_up:
        bear.append("Above BB Upper")

    vwap_num = 0
    vwap_den = 0
    for i in range(max(0, len(h1) - 50), len(h1)):
        tp = (h1["high"].iloc[i] + h1["low"].iloc[i] + h1["close"].iloc[i]) / 3
        vwap_num += tp
        vwap_den += 1
    vwap = vwap_num / vwap_den if vwap_den else cur
    if cur > vwap:
        bull.append("Above VWAP")
    else:
        bear.append("Below VWAP")

    prev_day = daily.iloc[-2]
    P = (prev_day["high"] + prev_day["low"] + prev_day["close"]) / 3
    if cur > P:
        bull.append("Above Pivot")
    else:
        bear.append("Below Pivot")

    pdh = daily["high"].iloc[-2]
    pdl = daily["low"].iloc[-2]
    if abs(cur - pdh) < adr * 0.25:
        bear.append("Near PDH " + str(round(pdh, 2)))
    if abs(cur - pdl) < adr * 0.25:
        bull.append("Near PDL " + str(round(pdl, 2)))

    week_high = daily["high"].tail(7).max()
    week_low = daily["low"].tail(7).min()
    if abs(cur - week_high) < adr * 0.3:
        bear.append("Near Weekly High")
    if abs(cur - week_low) < adr * 0.3:
        bull.append("Near Weekly Low")

    rn = round(cur / 50) * 50
    if abs(cur - rn) < adr * 0.15:
        if cur > rn:
            bear.append("Above Round " + str(rn))
        else:
            bull.append("Below Round " + str(rn))

    dow = datetime.now(timezone.utc).weekday()
    if dow == 0 and len(daily) > 1:
        fri_close = daily["close"].iloc[-2]
        mon_open = daily["open"].iloc[-1]
        gap = abs(mon_open - fri_close)
        if gap > adr * 0.3:
            if mon_open > fri_close:
                bull.append("Monday Gap UP")
            else:
                bear.append("Monday Gap DOWN")

    today = m5["dt"].iloc[-1].date()
    asian = m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour < 7)]
    aH = asian["high"].max() if len(asian) else 0
    aL = asian["low"].min() if len(asian) else 0
    if aH and cur > aH:
        bull.append("Above Asian High")
    if aL and cur < aL:
        bear.append("Below Asian Low")

    london = m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= 7) & (m5["dt"].dt.hour < 12)]
    lH = london["high"].max() if len(london) else 0
    lL = london["low"].min() if len(london) else 0
    if lH and cur > lH:
        bull.append("Above London High")
    if lL and cur < lL:
        bear.append("Below London Low")

    ny = m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= 12) & (m5["dt"].dt.hour < 21)]
    nH = ny["high"].max() if len(ny) else 0
    nL = ny["low"].min() if len(ny) else 0
    if nH and cur > nH:
        bull.append("Above NY High")
    if nL and cur < nL:
        bear.append("Below NY Low")

    c3 = h1.iloc[-1]
    c2 = h1.iloc[-2]
    body = abs(c3["close"] - c3["open"])
    uw = c3["high"] - max(c3["close"], c3["open"])
    dw = min(c3["close"], c3["open"]) - c3["low"]
    if dw > 2 * body and uw < body:
        bull.append("1H Bull Pin")
    if uw > 2 * body and dw < body:
        bear.append("1H Bear Pin")
    if c2["close"] < c2["open"] and c3["close"] > c3["open"] and c3["close"] > c2["open"]:
        bull.append("1H Bull Engulf")
    if c2["close"] > c2["open"] and c3["close"] < c3["open"] and c3["close"] < c2["open"]:
        bear.append("1H Bear Engulf")

    bS = len(bull)
    sS = len(bear)
    conf = max(bS, sS)

    if conf < 6:
        print("Conf " + str(conf) + " skip")
        return

    if bS > sS:
        action = "LONG"
        reasons = bull
    else:
        action = "SHORT"
        reasons = bear

    entry = cur
    if action == "LONG":
        sl = cur - 20
        tp1 = cur + 20
        tp2 = cur + 30
        tp3 = cur + 40
    else:
        sl = cur + 20
        tp1 = cur - 20
        tp2 = cur - 30
        tp3 = cur - 40

    sl_pips = abs(entry - sl) * 10
    rr = abs(tp1 - entry) / abs(entry - sl)
470.   now = datetime.now(timezone.utc).strftime("%H:%M UTC")
471    text = "GOLD SMC " + action + " - " + session
472    text = text + "\n\nTime: " + now
473    text = text + "\nEntry: " + str(round(entry, 2))
474    text = text + "\nSL: " + str(round(sl, 2))
475    text = text + "\nTP1: " + str(round(tp1, 2))
476    text = text + "\nTP2: " + str(round(tp2, 2))
477    text = text + "\nTP3: " + str(round(tp3, 2))
478    text = text + "\nConf: " + str(conf) + "/35"
479    
480    if pre:
481        text = text + "\n\nPRE-NEWS: " + pre
482        text = text + "\nCLOSE TRADES NOW"
483
484    if news_days:
485        text = text + "\n\nNEWS DAY: " + ", ".join(news_days)
486
487    requests.post(...)
