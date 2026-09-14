import requests, time, os, json, base64
import pandas as pd
from datetime import datetime, timezone, timedelta

TG = os.environ.get("TG_TOKEN")
CI = os.environ.get("TG_CHAT")
DK = os.environ.get("TD_KEY")
GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_REPO = os.environ.get("GITHUB_REPOSITORY")
STATE_FILE = "state.json"


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"position": None, "counter": None, "last_session": "",
                "session_open": None, "session_open_time": "",
                "session_high": None, "session_low": None,
                "exhaust_alerted": "", "ny_close_alerted": "",
                "weekend_alerted": "", "last_normal_run": "",
                "last_hold_msg": "", "neutral_alerted": "",
                "last_mode": "", "last_sl_time": "",
                "daily_loss": 0, "wins": 0, "losses": 0,
                "today_trades": 0, "today_date": "",
                "last_signal_price": 0, "last_signal_time": "",
                "last_signal_dir": "", "confluence_history": [],
                "flip_count": 0, "cache": {},
                "peak_equity": 1000, "current_equity": 1000}


def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception as e:
        print("State write failed: " + str(e))
    if not GH_TOKEN or not GH_REPO:
        return
    api = "https://api.github.com/repos/" + GH_REPO + "/contents/" + STATE_FILE
    headers = {"Authorization": "token " + GH_TOKEN}
    try:
        r = requests.get(api, headers=headers)
        sha = r.json().get("sha") if r.status_code == 200 else None
        content = base64.b64encode(json.dumps(state, indent=2, default=str).encode()).decode()
        data = {"message": "state update", "content": content}
        if sha:
            data["sha"] = sha
        requests.put(api, headers=headers, json=data, timeout=30)
    except Exception as e:
        print("State save failed: " + str(e))


def send_telegram(msg):
    try:
        requests.post(
            "https://api.telegram.org/bot" + TG + "/sendMessage",
            data={"chat_id": CI, "text": msg},
            timeout=30,
        )
    except Exception as e:
        print("Telegram failed: " + str(e))


def to_ist(dt_utc):
    return dt_utc + timedelta(hours=5, minutes=30)


def format_ist_time(dt_utc):
    ist = to_ist(dt_utc)
    h24, m = ist.hour, ist.minute
    ampm = "AM" if h24 < 12 else "PM"
    h12 = h24 % 12
    if h12 == 0:
        h12 = 12
    return str(h12) + ":" + str(m).zfill(2) + " " + ampm + " IST"


def format_ist_date(dt_utc):
    return to_ist(dt_utc).strftime("%d-%m-%Y")


def get_dst_flags():
    try:
        now_utc = pd.Timestamp.now(tz='UTC')
        ny_dst = now_utc.tz_convert('America/New_York').dst().total_seconds() > 0
        london_dst = now_utc.tz_convert('Europe/London').dst().total_seconds() > 0
        sydney_dst = now_utc.tz_convert('Australia/Sydney').dst().total_seconds() > 0
        return ny_dst, london_dst, sydney_dst
    except Exception:
        return False, False, False


def is_weekend():
    return datetime.now(timezone.utc).weekday() in [5, 6]


def is_holiday():
    now = datetime.now(timezone.utc)
    hol = [(1, 1), (1, 20), (2, 17), (5, 26), (7, 4), (9, 1), (11, 27), (12, 25)]
    for m, d in hol:
        if now.month == m and now.day == d:
            return True
    return False


def is_market_open():
    if is_weekend() or is_holiday():
        return False
    return True


def get_session():
    h = datetime.now(timezone.utc).hour
    ny_dst, london_dst, sydney_dst = get_dst_flags()
    london_open = 7 if london_dst else 8
    london_close = 16 if london_dst else 17
    ny_open = 12 if ny_dst else 13
    ny_close = 21 if ny_dst else 22
    if ny_close <= h or h < 9:
        return "SYDNEY"
    if 9 <= h < london_open:
        return "TOKYO"
    if london_open <= h < ny_open:
        return "LONDON"
    if ny_open <= h < london_close:
        return "NY_OVERLAP"
    if london_close <= h < ny_close:
        return "NEWYORK"
    return "SYDNEY"


def is_active_session(s):
    return s in ["LONDON", "NY_OVERLAP", "NEWYORK"]


def get_session_segment(m5, session):
    today = m5["dt"].iloc[-1].date()
    ny_dst, london_dst, sydney_dst = get_dst_flags()
    london_open = 7 if london_dst else 8
    ny_open = 12 if ny_dst else 13
    london_close = 16 if london_dst else 17
    ny_close = 21 if ny_dst else 22
    if session == "SYDNEY":
        return m5[(m5["dt"].dt.date == today) & ((m5["dt"].dt.hour >= ny_close) | (m5["dt"].dt.hour < 9))]
    if session == "TOKYO":
        return m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= 9) & (m5["dt"].dt.hour < london_open)]
    if session == "LONDON":
        return m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= london_open) & (m5["dt"].dt.hour < ny_open)]
    if session == "NY_OVERLAP":
        return m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= ny_open) & (m5["dt"].dt.hour < london_close)]
    if session == "NEWYORK":
        return m5[(m5["dt"].dt.date == today) & (m5["dt"].dt.hour >= london_close) & (m5["dt"].dt.hour < ny_close)]
    return m5.tail(50)


def is_session_transition(old_s, new_s):
    if not old_s or old_s == new_s:
        return False
    valid = [("SYDNEY", "TOKYO"), ("TOKYO", "LONDON"), ("LONDON", "NY_OVERLAP"),
             ("NY_OVERLAP", "NEWYORK"), ("NEWYORK", "SYDNEY")]
    return (old_s, new_s) in valid


def is_ny_close():
    now = datetime.now(timezone.utc)
    ny_dst, _, _ = get_dst_flags()
    nh = 21 if ny_dst else 22
    return now.hour == nh and now.minute < 10


def is_friday_close():
    now = datetime.now(timezone.utc)
    return now.weekday() == 4 and now.hour == 20 and now.minute < 10


def is_news_time():
    now = datetime.now(timezone.utc)
    h, m, dow = now.hour, now.minute, now.weekday()
    ny_dst, _, _ = get_dst_flags()
    nh = 12 if ny_dst else 13
    fh = 18 if ny_dst else 19
    if dow == 4 and h == nh and 25 <= m <= 40:
        return "NFP"
    if dow in [0, 1, 2, 3, 4] and h == fh and 55 <= m <= 65:
        return "FED"
    if dow in [0, 1, 2, 3, 4] and h == nh and 25 <= m <= 40:
        return "CPI"
    return None


def is_pre_news():
    now = datetime.now(timezone.utc)
    h, m, dow = now.hour, now.minute, now.weekday()
    ny_dst, _, _ = get_dst_flags()
    nh = 12 if ny_dst else 13
    fh = 18 if ny_dst else 19
    if dow == 4 and h == nh and 10 <= m <= 24:
        return "NFP in " + str(25 - m) + " min"
    if dow in [0, 1, 2, 3, 4] and h == fh and 40 <= m <= 54:
        return "FED in " + str(55 - m) + " min"
    if dow in [0, 1, 2, 3, 4] and h == nh and 10 <= m <= 24:
        return "CPI in " + str(25 - m) + " min"
    return None


def get_news_day():
    now = datetime.now(timezone.utc)
    dow, d = now.weekday(), now.day
    w = []
    if dow == 4 and 1 <= d <= 7:
        w.append("NFP Friday")
    if dow == 2 and 10 <= d <= 14:
        w.append("CPI Day")
    if dow == 3 and 15 <= d <= 21:
        w.append("FOMC Possible")
    return w


def fetch(i, s):
    u = "https://api.twelvedata.com/time_series"
    p = {"symbol": "XAU/USD", "interval": i, "outputsize": s, "apikey": DK}
    try:
        r = requests.get(u, params=p, timeout=20).json()
    except Exception:
        return None
    if "values" not in r:
        return None
    d = pd.DataFrame(r["values"])
    d["dt"] = pd.to_datetime(d["datetime"])
    for c in ["open", "high", "low", "close"]:
        d[c] = d[c].astype(float)
    return d.sort_values("dt").reset_index(drop=True)


def fetch_live_price():
    try:
        u = "https://api.twelvedata.com/price"
        p = {"symbol": "XAU/USD", "apikey": DK}
        r = requests.get(u, params=p, timeout=8).json()
        if "price" in r:
            return float(r["price"])
    except Exception:
        pass
    return None


def fetch_symbol(symbol):
    try:
        u = "https://api.twelvedata.com/time_series"
        p = {"symbol": symbol, "interval": "1h", "outputsize": 5, "apikey": DK}
        r = requests.get(u, params=p, timeout=15).json()
        if "values" not in r:
            return None
        d = pd.DataFrame(r["values"])
        d["close"] = d["close"].astype(float)
        return d.sort_values("datetime").reset_index(drop=True)
    except Exception:
        return None


def get_macro_changes():
    dxy = fetch_symbol("DXY")
    us10y = fetch_symbol("US10Y")
    dxy_change = 0
    us10y_change = 0
    if dxy is not None and len(dxy) >= 2:
        dxy_change = ((dxy["close"].iloc[-1] - dxy["close"].iloc[-2]) / dxy["close"].iloc[-2]) * 100
    if us10y is not None and len(us10y) >= 2:
        us10y_change = ((us10y["close"].iloc[-1] - us10y["close"].iloc[-2]) / us10y["close"].iloc[-2]) * 100
    return dxy_change, us10y_change


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
    if len(a) < p + 1:
        return 50
    g = l = 0
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
    H, L = [], []
    for i in range(w, len(df) - w):
        if df["high"].iloc[i] == df["high"].iloc[i - w:i + w + 1].max():
            H.append(df["high"].iloc[i])
        if df["low"].iloc[i] == df["low"].iloc[i - w:i + w + 1].min():
            L.append(df["low"].iloc[i])
    return H, L


def find_fvg(df):
    out = []
    for i in range(2, len(df)):
        c1h, c1l = df["high"].iloc[i - 2], df["low"].iloc[i - 2]
        c3h, c3l = df["high"].iloc[i], df["low"].iloc[i]
        if c1h < c3l:
            out.append({"t": "B", "ce": (c1h + c3l) / 2})
        if c1l > c3h:
            out.append({"t": "S", "ce": (c1l + c3h) / 2})
    return out


def find_ob(df):
    b_ob = s_ob = None
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
        return "BULL" if c["close"] > c["open"] else "BEAR"
    return None


def check_equal_levels(levels, tol=5):
    if len(levels) < 2:
        return False
    for i in range(len(levels) - 1):
        if abs(levels[i] - levels[i + 1]) < tol:
            return True
    return False


def check_adx(df, p=14):
    if len(df) < p * 2:
        return 0
    tr_list, plus_dm, minus_dm = [], [], []
    for i in range(len(df) - p * 2, len(df)):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        pc = df["close"].iloc[i - 1]
        ph = df["high"].iloc[i - 1]
        pl = df["low"].iloc[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
        up = h - ph
        dn = pl - l
        plus_dm.append(up if up > dn and up > 0 else 0)
        minus_dm.append(dn if dn > up and dn > 0 else 0)
    atr_v = sum(tr_list[-p:]) / p
    if atr_v == 0:
        return 0
    plus_di = 100 * (sum(plus_dm[-p:]) / p) / atr_v
    minus_di = 100 * (sum(minus_dm[-p:]) / p) / atr_v
    if plus_di + minus_di == 0:
        return 0
    return 100 * abs(plus_di - minus_di) / (plus_di + minus_di)


def bollinger_squeeze(df, p=20):
    if len(df) < p + 10:
        return False
    closes = df["close"].tolist()
    ma = sum(closes[-p:]) / p
    std = (sum([(x - ma) ** 2 for x in closes[-p:]]) / p) ** 0.5
    width_now = (2 * std) / ma if ma else 0
    widths = []
    for i in range(len(closes) - 20, len(closes)):
        m = sum(closes[i - p:i]) / p
        s = (sum([(x - m) ** 2 for x in closes[i - p:i]]) / p) ** 0.5
        widths.append((2 * s) / m if m else 0)
    if not widths:
        return False
    return width_now < (sum(widths) / len(widths)) * 0.7


def check_momentum(m5):
    if len(m5) < 4:
        return None, 0
    ranges = (m5["high"] - m5["low"]).tail(20)
    avg_range = ranges.mean()
    if avg_range == 0:
        return None, 0
    last3 = m5.tail(3)
    move = abs(last3["close"].iloc[-1] - last3["open"].iloc[0])
    speed = move / avg_range
    d = "BULL" if last3["close"].iloc[-1] > last3["open"].iloc[-1] else "BEAR"
    if speed > 1.2:
        return "FAST_" + d, speed
    if speed > 0.7:
        return "MED_" + d, speed
    return "SLOW", speed


def check_price_action(m5):
    if len(m5) < 2:
        return []
    s = []
    c1 = m5.iloc[-1]
    c2 = m5.iloc[-2]
    body = abs(c1["close"] - c1["open"])
    uw = c1["high"] - max(c1["close"], c1["open"])
    dw = min(c1["close"], c1["open"]) - c1["low"]
    if body == 0:
        body = 0.01
    if dw > body * 2.5 and uw < body * 0.5:
        s.append("PA: Bull Rejection")
    if uw > body * 2.5 and dw < body * 0.5:
        s.append("PA: Bear Rejection")
    if c1["close"] > c1["open"] and body > (c1["high"] - c1["low"]) * 0.7:
        s.append("PA: Strong Bull")
    if c1["close"] < c1["open"] and body > (c1["high"] - c1["low"]) * 0.7:
        s.append("PA: Strong Bear")
    if c2["close"] < c2["open"] and c1["close"] > c1["open"] and c1["close"] > c2["open"]:
        s.append("PA: Bull Engulf")
    if c2["close"] > c2["open"] and c1["close"] < c1["open"] and c1["close"] < c2["open"]:
        s.append("PA: Bear Engulf")
    return s


def session_vwap(m5, session):
    seg = get_session_segment(m5, session)
    if len(seg) == 0:
        return None
    return ((seg["high"] + seg["low"] + seg["close"]) / 3).mean()


def session_high_low(m5, session):
    seg = get_session_segment(m5, session)
    if len(seg) == 0:
        return None, None
    return seg["high"].max(), seg["low"].min()


def prev_session_close(m5):
    today = m5["dt"].iloc[-1].date()
    yesterday = today - pd.Timedelta(days=1)
    ny_dst, _, _ = get_dst_flags()
    lc = 16 if ny_dst else 17
    nc = 21 if ny_dst else 22
    seg = m5[(m5["dt"].dt.date == yesterday) & (m5["dt"].dt.hour >= lc) & (m5["dt"].dt.hour < nc)]
    if len(seg) == 0:
        return None
    return seg["close"].iloc[-1]


def rsi_divergence(df, lookback=20):
    closes = df["close"].tolist()
    if len(closes) < lookback + 14:
        return None
    recent = closes[-lookback:]
    rn = rsi(closes)
    rp = rsi(closes[:-5])
    phn = max(recent[-5:])
    php = max(recent[-15:-5])
    if phn > php and rn < rp:
        return "BEARISH"
    pln = min(recent[-5:])
    plp = min(recent[-15:-5])
    if pln < plp and rn > rp:
        return "BULLISH"
    return None


def volume_spike(m5):
    r = (m5["high"] - m5["low"]).tail(20)
    a = r.mean()
    if a == 0:
        return False
    cr = m5["high"].iloc[-1] - m5["low"].iloc[-1]
    return cr > a * 1.3


def session_liquidity_map(h1, m15, cur):
    H1, L1 = swings(h1)
    b = sorted([x for x in H1 if x > cur])[:3]
    s = sorted([x for x in L1 if x < cur], reverse=True)[:3]
    return {
        "nearest_bsl": b[0] if b else None,
        "nearest_ssl": s[0] if s else None,
        "far_bsl": b[-1] if len(b) > 1 else None,
        "far_ssl": s[-1] if len(s) > 1 else None,
    }


def detect_session_exhaustion(m5, m15, h1, session, cur):
    seg = get_session_segment(m5, session)
    if len(seg) < 3:
        return None, 0, {}
    sh = seg["high"].max()
    sl = seg["low"].min()
    sr = sh - sl
    dr = m5.groupby(m5["dt"].dt.date).agg({"high": "max", "low": "min"})
    dr["range"] = dr["high"] - dr["low"]
    adr = dr["range"].tail(14).mean()
    ru = sr / adr if adr > 0 else 0
    rv = rsi(m15["close"].tolist())
    ny_dst, ld, sd = get_dst_flags()
    lo = 7 if ld else 8
    lc = 16 if ld else 17
    no = 12 if ny_dst else 13
    nc = 21 if ny_dst else 22
    ss = {"SYDNEY": nc, "TOKYO": 9, "LONDON": lo, "NY_OVERLAP": no, "NEWYORK": lc}
    se = {"SYDNEY": 9, "TOKYO": lo, "LONDON": no, "NY_OVERLAP": lc, "NEWYORK": nc}
    sth = ss.get(session, 0)
    enh = se.get(session, 0)
    nh = datetime.now(timezone.utc).hour
    if enh > sth:
        el = (nh - sth) / (enh - sth)
    else:
        el = 0.5
    el = max(0, min(1, el))
    rr = (m5["high"] - m5["low"]).tail(5)
    pr = (m5["high"] - m5["low"]).tail(15).head(10)
    vd = rr.mean() < pr.mean() * 0.7 if len(pr) > 0 else False
    H1, L1 = swings(h1)
    swh = len(H1) >= 3 and any(abs(sh - x) < 1.5 for x in H1[-3:])
    swl = len(L1) >= 3 and any(abs(sl - x) < 1.5 for x in L1[-3:])
    mid = (sh + sl) / 2
    bs, brs = 0, 0
    if ru > 0.6:
        if cur > mid: brs += 1
        else: bs += 1
    if rv > 70: brs += 1
    if rv < 30: bs += 1
    if el > 0.6:
        if cur > mid: brs += 1
        else: bs += 1
    if vd:
        if cur > mid: brs += 1
        else: bs += 1
    if swh and cur < sh: brs += 1
    if swl and cur > sl: bs += 1
    info = {"range_used": round(ru * 100, 1), "rsi": round(rv, 1), "elapsed": round(el * 100, 1),
            "vol_decline": vd, "sess_high": round(sh, 2), "sess_low": round(sl, 2), "mid": round(mid, 2)}
    if ru < 0.5:
        return None, 0, info
    if brs >= 4 and brs > bs:
        return "BEAR_EXHAUST", brs, info
    if bs >= 4 and bs > brs:
        return "BULL_EXHAUST", bs, info
    return None, 0, info


def is_session_grace_period(state, session):
    sot = state.get("session_open_time", "")
    if not sot:
        return False
    try:
        od = datetime.strptime(sot, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except Exception:
        return False
    mins = (datetime.now(timezone.utc) - od).total_seconds() / 60
    return mins < 30


def detect_liquidity_sweep(m5, pos_side):
    if len(m5) < 5:
        return False
    H, L = swings(m5, w=2)
    if len(H) < 2 or len(L) < 2:
        return False
    rh, rl = H[-1], L[-1]
    c1 = m5.iloc[-1]
    c2 = m5.iloc[-2]
    if pos_side == "LONG":
        if c2["low"] < rl - 3 and c1["close"] > rl:
            return True
    if pos_side == "SHORT":
        if c2["high"] > rh + 3 and c1["close"] < rh:
            return True
    return False


def check_long_hold_break(state, h1, m15, m5, cur, dxy_c, us10y_c):
    pos = state.get("position")
    if not pos:
        return False, "", ""
    side, entry, sl = pos["side"], pos["entry"], pos["sl"]
    sd = abs(sl - entry)
    if side == "LONG":
        loss = entry - cur
    else:
        loss = cur - entry
    if loss <= 0:
        return False, "", ""
    prog = loss / sd if sd > 0 else 0
    score = 0
    reasons = []
    c15 = check_choch(m15)
    if side == "LONG" and c15 == "BEAR":
        score += 2; reasons.append("M15 CHoCH Bear")
    if side == "SHORT" and c15 == "BULL":
        score += 2; reasons.append("M15 CHoCH Bull")
    c1h = check_choch(h1)
    if side == "LONG" and c1h == "BEAR":
        score += 2; reasons.append("H1 CHoCH Bear")
    if side == "SHORT" and c1h == "BULL":
        score += 2; reasons.append("H1 CHoCH Bull")
    if detect_liquidity_sweep(m5, side):
        score += 2; reasons.append("Liq Sweep")
    if volume_spike(m5):
        score += 1; reasons.append("Vol Spike")
    if side == "LONG" and dxy_c > 0.1:
        score += 1; reasons.append("DXY Strong")
    if side == "SHORT" and dxy_c < -0.1:
        score += 1; reasons.append("DXY Weak")
    if side == "LONG" and us10y_c > 0.5:
        score += 1; reasons.append("US10Y Up")
    if side == "SHORT" and us10y_c < -0.5:
        score += 1; reasons.append("US10Y Down")
    pa = check_price_action(m5)
    if side == "LONG" and any("Bear" in p for p in pa):
        score += 1; reasons.append("Bear PA")
    if side == "SHORT" and any("Bull" in p for p in pa):
        score += 1; reasons.append("Bull PA")
    if prog >= 0.75:
        score += 2; reasons.append("75% Loss")
    elif prog >= 0.5:
        score += 1; reasons.append("50% Loss")
    if score >= 4:
        return True, " | ".join(reasons), "EXIT_AND_REVERSE"
    elif score >= 3:
        return True, " | ".join(reasons), "EXIT_NOW"
    elif score >= 2:
        return True, " | ".join(reasons), "WARNING"
    return False, "", ""


def check_flip_cycle(state, cur, h1, m15, m5, dxy_c, us10y_c):
    if state.get("flip_count", 0) >= 4:
        return None
    adx = check_adx(h1)
    pos = state.get("position")
    if not pos:
        return None
    side, entry, tp1, sl = pos["side"], pos["entry"], pos["tp1"], pos["sl"]
    sd = abs(sl - entry)
    if side == "LONG":
        pnl = cur - entry
        pl = (entry - cur) / sd if sd > 0 else 0
        hit = cur >= tp1
    else:
        pnl = entry - cur
        pl = (cur - entry) / sd if sd > 0 else 0
        hit = cur <= tp1
    if hit:
        return {"action": "TP_FLIP", "reverse": "SHORT" if side == "LONG" else "LONG", "reason": "Target hit", "close_side": side}
    if pl >= 0.75:
        return {"action": "SL_FLIP", "reverse": "SHORT" if side == "LONG" else "LONG", "reason": "75% loss", "close_side": side}
    c15 = check_choch(m15)
    c1h = check_choch(h1)
    rev = False
    reasons = []
    if side == "LONG" and c15 == "BEAR":
        rev = True; reasons.append("M15 CHoCH Bear")
    if side == "SHORT" and c15 == "BULL":
        rev = True; reasons.append("M15 CHoCH Bull")
    if side == "LONG" and c1h == "BEAR":
        rev = True; reasons.append("H1 CHoCH Bear")
    if side == "SHORT" and c1h == "BULL":
        rev = True; reasons.append("H1 CHoCH Bull")
    if side == "LONG" and dxy_c > 0.15:
        rev = True; reasons.append("DXY Strong")
    if side == "SHORT" and dxy_c < -0.15:
        rev = True; reasons.append("DXY Weak")
    if adx > 30:
        if side == "LONG" and c1h == "BULL":
            return None
        if side == "SHORT" and c1h == "BEAR":
            return None
    if rev:
        return {"action": "TREND_FLIP", "reverse": "SHORT" if side == "LONG" else "LONG", "reason": " | ".join(reasons), "close_side": side}
    op = pos.get("opened_at", "")
    if op:
        try:
            od = datetime.strptime(op, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - od).total_seconds() / 60
            if age > 60 and pnl < 5:
                return {"action": "TIME_FLIP", "reverse": "SHORT" if side == "LONG" else "LONG", "reason": "60 min no profit", "close_side": side}
        except Exception:
            pass
    return None


def execute_flip(state, fi, cur, now_str):
    rev = fi["reverse"]
    if rev == "LONG":
        sl, tp1, tp2, tp3 = cur - 20, cur + 20, cur + 30, cur + 40
    else:
        sl, tp1, tp2, tp3 = cur + 20, cur - 20, cur - 30, cur - 40
    state["position"] = {"side": rev, "entry": cur, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                          "tp_hits": [], "warn_hits": [], "opened_at": now_str, "flip_from": fi["close_side"]}
    state["flip_count"] = state.get("flip_count", 0) + 1
    return state


def check_htf_alignment(h1, m15, m5, action):
    m5b = m5["close"].iloc[-1] > m5["close"].iloc[-5]
    m15b = m15["close"].iloc[-1] > m15["close"].iloc[-3]
    h1b = h1["close"].iloc[-1] > h1["close"].iloc[-2]
    if action == "LONG":
        return m5b and m15b and h1b
    if action == "SHORT":
        return (not m5b) and (not m15b) and (not h1b)
    return True


def get_market_mode(bull, bear, h1, m15, m5):
    bS = len(bull)
    sS = len(bear)
    h1b = h1["close"].iloc[-1] > h1["close"].iloc[-3]
    m15b = m15["close"].iloc[-1] > m15["close"].iloc[-3]
    m5b = m5["close"].iloc[-1] > m5["close"].iloc[-3]
    hb, hs = 0, 0
    if h1b: hb += 10
    else: hs += 10
    if m15b: hb += 10
    else: hs += 10
    if m5b: hb += 10
    else: hs += 10
    buy_s = (bS * 5) + hb
    sell_s = (sS * 5) + hs
    tot = buy_s + sell_s
    if tot == 0:
        return "NEUTRAL", 0, 0
    bp = round((buy_s / tot) * 100)
    sp = 100 - bp
    if bp >= 65:
        return "BUY_MODE", bp, sp
    if sp >= 65:
        return "SELL_MODE", bp, sp
    return "NEUTRAL", bp, sp


def check_keltner(df, p=20):
    if len(df) < p + 1:
        return None
    closes = df["close"].tolist()
    ma = sum(closes[-p:]) / p
    av = atr(df)
    cur = closes[-1]
    if cur > ma + 2 * av:
        return "Above Keltner"
    if cur < ma - 2 * av:
        return "Below Keltner"
    return None


def check_stoch_rsi(df):
    closes = df["close"].tolist()
    if len(closes) < 20:
        return 50
    rv = []
    for i in range(len(closes) - 5, len(closes)):
        rv.append(rsi(closes[:i + 1]))
    mn, mx = min(rv), max(rv)
    if mx == mn:
        return 50
    return ((rv[-1] - mn) / (mx - mn)) * 100


def check_ichimoku(df):
    if len(df) < 52:
        return None
    highs = df["high"].tail(52).tolist()
    lows = df["low"].tail(52).tolist()
    t = (max(highs[-9:]) + min(lows[-9:])) / 2
    k = (max(highs[-26:]) + min(lows[-26:])) / 2
    cur = df["close"].iloc[-1]
    if cur > t and cur > k:
        return "Ichimoku Bull"
    if cur < t and cur < k:
        return "Ichimoku Bear"
    return None


def check_donchian(df, p=20):
    if len(df) < p + 1:
        return None
    u = df["high"].tail(p).max()
    l = df["low"].tail(p).min()
    cur = df["close"].iloc[-1]
    if cur >= u - 2:
        return "Donchian Upper Break"
    if cur <= l + 2:
        return "Donchian Lower Break"
    return None


def fib_retracement(daily):
    if len(daily) < 5:
        return {}
    high = daily["high"].tail(20).max()
    low = daily["low"].tail(20).min()
    rng = high - low
    if rng == 0:
        return {}
    return {"0.236": round(low + rng * 0.236, 2), "0.382": round(low + rng * 0.382, 2),
            "0.5": round(low + rng * 0.5, 2), "0.618": round(low + rng * 0.618, 2),
            "0.786": round(low + rng * 0.786, 2), "high": round(high, 2), "low": round(low, 2)}


def fib_extension(daily):
    if len(daily) < 5:
        return {}
    high = daily["high"].tail(20).max()
    low = daily["low"].tail(20).min()
    rng = high - low
    if rng == 0:
        return {}
    return {"1.272": round(low + rng * 1.272, 2), "1.414": round(low + rng * 1.414, 2),
            "1.618": round(low + rng * 1.618, 2), "2.0": round(low + rng * 2.0, 2)}


def camarilla_pivots(daily):
    if len(daily) < 2:
        return {}
    prev = daily.iloc[-2]
    h, l, c = prev["high"], prev["low"], prev["close"]
    rng = h - l
    if rng == 0:
        return {}
    return {"H5": round(c + rng * 1.1 / 2, 2), "H4": round(c + rng * 1.1 / 4, 2),
            "H3": round(c + rng * 1.1 / 6, 2), "L3": round(c - rng * 1.1 / 6, 2),
            "L4": round(c - rng * 1.1 / 4, 2), "L5": round(c - rng * 1.1 / 2, 2)}


def woodie_pivots(daily):
    if len(daily) < 2:
        return {}
    prev = daily.iloc[-2]
    h, l, c = prev["high"], prev["low"], prev["close"]
    p = (h + l + 2 * c) / 4
    return {"P": round(p, 2), "R1": round(2 * p - l, 2), "R2": round(p + h - l, 2),
            "S1": round(2 * p - h, 2), "S2": round(p - h + l, 2)}


def volume_profile(m5):
    if len(m5) < 50:
        return {}
    seg = m5.tail(100)
    bins = {}
    for _, row in seg.iterrows():
        mid = (row["high"] + row["low"]) / 2
        bucket = round(mid / 5) * 5
        bins[bucket] = bins.get(bucket, 0) + (row["high"] - row["low"])
    if not bins:
        return {}
    poc = max(bins, key=bins.get)
    sorted_bins = sorted(bins.keys())
    total_vol = sum(bins.values())
    cum = 0
    vah = poc
    val = poc
    for b in sorted_bins:
        cum += bins[b]
        if cum >= total_vol * 0.85:
            vah = b
            break
    cum = 0
    for b in reversed(sorted_bins):
        cum += bins[b]
        if cum >= total_vol * 0.85:
            val = b
            break
    return {"POC": poc, "VAH": vah, "VAL": val}


def is_killzone():
    now = datetime.now(timezone.utc)
    h = now.hour
    ny_dst, london_dst, _ = get_dst_flags()
    london_open = 7 if london_dst else 8
    ny_open = 12 if ny_dst else 13
    london_kz = london_open <= h < london_open + 1
    ny_kz = ny_open <= h < ny_open + 1
    london_close_kz = (15 <= h < 16) if london_dst else (16 <= h < 17)
    if london_kz: return "London KZ"
    if ny_kz: return "NY KZ"
    if london_close_kz: return "London Close KZ"
    return None


def is_silver_bullet():
    now = datetime.now(timezone.utc)
    ny_dst, _, _ = get_dst_flags()
    sb_h = 14 if ny_dst else 15
    return now.hour == sb_h


def ote_zone(daily):
    if len(daily) < 5:
        return None, None
    high = daily["high"].tail(20).max()
    low = daily["low"].tail(20).min()
    rng = high - low
    if rng == 0:
        return None, None
    return round(low + rng * 0.62, 2), round(low + rng * 0.79, 2)


def detect_hs_pattern(h1):
    H, L = swings(h1)
    if len(H) < 3:
        return None
    h1_, h2_, h3_ = H[-3], H[-2], H[-1]
    if h2_ > h1_ and h2_ > h3_ and abs(h1_ - h3_) < 5:
        return "H&S Bear"
    if len(L) < 3:
        return None
    l1_, l2_, l3_ = L[-3], L[-2], L[-1]
    if l2_ < l1_ and l2_ < l3_ and abs(l1_ - l3_) < 5:
        return "Inverse H&S Bull"
    return None


def detect_double_top_bottom(h1):
    H, L = swings(h1)
    if len(H) >= 2 and abs(H[-1] - H[-2]) < 3:
        return "Double Top Bear"
    if len(L) >= 2 and abs(L[-1] - L[-2]) < 3:
        return "Double Bottom Bull"
    return None


def prev_week_hl(daily):
    if len(daily) < 14:
        return None, None
    lw = daily.iloc[-14:-7]
    if len(lw) == 0:
        return None, None
    return lw["high"].max(), lw["low"].min()


def prev_month_hl(daily):
    if len(daily) < 60:
        return None, None
    lm = daily.iloc[-60:-30]
    if len(lm) == 0:
        return None, None
    return lm["high"].max(), lm["low"].min()


def day_of_week_pattern():
    dow = datetime.now(timezone.utc).weekday()
    patterns = {0: "Monday Range", 1: "Tuesday Trend", 2: "Wednesday Trend",
                3: "Thursday Reversal", 4: "Friday Close"}
    return patterns.get(dow, "")


def check_position(state, cur, now_str):
    pos = state.get("position")
    if not pos:
        return state, None
    side = pos["side"]
    entry = pos["entry"]
    sl = pos["sl"]
    tp1, tp2, tp3 = pos["tp1"], pos["tp2"], pos["tp3"]
    tph = pos.get("tp_hits", [])
    wh = pos.get("warn_hits", [])
    msg = None
    sd = abs(sl - entry)
    trail = pos.get("trailing_sl")
    if side == "LONG":
        if trail and cur <= trail:
            state["position"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "🎯 TRAILING SL - BUY locked at " + str(round(trail, 2))
        if cur <= sl:
            state["position"] = None
            state["last_sl_time"] = now_str
            state["losses"] = state.get("losses", 0) + 1
            state["daily_loss"] = state.get("daily_loss", 0) + sd
            return state, "🔴 SL HIT - BUY closed at " + str(round(sl, 2))
        adv = entry - cur
        if 1 not in wh and adv >= sd * 0.25:
            wh.append(1); msg = "⚠️ 25% LOSS: BUY -" + str(round(adv, 1))
        if 2 not in wh and adv >= sd * 0.5:
            wh.append(2); msg = "⚠️ 50% LOSS: BUY -" + str(round(adv, 1))
        if 3 not in wh and adv >= sd * 0.75:
            wh.append(3); msg = "🚨 75% LOSS: BUY -" + str(round(adv, 1)) + " CLOSE"
        if 1 not in tph and cur >= tp1:
            tph.append(1); pos["sl"] = entry
            msg = "✅ TP1 HIT - BUY +20 pips"
        if 2 not in tph and cur >= tp2:
            tph.append(2); pos["trailing_sl"] = tp1
            msg = "✅ TP2 HIT - BUY +30 pips"
        if 3 not in tph and cur >= tp3:
            tph.append(3); state["position"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "✅ TP3 HIT - BUY +40 pips"
    elif side == "SHORT":
        if trail and cur >= trail:
            state["position"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "🎯 TRAILING SL - SELL locked at " + str(round(trail, 2))
        if cur >= sl:
            state["position"] = None
            state["last_sl_time"] = now_str
            state["losses"] = state.get("losses", 0) + 1
            state["daily_loss"] = state.get("daily_loss", 0) + sd
            return state, "🔴 SL HIT - SELL closed at " + str(round(sl, 2))
        adv = cur - entry
        if 1 not in wh and adv >= sd * 0.25:
            wh.append(1); msg = "⚠️ 25% LOSS: SELL -" + str(round(adv, 1))
        if 2 not in wh and adv >= sd * 0.5:
            wh.append(2); msg = "⚠️ 50% LOSS: SELL -" + str(round(adv, 1))
        if 3 not in wh and adv >= sd * 0.75:
            wh.append(3); msg = "🚨 75% LOSS: SELL -" + str(round(adv, 1)) + " CLOSE"
        if 1 not in tph and cur <= tp1:
            tph.append(1); pos["sl"] = entry
            msg = "✅ TP1 HIT - SELL +20 pips"
        if 2 not in tph and cur <= tp2:
            tph.append(2); pos["trailing_sl"] = tp1
            msg = "✅ TP2 HIT - SELL +30 pips"
        if 3 not in tph and cur <= tp3:
            tph.append(3); state["position"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "✅ TP3 HIT - SELL +40 pips"
    pos["tp_hits"] = tph
    pos["warn_hits"] = wh
    state["position"] = pos
    return state, msg


def check_counter(state, cur):
    ctr = state.get("counter")
    if not ctr:
        return state, None
    side, sl, tp = ctr["side"], ctr["sl"], ctr["tp"]
    if side == "LONG":
        if cur <= sl:
            state["counter"] = None
            return state, "🔴 HEDGE BUY SL"
        if cur >= tp:
            state["counter"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "✅ HEDGE BUY +10 pips"
    elif side == "SHORT":
        if cur >= sl:
            state["counter"] = None
            return state, "🔴 HEDGE SELL SL"
        if cur <= tp:
            state["counter"] = None
            state["wins"] = state.get("wins", 0) + 1
            return state, "✅ HEDGE SELL +10 pips"
    return state, None


def detect_counter_signal(pos_side, h1, m15, m5, cur, session):
    c1 = check_choch(h1)
    c2 = check_choch(m15)
    d = check_displacement(m15, atr(h1) * 0.5)
    v = session_vwap(m5, session)
    sh, sl = session_high_low(m5, session)
    dv = rsi_divergence(m15)
    pc = prev_session_close(m5)
    vol = volume_spike(m5)
    if pos_side == "SHORT":
        c = sum([c2 == "BULL", c1 == "BULL", d == "BULL", bool(v and cur > v),
                 bool(sh and cur > sh), dv == "BULLISH", bool(pc and cur > pc), vol])
        if c >= 5:
            return "LONG", c
    if pos_side == "LONG":
        c = sum([c2 == "BEAR", c1 == "BEAR", d == "BEAR", bool(v and cur < v),
                 bool(sl and cur < sl), dv == "BEARISH", bool(pc and cur < pc), vol])
        if c >= 5:
            return "SHORT", c
    return None, 0


def check_cooldown_safe(state, now_utc):
    ls = state.get("last_sl_time", "")
    if not ls:
        return False
    try:
        ld = datetime.strptime(ls, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
        if (now_utc - ld).total_seconds() / 60 < 30:
            return True
    except Exception:
        pass
    return False


def check_1m_hold_cancel(m1, state, cur):
    pos = state.get("position")
    if not pos:
        return None
    pos_side = pos["side"]
    entry = pos["entry"]
    if len(m1) < 3:
        return None
    ranges = (m1["high"] - m1["low"]).tail(20)
    avg_range = ranges.mean()
    if avg_range == 0:
        return None
    c = m1.iloc[-1]
    c_range = c["high"] - c["low"]
    ratio = c_range / avg_range
    candle_bull = c["close"] > c["open"]
    against = (pos_side == "LONG" and not candle_bull) or (pos_side == "SHORT" and candle_bull)
    if not against:
        return None
    if ratio < 2.5:
        return None
    body = abs(c["close"] - c["open"])
    body_pct = (body / c_range * 100) if c_range > 0 else 0
    if body_pct < 60:
        return None
    if pos_side == "LONG":
        loss = entry - cur
    else:
        loss = cur - entry
    return {"ratio": round(ratio, 2), "range": round(c_range, 2), "body_pct": round(body_pct, 1),
            "loss": round(loss, 1), "direction": "BEAR" if pos_side == "LONG" else "BULL"}


def check_5m_10pip_move(m5, state, cur, h1, m15, dxy_c, us10y_c):
    pos = state.get("position")
    if not pos:
        return None
    pos_side = pos["side"]
    entry = pos["entry"]
    if len(m5) < 5:
        return None
    last3 = m5.tail(3)
    move = abs(last3["close"].iloc[-1] - last3["open"].iloc[0])
    if move < 10:
        return None
    if pos_side == "LONG":
        against = last3["close"].iloc[-1] < last3["open"].iloc[0]
    else:
        against = last3["close"].iloc[-1] > last3["open"].iloc[0]
    if not against:
        return None
    score = 0
    reasons = []
    c15 = check_choch(m15)
    if pos_side == "LONG" and c15 == "BEAR":
        score += 2; reasons.append("M15 CHoCH Bear")
    if pos_side == "SHORT" and c15 == "BULL":
        score += 2; reasons.append("M15 CHoCH Bull")
    if detect_liquidity_sweep(m5, pos_side):
        score += 2; reasons.append("Liq Sweep")
    if volume_spike(m5):
        score += 1; reasons.append("Vol 1.3x+")
    if pos_side == "LONG" and dxy_c > 0.1:
        score += 1; reasons.append("DXY Strong")
    if pos_side == "SHORT" and dxy_c < -0.1:
        score += 1; reasons.append("DXY Weak")
    if pos_side == "LONG":
        if h1["close"].iloc[-1] < h1["close"].iloc[-2]:
            score += 1; reasons.append("H1 Against")
    else:
        if h1["close"].iloc[-1] > h1["close"].iloc[-2]:
            score += 1; reasons.append("H1 Against")
    last_c = m5.iloc[-1]
    last_range = last_c["high"] - last_c["low"]
    avg_rng = (m5["high"] - m5["low"]).tail(20).mean()
    if avg_rng > 0 and last_range > avg_rng * 1.5 and volume_spike(m5):
        score += 1; reasons.append("Big 5M+Vol")
    last2 = m5.tail(2)
    if pos_side == "LONG":
        if last2["close"].iloc[-1] < last2["open"].iloc[-1] and last2["close"].iloc[-2] < last2["open"].iloc[-2]:
            score += 2; reasons.append("2 Bear Candles")
    else:
        if last2["close"].iloc[-1] > last2["open"].iloc[-1] and last2["close"].iloc[-2] > last2["open"].iloc[-2]:
            score += 2; reasons.append("2 Bull Candles")
    body = abs(last_c["close"] - last_c["open"])
    body_pct = (body / last_range * 100) if last_range > 0 else 0
    if body_pct > 70:
        score += 1; reasons.append("Body 70%+")
    op = pos.get("opened_at", "")
    if op:
        try:
            od = datetime.strptime(op, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - od).total_seconds() / 60
            if age > 30:
                score += 1; reasons.append("30min+ Old")
        except Exception:
            pass
    if pos_side == "LONG":
        pnl = cur - entry
    else:
        pnl = entry - cur
    if pnl < -10:
        score += 1; reasons.append("Loss 10+")
    if score >= 9:
        dec = "CLOSE_REVERSE"
    elif score >= 6:
        dec = "CLOSE"
    elif score >= 4:
        dec = "WATCH"
    else:
        dec = "HOLD"
    return {"score": score, "decision": dec, "reasons": reasons, "move": round(move, 1)}


def run():
    state = load_state()
    session = get_session()
    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%Y-%m-%d %H:%M UTC")

    if not is_market_open():
        print("Market closed")
        return
    if check_cooldown_safe(state, now_utc):
        print("Cooldown")
        return
    if state.get("daily_loss", 0) >= 60:
        print("Daily loss limit")
        return

    today = now_utc.strftime("%Y-%m-%d")
    if state.get("today_date", "") != today:
        state["today_date"] = today
        state["today_trades"] = 0
        state["daily_loss"] = 0

    if is_friday_close():
        k = now_utc.strftime("%Y-%m-%d")
        if state.get("weekend_alerted", "") != k:
            state["weekend_alerted"] = k
            save_state(state)
            send_telegram("🔔 WEEKEND CLOSE\nFriday close aa raha hai")

    if is_ny_close():
        k = now_utc.strftime("%Y-%m-%d")
        if state.get("ny_close_alerted", "") != k:
            state["ny_close_alerted"] = k
            save_state(state)
            send_telegram("🔔 NY CLOSE\nMarket close ho raha hai")

    if is_news_time():
        print("News time")
        return

    pre = is_pre_news()
    news_days = get_news_day()

    if not is_active_session(session):
        last = state.get("last_normal_run", "")
        if last:
            try:
                ld = datetime.strptime(last, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
                if (now_utc - ld).total_seconds() / 60 < 9:
                    return
            except Exception:
                pass
        state["last_normal_run"] = now_str
        save_state(state)

    data = {}
    cache = state.get("cache", {})
    now_ts = int(time.time())
    tfs = [("m5", "5min", 200, 300), ("m15", "15min", 200, 300),
           ("h1", "1h", 250, 1800), ("h4", "4h", 200, 3600), ("daily", "1day", 200, 7200)]
    for n, i, s, ttl in tfs:
        c = cache.get(n, {})
        if c.get("ts", 0) + ttl > now_ts and c.get("data"):
            try:
                d = pd.DataFrame(c["data"])
                if "dt" not in d.columns and "datetime" in d.columns:
                    d["dt"] = pd.to_datetime(d["datetime"])
                for col in ["open", "high", "low", "close"]:
                    d[col] = d[col].astype(float)
                data[n] = d.sort_values("dt").reset_index(drop=True)
            except Exception:
                data[n] = fetch(i, s)
        else:
            fetched = fetch(i, s)
            data[n] = fetched
            if fetched is not None:
                try:
                    cache[n] = {"ts": now_ts, "data": fetched.drop(columns=["dt"], errors="ignore").to_dict("records")}
                except Exception as e:
                    print("Cache fail " + n + ": " + str(e))
            time.sleep(6)
    state["cache"] = cache
    save_state(state)

    if any(v is None for v in data.values()):
        print("Fetch failed")
        return

    daily, h4, h1 = data["daily"], data["h4"], data["h1"]
    m15, m5 = data["m15"], data["m5"]
    cur = m5["close"].iloc[-1]
    live = fetch_live_price()
    if live:
        cur = live

    dxy_c, us10y_c = get_macro_changes()
    print("DXY: " + str(round(dxy_c, 3)) + "% | US10Y: " + str(round(us10y_c, 3)) + "%")

    state, msg = check_position(state, cur, now_str)
    if msg: send_telegram(msg)
    state, msg = check_counter(state, cur)
    if msg: send_telegram(msg)

    if state.get("position"):
        m1 = fetch("1min", 50)
        if m1 is not None:
            big_1m = check_1m_hold_cancel(m1, state, cur)
            if big_1m:
                pos = state["position"]
                stx = "BUY" if pos["side"] == "LONG" else "SELL"
                m = "🚨 1M BIG CANDLE - HOLD CANCEL\n\n"
                m += "Range: " + str(big_1m["range"]) + " (" + str(big_1m["ratio"]) + "x)\n"
                m += "Body: " + str(big_1m["body_pct"]) + "%\n"
                m += "Dir: " + big_1m["direction"] + " against " + stx + "\n"
                m += "Loss: " + str(big_1m["loss"]) + " pips\n\n"
                m += "CLOSE " + stx + " NOW"
                send_telegram(m)

    if state.get("position"):
        m5mv = check_5m_10pip_move(m5, state, cur, h1, m15, dxy_c, us10y_c)
        if m5mv and m5mv["decision"] != "HOLD":
            emo = {"WATCH": "⚠️", "CLOSE": "🚨", "CLOSE_REVERSE": "🚨🚨"}[m5mv["decision"]]
            pos = state["position"]
            stx = "BUY" if pos["side"] == "LONG" else "SELL"
            m = emo + " 5M " + str(m5mv["move"]) + " PIPS AGAINST " + stx + "\n\n"
            m += "Score: " + str(m5mv["score"]) + "/15\n"
            m += "Decision: " + m5mv["decision"].replace("_", " ") + "\n\n"
            for r in m5mv["reasons"]:
                m += "• " + r + "\n"
            if m5mv["decision"] == "CLOSE_REVERSE":
                rvs = "BUY" if pos["side"] == "SHORT" else "SELL"
                m += "\n1. CLOSE " + stx + "\n2. Take " + rvs + " @ " + str(round(cur, 2))
            send_telegram(m)

    broken, reason, action = check_long_hold_break(state, h1, m15, m5, cur, dxy_c, us10y_c)
    if broken:
        pos = state.get("position")
        if pos:
            stx = "BUY" if pos["side"] == "LONG" else "SELL"
            if action == "EXIT_AND_REVERSE":
                rtx = "SELL" if pos["side"] == "LONG" else "BUY"
                m = "🚨 " + stx + " TUT GAYA - REVERSE!\n\n" + reason
                m += "\nEntry: " + str(round(pos["entry"], 2)) + "\nCurrent: " + str(round(cur, 2))
                m += "\n\n1. CLOSE " + stx + "\n2. Take " + rtx + " @ " + str(round(cur, 2))
                send_telegram(m)
            elif action == "EXIT_NOW":
                send_telegram("⚠️ " + stx + " WEAK - EXIT\n\n" + reason)
            elif action == "WARNING":
                send_telegram("⚠️ " + stx + " WARNING\n\n" + reason)

    fi = check_flip_cycle(state, cur, h1, m15, m5, dxy_c, us10y_c)
    if fi:
        os_ = fi["close_side"]
        ns_ = fi["reverse"]
        state = execute_flip(state, fi, cur, now_str)
        save_state(state)
        otx = "BUY" if os_ == "LONG" else "SELL"
        ntx = "BUY" if ns_ == "LONG" else "SELL"
        m = "🔄 FLIP CYCLE #" + str(state.get("flip_count", 1))
        m += "\n\nReason: " + fi["reason"]
        m += "\n\n❌ CLOSE " + otx + " @ " + str(round(cur, 2))
        m += "\n✅ OPEN " + ntx + " @ " + str(round(cur, 2))
        send_telegram(m)
        return

    old_s = state.get("last_session", "")
    sc = is_session_transition(old_s, session)
    if sc or not state.get("session_open"):
        state["session_open"] = cur
        state["session_open_time"] = now_str
        state["session_high"] = cur
        state["session_low"] = cur
    if sc:
        state["flip_count"] = 0
    sh_prev = state.get("session_high")
    sl_prev = state.get("session_low")
    new_high = False
    new_low = False
    if sh_prev is None or cur > sh_prev:
        state["session_high"] = cur
        new_high = True
    if sl_prev is None or cur < sl_prev:
        state["session_low"] = cur
        new_low = True
    state["last_session"] = session
    save_state(state)
    if sc:
        send_telegram("🔔 SESSION CHANGE: " + old_s + " -> " + session)
    if new_high and not sc:
        send_telegram("📈 " + session + " NEW HIGH: " + str(round(cur, 2)))
    if new_low and not sc:
        send_telegram("📉 " + session + " NEW LOW: " + str(round(cur, 2)))

    bull, bear = [], []
    d_hi, d_lo = daily["high"].max(), daily["low"].min()
    d_rng = d_hi - d_lo
    d_mid = (d_hi + d_lo) / 2
    lv25 = d_lo + d_rng * 0.25
    lv618 = d_lo + d_rng * 0.618
    lv786 = d_lo + d_rng * 0.786
    lv75 = d_lo + d_rng * 0.75
    if cur > d_mid: bull.append("Above Daily 50%")
    else: bear.append("Below Daily 50%")
    if cur < lv25: bull.append("Deep Discount")
    if cur > lv75: bear.append("Deep Premium")
    if lv618 <= cur <= lv786: bear.append("At Fib 0.618-0.786")

    d_open = daily["open"].iloc[-1]
    w_open = daily["open"].iloc[-5] if len(daily) >= 5 else None
    if cur > d_open: bull.append("Above Daily Open")
    else: bear.append("Below Daily Open")
    if w_open:
        if cur > w_open: bull.append("Above Weekly Open")
        else: bear.append("Below Weekly Open")

    adr = (daily.tail(30)["high"] - daily.tail(30)["low"]).mean()
    atr_h1 = atr(h1)
    if h4.iloc[-1]["close"] > h4["high"].iloc[-12:-1].max(): bull.append("4H BOS UP")
    if h4.iloc[-1]["close"] < h4["low"].iloc[-12:-1].min(): bear.append("4H BOS DN")
    if h1.iloc[-1]["close"] > h1["high"].iloc[-15:-1].max(): bull.append("1H MSS UP")
    if h1.iloc[-1]["close"] < h1["low"].iloc[-15:-1].min(): bear.append("1H MSS DN")
    if m15.iloc[-1]["close"] > m15["high"].iloc[-10:-1].max(): bull.append("15M MSS UP")
    if m15.iloc[-1]["close"] < m15["low"].iloc[-10:-1].min(): bear.append("15M MSS DN")
    c1 = check_choch(h1)
    if c1 == "BULL": bull.append("1H CHoCH Bull")
    if c1 == "BEAR": bear.append("1H CHoCH Bear")
    c4 = check_choch(h4)
    if c4 == "BULL": bull.append("4H CHoCH Bull")
    if c4 == "BEAR": bear.append("4H CHoCH Bear")
    d = check_displacement(h1, atr_h1)
    if d == "BULL": bull.append("Bull Displacement")
    if d == "BEAR": bear.append("Bear Displacement")
    cm5, cm5b = m5.iloc[-1], m5.iloc[-2]
    bm5 = abs(cm5["close"] - cm5["open"])
    if cm5["close"] > cm5["open"] and bm5 > adr * 0.05: bull.append("5M Bull Candle")
    if cm5["close"] < cm5["open"] and bm5 > adr * 0.05: bear.append("5M Bear Candle")
    if cm5b["close"] < cm5b["open"] and cm5["close"] > cm5["open"] and cm5["close"] > cm5b["open"]: bull.append("5M Bull Engulf")
    if cm5b["close"] > cm5b["open"] and cm5["close"] < cm5["open"] and cm5["close"] < cm5b["open"]: bear.append("5M Bear Engulf")
    fvgs = find_fvg(m15)[-15:]
    if any(f["t"] == "B" and abs(cur - f["ce"]) < adr * 0.3 for f in fvgs): bull.append("Bull FVG")
    if any(f["t"] == "S" and abs(cur - f["ce"]) < adr * 0.3 for f in fvgs): bear.append("Bear FVG")
    bo, so = find_ob(h1)
    if bo and bo["bot"] - 5 <= cur <= bo["top"] + 5: bull.append("At Bull OB")
    if so and so["bot"] - 5 <= cur <= so["top"] + 5: bear.append("At Bear OB")
    H1, L1 = swings(h1)
    bs_ = sorted(H1, reverse=True)[:5]
    ss_ = sorted(L1)[:5]
    nB = next((p for p in bs_ if p > cur), None)
    nS = next((p for p in ss_ if p < cur), None)
    if nB and nB - cur < adr * 0.3: bear.append("Near BSL")
    if nS and cur - nS < adr * 0.3: bull.append("Near SSL")
    if check_equal_levels(L1): bull.append("Equal Lows")
    if check_equal_levels(H1): bear.append("Equal Highs")
    if len(H1) > 1 and cur > H1[-2] and cur < H1[-1]: bear.append("Above Inducement")
    if len(L1) > 1 and cur < L1[-2] and cur > L1[-1]: bull.append("Below Inducement")
    cl = h1["close"].tolist()
    e50, e200 = ema(cl, 50), ema(cl, 200)
    if cur > e50 and e50 > e200: bull.append("EMA50>200 UP")
    if cur < e50 and e50 < e200: bear.append("EMA50<200 DN")
    if cur > e200: bull.append("Above EMA200")
    else: bear.append("Below EMA200")
    rv = rsi(cl)
    if rv < 30: bull.append("RSI Oversold")
    if rv > 70: bear.append("RSI Overbought")
    if 50 < rv < 70: bull.append("RSI Bullish")
    if 30 < rv < 50: bear.append("RSI Bearish")
    mn = ema(cl[-50:], 12) - ema(cl[-50:], 26)
    mp = ema(cl[-51:-1], 12) - ema(cl[-51:-1], 26)
    if mn > 0 and mp <= 0: bull.append("MACD Cross UP")
    if mn < 0 and mp >= 0: bear.append("MACD Cross DN")
    if mn > 0: bull.append("MACD Positive")
    else: bear.append("MACD Negative")
    ma20 = sma(cl, 20)
    sd20 = (sum([(x - ma20) ** 2 for x in cl[-20:]]) / 20) ** 0.5
    if cur <= ma20 - 2 * sd20: bull.append("Below BB Lower")
    if cur >= ma20 + 2 * sd20: bear.append("Above BB Upper")
    vn = vd = 0
    for i in range(max(0, len(h1) - 50), len(h1)):
        tp = (h1["high"].iloc[i] + h1["low"].iloc[i] + h1["close"].iloc[i]) / 3
        vn += tp
        vd += 1
    vwap = vn / vd if vd else cur
    if cur > vwap: bull.append("Above VWAP")
    else: bear.append("Below VWAP")
    pd_ = daily.iloc[-2]
    P = (pd_["high"] + pd_["low"] + pd_["close"]) / 3
    if cur > P: bull.append("Above Pivot")
    else: bear.append("Below Pivot")
    pdh, pdl = daily["high"].iloc[-2], daily["low"].iloc[-2]
    if abs(cur - pdh) < adr * 0.25: bear.append("Near PDH")
    if abs(cur - pdl) < adr * 0.25: bull.append("Near PDL")
    wh = daily["high"].tail(7).max()
    wl = daily["low"].tail(7).min()
    if abs(cur - wh) < adr * 0.3: bear.append("Near Weekly High")
    if abs(cur - wl) < adr * 0.3: bull.append("Near Weekly Low")
    rn = round(cur / 50) * 50
    if abs(cur - rn) < adr * 0.15:
        if cur > rn: bear.append("Above Round")
        else: bull.append("Below Round")
    dow = now_utc.weekday()
    if dow == 0 and len(daily) > 1:
        gap = abs(daily["open"].iloc[-1] - daily["close"].iloc[-2])
        if gap > adr * 0.3:
            if daily["open"].iloc[-1] > daily["close"].iloc[-2]: bull.append("Monday Gap UP")
            else: bear.append("Monday Gap DOWN")
    td = m5["dt"].iloc[-1].date()
    for lb, h1_, h2_ in [("Asian", 0, 7), ("London", 7, 12), ("NY", 12, 21)]:
        sg = m5[(m5["dt"].dt.date == td) & (m5["dt"].dt.hour >= h1_) & (m5["dt"].dt.hour < h2_)]
        if len(sg):
            if cur > sg["high"].max(): bull.append("Above " + lb + " High")
            if cur < sg["low"].min(): bear.append("Below " + lb + " Low")
    c3, c2 = h1.iloc[-1], h1.iloc[-2]
    bd = abs(c3["close"] - c3["open"])
    uw = c3["high"] - max(c3["close"], c3["open"])
    dw = min(c3["close"], c3["open"]) - c3["low"]
    if dw > 2 * bd and uw < bd: bull.append("1H Bull Pin")
    if uw > 2 * bd and dw < bd: bear.append("1H Bear Pin")
    if c2["close"] < c2["open"] and c3["close"] > c3["open"] and c3["close"] > c2["open"]: bull.append("1H Bull Engulf")
    if c2["close"] > c2["open"] and c3["close"] < c3["open"] and c3["close"] < c2["open"]: bear.append("1H Bear Engulf")

    md, ms = check_momentum(m5)
    if md == "FAST_BULL": bull.append("Fast Bull")
    if md == "FAST_BEAR": bear.append("Fast Bear")
    if md == "MED_BULL": bull.append("Med Bull")
    if md == "MED_BEAR": bear.append("Med Bear")
    pa = check_price_action(m5)
    for s in pa:
        if "Bull" in s: bull.append(s)
        elif "Bear" in s: bear.append(s)

    av = check_adx(h1)
    if av > 25:
        if c1 == "BULL": bull.append("ADX Strong UP")
        if c1 == "BEAR": bear.append("ADX Strong DN")
    if bollinger_squeeze(m15):
        bull.append("BB Squeeze")
        bear.append("BB Squeeze")
    kc = check_keltner(h1)
    if kc:
        if "Above" in kc: bull.append(kc)
        else: bear.append(kc)
    sr = check_stoch_rsi(m15)
    if sr > 80: bear.append("StochRSI OB")
    if sr < 20: bull.append("StochRSI OS")
    ich = check_ichimoku(h1)
    if ich:
        if "Bull" in ich: bull.append(ich)
        else: bear.append(ich)
    dc = check_donchian(m15)
    if dc:
        if "Upper" in dc: bull.append(dc)
        else: bear.append(dc)

    fib_r = fib_retracement(daily)
    if fib_r:
        for k, v in fib_r.items():
            if k not in ["high", "low"] and abs(cur - v) < 3:
                bull.append("At Fib " + k)
                bear.append("At Fib " + k)
    fib_e = fib_extension(daily)
    if fib_e:
        for k, v in fib_e.items():
            if abs(cur - v) < 3:
                bull.append("Near Ext " + k)
    cam = camarilla_pivots(daily)
    if cam:
        if cur > cam["H3"]: bull.append("Above Cam H3")
        if cur < cam["L3"]: bear.append("Below Cam L3")
    wd = woodie_pivots(daily)
    if wd:
        if cur > wd["P"]: bull.append("Above Woodie P")
        else: bear.append("Below Woodie P")
    vp = volume_profile(m5)
    if vp:
        if abs(cur - vp["POC"]) < 3: bull.append("At POC")
        if cur > vp["VAH"]: bull.append("Above VAH")
        if cur < vp["VAL"]: bear.append("Below VAL")
    kz = is_killzone()
    if kz: bull.append(kz + " High Prob")
    if is_silver_bullet():
        bull.append("Silver Bullet")
        bear.append("Silver Bullet")
    ote_lo, ote_hi = ote_zone(daily)
    if ote_lo and ote_hi and ote_lo <= cur <= ote_hi:
        bull.append("At OTE Buy")
        bear.append("At OTE Sell")
    hs = detect_hs_pattern(h1)
    if hs:
        if "Bear" in hs: bear.append(hs)
        else: bull.append(hs)
    dt = detect_double_top_bottom(h1)
    if dt:
        if "Bear" in dt: bear.append(dt)
        else: bull.append(dt)
    pwh, pwl = prev_week_hl(daily)
    if pwh and abs(cur - pwh) < 5: bear.append("Near Prev Week High")
    if pwl and abs(cur - pwl) < 5: bull.append("Near Prev Week Low")
    pmh, pml = prev_month_hl(daily)
    if pmh and abs(cur - pmh) < 8: bear.append("Near Prev Month High")
    if pml and abs(cur - pml) < 8: bull.append("Near Prev Month Low")
    dp = day_of_week_pattern()
    if dp: bull.append(dp)
    try:
        silver = fetch_symbol("XAG/USD")
        if silver is not None and len(silver) >= 2:
            sil_ch = ((silver["close"].iloc[-1] - silver["close"].iloc[-2]) / silver["close"].iloc[-2]) * 100
            if sil_ch > 0.3: bull.append("Silver Strong")
            if sil_ch < -0.3: bear.append("Silver Weak")
    except Exception:
        pass

    liq = session_liquidity_map(h1, m15, cur)
    et, es, ei = detect_session_exhaustion(m5, m15, h1, session, cur)
    if et and es >= 4:
        la = state.get("exhaust_alerted", "")
        ak = session + "_" + et
        if la != ak:
            state["exhaust_alerted"] = ak
            save_state(state)
            a = "📊 SESSION LEVELS\n\nSession: " + session
            a += "\nRange: " + str(ei["range_used"]) + "%"
            a += "\nHigh: " + str(ei["sess_high"]) + " Mid: " + str(ei["mid"]) + " Low: " + str(ei["sess_low"])
            if et == "BEAR_EXHAUST":
                a += "\n\n🟢 Above " + str(ei["mid"]) + " = BUY"
                a += "\n🔴 Below " + str(ei["sess_low"]) + " = SELL"
            else:
                a += "\n\n🟢 Above " + str(ei["sess_high"]) + " = BUY"
                a += "\n🔴 Below " + str(ei["mid"]) + " = SELL"
            send_telegram(a)

    existing = state.get("position")
    counter = state.get("counter")
    if existing and not counter and sc:
        cs, csc = detect_counter_signal(existing["side"], h1, m15, m5, cur, session)
        if cs:
            if cs == "LONG":
                csl, ctp = cur - 10, cur + 15
            else:
                csl, ctp = cur + 10, cur - 15
            state["counter"] = {"side": cs, "entry": cur, "sl": csl, "tp": ctp, "opened_at": now_str}
            p = state["position"]
            if p["side"] == "SHORT":
                p["tp1"] += 10; p["tp2"] += 10; p["tp3"] += 10
            else:
                p["tp1"] -= 10; p["tp2"] -= 10; p["tp3"] -= 10
            state["position"] = p
            save_state(state)
            m = "⚡ HEDGE " + ("BUY" if cs == "LONG" else "SELL")
            m += "\nScore: " + str(csc) + "/8\nEntry: " + str(round(cur, 2))
            send_telegram(m)
            return

    if is_session_grace_period(state, session):
        print("Grace period")
        return

    bS, sS = len(bull), len(bear)
    conf = max(bS, sS)
    if conf < 10:
        if bS == sS:
            k = now_utc.strftime("%Y-%m-%d") + "_" + session
            if state.get("neutral_alerted", "") != k:
                state["neutral_alerted"] = k
                save_state(state)
                t = "⚪ NEUTRAL\n\nBull: " + str(bS) + " | Bear: " + str(sS)
                t += "\n" + str(round(cur, 2)) + " current"
                if liq["nearest_bsl"]:
                    t += "\n↑ " + str(round(liq["nearest_bsl"], 2)) + " = BUY"
                if liq["nearest_ssl"]:
                    t += "\n↓ " + str(round(liq["nearest_ssl"], 2)) + " = SELL"
                send_telegram(t)
        return

    action = "LONG" if bS > sS else "SHORT"
    if not check_htf_alignment(h1, m15, m5, action):
        print("HTF not aligned")
        return

    last_price = state.get("last_signal_price", 0)
    if last_price:
        drift = abs(cur - last_price)
        if drift > 15:
            print("Late signal drift " + str(round(drift, 1)) + " - skip")
            return

    if existing:
        if existing["side"] == action:
            lh = state.get("last_hold_msg", "")
            sh = True
            if lh:
                try:
                    lhd = datetime.strptime(lh, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
                    if (now_utc - lhd).total_seconds() / 60 < 60:
                        sh = False
                except Exception:
                    pass
            if sh:
                state["last_hold_msg"] = now_str
                save_state(state)
                ht = "⏸️ HOLD - " + ("BUY" if existing["side"] == "LONG" else "SELL")
                ht += "\nEntry: " + str(round(existing["entry"], 2)) + " Current: " + str(round(cur, 2))
                ht += "\nSL: " + str(round(existing["sl"], 2))
                send_telegram(ht)
            return
        else:
            if abs(bS - sS) >= 3:
                state["position"] = None
                send_telegram("🔄 FLIP - " + existing["side"] + " closed, " + action + " opening")
            else:
                return

    entry = cur
    av = atr(h1)
    if av == 0:
        av = 15
    sld = max(av, 15)
    if action == "LONG":
        sl = cur - sld
        tp1 = cur + sld
        tp2 = cur + sld * 1.5
        tp3 = cur + sld * 2
    else:
        sl = cur + sld
        tp1 = cur - sld
        tp2 = cur - sld * 1.5
        tp3 = cur - sld * 2

    state["position"] = {"side": action, "entry": entry, "sl": round(sl, 2),
                          "tp1": round(tp1, 2), "tp2": round(tp2, 2), "tp3": round(tp3, 2),
                          "tp_hits": [], "warn_hits": [], "opened_at": now_str}
    state["last_signal_price"] = cur
    state["last_signal_time"] = now_str
    state["last_signal_dir"] = action
    state["today_trades"] = state.get("today_trades", 0) + 1
    state["last_hold_msg"] = now_str
    save_state(state)

    mode, bp, sp = get_market_mode(bull, bear, h1, m15, m5)
    if action == "LONG":
        text = "🟢 BUY GOLD"
    else:
        text = "🔴 SELL GOLD"
    text += "\n\nEntry: " + str(round(entry, 2))
    text += "\nStop Loss: " + str(round(sl, 2))
    text += "\n\nTarget 1: " + str(round(tp1, 2))
    text += "\nTarget 2: " + str(round(tp2, 2))
    text += "\nTarget 3: " + str(round(tp3, 2))
    text += "\n\nDate: " + format_ist_date(now_utc)
    text += "\nTime: " + format_ist_time(now_utc)
    text += "\nSession: " + session
    text += "\nMode: " + mode + " (B:" + str(bp) + "% S:" + str(sp) + "%)"
    if liq["nearest_bsl"]:
        text += "\n\nLiquidity Above: " + str(round(liq["nearest_bsl"], 2))
    if liq["nearest_ssl"]:
        text += "\nLiquidity Below: " + str(round(liq["nearest_ssl"], 2))
    if pre:
        text += "\n\n⚠️ NEWS: " + pre + "\nClose trades"
    if news_days:
        text += "\n\n📅 " + ", ".join(news_days)
    send_telegram(text)
    print("Sent: " + action + " Conf " + str(conf))


if __name__ == "__main__":
    run()
