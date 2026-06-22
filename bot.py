"""
bot.py — Telegram valyuta kurslari boti (aiogram 3.x)

Format: har bank uchun kurs + Unired bilan farq + o'sish/pasayish
Bildirishnoma: kurs o'zgarganda barcha obunachilarga avtomatik xabar
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

import db
import fetcher

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Sozlamalar ───────────────────────────────────────────────────────────────

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise SystemExit("❌  BOT_TOKEN topilmadi. .env ga  BOT_TOKEN=...  yozing.")

CHANNEL_USERNAME:    str = os.getenv("CHANNEL_USERNAME", "")
SUPPORT_URL:         str = os.getenv("SUPPORT_URL", "")
UPDATE_INTERVAL:     int = int(os.getenv("AUTO_INTERVAL", os.getenv("UPDATE_INTERVAL", "600")))
NOTIFY_MIN_INTERVAL: int = int(os.getenv("NOTIFY_MIN_INTERVAL", "3600"))  # o'zgarmasa ham, kamida 1 soatda bir xabar

USERS_FILE = Path(__file__).parent / "users.json"

# ─── Tugma → juft ─────────────────────────────────────────────────────────────

BUTTON_TO_PAIR: dict[str, str] = {
    "🇷🇺 RUB → 🇺🇿 UZS": "RUB_UZS",
    "🇺🇿 UZS → 🇷🇺 RUB": "UZS_RUB",
}

PAIR_HEADER: dict[str, str] = {
    "RUB_UZS": "🇷🇺 <b>RUB → 🇺🇿 UZS</b>",
    "UZS_RUB": "🇺🇿 <b>UZS → 🇷🇺 RUB</b>",
}

PAIR_DISPLAY: dict[str, dict] = {
    "RUB_UZS": {"multiplier": 1,    "unit": "so'm",  "decimals": 2},
    "UZS_RUB": {"multiplier": 1000, "unit": "rubl",  "decimals": 2},
}

# Manba turi belgisi (Curso uslubida)
#   🏦 — markaziy bank rasmiy kursi
#   💳 — pul o'tkazma tizimi (kartaga/hisobga)
#   💱 — bank ilovasidagi ayirboshlash kursi
TYPE_ICON: dict[str, str] = {
    "official": "🏦",
    "card":     "💳",
    "market":   "💳",
    "transfer": "💳",
    "bank":     "💱",
}

# Bildirishnoma yuborish uchun juftlar (faqat eng muhimlar)
NOTIFY_PAIRS = ["RUB_UZS", "UZS_RUB"]

# ─── Foydalanuvchilar (xotirada + fayl) ──────────────────────────────────────

_users: set[int] = set()
_last_notify_time: Optional[datetime] = None

# Tugma bosishni cheklash (flood control oldini olish): user → oxirgi bosish vaqti
_last_button_press: dict[int, datetime] = {}
BUTTON_COOLDOWN = 1.5  # soniya — shu vaqt ichida qayta bosilsa e'tiborsiz qoldiriladi


def _load_users_from_file() -> None:
    global _users
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        _users = {int(u) for u in data.get("users", [])}
    except Exception:
        _users = set()


def _flush_users() -> None:
    USERS_FILE.write_text(
        json.dumps({"users": sorted(_users)}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _add_user(user_id: int) -> None:
    if user_id not in _users:
        _users.add(user_id)
        _flush_users()
        log.info("Yangi foydalanuvchi saqlandi: %s", user_id)


def _remove_user(user_id: int) -> None:
    if user_id in _users:
        _users.discard(user_id)
        _flush_users()
        log.info("Foydalanuvchi o'chirildi (bloklagan): %s", user_id)


# ─── Klaviatura ───────────────────────────────────────────────────────────────

def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🇷🇺 RUB → 🇺🇿 UZS"),
                KeyboardButton(text="🇺🇿 UZS → 🇷🇺 RUB"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


def _support_footer() -> str:
    """
    Support/kanal havolalarini xabar MATNIGA qo'shadi.
    Shunday qilib reply klaviatura (RUB↔UZS tugmalari) doim ko'rinib turadi —
    inline klaviatura bilan band qilmaydi.
    """
    parts: list[str] = []
    if CHANNEL_USERNAME:
        ch = CHANNEL_USERNAME if CHANNEL_USERNAME.startswith("https") else f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}"
        parts.append(f'<a href="{ch}">📢 Kanalga obuna</a>')
    if SUPPORT_URL:
        parts.append(f'<a href="{SUPPORT_URL}">❤️ Qo\'llab-quvvatlash</a>')
    return "\n\n" + "  ·  ".join(parts) if parts else ""


# ─── Xabar formatlash ─────────────────────────────────────────────────────────

def _fmt_rate(rate: float, pair: str) -> str:
    """
    RUB_UZS : "158.00 so'm"  (1 RUB uchun qancha so'm olasiz)
    UZS_RUB : "167 so'm/RUB" (1 RUB sotib olish narxi — kamroq = yaxshi)
    """
    if pair == "UZS_RUB":
        # rate = 1/sell_price; foydalanuvchiga sell_price ko'rsatamiz
        sell = 1.0 / rate if rate > 0 else 0
        return f"{sell:,.2f} so'm/RUB".replace(",", " ")
    cfg = PAIR_DISPLAY[pair]
    val = rate * cfg["multiplier"]
    return f"{val:,.{cfg['decimals']}f} {cfg['unit']}".replace(",", " ")


def _rate_num(rate: float, pair: str) -> str:
    """Curso uslubidagi raqam: '158.00' (birliksiz)."""
    if pair == "UZS_RUB":
        val = 1.0 / rate if rate > 0 else 0      # foydalanuvchiga sell narxi
    else:
        val = rate * PAIR_DISPLAY[pair]["multiplier"]
    return f"{val:,.2f}".replace(",", " ")


def _change_tag(e: dict, pair: str) -> str:
    """
    Oxirgi yangilanishdan beri o'zgarish: ko'tarilgan 🟢▲, tushgan 🔴▼.
    Ko'rsatiladigan qiymat (kurs) bo'yicha yo'nalish. O'zgarmasa — bo'sh.
    """
    prev = e.get("prev_rate")
    rate = e.get("rate")
    if prev is None or rate is None or prev <= 0 or rate <= 0:
        return ""
    if e.get("change") not in ("up", "down"):
        return ""

    if pair == "UZS_RUB":
        now_disp, prev_disp = 1.0 / rate, 1.0 / prev   # sell narxi
    else:
        mult = PAIR_DISPLAY[pair]["multiplier"]
        now_disp, prev_disp = rate * mult, prev * mult

    delta = now_disp - prev_disp
    if abs(delta) < 0.01:
        return ""
    tag = "▲" if delta > 0 else "▼"           # ko'tarildi / tushdi (rangsiz)
    return f" {tag}{abs(delta):.2f}"


def _vs_ref(e: dict, pair: str) -> str:
    """
    Universal bank bilan farqni QAVS ichida, har tiyingacha aniq qaytaradi:
      '(-5.84)' / '(+10.00)'. Universal bankning o'zida '(asos)'.
    Rangli belgi yo'q.
    """
    if e.get("key") == "universalbank":
        return " (asos)"
    ref  = fetcher.get_reference_rate(pair)
    rate = e.get("rate")
    if not ref or ref <= 0 or not rate or rate <= 0:
        return ""
    if pair == "UZS_RUB":
        diff = (1.0 / rate) - (1.0 / ref)   # sell narxi farqi
    else:
        diff = rate - ref                    # so'm farqi
    if abs(diff) < 0.005:
        return " (0.00)"
    sign = "+" if diff > 0 else "-"
    return f" ({sign}{abs(diff):.2f})"


def build_rates_message(pair: str, is_notification: bool = False) -> str:
    """
    Curso uslubidagi toza ro'yxat:
        158.00 | 💱 Asia Alliance Bank
    Eng yaxshi kurs tepada. Tur belgisi: 🏦 rasmiy · 💳 o'tkazma · 💱 bank ilovasi.
    """
    entries = fetcher.get_cached(pair) or []
    ts      = fetcher.get_cache_time()
    ts_str  = ts.strftime("%d.%m.%Y · %H:%M") if ts else "—"

    prefix   = "⚡ <b>Kurs yangilandi!</b>\n" if is_notification else ""
    subtitle = (
        "<i>Eng ko'p so'm beruvchi tepada 👆</i>"
        if pair == "RUB_UZS" else
        "<i>Eng arzon RUB beruvchi tepada 👆</i>"
    )

    lines = [f"{prefix}{PAIR_HEADER[pair]}", subtitle, f"🕐 {ts_str}", ""]

    if not entries:
        lines.append("⏳ <i>Kurslar olinmoqda, biroz kuting...</i>")
        return "\n".join(lines)

    ok_entries = [e for e in entries if e.get("rate") is not None]
    no_entries = [e for e in entries if e.get("rate") is None]

    if not ok_entries:
        lines.append("⚠️ <i>Hozircha ma'lumot yo'q. Birozdan so'ng qayta urinib ko'ring.</i>")
        return "\n".join(lines)

    # Bir qatorda: kurs | bank nomi (Universaldan farq)  + o'zgarish
    for e in ok_entries:
        icon     = TYPE_ICON.get(e.get("type", "bank"), "💱")
        rate_str = _rate_num(e["rate"], pair)
        vs_ref   = _vs_ref(e, pair)          # (-5.84) — Universaldan farq
        change   = _change_tag(e, pair)      # ▲/▼ oxirgi o'zgarish
        lines.append(f"<b>{rate_str}</b> | {icon} {e['name']}{vs_ref}{change}")

    # Izoh (legend) — Curso uslubida
    lines.append("")
    lines.append("(  ) — Universal bankdan farq, so'm · ▲/▼ — o'zgarish")
    lines.append("💳 — kartaga/hisobga oniy o'tkazma tizimlari kursi")
    lines.append("💱 — bank ilovasidagi ayirboshlash kursi")
    lines.append("🏦 — markaziy banklar rasmiy kursi")

    # Ma'lumot yo'q banklar — faqat UZS_RUB uchun, ixcham
    if no_entries and pair != "RUB_UZS":
        names = ", ".join(e["name"] for e in no_entries[:6])
        if len(no_entries) > 6:
            names += f" va yana {len(no_entries) - 6} ta"
        lines.append(f"\n<i>ℹ️ Ma'lumot yo'q: {names}</i>")

    # Ajratuvchi chiziq + ogohlantirish (Curso uslubida)
    lines.append("")
    lines.append("--------------------------------")
    lines.append(
        "<i>O'tkazma xizmatlaridagi kurslar nashr vaqtidan o'tgan vaqt, "
        "shaxsiy takliflar, akkaunt holati, promokodlar qo'llanishi va "
        "boshqa sabablarga ko'ra farq qilishi mumkin.</i>"
    )

    return "\n".join(lines)


# ─── Xavfsiz yuborish (flood/blok himoyasi) ───────────────────────────────────

async def _safe_send(bot: Bot, chat_id: int, text: str) -> str:
    """
    Xabarni xavfsiz yuboradi. Telegram xatolarini ushlaydi:
      'ok'      — yuborildi
      'blocked' — user botni bloklagan/o'chirgan (ro'yxatdan chiqarish kerak)
      'flood'   — flood control (vaqtincha o'tkazib yuboriladi, bot qulamaydi)
      'error'   — boshqa xato
    """
    try:
        await bot.send_message(
            chat_id, text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=main_keyboard(),
        )
        return "ok"
    except TelegramRetryAfter as e:
        log.warning("Flood control (chat=%s): %ss — o'tkazib yuborildi", chat_id, e.retry_after)
        return "flood"
    except TelegramForbiddenError:
        return "blocked"
    except TelegramBadRequest as e:
        if any(k in str(e).lower() for k in ("not found", "chat not found", "deactivated")):
            return "blocked"
        log.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, e)
        return "error"
    except Exception as e:
        log.warning("Xabar yuborilmadi (chat=%s): %s", chat_id, e)
        return "error"


# ─── Bildirishnomalar ─────────────────────────────────────────────────────────

async def send_notifications(bot: Bot, pairs: set[str], is_change: bool = False) -> None:
    """Barcha foydalanuvchilarga kurs xabarini yuboradi (flood'ga chidamli)."""
    if not _users:
        return

    blocked: list[int] = []
    for pair in sorted(pairs):
        if pair not in NOTIFY_PAIRS:
            continue
        text = build_rates_message(pair, is_notification=is_change) + _support_footer()
        for user_id in list(_users):
            status = await _safe_send(bot, user_id, text)
            if status == "blocked":
                blocked.append(user_id)
            elif status == "flood":
                break  # bu juftlik bo'yicha to'xtaymiz — Telegram'ni yana bezovta qilmaymiz
            await asyncio.sleep(0.5)   # chatlar orasida xavfsiz oraliq (flood oldini olish)

    for uid in set(blocked):
        _remove_user(uid)


# Orqaga moslik uchun alias
async def send_change_notifications(bot: Bot, changed_pairs: set[str]) -> None:
    await send_notifications(bot, changed_pairs, is_change=True)


# ─── Dispatcher ───────────────────────────────────────────────────────────────

dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    _add_user(message.from_user.id)
    await message.answer(
        "Salom! 👋\n\n"
        "Men Rossiya ↔ O'zbekiston pul o'tkazmalari kurslarini real vaqtda kuzataman.\n\n"
        "Kurs o'zgarganda sizga <b>avtomatik xabar</b> keladi.\n\n"
        "Quyidagi tugmalardan birini bosing:",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "ℹ️ <b>Yordam</b>\n\n"
        "• Kurs ko'rish uchun pastdagi tugmani bosing\n"
        "• /refresh — kurslarni hoziroq yangilash\n"
        "• Kurs o'zgarganda <b>avtomatik xabar</b> keladi\n\n"
        "<b>Belgilar:</b>\n"
        "• Qavs ichidagi raqam — Universal bankdan farq (so'm)\n"
        "   (-) arzon/ko'p foydali · (+) qimmat/kam · (0.00) bir xil\n"
        "• ▲ ko'tarildi · ▼ tushdi (oxirgi yangilanishdan)\n"
        "• 🏦 markaziy bank · 💳 o'tkazma · 💱 bank ilovasi\n\n"
        "Muammo bo'lsa /start ni bosing.",
        parse_mode=ParseMode.HTML,
        reply_markup=main_keyboard(),
    )


@dp.message(Command("refresh"))
async def cmd_refresh(message: Message) -> None:
    msg = await message.answer("⏳ Kurslar yangilanmoqda...", parse_mode=ParseMode.HTML)
    try:
        await fetcher.refresh_all()
        ts = fetcher.get_cache_time()
        ts_str = ts.strftime("%H:%M:%S") if ts else "—"
        await msg.edit_text(f"✅ Yangilandi — {ts_str}")
    except Exception as e:
        log.error("Refresh xato: %s", e)
        await msg.edit_text("❌ Yangilashda xato. Keyinroq urinib ko'ring.")


@dp.message(F.text.in_(BUTTON_TO_PAIR))
async def handle_pair_button(message: Message) -> None:
    user_id = message.from_user.id

    # Throttle: tez-tez bosishdan himoya (flood control oldini oladi)
    now = datetime.now()
    last = _last_button_press.get(user_id)
    if last and (now - last).total_seconds() < BUTTON_COOLDOWN:
        return  # juda tez bosildi — e'tiborsiz qoldiramiz
    _last_button_press[user_id] = now

    pair = BUTTON_TO_PAIR[message.text]
    if fetcher.get_cached(pair) is None:
        await _safe_send(message.bot, user_id, "⏳ Kurslar olinmoqda...")
        try:
            await fetcher.refresh_all()
        except Exception:
            pass
    text = build_rates_message(pair) + _support_footer()
    await _safe_send(message.bot, user_id, text)


# ─── Fon vazifasi ─────────────────────────────────────────────────────────────

async def updater_loop(bot: Bot) -> None:
    global _last_notify_time
    log.info(
        "Kurs yangilovchi vazifa boshlandi (interval=%ds, notify_min=%ds)",
        UPDATE_INTERVAL, NOTIFY_MIN_INTERVAL,
    )
    cycle = 0
    purge_every = max(1, int(86400 / max(UPDATE_INTERVAL, 1)))  # ~ kuniga 1 marta
    while True:
        try:
            changed_pairs = await fetcher.refresh_all()
            now = datetime.now()

            # Eski tarixni tozalash (DB shishib ketmasligi uchun)
            cycle += 1
            if cycle % purge_every == 0:
                try:
                    removed = db.purge_old_history(days=30)
                    if removed:
                        log.info("Tarix tozalandi: %d eski yozuv o'chirildi", removed)
                except Exception as e:
                    log.warning("Tarix tozalashda xato: %s", e)

            if changed_pairs:
                # Faqat O'ZGARGAN juftlarni yuboramiz (ikkalasini emas — kamroq xabar)
                to_send = changed_pairs & set(NOTIFY_PAIRS)
                log.info("O'zgargan juftlar: %s — xabar yuborilyapti", to_send)
                await send_notifications(bot, to_send, is_change=True)
                _last_notify_time = now
            elif _users:
                # Kurs o'zgarmadi → NOTIFY_MIN_INTERVAL o'tgan bo'lsa rejalashtirilgan xabar
                elapsed = (now - _last_notify_time).total_seconds() if _last_notify_time else NOTIFY_MIN_INTERVAL
                if elapsed >= NOTIFY_MIN_INTERVAL:
                    log.info("Rejalashtirilgan xabar yuborilyapti (%ds o'tdi)", int(elapsed))
                    await send_notifications(bot, set(NOTIFY_PAIRS), is_change=False)
                    _last_notify_time = now

        except Exception as e:
            log.error("Yangilash xatosi: %s", e, exc_info=True)
        await asyncio.sleep(UPDATE_INTERVAL)


# ─── Ishga tushirish ──────────────────────────────────────────────────────────

async def main() -> None:
    global _last_notify_time

    db.init_db()
    _load_users_from_file()
    log.info("Foydalanuvchilar yuklandi: %d ta", len(_users))

    # Ishga tushganda darhol rejalashtirilgan xabar yubormaslik uchun
    _last_notify_time = datetime.now()

    bot = Bot(token=BOT_TOKEN)  # type: ignore[arg-type]

    log.info("Boshlang'ich kurslar olinmoqda...")
    try:
        await fetcher.refresh_all()
        log.info("Boshlang'ich kurslar muvaffaqiyatli olindi")
    except Exception as e:
        log.warning("Boshlang'ich kurs olishda xato: %s", e)

    updater = asyncio.create_task(updater_loop(bot))
    log.info("Bot polling boshlandi")
    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        # Toza to'xtatish (server qayta ishga tushganda resurslar bo'shaydi)
        updater.cancel()
        await bot.session.close()
        log.info("Bot to'xtatildi")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Foydalanuvchi to'xtatdi (Ctrl+C)")
