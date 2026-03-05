"""
Main entry point for the stock analysis system.

Usage:
  # First time: build the knowledge base index
  python main.py --build-index

  # Analyze a stock (by Screener path)
  python main.py --analyze /company/INFY/consolidated/

  # Analyze a stock (by ticker, tries to find Screener path)
  python main.py --ticker INFY

  # Rebuild index and analyze
  python main.py --build-index --analyze /company/TCS/consolidated/
"""

import argparse
import json
import sys
import os


def cmd_build_index(force: bool = False):
    print("Building ChromaDB index from knowledge base files...")
    from rag.ingest import build_index
    build_index(force_rebuild=force)


def cmd_cache_stock(ticker: str, screener_path: str = "", force: bool = False):
    """
    Fetch full data for a stock, build narrative chunks, and index into ChromaDB.
    One-time setup per stock — refreshed quarterly with --force.
    """
    ticker = ticker.upper()
    if not screener_path:
        screener_path = f"/company/{ticker}/consolidated/"

    print(f"\n[Cache] Setting up per-stock RAG for {ticker}")
    print(f"[Cache] Screener path: {screener_path}")

    # Step 1: Full scrape (or load from cache)
    from cache.stock_store import get_or_fetch, _stock_dir
    raw = get_or_fetch(ticker, screener_path, force=force, verbose=True)

    # Step 2: Print a full breakdown of everything captured
    _print_cache_summary(ticker, raw)

    # Step 3: Save a clean structured export JSON
    export_path = _stock_dir(ticker) / "screener_export.json"
    export_data = {
        "ticker": ticker,
        "screener_path": screener_path,
        "scraped_at": raw.get("scrapedAt", ""),
        "about": raw.get("aboutText", ""),
        "ratios": raw.get("ratios", []),
        "pros": raw.get("prosConsData", {}).get("pros", []),
        "cons": raw.get("prosConsData", {}).get("cons", []),
        "quarterly": raw.get("quartersData", {}),
        "annual_pl": raw.get("annualPL", {}),
        "balance_sheet": raw.get("balanceSheet", {}),
        "cash_flows": raw.get("cashFlows", {}),
        "ratios_history": raw.get("ratiosHistory", {}),
        "shareholding": raw.get("shareholding", []),
        "peer_comparison": raw.get("peerComparison", {}),
        "documents": raw.get("documents", []),
        "news": raw.get("news", []),
    }
    export_path.write_text(json.dumps(export_data, indent=2))
    print(f"\n[Cache] Full data exported → {export_path}")

    # Step 4: Fetch RAG documents (concall, annual report, credit rating, announcements)
    print(f"\n[Cache] Fetching RAG documents (concall, annual report, credit rating)...")
    print("  (Downloads PDFs from BSE — may take 60-120s for annual report)")
    from cache.stock_store import build_rag_docs_for_ticker
    build_rag_docs_for_ticker(ticker, force=force, verbose=True)

    # Step 5: Build ChromaDB index from rag_docs.json + Screener narratives
    print(f"\n[Cache] Building ChromaDB index (section-aware chunks + Screener narratives)...")
    print("  (First run downloads the embedding model ~22MB — subsequent runs are instant)")
    from rag.stock_indexer import build_index_from_rag_docs
    n = build_index_from_rag_docs(ticker, verbose=True)
    print(f"\n[Cache] {ticker} ready. {n} total chunks indexed.")
    print(f"   Refresh quarterly with: python main.py --cache-stock {ticker} --force")


def _print_cache_summary(ticker: str, raw: dict):
    """Print a detailed breakdown of every section captured from Screener."""
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"  DATA CAPTURED FOR {ticker}")
    print(sep)

    # About
    about = raw.get("aboutText", "")
    print(f"\n📋 About: {len(about)} chars")
    if about:
        print(f"   {about[:120]}...")

    # Key ratios
    ratios = raw.get("ratios", [])
    print(f"\n📊 Key Ratios: {len(ratios)} items")
    for r in ratios:
        if r.get("name") and r.get("value"):
            print(f"   {r['name']}: {r['value']}")

    # Pros/Cons
    pros = raw.get("prosConsData", {}).get("pros", [])
    cons = raw.get("prosConsData", {}).get("cons", [])
    print(f"\n✅ Pros ({len(pros)}):  ❌ Cons ({len(cons)}):")
    for p in pros[:4]:
        print(f"   + {p[:80]}")
    for c in cons[:4]:
        print(f"   - {c[:80]}")

    # Quarterly
    q = raw.get("quartersData", {})
    q_heads = q.get("headings", [])
    q_vals = q.get("values", [])
    print(f"\n📅 Quarterly Data: {len(q_vals)} rows × {len(q_heads)} quarters")
    if q_heads:
        print(f"   Quarters: {', '.join(q_heads[:8])}")
    for row in q_vals[:5]:
        print(f"   {row.get('category','')}: {', '.join(row.get('values',[])[:6])}")

    # Annual P&L
    _print_table_summary("Annual P&L", raw.get("annualPL", {}))

    # Balance Sheet
    _print_table_summary("Balance Sheet", raw.get("balanceSheet", {}))

    # Cash Flows
    _print_table_summary("Cash Flows", raw.get("cashFlows", {}))

    # Historical Ratios
    _print_table_summary("Historical Ratios", raw.get("ratiosHistory", {}))

    # Shareholding
    sh = raw.get("shareholding", [])
    print(f"\n👥 Shareholding: {len(sh)} rows")
    for row in sh[:5]:
        cat = row.get("category", "")
        vals = [v for k, v in row.items() if k != "category"]
        latest = vals[0] if vals else ""
        print(f"   {cat}: {latest}")

    # Peers
    peers = raw.get("peerComparison", {})
    peer_list = peers.get("peers", [])
    peer_heads = peers.get("headings", [])
    print(f"\n🏢 Peer Comparison: {len(peer_list)} peers")
    if peer_heads:
        print(f"   Columns: {', '.join(peer_heads[:6])}")
    for p in peer_list[:5]:
        vals = [str(p.get(h, "")) for h in peer_heads[:4]]
        print(f"   {' | '.join(vals)}")

    # Documents
    docs = raw.get("documents", [])
    print(f"\n📁 Documents: {len(docs)} links")
    for d in docs[:8]:
        print(f"   [{d.get('type','').upper()}] {d.get('title','')[:60]}")

    # News
    news = raw.get("news", [])
    print(f"\n📰 News: {len(news)} articles")
    for n in news[:5]:
        print(f"   [{n.get('time','')}] {n.get('title','')[:70]}")

    print(f"\n{sep}")


def _print_table_summary(label: str, table: dict):
    heads = table.get("headings", [])
    vals = table.get("values", [])
    print(f"\n📈 {label}: {len(vals)} rows × {len(heads)} years")
    if heads:
        print(f"   Years: {', '.join(heads[:10])}")
    for row in vals[:5]:
        print(f"   {row.get('category','')}: {', '.join(row.get('values',[])[:8])}")


def cmd_analyze(screener_path: str):
    print(f"\nAnalyzing: {screener_path}")

    # Step 1: Scrape
    print("Step 1/3: Scraping data from Screener.in + Google Finance...")
    from scraper_bridge import fetch_compact_snapshot
    try:
        snapshot = fetch_compact_snapshot(screener_path)
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Extract ticker from path for display
    parts = screener_path.strip("/").split("/")
    ticker = parts[1] if len(parts) >= 2 else screener_path

    print(f"  Got data for {ticker}: {len(snapshot.get('ratios', []))} ratios, "
          f"{len(snapshot.get('quarterly', {}).get('values', []))} quarterly rows")

    # Step 2 + 3: RAG + Analysis
    print("Step 2/3: Retrieving RAG context...")
    print("Step 3/3: Running LLM analysis (this takes 30-60 seconds)...")
    from analysis.pipeline import analyze_stock
    result = analyze_stock(ticker, snapshot, verbose=True)

    # Print results
    print("\n" + "="*60)
    print(f"ANALYSIS RESULT: {ticker}")
    print("="*60)

    if result.get("error"):
        print(f"ERROR: {result['error']}")
        return

    verdict = result.get("verdict", "unknown").upper()
    conviction = result.get("conviction", 0)
    # Normalize: some models return 0-1 scale instead of 0-10
    if isinstance(conviction, (int, float)) and conviction <= 1.0:
        conviction = round(conviction * 10, 1)

    verdict_color = {"BUY": "✅", "WATCH": "👀", "AVOID": "❌"}.get(verdict, "❓")
    print(f"\n{verdict_color} Verdict: {verdict}  |  Conviction: {conviction}/10")
    print(f"Model: {result.get('model_used', 'unknown')}  |  Sector: {result.get('sector', 'unknown')}")

    breakdown = result.get("conviction_breakdown", {})
    if breakdown:
        print(f"\nConviction Breakdown:")
        print(f"  Business Quality:  {breakdown.get('business_quality', 'N/A')}/10")
        print(f"  Financial Health:  {breakdown.get('financial_health', 'N/A')}/10")
        print(f"  Governance:        {breakdown.get('governance', 'N/A')}/10")
        print(f"  Valuation:         {breakdown.get('valuation', 'N/A')}/10")

    print(f"\nSummary:\n  {result.get('summary', 'N/A')}")

    strengths = result.get("key_strengths", [])
    if strengths:
        print(f"\nKey Strengths:")
        for s in strengths:
            print(f"  + {s}")

    risks = result.get("key_risks", [])
    if risks:
        print(f"\nKey Risks:")
        for r in risks:
            print(f"  - {r}")

    red_flags = result.get("red_flags", [])
    if red_flags and red_flags != ["None identified"]:
        print(f"\n🚨 Red Flags:")
        for rf in red_flags:
            print(f"  ⚠️  {rf}")

    triggers = result.get("invalidation_triggers", [])
    if triggers:
        print(f"\nInvalidation Triggers (re-analyze if any of these happen):")
        for t in triggers:
            print(f"  → {t}")

    watch = result.get("watch_for_next_quarter", "")
    if watch:
        print(f"\nWatch Next Quarter:\n  {watch}")

    news_sentiment = result.get("news_sentiment", {})
    if news_sentiment:
        sentiment_label = news_sentiment.get("overall", "N/A").upper()
        sentiment_icon = {"POSITIVE": "📈", "NEGATIVE": "📉", "NEUTRAL": "➖", "MIXED": "↔️"}.get(sentiment_label, "❓")
        print(f"\nNews Sentiment: {sentiment_icon} {sentiment_label}")
        themes = news_sentiment.get("key_themes", [])
        if themes:
            print(f"  Themes: {', '.join(themes)}")
        note = news_sentiment.get("note", "")
        if note:
            print(f"  {note}")

    # Save clean result to data/ (strip heavy debug fields)
    clean = {k: v for k, v in result.items() if k not in ("raw_response", "rag_context")}
    output_path = f"data/{ticker}_analysis.json"
    os.makedirs("data", exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(clean, f, indent=2, ensure_ascii=False)
    print(f"\nFull result saved to: {output_path}")

    # Save full debug result (with raw_response + rag_context) to stock_cache/
    try:
        from pathlib import Path
        debug_path = Path("stock_cache") / ticker.upper() / "latest_analysis.json"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Debug result (full) saved to: {debug_path}")
    except Exception:
        pass


def cmd_ticker(ticker: str):
    """Try to find the Screener path for a ticker and analyze it."""
    print(f"Searching for {ticker} on Screener.in...")
    from scraper_bridge import search_stocks
    results = search_stocks(ticker)

    if not results:
        print(f"No results found. Try providing the full Screener path instead:")
        print(f"  python main.py --analyze /company/{ticker}/consolidated/")
        # Attempt direct path as fallback
        path = f"/company/{ticker}/consolidated/"
        print(f"\nAttempting direct path: {path}")
        cmd_analyze(path)
        return

    # Show options if multiple
    if len(results) > 1:
        print(f"Found {len(results)} matches:")
        for i, r in enumerate(results[:5]):
            print(f"  [{i}] {r.get('name', '')} — {r.get('url', '')}")
        choice = input("Enter number (0 for first): ").strip()
        try:
            idx = int(choice)
        except ValueError:
            idx = 0
        selected = results[min(idx, len(results) - 1)]
    else:
        selected = results[0]

    url = selected.get("url", "")
    if not url:
        print("Could not get URL from search result")
        return

    # Screener search returns full URL; extract path
    if "screener.in" in url:
        from urllib.parse import urlparse
        path = urlparse(url).path
    else:
        path = url

    print(f"Using: {path}")
    cmd_analyze(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Indian Stock Analysis System")
    parser.add_argument("--build-index", action="store_true", help="Build/rebuild ChromaDB index")
    parser.add_argument("--force", action="store_true", help="Force rebuild (skip cache)")
    parser.add_argument("--analyze", metavar="SCREENER_PATH",
                        help="Analyze a stock by Screener path (e.g. /company/INFY/consolidated/)")
    parser.add_argument("--ticker", metavar="TICKER",
                        help="Analyze a stock by ticker (searches Screener)")
    parser.add_argument("--cache-stock", metavar="TICKER",
                        help="Build per-stock RAG index (e.g. TCS). Run once per quarter.")
    parser.add_argument("--screener-path", metavar="PATH",
                        help="Screener path override for --cache-stock (optional)")
    args = parser.parse_args()

    if not any([args.build_index, args.analyze, args.ticker, args.cache_stock]):
        parser.print_help()
        sys.exit(0)

    if args.build_index:
        cmd_build_index(force=args.force)

    if args.cache_stock:
        cmd_cache_stock(args.cache_stock, screener_path=args.screener_path or "", force=args.force)

    if args.analyze:
        cmd_analyze(args.analyze)
    elif args.ticker:
        cmd_ticker(args.ticker)
