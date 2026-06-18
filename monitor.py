"""
monitor.py
==========
Real-time kurs monitoring daemon.

Vazifalar:
  • Barcha banklar RUB/UZS kurslarini parallel oladi (retry bilan)
  • Har bir bank uchun o'zgarishni aniqlaydi (delta, direction)
  • Bozor o'rtacha kursini hisoblaydi va uning o'zgarishini kuzatadi
  • Anomaliyalarni aniqlaydi (keskin sakrash ≥ ANOMALY_THRESHOLD%)
  • SMS (Eskiz.uz) va Telegram orqali ogohlantirish yuboradi
  • Barcha alertlarni alerts.log fayliga yozadi
  • To'xtovsiz, 24/7 ishlaydi

Ishga tushirish:
  python monitor.py                   # mustaqil daemon
  python monitor.py --once            # bir marta tekshirib chiqish
  python monitor.py --interval 60     # har 60 soniyada

Muhit o'zgaruvchilari (.env dan o'qiladi):
  MONITOR_INTERVAL   — tekshirish oralig'i, soniya (standart: 60)
  ANOMALY_THRESHOLD  — anomaliya uchun % o'zgarish chegarasi (standart: 5.0)
  MIN_CHANGE_RUB_UZS — RUB_UZS uchun minimal o'zgarish (standart: 0.05 UZS)
  MIN_CHANGE_UZS_RUB — UZS_RUB uchun minimal o'zgarish (standart: 0.00001)
  FETCH_TIMEOUT      — har bir HTTP so'rov uchun timeout, soniya (standart: 5)
  ESKIZ_EMAIL        — Eskiz.uz emaili
  ESKIZ_PASSWORD     — Eskiz.uz paroli
  ESKIZ_FROM         — SMS jo'natuvchi nomi
  SMS_RECIPIENTS     — Vergul bilan ajratilgan telefon raqamlari
  BOT_TOKEN          — Telegram bot tokeni
  TG_ALERT_CHATIDS   — Admin Telegram chat ID'lari (vergul bilan)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import db
import fetcher
from alerter import (
    format_sms_alert,
    format_telegram_alert,
    send_sms_alert,
    send_telegram_alert,
)

# ── Konfiguratsiya ─────────────────────────────────────────────────────────────

MONITOR_INTERVAL  = int(os.getenv("MONITOR_INTERVAL", "60"))
ANOMALY_THRESHOLD = float(os.getenv("ANOMALY_THRESHOLD", "5.0"))

# Minimal o'zgarish — kichik shovqinni filtrlash
MIN_CHANGE: dict[str, float] = {
    "RUB_UZS": float(os.getenv("MIN_CHANGE_RUB_UZS", "0.05")),
    "UZS_RUB": float(os.getenv("MIN_CHANGE_UZS_RUB", "0.00001")),
}

ALERT_LOG_FILE = Path(__file__).parent / "alerts.log"
PAIRS          = ["RUB_UZS", "UZS_RUB"]

# ── Logging sozlamalari ────────────────────────────────────────────────────────

# Windows konsol cp1251 encoding uchun: UTF-8 ga majburan o'tkazamiz
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_fmt = logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
_console = logging.StreamHandler(sys.stdout)
_console.setFormatter(_fmt)
_fileh = logging.FileHandler(Path(__file__).parent / "monitor.log", encoding="utf-8")
_fileh.setFormatter(_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_console, _fileh])
log = logging.getLogger("monitor")


# ── Yordamchi funksiyalar ─────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_local() -> datetime:
    return datetime.now()


def _log_alert_to_file(record: dict) -> None:
    """Alert yozuvini JSON-Lines fayli ga yozadi."""
    try:
        with ALERT_LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Alert log yozilmadi: %s", e)


def _is_anomaly(old_rate: float, new_rate: float) -> bool:
    """Kurs ANOMALY_THRESHOLD foizdan ko'proq o'zgarsa anomaliya."""
    if old_rate <= 0:
        return False
    return abs(new_rate - old_rate) / old_rate * 100 >= ANOMALY_THRESHOLD


# ── O'zgarish aniqlash ────────────────────────────────────────────────────────

def build_change_record(entry: dict, pair: str) -> dict:
    """
    Bitta bank uchun spetsifikatsiya bo'yicha tuzilgan JSON o'zgarish yozuvi:
    {
      "bank": "Kapitalbank",
      "pair": "RUB_UZS",
      "old_rate": 165.20,
      "new_rate": 166.80,
      "change": 1.60,
      "direction": "UP",
      "timestamp": "2026-06-18T14:00:00Z"
    }
    """
    new_rate = float(entry["rate"])
    old_rate = float(entry["prev_rate"])
    delta    = round(new_rate - old_rate, 6)
    return {
        "bank":      entry["name"],
        "key":       entry["key"],
        "pair":      pair,
        "old_rate":  round(old_rate, 6),
        "new_rate":  round(new_rate, 6),
        "change":    delta,
        "direction": "UP" if delta > 0 else "DOWN",
        "timestamp": _now_iso(),
        "type":      entry.get("type", "bank"),
        "anomaly":   _is_anomaly(old_rate, new_rate),
        "source":    entry.get("source", ""),
    }


def detect_bank_changes(cache: dict[str, list[dict]]) -> list[dict]:
    """
    Kesh dagi har bir bank yozuvini tekshiradi.
    fetcher.refresh_all() tomonidan to'ldirilgan `change` va `prev_rate` maydonlariga tayanadi.
    Qaytaradi: spec-format o'zgarish yozuvlari ro'yxati.
    """
    changes: list[dict] = []
    for pair, entries in cache.items():
        min_delta = MIN_CHANGE.get(pair, 0.0001)
        for entry in entries:
            if entry.get("change") not in ("up", "down"):
                continue
            rate     = entry.get("rate")
            prev     = entry.get("prev_rate")
            if rate is None or prev is None:
                continue
            if abs(rate - prev) < min_delta:
                continue
            rec = build_change_record(entry, pair)
            changes.append(rec)
            log_level = logging.WARNING if rec["anomaly"] else logging.INFO
            log.log(
                log_level,
                "[O'ZGARISH%s] %-20s | %-7s | %8.4f → %8.4f (%+.4f) %s",
                " ⚠ANOMALIYA" if rec["anomaly"] else "",
                rec["bank"], pair,
                rec["old_rate"], rec["new_rate"],
                rec["change"], rec["direction"],
            )
    return changes


# ── Bozor o'rtachasi ──────────────────────────────────────────────────────────

def compute_market_averages(cache: dict[str, list[dict]]) -> dict[str, Optional[float]]:
    """Har bir juft uchun faqat banklar (type=="bank") bo'yicha o'rtacha kurs."""
    avgs: dict[str, Optional[float]] = {}
    for pair, entries in cache.items():
        rates = [
            e["rate"] for e in entries
            if e.get("rate") is not None and e.get("type") == "bank"
        ]
        avgs[pair] = round(statistics.mean(rates), 6) if rates else None
    return avgs


_prev_market_avgs: dict[str, Optional[float]] = {}


def detect_average_changes(
    current: dict[str, Optional[float]],
    previous: dict[str, Optional[float]],
) -> list[dict]:
    """Bozor o'rtacha kursidagi o'zgarishlarni aniqlaydi."""
    changes: list[dict] = []
    for pair, curr in current.items():
        prev = previous.get(pair)
        if curr is None or prev is None:
            continue
        delta    = round(curr - prev, 6)
        min_d    = MIN_CHANGE.get(pair, 0.0001)
        if abs(delta) < min_d:
            continue
        changes.append({
            "bank":      "BOZOR O'RTACHA",
            "key":       f"market_avg_{pair.lower()}",
            "pair":      pair,
            "old_rate":  round(prev, 6),
            "new_rate":  round(curr, 6),
            "change":    delta,
            "direction": "UP" if delta > 0 else "DOWN",
            "timestamp": _now_iso(),
            "type":      "market_average",
            "anomaly":   _is_anomaly(prev, curr),
        })
    return changes


# ── Unavailable banklar ───────────────────────────────────────────────────────

def get_unavailable(cache: dict[str, list[dict]]) -> dict[str, list[str]]:
    """rate=None bo'lgan banklar — "unavailable" deb belgilanadi."""
    return {
        pair: [e["name"] for e in entries if e.get("rate") is None]
        for pair, entries in cache.items()
    }


def get_ok_counts(cache: dict[str, list[dict]]) -> dict[str, int]:
    return {
        pair: sum(1 for e in entries if e.get("rate") is not None)
        for pair, entries in cache.items()
    }


# ── Asosiy monitoring tsikli ──────────────────────────────────────────────────

async def run_cycle() -> dict:
    """
    Bitta monitoring tsikli. Qaytaradi: to'liq natija dict (JSON-serializable).

    Qaytariladigan tuzilma:
    {
      "status": "ok",
      "timestamp": "2026-06-18T14:00:00Z",
      "banks_ok": {"RUB_UZS": 22, "UZS_RUB": 18},
      "banks_unavailable": {"RUB_UZS": [...], "UZS_RUB": [...]},
      "changes": [...],          # spec-format o'zgarish yozuvlari
      "avg_changes": [...],      # bozor o'rtacha o'zgarishlari
      "market_averages": {...},  # joriy o'rtachalar
      "anomalies": [...],        # anomaliya bo'lgan o'zgarishlar
      "alerts_sent": {"sms": N, "telegram": N}
    }
    """
    global _prev_market_avgs

    ts = _now_iso()
    log.info("──── Monitoring tsikli: %s ────", ts)

    # 1. Barcha manbalardan kurslarni olish (retry fetcher.py da ishlangan)
    try:
        await fetcher.refresh_all()
    except Exception as e:
        log.error("Kurs olishda kritik xato: %s", e, exc_info=True)
        return {"status": "error", "timestamp": ts, "error": str(e)}

    cache = {pair: (fetcher.get_cached(pair) or []) for pair in PAIRS}

    # 2. Bank o'zgarishlarini aniqlash
    bank_changes = detect_bank_changes(cache)

    # 3. Bozor o'rtachasi o'zgarishi
    current_avgs = compute_market_averages(cache)
    avg_changes  = detect_average_changes(current_avgs, _prev_market_avgs)
    _prev_market_avgs = current_avgs

    all_changes = bank_changes + avg_changes
    anomalies   = [c for c in all_changes if c.get("anomaly")]

    # 4. Barcha alertlarni log fayliga yozish
    for c in all_changes:
        _log_alert_to_file(c)

    # 5. SMS va Telegram yuborish
    sms_sent = 0
    tg_sent  = 0

    if all_changes:
        summary = {
            "timestamp":     _now_local(),
            "total_changed": len(bank_changes),
            "avg_changes":   avg_changes,
        }

        # SMS: har bir o'zgarish uchun alohida xabar (maksimal 5 ta)
        # Anomaliyalar doimo yuboriladi, keyin qolganlar
        priority = sorted(all_changes, key=lambda c: (not c["anomaly"], 0))
        for c in priority[:5]:
            sms_text = format_sms_alert(c)
            n = await send_sms_alert(sms_text)
            sms_sent += n

        # Telegram: bitta umumiy xabar
        tg_text = format_telegram_alert(all_changes, summary)
        if tg_text:
            tg_sent = await send_telegram_alert(tg_text)

    # 6. Natija
    ok_counts   = get_ok_counts(cache)
    unavailable = get_unavailable(cache)

    if unavailable:
        for pair, names in unavailable.items():
            if names:
                log.warning("Mavjud emas (%s): %s", pair, ", ".join(names))

    result = {
        "status":             "ok",
        "timestamp":          ts,
        "banks_ok":           ok_counts,
        "banks_unavailable":  unavailable,
        "changes":            all_changes,
        "avg_changes":        avg_changes,
        "market_averages":    {k: v for k, v in current_avgs.items() if v is not None},
        "anomalies":          anomalies,
        "alerts_sent":        {"sms": sms_sent, "telegram": tg_sent},
    }

    log.info(
        "──── Tsikl tugadi | O'zgarish: %d | Anomaliya: %d | SMS: %d | TG: %d ────",
        len(all_changes), len(anomalies), sms_sent, tg_sent,
    )
    return result


# ── Cheksiz ishlash ───────────────────────────────────────────────────────────

async def run_forever(interval: int = MONITOR_INTERVAL) -> None:
    """24/7 monitoring daemoni. Ctrl+C bilan to'xtatiladi."""
    db.init_db()
    log.info(
        "Monitoring daemon ishga tushdi | interval=%ds | anomaly_threshold=%.1f%%",
        interval, ANOMALY_THRESHOLD,
    )

    # Boshlang'ich kurslarni olish (birinchi tsikl uchun prev_rate yo'q bo'ladi)
    log.info("Boshlang'ich ma'lumotlar olinmoqda...")
    try:
        await fetcher.refresh_all()
        cache = {pair: (fetcher.get_cached(pair) or []) for pair in PAIRS}
        ok    = get_ok_counts(cache)
        log.info("Boshlang'ich kurslar olindi: %s", ok)
    except Exception as e:
        log.warning("Boshlang'ich kurs olishda xato (monitoring davom etadi): %s", e)

    cycle = 0
    while True:
        cycle += 1
        log.debug("Tsikl #%d", cycle)
        try:
            await run_cycle()
        except asyncio.CancelledError:
            log.info("Monitoring to'xtatildi (CancelledError)")
            break
        except Exception as e:
            log.error("Tsikl #%d kutilmagan xato: %s", cycle, e, exc_info=True)
        await asyncio.sleep(interval)


# ── CLI ───────────────────────────────────────────────────────────────────────

async def _run_once() -> None:
    """Bir marta tekshirib, natijani chiqaradi (test uchun)."""
    db.init_db()
    log.info("Bir martalik tekshirish...")
    try:
        await fetcher.refresh_all()
    except Exception as e:
        log.error("Kurs olishda xato: %s", e)
        return

    # Kurslarni ekranga chiqarish
    for pair in PAIRS:
        entries = fetcher.get_cached(pair) or []
        ok      = [e for e in entries if e.get("rate") is not None]
        no      = [e for e in entries if e.get("rate") is None]
        print(f"\n{'─'*60}")
        print(f"  {pair}  ({len(ok)} ta kurs, {len(no)} ta mavjud emas)")
        print(f"{'─'*60}")
        for e in ok[:25]:
            ch_icon = {"up": "🟢", "down": "🔴", "same": "⚪", "unknown": "⚪"}.get(
                e.get("change", ""), "⚪"
            )
            print(f"  {ch_icon} {e['name']:<25}  {e['rate']:.4f}")
        if no:
            print(f"\n  ⛔ Mavjud emas: {', '.join(e['name'] for e in no)}")

    ref = fetcher.get_reference_rate("RUB_UZS")
    ref_label = fetcher.get_reference_label("RUB_UZS")
    if ref:
        print(f"\n  📌 Taqqoslash kursi ({ref_label}): {ref:.4f}")

    # DB dan so'nggi 5 ta tarix yozuvi
    hist = db.get_history("kapitalbank", "RUB_UZS", limit=5)
    if hist:
        print(f"\n  📊 Kapitalbank RUB_UZS oxirgi 5 ta yozuv:")
        for ts, rate in hist:
            print(f"     {ts}  {rate:.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="RUB/UZS kurs monitoring daemoni"
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Bir marta tekshirib chiqish (daemon rejimi emas)",
    )
    parser.add_argument(
        "--interval", type=int, default=MONITOR_INTERVAL,
        help=f"Tekshirish oralig'i soniyada (standart: {MONITOR_INTERVAL})",
    )
    parser.add_argument(
        "--purge-days", type=int, default=0,
        help="Eski tarix yozuvlarini o'chirish (0 = o'chirmaslik)",
    )
    args = parser.parse_args()

    if args.purge_days > 0:
        db.init_db()
        n = db.purge_old_history(args.purge_days)
        print(f"O'chirildi: {n} ta eski tarix yozuvi ({args.purge_days} kundan eski)")
        return

    if args.once:
        asyncio.run(_run_once())
    else:
        try:
            asyncio.run(run_forever(args.interval))
        except KeyboardInterrupt:
            log.info("Monitoring Ctrl+C bilan to'xtatildi.")


if __name__ == "__main__":
    main()
