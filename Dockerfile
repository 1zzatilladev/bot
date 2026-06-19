# Yengil image — Telegram bot 24/7 ishlashi uchun.
# Eslatma: Playwright brauzeri (js_render banklar) bu yengil image'ga KIRMAYDI.
# SQB/Universal/Agrobank avtomatik aggregatordan olinadi (bot baribir to'liq ishlaydi).
FROM python:3.12-slim

WORKDIR /app

# Tizim kutubxonalari (lxml va h.k. uchun)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Polling bot — port kerak emas
CMD ["python", "bot.py"]
