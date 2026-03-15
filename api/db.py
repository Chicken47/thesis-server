"""
Neon Postgres client + DB helpers.

Uses psycopg2 with a ThreadedConnectionPool — safe for Flask's multi-threaded
worker model. All table operations live here; nothing else should talk to the DB.
"""

import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any

import psycopg2
import psycopg2.extras
import psycopg2.pool

from api.logger import get_logger

log = get_logger(__name__)

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        dsn = os.environ["DATABASE_URL"]
        _pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=dsn,
            cursor_factory=psycopg2.extras.RealDictCursor,
            # Keep connections alive so Neon doesn't drop them during long scrapes
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=5,
            keepalives_count=5,
        )
        log.debug("Neon connection pool initialised")
    return _pool


@contextmanager
def _conn():
    """Get a connection from the pool, commit on success, rollback on error.

    Pings the connection with SELECT 1 before yielding — this proactively
    detects stale SSL connections that Neon drops server-side (conn.closed
    is still 0 until you actually try to use the socket).
    """
    pool = _get_pool()
    conn = pool.getconn()

    # Replace if psycopg2 already knows it's dead
    if conn.closed:
        pool.putconn(conn, close=True)
        conn = pool.getconn()

    # Ping to catch SSL-dropped connections before the real query
    try:
        conn.cursor().execute("SELECT 1")
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = pool.getconn()  # fresh connection from pool

    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            pool.putconn(conn)
        except Exception:
            pass


def _serialize(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, Decimal):
        return float(v)
    return v


def _row(r) -> dict | None:
    if r is None:
        return None
    return {k: _serialize(v) for k, v in r.items()}



def _J(val: Any):
    """Wrap a Python object for insertion into a JSONB column."""
    return psycopg2.extras.Json(val)


# ── Stocks ────────────────────────────────────────────────────────────────────

def upsert_stock(ticker: str, name: str = "", screener_path: str = "") -> None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stocks (symbol, name, screener_path)
                VALUES (%s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    name          = EXCLUDED.name,
                    screener_path = EXCLUDED.screener_path,
                    updated_at    = NOW()
                """,
                (ticker.upper(), name or ticker.upper(), screener_path),
            )
    log.debug("Stock upserted", extra={"ticker": ticker.upper()})


# ── Analyses ──────────────────────────────────────────────────────────────────

def save_analysis(ticker: str, result: dict) -> str:
    """Insert an analysis row. Returns the new row's UUID as a string."""
    log.info(
        "Saving analysis",
        extra={"ticker": ticker.upper(), "verdict": result.get("verdict"), "conviction": result.get("conviction")},
    )
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO analyses (
                    stock_symbol, model_used, verdict, conviction,
                    conviction_breakdown, summary,
                    key_strengths, key_risks, red_flags, invalidation_triggers,
                    watch_for_next_quarter, news_sentiment, step_outputs,
                    sector, raw_response, buy_zones, market_vs_verdikt
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s
                ) RETURNING id
                """,
                (
                    ticker.upper(),
                    result.get("model_used"),
                    (result.get("verdict") or "").upper() or None,
                    result.get("conviction"),
                    _J(result.get("conviction_breakdown")),
                    result.get("summary"),
                    result.get("key_strengths", []),
                    result.get("key_risks", []),
                    result.get("red_flags", []),
                    result.get("invalidation_triggers", []),
                    result.get("watch_for_next_quarter"),
                    _J(result.get("news_sentiment")),
                    _J(result.get("step_outputs")),
                    result.get("sector"),
                    _J({
                        "text": result.get("raw_response", ""),
                        "rag_context_length": result.get("rag_context_length", 0),
                    }),
                    _J(result.get("buy_zones") or {}),
                    _J(result.get("market_vs_verdikt") or {}),
                ),
            )
            return str(cur.fetchone()["id"])


def get_analysis_by_id(analysis_id: str) -> dict | None:
    """Lightweight fetch used by job polling to return a result summary."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT verdict, conviction, conviction_breakdown, summary, sector FROM analyses WHERE id = %s",
                (analysis_id,),
            )
            return _row(cur.fetchone())


# ── Jobs ──────────────────────────────────────────────────────────────────────

def create_job(ticker: str | None, job_type: str) -> str:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (stock_symbol, job_type, status)
                VALUES (%s, %s, 'queued')
                RETURNING id
                """,
                (ticker.upper() if ticker else None, job_type),
            )
            return str(cur.fetchone()["id"])


def update_job(
    job_id: str,
    status: str,
    error_message: str | None = None,
    result_id: str | None = None,
) -> None:
    sets = ["status = %s"]
    vals: list = [status]

    if status == "running":
        sets.append("started_at = NOW()")
    if status in ("done", "failed"):
        sets.append("finished_at = NOW()")
    if error_message is not None:
        sets.append("error_message = %s")
        vals.append(error_message)
    if result_id is not None:
        sets.append("result_id = %s")
        vals.append(result_id)

    vals.append(job_id)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE id = %s",
                vals,
            )
    log.debug("Job updated", extra={"job_id": job_id[:8], "status": status})


def get_job(job_id: str) -> dict | None:
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM jobs WHERE id = %s", (job_id,))
            return _row(cur.fetchone())


# ── Screener data ──────────────────────────────────────────────────────────────


def _parse_num(val: str | None) -> float | None:
    if not val:
        return None
    try:
        return float(str(val).replace(',', '').replace('%', '').strip())
    except (ValueError, TypeError):
        return None


def _period_to_date(heading: str) -> str | None:
    """Convert 'Mar 2023' → '2023-03-31', 'Sep 2022' → '2022-09-30', etc."""
    month_map = {
        'Jan': ('01', '31'), 'Feb': ('02', '28'), 'Mar': ('03', '31'),
        'Apr': ('04', '30'), 'May': ('05', '31'), 'Jun': ('06', '30'),
        'Jul': ('07', '31'), 'Aug': ('08', '31'), 'Sep': ('09', '30'),
        'Oct': ('10', '31'), 'Nov': ('11', '30'), 'Dec': ('12', '31'),
    }
    parts = str(heading).strip().split()
    if len(parts) == 2 and parts[0] in month_map:
        m, day = month_map[parts[0]]
        return f"{parts[1]}-{m}-{day}"
    return None


def _row_vals(table: dict, category: str) -> list[str]:
    """Return the values list for a matching category row (case-insensitive prefix match)."""
    cat_lower = category.lower()
    for row in table.get('values', []):
        if str(row.get('category', '')).lower().strip().startswith(cat_lower):
            return row.get('values', [])
    return []


def save_full_screener_data(ticker: str, raw: dict) -> None:
    """
    Parse raw Screener.in scrape and populate:
      - stocks.screener_data (JSONB cache)
      - stock_snapshots (one row per scrape)
      - stock_financials (one row per annual period)
      - peer_comparisons (one row per scrape)
      - scrape_log
    """
    upper = ticker.upper()

    # ── 1. JSONB cache on stocks row ─────────────────────────────────────
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE stocks SET screener_data = %s, updated_at = NOW() WHERE symbol = %s",
                (_J(raw), upper),
            )

    # ── 2. Build ratio lookup ─────────────────────────────────────────────
    ratio_map: dict[str, str] = {}
    for r in raw.get('ratios', []):
        ratio_map[r.get('name', '')] = r.get('value', '')

    high_low = ratio_map.get('High / Low', '')
    week_52_high = week_52_low = None
    if '/' in high_low:
        parts = high_low.split('/')
        week_52_high = _parse_num(parts[0])
        week_52_low  = _parse_num(parts[1])

    shareholding = raw.get('shareholding', [])
    sh_map: dict[str, float] = {}
    for row in shareholding:
        cat = str(row.get('category', '')).replace('\u00a0', '').replace('+', '').strip().lower()
        keys = [k for k in row if k != 'category']
        if keys:
            val = _parse_num(str(row.get(keys[-1], '') or '').replace('%', ''))
            if val is not None:
                sh_map[cat] = val

    # ── 3. stock_snapshots ────────────────────────────────────────────────
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stock_snapshots (
                    stock_symbol, market_cap, current_price,
                    week_52_high, week_52_low,
                    pe_ratio, pb_ratio, book_value, dividend_yield,
                    roce, roe, face_value,
                    promoter_holding, fii_holding, dii_holding, public_holding,
                    promoter_pledge,
                    pros, cons, about_text, raw_data
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    upper,
                    _parse_num(ratio_map.get('Market Cap')),
                    _parse_num(ratio_map.get('Current Price')),
                    week_52_high, week_52_low,
                    _parse_num(ratio_map.get('Stock P/E')),
                    _parse_num(ratio_map.get('Price to Book')),
                    _parse_num(ratio_map.get('Book Value')),
                    _parse_num(ratio_map.get('Dividend Yield')),
                    _parse_num(ratio_map.get('ROCE')),
                    _parse_num(ratio_map.get('ROE')),
                    _parse_num(ratio_map.get('Face Value')),
                    sh_map.get('promoters'),
                    sh_map.get('fils') or sh_map.get('fii') or sh_map.get('foreign institutions'),
                    sh_map.get('diis') or sh_map.get('dii') or sh_map.get('domestic institutions'),
                    sh_map.get('public'),
                    _parse_num(ratio_map.get('Promoter pledge')),
                    raw.get('prosConsData', {}).get('pros', []),
                    raw.get('prosConsData', {}).get('cons', []),
                    raw.get('aboutText', ''),
                    _J(raw.get('ratios', [])),
                ),
            )
    log.debug("stock_snapshots inserted", extra={"ticker": upper})

    # ── 4. stock_financials (annual periods) ─────────────────────────────
    annual_pl = raw.get('annualPL') or {}
    bs        = raw.get('balanceSheet') or {}
    cf        = raw.get('cashFlows') or {}
    headings  = annual_pl.get('headings', [])

    pl_revenue  = _row_vals(annual_pl, 'sales')
    pl_expenses = _row_vals(annual_pl, 'expenses')
    pl_op       = _row_vals(annual_pl, 'operating profit')
    pl_opm      = _row_vals(annual_pl, 'opm')
    pl_oi       = _row_vals(annual_pl, 'other income')
    pl_int      = _row_vals(annual_pl, 'interest')
    pl_dep      = _row_vals(annual_pl, 'depreciation')
    pl_pbt      = _row_vals(annual_pl, 'profit before tax')
    pl_tax      = _row_vals(annual_pl, 'tax')
    pl_np       = _row_vals(annual_pl, 'net profit')
    pl_eps      = _row_vals(annual_pl, 'eps')

    bs_borrow   = _row_vals(bs, 'borrowings')
    bs_res      = _row_vals(bs, 'reserves')
    bs_eq       = _row_vals(bs, 'equity capital')
    bs_inv      = _row_vals(bs, 'investments')
    bs_assets   = _row_vals(bs, 'total assets')

    cf_ocf      = _row_vals(cf, 'cash from operating')
    cf_icf      = _row_vals(cf, 'cash from investing')
    cf_fcf      = _row_vals(cf, 'cash from financing')
    cf_net      = _row_vals(cf, 'net cash')

    def _at(lst: list, i: int) -> float | None:
        return _parse_num(lst[i]) if i < len(lst) else None

    rows_to_insert = []
    for i, heading in enumerate(headings):
        period_end = _period_to_date(heading)
        if not period_end:
            continue
        rows_to_insert.append((
            upper, period_end, 'annual',
            _at(pl_revenue, i), _at(pl_expenses, i), _at(pl_op, i),
            _at(pl_opm, i), _at(pl_oi, i), _at(pl_int, i),
            _at(pl_dep, i), _at(pl_pbt, i), _at(pl_tax, i),
            _at(pl_np, i), _at(pl_eps, i),
            _at(bs_borrow, i), _at(bs_res, i), _at(bs_eq, i),
            _at(bs_inv, i), _at(bs_assets, i),
            _at(cf_ocf, i), _at(cf_icf, i), _at(cf_fcf, i), _at(cf_net, i),
        ))

    if rows_to_insert:
        with _conn() as conn:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO stock_financials (
                        stock_symbol, period_end, period_type,
                        revenue, expenses, operating_profit,
                        opm_percent, other_income, interest,
                        depreciation, pbt, tax_percent,
                        net_profit, eps,
                        borrowings, reserves, equity_capital,
                        investments, total_assets,
                        ocf, icf, fcf, net_cash_flow
                    ) VALUES %s
                    ON CONFLICT DO NOTHING
                    """,
                    rows_to_insert,
                )
        log.debug("stock_financials inserted", extra={"ticker": upper, "periods": len(rows_to_insert)})

    # ── 5. peer_comparisons ───────────────────────────────────────────────
    peers = raw.get('peerComparison', {}).get('peers', [])
    if peers:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO peer_comparisons (stock_symbol, peers) VALUES (%s, %s)",
                    (upper, _J(peers)),
                )
        log.debug("peer_comparisons inserted", extra={"ticker": upper})

    # ── 6. scrape_log ─────────────────────────────────────────────────────
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO scrape_log (stock_symbol, scrape_type, status, records_scraped)
                VALUES (%s, 'screener_full', 'success', %s)
                """,
                (upper, len(rows_to_insert)),
            )
    log.debug("scrape_log inserted", extra={"ticker": upper})

    # ── 7. Stock checklist ────────────────────────────────────────────────────
    upsert_checklist_from_raw(ticker, raw)


# ── Stock Checklist ────────────────────────────────────────────────────────────

def upsert_checklist_from_raw(ticker: str, raw: dict) -> None:
    """Populate stock_checklist boolean flags from a raw screener scrape."""
    upper = ticker.upper()

    def _has_table(key: str) -> bool:
        t = raw.get(key) or {}
        return bool(t.get('headings'))

    docs = raw.get('documents', [])
    doc_cats = {d.get('category') for d in docs}

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stock_checklist (
                    stock_symbol,
                    has_pnl, has_quarterly, has_balance_sheet, has_cash_flow,
                    has_shareholding, has_ratios, has_peers,
                    has_news, has_concalls, has_announcements, has_annual_reports,
                    screener_scraped_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (stock_symbol) DO UPDATE SET
                    has_pnl             = EXCLUDED.has_pnl,
                    has_quarterly       = EXCLUDED.has_quarterly,
                    has_balance_sheet   = EXCLUDED.has_balance_sheet,
                    has_cash_flow       = EXCLUDED.has_cash_flow,
                    has_shareholding    = EXCLUDED.has_shareholding,
                    has_ratios          = EXCLUDED.has_ratios,
                    has_peers           = EXCLUDED.has_peers,
                    has_news            = EXCLUDED.has_news,
                    has_concalls        = EXCLUDED.has_concalls,
                    has_announcements   = EXCLUDED.has_announcements,
                    has_annual_reports  = EXCLUDED.has_annual_reports,
                    screener_scraped_at = EXCLUDED.screener_scraped_at,
                    updated_at          = EXCLUDED.updated_at
                """,
                (
                    upper,
                    _has_table('annualPL'),
                    bool((raw.get('quartersData') or {}).get('headings')),
                    _has_table('balanceSheet'),
                    _has_table('cashFlows'),
                    bool(raw.get('shareholding')),
                    _has_table('ratiosHistory'),
                    bool((raw.get('peerComparison') or {}).get('peers')),
                    bool(raw.get('news')),
                    'concall' in doc_cats,
                    'announcement' in doc_cats,
                    'annual_report' in doc_cats,
                ),
            )
    log.debug("stock_checklist upserted from raw", extra={"ticker": upper})


def update_checklist_rag_and_prompt(ticker: str, rag_successful: bool, prompt: str | None = None) -> None:
    """Update RAG status and store the generated prompt in stock_checklist."""
    upper = ticker.upper()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO stock_checklist (
                    stock_symbol, rag_successful, latest_prompt, prompt_generated_at, updated_at
                ) VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (stock_symbol) DO UPDATE SET
                    rag_successful      = EXCLUDED.rag_successful,
                    latest_prompt       = COALESCE(EXCLUDED.latest_prompt, stock_checklist.latest_prompt),
                    prompt_generated_at = CASE
                        WHEN EXCLUDED.latest_prompt IS NOT NULL
                        THEN EXCLUDED.prompt_generated_at
                        ELSE stock_checklist.prompt_generated_at
                    END,
                    updated_at          = EXCLUDED.updated_at
                """,
                (upper, rag_successful, prompt),
            )
    log.debug("stock_checklist rag/prompt updated", extra={"ticker": upper})
