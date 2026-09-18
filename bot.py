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
