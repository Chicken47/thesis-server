"""
Full analysis pipeline:
  scraper snapshot → RAG context → CoT prompt → Claude API → parsed verdict
"""

import json
import os
import re
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote as url_quote

import anthropic
import requests

from rag.retrieval import retrieve_context
from analysis.prompt_builder import build_analysis_prompt


def _fetch_live_news(ticker: str, max_results: int = 15) -> list[dict]:
    """Fetch fresh news from Bing RSS at analysis time. Query: '{ticker} share'."""
    query = f"{ticker} share"
    url = f"https://www.bing.com/news/search?q={url_quote(query)}&format=rss"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        root = ET.fromstring(resp.text)
        items = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            pub_date = (item.findtext("pubDate") or "").strip()
            try:
                dt = parsedate_to_datetime(pub_date)
            except Exception:
                continue
            link = (item.findtext("link") or item.findtext("guid") or "").strip()
            raw_desc = (item.findtext("description") or "").strip()
            desc = re.sub(r"<[^>]+>", " ", raw_desc).strip()[:200]
            source = (item.findtext("source") or "").strip()
            if not source and desc:
                m = re.match(r'^([A-Z][A-Za-z\s&.]+?)\s*[-–]\s', desc)
                if m:
                    source = m.group(1).strip()
            items.append({"title": title, "source": source, "time": dt.isoformat(), "description": desc, "url": link, "_dt": dt})
        items.sort(key=lambda x: x["_dt"].timestamp(), reverse=True)
        return [{"title": i["title"], "source": i["source"], "time": i["time"], "description": i["description"], "url": i["url"]} for i in items[:max_results]]
    except Exception as e:
        return []

ANTHROPIC_MODEL = "claude-sonnet-4-6"

# Extended thinking budget (tokens reserved for internal reasoning).
# The model "thinks" before writing the CoT steps — this improves multi-step
# financial reasoning significantly.  Remaining tokens go to the output.
THINKING_BUDGET = 10_000   # tokens for internal reasoning
MAX_TOKENS      = 16_000   # total (thinking + visible output)
# Note: temperature is NOT set when extended thinking is enabled.
# The API requires temperature=1 (its default) in thinking mode.


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Export it before running: export ANTHROPIC_API_KEY=sk-ant-..."
        )
    return anthropic.Anthropic(api_key=api_key)


def analyze_stock(stock_symbol: str, snapshot: dict, verbose: bool = True) -> dict:
    """
    Run full analysis pipeline for a stock.

    Args:
        stock_symbol: e.g. "INFY"
        snapshot: compact snapshot from scraper (buildCompactStockSnapshot output)
        verbose: print progress

    Returns:
        dict with keys: verdict, conviction, conviction_breakdown, summary,
                        key_strengths, key_risks, red_flags, invalidation_triggers,
                        watch_for_next_quarter, raw_response, model_used, error (if any)
    """
    if verbose:
        print(f"\n[Pipeline] Starting analysis for {stock_symbol}")

    # Step 1: Extract sector from snapshot ratios (Screener includes it)
    sector = _extract_sector(snapshot, stock_symbol)
    about = snapshot.get("aboutText", "")

    if verbose:
        print(f"[Pipeline] Detected sector: {sector or 'Unknown'}")

    # Step 2: RAG context retrieval
    if verbose:
        print("[Pipeline] Retrieving RAG context...")
    rag_result = {}
    try:
        rag_result = retrieve_context(stock_symbol, sector=sector, company_about=about)
        rag_context = rag_result["assembled"]
        if verbose:
            print(f"[Pipeline] RAG context: {len(rag_context)} chars")
    except Exception as e:
        rag_context = "(RAG unavailable — ChromaDB index may not be built yet. Run: python -m rag.ingest)"
        if verbose:
            print(f"[Pipeline] RAG error: {e}")

    # Step 3: Load deep financial data from cache (P&L, BS, CF, peers)
    # This is injected directly into the prompt — NOT routed through RAG.
    deep_data = None
    try:
        from cache.stock_store import load_raw
        deep_data = load_raw(stock_symbol)
    except Exception:
        pass

    # Step 3b: Fetch fresh news and override whatever the scraper cached
    if verbose:
        print(f"[Pipeline] Fetching live news for {stock_symbol}...")
    live_news = _fetch_live_news(stock_symbol)
    snapshot["news"] = live_news
    if verbose:
        print(f"[Pipeline] Got {len(live_news)} live news items")

    # Step 4: Build prompt
    prompt = build_analysis_prompt(snapshot, rag_context, stock_symbol, sector=sector, deep_data=deep_data)
    if verbose:
        print(f"[Pipeline] Prompt built: {len(prompt)} chars")

    # Save full prompt to stock_cache for inspection / replay
    try:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "stock_cache" / stock_symbol.upper() / "latest_prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        if verbose:
            print(f"[Pipeline] Prompt saved → {prompt_path}")
    except Exception as _e:
        if verbose:
            print(f"[Pipeline] Could not save prompt: {_e}")

    # Update checklist with RAG status and the generated prompt
    try:
        from api.db import update_checklist_rag_and_prompt
        rag_ok = not rag_context.startswith("(RAG unavailable")
        update_checklist_rag_and_prompt(stock_symbol, rag_ok, prompt)
        if verbose:
            print(f"[Pipeline] Checklist updated  (rag_successful={rag_ok})")
    except Exception as _e:
        if verbose:
            print(f"[Pipeline] Could not update checklist: {_e}")

    # Step 5: Call Claude API with extended thinking
    if verbose:
        print(f"\n[Pipeline] Sending to {ANTHROPIC_MODEL}")
        print(f"           Prompt: {len(prompt):,} chars  |  Thinking budget: {THINKING_BUDGET:,} tokens  |  Max output: {MAX_TOKENS:,} tokens")
        print(f"           Extended thinking is ON — typical wait: 2–4 minutes\n")

    try:
        import sys
        import threading
        import time

        client = _get_client()
        _response_holder: list = []
        _error_holder:    list = []

        def _call():
            try:
                r = client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=MAX_TOKENS,
                    thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET},
                    messages=[{"role": "user", "content": prompt}],
                )
                _response_holder.append(r)
            except Exception as exc:
                _error_holder.append(exc)

        thread = threading.Thread(target=_call, daemon=True)
        thread.start()

        # Live progress ticker while waiting
        _PHASES = [
            (0,   "Thinking…"),
            (30,  "Reasoning through financials…"),
            (60,  "Evaluating governance & valuation…"),
            (90,  "Synthesising verdict…"),
            (140, "Almost done…"),
            (160, "Finalising output…"),
            (230, "Wrapping up…"),
        ]
        _DOTS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        start = time.time()
        dot_i = 0
        phase_label = _PHASES[0][1]

        if verbose:
            while thread.is_alive():
                elapsed = time.time() - start
                for t, label in reversed(_PHASES):
                    if elapsed >= t:
                        phase_label = label
                        break
                mins, secs = divmod(int(elapsed), 60)
                timer = f"{mins}m {secs:02d}s" if mins else f"{secs:02d}s"
                sys.stdout.write(f"\r  {_DOTS[dot_i % len(_DOTS)]}  {phase_label:<40} [{timer}]  ")
                sys.stdout.flush()
                dot_i += 1
                time.sleep(0.1)
            elapsed_final = time.time() - start
            mins, secs = divmod(int(elapsed_final), 60)
            timer = f"{mins}m {secs:02d}s" if mins else f"{secs:02d}s"
            sys.stdout.write(f"\r  ✓  Done                                          [{timer}]  \n\n")
            sys.stdout.flush()
        else:
            thread.join()

        if _error_holder:
            raise _error_holder[0]

        response = _response_holder[0]

        # Content is a list of blocks: thinking block(s) + text block(s)
        thinking_parts = [b.thinking for b in response.content if b.type == "thinking"]
        text_parts     = [b.text     for b in response.content if b.type == "text"]
        raw_text       = "\n".join(text_parts)

        if verbose:
            thinking_tokens = sum(len(t) for t in thinking_parts)
            print(f"[Pipeline] Response: {len(raw_text):,} chars output"
                  f"  |  thinking: {len(thinking_parts)} block(s), ~{thinking_tokens:,} chars")

        # Persist thinking for inspection alongside the prompt
        if thinking_parts:
            try:
                from pathlib import Path
                thinking_path = (
                    Path(__file__).parent.parent
                    / "stock_cache" / stock_symbol.upper() / "latest_thinking.txt"
                )
                thinking_path.write_text("\n\n---\n\n".join(thinking_parts), encoding="utf-8")
                if verbose:
                    print(f"[Pipeline] Thinking saved → {thinking_path}")
            except Exception:
                pass

    except Exception as e:
        return {
            "error": str(e),
            "stock": stock_symbol,
            "verdict": "error",
            "model_used": ANTHROPIC_MODEL,
        }

    # Step 6: Parse JSON from response
    parsed = _extract_json(raw_text)
    parsed["raw_response"] = raw_text
    parsed["step_outputs"] = _extract_step_outputs(raw_text)
    parsed["model_used"] = ANTHROPIC_MODEL
    parsed["sector"] = sector
    parsed["rag_context_length"] = len(rag_context)
    parsed["rag_context"] = {
        "total_chars": len(rag_context),
        "stock_rag": rag_result.get("stock_rag_context", ""),
        "sector": rag_result.get("sector_context", ""),
        "governance": rag_result.get("governance_context", ""),
        "macro": rag_result.get("macro_context", ""),
        "framework": rag_result.get("template_context", ""),
        "assembled": rag_context,
    }

    return parsed


def _extract_sector(snapshot: dict, stock_symbol: str = "") -> str:
    """Try to extract sector from ratios, ticker lookup, or about text."""
    # 1. Screener sometimes puts sector in ratios
    ratios = snapshot.get("ratios", [])
    for r in ratios:
        name = (r.get("name") or "").lower()
        if "sector" in name or "industry" in name:
            return r.get("value", "")

    # 2. Ticker-based lookup — for companies whose aboutText lacks sector keywords
    _TICKER_SECTOR = {
        "RELIANCE": "Energy", "ONGC": "Energy", "BPCL": "Energy",
        "IOC": "Energy", "HPCL": "Energy", "NTPC": "Energy",
        "POWERGRID": "Energy", "ADANIGREEN": "Energy", "TATAPOWER": "Energy",
        "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
        "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
        "HDFCBANK": "Banking", "ICICIBANK": "Banking", "SBIN": "Banking",
        "KOTAKBANK": "Banking", "AXISBANK": "Banking", "BAJFINANCE": "Banking",
        "INDUSINDBK": "Banking", "BANDHANBNK": "Banking",
        "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
        "HEROMOTOCO": "Auto", "BAJAJ-AUTO": "Auto", "EICHERMOT": "Auto",
        "SUNPHARMA": "Pharma", "CIPLA": "Pharma", "DRREDDY": "Pharma",
        "DIVISLAB": "Pharma", "AUROPHARMA": "Pharma",
        "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
        "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    }
    if stock_symbol.upper() in _TICKER_SECTOR:
        return _TICKER_SECTOR[stock_symbol.upper()]

    # 3. Infer from about text
    about = (snapshot.get("aboutText") or "").lower()
    sector_keywords = {
        "IT": ["software", "it services", "technology", "consulting", "digital", "outsourcing"],
        "Banking": ["bank", "lending", "deposits", "nbfc", "financial services", "credit", "loan"],
        "FMCG": ["consumer", "fmcg", "food", "beverages", "personal care", "household", "packaged"],
        "Pharma": ["pharma", "pharmaceutical", "drug", "medicine", "healthcare", "formulation"],
        "Auto": ["automobile", "vehicle", "car", "two-wheeler", "tractor", "automotive"],
        "Energy": ["oil", "gas", "energy", "power", "electricity", "refinery", "renewable",
                   "petroleum", "hydrocarbon", "crude", "jio", "telecom"],
    }
    for sector, keywords in sector_keywords.items():
        if any(kw in about for kw in keywords):
            return sector

    return ""


def _extract_step_outputs(text: str) -> dict:
    """Extract <stepN_output> tags from the LLM response."""
    steps = {}
    for i in range(1, 6):
        tag = f"step{i}_output"
        match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if match:
            steps[f"step{i}"] = match.group(1).strip()
    return steps


def _extract_json(text: str) -> dict:
    """Extract JSON block from LLM response text."""
    # Try to find JSON block in ```json ... ``` or ``` ... ```
    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
        r"(\{[^{}]*\"verdict\"[^{}]*\})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # Last resort: try to parse the whole text as JSON
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass

    # Return a failed parse result with the raw text
    return {
        "verdict": "parse_error",
        "conviction": 0,
        "conviction_breakdown": {},
        "summary": "Could not parse LLM response as JSON",
        "key_strengths": [],
        "key_risks": [],
        "red_flags": ["parse_error"],
        "invalidation_triggers": [],
        "watch_for_next_quarter": "",
        "news_sentiment": {},
        "parse_failed": True,
    }
