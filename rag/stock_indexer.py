"""
Per-stock RAG indexer using ChromaDB + sentence-transformers.

Builds a vector index from all narrative chunks for a given stock.
At query time, retrieves the most relevant chunks for the analysis question.

Index lives at: stock_cache/{TICKER}/rag_index/

Embedding model : BAAI/bge-small-en-v1.5  (133 MB, significantly better retrieval
                   than all-MiniLM-L6-v2; uses query instruction prefix)
Reranking model : cross-encoder/ms-marco-MiniLM-L-6-v2 (84 MB)

Collections (one per doc type, separate namespace inside one PersistentClient):
  concall | annual_report | credit_rating | announcement

Screener narrative chunks are NOT stored here — they are injected directly into
the prompt by prompt_builder.py, so indexing them is wasted space.
"""

from __future__ import annotations

from pathlib import Path

CACHE_DIR    = Path(__file__).parent.parent / "stock_cache"
EMBED_MODEL  = "BAAI/bge-small-en-v1.5"
RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

# BGE query instruction (applied to query texts only, NOT to indexed documents)
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

# Model version sentinel — if the rag_index was built with a different model we
# auto-wipe it so the caller gets a clean rebuild rather than garbage results.
_MODEL_VERSION = f"{EMBED_MODEL}|{RERANK_MODEL}|collections-v2"

# Document section → ChromaDB collection name mapping
_SECTION_TO_COLLECTION = {
    "concall":       "concall",
    "annual_report": "annual_report",
    "credit_rating": "credit_rating",
    "announcement":  "announcement",
}
_ALL_COLLECTIONS  = list(dict.fromkeys(_SECTION_TO_COLLECTION.values()))
_DOC_SECTIONS     = frozenset(_SECTION_TO_COLLECTION.keys())
_PDF_SECTIONS     = _DOC_SECTIONS  # backwards-compat alias

# Lazy-loaded singletons
_embedder = None
_reranker = None


# ─────────────────────────────────────────────────────────────────────────────
# Model helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        print(f"[StockIndex] Loading embedding model: {EMBED_MODEL}")
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def _get_reranker():
    global _reranker
    if _reranker is None:
        from sentence_transformers import CrossEncoder
        print(f"[StockIndex] Loading reranking model: {RERANK_MODEL}")
        _reranker = CrossEncoder(RERANK_MODEL)
    return _reranker


def _embed_documents(texts: list[str], show_progress: bool = False) -> list:
    """Embed passage/document texts. BGE does NOT use a prefix for documents."""
    return _get_embedder().encode(texts, show_progress_bar=show_progress).tolist()


def _embed_queries(texts: list[str]) -> list:
    """Embed query texts with the BGE retrieval instruction prefix."""
    prefixed = [f"{_BGE_QUERY_PREFIX}{t}" for t in texts]
    return _get_embedder().encode(prefixed).tolist()


# ─────────────────────────────────────────────────────────────────────────────
# ChromaDB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _model_version_file(ticker: str) -> Path:
    return CACHE_DIR / ticker.upper() / "rag_index" / ".model_version"


def _check_and_wipe_if_stale(ticker: str) -> None:
    """Wipe the rag_index directory if it was built with a different model."""
    import shutil
    index_dir = CACHE_DIR / ticker.upper() / "rag_index"
    ver_file  = _model_version_file(ticker)
    if index_dir.exists():
        current = ver_file.read_text().strip() if ver_file.exists() else ""
        if current != _MODEL_VERSION:
            print(f"[StockIndex] Model version mismatch for {ticker} — wiping stale index")
            shutil.rmtree(index_dir)


def _get_chroma_client(ticker: str):
    """Return a PersistentClient for this ticker's rag_index directory."""
    import chromadb, shutil
    index_dir = CACHE_DIR / ticker.upper() / "rag_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    try:
        return chromadb.PersistentClient(path=str(index_dir))
    except ValueError:
        # Corrupted / old-format index — wipe and recreate
        shutil.rmtree(index_dir)
        index_dir.mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=str(index_dir))


def _get_collection(client, name: str):
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Index building
# ─────────────────────────────────────────────────────────────────────────────

def build_stock_index(ticker: str, chunks: list[dict], verbose: bool = True) -> int:
    """
    Build (or rebuild) the per-stock ChromaDB index from document chunks.

    Chunks are routed to separate collections by section:
      concall | annual_report | credit_rating | announcement

    Args:
        ticker:  e.g. "TCS"
        chunks:  list of {"id": str, "section": str, "text": str}
        verbose: print progress

    Returns:
        Total number of chunks indexed.
    """
    if not chunks:
        if verbose:
            print(f"[StockIndex] No chunks to index for {ticker}")
        return 0

    # Bucket chunks by collection name, skipping any unknown sections
    buckets: dict[str, list[dict]] = {name: [] for name in _ALL_COLLECTIONS}
    skipped = 0
    for c in chunks:
        col_name = _SECTION_TO_COLLECTION.get(c.get("section", ""))
        if col_name:
            buckets[col_name].append(c)
        else:
            skipped += 1

    if verbose:
        summary = {k: len(v) for k, v in buckets.items() if v}
        print(f"[StockIndex] Building index for {ticker}: {len(chunks)} chunks "
              f"→ {summary} (skipped {skipped} non-doc chunks)")

    client    = _get_chroma_client(ticker)
    total_indexed = 0

    for col_name, col_chunks in buckets.items():
        if not col_chunks:
            continue

        collection = _get_collection(client, col_name)

        # Full rebuild: delete existing entries in this collection
        existing = collection.get()
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        texts      = [c["text"] for c in col_chunks]
        ids        = [c["id"]   for c in col_chunks]
        metadatas  = [{"section": c.get("section", col_name), "ticker": ticker}
                      for c in col_chunks]

        # Embed documents (no query prefix for BGE)
        embeddings = _embed_documents(texts, show_progress=verbose)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        total_indexed += len(col_chunks)
        if verbose:
            print(f"[StockIndex]   {col_name}: {len(col_chunks)} chunks indexed")

    # Write model version sentinel
    _model_version_file(ticker).write_text(_MODEL_VERSION)

    if verbose:
        print(f"[StockIndex] Done — {total_indexed} total chunks for {ticker}")

    return total_indexed


# ─────────────────────────────────────────────────────────────────────────────
# Query & reranking
# ─────────────────────────────────────────────────────────────────────────────

def query_stock_index(
    ticker: str,
    query: str,
    n_results: int = 5,
    sections: list[str] | None = None,
    section_filter: str | None = None,  # legacy compat
) -> list[dict]:
    """
    Retrieve the most relevant chunks for a query.

    Queries the specified section collections (default: all 4).
    Returns merged list of {"text": str, "section": str, "distance": float}.

    Args:
        ticker:        e.g. "TCS"
        query:         natural language query
        n_results:     results to fetch per collection
        sections:      list of section names to query; None = all
        section_filter: legacy single-section filter (maps to sections=[section_filter])
    """
    # Legacy compat
    if section_filter and not sections:
        sections = [section_filter]

    target_collections = sections if sections else _ALL_COLLECTIONS

    client       = _get_chroma_client(ticker)
    query_emb    = _embed_queries([query])[0]
    all_chunks: list[dict] = []

    for col_name in target_collections:
        try:
            collection = _get_collection(client, col_name)
            count = collection.count()
            if count == 0:
                continue
            results = collection.query(
                query_embeddings=[query_emb],
                n_results=min(n_results, count),
                include=["documents", "metadatas", "distances"],
            )
            for i, doc in enumerate(results["documents"][0]):
                all_chunks.append({
                    "text":     doc,
                    "section":  results["metadatas"][0][i].get("section", col_name),
                    "distance": results["distances"][0][i],
                })
        except Exception:
            pass  # collection may not exist if that doc type was never fetched

    return all_chunks


def _rerank(queries: list[str], chunks: list[dict], top_k: int) -> list[dict]:
    """
    Rerank candidate chunks using the cross-encoder.

    For each chunk, scores it against every query and takes the max score
    (a chunk is good if it's relevant to any of the analysis aspects).
    Returns top_k chunks sorted by descending rerank score.
    """
    if not chunks:
        return chunks

    reranker = _get_reranker()
    chunk_texts = [c["text"] for c in chunks]
    best: dict[int, float] = {i: -1e9 for i in range(len(chunks))}

    for q in queries:
        pairs  = [(q, t) for t in chunk_texts]
        scores = reranker.predict(pairs)
        for i, score in enumerate(scores):
            if float(score) > best[i]:
                best[i] = float(score)

    for i, chunk in enumerate(chunks):
        chunk["rerank_score"] = best[i]

    chunks.sort(key=lambda x: x["rerank_score"], reverse=True)
    return chunks[:top_k]


# ─────────────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────────────

def index_exists(ticker: str) -> bool:
    """Check if a usable ChromaDB index exists for this ticker."""
    index_dir = CACHE_DIR / ticker.upper() / "rag_index"
    if not index_dir.exists():
        return False
    ver_file = _model_version_file(ticker)
    if not ver_file.exists() or ver_file.read_text().strip() != _MODEL_VERSION:
        return False
    return any(index_dir.iterdir())


def build_index_from_rag_docs(ticker: str, verbose: bool = True) -> int:
    """
    Build the ChromaDB index from rag_docs.json.

    Reads concall, annual_report, credit_rating, and announcement chunks
    produced by doc_fetcher.build_rag_docs(), embeds them, and stores in
    per-section ChromaDB collections.

    Screener narrative chunks are intentionally NOT indexed here — they are
    injected directly into the analysis prompt by prompt_builder.py.

    Returns total chunks indexed.
    """
    import json

    # Wipe stale index built with a different model
    _check_and_wipe_if_stale(ticker)

    rag_docs_path = CACHE_DIR / ticker.upper() / "rag_docs.json"

    all_chunks: list[dict] = []

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

    if not all_chunks:
        if verbose:
            print(f"[StockIndex] Nothing to index for {ticker}")
        return 0

    return build_stock_index(ticker, all_chunks, verbose=verbose)


# ─────────────────────────────────────────────────────────────────────────────
# High-level retrieval
# ─────────────────────────────────────────────────────────────────────────────

def retrieve_stock_context(
    ticker: str,
    analysis_aspects: list[str] | None = None,
    max_chars: int = 8000,
    pdf_only: bool = False,
) -> str:
    """
    Retrieve relevant context from the stock's RAG index.

    Pipeline:
      1. For each analysis aspect, query all section collections (top-10 each)
      2. Deduplicate by text prefix
      3. Cross-encoder rerank against all aspects (takes max score per chunk)
      4. Assemble top chunks within the max_chars budget

    Args:
        ticker:           e.g. "TCS"
        analysis_aspects: list of aspect queries (default: 6 standard aspects)
        max_chars:        max total characters to return (default: 8000)
        pdf_only:         ignored — this indexer only stores PDF doc sections now

    Returns:
        Assembled context string ready to inject into the prompt.
    """
    if not index_exists(ticker):
        print(f"[StockIndex] No valid index for {ticker} — skipping retrieval")
        return ""

    if analysis_aspects is None:
        analysis_aspects = [
            "revenue growth profit margin operating performance",
            "cash flow quality earnings quality OCF capital expenditure",
            "debt borrowings balance sheet financial health leverage",
            "governance promoter shareholding pledge",
            "peer comparison industry competitors valuation",
            "management commentary outlook guidance strategy",
        ]

    # ── 1. Collect candidates from all collections ─────────────────────────
    seen_keys: set[str] = set()
    candidates: list[dict] = []

    # Log collection stats
    client = _get_chroma_client(ticker)
    for col_name in _ALL_COLLECTIONS:
        try:
            col = _get_collection(client, col_name)
            cnt = col.count()
            if cnt:
                print(f"[StockIndex]   {col_name}: {cnt} chunks")
        except Exception:
            pass

    for aspect in analysis_aspects:
        chunks = query_stock_index(ticker, aspect, n_results=10)
        new = 0
        for chunk in chunks:
            key = chunk["text"][:80]
            if key not in seen_keys:
                seen_keys.add(key)
                candidates.append(chunk)
                new += 1
        print(f"[StockIndex]   aspect '{aspect[:45]}': +{new} new candidates")

    print(f"[StockIndex] {len(candidates)} unique candidates before reranking")

    if not candidates:
        return ""

    # ── 2. Cross-encoder rerank ─────────────────────────────────────────────
    # Rerank against all aspects, take max score per chunk; keep top 25
    top_chunks = _rerank(analysis_aspects, candidates, top_k=25)
    print(f"[StockIndex] Reranked → keeping top {len(top_chunks)} chunks")

    # ── 3. Assemble within char budget ──────────────────────────────────────
    parts: list[str] = []
    total = 0
    for chunk in top_chunks:
        if total >= max_chars:
            break
        text      = chunk["text"]
        remaining = max_chars - total
        if len(text) > remaining:
            cut  = text.rfind("\n", 0, remaining)
            text = text[: cut if cut > remaining // 2 else remaining]
        if text.strip():
            parts.append(text)
            total += len(text)

    if not parts:
        return ""

    print(f"[StockIndex] Assembled {total} chars from {len(parts)} chunks")
    return "\n\n---\n\n".join(parts)
