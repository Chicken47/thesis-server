"""
PDF text extraction for earnings call transcripts.

Downloads PDFs from BSE/NSE/SEBI and extracts text for RAG indexing.
Only processes: concall transcripts (latest 2).
Annual reports and announcements are skipped — transcripts contain the most
current management commentary on growth, margins, deal pipeline, and outlook.
"""

from __future__ import annotations

import io
import re

MAX_PDF_MB = 35          # skip PDFs larger than this
REQUEST_TIMEOUT = 45     # seconds per download
CONCALL_MAX_PAGES = 60         # full transcript, usually 20-50 pages
CHUNK_CHARS = 3000             # chars per narrative chunk


def _effective_category(doc: dict) -> str:
    """
    Return the effective category for a document, correcting known scraper gaps.

    Screener's documents section sometimes titles concall transcripts simply as
    "Transcript" (BSE filing convention). These arrive as category="announcement"
    but are concall PDFs and should be treated as such.

    Credit rating updates ("Rating update ... from icra/crisil") are skipped.
    """
    cat = doc.get("category", "")
    title = doc.get("title", "").strip().lower()

    if cat == "announcement":
        if title == "transcript":
            return "concall"
        if "rating update" in title or "credit rating" in title or "rating reaffirm" in title:
            return "skip"

    return cat


def categorize_documents(docs: list[dict]) -> dict[str, list[dict]]:
    """
    Split document list into categorized buckets.

    Returns:
        {
            "annual_reports": [...],   # sorted newest first, capped at 2
            "concall": [...],          # latest 2
            "announcements": [...],    # all (just links, no PDF extraction)
        }
    """
    annual = [d for d in docs if _effective_category(d) == "annual_report"]
    concall = [d for d in docs if _effective_category(d) == "concall"]
    announcements = [d for d in docs if _effective_category(d) == "announcement"]

    # Sort annual reports by year desc, take latest 2
    annual.sort(key=lambda d: d.get("year") or 0, reverse=True)
    annual = annual[:2]

    # Concall: take latest 2 (already reverse-chron from Screener)
    concall = concall[:2]

    return {
        "annual_reports": annual,
        "concall": concall,
        "announcements": announcements,
    }


def extract_pdf_text(url: str, max_pages: int = 40, label: str = "") -> str:
    """
    Download a PDF from BSE/NSE and extract its text with pdfplumber.

    Args:
        url:       PDF URL
        max_pages: Maximum pages to process
        label:     Human-readable label for logging

    Returns:
        Extracted text joined with page breaks, or "" on failure.
    """
    try:
        import requests
        import pdfplumber
    except ImportError as e:
        print(f"  [PDF] Missing dependency: {e} — skipping PDF extraction")
        return ""

    try:
        import requests as _req
        resp = _req.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"},
            stream=True,
        )
        if resp.status_code != 200:
            print(f"  [PDF] HTTP {resp.status_code} for {label or url[:60]}")
            return ""

        # Check size before downloading fully
        content_len = int(resp.headers.get("Content-Length", 0))
        if content_len > MAX_PDF_MB * 1024 * 1024:
            print(f"  [PDF] Skipping {label or ''}: too large ({content_len // (1024*1024)}MB)")
            return ""

        chunks_raw = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_PDF_MB * 1024 * 1024:
                print(f"  [PDF] Skipping {label or ''}: exceeded size limit during download")
                return ""
            chunks_raw.append(chunk)

        content = b"".join(chunks_raw)

        with pdfplumber.open(io.BytesIO(content)) as pdf:
            pages_text = []
            for i, page in enumerate(pdf.pages[:max_pages]):
                text = page.extract_text()
                if text and text.strip():
                    pages_text.append(text.strip())

            if not pages_text:
                return ""

            return "\n\n[PAGE]\n\n".join(pages_text)

    except Exception as exc:
        print(f"  [PDF] Error extracting {label or url[:60]}: {exc}")
        return ""


def split_pdf_text_into_chunks(text: str, label: str, max_chunk: int = CHUNK_CHARS) -> list[str]:
    """
    Split extracted PDF text into RAG-sized chunks.

    Groups pages in sets of 3, then enforces the max_chunk char limit by
    splitting any oversized group into char windows at newline boundaries.
    This prevents dense pages (long concall Q&A) from producing chunks that
    exceed the RAG assembly budget and cause silent empty retrieval.
    """
    if not text.strip():
        return []

    pages = [p.strip() for p in text.split("[PAGE]") if p.strip()]
    if not pages:
        return []

    result = []
    for i in range(0, len(pages), 3):
        group = "\n\n".join(pages[i : i + 3]).strip()
        if not group:
            continue
        if len(group) <= max_chunk:
            result.append(group)
        else:
            # Group too large — split at newline boundaries within max_chunk windows
            start = 0
            while start < len(group):
                end = min(start + max_chunk, len(group))
                if end < len(group):
                    cut = group.rfind("\n", start, end)
                    if cut > start + max_chunk // 2:
                        end = cut
                result.append(group[start:end].strip())
                start = end

    return [c for c in result if len(c) > 100]


def extract_key_pdfs(docs: list[dict], verbose: bool = True) -> dict[str, dict]:
    """
    High-level: extract text from the latest concall transcripts.

    Args:
        docs: list of document dicts from the scraper
        verbose: print progress

    Returns:
        {
            url: {
                "title": str,
                "category": str,
                "text": str,          # full extracted text
                "chunks": [str],      # split into RAG-sized pieces
            }
        }
    """
    categorized = categorize_documents(docs)
    results = {}

    targets = [(d, CONCALL_MAX_PAGES) for d in categorized["concall"]]

    for doc, max_pages in targets:
        url = doc.get("url", "")
        title = doc.get("title", "")
        category = doc.get("category", "")

        if not url or doc.get("type") != "pdf":
            continue

        if verbose:
            year_tag = f" ({doc['year']})" if doc.get("year") else ""
            print(f"  [PDF] Extracting: {title[:60]}{year_tag}...")

        text = extract_pdf_text(url, max_pages=max_pages, label=title[:50])

        if text:
            if verbose:
                page_count = text.count("[PAGE]") + 1
                print(f"  [PDF] OK — {page_count} pages, {len(text):,} chars")
            results[url] = {
                "title": title,
                "category": category,
                "year": doc.get("year"),
                "text": text,
                "chunks": split_pdf_text_into_chunks(text, label=title),
            }
        else:
            if verbose:
                print(f"  [PDF] Failed or empty for {title[:50]}")

    return results
