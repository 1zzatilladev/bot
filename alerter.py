"""
alerter.py
==========
SMS (Eskiz.uz) va Telegram (to'g'ridan-to'g'ri Bot API) orqali
kurs o'zgarishi haqida ogohlantirish yuboradi.

Muhit o'zgaruvchilari:
  ESKIZ_EMAIL      — Eskiz.uz hisob emaili
  ESKIZ_PASSWORD   — Eskiz.uz paroli
  ESKIZ_FROM       — Ro'yxatdan o'tgan SMS jo'natuvchi nomi (masalan: "4546")
  SMS_RECIPIENTS   — Vergul bilan ajratilgan telefon raqamlari (masalan: "998901234567,998907654321")
  BOT_TOKEN        — Telegram bot tokeni (bot.py dagi bilan bir xil)
  TG_ALERT_CHATIDS — Vergul bilan ajratilgan admin chat ID'lari (masalan: "123456789,-100987654321")
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import aiohttp

log = logging.getLogger(__name__)

# ── Konfiguratsiya ─────────────────────────────────────────────────────────────

ESKIZ_EMAIL    = os.getenv("ESKIZ_EMAIL", "")
ESKIZ_PASSWORD = os.getenv("ESKIZ_PASSWORD", "")
ESKIZ_FROM     = os.getenv("ESKIZ_FROM", "4546")
SMS_RECIPIENTS = [p.strip() for p in os.getenv("SMS_RECIPIENTS", "").split(",") if p.strip()]

TG_BOT_TOKEN     = os.getenv("BOT_TOKEN", "")
TG_ALERT_CHATIDS = [p.strip() for p in os.getenv("TG_ALERT_CHATIDS", "").split(",") if p.strip()]

ESKIZ_BASE_URL = "https://notify.eskiz.uz/api"

# Token xotirada saqlanadi (token 29 kun amal qiladi)
_eskiz_token: Optional[str] = None
_eskiz_token_ts: Optional[datetime] = None

_ESKIZ_TIMEOUT = aiohttp.ClientTimeout(total=10)
_TG_TIMEOUT    = aiohttp.ClientTimeout(total=10)


# ── Eskiz.uz: avtorizatsiya ────────────────────────────────────────────────────

async def _eskiz_login(session: aiohttp.ClientSession) -> Optional[str]:
    """Eskiz.uz API dan JWT token oladi."""
    if not ESKIZ_EMAIL or not ESKIZ_PASSWORD:
        log.warning("SMS: ESKIZ_EMAIL yoki ESKIZ_PASSWORD .env da topilmadi")
        return None
    try:
        async with session.post(
            f"{ESKIZ_BASE_URL}/auth/login",
            data={"email": ESKIZ_EMAIL, "password": ESKIZ_PASSWORD},
            timeout=_ESKIZ_TIMEOUT,
        ) as r:
            data = await r.json(content_type=None)
            token = (data.get("data") or {}).get("token")
            if token:
                log.info("Eskiz.uz: avtorizatsiya muvaffaqiyatli")
                return str(token)
            log.warning("Eskiz.uz login: token topilmadi — %s", data)
    except Exception as e:
        log.error("Eskiz.uz login xato: %s", e)
    return None


async def _eskiz_send_one(
    session: aiohttp.ClientSession,
    token: str,
    phone: str,
    text: str,
) -> bool:
    """Bitta telefon raqamga SMS yuboradi. True = muvaffaqiyatli."""
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    try:
        async with session.post(
            f"{ESKIZ_BASE_URL}/message/sms/send",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "mobile_phone": clean_phone,
                "message":      text,
                "from":         ESKIZ_FROM,
                "callback_url": "",
            },
            timeout=_ESKIZ_TIMEOUT,
        ) as r:
            data = await r.json(content_type=None)
            status = data.get("status")
            if status in ("waiting", "ok", "sent"):
                log.info("SMS yuborildi: +%s (%s)", clean_phone, status)
                return True
            log.warning("SMS yuborishda kutilmagan status=%s raqam=+%s: %s",
                        status, clean_phone, data)
    except Exception as e:
        log.error("SMS yuborishda xato (+%s): %s", clean_phone, e)
    return False


async def send_sms_alert(text: str) -> int:
    """
    Barcha SMS_RECIPIENTS ga ogohlantirish matnini yuboradi.
    Qaytaradi: muvaffaqiyatli yuborilgan SMS soni.
    """
    global _eskiz_token

    if not SMS_RECIPIENTS:
        log.debug("SMS: SMS_RECIPIENTS bo'sh — yuborilmadi")
        return 0
    if not ESKIZ_EMAIL or not ESKIZ_PASSWORD:
        log.warning("SMS: Eskiz.uz hisob ma'lumotlari (.env) topilmadi")
        return 0

    sent = 0
    async with aiohttp.ClientSession() as session:
        if _eskiz_token is None:
            _eskiz_token = await _eskiz_login(session)
        if not _eskiz_token:
            return 0

        for phone in SMS_RECIPIENTS:
            ok = await _eskiz_send_one(session, _eskiz_token, phone, text)
            if not ok:
                # Token muddati o'tgan bo'lishi mumkin — yangilash
                log.info("SMS token yangilanmoqda...")
                _eskiz_token = await _eskiz_login(session)
                if _eskiz_token:
                    ok = await _eskiz_send_one(session, _eskiz_token, phone, text)
            if ok:
                sent += 1

    return sent


# ── Telegram: to'g'ridan-to'g'ri Bot API ──────────────────────────────────────

async def send_telegram_alert(text: str, parse_mode: str = "HTML") -> int:
    """
    TG_ALERT_CHATIDS ga Telegram xabari yuboradi (bot polling bilan emas,
    to'g'ridan-to'g'ri sendMessage API orqali).
    Qaytaradi: muvaffaqiyatli yuborilgan xabar soni.
    """
    if not TG_ALERT_CHATIDS:
        log.debug("TG: TG_ALERT_CHATIDS bo'sh — yuborilmadi")
        return 0
    if not TG_BOT_TOKEN:
        log.warning("TG: BOT_TOKEN topilmadi")
        return 0

    sent = 0
    url  = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        for chat_id in TG_ALERT_CHATIDS:
            try:
                async with session.post(
                    url,
                    json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                    timeout=_TG_TIMEOUT,
                ) as r:
                    data = await r.json(content_type=None)
                    if data.get("ok"):
                        sent += 1
                        log.debug("TG alert yuborildi: chat=%s", chat_id)
                    else:
                        log.warning("TG alert: chat=%s — %s", chat_id,
                                    data.get("description", "unknown error"))
            except Exception as e:
                log.warning("TG alert xato (chat=%s): %s", chat_id, e)
    return sent


# ── Xabar formatlash ──────────────────────────────────────────────────────────

def format_sms_alert(change: dict) -> str:
    """
    Spetsifikatsiya bo'yicha SMS matni:

    [ALERT]
    Bank: Kapitalbank
    Pair: RUB → UZS
    Old: 165.20
    New: 166.80
    Change: +1.60 UZS 🔼
    """
    direction_icon = "🔼" if change["direction"] == "UP" else "🔽"
    sign           = "+" if float(change["change"]) >= 0 else ""
    pair_display   = change["pair"].replace("_", " -> ")

    ts = change.get("timestamp", "")
    if isinstance(ts, datetime):
        ts = ts.strftime("%Y-%m-%dT%H:%M:%SZ")

    pair_unit = "UZS" if "RUB_UZS" in change["pair"] else "RUB"

    lines = [
        f"[ALERT] {ts}",
        f"Bank: {change['bank']}",
        f"Pair: {pair_display}",
        f"Old:  {change['old_rate']:.2f}",
        f"New:  {change['new_rate']:.2f}",
        f"Change: {sign}{change['change']:.2f} {pair_unit} {direction_icon}",
    ]
    if change.get("anomaly"):
        lines.append("⚠ ANOMALIYA: Keskin sakrash!")
    return "\n".join(lines)


def format_telegram_alert(changes: list[dict], summary: dict) -> str:
    """Bir nechta o'zgarishlar uchun HTML Telegram xabari."""
    if not changes:
        return ""

    lines: list[str] = ["⚡ <b>Kurs o'zgardi!</b>", ""]

    anomalies = [c for c in changes if c.get("anomaly")]
    if anomalies:
        lines.append(f"🚨 <b>ANOMALIYA aniqlandi: {len(anomalies)} ta keskin sakrash!</b>")
        lines.append("")

    for c in changes[:15]:
        direction_icon = "🔼" if c["direction"] == "UP" else "🔽"
        sign           = "+" if float(c["change"]) >= 0 else ""
        pair_disp      = c["pair"].replace("_", " → ")
        anom_mark      = " ⚠" if c.get("anomaly") else ""
        lines.append(
            f"{direction_icon} <b>{c['bank']}</b>  <i>{pair_disp}</i>{anom_mark}\n"
            f"   {c['old_rate']:.2f} → <b>{c['new_rate']:.2f}</b>"
            f"  ({sign}{c['change']:.2f})"
        )

    if len(changes) > 15:
        lines.append(f"\n<i>... va yana {len(changes) - 15} ta o'zgarish</i>")

    ts = summary.get("timestamp")
    if isinstance(ts, datetime):
        ts_str = ts.strftime("%d.%m.%Y %H:%M")
    else:
        ts_str = str(ts or "")

    total   = summary.get("total_changed", len(changes))
    avg_chg = summary.get("avg_changes", [])
    lines.append("")
    lines.append(f"🕐 {ts_str}  |  {total} bank o'zgardi")
    if avg_chg:
        for ac in avg_chg:
            d_icon = "🔼" if ac["direction"] == "UP" else "🔽"
            sign   = "+" if float(ac["change"]) >= 0 else ""
            lines.append(
                f"📊 Bozor o'rtacha {ac['pair'].replace('_', ' → ')}: "
                f"{ac['old_rate']:.2f} → {ac['new_rate']:.2f} "
                f"({sign}{ac['change']:.2f}) {d_icon}"
            )

    return "\n".join(lines)
