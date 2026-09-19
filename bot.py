# ============================================================
#   USDJPY BOT V9 — FINAL PRODUCTION
#   All Fixes + 3 Minor Fixed:
#   - Retest actual monitoring
#   - Concurrent lock tightened
#   - DXY last-closed candle
# ============================================================

import yfinance as yf
import pandas as pd
import numpy as np
import requests
import time
import json
import os
import uuid
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import warnings
warnings.filterwarnings('ignore')

# ============================================================
# CONFIG
# ============================================================
BOT_TOKEN = os.getenv('TG_TOKEN')
CHAT_ID = os.getenv('TG_CHAT')
JSONBIN_KEY = os.getenv('JSONBIN_KEY')
JSONBIN_ID = os.getenv('JSONBIN_ID')
STATE_FILE = "bot_state.json"

MAX_DAILY = 3
COOLDOWN_ANY_H = 3
COOLDOWN_SAME_DIR_H = 12
MAX_CANDLE_AGE_MIN = 15
MAX_ACTIVE_AGE_H = 24
MAX_TRADE_HOURS = 8
SL_PIPS = 20
TP1_PIPS = 20
TP2_PIPS = 40
PIP = 0.01
PIP_VALUE = 6.7
ACCOUNT = 1000
RISK_A_PLUS = 1.0
RISK_A = 1.0
RISK_B = 0.5
DAILY_LOSS_PCT = 3.0
WEEKLY_LOSS_PCT = 6.0
MONTHLY_LOSS_PCT = 10.0
RETEST_OFFSET_PIP = 5
RETEST_WAIT_MIN = 5
RUN_LOCK_MAX_SEC = 240  # FIX 2: 4 min (was 5)

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
NY = ZoneInfo("America/New_York")

# ============================================================
# STATE
# ============================================================
def default_state():
    return {
        'version': '9.0',
        'startup_date': None,
        'last_buy_time': None,
        'last_sell_time': None,
        'daily_count': {},
        'daily_pnl': {},
        'weekly_pnl': {},
        'monthly_pnl': {},
        'active_signal': None,
        'pending_order': None,
        'processed_candles': [],
        'last_price_update': None,
        'consecutive_losses': 0,
        'running_run': None,
        'running_start': None,
        'stats': {'total': 0, 'wins': 0, 'losses': 0, 'pips': 0}
    }

def load_state():
    d = default_state()
    if JSONBIN_KEY and JSONBIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}/latest"
            r = requests.get(url, headers={"X-Master-Key": JSONBIN_KEY}, timeout=10)
            if r.status_code == 200:
                data = r.json().get('record', {})
                if isinstance(data, dict):
                    for k in d:
                        if k in data: d[k] = data[k]
                print("✅ State loaded")
                return d
        except Exception as e:
            print(f"State load err: {e}")
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            for k in d:
                if k in data: d[k] = data[k]
        except: pass
    return d

def save_state(s):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(s, f, default=str)
    except: pass
    if JSONBIN_KEY and JSONBIN_ID:
        try:
            url = f"https://api.jsonbin.io/v3/b/{JSONBIN_ID}"
            requests.put(url, json=s,
                        headers={"X-Master-Key": JSONBIN_KEY, "Content-Type": "application/json"},
                        timeout=10)
        except Exception as e:
            print(f"State save err: {e}")

# ============================================================
# TELEGRAM
# ============================================================
def esc(t):
    return html.escape(str(t), quote=False)

def send_tg(msg, retries=3):
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Secrets missing")
        return False
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
# TIME
# ============================================================
def get_session_name(now_utc):
    london = now_utc.astimezone(LONDON)
    ny = now_utc.astimezone(NY)
    lo = 8 <= london.hour < 17
    ny_ok = 8 <= ny.hour < 17
    if lo and ny_ok: return "London+NY"
    if lo: return "London"
    if ny_ok: return "NY"
    return "None"

def in_session(now_utc):
    return get_session_name(now_utc) != "None"

def is_weekend():
    n = datetime.now(IST)
    wd, h = n.weekday(), n.hour
    if wd == 5 or wd == 6: return True
    if wd == 4 and h >= 22: return True
    if wd == 0 and h < 6: return True
    return False

def ist_date(dt): return str(dt.astimezone(IST).date())
def ist_week(dt):
    y, w, _ = dt.astimezone(IST).isocalendar()
    return f"{y}-W{w:02d}"
def ist_month(dt): return dt.astimezone(IST).strftime("%Y-%m")

# ============================================================
# DATA FETCH
# ============================================================
def fetch(ticker, days, interval, retries=3):
    for _ in range(retries):
        try:
            end = datetime.now(UTC)
            df = yf.download(ticker, start=end-timedelta(days=days), end=end,
                             interval=interval, progress=False, auto_adjust=False)
            if df is None or len(df) == 0: continue
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            cols = ['Open','High','Low','Close']
            if 'Volume' in df.columns: cols.append('Volume')
            df = df[cols].dropna()
            if 'Volume' not in df.columns: df['Volume'] = 1.0
            df = df[df['Close'] > 0]
            df = df[~df.index.duplicated(keep='last')]
            if len(df) > 5: return df
        except Exception as e:
            print(f"{ticker} err: {e}")
            time.sleep(3)
    return None

def get_1h():
    df = fetch('JPY=X', 59, '1h')
    return df if df is not None and len(df) > 210 else None

def get_4h():
    df = fetch('JPY=X', 120, '4h')
    return df if df is not None and len(df) > 210 else None

def get_1m():
    df = fetch('JPY=X', 2, '1m')
    return df if df is not None and len(df) >= 20 else None

def get_dxy():
    df = fetch('DX-Y.NYB', 30, '1h')
    return df if df is not None and len(df) > 50 else None

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
    vol_sum = df['Volume'].sum()
    df['Vol_MA'] = df['Volume'].rolling(20).mean() if vol_sum > 0 else 1.0
    df['Vol_MA'] = df['Vol_MA'].replace(0, 1.0).fillna(1.0)
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
    return df.dropna(subset=['EMA_200','RSI'])

# ============================================================
# FIX 3: DXY bias using LAST CLOSED candle
# ============================================================
def get_dxy_bias(dxy_df):
    try:
        if dxy_df is None or len(dxy_df) < 6: return None
        # Use last CLOSED candle (not forming)
        last = dxy_df['Close'].iloc[-2]
        prev = dxy_df['Close'].iloc[-6]
        diff = (last - prev) / prev * 100
        if diff > 0.05: return 'UP'
        if diff < -0.05: return 'DOWN'
        return 'FLAT'
    except: return None

def get_4h_bias(df4h):
    if df4h is None or len(df4h) < 210: return None
    d = add_ind(df4h)
    if len(d) < 3: return None
    c = d.iloc[-2]  # Last CLOSED
    if c['Close'] > c['EMA_200'] and c['EMA_50'] > c['EMA_200']: return 'UP'
    if c['Close'] < c['EMA_200'] and c['EMA_50'] < c['EMA_200']: return 'DOWN'
    return 'MIXED'

# ============================================================
# GRADING
# ============================================================
def grade(c, up, dn, sw_b, sw_s, pb_b, pb_s, dxy_bias, h4_bias, sdir):
    s = 0
    if sw_b or sw_s: s += 4
    if pb_b or pb_s: s += 2
    if up or dn: s += 2
    if h4_bias == 'UP' and sdir == 'BUY': s += 2
    elif h4_bias == 'DOWN' and sdir == 'SELL': s += 2
    try:
        if c['Vol_MA'] > 0:
            if c['Volume'] > c['Vol_MA'] * 1.5: s += 2
            elif c['Volume'] > c['Vol_MA'] * 1.2: s += 1
    except: pass
    body = abs(c['Close'] - c['Open'])
    rng = c['High'] - c['Low']
    if rng > 0:
        r = body / rng
        if r > 0.7: s += 2
        elif r > 0.5: s += 1
    if sdir == 'BUY' and c['RSI'] > 60: s += 2
    elif sdir == 'SELL' and c['RSI'] < 40: s += 2
    if dxy_bias == 'UP' and sdir == 'BUY': s += 1
    elif dxy_bias == 'DOWN' and sdir == 'SELL': s += 1
    if s >= 12: return 'A+', s
    if s >= 9: return 'A', s
    if s >= 6: return 'B', s
    return 'C', s

def risk_for_grade(g):
    if g == 'A+': return RISK_A_PLUS
    if g == 'A': return RISK_A
    if g == 'B': return RISK_B
    return 0

def should_retest(c):
    s = 0
    try:
        body = abs(c['Close'] - c['Open'])
        rng = c['High'] - c['Low']
        if rng > 0 and body/rng > 0.6: s += 1
    except: pass
    try:
        if c['Vol_MA'] > 0 and c['Volume'] > c['Vol_MA'] * 1.2: s += 1
    except: pass
    if c['RSI'] > 60 or c['RSI'] < 40: s += 1
    if c['Close'] > c['EMA_200'] or c['Close'] < c['EMA_200']: s += 1
    return s >= 3

# ============================================================
# REVERSAL SIGNALS
# ============================================================
def detect_reversal(df1m, active):
    if df1m is None or len(df1m) < 10: return 0
    n = 0
    c = df1m.iloc[-3]; p = df1m.iloc[-4]
    d = active['dir']
    if d == 'BUY' and p['Close'] > p['Open'] and c['Close'] < c['Open'] and c['Close'] < p['Open']:
        n += 1
    elif d == 'SELL' and p['Close'] < p['Open'] and c['Close'] > c['Open'] and c['Close'] > p['Open']:
        n += 1
    body = abs(c['Close'] - c['Open'])
    if body > 0:
        if d == 'BUY':
            uw = c['High'] - max(c['Open'], c['Close'])
            if uw > body * 1.5: n += 1
        else:
            lw = min(c['Open'], c['Close']) - c['Low']
            if lw > body * 1.5: n += 1
    try:
        if df1m['Volume'].sum() > 0:
            vma = df1m['Volume'].rolling(20).mean().iloc[-1]
            if vma > 0 and c['Volume'] < vma * 0.6: n += 1
    except: pass
    if d == 'BUY' and c['Close'] < c['Open']: n += 1
    elif d == 'SELL' and c['Close'] > c['Open']: n += 1
    try:
        if len(df1m) >= 6:
            rsi_now = df1m['Close'].iloc[-3] - df1m['Close'].iloc[-4]
            rsi_prev = df1m['Close'].iloc[-5] - df1m['Close'].iloc[-6]
            if d == 'BUY' and rsi_now < 0 and rsi_prev > 0: n += 1
            elif d == 'SELL' and rsi_now > 0 and rsi_prev < 0: n += 1
    except: pass
    return n

# ============================================================
# SCAN SIGNALS
# ============================================================
def scan_signals(df, state, now_utc, dxy_bias, h4_bias):
    out = []
    for idx in range(max(5, len(df)-4), len(df)-1):
        c = df.iloc[idx]; t = df.index[idx]
        try:
            ta = t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)
        except: continue
        if (now_utc - ta).total_seconds()/60 > MAX_CANDLE_AGE_MIN: continue
        cid = t.isoformat()
        if cid in state.get('processed_candles', []): continue
        if not in_session(ta): continue
        d_key = ist_date(ta)
        if state['daily_count'].get(d_key, 0) >= MAX_DAILY: continue
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
            if h4_bias in ['UP', 'MIXED', None]:
                sig = {'dir':'BUY','entry':e,'sl':e-SL_PIPS*PIP,
                       'tp1':e+TP1_PIPS*PIP,'tp2':e+TP2_PIPS*PIP,
                       'time':ta,'session':get_session_name(ta)}
        elif dn and (sw_s or pb_s) and 25 < c['RSI'] < 60:
            if h4_bias in ['DOWN', 'MIXED', None]:
                sig = {'dir':'SELL','entry':e,'sl':e+SL_PIPS*PIP,
                       'tp1':e-TP1_PIPS*PIP,'tp2':e-TP2_PIPS*PIP,
                       'time':ta,'session':get_session_name(ta)}
        if sig:
            g, sc = grade(c, up, dn, sw_b, sw_s, pb_b, pb_s, dxy_bias, h4_bias, sig['dir'])
            rt = should_retest(c)
            # FIX 1: Retest level
            if rt:
                if sig['dir'] == 'BUY':
                    retest_lvl = sig['entry'] + RETEST_OFFSET_PIP * PIP
                else:
                    retest_lvl = sig['entry'] - RETEST_OFFSET_PIP * PIP
            else:
                retest_lvl = None
            sig.update({'grade':g,'score':sc,'risk_pct':risk_for_grade(g),
                        'should_retest':rt,
                        'retest_level':retest_lvl,
                        'tp1_hit':False,'tp2_hit':False,
                        'sl_original':sig['sl'],
                        'id':f"{sig['dir']}_{cid}",'candle_id':cid,
                        'created':now_utc.isoformat()})
            if g != 'C': out.append(sig)
    return out

# ============================================================
# HOLD CHECK
# ============================================================
def hold_check(df1h, active):
    if df1h is None or len(df1h) < 5: return 'CLOSE'
    c = df1h.iloc[-2]; s = 0; d = active['dir']
    if d == 'BUY' and c['Close'] > c['Open']: s += 1
    elif d == 'SELL' and c['Close'] < c['Open']: s += 1
    try:
        if c['Vol_MA'] > 0 and c['Volume'] > c['Vol_MA']: s += 1
    except: pass
    if d == 'BUY' and c['Close'] > c['EMA_50']: s += 1
    elif d == 'SELL' and c['Close'] < c['EMA_50']: s += 1
    if d == 'BUY' and 45 < c['RSI'] < 75: s += 1
    elif d == 'SELL' and 25 < c['RSI'] < 55: s += 1
    if d == 'BUY' and c['EMA_50'] > c['EMA_200']: s += 1
    elif d == 'SELL' and c['EMA_50'] < c['EMA_200']: s += 1
    if s >= 5: return 'HOLD'
    if s >= 3: return 'TRAIL'
    return 'CLOSE'

# ============================================================
# REVERSAL CHECK
# ============================================================
def check_reversal(df1m, state):
    a = state.get('active_signal')
    if not a or df1m is None: return None
    try:
        at = datetime.fromisoformat(a['created']) if isinstance(a['created'], str) else a['created']
        if at.tzinfo is None: at = at.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC) - at.astimezone(UTC)).total_seconds()/3600
        if age_h > MAX_ACTIVE_AGE_H:
            return {'type':'EXPIRED','price':float(df1m.iloc[-1]['Close']),'pips':0}
        if age_h > MAX_TRADE_HOURS and not a.get('tp1_hit'):
            return {'type':'TIME_EXIT','price':float(df1m.iloc[-1]['Close']),'pips':0}
    except: pass
    if len(df1m) < 3: return None
    c = df1m.iloc[-2]
    price, high, low = float(c['Close']), float(c['High']), float(c['Low'])
    d, sl, tp1, tp2 = a['dir'], float(a['sl']), float(a['tp1']), float(a['tp2'])
    if d == 'BUY':
        if not a['tp1_hit']:
            if low <= sl: return {'type':'SL_HIT','price':price,'pips':-SL_PIPS}
            if high >= tp1: return {'type':'TP1_HIT','price':price,'pips':TP1_PIPS}
        elif not a['tp2_hit']:
            if low <= sl: return {'type':'BE_HIT','price':price,'pips':0}
            if high >= tp2: return {'type':'TP2_HIT','price':price,'pips':TP1_PIPS+TP2_PIPS}
        n = detect_reversal(df1m, a)
        if n >= 6: return {'type':'REVERSAL','price':price,'pips':0,'signals':n}
    else:
        if not a['tp1_hit']:
            if high >= sl: return {'type':'SL_HIT','price':price,'pips':-SL_PIPS}
            if low <= tp1: return {'type':'TP1_HIT','price':price,'pips':TP1_PIPS}
        elif not a['tp2_hit']:
            if high >= sl: return {'type':'BE_HIT','price':price,'pips':0}
            if low <= tp2: return {'type':'TP2_HIT','price':price,'pips':TP1_PIPS+TP2_PIPS}
        n = detect_reversal(df1m, a)
        if n >= 6: return {'type':'REVERSAL','price':price,'pips':0,'signals':n}
    return None

# ============================================================
# FIX 1: PENDING RETEST MONITORING
# ============================================================
def check_pending_order(df1m, state, now_utc):
    po = state.get('pending_order')
    if not po or df1m is None: return None
    try:
        exp = datetime.fromisoformat(po['expiry'])
        if exp.tzinfo is None: exp = exp.replace(tzinfo=UTC)
        if now_utc > exp.astimezone(UTC):
            return {'action':'EXPIRED','order':po}
    except: pass
    if len(df1m) < 2: return None
    c = df1m.iloc[-2]
    low = float(c['Low']); high = float(c['High'])
    lvl = float(po['level'])
    if po['dir'] == 'BUY' and low <= lvl:
        return {'action':'FILLED','order':po,'fill':lvl}
    if po['dir'] == 'SELL' and high >= lvl:
        return {'action':'FILLED','order':po,'fill':lvl}
    return None

# ============================================================
# HELPERS
# ============================================================
def calc_lot(rp=1.0):
    return max(round((ACCOUNT * rp / 100) / (SL_PIPS * PIP_VALUE), 2), 0.01)

def check_limits(state, now):
    d = ist_date(now); w = ist_week(now); m = ist_month(now)
    if state['daily_pnl'].get(d, 0) <= -DAILY_LOSS_PCT: return False
    if state['weekly_pnl'].get(w, 0) <= -WEEKLY_LOSS_PCT: return False
    if state['monthly_pnl'].get(m, 0) <= -MONTHLY_LOSS_PCT: return False
    if state.get('consecutive_losses', 0) >= 2: return False
    return True

def update_pnl(state, now, pips):
    d = ist_date(now); w = ist_week(now); m = ist_month(now)
    pnl = (pips * PIP_VALUE * 0.07) / ACCOUNT * 100
    state['daily_pnl'][d] = state['daily_pnl'].get(d, 0) + pnl
    state['weekly_pnl'][w] = state['weekly_pnl'].get(w, 0) + pnl
    state['monthly_pnl'][m] = state['monthly_pnl'].get(m, 0) + pnl
    if pips < 0: state['consecutive_losses'] = state.get('consecutive_losses', 0) + 1
    elif pips > 0: state['consecutive_losses'] = 0

# ============================================================
# MESSAGES
# ============================================================
def msg_signal(s):
    em = "🟢" if s['dir'] == 'BUY' else "🔴"
    lot = calc_lot(s['risk_pct'])
    ist = s['time'].astimezone(IST)
    if s.get('should_retest') and s.get('retest_level'):
        rt = f"Retest limit @ {s['retest_level']:.3f}"
    else:
        rt = "Market now"
    return f"""{em} <b>USDJPY {s['dir']}</b>
━━━━━━━━━━━━━━━━━━
📊 Grade: <b>{s['grade']}</b> ({s['score']}/15)
📍 Entry: {s['entry']:.3f}
🛑 SL: {s['sl']:.3f} ({SL_PIPS} pip)
🎯 TP1: {s['tp1']:.3f} ({TP1_PIPS} pip)
🎯 TP2: {s['tp2']:.3f} ({TP2_PIPS} pip)
💰 Lot: {lot} ({s['risk_pct']}%)
⏰ {ist.strftime('%H:%M IST')}
📊 {s['session']}
━━━━━━━━━━━━━━━━━━
💡 <b>{rt}</b>
"""

def msg_price(df1m, state):
    c = df1m.iloc[-1]; p = float(c['Close'])
    ist = datetime.now(IST).strftime('%H:%M IST')
    a = state.get('active_signal')
    base = f"📊 <b>USDJPY</b>\n⏰ {ist}\n💵 <b>{p:.3f}</b>\n"
    if a:
        e = float(a['entry'])
        pips = (p-e)/PIP if a['dir']=='BUY' else (e-p)/PIP
        em = "🟢" if a['dir']=='BUY' else "🔴"
        base += f"\n{em} {a['dir']} ({a['grade']})\n📍 {e:.3f}\n🎯 {float(a['tp1']):.3f} / {float(a['tp2']):.3f}\n🛑 {float(a['sl']):.3f}\n📊 <b>{pips:+.1f} pip</b>"
    return base

def msg_rev(r, a):
    e = float(a['entry'])
    t = r['type']
    if t == 'SL_HIT': return f"🚨 <b>SL HIT</b>\n{a['dir']} @ {e:.3f}\n📉 {r['pips']} pip"
    if t == 'TP1_HIT': return f"🎯 <b>TP1 HIT</b>\n{a['dir']} @ {e:.3f}\n📈 +{r['pips']} pip\n✅ 50% book, SL BE"
    if t == 'TP2_HIT': return f"🎯 <b>TP2 HIT</b>\n{a['dir']} @ {e:.3f}\n📈 +{r['pips']} pip\n💰 Full close"
    if t == 'BE_HIT': return f"⚪ <b>BE HIT</b>\n{a['dir']} closed at BE"
    if t == 'REVERSAL': return f"⚡ <b>REVERSAL</b>\n{a['dir']} — {r.get('signals',0)} signals\n💡 Closing"
    if t == 'TIME_EXIT': return f"⏰ <b>TIME EXIT</b>\n{a['dir']} — 8h crossed"
    return f"⏰ {t}"

def msg_retest_filled(po, fill):
    em = "🟢" if po['dir'] == 'BUY' else "🔴"
    return f"""{em} <b>RETEST FILLED</b>
📌 {po['dir']} @ {fill:.3f}
💡 Now active

📍 Entry: {po['signal_data']['entry']:.3f}
🛑 SL: {po['signal_data']['sl']:.3f}
🎯 TP1: {po['signal_data']['tp1']:.3f}
🎯 TP2: {po['signal_data']['tp2']:.3f}
"""

def msg_retest_expired(po):
    return f"""⏰ <b>RETEST EXPIRED</b>
📌 {po['dir']} @ {po['level']:.3f}
💡 5 min me nahi aaya
📊 Market order allowed
"""

# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("   USDJPY BOT V9")
    print("=" * 60)

    if not BOT_TOKEN or not CHAT_ID:
        print("❌ Secrets missing")
        return

    state = load_state()
    now_utc = datetime.now(UTC)

    # FIX 2: Concurrent lock (240 sec max)
    RUN_ID = str(uuid.uuid4())[:8]
    if state.get('running_run') and state.get('running_start'):
        try:
            st = datetime.fromisoformat(state['running_start'])
            if st.tzinfo is None: st = st.replace(tzinfo=UTC)
            age = (now_utc - st.astimezone(UTC)).total_seconds()
            if age < RUN_LOCK_MAX_SEC and state['running_run'] != RUN_ID:
                print(f"⚠️ Another run active ({int(age)}s). Exit.")
                return
            # If stale, force clear
            print(f"⚠️ Stale lock ({int(age)}s). Force clearing.")
        except: pass

    state['running_run'] = RUN_ID
    state['running_start'] = now_utc.isoformat()
    save_state(state)

    try:
        if is_weekend():
            print("Weekend — exit")
            return

        # Startup once per day
        today = ist_date(now_utc)
        if state.get('startup_date') != today:
            send_tg(f"🤖 <b>USDJPY Bot V9</b>\n{now_utc.astimezone(IST).strftime('%d-%b %H:%M IST')}")
            state['startup_date'] = today
            save_state(state)

        # Data
        df1h = get_1h()
        df4h = get_4h()
        df1m = get_1m()
        dxy_df = get_dxy()

        if df1h is not None: df1h = add_ind(df1h)

        dxy_bias = get_dxy_bias(dxy_df)
        h4_bias = get_4h_bias(df4h)

        print(f"1H:{len(df1h) if df1h is not None else 0} 4H:{len(df4h) if df4h is not None else 0} 1M:{len(df1m) if df1m is not None else 0}")
        print(f"DXY:{dxy_bias} 4H:{h4_bias}")

        # FIX 1: Check pending retest order
        if df1m is not None and state.get('pending_order'):
            res = check_pending_order(df1m, state, now_utc)
            if res:
                if res['action'] == 'FILLED':
                    po = res['order']
                    # Activate signal
                    state['active_signal'] = po['signal_data']
                    state['active_signal']['created'] = now_utc.isoformat()
                    send_tg(msg_retest_filled(po, res['fill']))
                    print(f"✅ Retest FILLED @ {res['fill']:.3f}")
                    state['pending_order'] = None
                    save_state(state)
                elif res['action'] == 'EXPIRED':
                    send_tg(msg_retest_expired(res['order']))
                    print("⏰ Retest expired")
                    state['pending_order'] = None
                    save_state(state)

        # REVERSAL CHECK
        if df1m is not None and state.get('active_signal'):
            r = check_reversal(df1m, state)
            if r:
                a = state['active_signal']
                send_tg(msg_rev(r, a))
                print(f"{r['type']}")
                if r['type'] == 'TP1_HIT':
                    a['tp1_hit'] = True
                    a['sl'] = a['entry']
                    state['active_signal'] = a
                else:
                    update_pnl(state, now_utc, r.get('pips', 0))
                    state['active_signal'] = None
                    state['stats']['total'] = state['stats'].get('total', 0) + 1
                    if r.get('pips', 0) > 0: state['stats']['wins'] += 1
                    elif r.get('pips', 0) < 0: state['stats']['losses'] += 1
                    state['stats']['pips'] = state['stats'].get('pips', 0) + r.get('pips', 0)
                save_state(state)

        # NEW SIGNAL
        if df1h is not None and not state.get('active_signal') and not state.get('pending_order'):
            if check_limits(state, now_utc):
                last_any = None
                for k in ['last_buy_time','last_sell_time']:
                    v = state.get(k)
                    if v:
                        try:
                            t = datetime.fromisoformat(v)
                            if t.tzinfo is None: t = t.replace(tzinfo=UTC)
                            if last_any is None or t > last_any: last_any = t
                        except: pass
                cooldown_ok = True
                if last_any:
                    if (now_utc - last_any.astimezone(UTC)).total_seconds()/3600 < COOLDOWN_ANY_H:
                        cooldown_ok = False
                if cooldown_ok:
                    sigs = scan_signals(df1h, state, now_utc, dxy_bias, h4_bias)
                    valid = []
                    for s in sigs:
                        last_dir = state.get('last_buy_time') if s['dir']=='BUY' else state.get('last_sell_time')
                        if last_dir:
                            try:
                                t = datetime.fromisoformat(last_dir)
                                if t.tzinfo is None: t = t.replace(tzinfo=UTC)
                                if (now_utc - t.astimezone(UTC)).total_seconds()/3600 < COOLDOWN_SAME_DIR_H:
                                    continue
                            except: pass
                        valid.append(s)
                    if valid:
                        s = valid[-1]
                        if send_tg(msg_signal(s)):
                            print(f"✅ {s['dir']} {s['grade']} @ {s['entry']:.3f}")

                            # FIX 1: If retest, create pending order
                            if s.get('should_retest') and s.get('retest_level'):
                                s['signal_data'] = dict(s)  # Store full signal
                                expiry = now_utc + timedelta(minutes=RETEST_WAIT_MIN)
                                state['pending_order'] = {
                                    'dir': s['dir'],
                                    'level': s['retest_level'],
                                    'created': now_utc.isoformat(),
                                    'expiry': expiry.isoformat(),
                                    'signal_data': s
                                }
                                print(f"⏳ Pending retest @ {s['retest_level']:.3f}")
                            else:
                                state['active_signal'] = s

                            pc = state.get('processed_candles', [])
                            for x in valid: pc.append(x['candle_id'])
                            state['processed_candles'] = pc[-50:]
                            if s['dir'] == 'BUY': state['last_buy_time'] = s['time'].isoformat()
                            else: state['last_sell_time'] = s['time'].isoformat()
                            d = ist_date(s['time'])
                            state['daily_count'][d] = state['daily_count'].get(d, 0) + 1
                            save_state(state)

        # PRICE UPDATE (30 min)
        if df1m is not None and state.get('active_signal'):
            last_pu = state.get('last_price_update')
            do = True
            if last_pu:
                try:
                    t = datetime.fromisoformat(last_pu)
                    if t.tzinfo is None: t = t.replace(tzinfo=UTC)
                    if (now_utc - t.astimezone(UTC)).total_seconds()/60 < 30: do = False
                except: pass
            if do:
                send_tg(msg_price(df1m, state))
                print("💰 Price update")
                state['last_price_update'] = now_utc.isoformat()

    finally:
        state['running_run'] = None
        save_state(state)
        print("✅ Done")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            send_tg(f"❌ Bot error: {esc(str(e)[:200])}")
        except: pass
