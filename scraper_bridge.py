"""
Python → Node.js scraper bridge.
Calls the Node.js scraper as a subprocess and returns the compact snapshot dict.
"""

import json
import subprocess
import os

SCRAPER_SCRIPT = os.path.join(os.path.dirname(__file__), "scraper", "run_scraper.js")
PROJECT_ROOT = os.path.dirname(__file__)


def fetch_compact_snapshot(screener_path: str, timeout: int = 300) -> dict:
    """
    Call the Node.js scraper for a given Screener path.

    Args:
        screener_path: e.g. "/company/INFY/consolidated/"
        timeout: seconds before giving up

    Returns:
        Compact stock snapshot dict (same structure as buildCompactStockSnapshot output)

    Raises:
        RuntimeError if scraper fails or times out
    """
    if not screener_path.startswith("/company/"):
        raise ValueError(f"Invalid screener path: '{screener_path}'. Must start with /company/")

    cmd = ["node", SCRAPER_SCRIPT, screener_path]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Scraper timed out after {timeout}s for {screener_path}")
    except FileNotFoundError:
        raise RuntimeError(
            "Node.js not found. Install Node.js and run 'npm install' in the scraper directory."
        )

    if result.returncode != 0:
        raise RuntimeError(
            f"Scraper failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    if not result.stdout.strip():
        raise RuntimeError("Scraper returned empty output")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse scraper output as JSON: {e}\nOutput: {result.stdout[:200]}")


def search_stocks(query: str) -> list:
    """
    Quick stock search via Screener API (uses axios, no browser needed).
    Returns list of {name, url} dicts from Screener.
    """
    search_script = """
import { searchStocks } from './scraper/index.js';
const results = await searchStocks(process.argv[2]);
process.stdout.write(JSON.stringify(results));
"""
    # Write a temp script and run it
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.mjs', delete=False, dir=PROJECT_ROOT) as f:
        f.write(search_script)
        temp_path = f.name

    try:
        result = subprocess.run(
            ["node", temp_path, query],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=PROJECT_ROOT,
        )
        os.unlink(temp_path)
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return []
    except Exception:
        try:
            os.unlink(temp_path)
        except Exception:
            pass
        return []


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "/company/INFY/consolidated/"
    print(f"Testing scraper with: {path}")
    snapshot = fetch_compact_snapshot(path)
    print(f"Got snapshot with keys: {list(snapshot.keys())}")
    print(f"About: {snapshot.get('aboutText', '')[:100]}")
    ratios = snapshot.get("ratios", [])
    print(f"Ratios count: {len(ratios)}")
    for r in ratios[:5]:
        print(f"  {r['name']}: {r['value']}")
