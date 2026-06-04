import pandas as pd
import requests
from datetime import datetime

ACCOUNT_ID = "YOUR_ACCOUNT"
API_KEY = "YOUR_API_KEY"

headers = {
    "Authorization": f"Bearer {API_KEY}"
}

url = "https://api-fxtrade.oanda.com/v3/instruments/XAU_USD/candles"

params = {
    "granularity": "M15",
    "count": 300,
    "price": "M"
}

r = requests.get(url, headers=headers, params=params)
data = r.json()

candles = []

for c in data["candles"]:
    if c["complete"]:
        candles.append({
            "time": c["time"],
            "open": float(c["mid"]["o"]),
            "high": float(c["mid"]["h"]),
            "low": float(c["mid"]["l"]),
            "close": float(c["mid"]["c"]),
        })

df = pd.DataFrame(candles)

# -------------------------
# Heikin Ashi
# -------------------------

ha_close = (
    df["open"] +
    df["high"] +
    df["low"] +
    df["close"]
) / 4

ha_open = [df["open"].iloc[0]]

for i in range(1, len(df)):
    ha_open.append(
        (ha_open[i-1] + ha_close.iloc[i-1]) / 2
    )

df["ha_open"] = ha_open
df["ha_close"] = ha_close

# -------------------------
# EMA
# -------------------------

df["ema9"] = df["ha_close"].ewm(span=9).mean()
df["ema200"] = df["ha_close"].ewm(span=200).mean()

# -------------------------
# Cross Up Check
# -------------------------

prev = df.iloc[-2]
curr = df.iloc[-1]

cross_up = (
    prev["ema9"] <= prev["ema200"] and
    curr["ema9"] > curr["ema200"]
)

if cross_up:
    print(
        f"[ALERT] XAUUSD HA M15 EMA9 crossed ABOVE EMA200 at {curr['time']}"
    )

    # ส่ง Telegram
    TOKEN = "YOUR_BOT_TOKEN"
    CHAT_ID = "YOUR_CHAT_ID"

    msg = (
        f"🚀 XAUUSD M15\n"
        f"EMA9 crossed above EMA200\n"
        f"Time: {curr['time']}"
    )

    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={
            "chat_id": CHAT_ID,
            "text": msg
        }
    )
