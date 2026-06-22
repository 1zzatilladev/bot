"""
api.py — RUB ⇄ UZS kurslari uchun barqaror FastAPI xizmati.

ENDPOINT:  GET /rates   → qat'iy JSON (struktura hech qachon o'zgarmaydi)

MUHIM ESLATMA (manba haqida):
    curso.best ochiq kurs ma'lumotini BERMAYDI — u statik landing sahifa,
    kurslar faqat ularning Telegram botida (yopiq tizim). Bu jonli tekshirib
    tasdiqlangan. Shuning uchun kurslar loyihaning O'Z tasdiqlangan
    manbalaridan olinadi (banklarning rasmiy sayt/API'lari — `fetcher.py`).
    JSON struktura siz bergan spetsifikatsiyada, o'zgarmaydi.

ISHGA TUSHIRISH:
    pip install -r requirements.txt
    uvicorn api:app --host 0.0.0.0 --port 8000
    →  http://localhost:8000/rates
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from fastapi import FastAPI

import fetcher

log = logging.getLogger("api")

app = FastAPI(title="Kurs API", docs_url="/docs", redoc_url=None)

# So'ralgan banklar:  fetcher_key -> (ko'rsatiladigan nom, type belgisi)
# (Unired, Tezda, UZUM, ЦБ РФ — ochiq jonli manba yo'qligi sababli qo'shilmaydi)
WANTED: dict[str, tuple[str, str]] = {
    "cbu":         ("ЦБ Узбекистана",    "🏦"),
    "aab":         ("Asia Alliance Bank", "💱"),
    "octobank":    ("Octobank",           "💱"),
    "kapitalbank": ("Kapital Bank",       "💱"),
    "ipakyuli":    ("Ipak Yuli",          "💱"),
    "uzum":        ("UZUM Bank",          "💱"),
}

CACHE_TTL = 60  # soniya — natija shu muddat keshlanadi

_cache: Optional[dict] = None
_cache_time: float = 0.0
_prev: dict[str, float] = {}          # bank nomi -> oxirgi uzs_to_rub (o'zgarish uchun)
_lock = asyncio.Lock()                # bir vaqtda bitta yangilash


def _empty() -> dict:
    return {
        "last_update": datetime.now().isoformat(timespec="seconds"),
        "base": "UZS",
        "target": "RUB",
        "rates": [],
    }


async def _build() -> dict:
    """Kurslarni yig'adi va qat'iy JSON tuzadi. Xatoni yuqoriga uzatadi."""
    global _prev

    # fetcher o'z keshiga ega (≈10 daqiqa). Kesh bo'sh bo'lsagina yangilaymiz.
    entries = fetcher.get_cached("UZS_RUB")
    if entries is None:
        await fetcher.refresh_all()
        entries = fetcher.get_cached("UZS_RUB") or []

    by_key = {e["key"]: e for e in entries if e.get("rate")}

    rates: list[dict] = []
    new_prev: dict[str, float] = {}
    for key, (name, icon) in WANTED.items():
        e = by_key.get(key)
        if not e:
            continue
        rate = float(e["rate"])               # fetcher'da rate = 1/sell
        if rate <= 0:
            continue
        uzs_to_rub = round(1.0 / rate, 5)      # parsed value (so'm / 1 RUB)
        rub_to_uzs = round(rate, 5)            # = 1 / uzs_to_rub

        prev = _prev.get(name)
        if prev is None or abs(uzs_to_rub - prev) < 1e-9:
            change = "0"
        elif uzs_to_rub > prev:
            change = "+"
        else:
            change = "-"
        new_prev[name] = uzs_to_rub

        rates.append({
            "bank": name,
            "type": icon,
            "uzs_to_rub": uzs_to_rub,
            "rub_to_uzs": rub_to_uzs,
            "change": change,
        })

    if new_prev:
        _prev = new_prev

    return {
        "last_update": datetime.now().isoformat(timespec="seconds"),
        "base": "UZS",
        "target": "RUB",
        "rates": rates,
    }


@app.get("/rates")
async def rates() -> dict:
    """RUB ⇄ UZS kurslari — qat'iy JSON. Sayt ishlamasa — oxirgi kesh."""
    global _cache, _cache_time
    now = time.time()
    if _cache is not None and (now - _cache_time) < CACHE_TTL:
        return _cache

    async with _lock:
        # Lock kutilayotganda boshqa so'rov yangilab bo'lgan bo'lishi mumkin
        now = time.time()
        if _cache is not None and (now - _cache_time) < CACHE_TTL:
            return _cache
        try:
            data = await _build()
            _cache = data
            _cache_time = now
            return data
        except Exception as e:           # sayt/manba ishlamasa
            log.warning("rates build xato: %s", e)
            return _cache if _cache is not None else _empty()