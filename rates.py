"""
rates.py
========
O'zbekiston banklarining RUB kurslarini parallel yig'adi va
Universal Bank bilan solishtirishni tayyorlaydi.

Valyuta yo'nalishlari:
  RUB → UZS  =  bank rublingizni SOTIB OLADI  →  rub_buy  (olish kursi)
  UZS → RUB  =  bank sizga rublni SOTADI       →  rub_sell (sotish kursi)

Strategiya (prioritet tartibda):
  1. bank.uz AGREGATOR — bitta sahifadan hamma bank kurslari (tavsiya etiladi)
  2. Individual bank scrapers — to'g'ridan-to'g'ri bank saytidan
  3. CBU rasmiy kursi — faqat reference (olish/sotish emas)
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Awaitable, Callable, Optional

import aiohttp
from bs4 import BeautifulSoup

# Pillow — PNG jadval chizish uchun (tavsiya etiladi)
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

log = logging.getLogger(__name__)

# Har HTTP so'rov uchun maksimal kutish vaqti (soniya)
TIMEOUT = aiohttp.ClientTimeout(total=15)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8",
}

# lxml bo'lsa tezroq ishlaydi, bo'lmasa standart parser ishlatiladi
try:
    import lxml  # noqa: F401
    BS4_PARSER = "lxml"
except ImportError:
    BS4_PARSER = "html.parser"

BASE_BANK_KEY = "universal"  # Solishtirish bazasi — Universal Bank


# ---------------------------------------------------------------------------
# MA'LUMOT MODELI
# ---------------------------------------------------------------------------

@dataclass
class Rate:
    """Bitta bankning bir vaqtdagi RUB kurslari."""
    key: str
    name: str
    rub_buy: Optional[float] = None    # RUB → UZS (bank sotib oladi)
    rub_sell: Optional[float] = None   # UZS → RUB (bank sotadi)
    ok: bool = False
    note: str = ""


# Kesh: oxirgi muvaffaqiyatli natijani saqlaydi (bot qayta so'raganda tez javob beradi)
_cache: dict[str, Rate] = {}
_cache_official: Optional[float] = None
_cache_time: Optional[datetime] = None
CACHE_TTL_SECONDS = 1800  # 30 daqiqa


# ---------------------------------------------------------------------------
# BANKLAR RO'YXATI
# ---------------------------------------------------------------------------
BANKS: dict[str, dict] = {
    "universal":   {"name": "Universal Bank",          "rates_page": "https://universalbank.uz/uz/exchange"},
    "nbu":         {"name": "Milliy bank (NBU)",       "rates_page": "https://nbu.uz/exchange-rates/"},
    "agrobank":    {"name": "Agrobank",                "rates_page": "https://agrobank.uz/page/exchange-rates"},
    "sqb":         {"name": "Sanoatqurilish banki",    "rates_page": "https://sqb.uz/uz/services/exchange-rates/"},
    "xalq":        {"name": "Xalq banki",              "rates_page": "https://xb.uz/uz/exchange-rates"},
    "ipoteka":     {"name": "Ipoteka-bank",            "rates_page": "https://ipotekabank.uz/uz/currency/"},
    "asaka":       {"name": "Asakabank",               "rates_page": "https://asakabank.uz/uz/exchange-rates"},
    "mkbank":      {"name": "Mikrokreditbank",         "rates_page": "https://mkbank.uz/uz/exchange-rates/"},
    "aloqabank":   {"name": "Aloqabank",               "rates_page": "https://aloqabank.uz/uz/services/exchange-rates/"},
    "turonbank":   {"name": "Turonbank",               "rates_page": "https://turonbank.uz/uz/exchange-rates/"},
    "brb":         {"name": "BRB Bank",                "rates_page": "https://brb.uz/uz/exchange-rates"},
    "kapitalbank": {"name": "Kapitalbank",             "rates_page": "https://kapitalbank.uz/uz/services/exchange-rates/"},
    "hamkorbank":  {"name": "Hamkorbank",              "rates_page": "https://hamkorbank.uz/uz/exchange-rates/"},
    "ipakyuli":    {"name": "Ipak Yo'li Bank",         "rates_page": "https://ipakyulibank.uz/uz/exchange"},
    "ofb":         {"name": "Orient Finans Bank",      "rates_page": "https://ofb.uz/uz/exchange-rates"},
    "aab":         {"name": "Asia Alliance Bank",      "rates_page": "https://aab.uz/uz/exchange-rates/"},
    "trastbank":   {"name": "Trastbank",               "rates_page": "https://trustbank.uz/uz/services/exchange-rates/"},
    "infinbank":   {"name": "Infinbank",               "rates_page": "https://infinbank.com/uz/private/exchange-rates/"},
    "davrbank":    {"name": "Davr Bank",               "rates_page": "https://davrbank.uz/uz/exchange-rates"},
    "anorbank":    {"name": "Anor Bank",               "rates_page": "https://anorbank.uz/uz/about/exchange-rates/"},
    "apexbank":    {"name": "Apex Bank",               "rates_page": "https://apexbank.uz/uz/exchange-rates"},
    "kdb":         {"name": "KDB Bank Uzbekiston",     "rates_page": "https://kdb.uz/uz/private/exchange-rates"},
    "garantbank":  {"name": "Garant Bank",             "rates_page": "https://garantbank.uz/uz/exchange-rates"},
    "hayotbank":   {"name": "Hayot Bank",              "rates_page": "https://hayotbank.uz/uz/exchange-rates"},
    "poytaxt":     {"name": "Poytaxt Bank",            "rates_page": "https://poytaxtbank.uz/uz/exchange-rates"},
    "octobank":    {"name": "Octobank",                "rates_page": "https://octobank.uz/uz/exchange-rates"},
    "ylo":         {"name": "Yo'l Bank",               "rates_page": "https://ylobank.uz/uz/exchange-rates"},
}


# ---------------------------------------------------------------------------
# YORDAMCHI: RAQAMLARNI AJRATISH
# ---------------------------------------------------------------------------

def _extract_numbers(texts: list[str]) -> list[float]:
    """
    Matn ro'yxatidan valyuta kurs raqamlarini topadi.
    '166,50' / '166.50' / '1 666.50' shakllarini qabul qiladi.
    """
    out: list[float] = []
    for text in texts:
        clean = text.replace("\xa0", " ").replace(" ", " ")
        for m in re.findall(r"\b\d[\d\s]*[.,]\d{2}\b", clean):
            s = re.sub(r"\s+", "", m).replace(",", ".")
            try:
                v = float(s)
                if v > 10:  # kichik raqamlar (tartib raqami, %)ni o'tkazib yuboramiz
                    out.append(v)
            except ValueError:
                pass
    return out


def _rub_row_in_table(soup: BeautifulSoup) -> Optional[list[float]]:
    """HTML jadvaldan RUB qatorini topadi va [buy, sell] qaytaradi."""
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        if re.search(r"\bRUB\b|Россий|рубл|Рубл", text, re.IGNORECASE):
            nums = _extract_numbers([text])
            if len(nums) >= 2:
                return nums[:2]
    return None


# ---------------------------------------------------------------------------
# MARKAZIY BANK — RASMIY KURS (reference, bitta qiymat)
# ---------------------------------------------------------------------------

async def cbu_official_rub(session: aiohttp.ClientSession) -> Optional[float]:
    """
    CBU rasmiy RUB kursini qaytaradi (1 RUB = ? so'm).
    Bu olish/sotish EMAS — faqat reference uchun.
    Kafolatlangan JSON API, o'zgarmaydi.
    """
    url = "https://cbu.uz/uz/arkhiv-kursov-valyut/json/RUB/"
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
            data = await r.json(content_type=None)
            if data and isinstance(data, list):
                return float(data[0]["Rate"])
    except Exception as e:
        log.warning("CBU kurs xato: %s", e)
    return None


# ---------------------------------------------------------------------------
# AGREGATOR: bank.uz — barcha banklarni bir yerda olish
# ---------------------------------------------------------------------------

# bank.uz saytida banklar nomlarini bizning kalitlarga moslaymiz
_BANKUZ_NAME_MAP: dict[str, str] = {
    "universal":          "universal",
    "milliy":             "nbu",
    "nbu":                "nbu",
    "agrо":               "agrobank",
    "agrobank":           "agrobank",
    "sanoatqurilish":     "sqb",
    "sqb":                "sqb",
    "xalq":               "xalq",
    "ipoteka":            "ipoteka",
    "asaka":              "asaka",
    "mikrokreditbank":    "mkbank",
    "aloqa":              "aloqabank",
    "turon":              "turonbank",
    "brb":                "brb",
    "kapital":            "kapitalbank",
    "hamkor":             "hamkorbank",
    "ipak":               "ipakyuli",
    "orient":             "ofb",
    "asia alliance":      "aab",
    "trast":              "trastbank",
    "infin":              "infinbank",
    "davr":               "davrbank",
    "anor":               "anorbank",
    "apex":               "apexbank",
    "kdb":                "kdb",
    "garant":             "garantbank",
    "hayot":              "hayotbank",
    "poytaxt":            "poytaxt",
    "octo":               "octobank",
    "octobank":           "octobank",
}


def _match_bank_name(raw_name: str) -> Optional[str]:
    """Agregator saytdagi bank nomini bizning kalitga aylantiradi."""
    lower = raw_name.lower().strip()
    for keyword, key in _BANKUZ_NAME_MAP.items():
        if keyword in lower:
            return key
    return None


async def scrape_bankuz_aggregate(
    session: aiohttp.ClientSession,
) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """
    bank.uz saytidan barcha banklarning RUB kurslarini yig'adi.
    Muvaffaqiyatli bo'lsa, individual scraperlarni ishga tushirmaydi.
    """
    urls = [
        "https://bank.uz/uz/valyuta-kurslari",
        "https://bank.uz/rates",
        "https://bank.uz/exchange-rates",
    ]
    for url in urls:
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    continue
                html = await r.text()
            soup = BeautifulSoup(html, BS4_PARSER)

            if "application/json" in r.content_type:
                data = json.loads(html)
                log.info("bank.uz JSON javob qaytardi")
                return {}

            result: dict[str, tuple[Optional[float], Optional[float]]] = {}

            for row in soup.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells:
                    continue
                row_text = " ".join(cells)
                if not re.search(r"\bRUB\b|рубл|рубль", row_text, re.IGNORECASE):
                    continue
                bank_raw = cells[0] if cells else ""
                bank_key = _match_bank_name(bank_raw)
                if not bank_key:
                    continue
                nums = _extract_numbers(cells[1:])
                if len(nums) >= 2:
                    result[bank_key] = (nums[0], nums[1])

            if result:
                log.info("bank.uz dan %d bank kursi olindi", len(result))
                return result

        except Exception as e:
            log.debug("bank.uz %s: %s", url, e)

    log.info("bank.uz agregatori ishlamadi — individual scraperlar ishlatiladi")
    return {}


async def scrape_kursuz_aggregate(
    session: aiohttp.ClientSession,
) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """
    kurs.uz saytidan barcha banklarning RUB kurslarini yig'adi.
    """
    urls = [
        "https://kurs.uz/",
        "https://kurs.uz/ru/",
        "https://kurs.uz/exchange/RUB",
        "https://kurs.uz/ru/exchange/RUB",
    ]
    for url in urls:
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    continue
                html = await r.text()
            soup = BeautifulSoup(html, BS4_PARSER)

            result: dict[str, tuple[Optional[float], Optional[float]]] = {}
            for row in soup.find_all("tr"):
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if not cells:
                    continue
                row_text = " ".join(cells)
                if not re.search(r"\bRUB\b|рубл|рубль", row_text, re.IGNORECASE):
                    continue
                bank_key = _match_bank_name(cells[0])
                if not bank_key:
                    continue
                nums = _extract_numbers(cells[1:])
                if len(nums) >= 2:
                    result[bank_key] = (nums[0], nums[1])

            if result:
                log.info("kurs.uz dan %d bank kursi olindi", len(result))
                return result
        except Exception as e:
            log.debug("kurs.uz %s: %s", url, e)

    return {}


# ---------------------------------------------------------------------------
# INDIVIDUAL BANK SCRAPERLAR
# ---------------------------------------------------------------------------

async def scrape_universal(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """
    Universal Bank — solishtirish BAZASI, shuning uchun alohida scraper.
    Avval JSON API, keyin HTML jadval, keyin script ichidagi JSON sinab ko'radi.
    """
    api_urls = [
        "https://universalbank.uz/api/currency/rates",
        "https://universalbank.uz/api/rates",
        "https://universalbank.uz/currency/rates.json",
        "https://universalbank.uz/api/v1/currency/rates",
        "https://universalbank.uz/api/exchange-rates",
        "https://universalbank.uz/api/v2/currency",
        "https://universalbank.uz/uz/api/rates",
    ]
    for api_url in api_urls:
        try:
            async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status == 200 and "json" in r.content_type:
                    data = await r.json(content_type=None)
                    if isinstance(data, list):
                        for item in data:
                            ccy = str(item.get("ccy", item.get("currency", ""))).upper()
                            if "RUB" in ccy:
                                buy = _safe_float(item.get("buy") or item.get("purchase"))
                                sell = _safe_float(item.get("sell") or item.get("sale"))
                                return buy, sell
        except Exception:
            pass

    try:
        async with session.get(cfg["rates_page"], headers=HEADERS, timeout=TIMEOUT) as r:
            html = await r.text()
        soup = BeautifulSoup(html, BS4_PARSER)
        nums = _rub_row_in_table(soup)
        if nums:
            return nums[0], nums[1]

        for script in soup.find_all("script"):
            src = script.string or ""
            if "RUB" in src.upper():
                matches = re.findall(
                    r'"RUB"[^}]*"buy"\s*:\s*"?([\d.,]+)"?[^}]*"sell"\s*:\s*"?([\d.,]+)"?',
                    src, re.IGNORECASE
                )
                if not matches:
                    matches = re.findall(
                        r'"purchase"\s*:\s*"?([\d.,]+)"?[^}]*"sale"\s*:\s*"?([\d.,]+)"?',
                        src
                    )
                if matches:
                    b, s = matches[0]
                    return _safe_float(b), _safe_float(s)
    except Exception as e:
        log.warning("Universal Bank scraping xato: %s", e)

    return None, None


async def scrape_kapitalbank(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """Kapitalbank — JSON API sinab ko'radi, bo'lmasa umumiy scraper."""
    api_endpoints = [
        "https://kapitalbank.uz/api/exchange-rates",
        "https://kapitalbank.uz/api/currency",
        "https://kapitalbank.uz/exchange-rates.json",
    ]
    for url in api_endpoints:
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status == 200 and "json" in r.content_type:
                    data = await r.json(content_type=None)
                    pair = _find_rub_in_json(data)
                    if pair:
                        return pair
        except Exception:
            pass

    return await scrape_generic_json_then_html(session, cfg)


async def scrape_hamkorbank(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """Hamkorbank — JSON API sinab ko'radi, bo'lmasa umumiy scraper."""
    try:
        async with session.get(
            "https://hamkorbank.uz/api/v1/currency/rates", headers=HEADERS, timeout=TIMEOUT
        ) as r:
            if r.status == 200 and "json" in r.content_type:
                data = await r.json(content_type=None)
                pair = _find_rub_in_json(data)
                if pair:
                    return pair
    except Exception:
        pass

    return await scrape_generic_json_then_html(session, cfg)


async def scrape_nbu(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """Milliy bank (NBU) — nbu.uz saytida HTML jadval bo'ladi."""
    return await scrape_generic_html(session, cfg)


async def scrape_generic_html(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """
    Umumiy HTML jadval scraper. Ko'p banklar RUB qatorini oddiy <table> ichida
    beradi: valyuta nomi | olish | sotish.
    """
    try:
        async with session.get(cfg["rates_page"], headers=HEADERS, timeout=TIMEOUT) as r:
            html = await r.text()
        soup = BeautifulSoup(html, BS4_PARSER)
        nums = _rub_row_in_table(soup)
        if nums:
            return nums[0], nums[1]
    except Exception as e:
        log.debug("%s HTML scraping: %s", cfg["name"], e)
    return None, None


async def scrape_generic_json_then_html(
    session: aiohttp.ClientSession, cfg: dict
) -> tuple[Optional[float], Optional[float]]:
    """
    Avval HTML sahifadagi inline JSON'ni sinab ko'radi,
    keyin HTML jadval parsing'ga tushadi.
    """
    try:
        async with session.get(cfg["rates_page"], headers=HEADERS, timeout=TIMEOUT) as r:
            html = await r.text()
        soup = BeautifulSoup(html, BS4_PARSER)

        for script in soup.find_all("script"):
            src = script.string or ""
            if "RUB" in src.upper():
                data_match = re.search(r'(\[.*?\]|\{.*?\})', src, re.DOTALL)
                if data_match:
                    try:
                        data = json.loads(data_match.group(1))
                        pair = _find_rub_in_json(data)
                        if pair:
                            return pair
                    except (json.JSONDecodeError, ValueError):
                        pass

        nums = _rub_row_in_table(soup)
        if nums:
            return nums[0], nums[1]

    except Exception as e:
        log.debug("%s scraping: %s", cfg["name"], e)

    return None, None


def _find_rub_in_json(data: object) -> Optional[tuple[float, float]]:
    """
    JSON (ro'yxat yoki lug'at) ichidan RUB buy/sell juftligini topadi.
    Ko'p formatlarni qo'llaydi.
    """
    if isinstance(data, list):
        for item in data:
            result = _find_rub_in_json(item)
            if result:
                return result
    if isinstance(data, dict):
        ccy = str(data.get("ccy") or data.get("currency") or data.get("code") or "").upper()
        if "RUB" in ccy:
            buy = _safe_float(
                data.get("buy") or data.get("purchase") or data.get("buyRate")
            )
            sell = _safe_float(
                data.get("sell") or data.get("sale") or data.get("sellRate")
            )
            if buy and sell:
                return buy, sell
        for v in data.values():
            result = _find_rub_in_json(v)
            if result:
                return result
    return None


def _safe_float(v: object) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# SCRAPER REGISTRI
# ---------------------------------------------------------------------------
ScraperFn = Callable[
    [aiohttp.ClientSession, dict],
    Awaitable[tuple[Optional[float], Optional[float]]]
]

SCRAPERS: dict[str, ScraperFn] = {
    "universal":   scrape_universal,
    "nbu":         scrape_nbu,
    "kapitalbank": scrape_kapitalbank,
    "hamkorbank":  scrape_hamkorbank,
}


# ---------------------------------------------------------------------------
# BITTA BANK YIGISH
# ---------------------------------------------------------------------------

async def fetch_one(session: aiohttp.ClientSession, key: str, cfg: dict) -> Rate:
    rate = Rate(key=key, name=cfg["name"])
    scraper = SCRAPERS.get(key, scrape_generic_json_then_html)
    try:
        buy, sell = await scraper(session, cfg)
        rate.rub_buy, rate.rub_sell = buy, sell
        rate.ok = buy is not None or sell is not None
        if not rate.ok:
            rate.note = "RUB topilmadi"
    except Exception as e:
        rate.note = f"xato: {type(e).__name__}: {e}"
        log.warning("%s: kutilmagan xato: %s", cfg["name"], e)
    return rate


# ---------------------------------------------------------------------------
# BARCHA BANKLARNI PARALLEL YIG'ISH
# ---------------------------------------------------------------------------

async def fetch_all(force: bool = False) -> tuple[dict[str, Rate], Optional[float]]:
    """
    Barcha banklarni parallel yig'adi.
    force=False bo'lsa kesh qaytaradi (CACHE_TTL_SECONDS ichida bo'lsa).
    """
    global _cache, _cache_official, _cache_time

    if not force and _cache and _cache_time:
        age = (datetime.now() - _cache_time).total_seconds()
        if age < CACHE_TTL_SECONDS:
            log.info("Keshdan qaytarildi (yoshi: %.0f sek)", age)
            return dict(_cache), _cache_official

    async with aiohttp.ClientSession() as session:
        cbu_task = asyncio.create_task(cbu_official_rub(session))

        # Avval bank.uz, keyin kurs.uz sinab ko'ramiz
        aggregate = await scrape_bankuz_aggregate(session)
        if not aggregate:
            aggregate = await scrape_kursuz_aggregate(session)

        rates: dict[str, Rate] = {}
        for key, cfg in BANKS.items():
            if key in aggregate:
                buy, sell = aggregate[key]
                r = Rate(key=key, name=cfg["name"], rub_buy=buy, rub_sell=sell)
                r.ok = buy is not None or sell is not None
                rates[key] = r

        missing_keys = [k for k in BANKS if k not in rates]
        if missing_keys:
            tasks = [fetch_one(session, k, BANKS[k]) for k in missing_keys]
            individual = await asyncio.gather(*tasks)
            for r in individual:
                rates[r.key] = r

        official = await cbu_task

    _cache = dict(rates)
    _cache_official = official
    _cache_time = datetime.now()

    ok_count = sum(1 for r in rates.values() if r.ok)
    log.info("Kurslar yangilandi: %d/%d bank muvaffaqiyatli", ok_count, len(rates))
    return rates, official


# ---------------------------------------------------------------------------
# RAQAM FORMATLASH YORDAMCHILARI
# ---------------------------------------------------------------------------

def _fmt(v: Optional[float]) -> str:
    """Raqamni '166 50' ko'rinishida formatlaydi."""
    return f"{v:,.2f}".replace(",", " ") if isinstance(v, (int, float)) else "—"


# ---------------------------------------------------------------------------
# MATNLI HISOBOT (Telegram HTML + emoji)
# ---------------------------------------------------------------------------

def build_text_fallback(
    rates: dict[str, Rate],
    official: Optional[float],
    updated_at: Optional[datetime] = None,
) -> str:
    """
    Matnli hisobot — Telegram HTML parse_mode uchun.
    🟢 musbat farq, 🔴 manfiy farq emoji bilan ko'rsatadi.
    """
    base = rates.get(BASE_BANK_KEY)
    b_buy  = base.rub_buy  if (base and base.ok) else None
    b_sell = base.rub_sell if (base and base.ok) else None
    ts = (updated_at or _cache_time or datetime.now()).strftime("%d.%m.%Y %H:%M")

    lines: list[str] = [
        "💱 <b>RUB kurslari — Universal Bank bilan solishtirma</b>",
        f"🕐 Yangilandi: {ts}",
    ]

    if official is not None:
        lines.append(f"🏛 CBU rasmiy: <b>{official:.2f}</b> so'm / 1 RUB (ma'lumot uchun)")

    lines.append("")

    if not base or not base.ok:
        lines.append(
            "⚠️ <b>Universal Bank kursi olinmadi</b>\n"
            "   Scraper selektorini tekshiring (universalbank.uz/uz/exchange)"
        )
    else:
        lines += [
            "📌 <b>Universal Bank (baza kurs)</b>",
            f"   RUB→UZS (olish):  <b>{_fmt(b_buy)}</b>",
            f"   UZS→RUB (sotish): <b>{_fmt(b_sell)}</b>",
            "",
            "<i>🟢 = Universaldan yuqori  |  🔴 = pastroq</i>",
            "─" * 34,
        ]

        sorted_rows = sorted(
            [(k, r) for k, r in rates.items() if k != BASE_BANK_KEY and r.ok],
            key=lambda x: x[1].name,
        )

        for _, r in sorted_rows:
            buy_d  = _diff_emoji(r.rub_buy,  b_buy)
            sell_d = _diff_emoji(r.rub_sell, b_sell)
            lines += [
                f"🏦 <b>{r.name}</b>",
                f"   Olish ({_fmt(r.rub_buy)}):  {buy_d}",
                f"   Sotish ({_fmt(r.rub_sell)}): {sell_d}",
            ]

        lines.append("─" * 34)

        # Eng yaxshi takliflar
        ok_all = [r for r in rates.values() if r.ok]
        best_buy  = max(ok_all, key=lambda r: r.rub_buy  or 0,            default=None)
        best_sell = min(ok_all, key=lambda r: r.rub_sell or float("inf"), default=None)
        if best_buy  and best_buy.rub_buy:
            lines.append(f"✅ RUB→UZS eng yuqori: <b>{best_buy.name}</b> ({_fmt(best_buy.rub_buy)})")
        if best_sell and best_sell.rub_sell:
            lines.append(f"✅ UZS→RUB eng qulay:  <b>{best_sell.name}</b> ({_fmt(best_sell.rub_sell)})")

    failed = [r.name for r in rates.values() if not r.ok]
    if failed:
        shown = ", ".join(failed[:8]) + ("…" if len(failed) > 8 else "")
        lines.append(f"\nℹ️ Kurs olinmadi ({len(failed)}): {shown}")

    return "\n".join(lines)


def _diff_emoji(val: Optional[float], base: Optional[float]) -> str:
    """Farqni 🟢/🔴 emoji bilan qaytaradi."""
    if not isinstance(val, (int, float)) or not isinstance(base, (int, float)):
        return "—"
    d = val - base
    if abs(d) < 0.005:
        return "≈ 0"
    return f"🟢 +{d:.2f}" if d > 0 else f"🔴 {d:.2f}"


# ---------------------------------------------------------------------------
# PNG JADVAL HISOBOT (Pillow)
# ---------------------------------------------------------------------------

# Jadval ranglari
_C_HDR_BG     = "#1565C0"   # Sarlavha foni (to'q ko'k)
_C_TBL_HDR    = "#1976D2"   # Ustun sarlavhasi foni
_C_ROW_ODD    = "#FFFFFF"   # Toq qatorlar
_C_ROW_EVEN   = "#F0F4FF"   # Juft qatorlar (ochiq ko'k)
_C_POSITIVE   = "#43A047"   # Musbat farq (yashil)
_C_NEGATIVE   = "#E53935"   # Manfiy farq (qizil)
_C_NEUTRAL    = "#757575"   # Nol yoki ma'lumot yo'q (kulrang)
_C_TEXT_LIGHT = "#FFFFFF"   # Ochiq fonn ustidagi matn
_C_TEXT_DARK  = "#212121"   # To'q fon ustidagi matn
_C_BORDER     = "#CFD8DC"   # Jadval ustun chegarasi


def _load_font(size: int, bold: bool = False) -> "ImageFont.FreeTypeFont | ImageFont.ImageFont":
    """Tizimdan TrueType shrift yuklaydi; topilmasa standart bitmap shrift."""
    if bold:
        candidates = [
            "arialbd.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    else:
        candidates = [
            "arial.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
        ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError, AttributeError):
            pass
    return ImageFont.load_default()


def _draw_diff_cell(
    draw: "ImageDraw.ImageDraw",
    x: int, y: int,
    val: Optional[float],
    base: Optional[float],
    font: "ImageFont.ImageFont",
) -> None:
    """Farq yoki haqiqiy qiymatni tegishli rangda chizadi."""
    if not isinstance(val, (int, float)):
        draw.text((x, y), "  —", font=font, fill=_C_NEUTRAL)
        return
    if not isinstance(base, (int, float)):
        # Base kurs yo'q — haqiqiy qiymatni ko'rsatamiz
        draw.text((x, y), f"  {val:,.2f}".replace(",", " "), font=font, fill=_C_TEXT_DARK)
        return
    d = val - base
    if abs(d) < 0.005:
        draw.text((x, y), "  = 0", font=font, fill=_C_NEUTRAL)
    elif d > 0:
        draw.text((x, y), f"  +{d:.2f}", font=font, fill=_C_POSITIVE)
    else:
        draw.text((x, y), f"  {d:.2f}", font=font, fill=_C_NEGATIVE)


def build_image(
    rates: dict[str, Rate],
    official: Optional[float],
    updated_at: Optional[datetime] = None,
) -> Optional[bytes]:
    """
    Kurslar jadvalini Pillow bilan PNG sifatida chizadi.
    Pillow o'rnatilmagan bo'lsa None qaytaradi.

    Ustunlar: Bank nomi | RUB→UZS farq (olish) | UZS→RUB farq (sotish)
    Ranglar:  Manfiy = qizil #E53935 | Musbat = yashil #43A047 | Nol = kulrang
    """
    if not PIL_AVAILABLE:
        return None

    base   = rates.get(BASE_BANK_KEY)
    b_buy  = base.rub_buy  if (base and base.ok) else None
    b_sell = base.rub_sell if (base and base.ok) else None
    ts = (updated_at or _cache_time or datetime.now()).strftime("%d.%m.%Y %H:%M")

    # Universal Bank-dan tashqari, faqat muvaffaqiyatli banklar, nomi bo'yicha tartiblangan
    rows = sorted(
        [(key, r) for key, r in rates.items() if key != BASE_BANK_KEY and r.ok],
        key=lambda x: x[1].name,
    )

    # Shriftlar
    fn  = _load_font(13, bold=False)   # Oddiy matn
    fb  = _load_font(13, bold=True)    # Qalin matn (farq raqamlari)
    fh  = _load_font(15, bold=True)    # Katta sarlavha
    fs  = _load_font(11, bold=False)   # Kichik matn (izoh)

    # Layout
    PAD    = 12
    COL_W  = [224, 138, 138]   # [Bank nomi, Olish farq, Sotish farq]
    ROW_H  = 30
    HDR_H  = 116  # Sarlavha bo'limi balandligi
    TH_H   = 44   # Jadval ustun sarlavhasi balandligi
    FOOT_H = 22   # Pastki izoh bo'limi

    W = PAD * 2 + sum(COL_W)
    H = HDR_H + TH_H + max(len(rows), 1) * ROW_H + FOOT_H

    img  = Image.new("RGB", (W, H), color=_C_ROW_ODD)
    draw = ImageDraw.Draw(img)

    # ── SARLAVHA BO'LIMI ───────────────────────────────────────────
    draw.rectangle([0, 0, W, HDR_H], fill=_C_HDR_BG)

    y = PAD
    draw.text((PAD, y), "RUB KURSLARI", font=fh, fill=_C_TEXT_LIGHT)
    y += 22
    draw.text((PAD, y), "Universal Bank bilan solishtirma", font=fn, fill="#BBDEFB")
    y += 20
    draw.text((PAD, y), f"Yangilandi: {ts}", font=fs, fill="#90CAF9")
    y += 18

    if b_buy is not None or b_sell is not None:
        buy_s  = _fmt(b_buy)
        sell_s = _fmt(b_sell)
        draw.text(
            (PAD, y),
            f"Universal Bank:  Olish {buy_s}  |  Sotish {sell_s}",
            font=fb, fill="#FFFFFF",
        )
    else:
        draw.text((PAD, y), "Universal Bank kursi mavjud emas!", font=fb, fill="#FFEB3B")
    y += 20

    if official is not None:
        draw.text(
            (PAD, y),
            f"CBU rasmiy: {official:.2f} som/RUB  (faqat ma'lumot uchun)",
            font=fs, fill="#90CAF9",
        )

    # ── JADVAL USTUN SARLAVHALARI ─────────────────────────────────
    ty = HDR_H
    draw.rectangle([0, ty, W, ty + TH_H], fill=_C_TBL_HDR)

    if b_buy is not None or b_sell is not None:
        col_headers = [
            "Bank nomi",
            "RUB→UZS farq\n(olish kursi)",
            "UZS→RUB farq\n(sotish kursi)",
        ]
    else:
        col_headers = [
            "Bank nomi",
            "RUB→UZS\n(olish kursi)",
            "UZS→RUB\n(sotish kursi)",
        ]
    cx = PAD
    for label, cw in zip(col_headers, COL_W):
        lines_h = label.split("\n")
        line_h  = 15
        start_y = ty + (TH_H - len(lines_h) * line_h) // 2
        for li, line in enumerate(lines_h):
            draw.text((cx + 4, start_y + li * line_h), line, font=fb, fill=_C_TEXT_LIGHT)
        cx += cw

    # ── MA'LUMOT QATORLARI ────────────────────────────────────────
    if not rows:
        ry = HDR_H + TH_H
        draw.text((PAD + 4, ry + 8), "Hech qaysi bankdan ma'lumot olinmadi.", font=fn, fill=_C_NEUTRAL)
    else:
        for ri, (_, r) in enumerate(rows):
            ry = HDR_H + TH_H + ri * ROW_H
            bg = _C_ROW_ODD if ri % 2 == 0 else _C_ROW_EVEN
            draw.rectangle([0, ry, W, ry + ROW_H - 1], fill=bg)

            # Vertikal chegaralar
            sep1 = PAD + COL_W[0]
            sep2 = sep1 + COL_W[1]
            draw.line([sep1, ry, sep1, ry + ROW_H], fill=_C_BORDER, width=1)
            draw.line([sep2, ry, sep2, ry + ROW_H], fill=_C_BORDER, width=1)

            text_y = ry + (ROW_H - 14) // 2

            # Bank nomi (uzun bo'lsa kesiladi)
            name = (r.name[:27] + "…") if len(r.name) > 28 else r.name
            draw.text((PAD + 4, text_y), name, font=fn, fill=_C_TEXT_DARK)

            # Olish farqi (RUB→UZS)
            _draw_diff_cell(draw, sep1 + 2, text_y, r.rub_buy, b_buy, fb)

            # Sotish farqi (UZS→RUB)
            _draw_diff_cell(draw, sep2 + 2, text_y, r.rub_sell, b_sell, fb)

    # ── PASTKI IZOH ───────────────────────────────────────────────
    fy = H - FOOT_H
    draw.rectangle([0, fy, W, H], fill=_C_HDR_BG)
    ok_n = len(rows)
    if b_buy is not None or b_sell is not None:
        footer_text = f"{ok_n} ta bank  |  yashil = Universaldan yuqori  |  qizil = pastroq"
    else:
        footer_text = f"{ok_n} ta bank  |  kurslar (so'm / 1 RUB)"
    draw.text((PAD, fy + 5), footer_text, font=fs, fill="#90CAF9")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# build_report — orqaga moslik uchun saqlangan
# ---------------------------------------------------------------------------

def build_report(
    rates: dict[str, Rate],
    official: Optional[float],
    updated_at: Optional[datetime] = None,
) -> str:
    """Matnli hisobot (build_text_fallback-ning taxallusi)."""
    return build_text_fallback(rates, official, updated_at)


# ---------------------------------------------------------------------------
# TERMINAL SINOVI: python rates.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    async def _demo() -> None:
        rates, official = await fetch_all(force=True)
        text = build_text_fallback(rates, official)
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()

        img = build_image(rates, official)
        if img:
            with open("rates_preview.png", "wb") as f:
                f.write(img)
            print("Rasm saqlandi: rates_preview.png")
        else:
            print("Pillow topilmadi — rasm yaratilmadi")

        ok_banks = [r.name for r in rates.values() if r.ok]
        fail_banks = [r.name for r in rates.values() if not r.ok]
        print(f"\nMuvaffaqiyatli ({len(ok_banks)}): {', '.join(ok_banks)}")
        print(f"Muvaffaqiyatsiz ({len(fail_banks)}): {', '.join(fail_banks[:10])}")

    asyncio.run(_demo())
