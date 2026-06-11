import os
import sqlite3
from datetime import datetime

JOURNEY_SQLITE   = os.getenv("JOURNEY_SQLITE", "")

def load_price_levels():
    """โหลด price levels ที่ active=1 จาก SQLite"""
    try:
        with sqlite3.connect(JOURNEY_SQLITE) as conn:
            rows = conn.execute(
                "SELECT price FROM price_levels WHERE active = 1 ORDER BY price"
            ).fetchall()

        print(rows)

        return [row[0] for row in rows]

    except Exception as e:
        print(f"[{datetime.now()}] ⚠️ Failed to load price levels: {e}")
        return []
