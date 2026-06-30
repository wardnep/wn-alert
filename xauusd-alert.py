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
import math

from datetime import datetime       # ใช้แสดงเวลาใน log และเช็คเวลา heartbeat
from zoneinfo import ZoneInfo       # ใช้แปลงเวลาเป็น timezone Asia/Bangkok

from dotenv import load_dotenv      # ใช้โหลด .env file เข้า environment

from tvDatafeed import TvDatafeed, Interval  # library ดึงข้อมูลจาก TradingView


# ====================================
# CONFIG
# ====================================

# โหลด .env file เพื่อดึงค่า secret ต่างๆ
# .env file ควรมีรูปแบบแบบนี้:
#   TELEGRAM_TOKEN=xxxx
#   TELEGRAM_CHAT_ID=xxxx
#   TV_USERNAME=xxxx   (optional)
#   TV_PASSWORD=xxxx   (optional)
load_dotenv()

# Token สำหรับ Telegram Bot API (ได้จาก @BotFather)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

# Chat ID ปลายทางที่จะส่ง alert ไป (ได้จาก @userinfobot)
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ชื่อไฟล์ที่เก็บ state เพื่อให้จำได้แม้ restart
# เก็บเป็น JSON เพื่ออ่านง่ายและแก้ไขมือได้
STATE_FILE = "state.json"

# Symbol และ Exchange ที่ต้องการดึงข้อมูล
SYMBOL = "XAUUSD"    # ทองคำ vs ดอลลาร์สหรัฐ
EXCHANGE = "OANDA"   # broker ที่ใช้ดึงราคา (มีผลต่อ spread/ราคา)

# Credential ของ TradingView (optional)
# ถ้าไม่มี จะใช้ guest mode ซึ่งอาจถูก rate limit ได้ง่ายกว่า
TV_USERNAME = os.getenv("TV_USERNAME")
TV_PASSWORD = os.getenv("TV_PASSWORD")


# ====================================
# TRADINGVIEW CONNECTION
# ====================================

# เชื่อมต่อ TradingView ตอน startup ครั้งเดียว แล้วใช้ตลอด
# ถ้ามี credential จะ login เพื่อได้ข้อมูลที่ stable กว่า guest mode
if TV_USERNAME and TV_PASSWORD:
    tv = TvDatafeed(TV_USERNAME, TV_PASSWORD)
else:
    # guest mode: ใช้ได้แต่อาจถูก TradingView block ถ้า request บ่อยเกินไป
    tv = TvDatafeed()


# ====================================
# TELEGRAM
# ====================================

def send_telegram(message):
    """
    ส่ง text message ไปยัง Telegram chat ที่กำหนดไว้

    การทำงาน:
    - เรียก Telegram Bot API ด้วย HTTP POST
    - timeout=10 วินาที ป้องกันการค้างถาวรถ้า network มีปัญหา
    - แยก exception เป็น 3 ระดับ เพื่อ log ที่ชัดเจนขึ้น

    Args:
        message (str): ข้อความที่จะส่ง รองรับ emoji และ newline (\n)
    """

    try:
        # รูปแบบ URL ของ Telegram Bot API
        # sendMessage endpoint รับ chat_id และ text
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        resp = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message
            },
            timeout=10  # ถ้า Telegram ไม่ตอบใน 10 วินาที ให้ throw Timeout
        )

        # raise_for_status() จะ throw HTTPError ถ้า status code เป็น 4xx หรือ 5xx
        # เช่น 401 (token ผิด), 400 (chat_id ผิด), 429 (rate limit)
        resp.raise_for_status()

    except requests.exceptions.Timeout:
        # network ช้า หรือ Telegram server ไม่ตอบ
        print(f"[{datetime.now()}] ⚠️ Telegram timeout")

    except requests.exceptions.HTTPError as e:
        # Telegram ตอบกลับมา แต่เป็น error เช่น token ผิด หรือ chat ไม่มี
        print(f"[{datetime.now()}] ⚠️ Telegram HTTP error: {e}")

    except Exception as e:
        # error อื่นๆ เช่น ไม่มี internet, DNS ใช้งานไม่ได้
        print(f"[{datetime.now()}] ⚠️ Telegram error: {e}")


# ====================================
# STATE MANAGEMENT
# ====================================
#
# State คืออะไร?
# โปรแกรมนี้ต้องจำสิ่งต่างๆ ระหว่าง loop เพื่อไม่ให้ส่ง alert ซ้ำ
# เช่น ถ้า EMA9 อยู่เหนือ EMA9 แล้ว ไม่ต้องส่ง alert ซ้ำอีกทุกนาที
#
# ทำไมต้องบันทึกลงไฟล์?
# ถ้าเก็บใน memory (variable ธรรมดา) พอ process crash หรือ restart
# ค่าทั้งหมดจะหายไป แล้วอาจส่ง alert ซ้ำได้อีกครั้ง
#
# State ที่เก็บ:
#   ema200_signal    : "bullish" / "bearish" / None — trend ปัจจุบัน
#   ema9_position    : "above" / "below" / None — ตำแหน่ง HA close vs EMA9
#   last_alert_candle: timestamp ของแท่งล่าสุดที่ส่ง EMA200 alert ไปแล้ว
#   heartbeat_sent   : list ของเวลาที่ส่ง heartbeat ในวันนี้ เช่น ["08:00", "14:00"]
#   heartbeat_date   : วันที่ปัจจุบัน ใช้รีเซ็ต heartbeat_sent ทุกวัน

def load_state():
    """
    โหลด state จากไฟล์ JSON

    Logic:
    1. ถ้าไม่มีไฟล์ → return default (เริ่มใหม่ทั้งหมด)
    2. ถ้ามีไฟล์แต่เสียหาย (JSON ผิดรูปแบบ) → return default + แจ้ง log
    3. ถ้าโหลดสำเร็จ → backfill key ที่อาจหายไปถ้า state เป็น version เก่า
    """

    # ค่า default ที่ใช้เมื่อยังไม่มีข้อมูลใดๆ
    default = {
        "ema200_signal": None,       # ยังไม่รู้ trend
        "ema9_position": None,       # ยังไม่รู้ตำแหน่ง
        "last_alert_candle": None,   # ยังไม่เคยส่ง EMA200 alert
        "heartbeat_sent": [],         # ยังไม่ได้ส่ง heartbeat วันนี้
        "ema200_slope_direction": None,   # "up" / "down" / "flat" — ทิศของ EMA200 ล่าสุด
        "ema200_slope_alert_candle": None # candle ที่ส่ง slope alert ไปแล้ว กันส่งซ้ำ
    }

    # ถ้าไฟล์ยังไม่มี (รันครั้งแรก) ให้ใช้ค่า default
    if not os.path.exists(STATE_FILE):
        return default

    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)

        # setdefault() จะเติม key ที่ขาดหายไปด้วยค่า default
        # มีประโยชน์เมื่ออัปเกรดโค้ดแล้วเพิ่ม key ใหม่ใน state
        # เช่น state.json เก่าไม่มี heartbeat_sent → ใส่ [] ให้อัตโนมัติ
        for key, val in default.items():
            state.setdefault(key, val)

        return state

    except (json.JSONDecodeError, IOError) as e:
        # json.JSONDecodeError: ไฟล์มีอยู่แต่เนื้อหาไม่ใช่ JSON ที่ valid
        # IOError: อ่านไฟล์ไม่ได้ เช่น permission ไม่พอ
        # ในทั้งสองกรณีให้เริ่มใหม่จาก default แทนที่จะ crash
        print(f"[{datetime.now()}] ⚠️ State file corrupted, resetting. Error: {e}")
        return default


def save_state(state):
    """
    บันทึก state ลงไฟล์ JSON

    ใช้ indent=2 เพื่อให้อ่านง่ายถ้าเปิดดูด้วย text editor

    Args:
        state (dict): state ทั้งหมดที่ต้องการบันทึก
    """

    try:
        with open(STATE_FILE, "w") as f:
            # indent=2 ทำให้ JSON มี whitespace สวยงาม อ่านง่ายขึ้น
            json.dump(state, f, indent=2)

    except IOError as e:
        # เขียนไฟล์ไม่ได้ เช่น disk เต็ม หรือ permission ไม่พอ
        # log แต่ไม่ crash เพราะ signal ยังทำงานได้แม้ไม่มี state
        print(f"[{datetime.now()}] ⚠️ Failed to save state: {e}")


# ====================================
# HEIKIN ASHI
# ====================================
#
# Heikin Ashi (平均足) คืออะไร?
# เป็น candle ชนิดหนึ่งที่ "เฉลี่ย" ราคาจาก candle ปกติ
# ทำให้ chart ดูเรียบขึ้น กรอง noise ออก มองเห็น trend ชัดกว่า
#
# สูตร:
#   HA Close = (Open + High + Low + Close) / 4
#   HA Open  = (HA Open[prev] + HA Close[prev]) / 2
#   HA High  = max(High, HA Open, HA Close)
#   HA Low   = min(Low,  HA Open, HA Close)
#
# ข้อแตกต่างสำคัญจาก candle ปกติ:
#   - HA Open และ HA Close ไม่ใช่ราคา "จริง" ในตลาด
#   - ค่า smoothing ทำให้ signal ช้ากว่า candle ปกติเล็กน้อย
#   - เหมาะสำหรับดู trend ไม่เหมาะสำหรับดู exact entry price

def build_heikin_ashi(df):
    """
    คำนวณ Heikin Ashi จาก DataFrame ราคาปกติ

    Args:
        df (pd.DataFrame): ข้อมูลราคาจาก TradingView
                           ต้องมี column: open, high, low, close

    Returns:
        ha (pd.DataFrame): HA candle ที่มี column: open, high, low, close
                           index เดียวกับ df (timestamp)
    """

    # สร้าง DataFrame ใหม่โดยใช้ index (timestamp) เดิมจาก df
    ha = pd.DataFrame(index=df.index)

    # --- HA Close ---
    # เฉลี่ยราคา OHLC ของ candle ปกติทั้ง 4 ค่า
    # ทำให้ close เรียบขึ้นและสะท้อน "กลางราคา" ของแท่งนั้น
    ha["close"] = (
        df["open"] + df["high"] + df["low"] + df["close"]
    ) / 4

    # --- HA Open ---
    # คำนวณด้วย loop เพราะแต่ละค่าขึ้นอยู่กับค่าก่อนหน้า (recursive)
    # ไม่สามารถใช้ vectorized operation ของ pandas ได้ตรงๆ
    ha_open = []

    for i in range(len(df)):

        if i == 0:
            # แท่งแรก: ไม่มี prev HA open/close ให้ใช้
            # ใช้ค่าเฉลี่ยของ open และ close ของแท่งปกติแรกแทน
            ha_open.append(
                (df["open"].iloc[0] + df["close"].iloc[0]) / 2
            )
        else:
            # แท่งถัดไป: เฉลี่ย HA Open และ HA Close ของแท่งก่อนหน้า
            # ทำให้ open "ลาก" ตามหลัง close อย่างช้าๆ
            ha_open.append(
                (ha_open[i - 1] + ha["close"].iloc[i - 1]) / 2
            )

    ha["open"] = ha_open

    # --- HA High ---
    # HA high ต้องครอบคลุม high จริง และ HA open/close ทั้งคู่
    # ใช้ pd.concat เพื่อรวม 3 column แล้วหาค่า max ในแต่ละแถว
    ha["high"] = pd.concat(
        [df["high"], ha["open"], ha["close"]], axis=1
    ).max(axis=1)

    # --- HA Low ---
    # HA low ต้องต่ำกว่าหรือเท่ากับ low จริง และ HA open/close ทั้งคู่
    ha["low"] = pd.concat(
        [df["low"], ha["open"], ha["close"]], axis=1
    ).min(axis=1)

    return ha


# ====================================
# DOWNLOAD DATA
# ====================================

def get_data():
    """
    ดึงข้อมูลราคา XAUUSD M15 จาก TradingView

    ดึง 500 แท่งล่าสุด (~5 วันของ M15)
    จำนวนนี้เพียงพอสำหรับคำนวณ EMA200 ที่แม่นยำ

    Returns:
        df (pd.DataFrame): ข้อมูลราคา OHLCV
        None: ถ้าดึงไม่ได้หรือ data ว่างเปล่า
    """

    try:
        df = tv.get_hist(
            symbol=SYMBOL,
            exchange=EXCHANGE,
            interval=Interval.in_15_minute,  # timeframe M15
            n_bars=500                        # จำนวนแท่งย้อนหลัง
        )

        # เช็คว่าได้ข้อมูลจริงๆ ไม่ใช่ None หรือ DataFrame ว่าง
        if df is None or df.empty:
            print(f"[{datetime.now()}] ⚠️ No data returned from TradingView")
            return None

        return df

    except Exception as e:
        # เช่น network ขาด, TradingView เปลี่ยน API, session หมดอายุ
        print(f"[{datetime.now()}] ⚠️ Failed to fetch data: {e}")
        return None

def calc_ema200_slope(ha, lookback=3):
    """
    คำนวณทิศทางและมุมเอียงของ EMA200 จาก HA close

    เทียบ ema200 ปัจจุบัน (แท่งปิดล่าสุด) กับ ema200 ก่อนหน้า lookback แท่ง
    แปลงเป็นมุม (degree) โดย normalize ด้วยราคาเฉลี่ย เพื่อให้ threshold
    ใช้ได้ stable ไม่ว่าทองจะอยู่โซนราคาไหน

    Args:
        ha (pd.DataFrame): ต้องมี column "ema200" และ "close" คำนวณแล้ว
        lookback (int): จำนวนแท่งย้อนหลังที่ใช้เทียบ

    Returns:
        direction (str): "up" / "down" / "flat"
        angle (float): มุมเอียงเป็น degree (ติดลบ = เอียงลง)
    """
    # ใช้ index -2 เป็น "ปัจจุบัน" เพราะแท่งปิดล่าสุดที่สมบูรณ์คือ iloc[-2]
    # (เหมือน logic ใน check_signal ที่ใช้ curr = ha.iloc[-2])
    if len(ha) < lookback + 2:
        return None, None

    ema_now = ha["ema200"].iloc[-2]
    ema_prev = ha["ema200"].iloc[-2 - lookback]
    avg_price = ha["close"].iloc[-2 - lookback:-1].mean()

    slope_pct = ((ema_now - ema_prev) / lookback) / avg_price * 100
    angle = math.degrees(math.atan(slope_pct))

    if angle > 0.05:
        direction = "up"
    elif angle < -0.05:
        direction = "down"
    else:
        direction = "flat"

    return direction, round(angle, 4)

# ====================================
# SIGNAL DETECTION
# ====================================
#
# Signal ที่ตรวจสอบมี 2 ประเภท:
#
# 1. EMA9 Cross EMA200 (Trend Change)
#    - EMA9 ตัดขึ้น EMA200 → UPTREND เริ่มต้น → แจ้งเตือน 🟢
#    - EMA9 ตัดลง EMA200 → DOWNTREND เริ่มต้น → แจ้งเตือน 🔴
#    - ใช้ last_alert_candle ป้องกันการส่งซ้ำในแท่งเดิม
#
# 2. HA Close Cross EMA9 (Entry Signal)
#    - HA Close ตัดขึ้น EMA9 → ราคาเริ่มแข็งแกร่ง → แจ้งเตือน ⬆️
#    - HA Close ตัดลง EMA9 → ราคาเริ่มอ่อนแอ → แจ้งเตือน ⬇️
#    - ใช้ ema9_position ป้องกันการส่งซ้ำจนกว่าจะตัดกลับ
#
# วิธีตรวจ "cross":
#    prev[A] <= prev[B]  และ  curr[A] > curr[B]  → A ตัดขึ้น B
#    prev[A] >= prev[B]  และ  curr[A] < curr[B]  → A ตัดลง B

def check_signal():
    """
    ฟังก์ชันหลักที่รันทุก 60 วินาที

    ขั้นตอน:
    1. ดึงข้อมูลราคาจาก TradingView
    2. คำนวณ Heikin Ashi
    3. คำนวณ EMA9 และ EMA200 บน HA Close
    4. ตรวจสอบ cross signal
    5. ส่ง Telegram ถ้าเกิด signal ใหม่
    6. บันทึก state ลงไฟล์
    """

    df = get_data()

    # ถ้าดึงข้อมูลไม่ได้ ให้ข้ามรอบนี้ไป จะลองใหม่อีกครั้งใน 60 วินาที
    if df is None:
        return

    # ต้องการอย่างน้อย 220 แท่งเพื่อให้ EMA200 คำนวณได้แม่นยำพอ
    # (EMA200 ต้องการข้อมูลย้อนหลัง 200 แท่ง + buffer เพิ่มอีกนิดหน่อย)
    if len(df) < 220:
        print(f"[{datetime.now()}] ⚠️ Not enough bars: {len(df)} (need 220+)")
        return

    # แปลงข้อมูลปกติเป็น Heikin Ashi
    ha = build_heikin_ashi(df)

    # คำนวณ EMA บน HA Close (ไม่ใช่ close ปกติ)
    # ewm = Exponential Weighted Moving Average
    # span=9  → EMA 9 period (ตอบสนองเร็ว)
    # span=200 → EMA 200 period (trend ระยะยาว)
    # adjust=False → ใช้สูตร recursive แบบ standard (เหมือน TradingView)
    ha["ema9"] = ha["close"].ewm(span=9, adjust=False).mean()
    ha["ema200"] = ha["close"].ewm(span=200, adjust=False).mean()

    # เลือกแท่งที่จะใช้ตรวจสอบ:
    #   iloc[-1] = แท่งปัจจุบัน (ยังไม่ปิด ค่าเปลี่ยนตลอดเวลา ❌ ไม่ใช้)
    #   iloc[-2] = แท่งปิดล่าสุดสมบูรณ์ ✅ ใช้เป็น "curr"
    #   iloc[-3] = แท่งก่อนหน้า ✅ ใช้เป็น "prev" เพื่อตรวจ cross
    prev = ha.iloc[-3]   # แท่งก่อนหน้าแท่งปิดล่าสุด
    curr = ha.iloc[-2]   # แท่งปิดล่าสุดที่สมบูรณ์แล้ว

    # ดึง timestamp ของ curr มาใช้เป็น ID ของแท่ง
    # ใช้เปรียบเทียบกับ last_alert_candle เพื่อป้องกันการส่งซ้ำ
    candle_time = str(curr.name)

    state = load_state()

    # ตรวจ trend ปัจจุบันจากความสัมพันธ์ EMA9 vs EMA200
    # EMA9 > EMA200 → ราคาระยะสั้นสูงกว่าระยะยาว → uptrend
    trend = (
        "UPTREND"
        if curr["ema9"] > curr["ema200"]
        else "DOWNTREND"
    )

    # Log ทุกรอบเพื่อ monitor ว่าโปรแกรมทำงานอยู่และค่าสมเหตุสมผล
    print(
        f"[{datetime.now()}] "
        f"Candle={candle_time} "
        f"Close={curr['close']:.2f} "
        f"EMA9={curr['ema9']:.2f} "
        f"EMA200={curr['ema200']:.2f} "
        f"Trend={trend}"
    )

    # ──────────────────────────────────────
    # SIGNAL 1: EMA9 CROSS EMA200
    # ──────────────────────────────────────
    # ตรวจว่า EMA9 เพิ่งตัดขึ้นหรือตัดลง EMA200 หรือไม่
    # "เพิ่ง" หมายถึง: แท่งก่อนหน้า (prev) อยู่คนละฝั่ง กับแท่งปัจจุบัน (curr)

    # EMA9 ตัดขึ้น EMA200:
    #   prev: EMA9 <= EMA200 (อยู่ใต้หรือเท่ากัน)
    #   curr: EMA9 >  EMA200 (ขึ้นไปอยู่เหนือ) → cross เกิดขึ้น!
    cross_up_200 = (
        prev["ema9"] <= prev["ema200"]
        and curr["ema9"] > curr["ema200"]
    )

    # EMA9 ตัดลง EMA200:
    #   prev: EMA9 >= EMA200 (อยู่เหนือหรือเท่ากัน)
    #   curr: EMA9 <  EMA200 (ลงไปอยู่ใต้) → cross เกิดขึ้น!
    cross_down_200 = (
        prev["ema9"] >= prev["ema200"]
        and curr["ema9"] < curr["ema200"]
    )

    if cross_up_200:

        # เช็ค candle_time ป้องกันส่งซ้ำ:
        # loop ทำงานทุก 60 วินาที แต่แท่ง M15 ปิดทุก 15 นาที
        # ดังนั้นในแท่งเดียวกัน loop จะผ่านมา ~15 ครั้ง
        # ถ้าไม่เช็ค จะส่ง alert 15 ครั้งต่อแท่ง
        if state["last_alert_candle"] != candle_time:

            send_telegram(
                f"📈 XAUUSD M15\n"
                f"🟢 EMA9 ABOVE EMA200\n"
                f"⏰ {candle_time}"
            )

            # บันทึกว่า trend เปลี่ยนเป็น bullish
            state["ema200_signal"] = "bullish"
            # บันทึก timestamp ของแท่งนี้ เพื่อป้องกันส่งซ้ำ
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

    # ──────────────────────────────────────
    # SIGNAL 2: HA CLOSE CROSS EMA9
    # ──────────────────────────────────────
    # ตรวจว่า HA Close เพิ่งตัดขึ้นหรือตัดลง EMA9 หรือไม่
    # Signal นี้ใช้ ema9_position เป็น guard แทน last_alert_candle
    # เพราะเราต้องการ alert ทุกครั้งที่ตัดกลับ ไม่ใช่แค่ครั้งแรก

    close_above_ema9 = (
        prev["close"] <= prev["ema9"]   # prev อยู่ใต้ EMA9
        and curr["close"] > curr["ema9"]  # curr ขึ้นเหนือ EMA9
    )

    close_below_ema9 = (
        prev["close"] >= prev["ema9"]   # prev อยู่เหนือ EMA9
        and curr["close"] < curr["ema9"]  # curr ลงใต้ EMA9
    )

    if close_above_ema9:

        # ส่งเฉพาะถ้า position เปลี่ยน (ไม่ใช่ "above" อยู่แล้ว)
        # ป้องกัน noise จากแท่งที่ touch EMA9 แล้วกลับมาซ้ำๆ
        if state["ema9_position"] != "above":

            send_telegram(
                f"⬆️ XAUUSD M15\n"
                f"HA Close ABOVE EMA9\n"
                f"📈 {trend}\n"        # บอก context ว่าตอนนี้อยู่ใน trend ไหน
                f"⏰ {candle_time}"
            )

            # อัปเดต position เป็น "above" เพื่อป้องกันส่งซ้ำ
            # จะส่งอีกครั้งก็ต่อเมื่อ HA Close ตัดลงต่ำกว่า EMA9 ก่อน
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

    # ──────────────────────────────────────
    # SIGNAL 3: EMA200 SLOPE (เอียง/เปลี่ยนทิศ)
    # ──────────────────────────────────────
    # บอกว่า EMA200 เริ่ม "เอียง" ขึ้น/ลงชัดเจน หรือกลับทิศ
    # ใช้คนละ guard กับ signal อื่น เพราะต้องการ alert เฉพาะตอนทิศเปลี่ยน

    slope_direction, slope_angle = calc_ema200_slope(ha, lookback=3)

    if slope_direction is not None:

        last_slope_direction = state.get("ema200_slope_direction")

        # alert เฉพาะตอนทิศเปลี่ยนจริง (ไม่ใช่ flat <-> flat) และยังไม่เคย
        # alert ในแท่งนี้มาก่อน (กันส่งซ้ำตอน loop วนทุก 60 วิ)
        direction_changed = (
            last_slope_direction is not None
            and last_slope_direction != "flat"
            and slope_direction != "flat"
            and slope_direction != last_slope_direction
        )

        if direction_changed and state.get("ema200_slope_alert_candle") != candle_time:

            arrow = "📈" if slope_direction == "up" else "📉"

            send_telegram(
                f"{arrow} XAUUSD M15\n"
                f"⚠️ EMA200 SLOPE เปลี่ยนทิศ → {slope_direction.upper()}\n"
                f"Angle: {slope_angle}°\n"
                f"EMA200: {curr['ema200']:.2f}\n"
                f"Price: {curr['close']:.2f} ({'above' if curr['close'] > curr['ema200'] else 'below'} EMA200)\n"
                f"Trend (EMA9/200): {trend}\n"
                f"⏰ {candle_time}"
            )

            state["ema200_slope_alert_candle"] = candle_time

        state["ema200_slope_direction"] = slope_direction

    # บันทึก state ที่อัปเดตแล้วกลับลงไฟล์ทุกรอบ
    save_state(state)
    """
    คำนวณทิศทางและมุมเอียงของ EMA200 จาก HA close

    เทียบ ema200 ปัจจุบัน (แท่งปิดล่าสุด) กับ ema200 ก่อนหน้า lookback แท่ง
    แปลงเป็นมุม (degree) โดย normalize ด้วยราคาเฉลี่ย เพื่อให้ threshold
    ใช้ได้ stable ไม่ว่าทองจะอยู่โซนราคาไหน

    Args:
        ha (pd.DataFrame): ต้องมี column "ema200" และ "close" คำนวณแล้ว
        lookback (int): จำนวนแท่งย้อนหลังที่ใช้เทียบ

    Returns:
        direction (str): "up" / "down" / "flat"
        angle (float): มุมเอียงเป็น degree (ติดลบ = เอียงลง)
    """
    # ใช้ index -2 เป็น "ปัจจุบัน" เพราะแท่งปิดล่าสุดที่สมบูรณ์คือ iloc[-2]
    # (เหมือน logic ใน check_signal ที่ใช้ curr = ha.iloc[-2])
    if len(ha) < lookback + 2:
        return None, None

    ema_now = ha["ema200"].iloc[-2]
    ema_prev = ha["ema200"].iloc[-2 - lookback]
    avg_price = ha["close"].iloc[-2 - lookback:-1].mean()

    slope_pct = ((ema_now - ema_prev) / lookback) / avg_price * 100
    angle = math.degrees(math.atan(slope_pct))

    if angle > 0.05:
        direction = "up"
    elif angle < -0.05:
        direction = "down"
    else:
        direction = "flat"

    return direction, round(angle, 4)

# ====================================
# MAIN LOOP
# ====================================

if __name__ == "__main__":
    # รันเฉพาะเมื่อ execute ไฟล์นี้โดยตรง
    # ถ้า import เป็น module จะไม่รันส่วนนี้

    # แจ้งให้รู้ว่า bot เพิ่ง start ขึ้นมา
    send_telegram("🚀 XAU Alert Started")

    while True:  # loop ไม่มีวันจบ รันตลอด 24 ชั่วโมง

        try:

            # ดึงเวลาปัจจุบัน timezone ไทย (UTC+7)
            now = datetime.now(ZoneInfo("Asia/Bangkok"))

            current_time = now.strftime("%H:%M")       # เช่น "08:00"
            current_date = now.strftime("%Y-%m-%d")    # เช่น "2025-06-01"

            state = load_state()

            # ────────────────────────────────
            # HEARTBEAT — รีเซ็ตรายวัน
            # ────────────────────────────────
            # heartbeat_date เก็บวันที่ที่ส่ง heartbeat ล่าสุด
            # ถ้าวันเปลี่ยน (เที่ยงคืน) ให้ reset รายการที่ส่งไปแล้ว
            last_heartbeat_date = state.get("heartbeat_date", "")

            if current_date != last_heartbeat_date:
                # วันใหม่เริ่มแล้ว → ล้าง list เพื่อให้ส่ง heartbeat ได้ใหม่
                state["heartbeat_sent"] = []
                state["heartbeat_date"] = current_date
                save_state(state)

            # ────────────────────────────────
            # HEARTBEAT — ส่งตามเวลาที่กำหนด
            # ────────────────────────────────
            # ส่ง 3 ครั้งต่อวัน เพื่อยืนยันว่า bot ยังทำงานอยู่
            # ถ้าไม่มี heartbeat มา → แสดงว่า bot อาจ crash หรือ network มีปัญหา
            HEARTBEAT_TIMES = ["08:00", "14:00", "19:00"]

            if current_time in HEARTBEAT_TIMES:

                # เช็คว่าเวลานี้ยังไม่ได้ส่งในวันนี้
                # (loop ทำงานทุก 60 วินาที จะผ่านเวลาเดียวกัน 1 ครั้งต่อวัน
                #  แต่ถ้า sleep ไม่แม่นนัก อาจผ่าน 08:00 สองครั้งในบางวัน)
                if current_time not in state["heartbeat_sent"]:

                    send_telegram(
                        f"💓 XAU Alert Alive\n"
                        f"Time: {current_time}"
                    )

                    # บันทึกว่าเวลานี้ส่งไปแล้ว
                    state["heartbeat_sent"].append(current_time)
                    save_state(state)

            # ────────────────────────────────
            # ตรวจสอบ signal หลัก
            # ────────────────────────────────
            check_signal()

        except Exception as e:
            # ดักทุก error ที่อาจเกิดใน loop นี้
            # ทำให้โปรแกรมไม่ crash แม้จะเกิดข้อผิดพลาดที่ไม่คาดคิด
            print(f"[{datetime.now()}] ❌ Main loop error: {e}")

        # รอ 60 วินาทีก่อน loop ถัดไป
        # M15 candle ปิดทุก 15 นาที การเช็คทุกนาทีเพียงพอ
        # และยังเหลือ buffer ถ้า TradingView ส่งข้อมูลช้าเล็กน้อย
        time.sleep(60)
