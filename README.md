# VERDIKT — Backend

The analysis engine for VERDIKT. Takes a stock ticker, scrapes all public financial data, reads earnings call transcripts, and produces a structured investment verdict using Claude Sonnet.

---

## What it does, step by step

1. **Scrape** — Puppeteer opens Screener.in and pulls the full financial profile for the stock: P&L, balance sheet, cash flow, ratios, shareholding, and more. Google Finance is used for recent news.

2. **Transcripts** — Downloads the latest two earnings call PDFs from the BSE website and extracts the text.

3. **RAG index** — Chunks the transcript text and builds a local vector index (ChromaDB). At analysis time, the most relevant passages are retrieved and injected into the prompt as supporting context.

4. **Prompt** — A detailed sector-aware prompt is assembled. Financial tables go in directly; transcript excerpts come via RAG. The prompt instructs the model to reason through 7 steps before producing a final JSON verdict.

5. **Claude** — The prompt is sent to Claude Sonnet with extended thinking enabled (10K token reasoning budget). The model works through business quality, financial health, governance, valuation, and macro before deciding.

6. **Parse** — The response is parsed into a clean structured record: verdict, conviction score, breakdown, strengths, risks, red flags, entry price zones, market narrative assessment, and more.

7. **Save** — Everything is written to the Neon Postgres database. The frontend picks it up from there.

---

## API endpoints

The Flask server exposes a small set of endpoints. The Next.js frontend calls these — they're not meant to be hit directly by users.

| Endpoint | What it does |
|---|---|
| `POST /api/analyze` | Start an analysis job for a ticker (runs in background) |
| `GET /api/job/<job_id>` | Check status of a running job |
| `GET /api/screener-data/<ticker>` | Scrape and return live financial data |
| `GET /api/rag-status/<ticker>` | Check whether the RAG index is built |
| `POST /api/admin/purge/<ticker>` | Clear cached data for a stock |

---

## Key files

| File | What it is |
|---|---|
| `wsgi.py` | Entry point — run this with gunicorn in production |
| `analysis/pipeline.py` | Orchestrates the full scrape → RAG → prompt → Claude → parse → save flow |
| `analysis/prompt_builder.py` | Builds the prompt — the most complex file; contains all scoring rules and sector benchmarks |
| `api/db.py` | All database reads and writes |
| `parse_response.py` | Standalone CLI to re-parse a saved raw Claude response |
| `scraper/` | Node.js Puppeteer modules (called via a Python bridge) |
| `cache/pdf_extractor.py` | Downloads and extracts BSE concall PDFs |
| `rag/stock_indexer.py` | Builds and queries the ChromaDB vector index |
| `migrations/` | One-off database migration scripts |

---

## Verdict logic

The model outputs a conviction score from 0–10, weighted across four dimensions:

| Dimension | Weight |
|---|---|
| Business Quality | 50% |
| Financial Health | 20% |
| Governance | 20% |
| Valuation | 10% |

Macro conditions add or subtract up to 0.5 points. Final verdict: **BUY** (>7.5), **WATCH** (6.0–7.5), **AVOID** (<6.0). Certain red flags (fraud investigation, auditor resignation, high promoter pledge) can override to AVOID regardless of score.

---

## Sectors supported

IT Services, Banking & NBFCs, Energy & Refining, Pharma, Auto & Auto Ancillaries, FMCG — each with its own set of benchmarks baked into the prompt.

---

## Environment variables

```
ANTHROPIC_API_KEY=
DATABASE_URL=           # Neon Postgres connection string
STOCK_CACHE_DIR=        # Where scraped data and RAG indexes are stored (default: stock_cache/)
ALLOWED_ORIGINS=        # Comma-separated list of frontend URLs for CORS
PORT=5000
```

---

## Running locally

```bash
pip install -r requirements.txt
gunicorn wsgi:app --timeout 300 --workers 1
```

The scraper also needs Node.js installed (`cd scraper && npm install`).
