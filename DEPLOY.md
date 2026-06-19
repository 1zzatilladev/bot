# Botni tekin serverga qo'yish — qadam-baqadam

> Men (Claude) sizning nomingizdan akkaunt ochib deploy qila olmayman (login/parol/karta
> kerak). Lekin barcha fayllar tayyor — quyidagi qadamlar bilan o'zingiz 5-10 daqiqada qo'yasiz.

## ⚠️ AVVAL: bot tokenini yangilang
Eski token GitHub'da ochiq bo'lgan. @BotFather → `/revoke` → yangi token oling.
Yangi tokenni serverda **environment variable** sifatida kiritasiz (`.env` serverga ketmaydi).

---

## Variant 1 — Railway (eng oson, GitHub orqali) ⭐

1. https://railway.app ga kiring, GitHub bilan ro'yxatdan o'ting
2. **New Project → Deploy from GitHub repo** → `1zzatilladev/bot` ni tanlang
3. Railway `Dockerfile`ni o'zi topadi va quradi
4. **Variables** bo'limiga o'ting va qo'shing:
   - `BOT_TOKEN` = `<yangi tokeningiz>`
   - (ixtiyoriy) `CHANNEL_USERNAME`, `SUPPORT_URL`
5. **Deploy** — tayyor! Loglarni "Deployments" da ko'rasiz.

> Railway oyiga ~$5 tekin kredit beradi — kichik bot uchun yetadi.

---

## Variant 2 — Koyeb (kartasiz tekin)

1. https://koyeb.com → GitHub bilan kiring
2. **Create Service → GitHub →** `1zzatilladev/bot`
3. Builder: **Dockerfile**
4. **Environment variables**: `BOT_TOKEN` = `<token>`
5. Instance: **Free** → Deploy

---

## Variant 3 — Fly.io (haqiqiy tekin, doimiy)

```bash
# 1. flyctl o'rnating: https://fly.io/docs/flyctl/install/
# 2. Kiring
fly auth login
# 3. Loyiha papkasida (fly.toml tayyor)
fly launch --no-deploy        # app nomini so'raydi
# 4. Tokenni maxfiy qo'shing
fly secrets set BOT_TOKEN=<yangi_token>
# 5. Deploy
fly deploy
fly logs                       # kuzatish
```

---

## Variant 4 — Oddiy VPS / VM (to'liq nazorat)

Agar sizda VPS bo'lsa (Oracle Cloud "Always Free" VM tekin, doimiy):
`README.md` dagi **systemd** bo'limiga qarang — `Restart=always` bilan 24/7 ishlaydi,
va Playwright brauzerini ham o'rnatib, JS banklarni ham o'z saytidan olishingiz mumkin.

---

## Muhim eslatmalar

- **Token .env'da emas, server "Variables/Secrets"da** bo'ladi. `.env` GitHub'ga ketmaydi (gitignore).
- **Playwright banklari** (SQB, Universal, Agrobank): Docker (Variant 1-3) yengil bo'lgani uchun
  ular **aggregatordan** olinadi — bot to'liq ishlaydi, faqat bu 3 tasi o'z saytidan emas.
  To'liq (Playwright bilan) kerak bo'lsa — Variant 4 (VPS) ishlating.
- **rates.db / users.json** — Docker'da har deploy'da yangilanadi (kichik muammo emas:
  obunachilar /start bosganda qayta qo'shiladi, kurslar qayta yig'iladi). Doimiy saqlash
  kerak bo'lsa, platformaning "Volume" funksiyasidan foydalaning.
