# Valyuta Kurslari Telegram Boti

Rossiya ↔ O'zbekiston pul o'tkazmalari kurslarini real vaqtda ko'rsatadigan Telegram bot.

## Xususiyatlar

- **4 yo'nalish** — RUB↔UZS va RUB↔USD tugmalari
- **Tezlik** — kurslar keshdan chiqadi (API kutilmaydi)
- **O'sish/pasayish** — 🟢/🔴 emoji bilan oldingi qiymat bilan taqqoslash
- **CBU farqi** — har servisning rasmiy kursdan farqi (+/-)
- **Avtomatik yangilanish** — har 10 daqiqada fon vazifasi

## Tez ishga tushirish

```bash
# 1. Muhit sozlash
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/Mac

# 2. Paketlarni o'rnatish
pip install -r requirements.txt

# 3. .env fayl yaratish
copy .env.example .env
# Keyin .env ni oching va kamida BOT_TOKEN ni to'ldiring

# 4. Botni ishga tushirish
python bot.py
```

## .env sozlamalari

| O'zgaruvchi | Tavsif | Majburiy |
|---|---|---|
| `BOT_TOKEN` | BotFather dan olingan token | ✅ |
| `CHANNEL_USERNAME` | Kanal username (masalan `@mychannel`) | ❌ |
| `SUPPORT_URL` | Qo'llab-quvvatlash URL | ❌ |
| `UPDATE_INTERVAL` | Yangilash oralig'i (soniya, standart 600) | ❌ |

## Loyiha tuzilmasi

```
bot-file2/
├── bot.py          — Telegram bot (aiogram 3.x), tugmalar, xabar formatlash
├── fetcher.py      — CBU/CBR/HTML kurs yig'ish, in-memory kesh
├── db.py           — SQLite: oldingi kurslarni saqlash (🟢/🔴 uchun)
├── sources.json    — Servislar konfiguratsiyasi (yangi servis qo'shish uchun)
├── rates.py        — Eski bank taqqoslov moduli (saqlab qolindi)
├── requirements.txt
├── .env.example
└── README.md
```

## Yangi servis qo'shish (sources.json)

```json
{
  "key": "myservice",
  "name": "My Service",
  "type": "card",
  "pairs": ["RUB_UZS"],
  "fetch": {
    "method": "html_scrape",
    "url": "https://myservice.com/rates",
    "rate_field": "buy"
  }
}
```

**`method` turlari:**

| Qiymat | Ma'no |
|---|---|
| `cbu_api` | CBU JSON API (faqat CBU uchun) |
| `cbr_xml` | CBR XML API (faqat CBR uchun) |
| `html_scrape` | HTML jadvaldan RUB qatorini topadi |
| `json_api` | JSON API (path va group parametrlari bilan) |
| `pending` | Manba hali to'ldirilmagan — `ma'lumot yo'q` holati |

**`type` turlari:**

| Qiymat | Ikona |
|---|---|
| `card` | 💳 |
| `app` | 📱 |
| `bank` / `official` | 🏦 |

## Pending servislarni to'ldirish

`sources.json` da `"method": "pending"` deb belgilangan servislar (Yubor, Avosend, Unired va boshqalar) hali manba URL'iga ega emas. Ularni quyidagicha to'ldirish mumkin:

```json
"fetch": {
  "method": "html_scrape",
  "url": "https://yubor.ru/rates",
  "rate_field": "buy"
}
```

Agar servisda JSON API bo'lsa:

```json
"fetch": {
  "method": "json_api",
  "url": "https://api.service.com/rates",
  "path": ["data", "RUB", "rate"]
}
```

## Kurs ma'lumot manbalari

| Manba | Usul | Tavsif |
|---|---|---|
| **CBU** | JSON API | O'zbekiston MB rasmiy kursi |
| **CBR** | XML API | Rossiya MB rasmiy kursi (USD/RUB) |
| **Uzbek banklar** | HTML scraping | NBU, Kapitalbank, UZUM va boshqalar |
| **Transfer servislar** | Pending | Yubor, Avosend va boshqalar (to'ldirish kerak) |

> **Muhim:** Soxta raqam chiqarilmaydi. Agar manba ishlamasa — o'sha servis `ma'lumot yo'q` holati bilan ko'rsatiladi.

## Talablar

- Python 3.11+
- `pip install -r requirements.txt`
- BotFather dan Telegram bot token
