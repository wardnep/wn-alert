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

# ถ้ามี TradingView account
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

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    requests.post(
        url,
        data={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        },
        timeout=10
    )

# ====================================
# STATE
# ====================================

def load_state():

    if not os.path.exists(STATE_FILE):

        return {
            "ema200_signal": None,
            "ema9_position": None,
            "last_alert_candle": None
        }

    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):

    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# ====================================
# HEIKIN ASHI
# ====================================

def build_heikin_ashi(df):

    ha = pd.DataFrame(index=df.index)

    ha["close"] = (
        df["open"]
        + df["high"]
        + df["low"]
        + df["close"]
    ) / 4

    ha_open = []

    for i in range(len(df)):

        if i == 0:

            ha_open.append(
                (df["open"].iloc[0] + df["close"].iloc[0]) / 2
            )

        else:

            ha_open.append(
                (
                    ha_open[i - 1]
                    + ha["close"].iloc[i - 1]
                ) / 2
            )

    ha["open"] = ha_open

    return ha

# ====================================
# DOWNLOAD DATA
# ====================================

def get_data():

    df = tv.get_hist(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        interval=Interval.in_15_minute,
        n_bars=500
    )

    return df

# ====================================
# SIGNAL
# ====================================

def check_signal():

    df = get_data()

    if df is None:
        return

    if len(df) < 220:
        return

    ha = build_heikin_ashi(df)

    ha["ema9"] = ha["close"].ewm(
        span=9,
        adjust=False
    ).mean()

    ha["ema200"] = ha["close"].ewm(
        span=200,
        adjust=False
    ).mean()

    # ใช้แท่งปิดล่าสุดจริง
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
        f"EMA200={curr['ema200']:.2f}"
    )

    # ==========================
    # EMA9 CROSS EMA200
    # ==========================

    cross_up_200 = (
        prev["ema9"] <= prev["ema200"]
        and
        curr["ema9"] > curr["ema200"]
    )

    cross_down_200 = (
        prev["ema9"] >= prev["ema200"]
        and
        curr["ema9"] < curr["ema200"]
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
        and
        curr["close"] > curr["ema9"]
    )

    close_below_ema9 = (
        prev["close"] >= prev["ema9"]
        and
        curr["close"] < curr["ema9"]
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

    heartbeat_sent = set()

    while True:

        try:

            now = datetime.now(
                ZoneInfo("Asia/Bangkok")
            )

            current_time = now.strftime("%H:%M")

            if current_time in [
                "08:00",
                "14:00",
                "19:00"
            ]:

                if current_time not in heartbeat_sent:

                    send_telegram(
                        f"💓 XAU Alert Alive\n"
                        f"Time: {current_time}"
                    )

                    heartbeat_sent.add(current_time)

            if current_time == "00:00":
                heartbeat_sent.clear()

            check_signal()

        except Exception as e:

            print(e)

        time.sleep(60)
