# Bot ma'lumot manbalari — barcha API'lar bir joyda

> Tekshirilgan sana: 2026-06-18. Har bir manzil **jonli so'rov yuborib** sinab ko'rilgan.
> Bu yerda faqat **haqiqatan ishlaydigan** manbalar bor — taxmin yo'q.

## ⚠️ Muhim haqiqat

O'zbekiston banklarining **ko'pchiligida ochiq (public) API yo'q**. Saytlari JavaScript
bilan ishlaydi, Cloudflare bloklaydi yoki API'lari yopiq. Tekshirilgan natija:

| Bank | Sinalgan endpoint | Natija |
|------|-------------------|--------|
| Kapitalbank | `/uz/welcome/getCourseList/` | ❌ 403 (Cloudflare bloklaydi) |
| NBU | `/exchange-rates/json/` | ❌ Timeout (javob bermaydi) |
| Hamkorbank | `/api/v1/exchange-rates/` | ❌ 404 |
| Ipoteka, SQB, Infin, Anor, Asaka, Davr, Ipak... | turli `/api/...` | ❌ 404 / DNS xato |

Shu sabab bot **aggregator saytlardan** foydalanadi — bu eng ishonchli usul.

---

## ✅ 1. Ishlaydigan rasmiy API'lar (to'g'ridan-to'g'ri)

| Manba | Turi | URL | Izoh |
|-------|------|-----|------|
| **CBU** (O'zbekiston MB) | JSON | `https://cbu.uz/uz/arkhiv-kursov-valyut/json/` | Rasmiy kurs, eng barqaror |
| **CBR** (Rossiya MB) | XML | `https://www.cbr.ru/scripts/XML_daily.asp` | Rossiya rasmiy kursi |
| **Wise** | JSON | `https://wise.com/rates/live?source=RUB&target=UZS` | Bozor (mid-market) kursi |
| **Koronapay** | JSON | `https://koronapay.com/transfers/online/api/transfers/tariffs` | O'tkazma kursi (parametr bilan) |
| **Hamkorbank** ⭐ | JSON | `https://api-dbo.hamkorbank.uz/webflow/v1/exchanges` | Bankning O'Z API'si! `buying_rate`/`selling_rate` ×100 (tiyin) |

**Hamkorbank API:** `data[]` massivida har valyuta. `currency_char="RUB"` ni top,
`buying_rate`/100 = RUB_UZS, `selling_rate`/100 = sell (UZS_RUB). `sb_course`/100 = CBU.

### Arxitektura: bank API = asosiy, aggregator = zaxira
Bankning o'z API'sidan kelgan kurs `direct: True` deb belgilanadi va aggregator uni
**bosib o'tmaydi**. Agar bank API ishlamasa, kurs aggregatordan to'ldiriladi (zaxira).
Yangi bank API topilsa, shu naqsh bo'yicha qo'shiladi (`_fetch_<bank>_api` + `direct:True`).

**Koronapay parametrlari:** `sendingCountryId=RUS`, `sendingCurrencyId=810`,
`receivingCountryId=UZB`, `receivingCurrencyId=860`, `receivingAmount=1000000`,
`receivingMethod=cash`, `paymentMethod=debitCard`.
`Origin` + `Referer: https://koronapay.com` header kerak.

---

## ✅ 2. Aggregatorlar — BARCHA banklar shu yerdan (asosiy manba)

Bu saytlar barcha banklarning kurslarini bir joyda to'playdi. Botning bank
ma'lumotlari **100% shulardan** keladi.

| Aggregator | URL | Nima beradi |
|------------|-----|-------------|
| **bank.uz** | `https://bank.uz/uz/currency` | `#best_RUB` blokidan ~19 bank buy/sell |
| **themoney.uz** | `https://themoney.uz/ruble-exchange-rate/` | JSON-LD'dan ~21 bank, + Universal bank |
| **kurs.uz** | `https://kurs.uz/` | Zaxira (qisqa timeout) |

**Jami qoplanadigan banklar (23 ta):** Universal bank, NBU, Agrobank, SQB, Ipoteka,
Asaka, Aloqabank, Turonbank, BRB, Kapitalbank, Hamkorbank, Ipak Yo'li, Orient Finans,
Asia Alliance, Trastbank, Infinbank, Anor, Garant, Hayot, Poytaxt, Octobank, Tenge,
Madad Invest.

---

## ⭐ O'Z SAYTIDAN olinadigan banklar (foydalanuvchi bergan URL'lar, 2026-06-18)

Bu banklar endi **o'z rasmiy sahifasidan** kursni oladi (`direct: True`),
aggregator faqat zaxira. Jadval ustunlari: [valyuta, BUY, SELL, CBU].

| Bank | Manba (o'z sayti) |
|------|-------------------|
| **Hamkorbank** | `https://api-dbo.hamkorbank.uz/webflow/v1/exchanges` (JSON API) |
| **Aloqabank** | `https://aloqabank.uz/en/services/exchange-rates/` |
| **Anor Bank** | `https://anorbank.uz/en/about/exchange-rates/` |
| **Ipoteka Bank** | `https://www.ipotekabank.uz/en/private/services/currency/` |
| **Trastbank** | `https://trustbank.uz/en/services/exchange-rates/` |
| **Turonbank** | `https://turonbank.uz/en/services/exchange-rates/` |
| **Poytaxt Bank** | `https://poytaxtbank.uz/ru/services/exchange-rates/` |

> Eslatma: bu sahifalardagi RUB **buy** kursi past (80-110) — bu banklarning
> haqiqiy **naqd** sotib olish kursi (CBU ustuni bilan tasdiqlangan), xato emas.

### Hali JS/blok sabab o'z saytidan olinmaydigan (aggregator ishlatadi):
- **Kapitalbank** — 403 Cloudflare bloklaydi
- **NBU** — server timeout
- **aab, Octobank, Infinbank** — `fondbozori.uz/usx/` widjeti (JS + nonce auth)
- **Ipak Yo'li, Universal** — Nuxt.js (JS-rendered, `__NUXT__`)
- **Agro, Asaka, Hayot, Tenge, Davr, SQB, Garant, mkbank, BRB, Madad, Orient** — JS yoki noaniq jadval

Bularni o'z saytidan olish uchun **Playwright** (brauzer) kerak — alohida ish.

---

## 📋 3. HAR BIR BANK — jonli tekshirilgan holat (2026-06-18)

Har bir bank o'z saytidan / API'sidan **jonli** sinaldi. Natija:

| Bank | O'z manbai holati | Xulosa |
|------|-------------------|--------|
| **Hamkorbank** | ✅ `api-dbo.hamkorbank.uz/webflow/v1/exchanges` | **O'Z API'si ulandi** |
| **Asia Alliance** | ✅ `aab.uz` HTML dan toza o'qiladi (154/166) | O'z saytidan olsa bo'ladi |
| Kapitalbank | ❌ 403 — Cloudflare butunlay bloklaydi | API yo'q |
| NBU | ❌ Timeout — server javob bermaydi | API yo'q |
| SQB | ❌ `/services/exchange-rates/` 404 | API yo'q |
| Orient Finans (ofb) | ⚠️ `ofb.uz` dan buy noto'g'ri (120), sell ok | Ishonchsiz |
| Trastbank | ⚠️ buy noto'g'ri (110), sell ok (173) | Ishonchsiz |
| Garant | ⚠️ faqat 165.3 (rasmiy kurs), sell yo'q | Ishonchsiz |
| Ipak Yo'li | ⚠️ RUB bor lekin jadval JS bilan | Ishonchsiz |
| BRB | ⚠️ sahifa bor (`/valyutalar-kursi`) lekin extract yo'q | Ishonchsiz |
| Asaka, Agrobank, Aloqabank, Turonbank, | ❌ bosh sahifada kurs yo'q (JS) | API yo'q |
| Hayot, Poytaxt, Universal, Anor, Ipoteka, | ❌ JS-rendered yoki API yo'q | API yo'q |
| Infin, Octobank, Tenge, Madad | ❌ JS-rendered yoki API yo'q | API yo'q |

**Xulosa:** 23 ta bankdan faqat **1 tasida** (Hamkorbank) ochiq JSON API bor.
Bittasini (Asia Alliance) o'z saytidan o'qish mumkin, lekin u ham mo'rt (HTML o'zgarsa buziladi).
Qolgan ~21 tasida **texnik jihatdan ochiq API umuman yo'q** — bu banklarning o'zida
yo'q, kod bilan hal qilib bo'lmaydi.

**Yagona qolgan yo'l (JS saytlar uchun):** brauzer avtomatlashtiruvi (Playwright —
venv'da o'rnatilgan). Har bank sahifasini brauzerda ochib, JS yuklanganidan keyin
kursni o'qiydi. Sekinroq va og'irroq, lekin "har bank o'z saytidan" bo'ladi.

---

## 📋 4. Bank → manba jadvali (umumiy)

| Bank | O'z API'si | Ma'lumot manbai |
|------|-----------|-----------------|
| CBU (rasmiy) | ✅ bor | cbu.uz JSON |
| Wise | ✅ bor | wise.com JSON |
| Koronapay | ✅ bor | koronapay.com JSON |
| Universal bank | ❌ yo'q | themoney.uz (taqqoslash asosi) |
| NBU, Agrobank, SQB, Ipoteka, Asaka, | ❌ yo'q | bank.uz / themoney.uz |
| Aloqabank, Turonbank, BRB, Kapitalbank, | ❌ yo'q | bank.uz / themoney.uz |
| Hamkorbank, Ipak Yo'li, Orient Finans, | ❌ yo'q | bank.uz / themoney.uz |
| Asia Alliance, Trastbank, Infinbank, Anor, | ❌ yo'q | bank.uz / themoney.uz |
| Garant, Hayot, Poytaxt, Octobank, Tenge, Madad | ❌ yo'q | bank.uz / themoney.uz |

---

## 🔧 Texnik joylashuvi (kodda)

- **`sources.json`** — barcha manbalar ro'yxati. `fetch.method`:
  - `cbu_api`, `wise_api`, `koronapay_api` — to'g'ridan-to'g'ri API
  - `aggregator` — ma'lumot bank.uz/themoney.uz dan keladi
- **`fetcher.py`** — `_fetch_cbu`, `_fetch_cbr`, `_fetch_wise_api`,
  `_fetch_koronapay_api`, `_fetch_bankuz_rub`, `_fetch_themoney_rub`, `_fetch_kursuz_rub`
- **Sozlamalar (`.env`):** `FETCH_TIMEOUT`, `SOURCE_TIMEOUT`, `REFRESH_TIMEOUT`,
  `RATE_BAND_LO`, `RATE_BAND_HI` (mantiqsiz kurslarni filtrlash)

---

## ❓ Yangi bank qo'shish

1. Avval shu bank **bank.uz yoki themoney.uz da bormi** tekshiring — bo'lsa, faqat
   `sources.json` ga `"method": "aggregator"` yozuv qo'shing va `fetcher.py` dagi
   `_BANK_KEY_MAP` ga nom moslamasini qo'shing.
2. Agar bankning **haqiqiy ishlaydigan API'si** topilsa (jonli tekshirib), uni
   alohida `fetch.method` sifatida qo'shish mumkin.
3. **Hech qachon API URL'ni taxmin qilmang** — 404 beradi yoki noto'g'ri ma'lumot.
