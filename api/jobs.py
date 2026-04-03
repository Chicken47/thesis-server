"""
Background job runner.

Jobs run in daemon threads so Flask stays responsive.
Status is persisted to Neon jobs table — clients poll /api/jobs/<id>.
"""

import threading
from api.db import update_job, save_analysis, upsert_stock, save_full_screener_data, get_latest_analysis_for_ticker, get_incremental_staleness
from api.logger import get_logger


def _clean_error(e: Exception) -> str:
    """Return a short, clean error string — never dumps HTML blobs."""
    msg = str(e)
    # Supabase/postgrest errors contain a dict with 'message' key
    if "'message'" in msg:
        try:
            import ast
            parsed = ast.literal_eval(msg)
            if isinstance(parsed, dict):
                return parsed.get("message", msg)[:300]
        except Exception:
            pass
    # Truncate anything too long (e.g. Cloudflare HTML in error_message)
    return msg[:300]

log = get_logger(__name__)


def run_analyze_job(job_id: str, ticker: str, screener_path: str) -> None:
    """
    Full analysis pipeline in a background thread:
      1. Live scrape (Screener + Google Finance)
      2. Run Claude analysis with RAG
      3. Save result to Supabase analyses table
      4. Mark job done
    """
    def _run():
        ctx = {"job_id": job_id[:8], "ticker": ticker}
        try:
            log.info("Analyze job started", extra=ctx)
            update_job(job_id, "running")

            # Step 1: Scrape
            log.info("Step 1/4 — scraping Screener.in", extra={**ctx, "path": screener_path})
            from scraper_bridge import fetch_compact_snapshot
            snapshot = fetch_compact_snapshot(screener_path)
            log.debug("Scrape complete", extra={**ctx, "keys": list(snapshot.keys())})

            # Step 2: Upsert stock record
            company = snapshot.get("companyName", ticker)
            log.info("Step 2/4 — upserting stock record", extra={**ctx, "company": company})
            upsert_stock(ticker, name=company, screener_path=screener_path)

            # Step 3: Run Claude analysis
            log.info("Step 3/4 — running Claude analysis (3-5 min)", extra=ctx)
            from analysis.pipeline import analyze_stock
            result = analyze_stock(ticker, snapshot, verbose=False)

            if result.get("error"):
                log.error("Claude analysis returned error", extra={**ctx, "error": result["error"]})
                update_job(job_id, "failed", error_message=result["error"])
                return

            log.info(
                "Analysis complete",
                extra={**ctx, "verdict": result.get("verdict"), "conviction": result.get("conviction")},
            )

            # Step 4: Persist to Neon
            log.info("Step 4/4 — saving to Neon", extra=ctx)
            analysis_id = save_analysis(ticker, result)
            update_job(job_id, "done", result_id=analysis_id)
            log.info("Analyze job DONE", extra={**ctx, "analysis_id": analysis_id[:8]})

        except Exception as e:
            log.error("Analyze job FAILED", extra=ctx, exc_info=True)
            update_job(job_id, "failed", error_message=_clean_error(e))

    threading.Thread(target=_run, daemon=True, name=f"analyze-{ticker}").start()
    log.debug("Analyze thread spawned", extra={"job_id": job_id[:8], "ticker": ticker})


def run_cache_stock_job(
    job_id: str,
    ticker: str,
    screener_path: str,
    force: bool = False,
) -> None:
    """
    Full stock cache build in a background thread:
      1. Full Screener scrape (raw_full.json)
      2. Fetch + chunk PDFs (rag_docs.json)
      3. Build ChromaDB index on local disk
      4. Mark job done
    """
    def _run():
        ctx = {"job_id": job_id[:8], "ticker": ticker, "force": force}
        try:
            log.info("Cache job started", extra=ctx)
            update_job(job_id, "running")

            from cache.stock_store import get_or_fetch, build_rag_docs_for_ticker
            from rag.stock_indexer import build_index_from_rag_docs

            # Step 1: Full scrape
            log.info("Step 1/4 — full Screener scrape", extra={**ctx, "path": screener_path})
            raw = get_or_fetch(ticker, screener_path, force=force, verbose=False)
            log.debug("Scrape complete", extra={**ctx, "company": raw.get("companyName")})

            # Step 2: Upsert stock + persist screener snapshot to Neon
            log.info("Step 2/4 — upserting stock record", extra=ctx)
            upsert_stock(
                ticker,
                name=raw.get("companyName", ticker),
                screener_path=screener_path,
            )
            save_full_screener_data(ticker, raw)

            # Step 3: Fetch + chunk PDFs
            log.info("Step 3/4 — downloading + chunking PDFs (slow)", extra=ctx)
            build_rag_docs_for_ticker(ticker, force=force, verbose=False)

            # Step 4: Build ChromaDB vector index
            log.info("Step 4/4 — building ChromaDB vector index", extra=ctx)
            build_index_from_rag_docs(ticker, verbose=False)

            update_job(job_id, "done")
            log.info("Cache job DONE", extra=ctx)

        except Exception as e:
            log.error("Cache job FAILED", extra=ctx, exc_info=True)
            update_job(job_id, "failed", error_message=_clean_error(e))

    threading.Thread(target=_run, daemon=True, name=f"cache-{ticker}").start()
    log.debug("Cache thread spawned", extra={"job_id": job_id[:8], "ticker": ticker})


def run_incremental_analyze_job(job_id: str, ticker: str) -> None:
    """
    Incremental reanalysis in a background thread:
      1. Fetch latest analysis from DB
      2. Call incremental pipeline (news + macro only, no full scrape)
      3. If model flags new quarterly results → fall back to full analysis
      4. Save result + update job
    """
    def _run():
        ctx = {"job_id": job_id[:8], "ticker": ticker}
        try:
            log.info("Incremental analyze job started", extra=ctx)
            update_job(job_id, "running")

            # Step 1: Get previous analysis
            previous = get_latest_analysis_for_ticker(ticker)
            if not previous:
                log.warning("No previous analysis — falling back to full analyze", extra=ctx)
                update_job(job_id, "failed", error_message="No previous analysis found. Run full analysis first.")
                return

            # Step 1b: Hard cap — force full analysis if stale
            staleness = get_incremental_staleness(ticker)
            consecutive = staleness["consecutive_incrementals"]
            days_since_full = staleness["days_since_full"]
            CAP_INCREMENTS = 5
            CAP_DAYS = 30
            if consecutive >= CAP_INCREMENTS or (days_since_full is not None and days_since_full >= CAP_DAYS):
                reason = (
                    f"{consecutive} consecutive incrementals" if consecutive >= CAP_INCREMENTS
                    else f"{days_since_full} days since last full analysis"
                )
                log.info("Hard cap reached — forcing full analysis", extra={**ctx, "reason": reason})
                update_job(job_id, "failed", error_message=f"REQUIRES_FULL_ANALYSIS: Hard cap ({reason})")
                return

            # Step 2: Run incremental pipeline
            from analysis.incremental import incremental_reanalysis
            result = incremental_reanalysis(ticker, previous, verbose=False)

            if result.get("error"):
                log.error("Incremental analysis error", extra={**ctx, "error": result["error"]})
                update_job(job_id, "failed", error_message=result["error"])
                return

            # Step 3: If model detected new quarterly results, flag for full reanalysis
            if result.get("requires_full_analysis"):
                reason = result.get("reason", "New quarterly results detected")
                log.info("Incremental flagged full reanalysis needed", extra={**ctx, "reason": reason})
                update_job(job_id, "failed", error_message=f"REQUIRES_FULL_ANALYSIS: {reason}")
                return

            # Step 4: Save
            log.info("Incremental analysis complete — saving to DB", extra={
                **ctx,
                "verdict": result.get("verdict"),
                "conviction": result.get("conviction"),
                "changes": len(result.get("changes_made", [])),
                "is_incremental": result.get("is_incremental"),
                "based_on": str(result.get("based_on_analysis_id", ""))[:8],
            })
            log.debug("Incremental result keys", extra={**ctx, "keys": list(result.keys())})
            analysis_id = save_analysis(ticker, result)
            log.info("save_analysis returned", extra={**ctx, "analysis_id": analysis_id[:8] if analysis_id else "None"})
            update_job(job_id, "done", result_id=analysis_id)
            log.info("Incremental job DONE", extra={**ctx, "analysis_id": analysis_id[:8] if analysis_id else "None"})

        except Exception as e:
            log.error("Incremental job FAILED", extra=ctx, exc_info=True)
            update_job(job_id, "failed", error_message=_clean_error(e))

    threading.Thread(target=_run, daemon=True, name=f"refresh-{ticker}").start()
    log.debug("Incremental thread spawned", extra={"job_id": job_id[:8], "ticker": ticker})
