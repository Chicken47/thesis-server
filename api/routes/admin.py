from flask import Blueprint, jsonify, request
from api.db import create_job, save_full_screener_data, upsert_stock
from api.jobs import run_cache_stock_job, run_analyze_job
from cache.stock_store import get_or_fetch
from rag.stock_indexer import index_exists
from api.logger import get_logger

log = get_logger(__name__)

admin_bp = Blueprint("admin", __name__)


@admin_bp.post("/cache-stock/<ticker>")
def cache_stock(ticker: str):
    ticker = ticker.upper()
    body = request.get_json(silent=True) or {}
    screener_path = body.get("screener_path") or f"/company/{ticker}/consolidated/"
    force = bool(body.get("force", False))

    log.info("Triggering cache build", extra={"ticker": ticker, "force": force})
    job_id = create_job(ticker, "cache_stock")
    run_cache_stock_job(job_id, ticker, screener_path, force=force)

    return jsonify({
        "job_id": job_id,
        "ticker": ticker,
        "status": "queued",
        "message": f"Cache build started for {ticker}. Poll GET /api/jobs/{job_id}.",
    }), 202


@admin_bp.post("/analyze/<ticker>")
def trigger_analysis(ticker: str):
    ticker = ticker.upper()
    body = request.get_json(silent=True) or {}
    screener_path = body.get("screener_path") or f"/company/{ticker}/consolidated/"

    rag_ready = index_exists(ticker)
    log.info("Triggering analysis", extra={"ticker": ticker, "rag_ready": rag_ready})
    job_id = create_job(ticker, "analyze")
    run_analyze_job(job_id, ticker, screener_path)

    return jsonify({
        "job_id": job_id,
        "ticker": ticker,
        "status": "queued",
        "rag_available": rag_ready,
        "message": f"Analysis started. Poll GET /api/jobs/{job_id}.",
    }), 202


@admin_bp.get("/rag-status/<ticker>")
def rag_status(ticker: str):
    """Check if RAG index exists for a ticker — no job started."""
    from rag.stock_indexer import index_exists
    return jsonify({"rag_available": index_exists(ticker.upper())})


@admin_bp.post("/admin/purge-stock/<ticker>")
def purge_stock_cache(ticker: str):
    """Delete the entire stock_cache directory for one ticker."""
    import shutil
    from pathlib import Path
    ticker = ticker.upper()
    stock_dir = Path(__file__).parent.parent.parent / "stock_cache" / ticker
    removed = False
    if stock_dir.exists() and stock_dir.is_dir():
        shutil.rmtree(stock_dir)
        removed = True
    log.info("Purged stock cache", extra={"ticker": ticker, "removed": removed})
    return jsonify({"ticker": ticker, "cache_removed": removed})


@admin_bp.post("/admin/purge-rag")
def purge_rag():
    """Delete all ChromaDB RAG index directories from stock_cache."""
    import shutil
    from pathlib import Path
    cache_dir = Path(__file__).parent.parent.parent / "stock_cache"
    deleted = []
    if cache_dir.exists():
        for rag_dir in cache_dir.glob("*/rag_index"):
            if rag_dir.is_dir():
                shutil.rmtree(rag_dir)
                deleted.append(rag_dir.parent.name)
    log.info("Purged RAG indexes", extra={"tickers": deleted})
    return jsonify({"deleted": deleted, "count": len(deleted)})


@admin_bp.post("/admin/update-macro")
def update_macro():
    """Fetch macro context for a single type ('global' or 'india') via Claude web search."""
    body = request.get_json(silent=True) or {}
    macro_type = body.get("type")
    if macro_type not in ("global", "india"):
        return jsonify({"ok": False, "error": "type must be 'global' or 'india'"}), 400
    try:
        from scripts.update_macro import update_macro_one
        update_macro_one(macro_type, verbose=False)
        return jsonify({"ok": True, "type": macro_type})
    except Exception as e:
        log.error("Macro update failed", extra={"type": macro_type}, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


@admin_bp.get("/screener-data/<ticker>")
def screener_data(ticker: str):
    """Scrape live from Screener.in, persist to DB, return raw JSON."""
    upper = ticker.upper()
    try:
        screener_path = f"/company/{upper}/consolidated/"
        raw = get_or_fetch(upper, screener_path, force=False, verbose=False)
        # get_or_fetch auto-retries with standalone if consolidated has no financials;
        # use the path that's actually stored in raw (may differ from initial request)
        actual_path = raw.get("screenerPath") or screener_path
        upsert_stock(upper, name=raw.get("companyName", upper), screener_path=actual_path)
        save_full_screener_data(upper, raw)
        return jsonify(raw)
    except Exception as e:
        log.error("Live screener scrape failed", extra={"ticker": upper}, exc_info=True)
        return jsonify({"error": str(e)}), 500
