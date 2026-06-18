"""
db.py
=====
Oldingi kurs qiymatlarini SQLite da saqlaydi.
  prev_rates    — oxirgi kurs (O(1) taqqoslash uchun)
  rate_history  — kurs tarixi (grafik va trend tahlili uchun)
"""

import sqlite3
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "rates.db"


def init_db() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prev_rates (
                svc_key  TEXT NOT NULL,
                pair     TEXT NOT NULL,
                rate     REAL NOT NULL,
                saved_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (svc_key, pair)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rate_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                svc_key     TEXT NOT NULL,
                pair        TEXT NOT NULL,
                rate        REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_rh_key_pair_time
                ON rate_history (svc_key, pair, recorded_at)
        """)
        conn.commit()


def get_prev(svc_key: str, pair: str) -> Optional[float]:
    """Oxirgi saqlangan kursni qaytaradi. Yo'q bo'lsa None."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT rate FROM prev_rates WHERE svc_key = ? AND pair = ?",
            (svc_key, pair),
        ).fetchone()
    return float(row[0]) if row else None


def save_rates(entries: list[tuple[str, str, float]]) -> None:
    """
    Kurslarni saqlaydi yoki yangilaydi.
    entries: [(svc_key, pair, rate), ...]
    """
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO prev_rates (svc_key, pair, rate, saved_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(svc_key, pair) DO UPDATE SET
                rate     = excluded.rate,
                saved_at = excluded.saved_at
            """,
            entries,
        )
        conn.commit()


def save_history(entries: list[tuple[str, str, float]]) -> None:
    """
    Kurs tarixiga yangi yozuvlar qo'shadi (faqat o'zgargan kurslar uchun chaqirish tavsiya etiladi).
    entries: [(svc_key, pair, rate), ...]
    """
    if not entries:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO rate_history (svc_key, pair, rate) VALUES (?, ?, ?)",
            entries,
        )
        conn.commit()


def get_history(
    svc_key: str,
    pair: str,
    limit: int = 200,
    since: Optional[str] = None,
) -> list[tuple[str, float]]:
    """
    Bitta bank uchun kurs tarixini qaytaradi.
    Qaytaradi: [(recorded_at_iso, rate), ...] yangi → eski tartibida.
    """
    with sqlite3.connect(DB_PATH) as conn:
        if since:
            rows = conn.execute(
                """
                SELECT recorded_at, rate FROM rate_history
                WHERE svc_key = ? AND pair = ? AND recorded_at >= ?
                ORDER BY recorded_at DESC LIMIT ?
                """,
                (svc_key, pair, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT recorded_at, rate FROM rate_history
                WHERE svc_key = ? AND pair = ?
                ORDER BY recorded_at DESC LIMIT ?
                """,
                (svc_key, pair, limit),
            ).fetchall()
    return [(r[0], float(r[1])) for r in rows]


def get_all_history(
    pair: str,
    limit: int = 2000,
    since: Optional[str] = None,
) -> list[tuple[str, str, float]]:
    """
    Barcha banklar uchun kurs tarixini qaytaradi.
    Qaytaradi: [(svc_key, recorded_at, rate), ...] yangi → eski.
    """
    with sqlite3.connect(DB_PATH) as conn:
        if since:
            rows = conn.execute(
                """
                SELECT svc_key, recorded_at, rate FROM rate_history
                WHERE pair = ? AND recorded_at >= ?
                ORDER BY recorded_at DESC LIMIT ?
                """,
                (pair, since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT svc_key, recorded_at, rate FROM rate_history
                WHERE pair = ?
                ORDER BY recorded_at DESC LIMIT ?
                """,
                (pair, limit),
            ).fetchall()
    return [(r[0], r[1], float(r[2])) for r in rows]


def purge_old_history(days: int = 30) -> int:
    """30 kundan eski tarix yozuvlarini o'chiradi. O'chirilgan satr sonini qaytaradi."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "DELETE FROM rate_history WHERE recorded_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        conn.commit()
        return cur.rowcount
