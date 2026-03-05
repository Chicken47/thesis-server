"""
Per-stock RAG indexer using ChromaDB + sentence-transformers.

Builds a vector index from all narrative chunks for a given stock.
At query time, retrieves the most relevant chunks for the analysis question.

Index lives at: stock_cache/{TICKER}/rag_index/
"""

from __future__ import annotations

from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "stock_cache"
EMBED_MODEL = "all-MiniLM-L6-v2"  # 22MB, fast on CPU, good for English financial text

# Lazy-loaded globals — imported only when needed to avoid slow startup
_embedder = None


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def _get_chroma(ticker: str):
    """Return (client, collection) for a given ticker."""
    import chromadb
    index_dir = CACHE_DIR / ticker.upper() / "rag_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(index_dir))
    collection = client.get_or_create_collection(
        name="stock_context",
        metadata={"hnsw:space": "cosine"},
    )
    return client, collection


def build_stock_index(ticker: str, chunks: list[dict], verbose: bool = True) -> int:
    """
    Build (or rebuild) the per-stock ChromaDB index from narrative chunks.

    Args:
        ticker: e.g. "TCS"
        chunks: list of {"id": str, "section": str, "text": str}
        verbose: print progress

    Returns:
        Number of chunks indexed.
    """
    if not chunks:
        if verbose:
            print(f"[StockIndex] No chunks to index for {ticker}")
        return 0

    if verbose:
        print(f"[StockIndex] Building index for {ticker}: {len(chunks)} chunks...")

    embedder = _get_embedder()
    _, collection = _get_chroma(ticker)

    # Delete existing entries so we rebuild cleanly
    existing = collection.get()
    if existing["ids"]:
        collection.delete(ids=existing["ids"])

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [{"section": c.get("section", ""), "ticker": ticker} for c in chunks]

    # Embed all chunks
    embeddings = embedder.encode(texts, show_progress_bar=verbose).tolist()

    collection.add(
        ids=ids,
        documents=texts,
        embeddings=embeddings,
        metadatas=metadatas,
    )

    if verbose:
        print(f"[StockIndex] Indexed {len(chunks)} chunks for {ticker}")

    return len(chunks)


def query_stock_index(
    ticker: str,
    query: str,
    n_results: int = 5,
    section_filter: str | None = None,
) -> list[dict]:
    """
    Retrieve the most relevant chunks for a query from the stock's index.

    Args:
        ticker: e.g. "TCS"
        query: natural language query e.g. "cash flow quality and debt levels"
        n_results: number of chunks to return
        section_filter: if given, only return chunks from this section

    Returns:
        List of {"text": str, "section": str, "distance": float}
    """
    embedder = _get_embedder()
    _, collection = _get_chroma(ticker)

    if collection.count() == 0:
        return []

    query_embedding = embedder.encode([query]).tolist()

    where = {"section": section_filter} if section_filter else None

    results = collection.query(
        query_embeddings=query_embedding,
        n_results=min(n_results, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for i, doc in enumerate(results["documents"][0]):
        chunks.append({
            "text": doc,
            "section": results["metadatas"][0][i].get("section", ""),
            "distance": results["distances"][0][i],
        })

    return chunks


def index_exists(ticker: str) -> bool:
    """Check if a ChromaDB index exists for this ticker."""
    index_dir = CACHE_DIR / ticker.upper() / "rag_index"
    return index_dir.exists() and any(index_dir.iterdir())


def build_index_from_rag_docs(ticker: str, verbose: bool = True) -> int:
    """
    Build the ChromaDB index from rag_docs.json (section-aware chunks).

    Reads concall, annual_report, credit_rating, and announcement chunks
    that were produced by doc_fetcher.build_rag_docs(), then embeds and
    stores them in the per-stock ChromaDB collection.

    Also indexes structured Screener narrative chunks from narrative.py
    (ratios, peers, shareholding, etc.) alongside the document chunks.

    Returns total chunks indexed.
    """
    import json

    rag_docs_path = CACHE_DIR / ticker.upper() / "rag_docs.json"
    raw_path      = CACHE_DIR / ticker.upper() / "raw_full.json"

    all_chunks: list[dict] = []

    # ── 1. Document chunks from rag_docs.json ────────────────────────────────
    if rag_docs_path.exists():
        rag_docs = json.loads(rag_docs_path.read_text())

        for doc_type in ("concall", "annual_report", "credit_rating"):
            doc = rag_docs.get(doc_type) or {}
            for c in doc.get("chunks", []):
                all_chunks.append({
                    "id":      c["id"],
                    "section": c.get("section", doc_type),
                    "text":    c["text"],
                })

        for c in rag_docs.get("announcement_chunks", []):
            all_chunks.append({
                "id":      c["id"],
                "section": c.get("section", "announcement"),
                "text":    c["text"],
            })

        if verbose:
            print(f"[StockIndex] rag_docs chunks: {len(all_chunks)} "
                  f"(concall + annual + rating + announcements)")
    else:
        if verbose:
            print(f"[StockIndex] No rag_docs.json for {ticker} — "
                  f"run: python -m cache.doc_fetcher --ticker {ticker}")

    # ── 2. Structured Screener narrative chunks from raw_full.json ───────────
    if raw_path.exists():
        raw = json.loads(raw_path.read_text())
        from cache.narrative import build_narratives
        narrative_chunks = build_narratives(raw, ticker)
        before = len(all_chunks)
        all_chunks.extend(narrative_chunks)
        if verbose:
            print(f"[StockIndex] Screener narrative chunks: {len(all_chunks) - before}")

    if not all_chunks:
        if verbose:
            print(f"[StockIndex] Nothing to index for {ticker}")
        return 0

    return build_stock_index(ticker, all_chunks, verbose=verbose)


# Sections sourced from fetched documents (concall, annual report, ratings, announcements).
# Used by retrieve_stock_context(pdf_only=True) to skip Screener table chunks.
_DOC_SECTIONS = {"concall", "annual_report", "credit_rating", "announcement"}
_PDF_SECTIONS = _DOC_SECTIONS  # backwards-compat alias


def retrieve_stock_context(
    ticker: str,
    analysis_aspects: list[str] | None = None,
    max_chars: int = 4000,
    pdf_only: bool = False,
) -> str:
    """
    High-level function: retrieve relevant context from the stock's RAG index.

    Args:
        ticker: e.g. "TCS"
        analysis_aspects: list of aspects to query for
        max_chars: max total chars to return
        pdf_only: if True, only return chunks from annual_report_text / concall_text sections.
                  Use this when Screener tables are already injected directly in the prompt.

    Returns:
        Assembled context string ready to inject into the prompt.
    """
    if not index_exists(ticker):
        print(f"[StockIndex] index_exists({ticker}): False — aborting retrieval")
        return ""

    # Log collection stats before querying
    _, collection = _get_chroma(ticker)
    total_in_index = collection.count()
    print(f"[StockIndex] Collection for {ticker}: {total_in_index} total chunks")

    if total_in_index > 0:
        # Show section breakdown of what's actually in the index
        all_meta = collection.get(include=["metadatas"])
        section_counts: dict[str, int] = {}
        for m in all_meta["metadatas"]:
            s = m.get("section", "unknown")
            section_counts[s] = section_counts.get(s, 0) + 1
        print(f"[StockIndex] Sections in index: {dict(sorted(section_counts.items()))}")
        pdf_chunk_count = sum(v for k, v in section_counts.items() if k in _PDF_SECTIONS)
        print(f"[StockIndex] PDF chunks (annual_report_text/concall_text): {pdf_chunk_count}")
        if pdf_only and pdf_chunk_count == 0:
            print(f"[StockIndex] WARNING: pdf_only=True but NO PDF chunks in index.")
            print(f"[StockIndex] Fix: run 'python main.py --cache-stock {ticker} --force' to rebuild with concall PDF.")

    if analysis_aspects is None:
        analysis_aspects = [
            "revenue growth profit margin operating performance",
            "cash flow quality earnings quality OCF capital expenditure",
            "debt borrowings balance sheet financial health leverage",
            "governance promoter shareholding pledge",
            "peer comparison industry competitors valuation",
            "management commentary outlook strategy",
        ]

    seen_ids = set()
    all_chunks = []

    for aspect in analysis_aspects:
        # For pdf_only, ask for more results since most will be non-PDF and get filtered
        n = 10 if pdf_only else 3
        chunks = query_stock_index(ticker, aspect, n_results=n)
        before_filter = len(chunks)
        filtered = []
        for chunk in chunks:
            if pdf_only and chunk["section"] not in _PDF_SECTIONS:
                continue
            text = chunk["text"]
            key = text[:80]
            if key not in seen_ids:
                seen_ids.add(key)
                filtered.append(chunk)
                all_chunks.append(chunk)
        if filtered or before_filter:
            print(f"[StockIndex]   aspect='{aspect[:40]}': {before_filter} retrieved, {len(filtered)} passed filter")

    print(f"[StockIndex] Total unique chunks after filtering: {len(all_chunks)}")

    if not all_chunks:
        return ""

    # Sort by relevance (lower distance = more relevant)
    all_chunks.sort(key=lambda x: x["distance"])

    # Assemble within char budget.
    # If a chunk exceeds the remaining budget, truncate it rather than skipping
    # entirely — this prevents large chunks from blocking all output.
    parts = []
    total = 0
    for chunk in all_chunks:
        if total >= max_chars:
            break
        text = chunk["text"]
        remaining = max_chars - total
        if len(text) > remaining:
            # Truncate at a newline boundary to avoid mid-sentence cuts
            cut = text.rfind("\n", 0, remaining)
            text = text[: cut if cut > remaining // 2 else remaining]
        if text.strip():
            parts.append(text)
            total += len(text)

    if not parts:
        return ""

    return "\n\n---\n\n".join(parts)
