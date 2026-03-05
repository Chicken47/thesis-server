# Indian Stock Analysis System — Project Log
*Last updated: 2026-02-27*

---

## 1. Core Analysis Pipeline (Python)

- **`main.py`** — CLI entry point; runs end-to-end analysis per ticker
- **Scraper** (`scraper/`) — Node.js/Puppeteer; scrapes Screener.in, Google Finance, Google News
- **Cache layer** (`cache/`) — persists raw scrape to `stock_cache/{TICKER}/raw_full.json`; includes price history, P&L, BS, CF, ratios, peers, shareholding, earnings call PDFs
- **RAG pipeline** (`rag/`) — per-stock ChromaDB index over earnings call transcript chunks; semantic retrieval at analysis time
- **Prompt builder** (`analysis/prompt_builder.py`) — assembles financial tables + RAG excerpts + macro/sector context into a structured CoT prompt
- **LLM analysis** (`analysis/pipeline.py`) — 5-step chain-of-thought via Ollama → parses `<stepN_output>` XML tags + JSON verdict block
- **`parse_response.py`** — standalone script: takes raw LLM response (XML tags + JSON), parses into structured `data/{TICKER}_analysis.json`; auto-detects ticker from JSON if `--ticker` not passed

---

## 2. Knowledge Base (`knowledge_base/`)

- **`macro/macro_context.md`** — Indian macroeconomic context (RBI rates, inflation, GDP, FII/DII flows, INR, crude, fiscal policy, SEBI, Nifty valuation); generated via `generateMacroPrompt.js`
- **`sectors/`** — folder for per-sector `.md` context files; populated via `generateSectorPrompt.js` (not yet filled)
- **`governance/`** — governance context (structure present)
- **`templates/`** — prompt templates

---

## 3. Scraper Scripts (`scraper/`)

- **`generateMacroPrompt.js`** — scrapes 10 macro topics from Google News India → saves `output/macro_prompt.txt` → paste into Claude → save as `macro_context.md`
- **`generateDiffPrompt.js`** — diffs old vs new news headlines per ticker → saves diff prompt; used to decide if a re-analysis is warranted
- **`generateSectorPrompt.js`** *(new)* — interactive CLI: enter sector name → fuzzy-matched against 20 predefined sector profiles → pick sub-topics by number (e.g. `0,2`) → scrapes Google News → saves:
  - `output/sector_prompt_{slug}.txt` — paste into Claude Sonnet 4.6 web
  - `knowledge_base/sectors/sector_context_{slug}.md` — placeholder at correct path

---

## 4. Web Client (`thesis-web-client/` — Next.js 16 App Router)

### Pages
- **`/`** — dashboard; lists all analysed stocks with verdict badges
- **`/analysis/[ticker]`** — full per-stock analysis page (see below)

### `/analysis/[ticker]` sections (top → bottom)
1. Sticky nav — breadcrumb + verdict badge
2. **Hero** — ticker, verdict badge, conviction meter, one-line summary, model metadata, key metrics strip (MCap, CMP, P/E, ROCE, ROE, Book Value, Div Yield, 52W High)
3. **NSE Price Chart** — real 248-point daily data from `stock_cache`; Recharts AreaChart with gradient; 52W high/low strip; 1Y return %; verdict-coloured (emerald/amber/red)
4. **AI Score Breakdown** — 4 cards: Business Quality · Financial Health · Governance · Valuation
5. **Financials** — shareholding stacked bar + tabbed tables: Annual P&L / Quarterly / Balance Sheet / Cash Flow / Ratios / Peers; Screener Pros & Cons
6. **AI Reasoning** — StepTimeline accordion (5 steps, framer-motion AnimatePresence); step 5 open by default; CalcBlock for weighted conviction calculation; **markdown rendered** (`**bold**`, `*italic*`, bullet lists)
7. Key Strengths · Key Risks · Red Flags · Invalidation Triggers
8. News Sentiment — badge + themes + analyst note
9. Watch Next Quarter
10. About the Company

### API Routes
- **`/api/analysis/[ticker]`** — reads `data/{TICKER}_analysis.json`
- **`/api/screener/[ticker]`** — reads `stock_cache/{TICKER}/raw_full.json`; extracts and cleans price data, financials, shareholding, peers, ratios, pros/cons, aboutText

### Key Components
| Component | What it does |
|---|---|
| `StockChart` | Recharts AreaChart, real NSE price data, custom tooltip, 52W ref line |
| `FinancialsSection` | Tabbed financial tables, peers table, shareholding bar |
| `StepTimeline` | framer-motion accordion, markdown rendering, CalcBlock |
| `ConvictionMeter` | SVG arc gauge 0–10 |
| `VerdictBadge` | BUY / WATCH / AVOID coloured chip |
| `BreakdownBar` | Horizontal bar for each score dimension |

---

## 5. Stock Data

### Analysed (in `data/`)
IRCTC · TCS · INFY · BHARTIARTL · JIOFIN · YESBANK · LATENTVIEW · IREDA · RELIANCE

### Cached in `stock_cache/` (raw Screener.in data)
IRCTC · TCS · INFY · BHARTIARTL · JIOFIN · YESBANK · LATENTVIEW · IREDA · RELIANCE · KPITTECH · SUZLON

---

## 6. What's Not Done Yet

- Sector `.md` files (`knowledge_base/sectors/`) — script built, content not generated yet
- `generateSectorPrompt.js` not yet run for any sector
- No live deployment (dev server only, `localhost:3000`)
- No automated re-analysis scheduler (diff prompt exists but manual)
