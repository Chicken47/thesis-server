"""
Incremental reanalysis pipeline.

Instead of a full 4-minute Claude run, this:
  1. Fetches the previous analysis from DB
  2. Fetches fresh news + current macro
  3. Calls Claude with a small prompt (no financials, no RAG)
  4. Returns updated JSON — same schema as full analysis

~80% cheaper and ~10x faster than a full reanalysis.
Automatically falls back to full analysis if new quarterly results are detected.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote as url_quote
from xml.etree import ElementTree as ET
from email.utils import parsedate_to_datetime

import anthropic
import requests

log = logging.getLogger(__name__)

ANTHROPIC_MODEL = "claude-sonnet-4-6"
INCREMENTAL_THINKING_BUDGET = int(os.environ.get("INCREMENTAL_THINKING_BUDGET", 10_000))
INCREMENTAL_MAX_TOKENS = INCREMENTAL_THINKING_BUDGET + 4_000


# ── Prompt ────────────────────────────────────────────────────────────────────

INCREMENTAL_PROMPT = """\
You are VERDIKT, an expert Indian equity analyst.

A previous analysis was run on {PREVIOUS_DATE}. Your job is to update it based on new information only.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## PREVIOUS VERDICT ({PREVIOUS_DATE})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

{PREVIOUS_ANALYSIS_JSON}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## NEW INFORMATION SINCE {PREVIOUS_DATE}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Stock: {TICKER}
Today: {TODAY_DATE}
Days since last analysis: {DAYS_SINCE}
Current price: ₹{CURRENT_PRICE}
Price change since last analysis: {PRICE_CHANGE_PCT}%

### Recent News
{NEWS_ITEMS}

### Current Macroeconomic Context
{MACRO_CONTEXT}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## PRE-FLIGHT CHECK
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If ANY news item contains signals of NEW quarterly/annual results:
  - "Q1/Q2/Q3/Q4 results", "quarterly earnings", "PAT grew/fell X%",
    "revenue for the quarter", "board approves financials", "net profit for Q"
→ Return ONLY this JSON and nothing else:
{{"requires_full_analysis": true, "reason": "New quarterly results detected in news"}}

Otherwise proceed with incremental analysis below.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## UPDATE RULES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALWAYS update:
- macro_adjustment → recalculate based on stock's primary macro driver vs current macro
- news_sentiment → fresh from the news items above
- watch_for_next_quarter → exactly 3 items: "(1) [≤10 words]; (2) [≤10 words]; (3) [≤10 words]". No prose.
- changes_made → list every field you changed and why

UPDATE ONLY IF price changed >5%:
- valuation score in conviction_breakdown
- entry_guidance (recalculate all 4 zones; round thresholds: <₹100→₹5, ₹100–500→₹10, ₹500–2000→₹25, >₹2000→₹50)
- Recompute final conviction if valuation score changed

UPDATE ONLY IF price moved >10% OR clear narrative shift in news:
- market_vs_verdikt (narrative, trade_signal, gap_analysis)

REWRITE key_risks from scratch: pick the top 5 most material risks given all data (previous + new). Max 5 items. Max 8 words per item — phrase only, no sentences.
REWRITE key_strengths from scratch: pick the top 5 most material strengths. Max 5 items. One phrase each.

CARRY FORWARD UNCHANGED (unless new info directly contradicts):
- conviction_breakdown.business_quality
- conviction_breakdown.financial_health
- conviction_breakdown.governance
- invalidation_triggers
- red_flags
- summary (update only if conviction changed ≥0.3 or verdict flipped; if updated, use this exact structure:
    S1: "[Company] is a [type] with [moat/position] — [key metric with number]."
    S2: "The stock sits at [VERDICT] at [X]/10 because [specific blocking factor with data point]."
    S3: "At ₹[price] ([% vs 52wk high/low]), [valuation metric] — [what would change conviction]."
    Tone: analytical, name the tension, cite numbers. NO temporal language. NO buy zone prices.)

VERDICT RULES after recalculating conviction:
- >7.5 AND no red flags → "buy"
- 6.0–7.5 → "watch"
- <6.0 OR governance red flag → "avoid"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
## OUTPUT FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY a single valid JSON object. No markdown. No explanation. No text outside the JSON.
Start with {{ and end with }}.

Required fields (same schema as full analysis, plus):
  "analysis_type": "incremental"
  "previous_date": "{PREVIOUS_DATE}"
  "refresh_date": "{TODAY_DATE}"
  "changes_made": ["field: what changed and why", ...]

Full required schema:
{{
  "analysis_type": "incremental",
  "previous_date": "{PREVIOUS_DATE}",
  "refresh_date": "{TODAY_DATE}",
  "changes_made": [],
  "stock": "{TICKER}",
  "verdict": "buy|watch|avoid",
  "conviction": 0.0,
  "conviction_breakdown": {{
    "business_quality": 0,
    "financial_health": 0,
    "governance": 0,
    "valuation": 0
  }},
  "summary": "...",
  "key_strengths": [],
  "key_risks": [],
  "red_flags": [],
  "invalidation_triggers": [],
  "watch_for_next_quarter": "...",
  "news_sentiment": {{
    "overall": "positive|neutral|negative|mixed",
    "key_themes": [],
    "note": "..."
  }},
  "entry_guidance": {{
    "current_price": 0,
    "current_zone": "OVERVALUED|FAIR|GOOD|EXCEPTIONAL",
    "action": "one sentence max 80 chars",
    "target_entry": 0,
    "upside_from_target": "X%",
    "zones": [
      {{"label": "Overvalued", "range": "₹X+", "action": "Wait for correction", "color": "red"}},
      {{"label": "Fair", "range": "₹Y–X", "action": "Entry for high-conviction bulls", "color": "yellow"}},
      {{"label": "Good Entry", "range": "₹Z–Y", "action": "Strong entry for most investors", "color": "lightgreen"}},
      {{"label": "Exceptional", "range": "<₹Z", "action": "Load up if thesis intact", "color": "darkgreen"}}
    ],
    "visual_position": {{
      "distance_to_good": "₹X drop needed (−Y%)",
      "distance_to_exceptional": "₹X drop needed (−Y%)"
    }}
  }},
  "market_vs_verdikt": {{
    "market_narrative": "...",
    "market_claims": [],
    "emotional_tone": "euphoric|fearful|neutral",
    "verdikt_view": "...",
    "gap_analysis": {{
      "market_expects": "...",
      "fundamentals_support": "...",
      "magnitude": "Large|Medium|Small|Aligned"
    }},
    "trade_signal": "FADE|RIDE|IGNORE",
    "reasoning": "..."
  }}
}}
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_news(ticker: str, max_results: int = 12) -> list[dict]:
    query = f"{ticker} share"
    url = f"https://www.bing.com/news/search?q={url_quote(query)}&format=rss"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    try:
        log.info("[Incremental] Fetching news", extra={"ticker": ticker, "url": url})
        resp = requests.get(url, headers=headers, timeout=15)
        log.info("[Incremental] News HTTP response", extra={"ticker": ticker, "status": resp.status_code, "content_len": len(resp.text)})
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
            items.append({"title": title, "time": dt.isoformat(), "_dt": dt})
        items.sort(key=lambda x: x["_dt"].timestamp(), reverse=True)
        result = [{"title": i["title"], "time": i["time"]} for i in items[:max_results]]
        log.info("[Incremental] News fetched", extra={"ticker": ticker, "count": len(result)})
        return result
    except Exception as e:
        log.error("[Incremental] News fetch FAILED", extra={"ticker": ticker, "error": str(e)}, exc_info=True)
        return []


def _format_news(items: list[dict]) -> str:
    if not items:
        return "(No recent news found)"
    lines = []
    for it in items:
        date_str = it["time"][:10] if it.get("time") else "?"
        lines.append(f"  [{date_str}] {it['title']}")
    return "\n".join(lines)


def _get_current_price(ticker: str) -> float | None:
    """Try to read current price from stock_cache screener export."""
    try:
        from pathlib import Path
        import json as _json
        path = Path(__file__).parent.parent / "stock_cache" / ticker.upper() / "raw_full.json"
        log.info("[Incremental] Looking for current price", extra={"ticker": ticker, "path": str(path), "exists": path.exists()})
        if path.exists():
            raw = _json.loads(path.read_text())
            for r in raw.get("ratios", []):
                if r.get("name") == "Current Price":
                    val = str(r.get("value", "")).replace(",", "").strip()
                    price = float(val)
                    log.info("[Incremental] Current price found", extra={"ticker": ticker, "price": price})
                    return price
            log.warning("[Incremental] Current price not found in ratios", extra={"ticker": ticker})
        else:
            log.warning("[Incremental] raw_full.json not found", extra={"ticker": ticker, "path": str(path)})
    except Exception as e:
        log.error("[Incremental] Price fetch FAILED", extra={"ticker": ticker, "error": str(e)}, exc_info=True)
    return None


def _price_change_pct(current: float | None, previous: float | None) -> str:
    if not current or not previous or previous == 0:
        return "unknown"
    pct = ((current - previous) / previous) * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.1f}"


def _client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    return anthropic.Anthropic(api_key=api_key)


# ── Main function ─────────────────────────────────────────────────────────────

def incremental_reanalysis(ticker: str, previous_analysis: dict, verbose: bool = True) -> dict:
    """
    Run incremental reanalysis for a stock.

    Args:
        ticker: e.g. "INFY"
        previous_analysis: full analysis dict from DB (latest row)
        verbose: print progress

    Returns:
        dict — same schema as full analysis, plus analysis_type/previous_date/changes_made.
        If new quarterly results are detected, returns {"requires_full_analysis": True, ...}
    """
    upper = ticker.upper()
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%d %b %Y")

    # Previous analysis date
    prev_date_raw = previous_analysis.get("created_at", "")
    try:
        prev_dt = datetime.fromisoformat(str(prev_date_raw).replace("Z", "+00:00"))
        prev_date_str = prev_dt.strftime("%d %b %Y")
        days_since = (today - prev_dt).days
    except Exception:
        prev_date_str = str(prev_date_raw)[:10]
        days_since = 0

    if verbose:
        print(f"\n[Incremental] {upper} — last analysis: {prev_date_str} ({days_since}d ago)")

    # Fetch news
    if verbose:
        print(f"[Incremental] Fetching live news...")
    news_items = _fetch_news(upper)
    if verbose:
        print(f"[Incremental] Got {len(news_items)} news items")

    # Get macro context
    macro_context = "(Macro context unavailable)"
    try:
        from api.db import get_latest_macro
        global_macro = get_latest_macro("global") or ""
        india_macro = get_latest_macro("india") or ""
        macro_context = f"### Global\n{global_macro}\n\n### India\n{india_macro}"
        log.info("[Incremental] Macro context fetched", extra={"ticker": upper, "global_len": len(global_macro), "india_len": len(india_macro)})
    except Exception as e:
        log.error("[Incremental] Macro fetch FAILED", extra={"ticker": upper, "error": str(e)}, exc_info=True)
        if verbose:
            print(f"[Incremental] Macro fetch error: {e}")

    # Current price
    current_price = _get_current_price(upper)
    prev_price = None
    try:
        bz = previous_analysis.get("entry_guidance") or previous_analysis.get("buy_zones") or {}
        prev_price = float(bz.get("current_price") or 0) or None
    except Exception:
        pass

    price_str = f"{current_price:.0f}" if current_price else "unavailable"
    price_change = _price_change_pct(current_price, prev_price)

    # Build clean previous analysis JSON (strip heavy fields)
    prev_clean = {k: v for k, v in previous_analysis.items()
                  if k not in ("raw_response", "step_outputs", "rag_context", "rag_context_length",
                               "model_used", "input_tokens", "output_tokens", "id", "created_at",
                               "updated_at", "stock_symbol")}

    prompt = INCREMENTAL_PROMPT.format(
        TICKER=upper,
        PREVIOUS_DATE=prev_date_str,
        TODAY_DATE=today_str,
        DAYS_SINCE=days_since,
        CURRENT_PRICE=price_str,
        PRICE_CHANGE_PCT=price_change,
        PREVIOUS_ANALYSIS_JSON=json.dumps(prev_clean, indent=2),
        NEWS_ITEMS=_format_news(news_items),
        MACRO_CONTEXT=macro_context,
    )

    if verbose:
        print(f"[Incremental] Prompt: {len(prompt):,} chars | Thinking: {INCREMENTAL_THINKING_BUDGET:,} tokens")
        print(f"[Incremental] Calling Claude (should take ~30-60s)...")

    # Call Claude
    try:
        log.info("[Incremental] Calling Claude", extra={"ticker": upper, "prompt_len": len(prompt), "thinking_budget": INCREMENTAL_THINKING_BUDGET})
        client = _client()
        response = client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=INCREMENTAL_MAX_TOKENS,
            thinking={"type": "enabled", "budget_tokens": INCREMENTAL_THINKING_BUDGET},
            messages=[{"role": "user", "content": prompt}],
        )

        text_parts = [b.text for b in response.content if b.type == "text"]
        raw_text = "\n".join(text_parts)

        usage = response.usage
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)
        cost = (input_tokens * 3 + output_tokens * 15) / 1_000_000
        log.info("[Incremental] Claude response received", extra={"ticker": upper, "input_tokens": input_tokens, "output_tokens": output_tokens, "cost_usd": round(cost, 4), "raw_text_len": len(raw_text)})

        if verbose:
            print(f"[Incremental] Done — tokens: {input_tokens:,} in / {output_tokens:,} out | cost: ~${cost:.4f}")

    except Exception as e:
        log.error("[Incremental] Claude call FAILED", extra={"ticker": upper, "error": str(e)}, exc_info=True)
        return {"error": str(e), "stock": upper, "verdict": "error", "model_used": ANTHROPIC_MODEL}

    # Parse response
    log.info("[Incremental] Parsing Claude response", extra={"ticker": upper, "raw_text_preview": raw_text[:200]})
    result = _extract_json(raw_text)

    if result.get("parse_failed"):
        log.error("[Incremental] JSON parse FAILED", extra={"ticker": upper, "raw_text": raw_text[:1000]})

    # Check if model flagged need for full analysis
    if result.get("requires_full_analysis"):
        reason = result.get('reason', 'unknown')
        log.info("[Incremental] Model flagged requires_full_analysis", extra={"ticker": upper, "reason": reason})
        if verbose:
            print(f"[Incremental] Model flagged: requires_full_analysis — {reason}")
        result["stock"] = upper
        return result

    # Enrich with metadata
    result["raw_response"] = raw_text
    result["model_used"] = ANTHROPIC_MODEL
    result["sector"] = previous_analysis.get("sector", "")
    result["input_tokens"] = input_tokens
    result["output_tokens"] = output_tokens
    result["is_incremental"] = True
    result["based_on_analysis_id"] = str(previous_analysis.get("id", ""))
    result["changes_made"] = result.get("changes_made", [])
    result["stock"] = upper

    # Carry forward step_outputs from previous (they don't change in incremental)
    result["step_outputs"] = previous_analysis.get("step_outputs") or {}

    if verbose:
        verdict = result.get("verdict", "?").upper()
        conviction = result.get("conviction", "?")
        changes = result.get("changes_made", [])
        print(f"[Incremental] Result: {verdict} @ {conviction}/10 | {len(changes)} changes")

    return result


def _extract_json(text: str) -> dict:
    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(text[start:end])
    except Exception:
        pass
    return {
        "verdict": "parse_error",
        "conviction": 0,
        "conviction_breakdown": {},
        "summary": "Could not parse incremental response as JSON",
        "key_strengths": [],
        "key_risks": [],
        "red_flags": ["parse_error"],
        "invalidation_triggers": [],
        "watch_for_next_quarter": "",
        "news_sentiment": {},
        "changes_made": [],
        "parse_failed": True,
    }
