"""
RAG retrieval — per-stock vector retrieval via ChromaDB.

The analytical framework (sector benchmarks, governance rules, verdict thresholds)
is now baked directly into the prompt. This module only fetches stock-specific
context: financial trends, peer comparison, news — from the per-stock index.

If no index exists for a stock (run: python main.py --cache-stock TICKER),
returns empty context and the prompt falls back to the compact snapshot alone.
"""


def retrieve_context(stock_symbol: str, sector: str = "", company_about: str = "") -> dict:  # noqa: ARG001
    """
    Retrieve stock-specific context via vector RAG.

    Returns dict with 'assembled' key containing the retrieved context string.
    """
    stock_rag_text = ""
    try:
        from rag.stock_indexer import index_exists, retrieve_stock_context
        exists = index_exists(stock_symbol)
        print(f"[RAG] index_exists({stock_symbol}): {exists}")
        if exists:
            # pdf_only=True: Screener tables are injected directly by prompt_builder.
            # RAG budget is reserved for document chunks only:
            # concall, annual_report, credit_rating, announcement.
            stock_rag_text = retrieve_stock_context(
                stock_symbol, max_chars=25000, pdf_only=True
            )
            print(f"[RAG] retrieve_stock_context returned: {len(stock_rag_text)} chars")
        else:
            print(f"[RAG] No index for {stock_symbol} — run: python -m cache.doc_fetcher --ticker {stock_symbol}"
                  f" then rebuild index via main.py --cache-stock")
    except Exception as e:
        print(f"[RAG] Exception during retrieval: {type(e).__name__}: {e}")

    assembled = stock_rag_text

    return {
        "assembled": assembled,
        "sector_canonical": sector,
        "stock_rag_context": stock_rag_text,
        "sector_context": "",
        "governance_context": "",
        "macro_context": "",
        "template_context": "",
    }


if __name__ == "__main__":
    ctx = retrieve_context("TCS", sector="IT")
    print(f"Assembled context: {len(ctx['assembled'])} chars")
    if ctx["assembled"]:
        print(ctx["assembled"][:500])
    else:
        print("(no per-stock index — run: python main.py --cache-stock TCS)")