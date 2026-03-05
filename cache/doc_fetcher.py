"""
doc_fetcher.py — Stock knowledge base builder.

Fetches and stores all RAG-relevant documents for a stock:
  1. Latest concall transcript          (BSE PDF)
  2. Latest annual report FY2025        (BSE PDF, section-aware extraction)
  3. Credit rating document             (CRISIL/ICRA/CARE PDF, stored with clickable URL)
  4. Announcements                      (pre-scraped summaries from raw_full.json)

Output: stock_cache/{TICKER}/rag_docs.json

Schema:
  {
    "ticker":        str,
    "generated_at":  str (ISO date),
    "concall":       { title, url, date, text, chunks: [...] } | null,
    "annual_report": { title, url, year, text, chunks: [...] } | null,
    "credit_rating": { title, url, date, text, chunks: [...] } | null,
    "announcements": [ { title, summary, date, url }, ... ],
  }

Run standalone:
  python -m cache.doc_fetcher --ticker INFY
  python -m cache.doc_fetcher --ticker INFY --force
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from cache.pdf_extractor import extract_pdf_text   # reuse existing downloader
from cache.doc_chunker import (
    chunk_concall,
    chunk_annual_report,
    chunk_credit_rating,
    chunk_announcements,
)

CACHE_DIR = Path(__file__).parent.parent / "stock_cache"

# Annual report: read up to this many pages before hitting boilerplate
ANNUAL_MAX_PAGES = 160
# Concall: full transcript (usually 20–50 pages)
CONCALL_MAX_PAGES = 60
# Credit rating: usually 2–4 pages, cap at 10 to be safe
RATING_MAX_PAGES = 10


# ── Document categorisation ───────────────────────────────────────────────────

def _classify(doc: dict) -> str:
    """Return 'concall' | 'annual_report' | 'credit_rating' | 'announcement' | 'skip'."""
    cat   = doc.get("category", "")
    title = doc.get("title", "").strip().lower()
    url   = doc.get("url", "").lower()

    if cat == "concall":
        return "concall"

    if cat == "annual_report":
        return "annual_report"

    if cat == "announcement":
        # Concall transcripts filed as announcements
        if title in ("transcript", "earnings call transcript", "conference call transcript"):
            return "concall"
        # Credit rating docs filed as announcements
        if any(k in title for k in ("rating update", "credit rating", "rating reaffirm",
                                     "rating revision", "crisil", "icra", "care ratings")):
            return "credit_rating"
        if any(k in url for k in ("crisil.com", "icra.in", "careratings.com")):
            return "credit_rating"
        return "announcement"

    return "skip"


def _pick_documents(docs: list[dict]) -> dict[str, list[dict]]:
    """
    Categorise all documents and select targets:
      - concall:       latest 1 PDF
      - annual_report: latest 1 (highest year)
      - credit_rating: latest 1 (first match)
      - announcements: all with summaries
    """
    buckets: dict[str, list[dict]] = {
        "concall": [], "annual_report": [], "credit_rating": [], "announcement": []
    }
    for doc in docs:
        kind = _classify(doc)
        if kind in buckets:
            buckets[kind].append(doc)

    # Sort annual reports by year desc, keep latest
    buckets["annual_report"].sort(key=lambda d: d.get("year") or 0, reverse=True)

    return {
        "concall":       buckets["concall"][:1],
        "annual_report": buckets["annual_report"][:1],
        "credit_rating": buckets["credit_rating"][:1],
        "announcements": buckets["announcement"],     # all
    }


# ── Per-document fetch helpers ────────────────────────────────────────────────

def _fetch_concall(doc: dict, ticker: str, verbose: bool) -> dict | None:
    url   = doc.get("url", "")
    title = doc.get("title", doc.get("category", "concall"))
    date  = str(doc.get("year") or "")

    if doc.get("type") != "pdf":
        if verbose:
            print(f"  [Concall] Not a PDF, skipping: {title}")
        return None

    if verbose:
        print(f"  [Concall] Fetching: {title} ({url[:70]})")

    text = extract_pdf_text(url, max_pages=CONCALL_MAX_PAGES, label="concall")
    if not text:
        if verbose:
            print(f"  [Concall] Empty or failed — skipping")
        return None

    chunks = chunk_concall(text, ticker, source_url=url)
    if verbose:
        print(f"  [Concall] {text.count('[PAGE]') + 1} pages → {len(chunks)} chunks")

    return {
        "title": title,
        "url":   url,
        "date":  date,
        "text":  text,
        "chunks": [{"id": c["id"], "section": c["section"], "text": c["text"]} for c in chunks],
    }


def _fetch_annual_report(doc: dict, ticker: str, verbose: bool) -> dict | None:
    url  = doc.get("url", "")
    year = doc.get("year") or 0

    if doc.get("type") != "pdf":
        if verbose:
            print(f"  [AnnualReport] Not a PDF ({url[:60]}), skipping")
        return None

    if verbose:
        print(f"  [AnnualReport] Fetching FY{year} ({url[:70]})")

    text = extract_pdf_text(url, max_pages=ANNUAL_MAX_PAGES, label=f"annual_report_fy{year}")
    if not text:
        if verbose:
            print(f"  [AnnualReport] Empty or failed — skipping")
        return None

    chunks = chunk_annual_report(text, ticker, year=year, source_url=url)
    if verbose:
        page_count = text.count("[PAGE]") + 1
        print(f"  [AnnualReport] {page_count} pages read → {len(chunks)} section chunks")

    return {
        "title": f"Annual Report FY{year}",
        "url":   url,
        "year":  year,
        "text":  text,
        "chunks": [{"id": c["id"], "section": c["section"],
                    "subsection": c.get("subsection", ""), "text": c["text"]} for c in chunks],
    }


def _fetch_credit_rating(doc: dict, ticker: str, verbose: bool) -> dict | None:
    url   = doc.get("url", "")
    title = doc.get("title", "Credit Rating")
    date  = str(doc.get("year") or "")

    if verbose:
        print(f"  [CreditRating] Fetching: {title} ({url[:70]})")

    # Try PDF first; some rating docs are HTML pages (CRISIL)
    text = ""
    if doc.get("type") == "pdf" or url.endswith(".pdf"):
        text = extract_pdf_text(url, max_pages=RATING_MAX_PAGES, label="credit_rating")

    if not text:
        # Try fetching as HTML and stripping tags
        text = _fetch_html_text(url, label="credit_rating")

    if not text:
        if verbose:
            print(f"  [CreditRating] Empty or failed — storing URL only")
        # Store the URL even if we can't fetch text (user can click through)
        return {
            "title": title,
            "url":   url,
            "date":  date,
            "text":  "",
            "chunks": [],
        }

    chunks = chunk_credit_rating(text, ticker, source_url=url)
    if verbose:
        print(f"  [CreditRating] {len(text):,} chars → {len(chunks)} chunk(s)")

    return {
        "title": title,
        "url":   url,
        "date":  date,
        "text":  text,
        "chunks": [{"id": c["id"], "section": c["section"], "text": c["text"]} for c in chunks],
    }


def _fetch_html_text(url: str, label: str = "") -> str:
    """Fallback: fetch a URL as HTML, strip tags, return plain text."""
    try:
        import requests
        from html.parser import HTMLParser

        class _Stripper(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = True

            def handle_endtag(self, tag):
                if tag in ("script", "style", "nav", "footer", "header"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self.parts.append(stripped)

        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"},
        )
        if resp.status_code != 200:
            return ""

        parser = _Stripper()
        parser.feed(resp.text)
        return "\n".join(parser.parts)

    except Exception as e:
        print(f"  [HTML] Failed fetching {label or url[:50]}: {e}")
        return ""


def _collect_announcements(docs: list[dict]) -> list[dict]:
    """
    Collect announcement summaries already scraped into raw_full.json.
    Filter to those with meaningful summaries.
    """
    out = []
    for doc in docs:
        summary = (doc.get("summary") or "").strip()
        title   = (doc.get("title") or "").strip()
        if not summary or len(summary) < 30:
            continue
        out.append({
            "title":   title,
            "summary": summary,
            "date":    str(doc.get("year") or ""),
            "url":     doc.get("url", ""),
        })
    return out


# ── Main entry point ──────────────────────────────────────────────────────────

def build_rag_docs(ticker: str, raw: dict, verbose: bool = True, force: bool = False) -> dict:
    """
    Build and save rag_docs.json for a ticker.

    Args:
        ticker:  Stock ticker e.g. "INFY"
        raw:     raw_full.json contents (already loaded)
        verbose: print progress
        force:   re-fetch even if rag_docs.json already exists

    Returns:
        The rag_docs dict (also written to disk).
    """
    rag_path = CACHE_DIR / ticker.upper() / "rag_docs.json"

    if not force and rag_path.exists():
        if verbose:
            print(f"[DocFetcher] rag_docs.json already exists for {ticker} — use --force to rebuild")
        import json
        return json.loads(rag_path.read_text())

    docs = raw.get("documents", [])
    targets = _pick_documents(docs)

    if verbose:
        print(f"\n[DocFetcher] {ticker} — documents found:")
        print(f"  concall:       {len(targets['concall'])}")
        print(f"  annual_report: {len(targets['annual_report'])}")
        print(f"  credit_rating: {len(targets['credit_rating'])}")
        print(f"  announcements: {len(targets['announcements'])} (with summaries: "
              f"{len([d for d in targets['announcements'] if (d.get('summary') or '').strip()])})")
        print()

    result: dict = {
        "ticker":       ticker.upper(),
        "generated_at": datetime.now(timezone.utc).isoformat()[:10],
        "concall":       None,
        "annual_report": None,
        "credit_rating": None,
        "announcements": [],
    }

    # Concall
    if targets["concall"]:
        result["concall"] = _fetch_concall(targets["concall"][0], ticker, verbose)

    # Annual report
    if targets["annual_report"]:
        result["annual_report"] = _fetch_annual_report(targets["annual_report"][0], ticker, verbose)

    # Credit rating
    if targets["credit_rating"]:
        result["credit_rating"] = _fetch_credit_rating(targets["credit_rating"][0], ticker, verbose)

    # Announcements (no PDF fetch needed — summaries are in raw_full.json)
    result["announcements"] = _collect_announcements(targets["announcements"])

    # Chunk announcements (done in-memory; no text fetch needed)
    ann_chunks = chunk_announcements(result["announcements"], ticker)
    result["announcement_chunks"] = [
        {"id": c["id"], "section": c["section"], "text": c["text"]}
        for c in ann_chunks
    ]

    # Save
    import json
    (CACHE_DIR / ticker.upper()).mkdir(parents=True, exist_ok=True)
    rag_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))

    if verbose:
        concall_chunks  = len((result["concall"]  or {}).get("chunks", []))
        annual_chunks   = len((result["annual_report"] or {}).get("chunks", []))
        rating_chunks   = len((result["credit_rating"] or {}).get("chunks", []))
        ann_chunk_count = len(result["announcement_chunks"])
        total = concall_chunks + annual_chunks + rating_chunks + ann_chunk_count
        print(f"\n[DocFetcher] Done — {ticker}")
        print(f"  Concall chunks      : {concall_chunks}")
        print(f"  Annual report chunks: {annual_chunks}")
        print(f"  Credit rating chunks: {rating_chunks}")
        print(f"  Announcement chunks : {ann_chunk_count}")
        print(f"  Total               : {total}")
        print(f"  Saved to            : {rag_path}")

    return result


def load_rag_docs(ticker: str) -> dict | None:
    """Load rag_docs.json for a ticker. Returns None if not found."""
    import json
    path = CACHE_DIR / ticker.upper() / "rag_docs.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse, json, sys

    parser = argparse.ArgumentParser(description="Build rag_docs.json for a stock")
    parser.add_argument("--ticker", required=True, help="Stock ticker e.g. INFY")
    parser.add_argument("--force",  action="store_true", help="Re-fetch even if already cached")
    args = parser.parse_args()

    ticker = args.ticker.upper()
    raw_path = CACHE_DIR / ticker / "raw_full.json"
    if not raw_path.exists():
        print(f"ERROR: No raw_full.json for {ticker}. Run: python main.py --cache-stock {ticker}")
        sys.exit(1)

    raw = json.loads(raw_path.read_text())
    build_rag_docs(ticker, raw, verbose=True, force=args.force)
