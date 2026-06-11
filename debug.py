import os
import sqlite3
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TV_USERNAME      = os.getenv("TV_USERNAME")
JOURNEY_SQLITE   = os.getenv("JOURNEY_SQLITE", "")

def load_price_levels():

    print(f"TELEGRAM_TOKEN {TELEGRAM_TOKEN}")
    print(f"TV_USERNAME {TV_USERNAME}")
    print(f"JOURNEY_SQLITE {JOURNEY_SQLITE}")

    """โหลด price levels ที่ active=1 จาก SQLite"""
    try:
        with sqlite3.connect(JOURNEY_SQLITE) as conn:
            rows = conn.execute(
                "SELECT price FROM price_levels WHERE active = 1 ORDER BY price"
            ).fetchall()

        return [row[0] for row in rows]

    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Failed to load price levels: {e}")
        return []

    price_levels = load_price_levels()
    for price in price_levels:
        state_key = f"h1_price_{price}"

        print(state_key);

