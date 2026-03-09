"""
Backfill stock_checklist for all stocks that already exist in stock_cache/.

Run from the project root:
    python scripts/backfill_checklist.py
"""
import json
import os
import sys
from pathlib import Path

# Make sure the project root is on sys.path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from api.db import upsert_checklist_from_raw, update_checklist_rag_and_prompt

CACHE_DIR = ROOT / "stock_cache"

def backfill():
    tickers = sorted(
        d.name for d in CACHE_DIR.iterdir()
        if d.is_dir() and (d / "raw_full.json").exists()
    )
    print(f"Found {len(tickers)} stocks with raw_full.json\n")

    for ticker in tickers:
        raw_path   = CACHE_DIR / ticker / "raw_full.json"
        prompt_path = CACHE_DIR / ticker / "latest_prompt.txt"
        rag_path   = CACHE_DIR / ticker / "rag_index"

        print(f"  {ticker}...", end=" ", flush=True)

        try:
            raw = json.loads(raw_path.read_text())
            upsert_checklist_from_raw(ticker, raw)
        except Exception as e:
            print(f"FAILED (raw): {e}")
            continue

        # RAG: check if rag_index directory has files in it
        rag_ok = rag_path.is_dir() and any(rag_path.iterdir())

        # Prompt: read from file if it exists
        prompt = None
        if prompt_path.exists():
            try:
                prompt = prompt_path.read_text(encoding="utf-8")
            except Exception:
                pass

        try:
            update_checklist_rag_and_prompt(ticker, rag_ok, prompt)
        except Exception as e:
            print(f"FAILED (rag/prompt): {e}")
            continue

        parts = []
        if rag_ok:     parts.append("RAG ✓")
        if prompt:     parts.append(f"prompt {len(prompt)//1000}k chars")
        print("✓" + (f"  [{', '.join(parts)}]" if parts else ""))

    print(f"\nBackfill complete — {len(tickers)} stocks processed.")

if __name__ == "__main__":
    backfill()
