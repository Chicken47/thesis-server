# Indian Stock Analysis System — Technical Documentation

## Table of Contents
1. [What the system does](#1-what-the-system-does)
2. [Repository layout](#2-repository-layout)
3. [End-to-end data flow](#3-end-to-end-data-flow)
4. [Component deep-dives](#4-component-deep-dives)
   - 4.1 Scraper (Node.js)
   - 4.2 Cache layer (Python)
   - 4.3 PDF extractor
   - 4.4 RAG pipeline
   - 4.5 Prompt builder
   - 4.6 Analysis pipeline
5. [The per-stock cache folder](#5-the-per-stock-cache-folder)
6. [How to run](#6-how-to-run)
7. [Design decisions and trade-offs](#7-design-decisions-and-trade-offs)
8. [Known limitations](#8-known-limitations)

---

## 1. What the system does

Given a stock ticker (e.g. `TCS`, `INFY`, `IREDA`), the system:

1. **Scrapes** live financial data from Screener.in and Google Finance via a headless browser.
2. **Caches** all scraped data locally so subsequent runs don't re-hit the internet unless data is stale.
3. **Downloads and extracts** the latest earnings call transcript PDFs (from BSE filings).
4. **Indexes** the transcript text into a per-stock vector database (ChromaDB) for semantic search.
5. **Builds a structured prompt** that contains: financial tables, live ratios, shareholding, peer comparison, recent news, and the most relevant excerpts from the earnings call transcript retrieved via RAG.
6. **Calls a local LLM** (via Ollama) with a detailed Chain-of-Thought analysis framework covering business quality, financial health, governance, and valuation.
7. **Parses the JSON verdict** from the LLM response and prints a structured investment recommendation with conviction score, key risks, red flags, and invalidation triggers.

---

## 2. Repository layout

```
Thesis/
│
├── main.py                    # CLI entry point — all commands start here
│
├── scraper/                   # Node.js headless browser scraping layer
│   ├── index.js               # Exports all scrapers
│   ├── screenerScraper.js     # Core: scrapes Screener.in pages
│   ├── googleFinanceScraper.js# Scrapes live indices (NIFTY, SENSEX, etc.)
│   ├── googleNewsScraper.js   # Fetches recent news headlines
│   ├── search.js              # Searches Screener for a ticker
│   ├── formatter.js           # Compacts raw scrape into LLM-friendly structure
│   └── run_full_scrape.js     # CLI wrapper called by Python (via subprocess)
│
├── cache/                     # Python data persistence layer
│   ├── stock_store.py         # Manages stock_cache/{TICKER}/ directory
│   ├── pdf_extractor.py       # Downloads PDFs and extracts text via pdfplumber
│   └── narrative.py           # Converts raw financial data into narrative text chunks
│
├── rag/                       # Vector search / retrieval-augmented generation
│   ├── stock_indexer.py       # Builds and queries per-stock ChromaDB index
│   ├── retrieval.py           # High-level: calls stock_indexer, returns assembled context
│   └── ingest.py              # Builds the global knowledge-base index (sectors, governance)
│
├── analysis/                  # LLM analysis layer
│   ├── pipeline.py            # Orchestrates: RAG → prompt → LLM → parse → result
│   └── prompt_builder.py      # Assembles the full CoT prompt from all data sources
│
├── scraper_bridge.py          # Python bridge: calls Node.js scraper via subprocess
│
└── stock_cache/               # Persistent per-stock data store (auto-created)
    └── {TICKER}/
        ├── raw_full.json      # Full scrape output from Screener.in
        ├── screener_export.json  # Clean structured export of key sections
        ├── pdf_extracts.json  # Extracted text from earnings call PDFs
        ├── meta.json          # Cache freshness metadata (quarter, scraped_at)
        └── rag_index/         # ChromaDB vector database for this stock
```

---

## 3. End-to-end data flow

### Path A: `--cache-stock TICKER` (one-time setup per stock)

```
User runs: python main.py --cache-stock TCS
                │
                ▼
    [1] Full scrape (Node.js via subprocess)
        screenerScraper.js → scrapes all tabs on Screener.in page:
        • About text
        • Key ratios (PE, ROCE, ROE, Book Value, etc.)
        • Quarterly P&L (last 8 quarters)
        • Annual P&L (last 13 years)
        • Balance sheet (last 13 years)
        • Cash flows (last 12 years)
        • Historical ratios
        • Peer comparison table
        • Document links (annual reports, concall transcripts, announcements)
        • Shareholding pattern
        Output: raw_full.json (saved to stock_cache/TCS/)

                │
                ▼
    [2] PDF extraction (Python, pdfplumber)
        Reads raw_full.json → documents list
        Categorizes documents:
          "Transcript"          → concall   ← these are the BSE earnings call PDFs
          "Financial Year XXXX" → annual_report
          "Rating update..."    → skip
          press releases        → announcement (no PDF extraction)
        Takes: latest 2 concall transcripts
        Downloads PDFs from BSE, extracts text page by page
        Splits into chunks of ~3,000 chars (3 pages per chunk)
        Output: pdf_extracts.json

                │
                ▼
    [3] Build narrative chunks (cache/narrative.py)
        Converts raw_full.json sections into text paragraphs → NOT used for RAG
        Converts pdf_extracts.json into concall_text chunks  → USED for RAG
        Two categories:
          Screener chunks (10):  balance_sheet, profit_loss, cash_flows, etc.
                                 These are NOT indexed — they go directly into the prompt
          PDF chunks (N):        concall_text chunks from transcript pages
                                 These ARE indexed — the RAG retrieves these

                │
                ▼
    [4] Build ChromaDB index (rag/stock_indexer.py)
        ONLY PDF chunks are embedded and stored.
        Uses sentence-transformers model: all-MiniLM-L6-v2 (22MB, CPU-fast)
        Stores in: stock_cache/TCS/rag_index/
        Each chunk: {id, text, section="concall_text", ticker="TCS"}
```

### Path B: `--ticker TCS` (analysis run)

```
User runs: python main.py --ticker TCS
                │
                ▼
    [1] Live compact scrape (Node.js)
        Scrapes Screener.in + Google Finance for current snapshot:
        • Current price, PE, ROCE, ROE, book value
        • Last 6-8 quarters of key metrics
        • Pros/cons (Screener's automated signals)
        • Shareholding pattern
        • Market indices (NIFTY, SENSEX, Nifty IT, etc.)
        • Recent news (10 headlines from Google News)

                │
                ▼
    [2] RAG retrieval (rag/retrieval.py → rag/stock_indexer.py)
        Checks: does stock_cache/TCS/rag_index/ exist?
          YES → query the ChromaDB index
          NO  → skip, return "" (analysis proceeds with Screener data only)

        Queries 6 aspects against the vector index:
          "revenue growth profit margin operating performance"
          "cash flow quality earnings OCF capital expenditure"
          "debt borrowings balance sheet financial health leverage"
          "governance promoter shareholding pledge"
          "peer comparison industry competitors valuation"
          "management commentary outlook strategy"

        For each aspect: retrieve top-5 chunks, filter to concall_text only
        Deduplicate, sort by semantic relevance (cosine distance)
        Assemble up to 12,000 chars — truncate at newline boundary if needed
        Result: a block of the most relevant concall transcript excerpts

                │
                ▼
    [3] Load deep financial data (cache/stock_store.py)
        Reads stock_cache/TCS/raw_full.json (from the last --cache-stock run)
        This gives the 10-year P&L, balance sheet, cash flows, peer table
        Injected DIRECTLY into the prompt — not through RAG

                │
                ▼
    [4] Build analysis prompt (analysis/prompt_builder.py)

        Prompt has 3 data sections (in this order):

        ┌─────────────────────────────────────────────┐
        │  ## TCS — FINANCIAL DATA (from cache)       │  ← deep_data (raw_full.json)
        │    Revenue & Profitability table             │    10-year P&L, CAGR, OCF trend
        │    Balance Sheet table                       │    last 4 periods, net cash/debt
        │    Peer Comparison table                     │    PE, ROCE, NP growth vs peers
        ├─────────────────────────────────────────────┤
        │  ## TCS — LIVE SNAPSHOT                     │  ← snapshot (live scrape)
        │    Current ratios, quarterly trends          │
        │    Shareholding, market indices, news        │
        ├─────────────────────────────────────────────┤
        │  ## TCS — PDF RESEARCH EXCERPTS             │  ← rag_context (concall RAG)
        │    Semantically relevant paragraphs          │    Only present if RAG index exists
        │    from earnings call transcripts            │    and returned non-empty
        └─────────────────────────────────────────────┘

        Followed by: Chain-of-Thought analysis instructions (5 steps)

                │
                ▼
    [5] LLM call (analysis/pipeline.py → Ollama)
        Model: mistral:latest or gemma3:latest (whichever is available)
        Temperature: 0.3 (low — for consistent structured output)
        The LLM works through:
          Step 1: Business quality (moat, ROE/ROCE, AI disruption for IT)
          Step 2: Financial health (revenue CAGR, OPM trend, OCF/PAT, debt)
          Step 3: Governance (promoter holding, pledge, SEBI issues)
          Step 4: Valuation (PE vs sector benchmark, peer growth, PEG)
          Step 5: Synthesis (weighted conviction score → verdict)

                │
                ▼
    [6] Parse and print result
        Extracts JSON block from LLM response
        Prints: verdict (BUY/WATCH/AVOID), conviction/10, breakdown,
                summary, strengths, risks, red flags, invalidation triggers
        Saves full result to: data/TCS_analysis.json
```

---

## 4. Component deep-dives

### 4.1 Scraper (Node.js) — `scraper/screenerScraper.js`

Uses Puppeteer (headless Chrome) to navigate Screener.in. The full scrape (`fetchFullStockData`) opens one browser session and scrapes all tabs in parallel:

**Document categorization** (`getDocumentLinks`):
The scraper reads the Documents section of a Screener page and categorizes each link:

| Title pattern | Category | Action |
|---|---|---|
| `"Financial Year XXXX"`, `"Annual Report"` | `annual_report` | Available, but NOT extracted by default |
| `"Transcript"` (exact, BSE convention) | `concall` | **Downloaded and extracted** |
| `"Earnings Call Transcript"`, etc. | `concall` | **Downloaded and extracted** |
| `"Rating update..."`, `"Credit rating..."` | `skip` | Dropped entirely |
| `"Audio Recording"`, `"Audio Call"` | `audio` | Dropped (no text to extract) |
| Everything else | `announcement` | Kept as metadata, no PDF extraction |

> **Why `"Transcript"` needed special handling:** BSE filing convention for TCS (and many large-caps) titles concall transcripts simply as `"Transcript"` with no qualifier. The original categorizer only matched longer phrases like `"earnings call transcript"`, so all TCS concalls were falling through to `"announcement"` and being silently ignored.

### 4.2 Cache layer — `cache/stock_store.py`

Manages `stock_cache/{TICKER}/` with freshness detection:

- **`get_or_fetch(ticker, path, force=False)`**: Returns cached data if from the current quarter (e.g. `2026Q1`), otherwise re-scrapes. `--force` always re-scrapes.
- **Quarter freshness**: Defined as same calendar quarter as the scrape date. A cache from Jan 2026 stays fresh until April 2026.
- **Files written**: `raw_full.json` (full scrape), `meta.json` (quarter, timestamps, boolean flags for each section), `pdf_extracts.json` (PDF text), `screener_export.json` (clean structured export).

### 4.3 PDF extractor — `cache/pdf_extractor.py`

**`_effective_category(doc)`**: Re-classifies documents from the cached `raw_full.json`. Applied at extraction time so it works against already-cached data without a re-scrape:
- `announcement` + title `"transcript"` → reclassified as `concall`
- `announcement` + title contains `"rating update"` / `"credit rating"` → `skip`

**`extract_key_pdfs(docs)`**:
- Takes the latest 2 concall transcripts (reverse-chronological from Screener)
- Downloads each PDF from BSE (max 15MB, timeout 45s)
- Extracts text with `pdfplumber`, page by page (max 60 pages)
- Pages joined with `[PAGE]` markers

**`split_pdf_text_into_chunks(text, max_chunk=3000)`**:
- Groups every 3 pages into one chunk
- If a 3-page group exceeds `max_chunk` chars, splits further at newline boundaries
- Returns chunks of 100–3000 chars (discards tiny fragments)
- **Why this matters**: Dense concall Q&A pages can be 3,000+ chars each. 3 pages = 9,000+ chars. The RAG assembly budget is 12,000 chars total, so one oversized chunk would consume it all and prevent other relevant chunks from being included.

### 4.4 RAG pipeline — `rag/stock_indexer.py` + `rag/retrieval.py`

**What gets indexed**: Only `concall_text` chunks (earnings call transcript excerpts). Screener financial data is NOT indexed — it's already injected directly into the prompt and would only add noise to vector search.

**Embedding model**: `all-MiniLM-L6-v2` via `sentence-transformers`. 22MB model, runs on CPU in ~100ms per batch.

**Vector database**: ChromaDB (local, persistent). Stored at `stock_cache/{TICKER}/rag_index/`. Uses cosine similarity (`hnsw:space: cosine`).

**Retrieval flow** (`retrieve_stock_context`):
1. Query 6 pre-defined analysis aspects (one embedding + search per aspect)
2. Retrieve top-5 chunks per aspect (asking for more since all 22 chunks are concall_text, they all pass the filter)
3. Deduplicate by first-80-chars key
4. Sort all unique chunks by cosine distance (most semantically relevant first)
5. Assemble up to 12,000 chars — if a chunk is too large for remaining budget, truncate at a newline boundary

**Why `pdf_only=True` exists**: The filter ensures only `concall_text` (or `annual_report_text`) chunks pass through. Even if Screener chunks were accidentally in the index, they'd be excluded. Currently redundant since we only index PDF chunks, but kept as a safety net.

**`index_exists(ticker)`**: Checks if `stock_cache/{TICKER}/rag_index/` exists and is non-empty. Returns `False` for stocks with no concall PDFs (e.g. IREDA) → retrieval is skipped entirely → analysis proceeds with Screener data only.

### 4.5 Prompt builder — `analysis/prompt_builder.py`

Assembles the full prompt from three sources:

**Section 1 — `_format_deep_financials(raw_full.json)`**: Directly injected financial tables:
- Revenue & Profitability: 5-6 representative years, computed 5yr and 10yr revenue CAGR, OPM range, OCF/PAT quality ratio, OCF trend
- Balance Sheet: last 4 periods, net cash/debt position
- Peer Comparison: PE, ROCE, YoY profit growth, market cap, dividend yield for all peers + sector median

**Section 2 — `_format_snapshot(live scrape)`**: Current state:
- Key ratios, pros/cons, recent quarterly OPM + EPS, shareholding, market indices, 5 news headlines

**Section 3 — `_build_pdf_section(rag_context)`**: Only rendered if RAG returned non-empty string. Contains the semantically retrieved concall excerpts, labelled clearly as primary source for management commentary.

**Analysis instructions**: 5-step Chain-of-Thought framework with:
- Sector-specific benchmarks (IT, Banking, FMCG, Pharma, Auto, Energy)
- For IT sector: explicit instruction to extract attrition %, deal TCV, margin guidance, and AI commentary from PDF excerpts
- Governance scoring rules (pledge cap hierarchy)
- Verdict thresholds (conviction >7.5 = BUY, 6-7.5 = WATCH, <6 = AVOID)
- Override rules (pledge >70%, auditor resignation, SEBI fraud = AVOID regardless of score)
- JSON output schema with strict field types

### 4.6 Analysis pipeline — `analysis/pipeline.py`

Orchestrates everything for a `--ticker` or `--analyze` run:
1. Calls live scraper via `scraper_bridge.py`
2. Detects sector (ticker lookup table → about text keyword matching)
3. Calls RAG retrieval
4. Loads `raw_full.json` from cache (for deep financial tables)
5. Builds prompt
6. Calls Ollama with `temperature=0.3`, `num_predict=1500`
7. Parses JSON from response (tries multiple regex patterns, falls back to substring search)
8. Saves full result including `raw_response` and `rag_context` to `data/{TICKER}_analysis.json`

---

## 5. The per-stock cache folder

```
stock_cache/TCS/
├── meta.json
│     ticker: "TCS"
│     quarter: "2026Q1"          ← used for freshness check
│     scraped_at: ISO timestamp
│     has_pl, has_bs, has_cf, has_peers: bool flags
│     doc_count: 79, news_count: 10
│
├── raw_full.json                 ← full Screener scrape (~138KB for TCS)
│     aboutText, ratios, quartersData, annualPL, balanceSheet,
│     cashFlows, ratiosHistory, peerComparison, documents, news
│
├── screener_export.json          ← clean structured re-export (~54KB)
│     same sections, slightly cleaner key names
│
├── pdf_extracts.json             ← extracted transcript text (~289KB for TCS)
│     {url: {title, category, year, text, chunks: [str]}}
│     "text": full extracted text with [PAGE] markers
│     "chunks": list of ~3-page segments (3,000 char max each)
│
└── rag_index/                    ← ChromaDB vector database
      Contains embedded concall_text chunks only.
      TCS: 22 chunks (11 per transcript × 2 transcripts).
      Queried at analysis time via semantic search.
```

**What happens if a stock has no concall transcripts (e.g. IREDA)?**
- `pdf_extracts.json` = `{}`
- No ChromaDB index is created (`rag_index/` doesn't exist)
- `index_exists("IREDA")` returns `False`
- RAG retrieval is skipped, `rag_context = ""`
- The PDF Research Excerpts section is omitted from the prompt
- Analysis proceeds normally using Screener data only

---

## 6. How to run

### First time for a new stock

```bash
# Step 1: Build the per-stock RAG index (once per quarter)
python main.py --cache-stock TCS

# Step 2: Run analysis
python main.py --ticker TCS

# OR: run analysis directly by Screener path
python main.py --analyze /company/TCS/consolidated/
```

### Refresh after a new earnings quarter

```bash
python main.py --cache-stock TCS --force
# --force re-scrapes Screener, re-downloads transcripts, rebuilds index
```

### Stocks with no concall transcripts (PSUs, newer listings)

```bash
# Just run analysis directly — RAG is skipped automatically
python main.py --ticker IREDA
```

### Suppress ChromaDB telemetry noise

```bash
ANONYMIZED_TELEMETRY=False python main.py --ticker TCS
```

---

## 7. Design decisions and trade-offs

### Screener data goes directly into prompt, NOT through RAG

The financial tables (P&L, balance sheet, peers) are structured, complete, and always relevant — there's no benefit to vector-searching them. Routing them through RAG would mean:
- Wasting embedding compute on tabular numbers that don't benefit from semantic search
- Risk of retrieval missing a key row (e.g. "OPM" row not returned) because a different row matched better
- Polluting vector search results when querying for management commentary

**Decision**: Screener data is formatted directly into the prompt by `prompt_builder.py`. RAG budget is reserved exclusively for unstructured PDF text (concall transcripts) that benefits from semantic retrieval.

### Only latest 2 concall transcripts are extracted

More transcripts = more context, but also more noise and longer indexing time. Two transcripts (typically the last two quarters) give:
- Current quarter management commentary
- One quarter back for trend comparison
- ~22 concall chunks for TCS (~44KB of transcript text before budgeting)

Annual reports are skipped by default — they're 200–400 page PDFs, mostly boilerplate legal text. The concall transcripts contain the actionable management commentary the LLM needs.

### ChromaDB over a simpler approach

For a single stock with 22 chunks, even linear scan would be fast enough. ChromaDB is used because:
- It persists to disk (no re-embedding on every run)
- It's already a standard RAG tool
- It allows future expansion to larger corpora without architectural change

### Local LLM (Ollama) over API

The analysis prompt is ~15,000 chars and produces ~1,500 token output. Running this repeatedly for multiple stocks would be expensive via API. Local Ollama allows unlimited analysis runs at zero cost, with models like `gemma3:latest` (4B params) being sufficient for structured output with a detailed prompt.

---

## 8. Known limitations

| Limitation | Detail |
|---|---|
| **No concall = no RAG** | PSUs and newer listings (IREDA, recent IPOs) often don't have BSE transcript filings. Analysis uses Screener data only. |
| **Transcript title matching** | The scraper detects concalls by title (`"Transcript"`). If a company files transcripts under a different title convention, they'll be missed until the pattern is added to the categorizer. |
| **Max 2 transcripts** | Only the 2 most recent concall transcripts are downloaded. Historical management commentary beyond 2 quarters is not in the RAG index. |
| **Ollama model quality** | `mistral:latest` / `gemma3:latest` at 4-7B params can occasionally produce malformed JSON or miss numerical precision. The `_extract_json` parser has multiple fallback patterns to handle this. |
| **ChromaDB telemetry errors** | Harmless API mismatch in the installed ChromaDB version. Set `ANONYMIZED_TELEMETRY=False` to suppress. |
| **Cache staleness** | Cache freshness is quarter-based. If a company reports mid-quarter or there's a major announcement, you need `--force` to get fresh data. |
| **Screener login not required** | The scraper works on public Screener pages. Premium data (e.g. detailed segment P&L) that requires login is not captured. |
