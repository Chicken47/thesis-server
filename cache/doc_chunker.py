"""
Section-aware + overlap chunker for RAG documents.

Three chunking strategies, one per document type:

  concall        — split by Q&A exchange (Analyst / Moderator / Management turns),
                   overlap = last 100 chars of previous chunk prepended to next.

  annual_report  — detect high-signal section headers, skip boilerplate,
                   chunk within sections at ~600 chars with 100-char overlap.

  credit_rating  — short doc (2–4 pages), single or 2-chunk max. No splitting needed.

  announcement   — already a one-liner summary; one chunk per announcement.

Each chunk is returned as:
    {"id": str, "section": str, "doc_type": str, "text": str, "source_url": str}
"""

from __future__ import annotations

import re

# ── Tuning ────────────────────────────────────────────────────────────────────
CONCALL_CHUNK_CHARS   = 500
CONCALL_OVERLAP       = 80
ANNUAL_CHUNK_CHARS    = 600
ANNUAL_OVERLAP        = 100

# ── Annual report section detection ──────────────────────────────────────────
# Patterns that signal HIGH-SIGNAL sections to include
_HIGH_SIGNAL = re.compile(
    r"^(management\s+discussion|md&a|m\.d\.&a|business\s+(overview|review|performance)|"
    r"segment(al)?\s+(performance|review|revenue|results)|risk\s+(management|factors)|"
    r"financial\s+(performance|highlights|review)|operating\s+performance|"
    r"revenue\s+and\s+profit|outlook|strategy\s+and|key\s+metrics|"
    r"chairman.s\s+(letter|message|statement)|chief\s+executive|ceo\s+review)",
    re.I,
)

# Patterns that signal BOILERPLATE sections to skip
# Keep these SPECIFIC — overly broad patterns (e.g. plain "shareholder") match too many lines
_LOW_SIGNAL = re.compile(
    r"^(directors.?\s+report|statutory\s+auditor|statutory\s+section|"
    r"notes\s+to\s+(accounts|financial\s+statements)|annexure\s+[ivx\d]|"
    r"secretarial\s+audit\s+report|independent\s+auditor.s\s+report|"
    r"proxy\s+form|agm\s+notice|notice\s+of\s+(agm|annual\s+general)|"
    r"balance\s+sheet\s+abstract|ten\s+year(s?)?\s+financial|"
    r"dividend\s+history|secretarial\s+audit$|certificate\s+of\s+)",
    re.I,
)

# Looser header detection — any ALL CAPS line ≥ 4 words = probable section header
_CAPS_HEADER = re.compile(r"^[A-Z][A-Z &,/()-]{15,}$")


def _is_high_signal_header(line: str) -> bool:
    return bool(_HIGH_SIGNAL.match(line.strip()))


def _is_low_signal_header(line: str) -> bool:
    return bool(_LOW_SIGNAL.match(line.strip()))


def _is_header(line: str) -> bool:
    t = line.strip()
    return bool(_HIGH_SIGNAL.match(t) or _LOW_SIGNAL.match(t) or _CAPS_HEADER.match(t))


# ── Overlap helper ─────────────────────────────────────────────────────────────
def _tail(text: str, n: int) -> str:
    """Return last n chars of text, starting at a word boundary."""
    if len(text) <= n:
        return text
    cut = text.rfind(" ", len(text) - n)
    return text[cut:].lstrip() if cut > 0 else text[-n:]


# ── Fixed-size chunker with overlap ───────────────────────────────────────────
def _fixed_overlap_chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    """
    Split text into chunks of ~max_chars, each chunk prepended with the
    last `overlap` chars of the previous chunk.
    Splits at newline boundaries where possible.
    """
    chunks = []
    prev_tail = ""
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            cut = text.rfind("\n", start, end)
            if cut > start + max_chars // 2:
                end = cut
        segment = (prev_tail + " " + text[start:end]).strip() if prev_tail else text[start:end].strip()
        if len(segment) > 80:
            chunks.append(segment)
        prev_tail = _tail(text[start:end], overlap)
        start = end
    return chunks


# ── CONCALL chunker ───────────────────────────────────────────────────────────
# Speaker turn markers common in Indian concall transcripts
_SPEAKER_RE = re.compile(
    r"^(moderator|operator|analyst|participant|management|"
    r"[A-Z][a-z]+\s+[A-Z][a-z]+\s*[:\-–]|"         # "John Smith:"
    r"[A-Z]{2,}[\s,]+[A-Z][a-z]+\s*[:\-–])",        # "CFO John:"
    re.M,
)


def chunk_concall(text: str, ticker: str, source_url: str) -> list[dict]:
    """
    Split concall transcript by speaker turns (Q&A exchanges).
    Groups consecutive turns into ~CONCALL_CHUNK_CHARS windows with overlap.
    """
    if not text.strip():
        return []

    # Split on [PAGE] markers first
    pages = [p.strip() for p in text.split("[PAGE]") if p.strip()]
    full_text = "\n\n".join(pages)

    # Try to split on speaker turns
    turns = re.split(r"(?=^(?:Moderator|Operator|Analyst|Participant)[:\s])", full_text, flags=re.M | re.I)
    if len(turns) < 3:
        # Fallback: no clear speaker markers → fixed-overlap chunking
        raw_chunks = _fixed_overlap_chunks(full_text, CONCALL_CHUNK_CHARS, CONCALL_OVERLAP)
    else:
        # Group turns into ~CONCALL_CHUNK_CHARS windows
        raw_chunks = []
        current = ""
        prev_tail = ""
        for turn in turns:
            turn = turn.strip()
            if not turn:
                continue
            candidate = (prev_tail + "\n\n" + current + "\n\n" + turn).strip() if prev_tail else (current + "\n\n" + turn).strip()
            if len(candidate) > CONCALL_CHUNK_CHARS and current:
                chunk_text = (prev_tail + "\n\n" + current).strip() if prev_tail else current
                raw_chunks.append(chunk_text)
                prev_tail = _tail(current, CONCALL_OVERLAP)
                current = turn
            else:
                current = (current + "\n\n" + turn).strip()
        if current.strip():
            raw_chunks.append((prev_tail + "\n\n" + current).strip() if prev_tail else current)

    # Post-process: split any oversized turn (e.g. long management presentation)
    # that wasn't naturally split by speaker-turn detection.
    final_chunks: list[str] = []
    for c in raw_chunks:
        if len(c) > CONCALL_CHUNK_CHARS * 2:
            final_chunks.extend(_fixed_overlap_chunks(c, CONCALL_CHUNK_CHARS, CONCALL_OVERLAP))
        else:
            final_chunks.append(c)

    return [
        {
            "id": f"{ticker}_concall_{i}",
            "section": "concall",
            "doc_type": "concall",
            "text": c,
            "source_url": source_url,
        }
        for i, c in enumerate(final_chunks)
        if len(c) > 80
    ]


# ── ANNUAL REPORT chunker ─────────────────────────────────────────────────────
def chunk_annual_report(text: str, ticker: str, year: int, source_url: str) -> list[dict]:
    """
    Section-aware chunking of annual report text.

    1. Walk lines, tracking current section (high-signal / low-signal / unknown).
    2. Accumulate lines only when inside a high-signal section.
    3. Apply fixed-overlap chunking within each section's accumulated text.
    4. Stop collecting after ~120 pages (proxy: after 3 low-signal sections hit).
    """
    if not text.strip():
        return []

    pages = [p.strip() for p in text.split("[PAGE]") if p.strip()]

    chunks = []
    chunk_idx = 0
    current_section_name = "general"
    current_section_text = ""
    in_high_signal = False
    low_signal_count = 0

    def flush_section(section_name: str, section_text: str):
        nonlocal chunk_idx
        raw = _fixed_overlap_chunks(section_text.strip(), ANNUAL_CHUNK_CHARS, ANNUAL_OVERLAP)
        for c in raw:
            if len(c) > 80:
                chunks.append({
                    "id": f"{ticker}_annual_{year}_{chunk_idx}",
                    "section": "annual_report",
                    "doc_type": "annual_report",
                    "subsection": section_name,
                    "text": f"[{section_name.upper()}]\n{c}",
                    "source_url": source_url,
                })
                chunk_idx += 1

    for page_text in pages:
        if low_signal_count >= 10:
            # Deep into boilerplate — stop
            break

        lines = page_text.split("\n")
        for line in lines:
            stripped = line.strip()
            if not stripped:
                current_section_text += "\n"
                continue

            if _is_high_signal_header(stripped):
                # Flush previous section if it was high-signal
                if in_high_signal and current_section_text.strip():
                    flush_section(current_section_name, current_section_text)
                current_section_name = stripped[:60]
                current_section_text = ""
                in_high_signal = True

            elif _is_low_signal_header(stripped):
                if in_high_signal and current_section_text.strip():
                    flush_section(current_section_name, current_section_text)
                current_section_name = stripped[:60]
                current_section_text = ""
                in_high_signal = False
                low_signal_count += 1

            else:
                if in_high_signal:
                    current_section_text += line + "\n"

    # Flush any remaining high-signal content
    if in_high_signal and current_section_text.strip():
        flush_section(current_section_name, current_section_text)

    # Fallback: if section detection found nothing, use first 40 pages with fixed chunking
    if not chunks and pages:
        fallback_text = "\n\n".join(pages[:40])
        raw = _fixed_overlap_chunks(fallback_text, ANNUAL_CHUNK_CHARS, ANNUAL_OVERLAP)
        for i, c in enumerate(raw):
            if len(c) > 80:
                chunks.append({
                    "id": f"{ticker}_annual_{year}_fb_{i}",
                    "section": "annual_report",
                    "doc_type": "annual_report",
                    "subsection": "general",
                    "text": c,
                    "source_url": source_url,
                })

    return chunks


# ── CREDIT RATING chunker ─────────────────────────────────────────────────────
def chunk_credit_rating(text: str, ticker: str, source_url: str) -> list[dict]:
    """
    Credit rating docs are 2–4 pages. Single chunk or two at most.
    We do preserve the full text — it's short enough.
    """
    if not text.strip():
        return []

    pages = [p.strip() for p in text.split("[PAGE]") if p.strip()]
    full_text = "\n\n".join(pages).strip()

    # If under 1800 chars: single chunk
    if len(full_text) <= 1800:
        return [{
            "id": f"{ticker}_credit_rating_0",
            "section": "credit_rating",
            "doc_type": "credit_rating",
            "text": full_text,
            "source_url": source_url,
        }]

    # Otherwise: fixed-overlap, 2 chunks max
    raw = _fixed_overlap_chunks(full_text, 1600, 200)
    return [
        {
            "id": f"{ticker}_credit_rating_{i}",
            "section": "credit_rating",
            "doc_type": "credit_rating",
            "text": c,
            "source_url": source_url,
        }
        for i, c in enumerate(raw[:2])
        if len(c) > 80
    ]


# ── ANNOUNCEMENTS chunker ─────────────────────────────────────────────────────
def chunk_announcements(announcements: list[dict], ticker: str) -> list[dict]:
    """
    Each announcement already has a scraped summary. One chunk per announcement.
    Only include announcements that have a non-trivial summary.
    """
    chunks = []
    for i, ann in enumerate(announcements):
        summary = (ann.get("summary") or "").strip()
        title = (ann.get("title") or "").strip()
        if not summary or len(summary) < 30:
            continue
        text = f"[ANNOUNCEMENT] {title}\n{summary}"
        chunks.append({
            "id": f"{ticker}_announcement_{i}",
            "section": "announcement",
            "doc_type": "announcement",
            "text": text,
            "source_url": ann.get("url", ""),
        })
    return chunks
