# Valyuta Kurslari Telegram Boti

Rossiya ↔ O'zbekiston pul o'tkazmalari (RUB ↔ UZS) kurslarini real vaqtda
ko'rsatadigan Telegram bot. Banklarning kurslarini o'z saytlari/API'lari va
aggregatorlardan yig'adi, Universal bank bilan taqqoslaydi.

## Xususiyatlar

- **RUB → UZS** va **UZS → RUB** tugmalari
- **Har bank o'z manbaidan** (real API yoki sayt), aggregator (bank.uz, themoney.uz, kurs.uz) — zaxira
- **Universal bank bilan farq** — har bank yonida qavs ichida (`-5.84`)
- **O'zgarish belgisi** — ▲/▼ oxirgi yangilanishdan farq
- **Avtomatik xabar** — kurs o'zgarganda barcha foydalanuvchiga (flood'ga chidamli)
- **Hech qachon bo'sh qolmaydi** — manba ishlamasa, oxirgi ma'lum kurs ko'rsatiladi

## O'rnatish

```bash
# 1. Virtual muhit
python -m venv venv
source venv/bin/activate       # Linux/Mac
# venv\Scripts\activate        # Windows

# 2. Paketlar
pip install -r requirements.txt

# 3. Playwright brauzeri (JS bilan ishlovchi bank saytlari uchun ZARUR)
playwright install chromium
# Linux serverda tizim kutubxonalari ham kerak:
playwright install-deps chromium      # yoki: sudo apt install libnss3 libatk1.0-0 libgbm1 ...

# 4. Sozlamalar
cp .env.example .env
# .env ni oching va BOT_TOKEN ni to'ldiring (@BotFather dan)

# 5. Ishga tushirish
python bot.py
```

> ⚠️ **Playwright brauzeri o'rnatilmasa**, JS bank saytlari (SQB, Universal, Agrobank)
> aggregatordan olinadi — bot baribir ishlaydi, lekin o'z saytidan emas.

## .env sozlamalari

| O'zgaruvchi | Tavsif | Standart |
|---|---|---|
| `BOT_TOKEN` | @BotFather dan olingan token — **MAJBURIY** | — |
| `CHANNEL_USERNAME` | Kanal (masalan `@mychannel`) | — |
| `SUPPORT_URL` | Qo'llab-quvvatlash havolasi | — |
| `AUTO_INTERVAL` | Kurslarni necha soniyada tekshirish | 600 |
| `NOTIFY_MIN_INTERVAL` | Kamida shu oraliqda bir xabar | 3600 |
| `REFRESH_TIMEOUT` | Bitta yangilanish uchun umumiy vaqt chegarasi | 60 |
| `SOURCE_TIMEOUT` | Bitta manba uchun vaqt chegarasi | 10 |
| `JS_RENDER_TIMEOUT` | Playwright (brauzer) uchun vaqt chegarasi | 40 |
| `RATE_BAND_LO` / `RATE_BAND_HI` | Mantiqsiz kurslarni filtrlash (CBU atrofida) | 0.80 / 1.08 |
| `MAX_STALE_SECONDS` | Oxirgi ma'lum kursni saqlash muddati | 86400 |

> ⚠️ `REFRESH_TIMEOUT` ni juda kichik (masalan 1) qilmang — banklar ulgurmay
> ro'yxatdan tushib qoladi. 60 tavsiya etiladi.

## Serverga chiqarish (Linux, systemd)

`/etc/systemd/system/kursbot.service`:

```ini
[Unit]
Description=Valyuta Kurslari Telegram Bot
After=network-online.target

[Service]
WorkingDirectory=/home/user/bot-file2
ExecStart=/home/user/bot-file2/venv/bin/python bot.py
Restart=always
RestartSec=10
User=user

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kursbot
sudo systemctl status kursbot       # holatni ko'rish
journalctl -u kursbot -f            # loglarni kuzatish
```

`Restart=always` — bot qulasa ham avtomatik qayta ishga tushadi.

## Loyiha tuzilmasi

```
bot-file2/
├── bot.py          — Telegram bot (aiogram 3.x): tugmalar, xabar, bildirishnoma
├── fetcher.py      — Kurs yig'ish: API'lar, aggregatorlar, Playwright, kesh
├── db.py           — SQLite: oldingi kurslar (▲/▼ uchun) va tarix
├── sources.json    — Banklar/servislar konfiguratsiyasi
├── rates.db        — SQLite ma'lumotlar bazasi (avtomatik yaratiladi)
├── users.json      — Obunachilar ro'yxati (avtomatik)
├── requirements.txt
├── .env            — Maxfiy sozlamalar (git'ga TUSHMAYDI)
├── .env.example    — Namuna
├── BANK_APIS.md    — Barcha ma'lumot manbalari (qaysi bank qayerdan)
└── README.md
```

## Ma'lumot manbalari (qisqacha)

| Tur | Manba |
|---|---|
| **Real API** | CBU, CBR, Wise, Koronapay, Hamkorbank, Tenge, Hayot |
| **O'z sayti (scrape)** | Aloqa, Anor, Ipoteka, Trast, Turon, Poytaxt, OFB |
| **Playwright (JS sayt)** | SQB, Universal, Agrobank |
| **Aggregator (zaxira)** | bank.uz, themoney.uz, kurs.uz — qolgan barcha banklar |

To'liq ro'yxat va texnik tafsilotlar: [BANK_APIS.md](BANK_APIS.md)

## Yangi bank qo'shish

`sources.json` ga yozuv qo'shing. `fetch.method` turlari:

| Method | Ma'no |
|---|---|
| `aggregator` | Ma'lumot bank.uz/themoney.uz dan (faqat toza nom) |
| `html_scrape` | Bankning o'z sahifasidan jadval o'qish (`url`, `source_label`) |
| `js_render` | JS bilan ishlovchi sahifa (Playwright orqali) |
| `hamkorbank_api` / `tengebank_api` / `hayotbank_api` | Bankning o'z JSON API'si |

> **Hech qachon API URL'ni taxmin qilmang** — jonli tekshiring. Aks holda 404 yoki
> noto'g'ri ma'lumot. Aggregatorlar baribir hamma bankni qoplaydi.

## Talablar

- Python 3.11+
- `pip install -r requirements.txt` + `playwright install chromium`
- @BotFather dan Telegram bot token
