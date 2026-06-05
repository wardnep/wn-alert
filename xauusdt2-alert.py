import sqlite3
import requests
import os
from datetime import datetime

DB_PATH = "alerts.db"

# =========================
# TELEGRAM
# =========================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def send_telegram(message: str):
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


# =========================
# DATABASE
# =========================

def init_db():
    conn = sqlite3.connect(DB_PATH)

    try:
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS price_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            target_price REAL NOT NULL,
            direction TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            triggered_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)

        conn.commit()

    finally:
        conn.close()


def get_pending_alerts(symbol: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        cur = conn.cursor()

        cur.execute("""
        SELECT *
        FROM price_alerts
        WHERE symbol = ?
          AND status = 'pending'
        """, (symbol,))

        return cur.fetchall()

    finally:
        conn.close()


def mark_triggered(alert_id: int):
    conn = sqlite3.connect(DB_PATH)

    try:
        cur = conn.cursor()

        cur.execute("""
        UPDATE price_alerts
        SET
            status = 'triggered',
            triggered_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """, (alert_id,))

        conn.commit()

    finally:
        conn.close()


# =========================
# ALERT LOGIC
# =========================

def check_price_alerts(symbol: str, close_price: float):

    alerts = get_pending_alerts(symbol)

    for alert in alerts:

        alert_id = alert["id"]
        target_price = float(alert["target_price"])
        direction = alert["direction"]

        triggered = False

        if direction == "above":
            triggered = close_price > target_price

        elif direction == "below":
            triggered = close_price < target_price

        if not triggered:
            continue

        send_telegram(
            f"""
                🔔 PRICE ALERT

                Symbol: {symbol}
                Direction: {direction.upper()}
                Target: {target_price}
                Close: {close_price}
                """
        )

        mark_triggered(alert_id)


# =========================
# EXAMPLE
# =========================

def get_latest_h1_close(symbol: str) -> float:
    """
    แทนที่ด้วยโค้ด OANDA/Binance ของคุณ
    """
    return 3405.25


def main():

    init_db()

    symbol = "XAUUSD"

    close_price = get_latest_h1_close(symbol)

    print(
        f"[{datetime.now()}] "
        f"{symbol} H1 Close = {close_price}"
    )

    check_price_alerts(
        symbol=symbol,
        close_price=close_price
    )


if __name__ == "__main__":
    main()
