#!/usr/bin/env python3
"""
Migration: add buy_zones and market_vs_verdikt JSONB columns to analyses table.
Also patches the latest IREDA row with the buy_zones/market_vs_verdikt data.

Usage:
    python migrations/add_buy_zones_narrative.py
"""

import json
import os
import sys

import psycopg2
import psycopg2.extras

DSN = os.environ.get("DATABASE_URL")
if not DSN:
    print("ERROR: DATABASE_URL env var not set")
    sys.exit(1)

conn = psycopg2.connect(DSN, cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

# ── 1. Add columns if they don't exist ──────────────────────────────────────
print("Adding columns to analyses table...")
cur.execute("""
    ALTER TABLE analyses
        ADD COLUMN IF NOT EXISTS buy_zones        JSONB DEFAULT '{}'::jsonb,
        ADD COLUMN IF NOT EXISTS market_vs_verdikt JSONB DEFAULT '{}'::jsonb;
""")
print("  ✓ buy_zones column added (or already existed)")
print("  ✓ market_vs_verdikt column added (or already existed)")

# ── 2. Patch latest IREDA row ────────────────────────────────────────────────
print("\nPatching latest IREDA analysis row...")

IREDA_BUY_ZONES = {
    "current_price": 115,
    "aggressive": {
        "low": 108,
        "high": 120,
        "for": "High-conviction investors with 18–24 month horizon accepting RE sector concentration and NPA trajectory risk",
        "reasoning": "Current price ₹115 is 38.5% below the 52-week high of ₹187; base case fair value ₹136–₹145 (FY26E EPS ₹8.0–₹8.5 × 17x) implies 18–26% upside. PEG of 0.46x is the most attractive in the sector. At ₹108–₹120, risk/reward asymmetry (18% downside vs 26%+ base upside) favors entry for those comfortable with current disclosure gaps.",
        "trigger": "Stock is already within this zone at ₹115. Best entered on a further 3–5% market-wide dip rather than stock-specific negative news. Ideal entry after Mar 2026 quarterly results clarify the Jun 2025 EPS anomaly."
    },
    "conservative": {
        "low": 95,
        "high": 107,
        "for": "Margin-of-safety investors requiring a 10–20% buffer to base case fair value before committing capital",
        "reasoning": "At ₹100, forward PE ~11.8x on FY26E EPS ₹8.5 — near PFC/REC territory despite higher growth; P/B ~2.17x still above book but materially discounted for ROE of 18%. Provides adequate buffer against NPA deterioration scenario without requiring a full bear case to materialise.",
        "trigger": "Private sector GNPA confirmed rising above 4%, a dividend payment announcement reducing retained capital, or broader NBFC sector de-rating triggered by RBI policy action on infrastructure lending risk weights."
    },
    "deep_value": {
        "low": 75,
        "high": 94,
        "for": "Contrarian investors requiring 25–35% discount to current price and 3+ year holding horizon",
        "reasoning": "Bear case: NPA stress drives provisioning spike, growth slows to 15%, PE compresses to 12x; EPS FY26E ~₹7.8 implies floor near ₹94. At ₹75–₹85, stock trades at 8.8–10x forward earnings — at or below PFC/REC multiples despite structurally higher growth; near-book-value support (Book Value ₹46 growing ~15%/yr reaches ~₹63 by FY27) provides downside protection.",
        "trigger": "Multiple large renewable energy developer defaults creating systemic NPA spike, RBI-mandated provisioning changes for Stage 2 renewable infrastructure assets, or broad market capitulation driving PSU NBFC sector to distressed multiples."
    },
    "position": "in aggressive zone — fair for bulls"
}

IREDA_MARKET_VS_VERDIKT = {
    "market_narrative": "Policy-mandated renewable energy growth compounder with government backing",
    "market_claims": [
        "QIP of ₹2,005.90 Cr completed successfully — strong institutional confidence in IREDA's growth trajectory",
        "MNRE MoU sets ₹8,200 Cr revenue target for FY26 — management signalling continued hypergrowth",
        "FY27 borrowing plan review signals uninterrupted loan book expansion"
    ],
    "emotional_tone": "neutral",
    "verdikt_view": "The growth narrative is real but has already partially corrected — PAT growth 35.64% and loan book growth 27.78% are genuine but the MNRE MoU target of ₹8,200 Cr implies 22% revenue growth in FY26 vs 35.83% in FY25, signalling deceleration. The market re-rated from ~28x to 17.1x (a 39% PE compression) which is the right directional move. Private NPA growing 28.6% YoY is not yet in the dominant narrative but is the most important variable to watch; the Jun 2025 EPS dip to ₹0.88 may be the first visible symptom.",
    "gap_analysis": {
        "market_expects": "30–35% EPS CAGR continuation based on government policy tailwind and loan book trajectory",
        "fundamentals_support": "20–25% EPS growth in FY26 (consistent with MNRE MoU 22% revenue growth guidance, higher leverage costs, and NPA provisioning headwinds)",
        "magnitude": "Small"
    },
    "trade_signal": "RIDE",
    "reasoning": "The stock has been over-corrected from post-IPO euphoria — a 38.5% decline from ₹187 to ₹115 has compressed PE from ~28x to 17.1x and PEG to 0.46x, which undervalues a structurally sound government NBFC growing at 2× sector median; modest fundamental-based upside (base case ₹136–₹145) is being missed by the cautious market tone."
}

cur.execute("""
    UPDATE analyses
    SET
        buy_zones         = %s,
        market_vs_verdikt = %s
    WHERE id = (
        SELECT id FROM analyses
        WHERE stock_symbol = 'IREDA'
        ORDER BY created_at DESC
        LIMIT 1
    )
""", (
    psycopg2.extras.Json(IREDA_BUY_ZONES),
    psycopg2.extras.Json(IREDA_MARKET_VS_VERDIKT),
))

patched = cur.rowcount
if patched:
    print(f"  ✓ IREDA latest row patched ({patched} row updated)")
else:
    print("  ⚠ No IREDA row found — skipping patch")

conn.commit()
cur.close()
conn.close()
print("\nMigration complete.")
