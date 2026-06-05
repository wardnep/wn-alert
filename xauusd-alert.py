import time
import json
import os
import requests
import pandas as pd

from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from tvDatafeed import TvDatafeed, Interval

# ====================================
# CONFIG
# ====================================

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "state.json"

SYMBOL = "XAUUSD"
EXCHANGE = "OANDA"

TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")

# ====================================
# TRADINGVIEW
# ====================================

if TV_USERNAME and TV_PASSWORD:
    tv = TvDatafeed(TV_USERNAME, TV_PASSWORD)
else:
    tv = TvDatafeed()

# ====================================
# TELEGRAM
# ====================================

def send_telegram(message):
    """ส่ง message ไป Telegram พร้อม error handling"""

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10
        )

        resp.raise_for_status()

    except requests.exceptions.Timeout:
        print(f"[{datetime.now()}] ⚠️ Telegram timeout")

    except requests.exceptions.HTTPError as e:
        print(f"[{datetime.now()}] ⚠️ Telegram HTTP error: {e}")

    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Telegram error: {e}")

# ====================================
# STATE
# ====================================

def load_state():
    """โหลด state จากไฟล์ ถ้าไฟล์เสียหายหรือไม่มี ให้ return default"""

    default = {
        "ema200_signal": None,
        "ema9_position": None,
        "last_alert_candle": None,
        "heartbeat_sent": []       # ย้ายเข้า state ให้รอดจาก restart
    }

    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        # backfill key ใหม่ที่อาจไม่มีใน state เก่า
        for key, val in default.items():
            state.setdefault(key, val)

        return state

    except (json.JSONDecodeError, IOError) as e:
        print(f"[{datetime.now()}] ⚠️ State file corrupted, resetting. Error: {e}")
        return default

def save_state(state):
    """บันทึก state ลงไฟล์ พร้อม error handling"""

    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

    except IOError as e:
        print(f"[{datetime.now()}] ⚠️ Failed to save state: {e}")

# ====================================
# HEIKIN ASHI
# ====================================

def build_heikin_ashi(df):
    """
    คำนวณ Heikin Ashi OHLC ครบทั้ง 4 ค่า
    - HA Close  = (O + H + L + C) / 4
    - HA Open   = (prev HA Open + prev HA Close) / 2
    - HA High   = max(High, HA Open, HA Close)
    - HA Low    = min(Low,  HA Open, HA Close)
    """

    ha = pd.DataFrame(index=df.index)

    ha["close"] = (
        df["open"] + df["high"] + df["low"] + df["close"]
    ) / 4

    ha_open = []

    for i in range(len(df)):

        if i == 0:
            ha_open.append(
                (df["open"].iloc[0] + df["close"].iloc[0]) / 2
            )
        else:
            ha_open.append(
                (ha_open[i - 1] + ha["close"].iloc[i - 1]) / 2
            )

    ha["open"] = ha_open

    # เพิ่ม high/low ให้ครบ (จำเป็นถ้าขยาย logic ในอนาคต)
    ha["high"] = pd.concat(
        [df["high"], ha["open"], ha["close"]], axis=1
    ).max(axis=1)

    ha["low"] = pd.concat(
        [df["low"], ha["open"], ha["close"]], axis=1
    ).min(axis=1)

    return ha

# ====================================
# DOWNLOAD DATA
# ====================================

def get_data():
    """ดึงข้อมูลจาก TradingView พร้อม validation"""

    try:
        df = tv.get_hist(
            symbol=SYMBOL,
            exchange=EXCHANGE,
            interval=Interval.in_15_minute,
            n_bars=500
        )

        if df is None or df.empty:
            print(f"[{datetime.now()}] ⚠️ No data returned from TradingView")
            return None

        return df

    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Failed to fetch data: {e}")
        return None

# ====================================
# SIGNAL
# ====================================

def check_signal():

    df = get_data()

    if df is None:
        return

    if len(df) < 220:
        print(f"[{datetime.now()}] ⚠️ Not enough bars: {len(df)} (need 220+)")
        return

    ha = build_heikin_ashi(df)

    ha["ema9"] = ha["close"].ewm(span=9, adjust=False).mean()
    ha["ema200"] = ha["close"].ewm(span=200, adjust=False).mean()

    # ใช้ iloc[-2] (แท่งปิดสมบูรณ์ล่าสุด) แทน iloc[-1]
    # เพราะ iloc[-1] คือแท่งที่ยังไม่ปิด ค่าจะเปลี่ยนตลอดเวลา
    prev = ha.iloc[-3]
    curr = ha.iloc[-2]

    candle_time = str(curr.name)

    state = load_state()

    trend = (
        "UPTREND"
        if curr["ema9"] > curr["ema200"]
        else "DOWNTREND"
    )

    print(
        f"[{datetime.now()}] "
        f"Candle={candle_time} "
        f"Close={curr['close']:.2f} "
        f"EMA9={curr['ema9']:.2f} "
        f"EMA200={curr['ema200']:.2f} "
        f"Trend={trend}"
    )

    # ==========================
    # EMA9 CROSS EMA200
    # ==========================

    cross_up_200 = (
        prev["ema9"] <= prev["ema200"]
        and curr["ema9"] > curr["ema200"]
    )

    cross_down_200 = (
        prev["ema9"] >= prev["ema200"]
        and curr["ema9"] < curr["ema200"]
    )

    if cross_up_200:

        if state["last_alert_candle"] != candle_time:

            send_telegram(
                f"📈 XAUUSD M15\n"
                f"🟢 EMA9 ABOVE EMA200\n"
                f"⏰ {candle_time}"
            )

            state["ema200_signal"] = "bullish"
            state["last_alert_candle"] = candle_time

    elif cross_down_200:

        if state["last_alert_candle"] != candle_time:

            send_telegram(
                f"📉 XAUUSD M15\n"
                f"🔴 EMA9 BELOW EMA200\n"
                f"⏰ {candle_time}"
            )

            state["ema200_signal"] = "bearish"
            state["last_alert_candle"] = candle_time

    # ==========================
    # CLOSE CROSS EMA9
    # ==========================

    close_above_ema9 = (
        prev["close"] <= prev["ema9"]
        and curr["close"] > curr["ema9"]
    )

    close_below_ema9 = (
        prev["close"] >= prev["ema9"]
        and curr["close"] < curr["ema9"]
    )

    if close_above_ema9:

        if state["ema9_position"] != "above":

            send_telegram(
                f"⬆️ XAUUSD M15\n"
                f"HA Close ABOVE EMA9\n"
                f"📈 {trend}\n"
                f"⏰ {candle_time}"
            )

            state["ema9_position"] = "above"

    elif close_below_ema9:

        if state["ema9_position"] != "below":

            send_telegram(
                f"⬇️ XAUUSD M15\n"
                f"HA Close BELOW EMA9\n"
                f"📉 {trend}\n"
                f"⏰ {candle_time}"
            )

            state["ema9_position"] = "below"

    save_state(state)

# ====================================
# MAIN
# ====================================

if __name__ == "__main__":

    send_telegram("🚀 XAU Alert Started")

    while True:

        try:

            now = datetime.now(ZoneInfo("Asia/Bangkok"))

            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            state = load_state()

            # รีเซ็ต heartbeat list ทุกวันเที่ยงคืน
            last_heartbeat_date = state.get("heartbeat_date", "")

            if current_date != last_heartbeat_date:
                state["heartbeat_sent"] = []
                state["heartbeat_date"] = current_date
                save_state(state)

            # ส่ง heartbeat ตามเวลาที่กำหนด
            HEARTBEAT_TIMES = ["08:00", "14:00", "19:00"]

            if current_time in HEARTBEAT_TIMES:

                if current_time not in state["heartbeat_sent"]:

                    send_telegram(
                        f"💓 XAU Alert Alive\n"
                        f"Time: {current_time}"
                    )

                    state["heartbeat_sent"].append(current_time)
                    save_state(state)

            check_signal()

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Main loop error: {e}")

        time.sleep(60)
