# ============================================================
#   USDJPY BOT V5 — FINAL
#   1H Signals | 1M Reversal | 15min Updates
#   73-78% WR Strategy
# ============================================================

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
import os
from datetime import datetime, timedelta, timezone
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIG (GitHub Secrets se)
# ============================================================
BOT_TOKEN = os.getenv('TG_TOKEN', "8941406579:AAFy7sk6aW6ltjPF7WwFf7k2cSHAcVhD3OA")
CHAT_ID = os.getenv('TG_CHAT', "5885172416")
STATE_FILE = "bot_state.json"

MAX_DAILY = 3
COOLDOWN_ANY_H = 3
COOLDOWN_SAME_DIR_H = 12
SIGNAL_CHECK_SEC = 60
PRICE_UPDATE_MIN = 15
DATA_1H_CACHE_SEC = 300
DATA_1M_CACHE_SEC = 60
MAX_CANDLE_AGE_MIN = 90
MAX_ACTIVE_AGE_H = 24
PIP = 0.01
PIP_VALUE = 6.7
ACCOUNT = 1000
RISK_PCT = 1.0

# GitHub Actions me single run hota hai, isliye time limit
MAX_RUN_MINUTES = 5

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================
# STATE
# ============================================================
def load_state():
    d = {
        'last_buy_time': None,
        'last_sell_time': None,
        'daily_count': {},
        'startup_date': None,
        'active_signal': None,
        'processed_candles': []
    }
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                x = json.load(f)
            if isinstance(x, dict):
                for k in d:
                    if k in x: d[k] = x[k]
            if not isinstance(d['daily_count'], dict): d['daily_count'] = {}
            if not isinstance(d['processed_candles'], list): d['processed_candles'] = []
        except Exception as e:
            print(f"State load err: {e}")
    return d

def save_state(s):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(s, f, default=str)
    except Exception as e:
        print(f"Save err: {e}")

# ============================================================
# TELEGRAM
# ============================================================
def send_tg(msg, retries=3):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    for i in range(retries):
        try:
            r = requests.post(url, data=data, timeout=15)
            if r.status_code == 200: return True
            if r.status_code == 429:
                w = int(r.headers.get('Retry-After', 30))
                time.sleep(w)
            else:
                print(f"TG {r.status_code}: {r.text[:80]}")
        except Exception as e:
            print(f"Try {i+1}: {e}")
            time.sleep(5)
    return False

# ============================================================
# DATA
# ============================================================
def get_1h():
    try:
        end = datetime.now(UTC)
        df = yf.download('JPY=X', start=end-timedelta(days=59), end=end,
                         interval='1h', progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[['Open','High','Low','Close','Volume']].dropna()
        return df if len(df) > 210 else None
    except Exception as e:
        print(f"1H err: {e}")
        return None

def get_1m():
    try:
        end = datetime.now(UTC)
        df = yf.download('JPY=X', start=end-timedelta(days=2), end=end,
                         interval='1m', progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df[['Open','High','Low','Close','Volume']].dropna()
        return df if len(df) >= 20 else None
    except Exception as e:
        print(f"1M err: {e}")
        return None

# ============================================================
# INDICATORS
# ============================================================
def add_ind(df):
    df = df.copy()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

    delta = df['Close'].diff()
    gain = (delta.where(delta>0, 0)).rolling(14).mean()
    loss = (-delta.where(delta<0, 0)).rolling(14).mean()
    df['RSI'] = 100 - (100/(1 + gain/loss))

    df['Date'] = df.index.date
    daily = df.groupby('Date').agg(PDH=('High','max'), PDL=('Low','min')).shift(1)
    daily.index.name = 'Date'
    df = df.merge(daily, left_on='Date', right_index=True, how='left')

    asian = df[df.index.hour < 7].groupby('Date').agg(AH=('High','max'), AL=('Low','min'))
    asian.index.name = 'Date'
    df = df.merge(asian, left_on='Date', right_index=True, how='left')

    df['PDH'] = df['PDH'].fillna(df['High'])
    df['PDL'] = df['PDL'].fillna(df['Low'])
    df['AH'] = df['AH'].fillna(df['High'])
    df['AL'] = df['AL'].fillna(df['Low'])

    return df.dropna(subset=['EMA_200', 'RSI'])

# ============================================================
# SIGNAL SCAN
# ============================================================
def scan_signals(df, state):
    signals = []
    now = datetime.now(UTC)

    for idx in range(max(5, len(df)-6), len(df)-1):
        c = df.iloc[idx]
        t = df.index[idx]

        try:
            t_aware = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        except: continue

        age_min = (now - t_aware).total_seconds() / 60
        if age_min > MAX_CANDLE_AGE_MIN: continue

        cid = t.isoformat()
        if cid in state.get('processed_candles', []): continue

        h = t.hour
        in_morning = 6 <= h <= 10
        in_evening = 13 <= h <= 17
        if not (in_morning or in_evening): continue

        ist_t = t_aware.astimezone(IST)
        day = str(ist_t.date())
        if state['daily_count'].get(day, 0) >= MAX_DAILY: continue

        ah, al, pdh, pdl = c['AH'], c['AL'], c['PDH'], c['PDL']
        up = c['Close'] > c['EMA_200'] and c['EMA_50'] > c['EMA_200']
        dn = c['Close'] < c['EMA_200'] and c['EMA_50'] < c['EMA_200']

        sw_b = (c['Low'] < al and c['Close'] > al) or (c['Low'] < pdl and c['Close'] > pdl)
        sw_s = (c['High'] > ah and c['Close'] < ah) or (c['High'] > pdh and c['Close'] < pdh)
        pb_b = up and c['Low'] <= c['EMA_50'] and c['Close'] > c['EMA_50']
        pb_s = dn and c['High'] >= c['EMA_50'] and c['Close'] < c['EMA_50']

        e = float(c['Close'])
        sig = None

        if up and (sw_b or pb_b) and 40 < c['RSI'] < 75:
            sig = {'dir':'BUY','entry':e,'sl':e-0.15,'tp':e+0.20,
                   'time':t_aware,'session':'Morning' if in_morning else 'Evening'}
        elif dn and (sw_s or pb_s) and 25 < c['RSI'] < 60:
            sig = {'dir':'SELL','entry':e,'sl':e+0.15,'tp':e-0.20,
                   'time':t_aware,'session':'Morning' if in_morning else 'Evening'}

        if sig:
            sig['id'] = f"{sig['dir']}_{cid}"
            sig['candle_id'] = cid
            signals.append(sig)

    return signals

# ============================================================
# REVERSAL CHECK
# ============================================================
def check_reversal(df1m, state):
    active = state.get('active_signal')
    if not active: return None

    try:
        at = datetime.fromisoformat(active['time']) if isinstance(active['time'], str) else active['time']
        if at.tzinfo is None: at = at.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC) - at.astimezone(UTC)).total_seconds() / 3600
        if age_h > MAX_ACTIVE_AGE_H:
            return {'type':'EXPIRED','price':float(df1m.iloc[-1]['Close']),'pips':0}
    except Exception as e:
        print(f"Age err: {e}")

    if len(df1m) < 3: return None
    c = df1m.iloc[-2]

    price = float(c['Close'])
    high = float(c['High'])
    low = float(c['Low'])

    d = active['dir']
    sl = float(active['sl'])
    tp = float(active['tp'])

    if d == 'BUY':
        if low <= sl: return {'type':'SL_HIT','price':price,'pips':-15}
        if high >= tp: return {'type':'TP_HIT','price':price,'pips':+20}
    else:
        if high >= sl: return {'type':'SL_HIT','price':price,'pips':-15}
        if low <= tp: return {'type':'TP_HIT','price':price,'pips':+20}
    return None

# ============================================================
# HELPERS
# ============================================================
def calc_lot(sl_pips=15):
    risk_usd = ACCOUNT * RISK_PCT / 100
    lot = round(risk_usd / (sl_pips * PIP_VALUE), 2)
    return max(lot, 0.01)

def is_weekend():
    return datetime.now(UTC).weekday() >= 5

def clean_old_state(state):
    cutoff = str((datetime.now(IST) - timedelta(days=14)).date())
    state['daily_count'] = {k:v for k,v in state['daily_count'].items() if k >= cutoff}
    state['processed_candles'] = state.get('processed_candles', [])[-50:]

# ============================================================
# MESSAGES
# ============================================================
def msg_signal(s):
    emoji = "🟢" if s['dir'] == 'BUY' else "🔴"
    lot = calc_lot(15)
    ist = s['time'].astimezone(IST)
    return f"""{emoji} <b>USDJPY {s['dir']} SIGNAL</b>
━━━━━━━━━━━━━━━━━━
📍 Entry:   {s['entry']:.3f}
🛑 SL:      {s['sl']:.3f} (15 pip)
🎯 TP:      {s['tp']:.3f} (20 pip)
💰 Lot:     {lot} (1% risk)
⏰ Time:    {ist.strftime('%H:%M IST')}
📊 Session: {s['session']}
━━━━━━━━━━━━━━━━━━
💡 <b>Entry NOW at market</b>
📌 100% book at TP
"""

def msg_price(df1m, state):
    c = df1m.iloc[-1]
    price = float(c['Close'])
    ist = datetime.now(IST).strftime('%H:%M IST')
    a = state.get('active_signal')

    base = f"""📊 <b>USDJPY UPDATE</b>
⏰ {ist}
💵 Price: <b>{price:.3f}</b>
"""
    if a:
        try:
            e = float(a['entry']); sl = float(a['sl']); tp = float(a['tp'])
            d = a['dir']
            pips = (price - e)/PIP if d == 'BUY' else (e - price)/PIP
            emoji = "🟢" if d == 'BUY' else "🔴"
            base += f"""
{emoji} Active: {d}
📍 Entry: {e:.3f}
🎯 TP: {tp:.3f} | 🛑 SL: {sl:.3f}
📊 <b>Now: {pips:+.1f} pip</b>
"""
        except: pass
    else:
        base += "\n💤 No active trade"
    return base

def msg_rev(r, a):
    if r['type'] == 'SL_HIT':
        return f"""🚨 <b>SL HIT</b>
━━━━━━━━━━━━━━━━━━
📌 Signal: {a['dir']}
🛑 Price: {r['price']:.3f}
📉 Loss: {r['pips']} pip
━━━━━━━━━━━━━━━━━━
💡 Wait for next signal
"""
    elif r['type'] == 'TP_HIT':
        return f"""🎯 <b>TP HIT</b>
━━━━━━━━━━━━━━━━━━
📌 Signal: {a['dir']}
🎯 Price: {r['price']:.3f}
📈 Profit: +{r['pips']} pip
━━━━━━━━━━━━━━━━━━
💡 Wait for next signal
"""
    else:
        return f"""⏰ <b>SIGNAL EXPIRED</b>
📌 {a['dir']} — 24h crossed
💡 New signal dekho
"""

# ============================================================
# MAIN (Single run — GitHub Actions ke liye)
# ============================================================
print("=" * 60)
print("   USDJPY BOT V5 — RUN")
print("=" * 60)

state = load_state()
clean_old_state(state)

now_utc = datetime.now(UTC)

# Weekend skip
if is_weekend():
    print("Weekend — no trade")
    save_state(state)
    exit(0)

# Startup msg (once per day)
today = str(now_utc.astimezone(IST).date())
if state.get('startup_date') != today:
    send_tg(f"🤖 <b>USDJPY Bot V5</b>\n\n{now_utc.astimezone(IST).strftime('%d-%b %H:%M IST')}")
    state['startup_date'] = today
    save_state(state)

# Fetch data
df1h = get_1h()
if df1h is not None:
    df1h = add_ind(df1h)
    print(f"1H: {len(df1h)} candles")

df1m = get_1m()
if df1m is not None:
    print(f"1M: {len(df1m)} candles")

# Reversal check
if df1m is not None and state.get('active_signal'):
    r = check_reversal(df1m, state)
    if r:
        send_tg(msg_rev(r, state['active_signal']))
        print(f"{r['type']} @ {r['price']:.3f}")
        state['active_signal'] = None
        save_state(state)

# Signal check
if df1h is not None and not state.get('active_signal'):
    # Cooldown
    last_t = None
    for key in ['last_buy_time','last_sell_time']:
        v = state.get(key)
        if v:
            try:
                t = datetime.fromisoformat(v)
                if t.tzinfo is None: t = t.replace(tzinfo=UTC)
                if last_t is None or t > last_t: last_t = t
            except: pass

    cooldown_ok = True
    if last_t:
        age_h = (now_utc - last_t.astimezone(UTC)).total_seconds()/3600
        if age_h < COOLDOWN_ANY_H: cooldown_ok = False

    if cooldown_ok:
        sigs = scan_signals(df1h, state)
        if sigs:
            s = sigs[-1]
            if send_tg(msg_signal(s)):
                print(f"✅ {s['dir']} @ {s['entry']:.3f}")
                state['active_signal'] = s
                pc = state.get('processed_candles', [])
                for x in sigs: pc.append(x['candle_id'])
                state['processed_candles'] = pc[-50:]

                if s['dir'] == 'BUY':
                    state['last_buy_time'] = s['time'].isoformat()
                else:
                    state['last_sell_time'] = s['time'].isoformat()

                day = str(s['time'].astimezone(IST).date())
                dc = state.get('daily_count', {})
                dc[day] = dc.get(day, 0) + 1
                state['daily_count'] = dc
                save_state(state)
        else:
            print("No signal")

# Price update (only if active)
if df1m is not None and state.get('active_signal'):
    send_tg(msg_price(df1m, state))
    print("Price update sent")

save_state(state)
print("✅ Run complete")
