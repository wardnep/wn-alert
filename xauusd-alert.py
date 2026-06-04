import time
import json
import os
import requests
import pandas as pd
import yfinance as yf

# =====================
# CONFIG
# =====================

SYM = "GC=F"  # Gold Futures

TELEGRAM_TOKEN = "8954966906:AAFRvWdzB2zQ5qZ3M3SGljsXUFHdUuDnkbI"
TELEGRAM_CHAT_ID = "8911413063"

STATE_FILE = "state.json"

# =====================
# TELEGRAM
# =====================

def send_telegram(msg):
    requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        params={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        },
        timeout=10
    )

# =====================
# STATE
# =====================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_signal": None}

    with open(STATE_FILE, "r") as f:
        return json.load(f)

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

# =====================
# HEIKIN ASHI
# =====================

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
                (ha_open[i - 1] + ha["close"].iloc[i - 1]) / 2
            )

    ha["open"] = ha_open

    return ha

# =====================
# CHECK SIGNAL
# =====================

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

    cross_up = (
        prev["ema9"] <= prev["ema200"]
        and
        curr["ema9"] > curr["ema200"]
    )

    cross_down = (
        prev["ema9"] >= prev["ema200"]
        and
        curr["ema9"] < curr["ema200"]
    )

    state = load_state()

    if cross_up and state["last_signal"] != "bullish":

        msg = (
            "🟢 XAUUSD M15\n"
            "Heikin Ashi EMA9 crossed ABOVE EMA200"
        )

        send_telegram(msg)

        state["last_signal"] = "bullish"
        save_state(state)

    elif cross_down and state["last_signal"] != "bearish":

        msg = (
            "🔴 XAUUSD M15\n"
            "Heikin Ashi EMA9 crossed BELOW EMA200"
        )

        send_telegram(msg)

        state["last_signal"] = "bearish"
        save_state(state)

# =====================
# MAIN LOOP
# =====================

if __name__ == "__main__":

    send_telegram("🚀 Test Alert")
    print("Started...")

    while True:

        try:
            check_signal()

        except Exception as e:
            print(e)

        time.sleep(60)
