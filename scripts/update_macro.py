"""
Fetch fresh global + India macro context using Claude with web search.
Saves each snapshot to the macro_snapshots DB table.

Run manually or via cron (e.g. daily at 9am):
    python -m scripts.update_macro
    python scripts/update_macro.py global
    python scripts/update_macro.py india
"""

import json
import os
import re
import sys
import datetime
from pathlib import Path

# Allow running as a script from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

import anthropic

MODEL = "claude-sonnet-4-6"

_GLOBAL_JSON_SCHEMA = """\
{
  "headline": "2-3 sentence factual overview of the most important developments this week",
  "key_signals": [
    "4-6 short factual data points with numbers, e.g. 'Fed held rates at 4.5-4.75%%', 'Crude oil +8%% WoW at $91/bbl'"
  ],
  "developments": [
    {
      "text": "Full explanation of the development and its implications",
      "impact": "POSITIVE | NEGATIVE | NEUTRAL"
    }
  ],
  "sectors_affected": ["IT", "Banking"],
  "watch_next_7_days": [
    "2-3 upcoming events or data releases with approximate dates"
  ],
  "summary": "250-300 word (200-250 token) condensed macro context optimized for injection into stock analysis prompts. Focus on quantified signals (rates X%%, crude $Y/bbl, DXY level, tariff %%). Include explicit sector impacts. Skip narrative prose. End with: Sectors most affected: X, Y, Z."
}"""

_INDIA_JSON_SCHEMA = """\
{
  "headline": "2-3 sentence factual overview of the most important developments this week",
  "key_signals": [
    "4-6 short factual data points with numbers: Nifty level, FII flows in ₹Cr, INR/USD rate, CPI %%, crude $/bbl"
  ],
  "developments": [
    {
      "text": "Full explanation of the development and its implications for Indian equities",
      "impact": "POSITIVE | NEGATIVE | NEUTRAL"
    }
  ],
  "sectors_affected": ["Oil & Gas", "Banking"],
  "watch_next_7_days": [
    "2-3 upcoming RBI meetings, data releases, or earnings with approximate dates"
  ],
  "summary": "250-300 word (200-250 token) condensed macro context optimized for injection into Indian stock analysis prompts. Focus on quantified signals (Nifty level, FII/DII flows ₹X Cr, INR rate, CPI/WPI %%, crude $/bbl, RBI repo %%). Include explicit sector impacts. Skip narrative prose. End with: Sectors most affected: X, Y, Z."
}"""


def _make_global_prompt(today: str) -> str:
    return f"""Today is {today}. Search the web for major economic, geopolitical, and supply chain developments from the past 7 days that could materially impact global equity markets and India's economy. Prioritise news from the last 3 days over older news.

Include: monetary policy changes (Fed, ECB, BOJ, BOE), trade and tariff announcements, armed conflicts or tensions affecting key trade routes, commodity price shocks (oil, gold, metals), major currency moves (USD, DXY), or regulatory shifts with global reach.

Focus on what affects demand, supply, costs, or investor sentiment across sectors.

Output ONLY a valid JSON object matching this exact structure — no prose before or after, no markdown, no code fences:

{_GLOBAL_JSON_SCHEMA}

Requirements:
- developments: 5-7 items
- key_signals: 4-6 items with actual numbers where possible
- impact must be exactly POSITIVE, NEGATIVE, or NEUTRAL
- summary must be facts-only, optimized for AI consumption
- summary must end with: Sectors most affected: X, Y, Z
- output only the JSON object, nothing else"""


def _make_india_prompt(today: str) -> str:
    return f"""Today is {today}. Search the web for major Indian economic and equity market developments from the past 7 days. Prioritise news from the last 3 days over older news.

Include: RBI policy signals and rate decisions, FII and DII flow data (in ₹Cr), rupee movement vs USD, CPI/WPI inflation data, Union Budget updates, SEBI regulatory changes, major corporate earnings, Nifty/Sensex levels, and sector-specific events affecting NSE/BSE-listed companies. Also note current Brent crude price if it has moved significantly (affects India's CAD, INR, inflation).

Focus on what materially impacts Indian equity valuations and investment sentiment.

Output ONLY a valid JSON object matching this exact structure — no prose before or after, no markdown, no code fences:

{_INDIA_JSON_SCHEMA}

Requirements:
- developments: 5-7 items
- key_signals: 4-6 items with actual numbers (Nifty, FII ₹Cr, INR, CPI %%)
- impact must be exactly POSITIVE, NEGATIVE, or NEUTRAL
- summary must be facts-only, optimized for AI consumption
- summary must end with: Sectors most affected: X, Y, Z
- output only the JSON object, nothing else"""


def _run_web_search_prompt(client: anthropic.Anthropic, prompt: str) -> dict:
    """Call Claude with web_search tool and return parsed structured JSON."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        tools=[{"type": "web_search_20250305", "name": "web_search"}],
        messages=[{"role": "user", "content": prompt}],
    )

    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text.strip())

    raw_text = "\n\n".join(p for p in parts if p)

    # Extract JSON object from the response
    json_match = re.search(r"\{[\s\S]*\}", raw_text)
    if not json_match:
        raise ValueError(f"No JSON found in response:\n{raw_text[:600]}")

    return json.loads(json_match.group())


PROMPT_BUILDERS = {
    "global": _make_global_prompt,
    "india": _make_india_prompt,
}


def update_macro_one(macro_type: str, verbose: bool = True) -> None:
    """Fetch and save a single macro snapshot ('global' or 'india')."""
    from api.db import save_macro_snapshot

    if macro_type not in PROMPT_BUILDERS:
        raise ValueError(f"Unknown macro_type: {macro_type!r}. Must be 'global' or 'india'.")

    today = datetime.date.today().strftime("%d %b %Y")
    prompt = PROMPT_BUILDERS[macro_type](today)

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    if verbose:
        print(f"[MacroUpdate] Fetching {macro_type} context for {today}...")

    structured = _run_web_search_prompt(client, prompt)

    if not structured.get("summary"):
        raise RuntimeError(f"Parsed JSON missing 'summary' field for {macro_type}")

    save_macro_snapshot(macro_type=macro_type, structured_data=structured, model_used=MODEL)

    if verbose:
        print(f"[MacroUpdate] Saved {macro_type} snapshot")
        print(f"  headline: {structured.get('headline', '')[:120]}")
        print(f"  sectors:  {structured.get('sectors_affected', [])}")
        print(f"  summary:  {structured.get('summary', '')[:200]}...")


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else None
    if t in ("global", "india"):
        update_macro_one(t)
    else:
        update_macro_one("global")
        update_macro_one("india")
