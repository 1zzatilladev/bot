"""
fetcher.py
==========
Barcha manbalardan kurs ma'lumotlarini yig'adi va keshda saqlaydi.

Juft nomlanishi (pair):
  RUB_UZS — 1 RUB = X UZS   (yuqori = yaxshi RUB yuboruvchi uchun)
  UZS_RUB — 1 UZS = X RUB   (DB da saqlanadi, ko'rsatishda ×1000)
  RUB_USD — 1 RUB = X USD   (ko'rsatishda ×1000)
  USD_RUB — 1 USD = X RUB   (yuqori = yaxshi USD yuboruvchi uchun)

Barcha juftlar uchun: rate = qabul qiluvchi valyuta (1 birlik yuborilganda)
Sort: kamayish bo'yicha (yuqori rate = ko'proq olish = yaxshi)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Optional

import os

import aiohttp
from bs4 import BeautifulSoup

import db

log = logging.getLogger(__name__)

SOURCES_FILE = Path(__file__).parent / "sources.json"
TIMEOUT = aiohttp.ClientTimeout(total=int(os.environ.get("FETCH_TIMEOUT", "5")))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*",
    "Accept-Language": "ru,uz;q=0.9,en;q=0.8",
}

try:
    import lxml  # noqa: F401
    _PARSER = "lxml"
except ImportError:
    _PARSER = "html.parser"

# bank.uz saytidagi bank nomlarini key'ga moslashtirish
_BANK_KEY_MAP: list[tuple[str, str]] = [
    ("asia alliance",             "aab"),
    ("kapital",                   "kapitalbank"),
    ("octobank",                  "octobank"),
    ("ravnaq",                    "octobank"),
    ("octo",                      "octobank"),
    ("milliy",                    "nbu"),
    ("national bank",             "nbu"),
    ("nbu",                       "nbu"),
    ("o'zbekiston milliy",        "nbu"),
    ("ozbekiston milliy",         "nbu"),
    ("ipak",                      "ipakyuli"),
    ("agrobank",                  "agrobank"),
    ("agro",                      "agrobank"),
    ("hamkor",                    "hamkorbank"),
    ("anorbank",                  "anorbank"),
    ("anor bank",                 "anorbank"),
    ("anor",                      "anorbank"),
    ("sqb",                       "sqb"),
    ("sanoatqurilish",            "sqb"),
    ("o'zsanoatqurilish",         "sqb"),
    ("biznesni rivojlantirish",   "brb"),   # themoney.uz nomi
    ("infinbank",                 "infinbank"),
    ("infin bank",                "infinbank"),
    ("invest-finance",            "infinbank"),
    ("infin",                     "infinbank"),
    ("trustbank",                 "trustbank"),
    ("trastbank",                 "trustbank"),
    ("trast",                     "trustbank"),
    ("orient",                    "ofb"),
    ("asaka",                     "asakabank"),
    ("ipoteka",                   "ipotekabank"),
    ("aloqa",                     "aloqabank"),
    ("turon",                     "turonbank"),
    ("universal",                 "universalbank"),
    ("poytaxt",                   "poytaxtbank"),
    ("garant",                    "garantbank"),
    ("savdogar",                  "garantbank"),
    ("hayot",                     "hayotbank"),
    ("tenge",                     "tengebank"),
    ("mkbank",                    "mkbank"),
    ("mikrokreditbank",           "mkbank"),
    ("madad",                     "madadbank"),  # themoney.uz nomi
    ("brb",                       "brb"),
    ("qishloq qurilish",          "brb"),
    ("xalq",                      "xalqbank"),
    ("avosend",                   "avosend"),
    ("yubor",                     "yubor"),
    ("tinkoff",                   "tbank"),
    ("t-bank",                    "tbank"),
    ("davr",                      "davrbank"),
    ("kdb",                       "kdb"),
    ("apex",                      "apexbank"),
    ("unired",                    "unired"),
    ("mpay",                      "mpay"),
    ("salamplay",                 "salamplay"),
    ("salam pay",                 "salamplay"),
    ("koronapay",                 "koronapay"),
    ("unistream",                 "unistream"),
    ("western union",             "westernunion"),
    ("multitransfer",             "multitransfer"),
    ("dengi",                     "dengi"),
]


def _bank_key(name: str, prefix: str = "agg") -> str:
    norm = name.lower()
    for pattern, key in _BANK_KEY_MAP:
        if pattern in norm:
            return key
    return prefix + "_" + re.sub(r"[^a-z0-9]+", "_", norm).strip("_")


def _parse_rate_text(text: str) -> Optional[float]:
    """'158 so'm' yoki '12 015 so'm' → float."""
    if not text:
        return None
    m = re.search(r"([\d\s]+(?:[.,]\d+)?)", text.replace("\xa0", " "))
    if not m:
        return None
    try:
        val = float(re.sub(r"\s+", "", m.group(1)).replace(",", "."))
        return val if val > 5 else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# BANK.UZ AGREGATOR — barcha banklar RUB kursi
# ---------------------------------------------------------------------------

_bankuz_hash: Optional[str] = None
_bankuz_cached: dict[str, dict] = {}

_kursuz_hash: Optional[str] = None
_kursuz_cached: dict[str, dict] = {}


def _parse_bankuz_col(container) -> dict[str, float]:
    """bc-inner-block-left yoki bc-inner-blocks-right ichidan {bank_name: rate} qaytaradi."""
    result: dict[str, float] = {}
    for row in container.find_all("div", class_="bc-inner-block-left-texts"):
        name_el = row.select_one(".bc-inner-block-left-text .medium-text")
        rate_el = row.select_one("span.green-date")
        if not name_el or not rate_el:
            continue
        name = name_el.get_text(strip=True)
        rate = _parse_rate_text(rate_el.get_text(strip=True))
        if name and rate is not None:
            result[name] = rate
    return result


async def _fetch_bankuz_rub(
    session: aiohttp.ClientSession,
    retries: int = 3,
) -> dict[str, dict[str, float | str]]:
    """
    bank.uz/uz/currency dan barcha banklarning RUB kurslarini oladi.
    Qaytaradi: {key: {"name": str, "buy": float|None, "sell": float|None}, ...}
    - buy  = bank RUB sotib oladi (siz RUB berasiz, UZS olasiz) → RUB_UZS
    - sell = bank RUB sotadi (siz UZS berasiz, RUB olasiz)      → UZS_RUB = 1/sell
    Content hash orqali o'zgarmagan HTML qayta parse qilinmaydi.
    """
    global _bankuz_hash, _bankuz_cached

    url = "https://bank.uz/uz/currency"
    last_exc: Exception = RuntimeError("no attempt")

    for attempt in range(retries):
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    log.warning("bank.uz HTTP %d (urinish %d/%d)", r.status, attempt + 1, retries)
                    if r.status < 500:
                        return {}
                    raise aiohttp.ClientResponseError(r.request_info, r.history, status=r.status)
                html = await r.text(errors="replace")

            # Hash tekshirish — o'zgarmagan bo'lsa qayta parse qilmaymiz
            content_hash = hashlib.md5(html.encode("utf-8", errors="replace")).hexdigest()
            if content_hash == _bankuz_hash and _bankuz_cached:
                log.debug("bank.uz: kontent o'zgarmagan, kesh ishlatildi")
                return _bankuz_cached

            soup = BeautifulSoup(html, _PARSER)

            # #best_RUB tab pane'ni to'g'ridan-to'g'ri topamiz
            rub_pane = soup.find("div", id="best_RUB")
            if not rub_pane:
                log.warning("bank.uz: #best_RUB bloki topilmadi")
                return {}

            buy_col  = rub_pane.find("div", class_="bc-inner-block-left")
            sell_col = rub_pane.find("div", class_="bc-inner-blocks-right")

            buy_by_name:  dict[str, float] = _parse_bankuz_col(buy_col)  if buy_col  else {}
            sell_by_name: dict[str, float] = _parse_bankuz_col(sell_col) if sell_col else {}

            out: dict[str, dict] = {}
            for name in set(buy_by_name) | set(sell_by_name):
                key = _bank_key(name, "bankuz")
                existing = out.get(key)
                if existing is None:
                    out[key] = {
                        "name": name,
                        "buy":  buy_by_name.get(name),
                        "sell": sell_by_name.get(name),
                    }
                else:
                    if "buy" not in existing or existing["buy"] is None:
                        existing["buy"] = buy_by_name.get(name)
                    if "sell" not in existing or existing["sell"] is None:
                        existing["sell"] = sell_by_name.get(name)

            _bankuz_hash   = content_hash
            _bankuz_cached = out
            log.info("bank.uz: %d ta bank RUB kursi olindi (buy=%d, sell=%d)",
                     len(out), len(buy_by_name), len(sell_by_name))
            return out

        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                log.warning("bank.uz qayta urinish %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(1)

    log.warning("bank.uz: %d urinishdan keyin muvaffaqiyatsiz — %s", retries, last_exc)
    return {}


# ---------------------------------------------------------------------------
# THEMONEY.UZ AGREGATOR — JSON-LD orqali banklar RUB kursi (banklar bo'limi)
# ---------------------------------------------------------------------------

_themoney_hash: Optional[str] = None
_themoney_cached: dict[str, dict] = {}


async def _fetch_themoney_rub(
    session: aiohttp.ClientSession,
    retries: int = 3,
) -> dict[str, dict[str, float | str]]:
    """
    themoney.uz/ruble-exchange-rate/ sahifasidagi JSON-LD strukturasidan
    banklar RUB kurslarini oladi.
    Qaytaradi: {key: {"name": str, "buy": float|None, "sell": float|None}, ...}
    - buy  = bank RUB sotib oladi (siz RUB berasiz, UZS olasiz) → RUB_UZS
    - sell = bank RUB sotadi (siz UZS berasiz, RUB olasiz) → UZS_RUB
    Kurslar JSON-LD offers massivida keladi: avval buy (past qiymatlar),
    keyin sell (yuqori qiymatlar). Chegara: 163 UZS.
    """
    global _themoney_hash, _themoney_cached

    url = "https://themoney.uz/ruble-exchange-rate/"
    last_exc: Exception = RuntimeError("no attempt")

    for attempt in range(retries):
        try:
            async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    log.warning("themoney.uz HTTP %d (urinish %d/%d)", r.status, attempt + 1, retries)
                    if r.status < 500:
                        return {}
                    raise aiohttp.ClientResponseError(r.request_info, r.history, status=r.status)
                html = await r.text(errors="replace")

            content_hash = hashlib.md5(html.encode("utf-8", errors="replace")).hexdigest()
            if content_hash == _themoney_hash and _themoney_cached:
                log.debug("themoney.uz: kontent o'zgarmagan, kesh ishlatildi")
                return _themoney_cached

            soup = BeautifulSoup(html, _PARSER)
            scripts = soup.find_all("script", type="application/ld+json")

            offers: list[dict] = []
            for script in scripts:
                try:
                    data = json.loads(script.string or "")
                    if data.get("@type") == "Service" and "offers" in data:
                        offers = data["offers"]
                        break
                except (json.JSONDecodeError, AttributeError):
                    continue

            if not offers:
                log.warning("themoney.uz: JSON-LD offers topilmadi")
                return {}

            threshold = float(os.environ.get("THEMONEY_THRESHOLD", "161"))

            raw_offers: list[tuple[str, float]] = []
            for offer in offers:
                name  = offer.get("seller", {}).get("name", "").strip()
                price = offer.get("price")
                if not name or price is None:
                    continue
                try:
                    raw_offers.append((name, float(price)))
                except (ValueError, TypeError):
                    continue

            out: dict[str, dict] = {}
            for name, price in raw_offers:
                key = _bank_key(name, "tmoney")
                if key not in out:
                    out[key] = {"name": name}
                if price <= threshold:
                    if "buy" not in out[key] or out[key]["buy"] < price:
                        out[key]["buy"] = price
                else:
                    if "sell" not in out[key] or out[key]["sell"] > price:
                        out[key]["sell"] = price

            buy_cnt_check  = sum(1 for v in out.values() if v.get("buy"))
            sell_cnt_check = sum(1 for v in out.values() if v.get("sell"))
            if buy_cnt_check == 0 or sell_cnt_check == 0:
                log.warning(
                    "themoney.uz: threshold=%.0f noto'g'ri bo'lishi mumkin "
                    "(buy=%d, sell=%d). THEMONEY_THRESHOLD env ni tekshiring.",
                    threshold, buy_cnt_check, sell_cnt_check,
                )

            _themoney_hash   = content_hash
            _themoney_cached = out
            buy_cnt  = sum(1 for v in out.values() if v.get("buy"))
            sell_cnt = sum(1 for v in out.values() if v.get("sell"))
            log.info("themoney.uz: %d bank (buy=%d, sell=%d)", len(out), buy_cnt, sell_cnt)
            return out

        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                log.warning("themoney.uz qayta urinish %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(1)

    log.warning("themoney.uz: %d urinishdan keyin muvaffaqiyatsiz — %s", retries, last_exc)
    return {}


def _apply_agg_rates(
    new_cache: dict[str, list[dict]],
    agg_data: dict[str, dict],
    sources: list[dict],
    source_name: str,
    override_existing: bool = False,
) -> None:
    """
    Agregator ma'lumotlarini (bank.uz yoki themoney.uz) keshga birlashtiradi.
    - override_existing=True  → bank.uz: har doim mavjud entryni yangilaydi
    - override_existing=False → themoney.uz: faqat bank.uz dan EMAS bo'lsa yangilaydi
    O(1) lookup uchun index dict ishlatadi.
    """
    if not agg_data:
        return

    source_by_key = {s["key"]: s for s in sources}
    idx: dict[str, dict[str, dict]] = {
        "RUB_UZS": {e["key"]: e for e in new_cache["RUB_UZS"]},
        "UZS_RUB": {e["key"]: e for e in new_cache["UZS_RUB"]},
    }

    for key, data in agg_data.items():
        buy  = data.get("buy")
        sell = data.get("sell")
        name = str(data["name"])

        src          = source_by_key.get(key)
        stype        = src.get("type", "bank") if src else "bank"
        display_name = src["name"] if src else name

        # ── RUB_UZS ──────────────────────────────────────────────────────
        if isinstance(buy, (int, float)) and buy > 0:
            rub_uzs  = float(buy)
            existing = idx["RUB_UZS"].get(key)
            if existing is None:
                entry = {
                    "key":    key,
                    "name":   display_name,
                    "type":   stype,
                    "pair":   "RUB_UZS",
                    "rate":   rub_uzs,
                    "source": source_name,
                }
                new_cache["RUB_UZS"].append(entry)
                idx["RUB_UZS"][key] = entry
            elif existing.get("direct"):
                pass  # bank o'z API'sidan keldi — aggregator bosib o'tmaydi
            elif override_existing or existing.get("source") != "bank.uz":
                existing.update(rate=rub_uzs, name=display_name, source=source_name)

        # ── UZS_RUB (sell = UZS/RUB → rate = 1/sell) ────────────────────
        if isinstance(sell, (int, float)) and sell > 0:
            uzs_rub  = 1.0 / float(sell)
            existing = idx["UZS_RUB"].get(key)
            if existing is None:
                entry = {
                    "key":    key,
                    "name":   display_name,
                    "type":   stype,
                    "pair":   "UZS_RUB",
                    "rate":   uzs_rub,
                    "source": source_name,
                }
                new_cache["UZS_RUB"].append(entry)
                idx["UZS_RUB"][key] = entry
            elif existing.get("direct"):
                pass  # bank o'z API'sidan keldi — aggregator bosib o'tmaydi
            elif override_existing or existing.get("source") != "bank.uz":
                existing.update(rate=uzs_rub, name=display_name, source=source_name)


# ---------------------------------------------------------------------------
# KESH
# ---------------------------------------------------------------------------

_cache: dict[str, list[dict]] = {}
_cache_time: Optional[datetime] = None
_cbu_official: dict[str, float] = {}
_universal_bank_rates: dict[str, float] = {}
CACHE_TTL = 600  # 10 daqiqa


def get_cached(pair: str) -> Optional[list[dict]]:
    if _cache_time and (datetime.now() - _cache_time).total_seconds() < CACHE_TTL:
        return _cache.get(pair)
    return None


def get_cache_time() -> Optional[datetime]:
    return _cache_time


def get_cbu_official(pair: str) -> Optional[float]:
    return _cbu_official.get(pair)


def get_reference_rate(pair: str) -> Optional[float]:
    """Taqqoslash kursi: avval Universal bank, yo'q bo'lsa CBU."""
    return _universal_bank_rates.get(pair) or _cbu_official.get(pair)


def get_reference_label(pair: str) -> str:
    """Taqqoslash manbasining nomi."""
    return "Universal bank" if _universal_bank_rates.get(pair) else "CBU"


# ---------------------------------------------------------------------------
# CBU — O'zbekiston Markaziy Banki
# ---------------------------------------------------------------------------

async def _fetch_cbu(session: aiohttp.ClientSession, retries: int = 3) -> dict[str, float]:
    last_exc: Exception = RuntimeError("no attempt")
    for attempt in range(retries):
        try:
            async with session.get(
                "https://cbu.uz/uz/arkhiv-kursov-valyut/json/",
                headers=HEADERS, timeout=TIMEOUT,
            ) as r:
                data = await r.json(content_type=None)

            raw: dict[str, float] = {}
            for item in data:
                ccy      = item["Ccy"].upper()
                nom      = float(item.get("Nominal", 1) or 1)
                raw[ccy] = float(item["Rate"]) / nom

            out: dict[str, float] = {}
            rub = raw.get("RUB")
            usd = raw.get("USD")

            if rub:
                out["RUB_UZS"] = rub
                out["UZS_RUB"] = 1.0 / rub
            if usd:
                out["USD_UZS"] = usd
            if rub and usd:
                out["USD_RUB"] = usd / rub
                out["RUB_USD"] = rub / usd

            log.info("CBU: RUB=%.2f UZS/RUB, USD=%.2f UZS/USD", rub or 0, usd or 0)
            return out
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                log.warning("CBU qayta urinish %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(1)
    log.warning("CBU: %d urinishdan keyin muvaffaqiyatsiz — %s", retries, last_exc)
    return {}


# ---------------------------------------------------------------------------
# CBR — Rossiya Markaziy Banki (XML)
# ---------------------------------------------------------------------------

async def _fetch_cbr(session: aiohttp.ClientSession, retries: int = 3) -> dict[str, float]:
    last_exc: Exception = RuntimeError("no attempt")
    for attempt in range(retries):
        try:
            async with session.get(
                "https://www.cbr.ru/scripts/XML_daily.asp",
                headers=HEADERS, timeout=TIMEOUT,
            ) as r:
                text = await r.text(encoding="windows-1251")

            root = ET.fromstring(text)
            raw: dict[str, float] = {}
            for valute in root.findall("Valute"):
                code    = (valute.findtext("CharCode") or "").upper()
                nom     = float((valute.findtext("Nominal") or "1").replace(",", "."))
                val     = float((valute.findtext("Value")   or "0").replace(",", "."))
                raw[code] = val / nom

            out: dict[str, float] = {}
            if "USD" in raw:
                out["USD_RUB"] = raw["USD"]
                out["RUB_USD"] = 1.0 / raw["USD"]
            log.info("CBR: USD=%.2f RUB/USD", raw.get("USD", 0))
            return out
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                log.warning("CBR qayta urinish %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(1)
    log.warning("CBR: %d urinishdan keyin muvaffaqiyatsiz — %s", retries, last_exc)
    return {}


# ---------------------------------------------------------------------------
# HTML SCRAPER — individual banklar uchun
# ---------------------------------------------------------------------------

def _extract_number(text: str) -> Optional[float]:
    if not text:
        return None
    # Faqat birinchi raqam tokenini olamiz: "100,00 - 0,00" → 100.0
    # (ko'p bank jadvallari "kurs - o'zgarish" ko'rinishida beradi)
    m = re.search(r"\d[\d\s]*(?:[.,]\d+)?", text.replace("\xa0", " "))
    if not m:
        return None
    clean = re.sub(r"\s+", "", m.group()).replace(",", ".")
    try:
        val = float(clean)
        return val if 10 < val < 5000 else None
    except (ValueError, TypeError):
        return None


def _extract_rub_from_table(soup: BeautifulSoup) -> Optional[tuple[Optional[float], Optional[float]]]:
    for row in soup.find_all("tr"):
        text = row.get_text(" ", strip=True)
        if not re.search(r"\bRUB\b|[Рр]убл|рубл|\bруб\b", text, re.IGNORECASE):
            continue
        cells = [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
        nums: list[float] = [n for c in cells if (n := _extract_number(c)) is not None]
        if len(nums) >= 2:
            return nums[0], nums[1]
        if len(nums) == 1:
            return nums[0], None
    return None


async def _scrape_bank_html(
    session: aiohttp.ClientSession,
    url: str,
    rate_field: str = "buy",
    fallback_urls: Optional[list[str]] = None,
) -> Optional[tuple[Optional[float], Optional[float]]]:
    for try_url in [url] + (fallback_urls or []):
        try:
            async with session.get(try_url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    log.debug("HTML %s → HTTP %d", try_url, r.status)
                    continue
                html = await r.text(errors="replace")

            soup = BeautifulSoup(html, _PARSER)
            result = _extract_rub_from_table(soup)
            if result and result[0] is not None:
                return result

            log.debug("HTML scrape (%s): RUB kursi topilmadi", try_url)
        except Exception as e:
            log.debug("HTML scrape (%s): %s", try_url, e)
    return None


async def _fetch_themoney_bank_page(
    session: aiohttp.ClientSession,
    url: str,
) -> Optional[tuple[Optional[float], Optional[float]]]:
    """
    themoney.uz/banks/{slug}/rub/ sahifasidan buy/sell kurslarini oladi.
    Sahifada 3 ta bold span: [0]=buy, [1]=sell, [2]=CBU rasmiy kurs.
    Qaytaradi: (buy, sell) — biri None bo'lishi mumkin.
    """
    try:
        async with session.get(url, headers=HEADERS, timeout=TIMEOUT) as r:
            if r.status != 200:
                log.debug("themoney bank page %s → HTTP %d", url, r.status)
                return None
            html = await r.text(errors="replace")

        soup = BeautifulSoup(html, _PARSER)

        rate_spans = soup.find_all(
            "span",
            class_=lambda c: c and "text-2xl" in c and "font-bold" in c,
        )

        nums: list[Optional[float]] = []
        for s in rate_spans[:2]:
            text = s.get_text(strip=True).replace(",", ".").replace("\xa0", "")
            try:
                val = float(text)
                nums.append(val if 10 < val < 5000 else None)
            except ValueError:
                nums.append(None)

        buy  = nums[0] if len(nums) > 0 else None
        sell = nums[1] if len(nums) > 1 else None

        if buy is not None or sell is not None:
            return buy, sell
        return None

    except Exception as e:
        log.debug("themoney bank page (%s): %s", url, e)
    return None


# ---------------------------------------------------------------------------
# KORONAPAY API — RUB->UZS o'tkazma kursi (tasdiqlangan endpoint)
# ---------------------------------------------------------------------------

async def _fetch_koronapay_api(
    session: aiohttp.ClientSession,
    receiving_method: str = "cash",
) -> Optional[float]:
    """
    KoronaPay tariffs API dan RUB->UZS o'tkazma kursini oladi.
    sendingCurrencyId=810 (API ning ishlaydigan kodi; 643 ISO ishlaydi bo'lsa ham 500 qaytaradi).
    API javobi: exchangeRate = UZS/RUB inverted (ya'ni ~0.006 = 1 UZS uchun RUB).
    Biz 1/exchangeRate yoki receivingAmount/sendingAmountWithoutCommission ni olamiz.
    Qaytaradi: 1 RUB = X UZS (RUB_UZS pair uchun).
    """
    url = "https://koronapay.com/transfers/online/api/transfers/tariffs"
    params = {
        "sendingCountryId":        "RUS",
        "sendingCurrencyId":       "810",
        "receivingCountryId":      "UZB",
        "receivingCurrencyId":     "860",
        "receivingAmount":         "1000000",
        "receivingMethod":         receiving_method,
        "paymentMethod":           "debitCard",
        "paidNotificationEnabled": "false",
    }
    headers = {
        **HEADERS,
        "Accept":   "application/json, text/plain, */*",
        "Origin":   "https://koronapay.com",
        "Referer":  "https://koronapay.com/transfers/online/",
    }

    try:
        async with session.get(url, params=params, headers=headers, timeout=TIMEOUT) as r:
            if r.status != 200:
                log.warning("KoronaPay API: HTTP %d", r.status)
                return None
            data = await r.json(content_type=None)

        if isinstance(data, list):
            data = data[0] if data else {}
        if not isinstance(data, dict):
            log.warning("KoronaPay: kutilmagan javob turi")
            return None

        # exchangeRate = UZS→RUB yo'nalishi (kichik son, ~0.006)
        # RUB_UZS = 1 / exchangeRate  YO'Q'SA  receivingAmount / sendingAmountWithoutCommission
        exchange_rate_raw = data.get("exchangeRate")
        sending_raw       = data.get("sendingAmountWithoutCommission") or data.get("sendingAmount")
        receiving_raw     = data.get("receivingAmount")

        rub_uzs: Optional[float] = None
        if exchange_rate_raw and float(exchange_rate_raw) > 0:
            er = float(exchange_rate_raw)
            # exchangeRate < 1 → teskari: 1/er = UZS/RUB
            rub_uzs = (1.0 / er) if er < 1 else er
        elif sending_raw and receiving_raw:
            rub_uzs = float(receiving_raw) / float(sending_raw)

        if rub_uzs is None or not (50 < rub_uzs < 500):
            log.warning("KoronaPay: kutilmagan kurs=%.4f", rub_uzs or 0)
            return None

        log.info("KoronaPay: RUB_UZS=%.4f (method=%s)", rub_uzs, receiving_method)
        return rub_uzs

    except Exception as e:
        log.warning("KoronaPay API: %s", e)
        return None


# ---------------------------------------------------------------------------
# JS-RENDER — Playwright orqali JS bilan ishlovchi bank saytlaridan kurs olish
# ---------------------------------------------------------------------------

def _rub_numbers_from_html(html: str) -> list[float]:
    """Renderlangan HTML dagi RUB qatoridan barcha kurs raqamlarini qaytaradi.
    Faqat 50–300 oralig'i (valyuta kodlari 643/840/860 va mayda sonlar tashlanadi)."""
    soup = BeautifulSoup(html, _PARSER)
    for tr in soup.find_all("tr"):
        t = tr.get_text(" ", strip=True)
        if not re.search(r"\bRUB\b|\b643\b|рубл", t, re.I):
            continue
        nums: list[float] = []
        for m in re.findall(r"\d[\d\s]*(?:[.,]\d+)?", t.replace("\xa0", " ")):
            try:
                v = float(re.sub(r"\s+", "", m).replace(",", "."))
            except ValueError:
                continue
            if 50 < v < 300:
                nums.append(v)
        if nums:
            return nums
    return []


async def _fetch_js_render_batch(js_sources: list[dict]) -> dict[str, dict]:
    """
    Playwright (bitta brauzer) orqali JS bilan ishlovchi bank sahifalarini
    renderlaydi va RUB qatoridagi xom raqamlarni qaytaradi.
    Qaytaradi: {key: {"name", "type", "numbers": [..]}}.
    CB kursini ajratish refresh_all da (cbu ma'lum) bajariladi.
    Brauzer yo'q yoki xato bo'lsa — bo'sh dict (aggregator zaxira bo'ladi).
    """
    if not js_sources:
        return {}
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        log.warning("Playwright o'rnatilmagan — JS banklar aggregatordan olinadi")
        return {}

    results: dict[str, dict] = {}
    nav_timeout = int(os.environ.get("JS_NAV_TIMEOUT", "30000"))

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(user_agent=HEADERS["User-Agent"])

            async def one(src: dict) -> None:
                key = src["key"]
                url = src.get("fetch", {}).get("url", "")
                if not url:
                    return
                page = await ctx.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=nav_timeout)
                    await page.wait_for_timeout(2000)
                    htmls = [await page.content()]
                    for fr in page.frames:      # iframe (fondbozori va h.k.)
                        try:
                            htmls.append(await fr.content())
                        except Exception:
                            pass
                except Exception as e:
                    log.warning("js_render %s: %s", key, type(e).__name__)
                    return
                finally:
                    await page.close()
                for h in htmls:
                    nums = _rub_numbers_from_html(h)
                    if nums:
                        results[key] = {
                            "name": src["name"],
                            "type": src.get("type", "bank"),
                            "numbers": nums,
                        }
                        log.info("js_render %s: %s", key, nums)
                        return
                log.warning("js_render %s: RUB topilmadi", key)

            await asyncio.gather(*[one(s) for s in js_sources], return_exceptions=True)
            await browser.close()
    except Exception as e:
        log.warning("js_render batch xato: %s", e)
    return results


# ---------------------------------------------------------------------------
# HAMKORBANK — bankning o'z rasmiy API'si (tasdiqlangan)
# ---------------------------------------------------------------------------

async def _fetch_hamkorbank_api(
    session: aiohttp.ClientSession,
) -> tuple[Optional[float], Optional[float]]:
    """
    Hamkorbank rasmiy DBO API'sidan RUB kursini oladi.
    Endpoint: api-dbo.hamkorbank.uz/webflow/v1/exchanges
    Qiymatlar tiyinda (×100): buying_rate=14700 → 147.00, selling_rate=16700 → 167.00.
    Qaytaradi: (buy, sell) — RUB_UZS=buy, UZS_RUB=1/sell.
    """
    url = "https://api-dbo.hamkorbank.uz/webflow/v1/exchanges"
    try:
        async with session.get(
            url, headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT,
        ) as r:
            if r.status != 200:
                log.warning("Hamkorbank API: HTTP %d", r.status)
                return (None, None)
            data = await r.json(content_type=None)

        rows = data.get("data") if isinstance(data, dict) else data
        for it in (rows or []):
            if str(it.get("currency_char", "")).upper() != "RUB":
                continue
            buy_raw  = it.get("buying_rate")
            sell_raw = it.get("selling_rate")
            buy  = float(buy_raw) / 100.0  if buy_raw  else None
            sell = float(sell_raw) / 100.0 if sell_raw else None
            if buy is not None and not (10 < buy < 5000):
                buy = None
            if sell is not None and not (10 < sell < 5000):
                sell = None
            log.info("Hamkorbank API: buy=%s sell=%s", buy, sell)
            return (buy, sell)
        log.warning("Hamkorbank API: RUB topilmadi")
    except Exception as e:
        log.warning("Hamkorbank API: %s", e)
    return (None, None)


# ---------------------------------------------------------------------------
# TENGEBANK — bankning o'z rasmiy API'si (tasdiqlangan)
# ---------------------------------------------------------------------------

async def _fetch_tengebank_api(
    session: aiohttp.ClientSession,
) -> tuple[Optional[float], Optional[float]]:
    """
    Tenge Bank rasmiy API'sidan RUB kursini oladi.
    Endpoint: tengebank.uz/api/exchangerates/tables
    Javob: personal[0].currency.RUB.{buy, sell} (to'g'ridan-to'g'ri so'm).
    """
    url = "https://tengebank.uz/api/exchangerates/tables"
    try:
        async with session.get(
            url, headers={**HEADERS, "Accept": "application/json"}, timeout=TIMEOUT,
        ) as r:
            if r.status != 200:
                log.warning("Tengebank API: HTTP %d", r.status)
                return (None, None)
            data = await r.json(content_type=None)

        personal = data.get("personal") or []
        if personal:
            rub = (personal[0].get("currency") or {}).get("RUB") or {}
            buy_raw, sell_raw = rub.get("buy"), rub.get("sell")
            buy  = float(buy_raw)  if buy_raw  else None
            sell = float(sell_raw) if sell_raw else None
            if buy is not None and not (10 < buy < 5000):
                buy = None
            if sell is not None and not (10 < sell < 5000):
                sell = None
            log.info("Tengebank API: buy=%s sell=%s", buy, sell)
            return (buy, sell)
        log.warning("Tengebank API: RUB topilmadi")
    except Exception as e:
        log.warning("Tengebank API: %s", e)
    return (None, None)


# ---------------------------------------------------------------------------
# WISE API — bepul mid-market kurs (barcha juftlar)
# ---------------------------------------------------------------------------

async def _fetch_wise_api(
    session: aiohttp.ClientSession,
    pairs: list[str],
) -> dict[str, float]:
    """
    wise.com/rates/live dan barcha kerakli juftlar kursini oladi.
    Javob: {"source":"RUB","target":"UZS","value":164.718,"time":...}
    Mid-market kurs — olish/sotish farqisiz rasmiy bozor kurs.
    """
    _PAIR_PARAMS: dict[str, tuple[str, str]] = {
        "RUB_UZS": ("RUB", "UZS"),
        "UZS_RUB": ("UZS", "RUB"),
    }
    results: dict[str, float] = {}
    for pair in pairs:
        ccy = _PAIR_PARAMS.get(pair)
        if not ccy:
            continue
        try:
            async with session.get(
                "https://wise.com/rates/live",
                params={"source": ccy[0], "target": ccy[1]},
                headers=HEADERS, timeout=TIMEOUT,
            ) as r:
                if r.status != 200:
                    log.warning("Wise API (%s): HTTP %d", pair, r.status)
                    continue
                data = await r.json(content_type=None)
                val = float(data["value"])
                if val > 0:
                    results[pair] = val
                    log.info("Wise: %s=%.4f", pair, val)
        except Exception as e:
            log.warning("Wise API (%s): %s", pair, e)
    return results


# ---------------------------------------------------------------------------
# UNIRATEAPI — forex mid-market kurslari (USD orqali cross-rate)
# ---------------------------------------------------------------------------

async def _fetch_unirateapi(
    session: aiohttp.ClientSession,
    pairs: list[str],
) -> dict[str, float]:
    """
    UniRateAPI dan USD orqali RUB_UZS cross-kursini hisoblaydi.
    from=USD → rates["UZS"] / rates["RUB"] = RUB_UZS
    API kaliti UNIRATEAPI_KEY env o'zgaruvchisida bo'lishi kerak.
    """
    api_key = os.environ.get("UNIRATEAPI_KEY", "")
    if not api_key:
        log.warning("UniRateAPI: UNIRATEAPI_KEY env topilmadi")
        return {}

    results: dict[str, float] = {}
    try:
        async with session.get(
            "https://api.unirateapi.com/api/rates",
            params={"api_key": api_key, "from": "USD"},
            headers=HEADERS, timeout=TIMEOUT,
        ) as r:
            if r.status != 200:
                log.warning("UniRateAPI: HTTP %d", r.status)
                return {}
            data = await r.json(content_type=None)

        rates = data.get("rates", {})
        uzs_per_usd = float(rates.get("UZS") or 0)
        rub_per_usd = float(rates.get("RUB") or 0)

        if uzs_per_usd > 0 and rub_per_usd > 0:
            rub_uzs = uzs_per_usd / rub_per_usd
            if "RUB_UZS" in pairs:
                results["RUB_UZS"] = rub_uzs
            if "UZS_RUB" in pairs:
                results["UZS_RUB"] = 1.0 / rub_uzs
            log.info(
                "UniRateAPI: RUB_UZS=%.4f (UZS/USD=%.2f, RUB/USD=%.4f)",
                rub_uzs, uzs_per_usd, rub_per_usd,
            )
    except Exception as e:
        log.warning("UniRateAPI: %s", e)
    return results


# ---------------------------------------------------------------------------
# COINBASE MPAY — CoinGecko orqali MPAY/USD → RUB_UZS cross-rate
# ---------------------------------------------------------------------------

async def _fetch_coinbase_mpay(
    session: aiohttp.ClientSession,
    pairs: list[str],
) -> dict[str, float]:
    """
    coinbase.com/converter/mpay/uzs manbasiga asosan MPAY token kurslari oladi.
    Coinbase API MPAY'ni bloklaydi → CoinGecko (id: mmp-pay) ishlatiladi.
    MPAY/USD (CoinGecko) + USD/UZS, USD/RUB (UniRateAPI) → RUB_UZS hisoblash.
    """
    api_key = os.environ.get("UNIRATEAPI_KEY", "")
    results: dict[str, float] = {}
    try:
        # 1) CoinGecko: MPAY token narxi USD va RUB da
        async with session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "mmp-pay", "vs_currencies": "usd,rub"},
            headers={**HEADERS, "Accept": "application/json"},
            timeout=TIMEOUT,
        ) as r:
            if r.status != 200:
                log.warning("CoinGecko MPAY: HTTP %d", r.status)
                return {}
            gecko = await r.json(content_type=None)

        mpay_usd = float((gecko.get("mmp-pay") or {}).get("usd") or 0)
        mpay_rub = float((gecko.get("mmp-pay") or {}).get("rub") or 0)

        if mpay_usd <= 0 or mpay_rub <= 0:
            log.warning("CoinGecko MPAY: narx topilmadi")
            return {}

        # 2) UniRateAPI: USD/UZS olish
        if not api_key:
            log.warning("CoinGecko MPAY: UNIRATEAPI_KEY yo'q — UZS hisoblash mumkin emas")
            return {}

        async with session.get(
            "https://api.unirateapi.com/api/rates",
            params={"api_key": api_key, "from": "USD"},
            headers=HEADERS, timeout=TIMEOUT,
        ) as r:
            if r.status != 200:
                return {}
            uni = await r.json(content_type=None)

        usd_uzs = float((uni.get("rates") or {}).get("UZS") or 0)
        if usd_uzs <= 0:
            return {}

        # MPAY/UZS = mpay_usd × usd_uzs
        # RUB_UZS = MPAY_UZS / MPAY_RUB
        mpay_uzs = mpay_usd * usd_uzs
        rub_uzs  = mpay_uzs / mpay_rub

        if "RUB_UZS" in pairs and 50 < rub_uzs < 500:
            results["RUB_UZS"] = rub_uzs
            log.info(
                "Coinbase MPAY (CoinGecko): RUB_UZS=%.4f "
                "(MPAY/USD=%.2e, MPAY/RUB=%.6f, USD/UZS=%.2f)",
                rub_uzs, mpay_usd, mpay_rub, usd_uzs,
            )
    except Exception as e:
        log.warning("Coinbase MPAY: %s", e)
    return results


# ---------------------------------------------------------------------------
# BANK RASMIY JSON API → HTML FALLBACK
# ---------------------------------------------------------------------------

def _parse_rate_val(v: object) -> Optional[float]:
    if v is None:
        return None
    try:
        val = float(str(v).replace(",", ".").replace(" ", "").replace("\xa0", ""))
        return val if 10 < val < 5000 else None
    except (ValueError, TypeError):
        return None


def _parse_json_for_rub(data: object) -> Optional[tuple[Optional[float], Optional[float]]]:
    """Har qanday JSON strukturasidan RUB buy/sell topadi (rekursiv)."""
    if isinstance(data, list):
        for item in data:
            r = _parse_json_for_rub(item)
            if r:
                return r
        return None
    if not isinstance(data, dict):
        return None
    ccy = str(
        data.get("ccy") or data.get("currency") or data.get("code") or
        data.get("charCode") or data.get("name") or data.get("title") or ""
    ).upper()
    if "RUB" in ccy:
        buy  = _parse_rate_val(
            data.get("buy") or data.get("purchase") or
            data.get("buyRate") or data.get("buy_rate") or data.get("in")
        )
        sell = _parse_rate_val(
            data.get("sell") or data.get("sale") or
            data.get("sellRate") or data.get("sell_rate") or data.get("out")
        )
        if buy or sell:
            return buy, sell
    for v in data.values():
        if isinstance(v, (dict, list)):
            r = _parse_json_for_rub(v)
            if r:
                return r
    return None


async def _fetch_bank_json_api(
    session: aiohttp.ClientSession,
    api_urls: list[str],
    html_urls: list[str],
) -> Optional[tuple[Optional[float], Optional[float]]]:
    """
    Bank rasmiy JSON API → inline JSON → HTML jadval ketma-ket urinadi.
    api_urls: rasmiy API endpointlar ro'yxati.
    html_urls: HTML sahifa URL'lar (birinchisi asosiy, qolganlari fallback).
    """
    # 1) Rasmiy JSON API urinish
    for api_url in api_urls:
        try:
            async with session.get(api_url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    continue
                text = await r.text(errors="replace")
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    continue
                result = _parse_json_for_rub(data)
                if result:
                    log.info("Bank API (%s): buy=%s sell=%s", api_url, result[0], result[1])
                    return result
        except Exception as e:
            log.debug("Bank API (%s): %s", api_url, e)

    # 2) HTML sahifalar: avval inline JSON, keyin jadval
    for html_url in html_urls:
        try:
            async with session.get(html_url, headers=HEADERS, timeout=TIMEOUT) as r:
                if r.status != 200:
                    continue
                html = await r.text(errors="replace")
            soup = BeautifulSoup(html, _PARSER)

            # 2a) <script> ichidagi inline JSON
            for script in soup.find_all("script"):
                src = script.string or ""
                if "RUB" not in src.upper():
                    continue
                for m in re.finditer(r'(\[[\s\S]*?\]|\{[\s\S]*?\})', src):
                    try:
                        result = _parse_json_for_rub(json.loads(m.group()))
                        if result:
                            log.info("Bank inline JSON (%s): buy=%s sell=%s", html_url, *result)
                            return result
                    except Exception:
                        pass

            # 2b) HTML jadval
            result = _extract_rub_from_table(soup)
            if result and (result[0] is not None or result[1] is not None):
                log.info("Bank HTML table (%s): buy=%s sell=%s", html_url, *result)
                return result
        except Exception as e:
            log.debug("Bank HTML (%s): %s", html_url, e)

    return None


# ---------------------------------------------------------------------------
# KURS.UZ AGREGATOR — zaxira manba
# ---------------------------------------------------------------------------

async def _fetch_kursuz_rub(
    session: aiohttp.ClientSession,
    retries: int = 1,
) -> dict[str, dict[str, float | str]]:
    """
    kurs.uz agregatoridan barcha banklarning RUB kurslarini oladi.
    bank.uz va themoney.uz dan keyin uchinchi zaxira manba sifatida ishlatiladi.
    Faqat zaxira bo'lgani uchun qisqa timeout — ishga tushishni bloklamasligi kerak.
    """
    global _kursuz_hash, _kursuz_cached

    # Zaxira manba: qisqa timeout (asosiy fetch'larni bloklamaslik uchun)
    kursuz_timeout = aiohttp.ClientTimeout(total=4)

    for attempt in range(retries):
        try:
            async with session.get(
                "https://kurs.uz/", headers=HEADERS, timeout=kursuz_timeout
            ) as r:
                if r.status != 200:
                    break
                html = await r.text(errors="replace")

            content_hash = hashlib.md5(html.encode("utf-8", errors="replace")).hexdigest()
            if content_hash == _kursuz_hash and _kursuz_cached:
                log.debug("kurs.uz: kesh ishlatildi")
                return _kursuz_cached

            soup = BeautifulSoup(html, _PARSER)
            out: dict[str, dict] = {}

            for row in soup.find_all("tr"):
                text = row.get_text(" ", strip=True)
                if not re.search(r"\bRUB\b|рубл", text, re.IGNORECASE):
                    continue
                cells = [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                if len(cells) < 3:
                    continue
                bank_name = cells[0]
                key = _bank_key(bank_name, "kursuz")
                nums = [n for c in cells[1:] if (n := _parse_rate_text(c)) is not None]
                if len(nums) >= 2:
                    out[key] = {"name": bank_name, "buy": nums[0], "sell": nums[1]}
                elif nums:
                    out[key] = {"name": bank_name, "buy": nums[0]}

            if out:
                _kursuz_hash = content_hash
                _kursuz_cached = out
                log.info("kurs.uz: %d ta bank RUB kursi olindi", len(out))
                return out

        except Exception as e:
            if attempt < retries - 1:
                log.warning("kurs.uz urinish %d/%d: %s", attempt + 1, retries, e)
                await asyncio.sleep(1)

    return {}


# ---------------------------------------------------------------------------
# BITTA SERVIS UCHUN KURS OLISH
# ---------------------------------------------------------------------------

async def _fetch_service_pairs(
    session: aiohttp.ClientSession,
    src: dict,
    cbu: dict[str, float],
) -> list[dict]:
    fetch  = src.get("fetch", {})
    method = fetch.get("method", "pending")
    key    = src["key"]
    name   = src["name"]
    stype  = src.get("type", "card")
    pairs  = src.get("pairs", [])
    results: list[dict] = []

    if method == "pending":
        for pair in pairs:
            results.append({"key": key, "name": name, "type": stype, "pair": pair, "rate": None})
        return results

    if method == "bank_json_api":
        api_urls  = fetch.get("api_urls", [])
        html_urls = [fetch["url"]] + fetch.get("fallback_urls", []) if fetch.get("url") else fetch.get("fallback_urls", [])
        scraped   = await _fetch_bank_json_api(session, api_urls, html_urls)
        buy_rate, sell_rate = scraped if scraped else (None, None)
        if buy_rate is not None or sell_rate is not None:
            log.info("✓ %s: RUB buy=%s sell=%s", name, buy_rate, sell_rate)
        else:
            log.warning("✗ %s: kurs topilmadi (bank_json_api)", name)
        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = buy_rate
            elif pair == "UZS_RUB":
                rate = (1.0 / sell_rate) if sell_rate else None
            results.append({"key": key, "name": name, "type": stype, "pair": pair, "rate": rate})
        return results

    if method == "html_scrape":
        fallback_urls = fetch.get("fallback_urls", [])
        scraped = await _scrape_bank_html(session, fetch["url"], fetch.get("rate_field", "buy"), fallback_urls)
        buy_rate, sell_rate = scraped if scraped else (None, None)

        if buy_rate is not None or sell_rate is not None:
            log.info("✓ %s: RUB buy=%s sell=%s (o'z sayti)", name, buy_rate, sell_rate)
        else:
            log.warning("✗ %s: RUB kurs topilmadi (o'z sayti)", name)

        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = buy_rate
            elif pair == "UZS_RUB":
                rate = (1.0 / sell_rate) if sell_rate else None
            entry = {"key": key, "name": name, "type": stype, "pair": pair, "rate": rate}
            if rate is not None:
                entry["direct"] = True               # bank o'z saytidan — aggregator bosib o'tmaydi
                entry["source"] = fetch.get("source_label", "bank sayti")
            results.append(entry)
        return results

    if method == "themoney_bank":
        scraped = await _fetch_themoney_bank_page(session, fetch["url"])
        buy_rate, sell_rate = scraped if scraped else (None, None)

        if buy_rate is not None or sell_rate is not None:
            log.info("✓ %s (themoney): buy=%s sell=%s", name, buy_rate, sell_rate)
        else:
            log.warning("✗ %s (themoney): kurs topilmadi — %s", name, fetch.get("url", ""))

        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = buy_rate
            elif pair == "UZS_RUB":
                rate = (1.0 / sell_rate) if sell_rate else None
            results.append({"key": key, "name": name, "type": stype, "pair": pair, "rate": rate})
        return results

    if method == "hamkorbank_api":
        buy_rate, sell_rate = await _fetch_hamkorbank_api(session)
        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = buy_rate
            elif pair == "UZS_RUB":
                rate = (1.0 / sell_rate) if sell_rate else None
            entry = {"key": key, "name": name, "type": stype, "pair": pair, "rate": rate}
            if rate is not None:
                entry["direct"] = True          # bank o'z API'si — aggregator bosib o'tmaydi
                entry["source"] = "hamkorbank.uz"
            results.append(entry)
        return results

    if method == "tengebank_api":
        buy_rate, sell_rate = await _fetch_tengebank_api(session)
        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = buy_rate
            elif pair == "UZS_RUB":
                rate = (1.0 / sell_rate) if sell_rate else None
            entry = {"key": key, "name": name, "type": stype, "pair": pair, "rate": rate}
            if rate is not None:
                entry["direct"] = True
                entry["source"] = "tengebank.uz"
            results.append(entry)
        return results

    if method == "koronapay_api":
        receiving_method = fetch.get("receiving_method", "cash")
        rub_uzs = await _fetch_koronapay_api(session, receiving_method)
        for pair in pairs:
            rate: Optional[float] = None
            if pair == "RUB_UZS":
                rate = rub_uzs
            results.append({"key": key, "name": name, "type": stype, "pair": pair, "rate": rate})
        return results

    if method == "wise_api":
        wise_rates = await _fetch_wise_api(session, pairs)
        for pair in pairs:
            results.append({
                "key":  key,
                "name": name,
                "type": stype,
                "pair": pair,
                "rate": wise_rates.get(pair),
            })
        return results

    if method == "unirateapi_rates":
        uni_rates = await _fetch_unirateapi(session, pairs)
        for pair in pairs:
            results.append({
                "key":  key,
                "name": name,
                "type": stype,
                "pair": pair,
                "rate": uni_rates.get(pair),
            })
        return results

    if method == "coinbase_mpay":
        cb_rates = await _fetch_coinbase_mpay(session, pairs)
        for pair in pairs:
            results.append({
                "key":  key,
                "name": name,
                "type": stype,
                "pair": pair,
                "rate": cb_rates.get(pair),
            })
        return results

    return []


# ---------------------------------------------------------------------------
# ASOSIY YANGILASH FUNKSIYASI
# ---------------------------------------------------------------------------

async def refresh_all() -> set[str]:
    """
    Barcha manbalardan kurslarni parallel oladi, keshni yangilaydi,
    oldingi qiymatlar bilan solishtiradi, DB ga saqlaydi.
    Qaytaradi: o'zgargan juftlar to'plami (masalan {"RUB_UZS", "UZS_RUB"}).
    """
    global _cache, _cache_time, _cbu_official, _universal_bank_rates

    sources: list[dict] = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    api_sources = [
        s for s in sources
        if s.get("fetch", {}).get("method") in (
            "html_scrape", "themoney_bank", "koronapay_api",
            "wise_api", "unirateapi_rates", "coinbase_mpay",
            "bank_json_api", "hamkorbank_api", "tengebank_api",
        )
    ]
    js_sources = [s for s in sources if s.get("fetch", {}).get("method") == "js_render"]

    async with aiohttp.ClientSession() as session:
        # Barcha fetch'larni bir vaqtda boshlaymiz (parallel)
        cbu_task      = asyncio.create_task(_fetch_cbu(session))
        cbr_task      = asyncio.create_task(_fetch_cbr(session))
        bankuz_task   = asyncio.create_task(_fetch_bankuz_rub(session))
        themoney_task = asyncio.create_task(_fetch_themoney_rub(session))
        kursuz_task   = asyncio.create_task(_fetch_kursuz_rub(session))
        # JS-render banklar (Playwright, brauzer) — alohida, o'z timeout'i bilan
        js_timeout = float(os.environ.get("JS_RENDER_TIMEOUT", "40"))
        js_task = asyncio.create_task(
            asyncio.wait_for(_fetch_js_render_batch(js_sources), timeout=js_timeout)
        ) if js_sources else None

        # Har bir individual manba uchun vaqt chegarasi — bitta sekin bank
        # butun yangilanishni cho'zib yubormasligi uchun. Aggregatorlar
        # (bank.uz/themoney.uz) baribir hamma bankni qoplaydi.
        per_source_timeout = float(os.environ.get("SOURCE_TIMEOUT", "8"))

        async def _bounded_fetch(src: dict) -> list[dict]:
            try:
                return await asyncio.wait_for(
                    _fetch_service_pairs(session, src, {}),
                    timeout=per_source_timeout,
                )
            except asyncio.TimeoutError:
                log.warning("✗ %s: vaqt chegarasi (%.0fs)", src.get("name", "?"), per_source_timeout)
                return []

        html_tasks    = [
            asyncio.create_task(_bounded_fetch(src))
            for src in api_sources
        ]

        gather_future = asyncio.gather(
            cbu_task, cbr_task, bankuz_task, themoney_task, kursuz_task, *html_tasks,
            return_exceptions=True,
        )

        # Umumiy timeout — biror manba osilib qolsa ham bot muzlab qolmaydi.
        # Tugamagan task'lar None deb hisoblanadi, qolgan ma'lumotlar ishlatiladi.
        overall_timeout = float(os.environ.get("REFRESH_TIMEOUT", "25"))
        try:
            all_results = await asyncio.wait_for(gather_future, timeout=overall_timeout)
        except asyncio.TimeoutError:
            log.warning(
                "refresh_all: umumiy timeout (%.0fs) — tugagan task'lar ishlatiladi",
                overall_timeout,
            )
            all_tasks = [cbu_task, cbr_task, bankuz_task, themoney_task, kursuz_task, *html_tasks]
            all_results = [
                (t.result() if (t.done() and not t.cancelled() and not t.exception()) else None)
                for t in all_tasks
            ]
            for t in all_tasks:
                if not t.done():
                    t.cancel()

        # JS-render (brauzer) natijasi — alohida kutamiz (sessiyaga bog'liq emas)
        js_raw: dict[str, dict] = {}
        if js_task is not None:
            try:
                js_raw = await js_task
            except Exception as e:
                log.warning("js_render: %s", type(e).__name__)

    def _ok(idx: int) -> dict:
        v = all_results[idx]
        return v if isinstance(v, dict) else {}

    cbu      = _ok(0)
    cbr      = _ok(1)
    bankuz   = _ok(2)
    themoney = _ok(3)
    kursuz   = _ok(4)
    html_results = all_results[5:]

    official_rates = {**cbu}

    # Boshlang'ich kesh strukturasi
    new_cache: dict[str, list[dict]] = {
        "RUB_UZS": [], "UZS_RUB": [],
    }

    # 1) Rasmiy (CBU + CBR) kurslar
    for src in sources:
        method = src.get("fetch", {}).get("method")
        if method not in ("cbu_api", "cbr_xml"):
            continue
        for pair in src.get("pairs", []):
            rate = cbu.get(pair)

            if rate is not None and pair in new_cache:
                if not any(e["key"] == src["key"] for e in new_cache[pair]):
                    new_cache[pair].append({
                        "key":  src["key"],
                        "name": src["name"],
                        "type": src.get("type", "official"),
                        "pair": pair,
                        "rate": rate,
                    })

    # 2) HTML natijalar
    for res in html_results:
        if not isinstance(res, list):  # Exception yoki None (timeout/bekor qilingan) — o'tkazib yuboramiz
            continue
        for entry in res:
            pair = entry["pair"]
            if pair in new_cache:
                new_cache[pair].append(entry)

    # 2b) JS-render (brauzer) banklar — o'z saytidan, direct=True.
    # Xom raqamlardan CB kursini (~cbu) olib tashlaymiz, qolganidan
    # eng kichigi=buy, eng kattasi=sell. Aggregator bu entry'larni bosib o'tmaydi.
    cbu_ruz = cbu.get("RUB_UZS")
    for key, info in js_raw.items():
        nums = info.get("numbers", [])
        clean = [
            n for n in nums
            if not (cbu_ruz and abs(n - cbu_ruz) < 0.6)   # CB kursini tashlaymiz
        ]
        if not clean:
            continue
        buy  = min(clean) if len(clean) >= 2 else None
        sell = max(clean)
        stype = info.get("type", "bank")
        nm    = info.get("name", key)
        src_label = next((s["fetch"].get("source_label") or s["fetch"].get("url", "")
                          for s in js_sources if s["key"] == key), "")
        for pair, rate in (("RUB_UZS", buy), ("UZS_RUB", (1.0 / sell) if sell else None)):
            existing = next((e for e in new_cache[pair] if e["key"] == key), None)
            if rate is None:
                continue
            entry = {"key": key, "name": nm, "type": stype, "pair": pair,
                     "rate": rate, "direct": True, "source": src_label}
            if existing is None:
                new_cache[pair].append(entry)
            else:
                existing.update(entry)

    # 3a) bank.uz agregator — eng yuqori ustuvorlik, har doim yangilaydi
    _apply_agg_rates(new_cache, bankuz, sources, "bank.uz", override_existing=True)

    # 3b) themoney.uz — faqat bank.uz da yo'q yoki bank.uz manba bo'lmaganlar uchun
    _apply_agg_rates(new_cache, themoney, sources, "themoney.uz", override_existing=False)

    # 3c) kurs.uz — uchinchi zaxira agregator (bank.uz ham themoney.uz ham to'ldirolmaganlari uchun)
    _apply_agg_rates(new_cache, kursuz, sources, "kurs.uz", override_existing=False)

    # Universal bank kursini taqqoslash uchun saqlash
    ub_data = themoney.get("universalbank", {})
    _universal_bank_rates = {}
    ub_sell = ub_data.get("sell")
    ub_buy  = ub_data.get("buy")
    if ub_sell and float(ub_sell) > 0:
        _universal_bank_rates["UZS_RUB"] = 1.0 / float(ub_sell)
        # buy bo'lmasa, sell ni RUB_UZS reference sifatida ishlat
        if not (ub_buy and float(ub_buy) > 0):
            _universal_bank_rates["RUB_UZS"] = float(ub_sell)
    if ub_buy and float(ub_buy) > 0:
        _universal_bank_rates["RUB_UZS"] = float(ub_buy)

    # 4) Pending/aggregator servislar: aggregator to'ldirmagan bo'lsa,
    #    ro'yxatda "ma'lumot yo'q" sifatida ko'rinadi (rate=None).
    existing_keys: dict[str, set] = {p: {e["key"] for e in new_cache[p]} for p in new_cache}
    for src in sources:
        if src.get("fetch", {}).get("method") not in ("pending", "aggregator"):
            continue
        for pair in src.get("pairs", []):
            if pair in new_cache and src["key"] not in existing_keys.get(pair, set()):
                new_cache[pair].append({
                    "key":  src["key"],
                    "name": src["name"],
                    "type": src.get("type", "card"),
                    "pair": pair,
                    "rate": None,
                })

    # 4b) Mantiqsiz kurslarni saralash (scraping xatolari: buy/sell aralashishi).
    # Haqiqiy kurs CBU rasmiy kursi atrofida bo'ladi. Bandadan tashqari
    # qiymatlar deyarli har doim noto'g'ri scrape qilingan — ularni chiqaramiz.
    band_lo = float(os.environ.get("RATE_BAND_LO", "0.80"))  # CBU dan -20%
    band_hi = float(os.environ.get("RATE_BAND_HI", "1.08"))  # CBU dan +8%
    for pair, entries in new_cache.items():
        cbu_rate = official_rates.get(pair)
        if not cbu_rate or cbu_rate <= 0:
            continue
        lo, hi = cbu_rate * band_lo, cbu_rate * band_hi
        for e in entries:
            if e.get("direct"):
                continue  # bank o'z sahifasidan — ishonamiz, filtrlamaymiz
            r = e.get("rate")
            if r is not None and not (lo <= r <= hi):
                log.info(
                    "Shubhali %s kurs chiqarildi: %s = %.4f (CBU=%.4f band=%.4f..%.4f)",
                    pair, e["name"], r, cbu_rate, lo, hi,
                )
                e["rate"] = None

    # 5) O'sish/pasayish hisoblash + CBU farq
    db_entries:      list[tuple[str, str, float]] = []
    history_entries: list[tuple[str, str, float]] = []

    for pair, entries in new_cache.items():
        cbu_rate = official_rates.get(pair)
        for e in entries:
            rate = e.get("rate")
            if rate is None:
                e["change"]    = "unknown"
                e["prev_rate"] = None
                e["diff_cbu"]  = None
                continue

            prev = db.get_prev(e["key"], pair)
            e["prev_rate"] = prev  # monitor.py tomonidan ishlatiladi

            if prev is None:
                e["change"] = "unknown"
            elif abs(rate - prev) < 0.0001:
                e["change"] = "same"
            elif rate > prev:
                e["change"] = "up"
            else:
                e["change"] = "down"

            e["diff_cbu"] = (rate - cbu_rate) if cbu_rate else None
            db_entries.append((e["key"], pair, rate))

            # Faqat o'zgargan kurslarni tarixga yozamiz
            if e["change"] in ("up", "down"):
                history_entries.append((e["key"], pair, rate))

        # Eng yuqori rate → birinchi (rate yo'qlar oxirida)
        entries.sort(
            key=lambda x: (x.get("rate") is not None, x.get("rate") or 0),
            reverse=True,
        )

    # 6) DB ga yozish
    if db_entries:
        db.save_rates(db_entries)
    if history_entries:
        db.save_history(history_entries)

    _cache      = new_cache
    _cache_time = datetime.now()

    _cbu_official = {}
    for pair in new_cache:
        if pair in cbu:
            _cbu_official[pair] = cbu[pair]

    # _universal_bank_rates yuqorida 3b bo'limda to'ldirildi

    ok_counts = {p: sum(1 for e in lst if e.get("rate") is not None) for p, lst in new_cache.items()}
    log.info(
        "✅ Kesh yangilandi | RUB_UZS:%d✓  UZS_RUB:%d✓",
        ok_counts["RUB_UZS"], ok_counts["UZS_RUB"],
    )

    changed: set[str] = set()
    for pair, entries in _cache.items():
        for e in entries:
            if e.get("change") in ("up", "down"):
                changed.add(pair)
                break

    return changed
