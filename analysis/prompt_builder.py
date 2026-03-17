"""
Builds the structured Chain-of-Thought prompt for stock analysis.

Data flow into the prompt:
  1. deep_data (raw_full.json from cache) → P&L, BS, CF, peers injected directly
  2. snapshot (live compact scrape) → ratios, quarterly, pros/cons, news
  3. rag_context (PDF-only RAG retrieval) → annual report / concall excerpts

All analytical rules (benchmarks, governance caps, verdict thresholds) are
baked into the instructions section.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from email.utils import parsedate_to_datetime


def _parse_news_date(time_str: str) -> datetime.datetime:
    """Parse an ISO 8601 or RFC 822 date string. Returns epoch (1970) if unparseable."""
    if not time_str:
        return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    for parse in (
        lambda s: parsedate_to_datetime(s),
        lambda s: datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)
        if datetime.datetime.fromisoformat(s).tzinfo is None
        else datetime.datetime.fromisoformat(s),
    ):
        try:
            return parse(time_str)
        except Exception:
            pass
    return datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


def _fmt_news_date(time_str: str) -> str:
    """Convert ISO 8601 or RFC 822 date string to '13 Mar 2026' display format."""
    if not time_str:
        return ""
    for parse in (
        lambda s: parsedate_to_datetime(s),
        lambda s: __import__("datetime").datetime.fromisoformat(s),
    ):
        try:
            dt = parse(time_str)
            return dt.strftime("%-d %b %Y")
        except Exception:
            pass
    # last resort: return the raw string (truncated)
    return time_str[:16]


def _fmt_news_item(n: dict) -> str:
    """Format a single news item for injection into the prompt."""
    date = _fmt_news_date(n.get("time", ""))
    title = n.get("title", "").strip()
    source = n.get("source", "").strip()
    desc = n.get("description", "").strip()

    header = f"  [{date}] {title}"
    if source:
        header += f" — {source}"
    if desc and desc.lower() != title.lower():
        # Truncate to 160 chars to keep prompt lean
        snippet = desc if len(desc) <= 160 else desc[:157] + "..."
        return f"{header}\n    {snippet}"
    return header

_MACRO_CONTEXT_PATH = Path(__file__).parent.parent / "knowledge_base" / "macro" / "macro_context.md"


def _load_macro_context() -> str:
    """Load macro_context.md if it exists. Returns empty string if missing."""
    try:
        return _MACRO_CONTEXT_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_analysis_prompt(
    snapshot: dict,
    rag_context: str,
    stock_symbol: str,
    sector: str = "",
    deep_data: dict | None = None,
) -> str:
    """
    Build the full CoT analysis prompt.

    Args:
        snapshot:     compact live scrape (ratios, quarterly, pros/cons, news)
        rag_context:  PDF-only RAG excerpts (annual report / concall text)
        stock_symbol: e.g. "TCS"
        sector:       detected sector string e.g. "IT", "Banking"
        deep_data:    raw_full.json from stock_cache (P&L, BS, CF, peers).
                      When None, only the compact snapshot is used.
    """
    step1_sector_guidance = _build_step1_sector_guidance(sector)
    financials_section = _format_deep_financials(deep_data, stock_symbol) if deep_data else ""
    snapshot_section = _format_snapshot(snapshot, stock_symbol)
    pdf_section = _build_pdf_section(rag_context, stock_symbol)
    macro_section = _build_macro_section()
    earnings_section = _build_recent_earnings_section(snapshot, deep_data)
    today = datetime.date.today().strftime("%-d %b %Y")

    prompt = f"""You are an expert Indian equity analyst with 15+ years of experience analyzing NSE/BSE listed companies.

Today's date: {today}. Use this to judge the recency of all news items — anything older than 3 months should be treated as background context, not a current signal.

Your task: Perform a rigorous fundamental analysis of {stock_symbol} and produce a structured investment verdict.

---
{financials_section}{snapshot_section}{earnings_section}{pdf_section}{macro_section}
---

## ANALYSIS INSTRUCTIONS

Work through each step carefully. ONLY use data explicitly present above. Do NOT assume or hallucinate facts not shown.

**DATA HIERARCHY — read in this order of priority:**
1. **PDF RESEARCH EXCERPTS** (if present above): primary source for management commentary, deal pipeline, attrition, margin guidance, and strategic outlook. Treat these as the company's own words from earnings calls. Cite specific figures or quotes where available.
2. **FINANCIAL DATA (from cache)**: P&L, balance sheet, cash flows, peers — the ground truth for quantitative scoring.
3. **LIVE SNAPSHOT**: current ratios, quarterly trends, news — for recency and sentiment.

If a PDF RESEARCH EXCERPTS section is present, you MUST draw on it in Steps 1 and 5. Explicitly note any management guidance on revenue growth, margins, deal wins, or headcount that appears in the excerpts.

---

## ⚠️ MANDATORY OUTPUT FORMAT

Before the JSON block you MUST produce ALL SEVEN tagged sections below, in order.
Do NOT skip any tag. Do NOT merge steps. Every tag must appear exactly once in your response.

<step1_output>
Business Quality Score: [X]/10
[Cite actual ROE/ROCE numbers, name the specific moat, address sector risks. No generic statements.]
</step1_output>

<step2_output>
Financial Health Score: [X]/10
[Quote actual numbers: revenue CAGR X%, OPM range X%–Y%, EPS from X to Y, OCF/PAT = X. No vague statements.]
</step2_output>

<step3_output>
Governance Score: [X]/10
Promoter holding: X% | Pledge: X% (or "not mentioned, assumed 0%")
[Red flags: only actual issues from the data, or "None identified"]
</step3_output>

<step4_output>
Valuation Score: [X]/10
Current PE: Xx | Sector benchmark: Xx–Xx | Premium/discount: X%
[Peer growth comparison and PEG conclusion]
</step4_output>

<step5_output>
Weighted conviction calculation:
  Business Quality : [X]/10 × 0.5 = [Y]
  Financial Health  : [X]/10 × 0.2 = [Y]
  Governance        : [X]/10 × 0.2 = [Y]
  Valuation         : [X]/10 × 0.1 = [Y]
  Subtotal          : [Z]
  Macro adjustment  : [±N] ([cite specific macro fact, or "0 — no clear signal"])
  Final conviction  : [Z]/10

Verdict: [BUY | WATCH | AVOID]
[One sentence on the deciding factor]
</step5_output>

<step6_output>
BUY ZONES (derived from bull/bear price scenarios in Step 5):
  Current price: ₹[X]
  AGGRESSIVE BUY: ₹[A]–₹[B] | For: [investor type] | Reasoning: [link to bull case] | Trigger: [entry condition]
  CONSERVATIVE BUY: ₹[C]–₹[D] | For: [investor type] | Reasoning: [link to base case] | Trigger: [event/de-rating]
  DEEP VALUE: ₹[E]–₹[F] | For: [investor type] | Reasoning: [link to bear case floor] | Trigger: [crisis/capitulation]
  Position: [overvalued — wait | in aggressive zone — fair for bulls | in conservative zone — good entry | in deep value — exceptional opportunity]
</step6_output>

<step7_output>
MARKET NARRATIVE vs VERDIKT POV:
  Dominant market story: [growth/turnaround/distress/hype — in one phrase]
  Market claims: [key claims extracted from news headlines]
  Emotional tone: [euphoric/fearful/neutral]
  VERDIKT fundamental view: [what the numbers actually show]
  Gap: Market expects [X metric] | Fundamentals support [Y metric] | Magnitude: [Large/Medium/Small/Aligned]
  Trade signal: [FADE | RIDE | IGNORE]
  Reasoning: [one sentence why]
</step7_output>

---

### Step 1: Business Quality Assessment
- State the core revenue model in one sentence.
- Name the specific moat: switching costs / regulatory / brand / network effects / scale. Be concrete.
- Cite actual ROE/ROCE. Score on TRAJECTORY + MOAT QUALITY, not just current level:
  - 8–10: Durable moat + ROCE >18% sustained 3+ years + market leadership. (HDFC Bank, Asian Paints tier)
  - 6–7: Real moat but returns building, OR strong returns but contested moat. Score trajectory.
  - 4–5: Questionable moat AND ROCE <10%, or category in structural decline.
  - 1–3: No moat, commoditised, near-zero or negative returns.
  CRITICAL: A genuine moat with improving trajectory can score 6–7 even if ROE is still low (e.g., JFIN at 1.23% ROE but Jio ecosystem moat = 6/10, NOT 5/10). Do NOT penalise trajectory businesses for current-period weakness.
{step1_sector_guidance}
→ Write your answer inside <step1_output> ... </step1_output>

### Step 2: Financial Health Check
- For banks: other income = fee/trading income is legitimate; don't flag it.
- Revenue growth: 5-year CAGR vs 10% benchmark. List actual values.
- OPM trend: list actual % values — stable / expanding / compressing?
- EPS trend: list actual values — growing / flat / declining?
- Debt: Net Debt/EBITDA <2x comfortable, 2–4x watch, >4x stress. Capital-intensive (infra, power) = higher D/E acceptable.
- OCF/PAT — interpret by business model:
  * NBFCs/Banks/Lending: negative OCF is NORMAL when loan book growing — loan disbursements = operating outflow. Do NOT flag as earnings quality risk if loan book growing >20% YoY and GNPA <3%. Write: "OCF/PAT reflects loan book expansion — standard for growing NBFC." Monitor GNPA trajectory instead.
  * Asset-light (IT, platforms, SaaS): OCF/PAT should be >1.0x. Below 0.8x = flag.
  * Capex-heavy (infra, manufacturing): 0.5–0.8x acceptable during expansion. Negative for 3+ years with no revenue growth = flag.
- Other income check: if Other Income > 20% of Net Profit → compute Operating PAT = Net Profit − Other Income → if Operating PE > 1.5× headline PE, flag "earnings quality risk."
→ Write your answer inside <step2_output> ... </step2_output>

### Step 3: Indian Governance — SCORE ONLY WHAT THE DATA SHOWS
CRITICAL RULE: Base your score ONLY on data present above. Do NOT invent or assume risks.

1. Promoter HOLDING %: from shareholding table. HIGH holding (>50%) by reputable group = POSITIVE.
2. Promoter PLEDGE % (CRITICAL — DIFFERENT from holding %):
   DEFINITION: Pledge = % of promoter's OWN shares pledged as loan collateral. NOT the same as their holding.
   WHERE TO FIND: Look for an explicit "Pledge %" or "Pledged shares" row in the shareholding table.
   IF NOT FOUND: Write "Promoter pledge: Not mentioned in data, assumed 0%"
   NEVER assume: A promoter holding 50% of the company does NOT mean they pledged 50%.
   EXAMPLE:
   ✓ CORRECT: "Promoter holding 50%, pledge 0% (not mentioned in data)"
   ✗ WRONG: "Promoter holding 50%, pledge 50%"
3. Promoter identity: Tata Sons, established MNC parent, Ambani (Reliance) = known institutional promoter = positive.
4. SEBI/audit issues: Only flag if explicitly mentioned in news or cons. If not mentioned = no issues.

SCORING GUIDE:
- 0% pledge + reputable group + no SEBI issues = 9-10/10
- Minor concern (pledge <10%, one filing delay) = 7-8/10
- Pledge 10-25% OR one SEBI issue = 5-6/10
- Pledge 25-50% = cap at 6/10. Include in key risks.
- Pledge 50-70% = cap at 4/10. Mark as red flag.
- Pledge >70% OR auditor mid-term resignation OR SEBI fraud notice = ≤2/10. Verdict must be AVOID.
→ Write your answer inside <step3_output> ... </step3_output>

### Step 4: Valuation — CITE THE ACTUAL PE AND COMPARE TO SECTOR BENCHMARK
MANDATORY: Quote the exact PE ratio from the ratios data.

- Current PE: [exact number]
- Sector PE benchmark:
    IT large-cap (TCS/Infosys): 22-30x normal. Post-AI slowdown: 18-22x may be new normal if growth <8%.
    IT mid-cap (LTIMindtree, Mphasis): 25-40x.
    Banking (private): 1.5-3x PB is more relevant. ROE >15% justifies >2x PB.
    FMCG: 40-60x for quality compounders. <35x = cheap.
    Pharma: 20-30x branded domestic; 15-22x generics.
    Auto: 15-25x. Evaluate at mid-cycle PE, not peak.
    Energy/Refining: 8-15x. Low PE is structural, not a discount.
- Premium/discount: "Xx vs sector range Xx-Xx = X% premium/discount"
- Is it justified? Compare company ROE vs sector avg ROE, revenue growth vs sector growth.
- PEER GROWTH TABLE (mandatory if available):
  * List each peer's NP Gr% and PE. Compute sector median growth and median PE.
  * State: "[Company] at X% growth vs sector median Y% — above/below."
  * SCORING CAPS FROM PEER COMPARISON (these override your initial estimate):
    - Company ONLY negative/zero grower in peer set → valuation score MAX 3/10 regardless of absolute PE
    - Company growing faster than all peers AND trading at discount to median → valuation score MIN 7/10
    - Company slowest grower AND trading at premium to all peers → valuation score MAX 3/10
    - Company fastest grower with PEG below peer median PEG → score 7–9/10
  * Always state: "Paying [X]x for [Y]% growth vs [Peer] at [A]x for [B]% growth → subject is [better/worse] value"
- PEG: A stock is only genuinely cheap if PE at discount AND growth ≥ peers. Never call cheap on PE alone.
→ Write your answer inside <step4_output> ... </step4_output>

### Step 5: Synthesis — Final Verdict
Using the four scores from steps 1–4, compute the weighted conviction and write it inside <step5_output>.

VERDICT RULES:
- Conviction >7.5 AND no red flags → BUY
- Conviction 6.0–7.5 AND no major red flags → WATCH
- Conviction <6.0 OR any governance red flag → AVOID
- OVERRIDE to AVOID regardless of score: pledge >70% spike QoQ / auditor resignation / SEBI fraud / SFIO
- SANITY CHECKS: conviction 7.8 → must be BUY; conviction 5.2 → must be AVOID; red_flags 2+ items → conviction ≤6.5; invalidation_triggers 5+ items → conviction ≤7.0
- If verdict = WATCH, the summary MUST contain: "WATCH means: [specific action]. Becomes BUY if [specific condition]. Becomes AVOID if [specific condition]."

DOWNSIDE SCENARIO (mandatory for all stocks):
- Quote the 52-week high and current price from market indicators. Calculate % from high.
- At the current PE of X, the market is pricing in approximately Y% earnings growth.
- Bear case: if growth slows to Z%, a fair PE would be W → implied price = ₹V → X% downside from current.
- Bull case: if growth accelerates to A%, PE re-rates to B → implied price = ₹C → X% upside.
- State: "Risk/reward ratio is [favorable/balanced/unfavorable] because [specific reason with numbers]."
- Only skip if 52-week price data is entirely absent from the snapshot.

MACRO ADJUSTMENT (±0.5 max, never exceed):
Use the MACROECONOMIC CONTEXT section as primary source. Rules:
- +0.3 to +0.5: direct, confirmed tailwind (cite specific policy + transmission mechanism)
- +0.1 to +0.2: mild industry-level tailwind
- 0: default unless clear directional impact; state "0 — no clear signal"
- -0.1 to -0.5: headwind (same specificity required)
- FORBIDDEN: vague adjustments ("positive environment") / double-counting (if already in Step 1 score, don't add macro again) / adjustments >0.5
Sector signals: IT: INR depreciation +0.5 / US slowdown -0.5 | Banking/NBFC: rate cut cycle +0.3→+0.5 / rate pause neutral / rising NPA -0.5 | Energy/OMCs: crude >$85 -0.5 / $65–75 neutral | FMCG: bad monsoon -0.5 / consumption boost +0.5 | Infra: capex front-loading +0.5 | Hospitality: disruptions -0.3→-0.5 / strong GDP +0.3 | Other: identify primary driver; clear signal ±0.3–0.5, ambiguous 0–±0.2
Cite the specific macro fact with date. If macro section absent, write "0 — macro context unavailable".

→ Write your answer inside <step5_output> ... </step5_output>

### Step 6: Buy Zones — Entry Price Framework
Extract current price from snapshot. If unavailable, set all zone values to 0 and skip.

ZONE CALCULATION (all anchored to your Step 5 price scenarios — do not invent):
- AGGRESSIVE BUY: lower = current price × 0.85–0.90 OR book value (whichever gives better support); upper = current price ± 5%. Should offer 40–60% upside to bull case from mid-point.
- CONSERVATIVE BUY: lower = current × 0.70–0.80; upper = current × 0.75–0.85. Align with base case fair value ±10%. Offers 25–40% upside from mid-point.
- DEEP VALUE: lower = bear case floor from Step 5; upper = current × 0.50–0.65 OR 0.8× book value (whichever is lower). Minimum 50% upside even in bear case.

HARD RULES:
- Zones must NOT overlap (aggressive high < conservative low < deep value high)
- Every zone must cite the specific valuation assumption it's anchored to
- Trigger must be a specific event ("GNPA exceeds 4%" / "EPS crosses ₹X") — NOT "if market falls"
- If all three zones are within 15% of each other: recalculate — insufficient differentiation
- If P/B at deep value zone > 1.2x: add WARNING — may not provide genuine margin of safety

POSITION (use exact phrase):
- Current price > aggressive high + 10%: "overvalued — wait for correction"
- Within aggressive zone: "at aggressive zone — fair for bulls with conviction"
- At aggressive/conservative boundary: "approaching value zone — watch for conservative entry"
- Within conservative zone: "in conservative buy zone — good entry for most investors"
- Within deep value zone: "in deep value zone — exceptional opportunity if thesis intact"

→ Write your answer inside <step6_output> ... </step6_output>

### Step 7: Market Narrative vs VERDIKT POV

EXTRACTION (NEVER invent — only use news items present in the data):
- Scan news for the dominant story. If <3 news items or no clear narrative: set trade_signal = "IGNORE", explain.
- Market claims: 2–5 claims, each citing source + date + specific number if available.
  GOOD: "[15 Mar 2026 — ET] Targets 40% revenue growth for FY26 based on deal pipeline"
  BAD: "Market believes growth will continue" (vague, no source)

VERDIKT VIEW (from your Steps 1–5):
- Acknowledge what the narrative gets RIGHT (be honest; not everything is FADE)
- Identify where narrative is CONTRADICTED by numbers with specific data
- Format: "Narrative claims X → data shows Y (cite step and figure)"

GAP QUANTIFICATION (must include specific metric + timeframe):
- Market expects: [X% growth / Y margins / Z multiple over FY26–27]
- Fundamentals support: [A% / B / C — cite which data point]
- Large >30% deviation | Medium 15–30% | Small <15% | Aligned <5%

TRADE SIGNAL (must be consistent with verdict):
- FADE: market pricing 2+ years out OR ignoring material risk → aligns with AVOID or upper WATCH
- RIDE: de-rating overdone OR quality improving faster than market realises → aligns with BUY or lower WATCH
- IGNORE: narrative matches fundamentals → aligns with WATCH at fair value
- CONSISTENCY CHECKS: FADE + BUY verdict = ERROR; RIDE + AVOID verdict = ERROR; Large gap + IGNORE = ERROR

→ Write your answer inside <step7_output> ... </step7_output>

RULES FOR THE JSON OUTPUT:

Rule 1 — "conviction": number 0-10. Must equal your weighted total above.
Rule 2 — "invalidation_triggers": NEGATIVE failure conditions that would break the thesis.
  CORRECT: "EBIT margin falls below 23% for 2 consecutive quarters"
  WRONG: "Improvement in governance" (that's an upside, not a trigger)
Rule 3 — "key_risks": Specific. Name the revenue segment, metric, or mechanism.
  For cyclical/seasonal businesses (hospitality, retail, agrochemicals, tourism):
  Do NOT flag known seasonal patterns as risks — Q1/Q2 slowdowns for a hotel chain are expected, not a risk.
  ONLY flag seasonality if it is WORSENING (e.g., peak season revenues declining YoY, or off-season losses deepening).
Rule 4 — "red_flags": ONLY issues found in the data. If none: ["None identified from available data"]
Rule 5 — "news_sentiment.note": DISTINGUISH confirmed revenue from mere announcements.
  Partnerships / MoUs / AI tie-ups with NO disclosed TCV or deal value = NOT a revenue-positive signal.
  CORRECT: "OpenAI partnership announced — no TCV disclosed. Watch for concrete deal wins over next 2 quarters before treating as positive."
  WRONG: "OpenAI partnership signals strong AI positioning" (no revenue confirmed — do not price in)
  Only call news positive if: earnings call confirms revenue impact, or TCV/contract value is explicitly stated.
Rule 6 — "summary": MAX 250 characters. Must contain: (1) verdict + deciding factor in one sentence, (2) key strength, (3) key risk, (4) current valuation context.
  Format: "[Stock] is [VERDICT] at [X]/10 because [deciding factor]. [Key strength] offset by [key risk] → [favorable/balanced/unfavorable] risk/reward at ₹[price] ([valuation metric])."
  If verdict = WATCH: add "WATCH means: [action]. BUY if [condition]. AVOID if [condition]."

```json
{{
  "stock": "{stock_symbol}",
  "verdict": "buy|watch|avoid",
  "conviction": 0.0,
  "conviction_breakdown": {{
    "business_quality": 0,
    "financial_health": 0,
    "governance": 0,
    "valuation": 0
  }},
  "summary": "2-3 sentence plain English summary of the thesis",
  "key_strengths": ["strength 1", "strength 2"],
  "key_risks": ["risk 1", "risk 2"],
  "red_flags": ["red flag 1"],
  "invalidation_triggers": ["specific metric change that would invalidate this thesis"],
  "watch_for_next_quarter": "what to watch in the next earnings",
  "news_sentiment": {{
    "overall": "positive|neutral|negative|mixed",
    "key_themes": ["theme from headlines"],
    "note": "1-sentence explanation of what the headlines signal"
  }},
  "buy_zones": {{
    "current_price": 0,
    "aggressive": {{
      "low": 0,
      "high": 0,
      "for": "high conviction investors willing to pay a premium",
      "reasoning": "link to bull case assumption from Step 5",
      "trigger": "condition that justifies entry at this level"
    }},
    "conservative": {{
      "low": 0,
      "high": 0,
      "for": "margin-of-safety investors seeking 10–20% discount",
      "reasoning": "link to base case valuation from Step 4",
      "trigger": "de-rating event or time-based correction that creates this entry"
    }},
    "deep_value": {{
      "low": 0,
      "high": 0,
      "for": "contrarian or distressed buyers requiring 25–40% discount",
      "reasoning": "link to bear case floor from Step 5 downside scenario",
      "trigger": "crisis, earnings miss, or sector capitulation that drives price here"
    }},
    "position": "overvalued — wait|in aggressive zone — fair for bulls|in conservative zone — good entry|in deep value — exceptional opportunity"
  }},
  "market_vs_verdikt": {{
    "market_narrative": "one-phrase dominant market story",
    "market_claims": ["claim extracted from news 1", "claim extracted from news 2"],
    "emotional_tone": "euphoric|fearful|neutral",
    "verdikt_view": "what the numbers actually show, contrasting market claims",
    "gap_analysis": {{
      "market_expects": "X% metric description",
      "fundamentals_support": "Y% metric description",
      "magnitude": "Large|Medium|Small|Aligned"
    }},
    "trade_signal": "FADE|RIDE|IGNORE",
    "reasoning": "one sentence explaining the gap and what to do about it"
  }}
}}
```

Important: conviction is 0-10. verdict is exactly one of: buy, watch, avoid. news_sentiment.overall is exactly one of: positive, neutral, negative, mixed. market_vs_verdikt.trade_signal is exactly one of: FADE, RIDE, IGNORE. buy_zones.position is exactly one of the four phrases above. If current price data is unavailable, set buy_zones.current_price to 0 and all zone low/high to 0.
"""
    return prompt.strip()


# ─── Deep financials section (from raw_full.json cache) ───────────────────────

def _format_deep_financials(raw: dict, ticker: str) -> str:
    """
    Format P&L, cash flows, balance sheet, and peer comparison from raw_full.json.
    Injected directly — not through RAG — so no data is lost to vector filtering.
    """
    parts = []

    pl = _format_pl(raw.get("annualPL", {}), raw.get("cashFlows", {}))
    if pl:
        parts.append(pl)

    bs = _format_bs(raw.get("balanceSheet", {}))
    if bs:
        parts.append(bs)

    peers = _format_peers(raw.get("peerComparison", {}))
    if peers:
        parts.append(peers)

    if not parts:
        return ""

    return f"## {ticker} — FINANCIAL DATA (from cache)\n\n" + "\n\n".join(parts) + "\n\n---\n\n"


def _format_pl(pl: dict, cf: dict) -> str:
    headings = pl.get("headings", [])
    values = pl.get("values", [])
    if not headings or not values:
        return ""

    # Pick 5-6 representative years: first, ~5yr ago, last 3, TTM
    n = len(headings)
    idx = sorted(set([0, max(0, n - 6), max(0, n - 4), max(0, n - 3), max(0, n - 2), n - 1]))
    selected_heads = [headings[i] for i in idx]

    # Key row labels to include
    key_rows = ["Sales", "OPM %", "Net Profit", "EPS in Rs", "Dividend Payout %"]

    lines = ["### Revenue & Profitability"]
    lines.append(f"{'Metric':<22} | " + " | ".join(f"{h:>10}" for h in selected_heads))
    lines.append("-" * (22 + 15 * len(selected_heads)))

    for label in key_rows:
        row = _find_row_by_label(values, label)
        if not row:
            continue
        vals = row.get("values", [])
        selected_vals = [vals[i] if i < len(vals) else "" for i in idx]
        lines.append(f"{label:<22} | " + " | ".join(f"{v:>10}" for v in selected_vals))

    # Derived insights
    rev_row = _find_row_by_label(values, "Sales")
    pat_row = _find_row_by_label(values, "Net Profit")
    opm_row = _find_row_by_label(values, "OPM %")

    insights = []
    if rev_row:
        rev_vals = rev_row.get("values", [])
        cagr_5 = _cagr(rev_vals, years=5)
        cagr_10 = _cagr(rev_vals, years=10)
        if cagr_5:
            insights.append(f"Revenue 5yr CAGR: {cagr_5:.1f}%")
        if cagr_10:
            insights.append(f"Revenue 10yr CAGR: {cagr_10:.1f}%")

    if opm_row:
        opm_vals = [v.strip("%").strip() for v in opm_row.get("values", []) if v.strip()]
        if opm_vals:
            insights.append(f"OPM range: {min(opm_vals, key=lambda x: _safe_float(x))}% – {max(opm_vals, key=lambda x: _safe_float(x))}%")

    # OCF/PAT quality check using cash flow data
    cf_values = cf.get("values", [])
    ocf_row = _find_row_by_label(cf_values, "Cash from Operating Activity") if cf_values else None
    if ocf_row and pat_row:
        ocf_vals = ocf_row.get("values", [])
        pat_vals = pat_row.get("values", [])
        # Use latest period from both datasets (assuming they're aligned)
        # If CF data starts earlier than P&L, both [-1] will still be from latest available year
        ocf_latest = _safe_float(ocf_vals[-1]) if ocf_vals else None
        pat_latest = _safe_float(pat_vals[-1]) if pat_vals else None  # use same period as OCF
        if ocf_latest and pat_latest and pat_latest > 0:
            ratio = ocf_latest / pat_latest
            insights.append(f"OCF/PAT (latest): {ratio:.2f}x ({'quality earnings' if ratio >= 0.8 else 'below par'})")

    if insights:
        lines.append("")
        lines.append("Key insights: " + " | ".join(insights))

    # Cash flow trend inline
    cf_headings = cf.get("headings", [])
    if ocf_row and cf_headings:
        ocf_vals = ocf_row.get("values", [])
        last5 = list(zip(cf_headings[-5:], ocf_vals[-5:]))
        trend = " → ".join(f"{h}={v}" for h, v in last5)
        lines.append(f"OCF trend (₹Cr): {trend}")

    return "\n".join(lines)


def _format_bs(bs: dict) -> str:
    headings = bs.get("headings", [])
    values = bs.get("values", [])
    if not headings or not values:
        return ""

    # Only show last 4 years — balance sheet trends matter more than 10yr history
    n = len(headings)
    idx = list(range(max(0, n - 4), n))
    selected_heads = [headings[i] for i in idx]

    key_rows = ["Borrowings", "Reserves", "Equity Capital", "Investments", "Total Assets"]
    lines = ["### Balance Sheet (last 4 periods)"]
    lines.append(f"{'Metric':<22} | " + " | ".join(f"{h:>10}" for h in selected_heads))
    lines.append("-" * (22 + 15 * len(selected_heads)))

    for label in key_rows:
        row = _find_row_by_label(values, label)
        if not row:
            continue
        vals = row.get("values", [])
        selected_vals = [vals[i] if i < len(vals) else "" for i in idx]
        lines.append(f"{label:<22} | " + " | ".join(f"{v:>10}" for v in selected_vals))

    # Net cash/debt position
    borrow_row = _find_row_by_label(values, "Borrowings")
    invest_row = _find_row_by_label(values, "Investments")
    if borrow_row and invest_row:
        borrow_vals = borrow_row.get("values", [])
        invest_vals = invest_row.get("values", [])
        borrow_latest = _safe_float(borrow_vals[-1]) if borrow_vals else None
        invest_latest = _safe_float(invest_vals[-1]) if invest_vals else None
        if borrow_latest is not None and invest_latest is not None:
            net = invest_latest - borrow_latest
            label = "net cash" if net > 0 else "net debt"
            lines.append(f"\nNet position: Investments {invest_vals[-1]} − Borrowings {borrow_vals[-1]} = {net:,.0f}Cr ({label})")

    return "\n".join(lines)


def _format_peers(peer_data: dict) -> str:
    headings = peer_data.get("headings", [])
    peers = peer_data.get("peers", [])
    median = peer_data.get("median")
    if not headings or not peers:
        return ""

    # Column mapping: pick the most useful columns for analysis
    # Headings from scraper use data-tooltip values (e.g. "Current Price", "Price to Earning")
    _COL_DISPLAY = {
        "Name": ("Company", 16),
        "Price to Earning": ("P/E", 6),
        "Return on capital employed": ("ROCE%", 7),
        "YOY Quarterly profit growth": ("NP Gr%", 7),
        "Net Profit latest quarter": ("NP Qtr(Cr)", 11),
        "Market Capitalization": ("MarCap(Cr)", 11),
        "Dividend yield": ("DivYld%", 8),
    }

    # Only include columns that exist in the data
    display_cols = [(orig, disp, w) for orig, (disp, w) in _COL_DISPLAY.items() if orig in headings]
    if not display_cols:
        # Fallback: show first 6 columns as-is
        display_cols = [(h, h[:10], 10) for h in headings[:6]]

    lines = ["### Peer Comparison"]
    header = " | ".join(f"{disp:<{w}}" for _, disp, w in display_cols)
    lines.append(header)
    lines.append("-" * len(header))

    for peer in peers:
        row = " | ".join(f"{str(peer.get(orig, '')):<{w}}" for orig, _, w in display_cols)
        lines.append(row)

    if median:
        lines.append("-" * len(header))
        lines.append(" | ".join(
            f"{'Sector Median' if i == 0 else str(median.get(orig, '')):<{w}}"
            for i, (orig, _, w) in enumerate(display_cols)
        ))

    return "\n".join(lines)


# ─── Compact snapshot section (live scrape) ───────────────────────────────────

def _format_snapshot(snapshot: dict, symbol: str) -> str:
    """Format the compact live scrape data — ratios, quarterly, pros/cons, news."""
    parts = []

    about = snapshot.get("aboutText", "")
    if about:
        parts.append(f"**Company:** {about[:400]}")

    ratios = snapshot.get("ratios", [])
    if ratios:
        ratio_lines = [f"  {r['name']}: {r['value']}" for r in ratios if r.get("name") and r.get("value")]
        parts.append("**Key Ratios (current):**\n" + "\n".join(ratio_lines))

    pros = snapshot.get("pros", [])
    cons = snapshot.get("cons", [])
    if pros:
        parts.append("**Pros (Screener):**\n" + "\n".join(f"  + {p}" for p in pros[:5]))
    if cons:
        parts.append("**Cons (Screener):**\n" + "\n".join(f"  - {c}" for c in cons[:5]))

    quarterly = snapshot.get("quarterly", {})
    headings = quarterly.get("headings", [])
    values = quarterly.get("values", [])
    if headings and values:
        header_str = " | ".join(["Metric"] + headings[-6:])
        rows = []
        for row in values[:8]:
            cat = row.get("category", "")
            vals = row.get("values", [])[-6:]
            rows.append(" | ".join([cat] + vals))
        parts.append("**Recent Quarterly Trends:**\n" + header_str + "\n" + "\n".join(rows))

    shareholding = snapshot.get("shareholding", [])
    if shareholding:
        sh_lines = []
        for row in shareholding[:5]:
            cat = row.get("category", "")
            vals = [v for k, v in row.items() if k != "category"]
            latest = vals[0] if vals else ""
            if cat:
                sh_lines.append(f"  {cat}: {latest}")
        if sh_lines:
            parts.append("**Shareholding (latest):**\n" + "\n".join(sh_lines))

    market = snapshot.get("marketIndicators", [])
    if market:
        mi_lines = [f"  {m['name']}: {m['value']} {m.get('percentage', '')}" for m in market[:4] if m.get("name")]
        if mi_lines:
            parts.append("**Market:**\n" + "\n".join(mi_lines))

    news = snapshot.get("news", [])
    if news:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)
        fresh_news = [
            n for n in news
            if n.get("title") and _parse_news_date(n.get("time", "")) >= cutoff
        ]
        news_lines = [_fmt_news_item(n) for n in fresh_news[:8]]
        if news_lines:
            parts.append("**Recent News:**\n" + "\n".join(news_lines))

    body = "\n\n".join(parts) if parts else "(No snapshot data)"
    return f"## {symbol} — LIVE SNAPSHOT\n\n{body}\n\n---\n\n"


# ─── Macro context section ────────────────────────────────────────────────────

_MACRO_MAX_CHARS = 2500  # cap macro injection to keep prompt lean


def _build_macro_section() -> str:
    """Inject macro_context.md if present, truncated to _MACRO_MAX_CHARS."""
    content = _load_macro_context()
    if not content:
        return ""
    if len(content) > _MACRO_MAX_CHARS:
        # Truncate at a newline boundary
        cut = content.rfind("\n", 0, _MACRO_MAX_CHARS)
        content = content[: cut if cut > _MACRO_MAX_CHARS // 2 else _MACRO_MAX_CHARS]
        content = content.rstrip() + "\n\n_(macro context truncated for brevity)_"
    return (
        "## MACROECONOMIC CONTEXT (India, current)\n"
        "(Generated from live news research — treat as authoritative background)\n\n"
        f"{content}\n\n---\n\n"
    )


# ─── Recent earnings context section ──────────────────────────────────────────

_EARNINGS_KEYWORDS = (
    "results", "earnings", "profit", "revenue", "q1", "q2", "q3", "q4",
    "quarter", "fy25", "fy26", "annual", "guidance", "outlook",
)


def _build_recent_earnings_section(snapshot: dict, deep_data: dict | None) -> str:
    """
    Surfaces recent earnings-related signals from news + peer data.
    Gives the LLM explicit context about what peers reported this quarter.
    """
    parts: list[str] = []

    # 1. Filter news for earnings/results items
    news = snapshot.get("news", [])
    earnings_news = [
        n for n in news
        if any(kw in (n.get("title", "") + n.get("time", "")).lower() for kw in _EARNINGS_KEYWORDS)
    ]
    if earnings_news:
        lines = [_fmt_news_item(n) for n in earnings_news[:6] if n.get("title")]
        if lines:
            parts.append("**Earnings-related news:**\n" + "\n".join(lines))

    # 2. Quarterly trend for the company (last 2 quarters vs year-ago for key metrics)
    quarterly = snapshot.get("quarterly", {})
    headings = quarterly.get("headings", [])
    values = quarterly.get("values", [])
    if headings and values:
        # Show YoY comparison: latest Q vs same Q last year (offset by 4)
        n_cols = len(headings)
        if n_cols >= 5:
            latest_q = headings[-1]
            yoy_q = headings[-5] if n_cols >= 5 else headings[0]
            yoy_lines = []
            for row in values[:5]:
                cat = row.get("category", "")
                vals = row.get("values", [])
                v_latest = vals[-1] if vals else ""
                v_yoy = vals[-5] if len(vals) >= 5 else ""
                if v_latest and v_yoy:
                    yoy_lines.append(f"  {cat}: {yoy_q}={v_yoy} → {latest_q}={v_latest}")
            if yoy_lines:
                parts.append(f"**YoY quarterly comparison ({yoy_q} → {latest_q}):**\n" + "\n".join(yoy_lines))

    # 3. Peer NP growth from deep_data (extracted for quick reference)
    if deep_data:
        peer_data = deep_data.get("peerComparison", {})
        headings_p = peer_data.get("headings", [])
        peers = peer_data.get("peers", [])
        # Find "YOY Quarterly profit growth" column
        np_gr_col = next((h for h in headings_p if "profit growth" in h.lower()), None)
        name_col = next((h for h in headings_p if "name" in h.lower()), None)
        if np_gr_col and name_col and peers:
            growth_lines = []
            for p in peers:
                name = str(p.get(name_col, ""))[:20]
                gr = str(p.get(np_gr_col, ""))
                if name and gr:
                    growth_lines.append(f"  {name}: {gr}%")
            if growth_lines:
                parts.append("**Peer YoY quarterly profit growth (NP Gr%):**\n" + "\n".join(growth_lines))

    if not parts:
        return ""

    body = "\n\n".join(parts)
    return (
        f"## RECENT EARNINGS CONTEXT\n"
        f"(Latest quarterly results and peer earnings — use for Step 2 and Step 4 analysis)\n\n"
        f"{body}\n\n---\n\n"
    )


# ─── PDF research section (RAG retrieval) ─────────────────────────────────────

def _build_pdf_section(rag_context: str, stock_symbol: str) -> str:
    """Render PDF excerpts only when content exists."""
    if not rag_context or not rag_context.strip():
        return ""
    return (
        f"## {stock_symbol} — PDF RESEARCH EXCERPTS\n"
        "(Selectively retrieved from annual report / concall transcript)\n\n"
        f"{rag_context}\n\n---\n\n"
    )


# ─── Sector guidance ──────────────────────────────────────────────────────────

def _build_step1_sector_guidance(sector: str) -> str:
    s = (sector or "").upper()

    if s == "IT":
        return (
            "- SECTOR-SPECIFIC (IT): Address AI disruption directly:\n"
            "  * At-risk segments: testing ~20%, junior coding, BPO ~15%, app maintenance\n"
            "  * Protected: complex transformations, regulatory compliance, AI deployment for clients\n"
            "  * Revenue growth: >10% YoY = BUY signal. 7-10% = WATCH. <7% with no recovery = AVOID.\n"
            "  * MANDATORY PEER GROWTH CHECK: From the peer comparison table, list the YoY profit growth % for each peer.\n"
            "    Compute whether the company is above or below the sector median growth. State it explicitly:\n"
            "    'TCS growing at X% vs sector median Y%' — if below median, write: 'underperforming peers on growth.'\n"
            "  * Attrition: >20% hurts margins. Normal post-2022 range: 12-16%.\n"
            "  * INR depreciation = positive for margins (+50-100 bps EBIT per ₹1 move).\n"
            "  * IF PDF RESEARCH EXCERPTS ARE PRESENT: extract and cite the following from the concall text:\n"
            "    — Attrition % (trailing twelve months or latest quarter)\n"
            "    — Deal TCV won in the quarter (total and large deals)\n"
            "    — Management guidance on revenue growth and EBIT margin for next quarter/year\n"
            "    — Any commentary on AI-related deal wins or revenue impact\n"
            "    Quote the actual numbers or phrases. If not found in excerpts, say 'not mentioned in excerpts'."
        )
    elif s == "BANKING":
        return (
            "- SECTOR-SPECIFIC (Banking/NBFC):\n"
            "  * NIM 3-4% for banks. Compressing NIM = margin risk.\n"
            "  * GNPA/NNPA: >5% GNPA = concern. Trend direction matters more than level.\n"
            "  * Credit growth >15% YoY is healthy.\n"
            "  * Use PB ratio not PE: 1.5-3x PB for private banks. ROE >15% justifies >2x PB.\n"
            "  * RBI rate cuts = positive for loan demand."
        )
    elif s == "ENERGY":
        return (
            "- SECTOR-SPECIFIC (Energy): ROCE 8-12% is NORMAL — do NOT penalize.\n"
            "  * For Reliance: evaluate O2C, Jio, Retail as separate segments.\n"
            "  * GRM and crude sensitivity matter more than PE for refiners.\n"
            "  * Crude >$90/barrel sustained = negative for OMCs (BPCL, IOC). Positive for ONGC.\n"
            "  * PE benchmark: 8-15x is structural, not a discount."
        )
    elif s == "PHARMA":
        return (
            "- SECTOR-SPECIFIC (Pharma):\n"
            "  * US FDA: any 483 observations or import alerts = serious red flag.\n"
            "  * Revenue mix: domestic formulations vs US generics vs API.\n"
            "  * R&D spend <5% of revenue = low for branded pharma.\n"
            "  * PE benchmark: 20-30x branded domestic; 15-22x generics."
        )
    elif s == "AUTO":
        return (
            "- SECTOR-SPECIFIC (Auto):\n"
            "  * Volume growth (units) matters as much as revenue.\n"
            "  * EV transition: % of portfolio in EV? ICE players face structural disruption.\n"
            "  * Operating leverage: margins expand sharply in up-cycles, compress in down-cycles.\n"
            "  * PE benchmark: 15-25x. Evaluate at mid-cycle, not peak."
        )
    elif s == "FMCG":
        return (
            "- SECTOR-SPECIFIC (FMCG):\n"
            "  * Volume growth (excluding price hikes) is the true health indicator.\n"
            "  * Rural penetration and distribution reach are the real moats.\n"
            "  * Below-normal monsoon = negative for rural FMCG.\n"
            "  * PE benchmark: 40-60x for quality compounders. <35x = potentially cheap."
        )
    else:
        return (
            "- Address the key moat question: what prevents a competitor from taking these customers?\n"
            "  Moat types in India: switching costs, regulatory moat (banking licenses, power zones),\n"
            "  scale (Asian Paints), network effects (BSE/NSE, Naukri), brand (Titan, Pidilite).\n"
            "- ROCE >20% consistently = strong business. 15-20% = decent. <12% = needs strong story.\n"
            "- MANDATORY PEER GROWTH CHECK (applies to all sectors):\n"
            "  * From the peer comparison table, list YoY profit growth % for each peer.\n"
            "  * State explicitly: '[Company] at X% growth vs sector median Y%'\n"
            "  * Banking: compare ROE + NP growth. FMCG: compare volume growth + pricing power.\n"
            "  * Auto: compare volume growth + margin trends. Energy: compare capacity utilization + realization.\n"
            "  * If below median: 'Growing slower than peers — premium PE not justified.'"
        )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _find_row_by_label(values: list, label: str) -> dict | None:
    """Find first row whose category starts with or contains the label (case-insensitive)."""
    label_lower = label.lower()
    # Exact-start match first
    for row in values:
        cat = row.get("category", "").lower().strip().rstrip("+").strip()
        if cat.startswith(label_lower):
            return row
    # Fallback: substring match
    for row in values:
        cat = row.get("category", "").lower()
        if label_lower in cat:
            return row
    return None


def _safe_float(s: str) -> float:
    """Parse number strings like '1,17,966' or '27%' to float."""
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return 0.0


def _cagr(values: list, years: int) -> float | None:
    """Compute CAGR over the last `years` periods in a values list."""
    clean = [_safe_float(v) for v in values if _safe_float(v) > 0]
    if len(clean) < years + 1:
        return None
    start = clean[-(years + 1)]
    end = clean[-1]
    if start <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100
