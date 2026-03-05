"""
Per-stock cache store.
Manages stock_cache/{TICKER}/ directory structure.
Detects stale data (new quarter) and triggers re-scrape when needed.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).parent.parent / "stock_cache"
SCRAPER_SCRIPT = Path(__file__).parent.parent / "scraper" / "run_full_scrape.js"


def _stock_dir(ticker: str) -> Path:
    return CACHE_DIR / ticker.upper()


def _meta_path(ticker: str) -> Path:
    return _stock_dir(ticker) / "meta.json"


def _raw_path(ticker: str) -> Path:
    return _stock_dir(ticker) / "raw_full.json"


def _pdf_extracts_path(ticker: str) -> Path:
    return _stock_dir(ticker) / "pdf_extracts.json"


def _rag_docs_path(ticker: str) -> Path:
    return _stock_dir(ticker) / "rag_docs.json"


def load_rag_docs(ticker: str) -> dict | None:
    """Load rag_docs.json for a ticker. Returns None if not built yet."""
    path = _rag_docs_path(ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_rag_docs(ticker: str, data: dict) -> None:
    """Save rag_docs.json for a ticker."""
    _stock_dir(ticker).mkdir(parents=True, exist_ok=True)
    _rag_docs_path(ticker).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def build_rag_docs_for_ticker(ticker: str, force: bool = False, verbose: bool = True) -> dict:
    """
    Fetch and build rag_docs.json (concall, annual report, credit rating, announcements).
    Requires raw_full.json to already exist.
    """
    raw = load_raw(ticker)
    if not raw:
        if verbose:
            print(f"[StockStore] No cached data for {ticker} — run --cache-stock first")
        return {}

    from cache.doc_fetcher import build_rag_docs
    result = build_rag_docs(ticker, raw, verbose=verbose, force=force)
    return result


def _current_quarter() -> str:
    """Return current quarter string e.g. '2025Q1'."""
    now = datetime.now(timezone.utc)
    q = (now.month - 1) // 3 + 1
    return f"{now.year}Q{q}"


def load_pdf_extracts(ticker: str) -> dict:
    """Load previously extracted PDF text. Returns {} if not cached."""
    path = _pdf_extracts_path(ticker)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_pdf_extracts(ticker: str, extracts: dict) -> None:
    """Save PDF text extracts to cache."""
    _stock_dir(ticker).mkdir(parents=True, exist_ok=True)
    _pdf_extracts_path(ticker).write_text(json.dumps(extracts, indent=2))


def extract_pdfs_for_ticker(ticker: str, verbose: bool = True) -> dict:
    """
    Extract text from the latest annual report + concall transcript for this ticker.
    Saves result to pdf_extracts.json and returns it.
    """
    raw = load_raw(ticker)
    if not raw:
        if verbose:
            print(f"[StockStore] No cached data for {ticker} — run --cache-stock first")
        return {}

    docs = raw.get("documents", [])
    if not docs:
        if verbose:
            print(f"[StockStore] No documents found for {ticker}")
        return {}

    from cache.pdf_extractor import extract_key_pdfs
    extracts = extract_key_pdfs(docs, verbose=verbose)
    save_pdf_extracts(ticker, extracts)

    if verbose:
        print(f"[StockStore] PDF extracts saved for {ticker}: {len(extracts)} document(s)")

    return extracts


def load_meta(ticker: str) -> dict:
    path = _meta_path(ticker)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def is_cache_fresh(ticker: str) -> bool:
    """Return True if cached data is from the current quarter."""
    meta = load_meta(ticker)
    return meta.get("quarter") == _current_quarter()


def load_raw(ticker: str) -> dict | None:
    """Load the full raw JSON from cache. Returns None if not cached."""
    path = _raw_path(ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save_raw(ticker: str, data: dict) -> None:
    """Save raw full scrape data to cache."""
    d = _stock_dir(ticker)
    d.mkdir(parents=True, exist_ok=True)

    _raw_path(ticker).write_text(json.dumps(data, indent=2))

    meta = {
        "ticker": ticker.upper(),
        "screener_path": data.get("screenerPath", ""),
        "quarter": _current_quarter(),
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "has_pl": bool(data.get("annualPL", {}).get("values")),
        "has_bs": bool(data.get("balanceSheet", {}).get("values")),
        "has_cf": bool(data.get("cashFlows", {}).get("values")),
        "has_peers": bool(data.get("peerComparison", {}).get("peers")),
        "doc_count": len(data.get("documents", [])),
        "news_count": len(data.get("news", [])),
    }
    _meta_path(ticker).write_text(json.dumps(meta, indent=2))


def fetch_and_cache(ticker: str, screener_path: str, verbose: bool = True) -> dict:
    """
    Run the Node.js full scraper and save result to cache.
    Returns the raw data dict.
    """
    if verbose:
        print(f"[StockStore] Running full scrape for {ticker}...")

    result = subprocess.run(
        ["node", str(SCRAPER_SCRIPT), screener_path],
        capture_output=True,
        text=True,
        timeout=180,  # 3 min — full scrape takes longer than compact
        cwd=str(Path(__file__).parent.parent),
    )

    if result.returncode != 0:
        raise RuntimeError(f"Full scraper failed: {result.stderr[:500]}")

    if verbose and result.stderr:
        for line in result.stderr.strip().split("\n"):
            print(f"  {line}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse full scraper output: {e}")

    save_raw(ticker, data)

    if verbose:
        meta = load_meta(ticker)
        print(f"[StockStore] Cached {ticker}: P&L={meta.get('has_pl')}, BS={meta.get('has_bs')}, CF={meta.get('has_cf')}, Peers={meta.get('has_peers')}, Docs={meta.get('doc_count')}")

    return data


def get_or_fetch(ticker: str, screener_path: str, force: bool = False, verbose: bool = True) -> dict:
    """
    Return cached data if fresh, otherwise run full scrape.
    This is the main entry point for the pipeline to use.
    """
    if not force and is_cache_fresh(ticker):
        raw = load_raw(ticker)
        if raw is not None:
            if verbose:
                print(f"[StockStore] Using cached data for {ticker} (quarter: {_current_quarter()})")
            return raw

    return fetch_and_cache(ticker, screener_path, verbose=verbose)


def cache_exists(ticker: str) -> bool:
    return _raw_path(ticker).exists()


def list_cached() -> list[str]:
    if not CACHE_DIR.exists():
        return []
    return sorted([d.name for d in CACHE_DIR.iterdir() if d.is_dir()])
