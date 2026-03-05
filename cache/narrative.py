"""
Converts raw JSON financial data from Screener into narrative text paragraphs.

The output is what gets chunked and embedded into the per-stock RAG index.
The LLM retrieves these paragraphs at query time — they describe trends and patterns,
not just raw numbers.
"""

from __future__ import annotations


def build_narratives(raw: dict, ticker: str, pdf_extracts: dict | None = None) -> list[dict]:
    """
    Convert all financial sections into a list of narrative chunks.

    Args:
        raw:          Raw scraper output (from raw_full.json)
        ticker:       Stock ticker symbol
        pdf_extracts: Optional dict {url: {title, category, year, text, chunks}}
                      from cache/pdf_extractor.py. When provided, PDF content is
                      indexed as separate RAG chunks instead of just link lists.

    Returns:
        List of {"id": str, "text": str, "section": str}
        Each entry is an embeddable chunk for the RAG index.
    """
    chunks = []

    chunks += _about_chunk(raw, ticker)
    chunks += _ratios_chunk(raw, ticker)
    chunks += _quarterly_chunk(raw, ticker)
    chunks += _annual_pl_chunk(raw, ticker)
    chunks += _balance_sheet_chunk(raw, ticker)
    chunks += _cash_flow_chunk(raw, ticker)
    chunks += _shareholding_chunk(raw, ticker)
    chunks += _peer_chunk(raw, ticker)
    chunks += _news_chunk(raw, ticker)
    chunks += _documents_chunk(raw, ticker)

    # PDF content chunks (annual report + concall text, split into ~3-page pieces)
    if pdf_extracts:
        chunks += _pdf_content_chunks(pdf_extracts, ticker)

    return chunks


# ─── individual section builders ──────────────────────────────────────────────

def _about_chunk(raw: dict, ticker: str) -> list[dict]:
    about = raw.get("aboutText", "").strip()
    if not about:
        return []
    return [{"id": f"{ticker}_about", "section": "company_overview", "text":
             f"{ticker} Company Overview:\n{about}"}]


def _ratios_chunk(raw: dict, ticker: str) -> list[dict]:
    ratios = raw.get("ratios", [])
    if not ratios:
        return []
    lines = [f"  {r.get('name', '')}: {r.get('value', '')}" for r in ratios if r.get("name")]
    text = f"{ticker} Key Financial Ratios (current):\n" + "\n".join(lines)
    return [{"id": f"{ticker}_ratios", "section": "ratios", "text": text}]


def _quarterly_chunk(raw: dict, ticker: str) -> list[dict]:
    q = raw.get("quartersData", {})
    headings = q.get("headings", [])
    values = q.get("values", [])
    if not headings or not values:
        return []

    lines = [f"Quarterly Financial Trends for {ticker} (most recent quarters shown):"]
    lines.append("  " + " | ".join(["Metric"] + headings[:8]))
    for row in values[:10]:
        cat = row.get("category", "")
        vals = row.get("values", [])
        lines.append("  " + " | ".join([cat] + vals[:8]))

    # Compute OPM trend comment
    opm_row = next((r for r in values if "opm" in r.get("category", "").lower()), None)
    if opm_row:
        opm_vals = [v.replace("%", "").strip() for v in opm_row.get("values", [])[:6] if v]
        lines.append(f"\n  OPM % over last quarters: {', '.join(opm_vals)}")

    return [{"id": f"{ticker}_quarterly", "section": "quarterly_results",
             "text": "\n".join(lines)}]


def _annual_pl_chunk(raw: dict, ticker: str) -> list[dict]:
    pl = raw.get("annualPL", {})
    headings = pl.get("headings", [])
    values = pl.get("values", [])
    if not headings or not values:
        return []

    years = headings[-10:]  # last 10 years max
    lines = [f"{ticker} Annual Profit & Loss (10-year trend):"]
    lines.append("  " + " | ".join(["Metric"] + years))

    for row in values:
        cat = row.get("category", "")
        vals = row.get("values", [])[-10:]
        if cat and vals:
            lines.append("  " + " | ".join([cat] + vals))

    # Add derived narrative for key metrics
    revenue_row = _find_row(values, ["sales", "revenue"])
    pat_row = _find_row(values, ["net profit", "profit after tax"])
    opm_row = _find_row(values, ["opm", "operating profit margin"])

    commentary = []
    if revenue_row:
        rev_vals = _clean_nums(revenue_row.get("values", []))
        if len(rev_vals) >= 5:
            commentary.append(f"  Revenue grew from {rev_vals[0]} to {rev_vals[-1]} over {len(rev_vals)} years.")
    if pat_row:
        pat_vals = _clean_nums(pat_row.get("values", []))
        if len(pat_vals) >= 2:
            commentary.append(f"  Net profit trend: {' → '.join(pat_vals[-5:])}")
    if opm_row:
        opm_vals = _clean_nums(opm_row.get("values", []))
        if opm_vals:
            commentary.append(f"  OPM trend: {' → '.join(opm_vals[-5:])}")

    if commentary:
        lines.append("\nKey trends:")
        lines.extend(commentary)

    return [{"id": f"{ticker}_annual_pl", "section": "profit_loss",
             "text": "\n".join(lines)}]


def _balance_sheet_chunk(raw: dict, ticker: str) -> list[dict]:
    bs = raw.get("balanceSheet", {})
    headings = bs.get("headings", [])
    values = bs.get("values", [])
    if not headings or not values:
        return []

    years = headings[-10:]
    lines = [f"{ticker} Balance Sheet (10-year trend):"]
    lines.append("  " + " | ".join(["Metric"] + years))

    for row in values:
        cat = row.get("category", "")
        vals = row.get("values", [])[-10:]
        if cat and vals:
            lines.append("  " + " | ".join([cat] + vals))

    # Narrative for debt and equity
    debt_row = _find_row(values, ["borrowings", "debt", "total debt"])
    equity_row = _find_row(values, ["equity", "shareholders equity", "networth"])

    commentary = []
    if debt_row:
        debt_vals = _clean_nums(debt_row.get("values", []))
        if debt_vals:
            latest = debt_vals[-1]
            commentary.append(f"  Debt (latest): {latest}. Trend: {' → '.join(debt_vals[-4:])}")
    if equity_row:
        eq_vals = _clean_nums(equity_row.get("values", []))
        if eq_vals:
            commentary.append(f"  Equity trend: {' → '.join(eq_vals[-4:])}")

    if commentary:
        lines.append("\nBalance sheet health:")
        lines.extend(commentary)

    return [{"id": f"{ticker}_balance_sheet", "section": "balance_sheet",
             "text": "\n".join(lines)}]


def _cash_flow_chunk(raw: dict, ticker: str) -> list[dict]:
    cf = raw.get("cashFlows", {})
    headings = cf.get("headings", [])
    values = cf.get("values", [])
    if not headings or not values:
        return []

    years = headings[-10:]
    lines = [f"{ticker} Cash Flow Statement (10-year trend):"]
    lines.append("  " + " | ".join(["Metric"] + years))

    for row in values:
        cat = row.get("category", "")
        vals = row.get("values", [])[-10:]
        if cat and vals:
            lines.append("  " + " | ".join([cat] + vals))

    # Narrative — this is what the LLM needs to understand earnings quality
    ocf_row = _find_row(values, ["operating", "cash from operations", "cash from operating"])
    capex_row = _find_row(values, ["capital expenditure", "capex", "purchase of fixed"])
    fcf_commentary = []

    if ocf_row:
        ocf_vals = _clean_nums(ocf_row.get("values", []))
        if len(ocf_vals) >= 3:
            fcf_commentary.append(
                f"  Operating cash flow trend: {' → '.join(ocf_vals[-5:])} "
                f"({'growing' if _is_growing(ocf_vals) else 'declining or flat'})"
            )
    if capex_row:
        cap_vals = _clean_nums(capex_row.get("values", []))
        if cap_vals:
            fcf_commentary.append(f"  Capex trend: {' → '.join(cap_vals[-4:])}")

    if fcf_commentary:
        lines.append("\nCash flow analysis:")
        lines.extend(fcf_commentary)

    return [{"id": f"{ticker}_cash_flows", "section": "cash_flows",
             "text": "\n".join(lines)}]


def _shareholding_chunk(raw: dict, ticker: str) -> list[dict]:
    sh = raw.get("shareholding", [])
    if not sh:
        return []

    lines = [f"{ticker} Shareholding Pattern (latest quarters):"]
    for row in sh:
        cat = row.get("category", "")
        vals = {k: v for k, v in row.items() if k != "category"}
        if cat:
            latest_val = list(vals.values())[0] if vals else ""
            lines.append(f"  {cat}: {latest_val}")

    return [{"id": f"{ticker}_shareholding", "section": "shareholding",
             "text": "\n".join(lines)}]


def _peer_chunk(raw: dict, ticker: str) -> list[dict]:
    peers = raw.get("peerComparison", {})
    headings = peers.get("headings", [])
    peer_list = peers.get("peers", [])
    if not headings or not peer_list:
        return []

    lines = [f"{ticker} Peer Comparison (industry peers on Screener):"]
    lines.append("  " + " | ".join(headings[:8]))
    for p in peer_list[:10]:
        row_vals = [str(p.get(h, "")) for h in headings[:8]]
        lines.append("  " + " | ".join(row_vals))

    return [{"id": f"{ticker}_peers", "section": "peer_comparison",
             "text": "\n".join(lines)}]


def _news_chunk(raw: dict, ticker: str) -> list[dict]:
    news = raw.get("news", [])
    if not news:
        return []

    lines = [f"{ticker} Recent News Headlines:"]
    for n in news[:10]:
        time = n.get("time", "")
        title = n.get("title", "")
        source = n.get("source", "")
        if title:
            lines.append(f"  [{time}] {title} — {source}")

    return [{"id": f"{ticker}_news", "section": "recent_news",
             "text": "\n".join(lines)}]


def _documents_chunk(raw: dict, ticker: str) -> list[dict]:
    docs = raw.get("documents", [])
    if not docs:
        return []

    # Group by category for a cleaner summary
    annual = [d for d in docs if d.get("category") == "annual_report"]
    concall = [d for d in docs if d.get("category") == "concall"]
    announcements = [d for d in docs if d.get("category") == "announcement"]

    lines = [f"{ticker} Available Documents:"]

    if annual:
        lines.append("  Annual Reports:")
        for d in sorted(annual, key=lambda x: x.get("year") or 0, reverse=True)[:3]:
            year = f" ({d['year']})" if d.get("year") else ""
            lines.append(f"    [PDF] {d.get('title', '')}{year}")

    if concall:
        lines.append("  Earnings Call Transcripts:")
        for d in concall[:3]:
            date = f" — {d['date']}" if d.get("date") else ""
            lines.append(f"    [PDF] {d.get('title', '')}{date}")

    if announcements:
        lines.append(f"  Recent Announcements ({len(announcements)} total):")
        for d in announcements[:5]:
            date = f" [{d['date']}]" if d.get("date") else ""
            lines.append(f"   {date} {d.get('title', '')[:80]}")

    return [{"id": f"{ticker}_documents", "section": "documents",
             "text": "\n".join(lines)}]


def _pdf_content_chunks(pdf_extracts: dict, ticker: str) -> list[dict]:
    """
    Create RAG chunks from extracted PDF text (annual reports + concall transcripts).

    Each ~3-page segment becomes a separate chunk so vector search can
    pinpoint the relevant passage (e.g. management commentary on margins)
    rather than retrieving the whole 50-page document.
    """
    chunks = []

    for doc_idx, (_, info) in enumerate(pdf_extracts.items()):
        category = info.get("category", "doc")
        title = info.get("title", "")
        year = info.get("year", "")
        text_chunks = info.get("chunks", [])

        if not text_chunks:
            continue

        section = "annual_report_text" if category == "annual_report" else "concall_text"
        year_tag = f"_{year}" if year else f"_doc{doc_idx}"

        for i, chunk_text in enumerate(text_chunks):
            chunk_id = f"{ticker}_{category}{year_tag}_p{i}".replace(" ", "_")
            header = f"{ticker} {title[:60]} — pages {i*3+1}–{i*3+3}:\n\n"
            chunks.append({
                "id": chunk_id,
                "section": section,
                "text": header + chunk_text,
            })

    return chunks


# ─── helpers ──────────────────────────────────────────────────────────────────

def _find_row(values: list, keywords: list) -> dict | None:
    """Find first row where category matches any keyword (case-insensitive)."""
    for row in values:
        cat = row.get("category", "").lower()
        if any(kw in cat for kw in keywords):
            return row
    return None


def _clean_nums(vals: list) -> list:
    """Return non-empty string values from a list."""
    return [str(v).strip() for v in vals if str(v).strip() and str(v).strip() != ""]


def _is_growing(vals: list) -> bool:
    """Rough check: is the last value larger than the first?"""
    try:
        first = float(str(vals[0]).replace(",", ""))
        last = float(str(vals[-1]).replace(",", ""))
        return last > first
    except Exception:
        return False
