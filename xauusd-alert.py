from datetime import datetime
import time
import json
import os
import requests
import pandas as pd
import yfinance as yf
from datetime import datetime

# ====================================
# CONFIG
# ====================================

SYM = "GC=F"  # Gold Futures

TELEGRAM_TOKEN = "8954966906:AAFRvWdzB2zQ5qZ3M3SGljsXUFHdUuDnkbI"
TELEGRAM_CHAT_ID = "8911413063"

STATE_FILE = "state.json"

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
            "ema9_position": None
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
        df["Open"] +
        df["High"] +
        df["Low"] +
        df["Close"]
    ) / 4

    ha_open = []

    for i in range(len(df)):
        if i == 0:
            ha_open.append(
                (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2
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
# CHECK SIGNAL
# ====================================

def check_signal():

    df = yf.download(
        SYM,
        interval="15m",
        period="10d",
        auto_adjust=False,
        progress=False
    )

    if len(df) < 220:
        return

    ha = build_heikin_ashi(df)

    ha["ema9"] = ha["close"].ewm(span=9).mean()
    ha["ema200"] = ha["close"].ewm(span=200).mean()

    prev = ha.iloc[-2]
    curr = ha.iloc[-1]

    state = load_state()

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

    if cross_up_200 and state["ema200_signal"] != "bullish":

        send_telegram(
            "🟢 XAUUSD M15\n"
            "EMA9 crossed ABOVE EMA200"
        )

        state["ema200_signal"] = "bullish"

    elif cross_down_200 and state["ema200_signal"] != "bearish":

        send_telegram(
            "🔴 XAUUSD M15\n"
            "EMA9 crossed BELOW EMA200"
        )

        state["ema200_signal"] = "bearish"

    # ==========================
    # HA CLOSE CROSS EMA9
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

    trend = (
        "UPTREND"
        if curr["ema9"] > curr["ema200"]
        else "DOWNTREND"
    )

    if close_above_ema9 and state["ema9_position"] != "above":

        send_telegram(
            f"⬆️ XAUUSD M15\n"
            f"HA Close crossed ABOVE EMA9\n"
            f"Trend: {trend}"
        )

        state["ema9_position"] = "above"

    elif close_below_ema9 and state["ema9_position"] != "below":

        send_telegram(
            f"⬇️ XAUUSD M15\n"
            f"HA Close crossed BELOW EMA9\n"
            f"Trend: {trend}"
        )

        state["ema9_position"] = "below"

    save_state(state)

# ====================================
# MAIN
# ====================================

HEARTBEAT_INTERVAL = 86400

if __name__ == "__main__":

    send_telegram("🚀 XAU Alert Started")

    print("Started...")

    last_heartbeat = 0

    while True:

        try:

            now = time.time()

            # ส่ง heartbeat ทุก 1 ชั่วโมง
            if now - last_heartbeat > HEARTBEAT_INTERVAL:

                send_telegram(
                    f"💓 XAU Alert Alive\n"
                    f"{datetime.now():%Y-%m-%d %H:%M:%S}"
                )

                last_heartbeat = now

            check_signal()

        except Exception as e:

            print(e)

        time.sleep(60)
