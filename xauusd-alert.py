# ==============================================================================
# XAU/USD M15 Alert Bot
# ==============================================================================
# โปรแกรมนี้ทำงานเป็น loop ทุก 60 วินาที โดยจะ:
#   1. ดึงข้อมูลราคา XAUUSD (ทองคำ) จาก TradingView ทุกรอบ
#   2. คำนวณ Heikin Ashi candle และ EMA บน HA close
#   3. ตรวจสอบ signal 2 ประเภท:
#      - EMA9 ตัด EMA200 (บอก trend เปลี่ยน)
#      - HA Close ตัด EMA9 (บอก entry/exit)
#   4. ส่ง alert ไป Telegram เมื่อเกิด signal
#   5. ส่ง heartbeat ทุก 08:00, 14:00, 19:00 เพื่อยืนยันว่า bot ยังทำงานอยู่
# ==============================================================================

import time          # ใช้สำหรับ sleep() หยุดรอระหว่าง loop
import json          # ใช้อ่าน/เขียน state.json
import os            # ใช้อ่าน environment variable และเช็คไฟล์
import requests      # ใช้เรียก Telegram API (HTTP POST)
import pandas as pd  # ใช้จัดการ DataFrame ของข้อมูลราคา

from datetime import datetime       # ใช้แสดงเวลาใน log และเช็คเวลา heartbeat
from zoneinfo import ZoneInfo       # ใช้แปลงเวลาเป็น timezone Asia/Bangkok

from dotenv import load_dotenv      # ใช้โหลด .env file เข้า environment

from tvDatafeed import TvDatafeed, Interval  # library ดึงข้อมูลจาก TradingView


# ====================================
# CONFIG
# ====================================

# โหลด .env file เพื่อดึงค่า secret ต่างๆ
load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
TV_USERNAME      = os.getenv("TV_USERNAME")
TV_PASSWORD      = os.getenv("TV_PASSWORD")

STATE_FILE = "state.json"

SYMBOL   = "XAUUSD"
EXCHANGE = "OANDA"

# [แก้ไข #3] ย้าย price_levels ออกมาไว้ใน CONFIG ให้แก้ง่าย
PRICE_LEVELS = [4300, 4310]

# ====================================
# TRADINGVIEW CONNECTION
# ====================================

# [แก้ไข #4] แยก connect logic เป็นฟังก์ชัน เพื่อให้ reconnect ได้
def create_tv_connection():
    """สร้าง TvDatafeed object ใหม่ ใช้ทั้งตอน startup และตอน reconnect"""
    if TV_USERNAME and TV_PASSWORD:
        return TvDatafeed(TV_USERNAME, TV_PASSWORD)
    return TvDatafeed()

tv = create_tv_connection()

# นับจำนวนครั้งที่ fetch ล้มเหลวติดต่อกัน ถ้าเกิน threshold จะ reconnect
_consecutive_failures = 0
RECONNECT_THRESHOLD = 5  # reconnect หลังจาก fail ติดต่อกัน 5 ครั้ง


# ====================================
# TELEGRAM
# ====================================

def send_telegram(message):
    """
    ส่ง text message ไปยัง Telegram chat ที่กำหนดไว้

    Args:
        message (str): ข้อความที่จะส่ง รองรับ emoji และ newline (\n)
    """

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            data={"chat_id": TELEGRAM_CHAT_ID, "text": message},
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
# STATE MANAGEMENT
# ====================================

def load_state():
    """โหลด state จากไฟล์ JSON"""

    default = {
        "ema200_signal":    None,
        "ema9_position":    None,
        "last_alert_candle": None,
        "heartbeat_sent":   [],
        "heartbeat_date":   ""
    }

    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        for key, val in default.items():
            state.setdefault(key, val)

        return state

    except (json.JSONDecodeError, IOError) as e:
        print(f"[{datetime.now()}] ⚠️ State file corrupted, resetting. Error: {e}")
        return default


def save_state(state):
    """
    บันทึก state ลงไฟล์ JSON แบบ atomic write
    เขียนไฟล์ temp ก่อน แล้วค่อย rename เพื่อป้องกันไฟล์เสียหาย
    ถ้า process ถูก kill กลางคัน ไฟล์เดิมจะยังอยู่ครบถ้วน

    [แก้ไข #5] ใช้ atomic write แทนการเขียนตรง
    """

    tmp_file = STATE_FILE + ".tmp"

    try:
        with open(tmp_file, "w") as f:
            json.dump(state, f, indent=2)

        # os.replace() เป็น atomic operation บน POSIX (Linux/macOS)
        # ถ้าเขียน .tmp สำเร็จแล้ว rename จะ guaranteed ว่า state.json ไม่เสียหาย
        os.replace(tmp_file, STATE_FILE)

    except IOError as e:
        print(f"[{datetime.now()}] ⚠️ Failed to save state: {e}")

        # ลบ .tmp ที่อาจค้างไว้
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass


# ====================================
# HEIKIN ASHI
# ====================================

def build_heikin_ashi(df):
    """
    คำนวณ Heikin Ashi จาก DataFrame ราคาปกติ

    Args:
        df (pd.DataFrame): ต้องมี column: open, high, low, close

    Returns:
        ha (pd.DataFrame): HA candle ที่มี column: open, high, low, close
    """

    ha = pd.DataFrame(index=df.index)

    ha["close"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4

    ha_open = []
    for i in range(len(df)):
        if i == 0:
            ha_open.append((df["open"].iloc[0] + df["close"].iloc[0]) / 2)
        else:
            ha_open.append((ha_open[i - 1] + ha["close"].iloc[i - 1]) / 2)

    ha["open"] = ha_open
    ha["high"] = pd.concat([df["high"], ha["open"], ha["close"]], axis=1).max(axis=1)
    ha["low"]  = pd.concat([df["low"],  ha["open"], ha["close"]], axis=1).min(axis=1)

    return ha


# ====================================
# DOWNLOAD DATA
# ====================================

def get_data(interval, n_bars, label):
    """
    ดึงข้อมูลราคา XAUUSD จาก TradingView พร้อม reconnect logic

    [แก้ไข #4] เพิ่ม reconnect เมื่อ fail ติดต่อกันเกิน threshold

    Args:
        interval : TvDatafeed Interval (เช่น Interval.in_15_minute)
        n_bars   : จำนวนแท่งที่ต้องการ
        label    : ชื่อ timeframe สำหรับ log (เช่น "M15", "1H")

    Returns:
        df   : pd.DataFrame ถ้าสำเร็จ
        None : ถ้าล้มเหลว
    """

    global tv, _consecutive_failures

    try:
        df = tv.get_hist(
            symbol=SYMBOL,
            exchange=EXCHANGE,
            interval=interval,
            n_bars=n_bars
        )

        if df is None or df.empty:
            raise ValueError("Empty data returned")

        # fetch สำเร็จ → reset failure counter
        _consecutive_failures = 0
        return df

    except Exception as e:
        _consecutive_failures += 1
        print(f"[{datetime.now()}] ⚠️ [{label}] Failed to fetch data ({_consecutive_failures} consecutive): {e}")

        # reconnect ถ้า fail ติดต่อกันเกิน threshold
        if _consecutive_failures >= RECONNECT_THRESHOLD:
            print(f"[{datetime.now()}] 🔄 Reconnecting to TradingView...")
            try:
                tv = create_tv_connection()
                _consecutive_failures = 0
                print(f"[{datetime.now()}] ✅ Reconnected successfully")
            except Exception as re:
                print(f"[{datetime.now()}] ❌ Reconnect failed: {re}")

        return None


def get_data_15m():
    return get_data(Interval.in_15_minute, 500, "M15")


def get_data_1h():
    return get_data(Interval.in_1_hour, 500, "1H")


# ====================================
# SIGNAL DETECTION
# ====================================

def check_m15_ema_signal(state):
    """
    ตรวจสอบ EMA cross signal บน M15

    [แก้ไข #1] รับ state จากภายนอก ไม่โหลด/save เอง
               เพื่อให้ state ไม่ทับกับ check_h1_price_alert()

    Args:
        state (dict): state ที่โหลดมาจาก main loop (แก้ไข in-place)
    """

    df = get_data_15m()
    if df is None:
        return

    if len(df) < 220:
        print(f"[{datetime.now()}] ⚠️ Not enough bars: {len(df)} (need 220+)")
        return

    ha = build_heikin_ashi(df)
    ha["ema9"]   = ha["close"].ewm(span=9,   adjust=False).mean()
    ha["ema200"] = ha["close"].ewm(span=200,  adjust=False).mean()

    prev = ha.iloc[-3]
    curr = ha.iloc[-2]
    candle_time = str(curr.name)

    trend = "UPTREND" if curr["ema9"] > curr["ema200"] else "DOWNTREND"

    print(
        f"[{datetime.now()}] "
        f"M15 Candle={candle_time} "
        f"Close={curr['close']:.2f} "
        f"EMA9={curr['ema9']:.2f} "
        f"EMA200={curr['ema200']:.2f} "
        f"Trend={trend}"
    )

    # ──────────────────────────────────────
    # SIGNAL 1: EMA9 CROSS EMA200
    # ──────────────────────────────────────

    cross_up_200   = prev["ema9"] <= prev["ema200"] and curr["ema9"] >  curr["ema200"]
    cross_down_200 = prev["ema9"] >= prev["ema200"] and curr["ema9"] <  curr["ema200"]

    if cross_up_200 and state["last_alert_candle"] != candle_time:
        send_telegram(f"📈 XAUUSD M15\n🟢 EMA9 ABOVE EMA200\n⏰ {candle_time}")
        state["ema200_signal"]     = "bullish"
        state["last_alert_candle"] = candle_time

    elif cross_down_200 and state["last_alert_candle"] != candle_time:
        send_telegram(f"📉 XAUUSD M15\n🔴 EMA9 BELOW EMA200\n⏰ {candle_time}")
        state["ema200_signal"]     = "bearish"
        state["last_alert_candle"] = candle_time

    # ──────────────────────────────────────
    # SIGNAL 2: HA CLOSE CROSS EMA9
    # ──────────────────────────────────────

    close_above_ema9 = prev["close"] <= prev["ema9"] and curr["close"] > curr["ema9"]
    close_below_ema9 = prev["close"] >= prev["ema9"] and curr["close"] < curr["ema9"]

    if close_above_ema9 and state["ema9_position"] != "above":
        send_telegram(f"⬆️ XAUUSD M15\nHA Close ABOVE EMA9\n📈 {trend}\n⏰ {candle_time}")
        state["ema9_position"] = "above"

    elif close_below_ema9 and state["ema9_position"] != "below":
        send_telegram(f"⬇️ XAUUSD M15\nHA Close BELOW EMA9\n📉 {trend}\n⏰ {candle_time}")
        state["ema9_position"] = "below"


def check_h1_price_alert(state):
    """
    ตรวจสอบ candle close ที่ตัดผ่าน price level บน 1H

    [แก้ไข #1] รับ state จากภายนอก ไม่โหลด/save เอง
    [แก้ไข #2] ย้าย save_state() ออกนอก for loop (save ครั้งเดียวใน main loop)
    [แก้ไข #3] price_levels อ่านจาก PRICE_LEVELS ใน CONFIG แทน hardcode

    Args:
        state (dict): state ที่โหลดมาจาก main loop (แก้ไข in-place)
    """

    df = get_data_1h()
    if df is None:
        return

    if len(df) < 10:
        print(f"[{datetime.now()}] ⚠️ Not enough bars: {len(df)} (need 10+)")
        return

    prev_candle = df.iloc[-3]
    curr_candle = df.iloc[-2]
    candle_time = str(curr_candle.name)

    print(
        f"[{datetime.now()}] "
        f"H1 Candle={candle_time} "
        f"Close={curr_candle['close']:.2f}"
    )

    # ──────────────────────────────────────
    # SIGNAL 3: CANDLE CLOSE CROSS PRICE LEVEL
    # ──────────────────────────────────────

    for price in PRICE_LEVELS:
        state_key = f"h1_price_{price}"

        if state_key not in state:
            state[state_key] = "unknown"

        price_cross_up   = prev_candle["close"] <= price and curr_candle["close"] > price
        price_cross_down = prev_candle["close"] >= price and curr_candle["close"] < price

        if price_cross_up and state[state_key] != "above":
            send_telegram(
                f"🔔 XAUUSD 1H\n⬆️ CLOSE ABOVE {price}\n"
                f"💰 Close={curr_candle['close']:.2f}\n⏰ {candle_time}"
            )
            state[state_key] = "above"

        elif price_cross_down and state[state_key] != "below":
            send_telegram(
                f"🔔 XAUUSD 1H\n⬇️ CLOSE BELOW {price}\n"
                f"💰 Close={curr_candle['close']:.2f}\n⏰ {candle_time}"
            )
            state[state_key] = "below"


# ====================================
# MAIN LOOP
# ====================================

if __name__ == "__main__":

    send_telegram("🚀 XAU Alert Started")

    while True:

        try:
            now          = datetime.now(ZoneInfo("Asia/Bangkok"))
            current_time = now.strftime("%H:%M")
            current_date = now.strftime("%Y-%m-%d")

            # ────────────────────────────────
            # โหลด state ครั้งเดียวต่อรอบ
            # [แก้ไข #1] ย้ายมาไว้ที่นี่แทนให้แต่ละฟังก์ชันโหลดเอง
            # ────────────────────────────────
            state = load_state()

            # ────────────────────────────────
            # HEARTBEAT — รีเซ็ตรายวัน
            # ────────────────────────────────
            if current_date != state.get("heartbeat_date", ""):
                state["heartbeat_sent"] = []
                state["heartbeat_date"] = current_date

            # ────────────────────────────────
            # HEARTBEAT — ส่งตามเวลาที่กำหนด
            # ────────────────────────────────
            HEARTBEAT_TIMES = ["08:00", "14:00", "19:00"]

            if current_time in HEARTBEAT_TIMES and current_time not in state["heartbeat_sent"]:
                send_telegram(f"💓 XAU Alert Alive\nTime: {current_time}")
                state["heartbeat_sent"].append(current_time)

            # ────────────────────────────────
            # ตรวจสอบ signal — ใช้ state ตัวเดียวกัน
            # [แก้ไข #1] ทั้งสองฟังก์ชันแก้ไข state in-place
            #            save ครั้งเดียวหลังจากทั้งคู่เสร็จ
            # ────────────────────────────────
            check_m15_ema_signal(state)
            check_h1_price_alert(state)

            # save ครั้งเดียวหลังจากทุก signal ถูกตรวจสอบแล้ว
            save_state(state)

        except Exception as e:
            print(f"[{datetime.now()}] ❌ Main loop error: {e}")

        time.sleep(60)
