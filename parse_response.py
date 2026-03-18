#!/usr/bin/env python3
"""
parse_response.py

Takes a raw LLM response (XML step tags + JSON block) and saves it as a
properly structured analysis JSON to data/{TICKER}_analysis.json.

Usage:
    python parse_response.py <response_file> [--ticker IRCTC] [--sector "Travel & Tourism"] [--model claude-sonnet-4-6]
    python parse_response.py <response_file>            # auto-detects ticker from JSON
    cat response.txt | python parse_response.py -       # read from stdin
"""

import sys
import re
import json
import argparse
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


# ─────────────────────────────────────────────────────────────────────────────
# PARSING
# ─────────────────────────────────────────────────────────────────────────────

def extract_step_outputs(text: str) -> dict:
    steps = {}
    for i in range(1, 8):
        tag = f"step{i}_output"
        m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
        if m:
            steps[f"step{i}"] = m.group(1).strip()
    return steps


def extract_json(text: str) -> dict | None:
    patterns = [
        r"```json\s*(\{.*?\})\s*```",
        r"```\s*(\{.*?\})\s*```",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue

    # Bare JSON block — find outermost { ... }
    start = text.rfind("{")           # last { in case step outputs have stray {
    # Actually we want the FIRST standalone JSON object after the step tags
    # Find the JSON by looking for the block that contains "verdict"
    for m in re.finditer(r"\{", text):
        candidate_start = m.start()
        # Find matching closing brace
        depth = 0
        for i, ch in enumerate(text[candidate_start:]):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[candidate_start : candidate_start + i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if "verdict" in parsed:
                            return parsed
                    except json.JSONDecodeError:
                        break

    return None


def build_record(raw: str, ticker: str | None, sector: str | None, model: str | None) -> dict:
    step_outputs = extract_step_outputs(raw)
    parsed_json = extract_json(raw)

    if parsed_json is None:
        print("ERROR: Could not find a valid JSON block with a 'verdict' field in the response.")
        sys.exit(1)

    # Auto-detect ticker from JSON if not provided
    stock = (ticker or parsed_json.get("stock") or "UNKNOWN").upper()
    if not ticker and "stock" not in parsed_json:
        print(f"WARNING: No ticker found in JSON. Using '{stock}'. Pass --ticker to override.")

    record = {
        "stock":                  stock,
        "verdict":                parsed_json.get("verdict", "parse_error"),
        "conviction":             parsed_json.get("conviction", 0),
        "conviction_breakdown":   parsed_json.get("conviction_breakdown", {}),
        "summary":                parsed_json.get("summary", ""),
        "key_strengths":          parsed_json.get("key_strengths", []),
        "key_risks":              parsed_json.get("key_risks", []),
        "red_flags":              parsed_json.get("red_flags", []),
        "invalidation_triggers":  parsed_json.get("invalidation_triggers", []),
        "watch_for_next_quarter": parsed_json.get("watch_for_next_quarter", ""),
        "news_sentiment":         parsed_json.get("news_sentiment", {}),
        "market_vs_verdikt":      parsed_json.get("market_vs_verdikt", {}),
        "step_outputs":           step_outputs,
        "model_used":             model or parsed_json.get("model_used", "unknown"),
        "sector":                 sector or parsed_json.get("sector", ""),
        "rag_context_length":     parsed_json.get("rag_context_length", 0),
        "parse_failed":           parsed_json.get("parse_failed", False),
    }

    return record, stock


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Parse raw LLM response → data/{TICKER}_analysis.json")
    parser.add_argument("file", help="Path to raw response file, or '-' for stdin")
    parser.add_argument("--ticker",  default=None, help="Stock ticker (e.g. IRCTC). Auto-detected from JSON if omitted.")
    parser.add_argument("--sector",  default=None, help="Sector label (e.g. 'Travel & Tourism')")
    parser.add_argument("--model",   default=None, help="Model name (e.g. claude-sonnet-4-6)")
    args = parser.parse_args()

    # Read raw response
    if args.file == "-":
        raw = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.exists():
            print(f"ERROR: File not found: {path}")
            sys.exit(1)
        raw = path.read_text(encoding="utf-8")

    if not raw.strip():
        print("ERROR: Input is empty.")
        sys.exit(1)

    # Parse
    record, stock = build_record(raw, args.ticker, args.sector, args.model)

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{stock}_analysis.json"

    if out_path.exists():
        print(f"WARNING: {out_path} already exists — overwriting.")

    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    # Summary
    steps_found = len(record["step_outputs"])
    print(f"\n✓ Parsed successfully")
    print(f"  Stock    : {stock}")
    print(f"  Verdict  : {record['verdict'].upper()}  (conviction {record['conviction']})")
    print(f"  Sector   : {record['sector'] or '(none)'}")
    print(f"  Model    : {record['model_used']}")
    print(f"  Steps    : {steps_found}/7 extracted")
    print(f"  Saved to : {out_path}")


if __name__ == "__main__":
    main()
