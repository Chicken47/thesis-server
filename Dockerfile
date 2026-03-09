FROM python:3.13-slim

# ── System: build tools + Node.js 22 ─────────────────────────────────────────
# build-essential + cmake needed to compile chroma-hnswlib (C++ extension)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        build-essential \
        cmake \
        python3-dev \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Python dependencies (own layer — invalidates only when requirements change) ─
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Node.js dependencies ──────────────────────────────────────────────────────
# PUPPETEER_SKIP_DOWNLOAD: the main scraper (screenerScraper.js) uses
# axios + cheerio and requires no browser. Skip the 170MB Chromium download.
ENV PUPPETEER_SKIP_DOWNLOAD=true
COPY scraper/package*.json ./scraper/
RUN cd scraper && npm install --omit=dev

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

EXPOSE 5000

CMD ["gunicorn", "wsgi:app", \
     "--workers", "2", \
     "--timeout", "300", \
     "--bind", "0.0.0.0:5000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
