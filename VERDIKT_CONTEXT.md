# VERDIKT — Full Project Context

## What This Is

**Verdikt** is an AI-powered Indian equity analysis platform. It scrapes financial data for NSE/BSE listed stocks, runs a 7-step chain-of-thought analysis using Claude Sonnet 4.6 with extended thinking, and produces a structured investment verdict (BUY / WATCH / AVOID) with a conviction score (0–10), breakdown, strengths, risks, and invalidation triggers. It is not SEBI-registered; it is a research tool for serious retail investors.

---

## Tech Stack

### Backend (Python)
- **Python 3.11** — Flask API (`wsgi.py`, `api/app.py`), gunicorn, port 5000, 300s timeout
- **Puppeteer / Node.js** — headless scraper (`scraper/`) → Screener.in full scrape
- **ChromaDB + all-MiniLM-L6-v2** — RAG index for earnings call transcript chunks
- **Claude Sonnet 4.6** — `claude-sonnet-4-6` via `anthropic` Python SDK, extended thinking
- **PostgreSQL (Neon)** — primary DB accessed from both Flask (`psycopg2`) and Next.js (`@neondatabase/serverless`)

### Frontend (Next.js)
- **Next.js 15** (App Router, `force-dynamic` on most pages), TypeScript, Tailwind v4
- **Stack Auth** — authentication (`@stackframe/stack`), session cookies
- **Framer Motion** — animations throughout
- **Recharts** — stock price chart (`StockChart.tsx`)
- **Deployment**: Vercel (frontend) + separate server (Flask backend)

---

## Repository Structure

```
/
├── analysis/
│   ├── pipeline.py          # Core: RAG → prompt → Claude → parse → save
│   └── prompt_builder.py    # 720-line prompt constructor, sector-specific rules
├── api/
│   ├── app.py               # Flask factory, 4 blueprints
│   ├── db.py                # Neon/psycopg2 — all DB writes (analyses, jobs, stocks)
│   ├── jobs.py              # Background job runner (scrape + analyze)
│   └── routes/              # stocks, analyses, jobs, admin blueprints
├── cache/
│   ├── stock_store.py       # Quarter-aware cache freshness
│   └── pdf_extractor.py     # Downloads & extracts BSE concall PDFs
├── rag/
│   ├── stock_indexer.py     # ChromaDB index builder + retrieval
│   └── retrieval.py         # assemble RAG context (stock + sector + governance + macro + framework)
├── scraper/                 # Node.js Puppeteer modules
│   ├── screenerScraper.js   # Full Screener.in scrape
│   └── googleFinanceScraper.js
├── scraper_bridge.py        # Python ↔ Node.js bridge
├── main.py                  # CLI: --cache-stock, --ticker, --analyze, --build-index
├── wsgi.py                  # Gunicorn entry point
├── thesis-web-client/       # Next.js frontend
│   └── src/
│       ├── app/
│       │   ├── page.tsx                    # Landing page
│       │   ├── dashboard/page.tsx          # Stocks grid
│       │   ├── analysis/[ticker]/page.tsx  # Per-stock analysis page (server component)
│       │   ├── watchlist/page.tsx
│       │   ├── admin/page.tsx              # Admin dashboard
│       │   ├── admin/checklist/            # Stock checklist (prep steps before analysis)
│       │   └── api/                        # Next.js API routes (see below)
│       ├── components/
│       │   ├── VerdictCard.tsx             # Main verdict display
│       │   ├── AnalysisTabs.tsx            # Tab bar: Business / Financial / Governance / Valuation / Outlook
│       │   ├── ChatSection.tsx             # Chat UI (textarea, messages, streaming)
│       │   ├── FloatingChat.tsx            # Floating chat panel (slide-up overlay)
│       │   ├── SelectionTooltip.tsx        # "Add to Chat" button on text selection
│       │   ├── Abbr.tsx                    # Inline abbreviation with hover tooltip (glossary)
│       │   ├── AbbrText.tsx               # Wraps text, auto-detects abbreviations
│       │   ├── StockChart.tsx             # Recharts price chart
│       │   ├── AnalyzeFlow.tsx            # Trigger analysis + job polling UI
│       │   ├── ConvictionMeter.tsx        # Animated conviction score display
│       │   ├── BreakdownBar.tsx           # Conviction breakdown bars
│       │   ├── FinancialsSection.tsx      # P&L / Balance Sheet / Cash Flow tables
│       │   ├── CollapsibleFinancials.tsx  # Wrapper with expand/collapse
│       │   ├── NarrativePanel.tsx         # Market vs VERDIKT POV panel
│       │   ├── ReasoningPanel.tsx         # Claude extended thinking reveal
│       │   └── landing/                   # Landing page sections
│       ├── lib/
│       │   ├── db.ts                      # All Next.js DB reads (Neon serverless)
│       │   ├── types.ts                   # StockAnalysis, ScreenerData, Verdict types
│       │   └── auth/                      # syncUser, isAdmin, getUserId
│       └── app/globals.css               # CSS vars, Tailwind v4 theme, light/dark tokens
```

---

## Analysis Pipeline (How It Works)

1. **Scraper** (Node.js / Puppeteer) scrapes Screener.in → `stock_cache/{TICKER}/raw_full.json` (~138KB)
2. **PDF extraction** → BSE latest 2 concall PDFs → chunked text → `pdf_extracts.json`
3. **RAG indexing** (ChromaDB, `all-MiniLM-L6-v2`) → `stock_cache/{TICKER}/rag_index/`
4. **pipeline.py `analyze_stock()`**:
   - Retrieves RAG context (concall excerpts, sector benchmarks, governance rules, macro, framework docs)
   - Loads deep financial data (P&L, balance sheet, cash flows, peers) directly — NOT via RAG
   - Fetches live news via Bing RSS
   - Builds prompt via `prompt_builder.py`
   - Calls `client.messages.create(model="claude-sonnet-4-6", thinking={"type":"enabled","budget_tokens":THINKING_BUDGET}, max_tokens=THINKING_BUDGET+6000)`
   - `THINKING_BUDGET` defaults to 10,000, configurable via env var `THINKING_BUDGET=7000`
   - Temperature is locked at 1.0 (API requirement for extended thinking)
   - Captures `response.usage.input_tokens` and `output_tokens` for cost tracking
   - Saves thinking to `stock_cache/{TICKER}/latest_thinking.txt`
   - Parses 7 XML step tags + JSON block from response
5. **Saves to Neon** via `api/db.py save_analysis()` — includes `input_tokens`, `output_tokens`

---

## Claude API Usage

### Analysis (heavy)
- **Model**: `claude-sonnet-4-6`
- **Extended thinking**: enabled, `budget_tokens` = 10,000 (configurable)
- **max_tokens**: `THINKING_BUDGET + 6000` (always leaves 6K for visible output)
- **Typical cost**: ~$0.26/analysis ($0.017 input + $0.15 thinking + $0.09 output)
- **Typical time**: 2–4 minutes

### Chat (lightweight)
- **Model**: `claude-haiku-4-5-20251001`
- **max_tokens**: 600
- **Streaming**: yes (ReadableStream via Next.js API route)
- **Prompt caching**: system prompt uses `cache_control: { type: "ephemeral" }`
- **Context**: verdict + conviction breakdown + key strengths/risks + key ratios
- **Restricted to**: Pro/ProMax users only

---

## Output Format (Analysis JSON)

```json
{
  "verdict": "BUY|WATCH|AVOID",
  "conviction": 7.4,
  "conviction_breakdown": {
    "business_quality": 7,
    "financial_health": 7,
    "governance": 8,
    "valuation": 7
  },
  "summary": "250-char max. verdict + deciding factor + key strength + key risk + valuation context",
  "key_strengths": ["..."],
  "key_risks": ["..."],
  "red_flags": ["..."],
  "invalidation_triggers": ["specific failure conditions that break the thesis"],
  "watch_for_next_quarter": "...",
  "news_sentiment": {
    "overall": "bullish|neutral|bearish",
    "key_themes": ["..."],
    "note": "..."
  }
}
```

### Verdict Rules
- Conviction > 7.5 → BUY
- Conviction 6.0–7.5 → WATCH
- Conviction < 6.0 → AVOID
- OVERRIDE to AVOID regardless of score: pledge >70%, auditor resignation, SEBI fraud
- Weighted: Business Quality ×0.5, Financial Health ×0.2, Governance ×0.2, Valuation ×0.1, Macro ±0.5 max

---

## Database Schema (Key Tables)

**Neon PostgreSQL** — accessed from Next.js via `@neondatabase/serverless` and from Flask via `psycopg2`.

### `analyses`
```
id UUID PK
stock_symbol TEXT
model_used TEXT
verdict TEXT
conviction NUMERIC
conviction_breakdown JSONB
summary TEXT
key_strengths TEXT[]
key_risks TEXT[]
red_flags TEXT[]
invalidation_triggers TEXT[]
watch_for_next_quarter TEXT
news_sentiment JSONB
step_outputs JSONB          -- {step1: "...", step2: "...", ...step5}
sector TEXT
raw_response JSONB          -- {text, rag_context_length}
market_vs_verdikt JSONB
input_tokens INTEGER        -- added in migration: add_token_columns.sql
output_tokens INTEGER
created_at TIMESTAMPTZ
```

### `stocks`
```
id UUID PK
symbol TEXT UNIQUE
name TEXT
sector TEXT
screener_data JSONB         -- full Screener.in scrape
created_at / updated_at
```

### `users`
```
id UUID PK (Stack Auth user ID)
email TEXT
name TEXT
tier TEXT  -- 'free' | 'pro' | 'promax'
created_at
```

### `stock_views`, `watchlist`, `jobs`, `chat_messages`, `macro_context`, `stock_checklist`

---

## Next.js API Routes

| Route | Method | Purpose |
|---|---|---|
| `/api/analysis/[ticker]` | GET | Latest analysis for ticker |
| `/api/screener/[ticker]` | GET | Raw screener data from DB |
| `/api/chat/[ticker]` | GET/POST | Chat history + streaming chat (Pro only) |
| `/api/trigger-analyze/[ticker]` | POST | Start background analysis job |
| `/api/trigger-cache/[ticker]` | POST | Start background scrape job |
| `/api/job/[job_id]` | GET | Poll job status |
| `/api/watchlist/[ticker]` | GET/POST/DELETE | Watchlist management |
| `/api/search` | GET | Typeahead search |
| `/api/track-view/[ticker]` | POST | Log stock view |
| `/api/admin/update-macro` | POST | Regenerate macro context |
| `/api/admin/purge` | POST | Purge all data |
| `/api/admin/purge-stock/[ticker]` | POST | Purge single stock |

---

## Frontend Design System

### Theme (Tailwind v4, `globals.css`)

**CSS custom properties** — light and dark mode defined in `:root` and `.dark`:

| Token | Light | Dark | Usage |
|---|---|---|---|
| `--background` | `#FFFFFF` | `#080808` | Page bg |
| `--surface` | `#F2EEE4` | `#0e0e0e` | Cards, inputs |
| `--elevated` | `#E8E3D6` | `#141414` | Elevated surfaces |
| `--warm` | `#1C1917` | `#F0EBE0` | Primary text |
| `--dim` | `#2A2420` | `#A39B90` | Secondary text |
| `--quiet` | `#3D342C` | `#6B6259` | Muted text |
| `--gold` | `#9B7118` | `#C9A84C` | Accents, CTAs, borders |
| `--crimson` | `#7B1D28` | `#8B2635` | Danger/red flags |

**Border utilities** (adaptive opacity — do NOT use hardcoded rgba):
```
border-faint / border-subtle / border-muted / border-moderate / border-prominent
```

**Background utilities**:
```
bg-faint / bg-subtle / bg-muted / bg-moderate
```

**Text utilities**:
```
text-faint / text-ghost / text-haze  (for very muted text)
text-quiet / text-dim / text-warm    (Tailwind color tokens)
```

### Typography
- Sans: `var(--font-inter)` (Inter)
- Mono: `var(--font-mono)` (used extensively for labels, numbers, badges)
- Light mode enforces minimum font sizes on desktop (sm+): `text-xs` → 14px, `text-[10px/11px]` → 13px

### Component Patterns
- **No rounded corners** — sharp edges everywhere, zero `rounded-*` unless specifically noted
- **Framer Motion** for all entrance animations — `initial={{ opacity: 0, y: 20 }}` → `animate={{ opacity: 1, y: 0 }}`
- **`useInView`** with `once: true, margin: '-60px 0px'` for scroll-triggered animations
- **Font mono** for all labels, badges, numbers, metadata
- **Gold** (`text-gold`, `border-gold/40`, `bg-gold/8`) for active states, CTAs, key data
- **`::selection`** uses gold-tinted highlight (custom in globals.css)

---

## Key Components Detail

### `AnalysisTabs.tsx`
5 tabs: Business Quality, Financial Health, Governance, Valuation, Outlook.
Active tab: full 4-sided `border border-gold/40` box indicator (Framer Motion `layoutId`).
Tab content wrapper: `min-h-[60vh] pb-16`.

### `SelectionTooltip.tsx`
Listens for `mouseup` globally. Shows `position: fixed` button above any selected text.
Dispatches `verdikt:add-to-chat` custom DOM event with selected text as detail.

### `FloatingChat.tsx`
Listens for `verdikt:add-to-chat` event → opens chat panel → passes prefillText to `ChatSection`.
`ChatSection` receives `prefillText?: string` prop, sets input + focuses on change.

### `Abbr.tsx` / `AbbrText.tsx`
`Abbr` wraps a term with a hover tooltip showing formula + plain-English meaning + healthy range.
`AbbrText` auto-detects known abbreviations in a string and wraps them.
Tooltip: `position: fixed`, gold border, `bg-surface` in light mode — visibility was improved for light mode.

### `AnalyzeFlow.tsx`
Triggers analysis job via `/api/trigger-analyze/[ticker]`, polls `/api/job/[job_id]` every 2s, shows live step timeline.

---

## User Tiers & Gates

| Feature | Free | Pro | ProMax |
|---|---|---|---|
| View analysis | ✓ | ✓ | ✓ |
| AI Chat | ✗ | ✓ | ✓ |
| Investor profile weights | ✗ | ✓ | ✓ |
| PDF export | ✗ | ✓ | ✓ |
| Trigger re-analysis | admin only | ✓ | ✓ |

---

## File Storage Layout

```
stock_cache/{TICKER}/
  raw_full.json           # Full Screener.in scrape (~138KB)
  screener_export.json
  pdf_extracts.json       # Concall transcript text chunks
  rag_docs.json
  rag_index/              # ChromaDB persistent index
  meta.json               # Cache freshness (quarter-aware)
  latest_analysis.json    # Last analysis output
  latest_prompt.txt       # Last full prompt sent to Claude
  latest_thinking.txt     # Claude extended thinking output

data/{TICKER}_analysis.json  # Clean verdict JSON output
migrations/
  add_token_columns.sql   # ALTER TABLE analyses ADD COLUMN input_tokens, output_tokens
```

---

## Environment Variables

```
# Backend (Flask / Python)
ANTHROPIC_API_KEY=sk-ant-...
SUPABASE_URL=...
SUPABASE_SERVICE_KEY=...
FLASK_DEBUG=0
PORT=5000
ALLOWED_ORIGINS=https://verdikt.io
STOCK_CACHE_DIR=./stock_cache
THINKING_BUDGET=10000        # Optional: override Claude thinking budget

# Frontend (Next.js / Vercel)
ANTHROPIC_API_KEY=...        # For chat route (Haiku)
DATABASE_URL=...             # Neon connection string
NEXT_PUBLIC_STACK_PROJECT_ID=...
NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY=...
STACK_SECRET_SERVER_KEY=...
SITE_URL=https://verdikt.io
```

---

## Sectors & Benchmarks

Handled in `prompt_builder.py` with hardcoded benchmarks:
**IT, Banking, Energy, Pharma, Auto, FMCG**

Energy-specific: ROCE 8–12% is normal (do not penalise), PE 8–15x is structural, crude >$90/bbl = positive for ONGC (E&P), negative for OMCs (BPCL, IOC).

---

## Stocks in System

**Analysed** (`data/`): IRCTC, TCS, INFY, BHARTIARTL, JIOFIN, YESBANK, LATENTVIEW, IREDA, RELIANCE, ONGC

**Cached** (`stock_cache/`): above + KPITTECH, SUZLON

---

## Known Constraints / Gotchas

1. **ONGC concalls** are hosted on ongcindia.com (Liferay CMS), not BSE — not directly fetchable by the PDF pipeline. Use "Add Missing" in admin checklist.
2. **Extended thinking + temperature**: cannot set temperature when thinking is enabled — API requires default (1.0).
3. **RAG missing from prompt**: if `stock_cache/{TICKER}/rag_index/` doesn't exist or is empty, pipeline falls back to `"(RAG unavailable — ChromaDB index may not be built yet)"`. Check checklist.
4. **`overflow-hidden` on flex rows**: if placed on the outer flex container, clips right-side buttons (PDF export, etc.). Always place on the inner `flex-1` child only.
5. **iOS Safari auto-zoom**: all inputs/textareas use `font-size: max(1rem, 16px)` in globals.css to prevent zoom.
6. **Light mode**: built primarily for dark mode. Light mode uses CSS var overrides. Never use hardcoded `rgba(255,255,255,X)` — use `var(--border-subtle)`, `var(--quiet)`, etc.
7. **Token tracking**: requires `migrations/add_token_columns.sql` to be run. Old analyses will have NULL for both columns.
8. **Chat history depth**: capped at last 20 messages (`safeHistory.slice(-20)`).
9. **Prompt size**: ~15K–20K chars per full analysis. Prompt saved to `latest_prompt.txt` after every run for inspection.
