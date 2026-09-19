# ============================================================
#   USDJPY BOT V7 — COMPLETE FINAL CODE
#   1 Min Check | TP1/TP2 | Retest | DST Auto | JSONBin State
# ============================================================

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv('TG_TOKEN', "8941406579:AAFy7sk6aW6ltjPF7WwFf7k2cSHAcVhD3OA")
CHAT_ID = os.getenv('TG_CHAT', "5885172416")
JSONBIN_KEY = os.getenv('JSONBIN_KEY', "")
JSONBIN_ID = os.getenv('JSONBIN_ID', "")
STATE_FILE = "bot_state.json"

MAX_DAILY = 3
COOLDOWN_ANY_H = 3
COOLDOWN_SAME_DIR_H = 12
CHECK_INTERVAL_SEC = 60
PRICE_UPDATE_MIN = 30
LOOP_DURATION_SEC = 300
MAX_CANDLE_AGE_MIN = 90
MAX_ACTIVE_AGE_H = 24
MAX_TRADE_HOURS = 8
PIP = 0.01
PIP_VALUE = 6.7
ACCOUNT = 1000
RISK_PCT = 1.0
SL_PIPS = 20
TP1_PIPS = 20
TP2_PIPS = 40
RETEST_WAIT_MIN = 5

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")

# ============================================================
# STATE
# ============================================================
def default_state():
    return {
        'version': '7.0',
        'startup_date': None,
        'last_buy_time': None,
        'last_sell_time': None,
        'daily_count': {},
        'active_signal': None,
        'pending_order': None,
        'processed_candles': [],
        'running_run': None,
        'running_start': None,
        'last_price_update': None,
        'stats': {'total_trades': 0, 'wins': 0, 'losses': 0, 'total_pips': 0}
    }

def load_state():
    if JSONBIN_KEY and JSONBIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}/latest"
            r = requests.get(url, headers={"X-Master-Key": JSONBIN_KEY}, timeout=10)
            if r.status_code == 200:
                data = r.json().get('record', {})
                d = default_state()
                if isinstance(data, dict):
                    for k in d:
                        if k in data: d[k] = data[k]
                return d
        except Exception as e:
            print(f"JSONBin load err: {e}")
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                d = default_state()
                data = json.load(f)
                for k in d:
                    if k in data: d[k] = data[k]
                return d
        except: pass
    return default_state()

def save_state(s):
    if JSONBIN_KEY and JSONBIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"
            headers = {"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"}
            requests.put(url, json=s, headers=headers, timeout=10)
        except Exception as e:
            print(f"JSONBin save err: {e}")
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(s, f, default=str)
    except: pass

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
                time.sleep(int(r.headers.get('Retry-After', 30)))
            else:
                print(f"TG {r.status_code}")
        except Exception as e:
            print(f"TG try {i+1}: {e}")
            time.sleep(5)
    return False

# ============================================================
# TIME HELPERS
# ============================================================
def get_session_name(now_utc):
    london = now_utc.astimezone(LONDON)
    ny = now_utc.astimezone(NY)
    london_ok = 8 <= london.hour < 17
    ny_ok = 8 <= ny.hour < 17
    if london_ok and ny_ok: return "London+NY"
    if london_ok: return "London"
    if ny_ok: return "NY"
    return "None"

def in_session(now_utc):
    return get_session_name(now_utc) != "None"

def is_weekend():
    now_ist = datetime.now(IST)
    wd = now_ist.weekday()
    h = now_ist.hour
    if wd >= 5: return True
    if wd == 4 and h >= 22: return True
    if wd == 0 and h < 6: return True
    return False

# ============================================================
# DATA
# ============================================================
def get_1h(retries=3):
    for _ in range(retries):
        try:
            end = datetime.now(UTC)
            df = yf.download('JPY=X', start=end-timedelta(days=59), end=end,
                             interval='1h', progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[['Open','High','Low','Close','Volume']].dropna()
            if len(df) > 210: return df
        except Exception as e:
            print(f"1H err: {e}")
            time.sleep(5)
    return None

def get_1m(retries=3):
    for _ in range(retries):
        try:
            end = datetime.now(UTC)
            df = yf.download('JPY=X', start=end-timedelta(days=2), end=end,
                             interval='1m', progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[['Open','High','Low','Close','Volume']].dropna()
            if len(df) >= 20: return df
        except Exception as e:
            print(f"1M err: {e}")
            time.sleep(5)
    return None

def add_indicators(df):
    df = df.copy()
    df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

    delta = df['Close'].diff()
    gain = (delta.where(delta>0, 0)).rolling(14).mean()
    loss = (-delta.where(delta<0, 0)).rolling(14).mean()
    df['RSI'] = 100 - (100/(1 + gain/loss))
    df['Vol_MA'] = df['Volume'].rolling(20).mean()

    df['Date'] = df.index.date
    daily = df.groupby('Date').agg(PDH=('High','max'), PDL=('Low','min'), PDC=('Close','last')).shift(1)
    daily.index.name = 'Date'
    df = df.merge(daily, left_on='Date', right_index=True, how='left')

    asian = df[df.index.hour < 7].groupby('Date').agg(AH=('High','max'), AL=('Low','min'))
    asian.index.name = 'Date'
    df = df.merge(asian, left_on='Date', right_index=True, how='left')

    df['PDH'] = df['PDH'].fillna(df['High'])
    df['PDL'] = df['PDL'].fillna(df['Low'])
    df['PDC'] = df['PDC'].fillna(df['Close'])
    df['AH'] = df['AH'].fillna(df['High'])
    df['AL'] = df['AL'].fillna(df['Low'])

    return df.dropna(subset=['EMA_200', 'RSI'])

# ============================================================
# SIGNAL GRADING
# ============================================================
def grade_signal(c, up, dn, sw_b, sw_s, pb_b, pb_s):
    score = 0
    if sw_b or sw_s: score += 4
    if pb_b or pb_s: score += 2
    if up or dn: score += 2
    try:
        if c['Volume'] > c['Vol_MA'] * 1.5: score += 2
        elif c['Volume'] > c['Vol_MA'] * 1.2: score += 1
    except: pass
    body = abs(c['Close'] - c['Open'])
    rng = c['High'] - c['Low']
    if rng > 0:
        ratio = body / rng
        if ratio > 0.7: score += 2
        elif ratio > 0.5: score += 1
    if up and c['RSI'] > 60: score += 2
    elif dn and c['RSI'] < 40: score += 2
    elif 50 <= c['RSI'] <= 60: score += 1
    return score

def get_grade(score):
    if score >= 12: return "A+"
    if score >= 9: return "A"
    if score >= 6: return "B"
    return "C"

# ============================================================
# RETEST DECISION
# ============================================================
def should_retest(c, up, dn, sw_b, sw_s):
    score = 0
    try:
        body = abs(c['Close'] - c['Open'])
        rng = c['High'] - c['Low']
        if rng > 0 and body/rng > 0.6: score += 1
    except: pass
    try:
        if c['Volume'] > c['Vol_MA'] * 1.2: score += 1
    except: pass
    if (up and c['EMA_50'] > c['EMA_200']) or (dn and c['EMA_50'] < c['EMA_200']): score += 1
    if (up and c['RSI'] > 60) or (dn and c['RSI'] < 40): score += 1
    if sw_b or sw_s: score += 1
    return score >= 4

# ============================================================
# SIGNAL SCAN
# ============================================================
def scan_signals(df, state, now_utc):
    signals = []
    for idx in range(max(5, len(df)-6), len(df)-1):
        c = df.iloc[idx]
        t = df.index[idx]
        try:
            t_aware = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        except: continue

        age_min = (now_utc - t_aware).total_seconds() / 60
        if age_min > MAX_CANDLE_AGE_MIN: continue

        cid = t.isoformat()
        if cid in state.get('processed_candles', []): continue
        if not in_session(t_aware): continue

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
            sig = {'dir':'BUY','entry':e,'sl':e-SL_PIPS*PIP,
                   'tp1':e+TP1_PIPS*PIP,'tp2':e+TP2_PIPS*PIP,
                   'time':t_aware,'session':get_session_name(t_aware)}
        elif dn and (sw_s or pb_s) and 25 < c['RSI'] < 60:
            sig = {'dir':'SELL','entry':e,'sl':e+SL_PIPS*PIP,
                   'tp1':e-TP1_PIPS*PIP,'tp2':e-TP2_PIPS*PIP,
                   'time':t_aware,'session':get_session_name(t_aware)}

        if sig:
            score = grade_signal(c, up, dn, sw_b, sw_s, pb_b, pb_s)
            sig['score'] = score
            sig['grade'] = get_grade(score)
            sig['should_retest'] = should_retest(c, up, dn, sw_b, sw_s)
            sig['tp1_hit'] = False
            sig['tp2_hit'] = False
            sig['sl_moved_be'] = False
            sig['id'] = f"{sig['dir']}_{cid}"
            sig['candle_id'] = cid
            signals.append(sig)

    return signals

# ============================================================
# HOLD CHECK
# ============================================================
def hold_check(df1h, active):
    if df1h is None or len(df1h) < 5: return 'CLOSE'
    c = df1h.iloc[-2]
    score = 0
    if active['dir'] == 'BUY' and c['Close'] > c['Open']: score += 1
    elif active['dir'] == 'SELL' and c['Close'] < c['Open']: score += 1
    try:
        if c['Volume'] > c['Vol_MA']: score += 1
    except: pass
    if active['dir'] == 'BUY' and c['Close'] > c['EMA_50']: score += 1
    elif active['dir'] == 'SELL' and c['Close'] < c['EMA_50']: score += 1
    if active['dir'] == 'BUY' and 45 < c['RSI'] < 75: score += 1
    elif active['dir'] == 'SELL' and 25 < c['RSI'] < 55: score += 1
    if active['dir'] == 'BUY' and c['EMA_50'] > c['EMA_200']: score += 1
    elif active['dir'] == 'SELL' and c['EMA_50'] < c['EMA_200']: score += 1
    if score >= 5: return 'HOLD'
    if score >= 3: return 'TRAIL'
    return 'CLOSE'

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
            return {'type':'EXPIRED','price':float(df1m.iloc[-1]['Close'])}
    except: pass

    if len(df1m) < 3: return None
    c = df1m.iloc[-2]

    price = float(c['Close'])
    high = float(c['High'])
    low = float(c['Low'])

    d = active['dir']
    sl = float(active['sl'])
    tp1 = float(active['tp1'])
    tp2 = float(active['tp2'])

    if d == 'BUY':
        if not active['tp1_hit']:
            if low <= sl: return {'type':'SL_HIT','price':price,'pips':-SL_PIPS}
            if high >= tp1: return {'type':'TP1_HIT','price':price,'pips':TP1_PIPS}
        elif not active['tp2_hit']:
            if low <= sl: return {'type':'BE_HIT','price':price,'pips':0}
            if high >= tp2: return {'type':'TP2_HIT','price':price,'pips':TP1_PIPS + TP2_PIPS}
    else:
        if not active['tp1_hit']:
            if high >= sl: return {'type':'SL_HIT','price':price,'pips':-SL_PIPS}
            if low <= tp1: return {'type':'TP1_HIT','price':price,'pips':TP1_PIPS}
        elif not active['tp2_hit']:
            if high >= sl: return {'type':'BE_HIT','price':price,'pips':0}
            if low <= tp2: return {'type':'TP2_HIT','price':price,'pips':TP1_PIPS + TP2_PIPS}
    return None

# ============================================================
# HELPERS
# ============================================================
def calc_lot(sl_pips=SL_PIPS):
    risk_usd = ACCOUNT * RISK_PCT / 100
    return max(round(risk_usd / (sl_pips * PIP_VALUE), 2), 0.01)

def clean_state(state):
    cutoff = str((datetime.now(IST) - timedelta(days=14)).date())
    state['daily_count'] = {k:v for k,v in state['daily_count'].items() if k >= cutoff}
    state['processed_candles'] = state.get('processed_candles', [])[-50:]

# ============================================================
# MESSAGES
# ============================================================
def msg_signal(s):
    emoji = "🟢" if s['dir'] == 'BUY' else "🔴"
    lot = calc_lot()
    ist = s['time'].astimezone(IST)
    retest_txt = "Retest + 5 pip" if s['should_retest'] else "Market now"
    return f"""{emoji} <b>USDJPY {s['dir']} SIGNAL</b>
━━━━━━━━━━━━━━━━━━
📊 Grade: <b>{s['grade']}</b> ({s['score']}/15)
📍 Entry:   {s['entry']:.3f}
🛑 SL:      {s['sl']:.3f} ({SL_PIPS} pip)
🎯 TP1:     {s['tp1']:.3f} ({TP1_PIPS} pip)
🎯 TP2:     {s['tp2']:.3f} ({TP2_PIPS} pip)
💰 Lot:     {lot} (1% risk)
⏰ Time:    {ist.strftime('%H:%M IST')}
📊 Session: {s['session']}
━━━━━━━━━━━━━━━━━━
💡 Entry: <b>{retest_txt}</b>
📌 TP1: 50% book + SL to BE
📌 TP2: 50% close
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
        e = float(a['entry'])
        d = a['dir']
        pips = (price - e)/PIP if d == 'BUY' else (e - price)/PIP
        emoji = "🟢" if d == 'BUY' else "🔴"
        status = f"""
{emoji} {d} Active ({a['grade']})
📍 Entry: {e:.3f}
🎯 TP1: {float(a['tp1']):.3f} {'✅' if a.get('tp1_hit') else ''}
🎯 TP2: {float(a['tp2']):.3f} {'✅' if a.get('tp2_hit') else ''}
🛑 SL: {float(a['sl']):.3f}
📊 <b>Now: {pips:+.1f} pip</b>
"""
        base += status
    else:
        base += "\n💤 No active trade"
    return base

def msg_reversal(r, a):
    if r['type'] == 'SL_HIT':
        return f"""🚨 <b>SL HIT</b>
📌 {a['dir']} @ {float(a['entry']):.3f}
🛑 Price: {r['price']:.3f}
📉 Loss: {r['pips']} pip
💡 Wait for next signal
"""
    elif r['type'] == 'TP1_HIT':
        return f"""🎯 <b>TP1 HIT</b>
📌 {a['dir']} @ {float(a['entry']):.3f}
🎯 Price: {r['price']:.3f}
📈 Profit: +{r['pips']} pip

✅ 50% book done
🛑 SL moved to BE
💡 Wait for TP2
"""
    elif r['type'] == 'TP2_HIT':
        return f"""🎯 <b>TP2 HIT — DONE</b>
📌 {a['dir']} @ {float(a['entry']):.3f}
🎯 Price: {r['price']:.3f}
📈 Profit: +{r['pips']} pip
💰 Full trade closed
💡 Wait for next signal
"""
    elif r['type'] == 'BE_HIT':
        return f"""⚪ <b>BE HIT</b>
📌 {a['dir']} closed at BE
💡 Wait for next signal
"""
    else:
        return f"""⏰ <b>EXPIRED</b>
📌 {a['dir']} — 24h crossed
"""

# ============================================================
# MAIN LOOP
# ============================================================
print("=" * 60)
print("   USDJPY BOT V7 — RUN")
print("=" * 60)

state = load_state()
clean_state(state)

if is_weekend():
    print("Weekend — exit")
    save_state(state)
    exit(0)

now_utc = datetime.now(UTC)
today = str(now_utc.astimezone(IST).date())

if state.get('startup_date') != today:
    send_tg(f"🤖 <b>USDJPY Bot V7</b>\n{now_utc.astimezone(IST).strftime('%d-%b %H:%M IST')}")
    state['startup_date'] = today
    save_state(state)

df1h = get_1h()
if df1h is not None:
    df1h = add_indicators(df1h)

loop_start = time.time()
check_count = 0

while (time.time() - loop_start) < LOOP_DURATION_SEC:
    check_count += 1
    now_utc = datetime.now(UTC)

    # Refresh 1H every 3rd check (3 min)
    if check_count == 1 or check_count % 3 == 0:
        d = get_1h()
        if d is not None: df1h = add_indicators(d)

    # Fetch 1M only if active signal
    df1m = get_1m() if state.get('active_signal') else None

    # REVERSAL CHECK
    if df1m is not None and state.get('active_signal'):
        r = check_reversal(df1m, state)
        if r:
            a = state['active_signal']
            send_tg(msg_reversal(r, a))
            print(f"[{now_utc.strftime('%H:%M:%S')}] {r['type']}")

            if r['type'] == 'TP1_HIT':
                a['tp1_hit'] = True
                a['sl'] = a['entry']  # BE
                a['sl_moved_be'] = True
                state['active_signal'] = a
                save_state(state)
            else:
                # TP2, SL, BE, EXPIRED
                state['active_signal'] = None
                state['stats']['total_trades'] = state['stats'].get('total_trades', 0) + 1
                if r['type'] in ['TP1_HIT','TP2_HIT']:
                    state['stats']['wins'] = state['stats'].get('wins', 0) + 1
                elif r['type'] == 'SL_HIT':
                    state['stats']['losses'] = state['stats'].get('losses', 0) + 1
                state['stats']['total_pips'] = state['stats'].get('total_pips', 0) + r.get('pips', 0)
                save_state(state)

    # NEW SIGNAL CHECK
    if df1h is not None and not state.get('active_signal'):
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
            sigs = scan_signals(df1h, state, now_utc)
            if sigs:
                s = sigs[-1]
                if s['grade'] != 'C':
                    if send_tg(msg_signal(s)):
                        print(f"[{now_utc.strftime('%H:%M:%S')}] ✅ {s['dir']} {s['grade']} @ {s['entry']:.3f}")
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

    # PRICE UPDATE (every 30 min)
    last_pu = state.get('last_price_update')
    do_update = True
    if last_pu:
        try:
            last_dt = datetime.fromisoformat(last_pu)
            if last_dt.tzinfo is None: last_dt = last_dt.replace(tzinfo=UTC)
            mins = (now_utc - last_dt.astimezone(UTC)).total_seconds()/60
            if mins < PRICE_UPDATE_MIN: do_update = False
        except: pass

    if do_update and state.get('active_signal'):
        d1m = df1m if df1m is not None else get_1m()
        if d1m is not None:
            send_tg(msg_price(d1m, state))
            print(f"[{now_utc.strftime('%H:%M:%S')}] 💰 Price update")
            state['last_price_update'] = now_utc.isoformat()
            save_state(state)

    # Wait 60 sec
    if (time.time() - loop_start) < LOOP_DURATION_SEC - 60:
        time.sleep(CHECK_INTERVAL_SEC)

save_state(state)
print("✅ Run complete")
