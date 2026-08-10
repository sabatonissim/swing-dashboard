"""
API Server - connects the dashboard frontend to the real database
====================================================================
Run with:
    pip install --break-system-packages fastapi uvicorn
    uvicorn api_server:app --reload --port 8000

Endpoints:
    GET /api/stocks              -> latest flagged stocks (scanned_stocks)
    GET /api/macro-news          -> latest macro news items (macro_news)
    GET /api/ui-strings?lang=he  -> UI text dictionary for a given language
    GET /api/market-movers       -> whole-US-market today's biggest movers
    GET /api/sector-performance  -> the 11 SPDR sector ETFs: 1D table + normalized period chart

CORS is open for local development. Lock this down (allow_origins) before
deploying publicly.
"""

import json
import math
import os
import time
import io
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from datetime import date, datetime as dt
from typing import List, Optional

import pandas as pd
import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf
import psycopg2
import psycopg2.extras

DB_URL = os.environ.get("SWING_DB_PATH") or os.environ.get("DATABASE_URL")

app = FastAPI(title="Swing Desk API")

# Allowed frontend origins for CORS. Set via the ALLOWED_ORIGINS env var
# (comma-separated) on whatever host runs this — e.g.
#   ALLOWED_ORIGINS=https://your-new-domain.com,https://your-frontend.vercel.app
# so moving to a new domain or a different hosting provider is a config
# change, not a code change. Falls back to the current known origins if the
# env var isn't set (keeps working as-is with no setup needed).
_default_origins = "https://swing-desk-tau.vercel.app,http://localhost:8000,http://localhost:3000"
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", _default_origins).split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


def get_conn():
    conn = psycopg2.connect(DB_URL)
    return conn


@contextmanager
def db_cursor(dict_cursor: bool = False):
    """Guarantees the connection is always closed, even if the query
    raises. Every endpoint below used to open a connection and close it
    manually at the end of the function — meaning ANY exception mid-query
    (a bad value, a transient network blip to Postgres, etc.) skipped the
    close() call and leaked the connection. With the dashboard polling
    every 20-60s all day, that leak very plausibly accumulates until the
    connection pool is exhausted by evening — a strong candidate for why
    the evening scan (which also needs its own DB connection) has been
    failing while the morning scan works fine on a fresh/idle pool."""
    conn = get_conn()
    cur = None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if dict_cursor else conn.cursor()
        yield conn, cur
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        try:
            conn.close()
        except Exception:
            pass


def init_db():
    """Create tables if they don't exist yet."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanned_stocks (
            id SERIAL PRIMARY KEY,
            ticker TEXT NOT NULL,
            trigger_text_he TEXT,
            trigger_text_en TEXT,
            swing_score INTEGER,
            entry_price REAL,
            support_level REAL,
            resistance_targets TEXT,
            social_volume_spike_pct REAL,
            ai_summary_he TEXT,
            ai_summary_en TEXT,
            market_cap REAL,
            avg_volume_20d REAL,
            breakout_volume_pct REAL,
            exchange TEXT,
            change_pct REAL,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS macro_news (
            id SERIAL PRIMARY KEY,
            category_tag TEXT NOT NULL,
            summary_he TEXT,
            summary_en TEXT,
            impact_level TEXT,
            source_url TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS ui_strings (
            string_key TEXT PRIMARY KEY,
            he TEXT NOT NULL,
            en TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS analytics_events (
            id SERIAL PRIMARY KEY,
            event_type TEXT NOT NULL,
            entity_id TEXT,
            session_id TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS universe_movers (
            ticker TEXT PRIMARY KEY,
            change_pct REAL,
            close_price REAL,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker TEXT PRIMARY KEY,
            added_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    # Patches an already-deployed table that predates this column
    # (CREATE TABLE IF NOT EXISTS above is a no-op once the table exists).
    cur.execute("ALTER TABLE universe_movers ADD COLUMN IF NOT EXISTS in_sp500 BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE universe_movers ADD COLUMN IF NOT EXISTS in_nasdaq100 BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS change_pct REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS sma50 REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS sma150 REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS sma200 REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS trend_stage TEXT;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS atr_value REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS atr_pct REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS rs_rating INTEGER;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS pattern_type TEXT;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS forward_return_10d REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS forward_return_20d REAL;")
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS days_to_earnings INTEGER;")
    conn.commit()
    cur.close()
    conn.close()


# Initialize DB tables on startup
try:
    init_db()
except Exception as e:
    print(f"DB init warning: {e}")


@app.get("/api/stocks")
def get_stocks(limit: int = Query(default=25, le=100)):
    with db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT * FROM scanned_stocks ORDER BY timestamp DESC LIMIT %s", (limit,))
        rows = cur.fetchall()
    result = []
    for r in rows:
        d = dict(r)
        try:
            d["resistance_targets"] = json.loads(d["resistance_targets"] or "[]")
        except (json.JSONDecodeError, TypeError):
            d["resistance_targets"] = []
        result.append(d)
    return result


@app.get("/api/backtest/patterns")
def get_backtest_patterns():
    """Distinct pattern types that have at least one resolved (10d) signal —
    for populating the pattern picker on the backtest page."""
    with db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            SELECT DISTINCT pattern_type FROM scanned_stocks
            WHERE pattern_type IS NOT NULL AND forward_return_10d IS NOT NULL
            ORDER BY pattern_type
            """
        )
        rows = cur.fetchall()
    return [r["pattern_type"] for r in rows]


@app.get("/api/backtest")
def get_backtest(
    pattern: str = Query(default="all"),
    horizon: int = Query(default=10),
):
    """Backtest view over our own scan history: an equity curve (what a
    $1 stake compounded through every signal, in chronological order, would
    have grown to), max drawdown, a breakdown by RS Rating bucket, and the
    full list of resolved signals behind the numbers — so a win-rate % isn't
    just a number to trust blindly, it's traceable back to real signals.

    horizon must be 10 or 20 (trading days). Query param arrives as a
    plain int; FastAPI's pattern= on an int doesn't apply, so validate
    manually and fail clearly rather than silently coercing."""
    if horizon not in (10, 20):
        raise HTTPException(status_code=400, detail="horizon must be 10 or 20")
    return_col = f"forward_return_{horizon}d"

    with db_cursor(dict_cursor=True) as (conn, cur):
        if pattern and pattern != "all":
            cur.execute(
                f"""
                SELECT ticker, timestamp, pattern_type, rs_rating, entry_price, {return_col} AS fwd_return
                FROM scanned_stocks
                WHERE pattern_type = %s AND {return_col} IS NOT NULL
                ORDER BY timestamp ASC
                """,
                (pattern,),
            )
        else:
            cur.execute(
                f"""
                SELECT ticker, timestamp, pattern_type, rs_rating, entry_price, {return_col} AS fwd_return
                FROM scanned_stocks
                WHERE pattern_type IS NOT NULL AND {return_col} IS NOT NULL
                ORDER BY timestamp ASC
                """
            )
        rows = cur.fetchall()

    if not rows:
        return {
            "pattern": pattern, "horizon": horizon, "n": 0,
            "win_rate": None, "avg_return": None, "max_drawdown": None,
            "equity_curve": [], "rs_breakdown": [], "signals": [],
        }

    # Equity curve: treat every signal as a full-stake, non-overlapping trade
    # taken in chronological order (position closed before the next opens).
    # This is a simplification — real trades would overlap — but it's the
    # standard, honest way to show "did this pattern make money over time"
    # without pretending we know real position sizing or concurrency.
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    equity_curve = []
    wins = 0
    total_return = 0.0

    for r in rows:
        pct = float(r["fwd_return"])
        equity *= (1 + pct / 100)
        peak = max(peak, equity)
        drawdown = (equity - peak) / peak * 100
        max_dd = min(max_dd, drawdown)
        equity_curve.append({
            "date": r["timestamp"].strftime("%Y-%m-%d"),
            "ticker": r["ticker"],
            "equity": round(equity, 4),
        })
        if pct > 0:
            wins += 1
        total_return += pct

    n = len(rows)
    win_rate = round(100 * wins / n, 1)
    avg_return = round(total_return / n, 2)

    # RS Rating breakdown — does this pattern work better on stronger stocks?
    bucket_rows = {"80+": [], "50-79": [], "under_50": []}
    for r in rows:
        rs = r["rs_rating"]
        pct = float(r["fwd_return"])
        if rs is None:
            continue
        if rs >= 80:
            bucket_rows["80+"].append(pct)
        elif rs >= 50:
            bucket_rows["50-79"].append(pct)
        else:
            bucket_rows["under_50"].append(pct)

    rs_breakdown = []
    for label, returns in bucket_rows.items():
        if not returns:
            continue
        wins_b = sum(1 for p in returns if p > 0)
        rs_breakdown.append({
            "rs_bucket": label,
            "n": len(returns),
            "win_rate": round(100 * wins_b / len(returns), 1),
            "avg_return": round(sum(returns) / len(returns), 2),
        })

    signals = [
        {
            "ticker": r["ticker"],
            "date": r["timestamp"].strftime("%Y-%m-%d"),
            "pattern_type": r["pattern_type"],
            "rs_rating": r["rs_rating"],
            "entry_price": r["entry_price"],
            "return": round(float(r["fwd_return"]), 2),
        }
        for r in rows
    ]
    # Most recent first for the signal table — chronological order matters
    # for the equity curve above, but a human scanning the list wants newest first.
    signals.reverse()

    return {
        "pattern": pattern,
        "horizon": horizon,
        "n": n,
        "win_rate": win_rate,
        "avg_return": avg_return,
        "max_drawdown": round(max_dd, 2),
        "equity_curve": equity_curve,
        "rs_breakdown": rs_breakdown,
        "signals": signals,
    }


@app.get("/api/pattern-stats")
def get_pattern_stats():
    """Win-rate and average forward return per pattern type, computed from
    our own scan history — only counts signals old enough to have a known
    10-day outcome (see backfill_forward_returns in pipeline_a_scanner.py).
    Patterns with fewer than 5 resolved signals are excluded — too small a
    sample to mean anything yet, and will fill in naturally as more days
    of scan history accumulate."""
    with db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            SELECT
                pattern_type,
                COUNT(*) AS n,
                ROUND(100.0 * COUNT(*) FILTER (WHERE forward_return_10d > 0) / COUNT(*), 1) AS win_rate_10d,
                ROUND(AVG(forward_return_10d)::numeric, 2) AS avg_return_10d,
                ROUND(100.0 * COUNT(*) FILTER (WHERE forward_return_20d > 0) / NULLIF(COUNT(*) FILTER (WHERE forward_return_20d IS NOT NULL), 0), 1) AS win_rate_20d,
                ROUND(AVG(forward_return_20d)::numeric, 2) AS avg_return_20d
            FROM scanned_stocks
            WHERE pattern_type IS NOT NULL AND forward_return_10d IS NOT NULL
            GROUP BY pattern_type
            HAVING COUNT(*) >= 5
            ORDER BY win_rate_10d DESC NULLS LAST
            """
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


@app.get("/api/macro-news")
def get_macro_news(limit: int = Query(default=25, le=100)):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Critical-impact stories (Fed rate decisions, wars, etc.) stay relevant
    # for days, not just until the next batch of routine updates rolls in
    # 30 minutes later. Reserve a slice of the response for recent Critical
    # items specifically, so they don't get pushed out purely by the volume
    # of newer but less important "High" items — those keep rotating
    # normally with the per-category diversity cap below.
    critical_slots = max(3, limit // 5)
    cur.execute(
        """
        SELECT * FROM macro_news
        WHERE impact_level = 'Critical'
          AND timestamp > NOW() - INTERVAL '72 hours'
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (critical_slots,),
    )
    critical_rows = cur.fetchall()
    critical_ids = [r["id"] for r in critical_rows] or [0]  # guard against an empty array in the NOT IN below

    remaining = max(limit - len(critical_rows), 5)
    per_category_cap = max(3, remaining // 5)
    cur.execute(
        """
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (PARTITION BY category_tag ORDER BY timestamp DESC) AS rn
            FROM macro_news
            WHERE id != ALL(%s)
        ) ranked
        WHERE rn <= %s
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (critical_ids, per_category_cap, remaining),
    )
    other_rows = cur.fetchall()
    cur.close()
    conn.close()

    combined = list(critical_rows) + list(other_rows)
    combined.sort(key=lambda r: r["timestamp"], reverse=True)
    return [{k: v for k, v in dict(r).items() if k != "rn"} for r in combined]


@app.get("/api/macro-news/search")
def search_macro_news(
    q: str = Query(default="", max_length=100),
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=60, le=200),
):
    """Full search across macro_news, independent of the diversity/rotation
    algorithm the main feed (/api/macro-news) uses. That endpoint only ever
    returns ~25 recent items so the feed stays fresh and balanced across
    categories — great for browsing, but it means a search box wired to it
    can only ever search the last half hour or so of news, which is why
    older-but-relevant stories seemed to "disappear". This endpoint instead
    queries the full macro_news table directly over a wider window (default
    7 days), so search actually has something to find."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    query = q.strip()
    if not query:
        cur.execute(
            """
            SELECT * FROM macro_news
            WHERE timestamp > NOW() - make_interval(days => %s)
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (days, limit),
        )
    else:
        like_pattern = f"%{query}%"
        cur.execute(
            """
            SELECT * FROM macro_news
            WHERE timestamp > NOW() - make_interval(days => %s)
              AND (
                summary_he ILIKE %s OR
                summary_en ILIKE %s OR
                category_tag ILIKE %s OR
                hook_he ILIKE %s
              )
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (days, like_pattern, like_pattern, like_pattern, like_pattern, limit),
        )

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/ui-strings")
def get_ui_strings(lang: str = Query(default="he", pattern="^(he|en)$")):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT string_key, he, en FROM ui_strings")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["string_key"]: r[lang] for r in rows}


@app.get("/api/health")
def health():
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@app.get("/api/stock-news/{ticker}")
def stock_news(ticker: str, limit: int = 6):
    """Recent headlines for a specific ticker, via yfinance's built-in feed."""
    ticker = ticker.upper().strip()
    try:
        raw_items = yf.Ticker(ticker).news or []
    except Exception:
        raise HTTPException(status_code=502, detail="שגיאה בשליפת חדשות")

    items = []
    for it in raw_items[:limit]:
        content = it.get("content", it)  # newer yfinance versions nest under "content"
        title = content.get("title") or it.get("title")
        link = (content.get("canonicalUrl") or {}).get("url") or it.get("link")
        publisher = (content.get("provider") or {}).get("displayName") or it.get("publisher")
        published = content.get("pubDate") or it.get("providerPublishTime")
        if title and link:
            items.append({"title": title, "link": link, "publisher": publisher, "published": published})
    return items


# ------------------------------------------------------------------
# Free-text stock search: works for ANY ticker, not just the ones the
# nightly scanner flagged. Pulls live data directly from Yahoo Finance
# on demand - no OpenAI call here, so it's instant and free to run.
# ------------------------------------------------------------------

# yfinance's `sector` field uses GICS sector names, which don't exactly
# match the "Cons. Discretionary"-style labels used for the SECTOR_ETFS
# dict above (that dict is for display; this one is for matching a stock's
# reported sector to the right SPDR ETF ticker).
YF_SECTOR_TO_ETF = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
    "Utilities": "XLU",
}


# Ticker -> GICS sector, sourced from the same plain-CSV S&P 500 list the
# scanner already uses (see pipeline_a_scanner.py's fetch_sp500_tickers —
# same reasoning applies: no HTML-parsing dependency, and critically, no
# reliance on yfinance's .info, which this deployment's cloud IP gets
# rate-limited/blocked on far more often than a plain CSV download.
# Cached for a day since sector/constituent changes are rare.
_sp500_sector_cache = {"data": {}, "ts": 0}
SP500_SECTOR_CACHE_TTL = 86400  # 24 hours


def get_sp500_sector_map() -> dict:
    now = time.time()
    if _sp500_sector_cache["data"] and (now - _sp500_sector_cache["ts"]) < SP500_SECTOR_CACHE_TTL:
        return _sp500_sector_cache["data"]
    try:
        resp = requests.get(
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
            timeout=15,
        )
        resp.raise_for_status()
        table = pd.read_csv(io.StringIO(resp.text))
        sector_col = "Sector" if "Sector" in table.columns else "GICS Sector"
        mapping = {
            str(row["Symbol"]).strip().replace(".", "-"): str(row[sector_col]).strip()
            for _, row in table.iterrows()
            if pd.notna(row.get(sector_col))
        }
        _sp500_sector_cache["data"] = mapping
        _sp500_sector_cache["ts"] = now
        return mapping
    except Exception as e:
        print(f"[warn] failed to fetch S&P 500 sector list: {e}")
        return _sp500_sector_cache["data"]  # serve last-good if we have it, else empty


@app.get("/api/sector-comparison/{ticker}")
def get_sector_comparison(ticker: str):
    """How has this stock performed vs. the SPDR ETF for its own sector,
    over the same 1-year window? Answers 'is this stock actually
    outperforming its peers, or just going up with the tide.'"""
    ticker = ticker.upper().strip()
    try:
        tk = yf.Ticker(ticker)
        stock_hist = tk.history(period="1y", interval="1d")
    except Exception:
        raise HTTPException(status_code=502, detail="שגיאה בשליפת הנתונים")

    if stock_hist.empty:
        raise HTTPException(status_code=404, detail=f"הטיקר {ticker} לא נמצא")

    # Primary source: the CSV sector map (reliable, no .info dependency).
    # Only covers S&P 500 names, so for anything else fall back to .info —
    # better than nothing for smaller tickers, even if it's less reliable.
    sector_name = get_sp500_sector_map().get(ticker)
    if not sector_name:
        try:
            sector_name = (tk.info or {}).get("sector")
        except Exception as e:
            print(f"[warn] sector-comparison: .info fallback failed for {ticker}: {e}")

    etf_ticker = YF_SECTOR_TO_ETF.get(sector_name)

    stock_return = round((float(stock_hist["Close"].iloc[-1]) / float(stock_hist["Close"].iloc[0]) - 1) * 100, 2)

    etf_return = None
    if etf_ticker:
        try:
            etf_hist = yf.Ticker(etf_ticker).history(period="1y", interval="1d")
            if not etf_hist.empty:
                etf_return = round((float(etf_hist["Close"].iloc[-1]) / float(etf_hist["Close"].iloc[0]) - 1) * 100, 2)
        except Exception as e:
            print(f"[warn] sector-comparison: ETF fetch failed for {etf_ticker}: {e}")

    return {
        "ticker": ticker,
        "sector": sector_name,
        "sector_etf": etf_ticker,
        "stock_return_1y": stock_return,
        "sector_return_1y": etf_return,
        "outperformance": round(stock_return - etf_return, 2) if etf_return is not None else None,
    }


@app.get("/api/signal-history/{ticker}")
def get_signal_history(ticker: str, limit: int = Query(default=20, le=100)):
    """Every past scan signal for one specific ticker — the pattern that
    fired, when, and (once resolved) how it played out. Powers the
    'has this stock done this before?' section of the stock deep-dive page."""
    ticker = ticker.upper().strip()
    with db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            """
            SELECT ticker, timestamp, pattern_type, swing_score, rs_rating,
                   entry_price, forward_return_10d, forward_return_20d
            FROM scanned_stocks
            WHERE ticker = %s AND pattern_type IS NOT NULL
            ORDER BY timestamp DESC
            LIMIT %s
            """,
            (ticker, limit),
        )
        rows = cur.fetchall()
    return [
        {
            "date": r["timestamp"].strftime("%Y-%m-%d"),
            "pattern_type": r["pattern_type"],
            "swing_score": r["swing_score"],
            "rs_rating": r["rs_rating"],
            "entry_price": r["entry_price"],
            "return_10d": r["forward_return_10d"],
            "return_20d": r["forward_return_20d"],
        }
        for r in rows
    ]


@app.get("/api/lookup/{ticker}")
def lookup_stock(ticker: str):
    ticker = ticker.upper().strip()

    # .history() is the reliable call from this deployment — Yahoo Finance
    # appears to rate-limit or block .info more aggressively from cloud
    # hosting IPs (Render/Railway/etc.) than from a home connection, which
    # was already worked around elsewhere in this codebase (see
    # save_universe_movers_fallback's docstring). So: get price/volume from
    # .history() first since that's dependable, and treat .info as optional
    # enrichment — if it fails, the endpoint still returns real price data
    # instead of a blanket 404/502 for every ticker.
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period="6mo", interval="1d")
    except Exception:
        raise HTTPException(status_code=502, detail="שגיאה בשליפת הנתונים, נסה שוב")

    if hist.empty:
        raise HTTPException(status_code=404, detail=f"הטיקר {ticker} לא נמצא")

    price = float(hist["Close"].iloc[-1])
    prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
    change_pct = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

    info = {}
    try:
        info = tk.info or {}
    except Exception as e:
        print(f"[warn] lookup: .info failed for {ticker} (continuing with history-only data): {e}")

    # Prefer .info's price if it's actually there (more current intraday
    # value) but never let a missing/failed .info block the response.
    if info.get("currentPrice") or info.get("regularMarketPrice"):
        price = info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close_info = info.get("previousClose")
        if prev_close_info:
            change_pct = round(((price - prev_close_info) / prev_close_info) * 100, 2)

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "price": round(float(price), 2),
        "change_pct": change_pct,
        "market_cap": info.get("marketCap"),
        "avg_volume_20d": round(float(hist["Volume"].tail(20).mean()), 0) if len(hist) >= 20 else None,
        "week52_high": info.get("fiftyTwoWeekHigh") or round(float(hist["Close"].max()), 2),
        "week52_low": info.get("fiftyTwoWeekLow") or round(float(hist["Close"].min()), 2),
        "exchange": info.get("exchange"),
        "sector": info.get("sector"),
        "pe_ratio": info.get("trailingPE"),
    }


# ------------------------------------------------------------------
# Personal watchlist — a plain list of tickers the user chose to track,
# independent of whatever the scanner happens to flag that day. Single
# shared list (this is a personal dashboard, not multi-user), so no auth
# needed here — just persisted tickers.
# ------------------------------------------------------------------

def _compute_atr_pct(hist: pd.DataFrame, period: int = 14) -> Optional[float]:
    """Same ATR math as the scanner, duplicated here (rather than imported)
    so api_server.py doesn't depend on pipeline_a_scanner.py's module — the
    two are deployed/run independently."""
    if len(hist) < period + 1:
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    atr_val = float(atr.iloc[-1])
    price = float(close.iloc[-1])
    if price <= 0:
        return None
    return round(atr_val / price * 100, 2)


@app.get("/api/watchlist")
def get_watchlist():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT ticker FROM watchlist ORDER BY added_at DESC")
    tickers = [r["ticker"] for r in cur.fetchall()]
    cur.close()
    conn.close()

    if not tickers:
        return []

    # Price/change/ATR all come from .history() — the endpoint already
    # proven reliable from Railway elsewhere in this file — rather than
    # fast_info/quoteSummary-style calls. Company name has no way around
    # needing .info; that call is wrapped separately so a name lookup
    # failure never costs us the price/ATR data for that same ticker.
    out = []
    for t in tickers:
        row = {"ticker": t, "name": t, "price": None, "change_pct": None, "atr_pct": None}
        try:
            tk = yf.Ticker(t)
            hist = tk.history(period="1mo", interval="1d")
            closes = hist["Close"].dropna()
            if len(closes) >= 2:
                price = float(closes.iloc[-1])
                prev = float(closes.iloc[-2])
                row["price"] = round(price, 2)
                row["change_pct"] = round((price / prev - 1) * 100, 2)
            atr_pct = _compute_atr_pct(hist)
            if atr_pct is not None:
                row["atr_pct"] = atr_pct
            try:
                name = tk.info.get("longName") or tk.info.get("shortName")
                if name:
                    row["name"] = name
            except Exception as e:
                print(f"[warn] watchlist name fetch failed for {t}: {e}")
        except Exception as e:
            print(f"[warn] watchlist history fetch failed for {t}: {e}")
        out.append(row)

    return out


class WatchlistTicker(BaseModel):
    ticker: str


@app.post("/api/watchlist")
def add_to_watchlist(body: WatchlistTicker):
    ticker = body.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO watchlist (ticker) VALUES (%s) ON CONFLICT (ticker) DO NOTHING",
        (ticker,),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"ticker": ticker, "added": True}


@app.delete("/api/watchlist/{ticker}")
def remove_from_watchlist(ticker: str):
    ticker = ticker.upper().strip()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM watchlist WHERE ticker = %s", (ticker,))
    conn.commit()
    cur.close()
    conn.close()
    return {"ticker": ticker, "removed": True}


# ------------------------------------------------------------------
# Market-wide movers: genuine "biggest movers in the US market today",
# NOT limited to the ~90 tickers our own scanner tracks. Uses yfinance's
# built-in wrapper around Yahoo Finance's public predefined screeners
# (day_gainers / day_losers) — still free, no paid data provider, but
# covers the whole market instead of just our tracked universe.
#
# Cached in-process for a couple of minutes so a burst of dashboard
# polls doesn't hammer Yahoo's screener endpoint or trip rate limits.
# ------------------------------------------------------------------

MOVERS_CACHE_TTL_SEC = 180
_movers_cache = {"data": None, "ts": 0.0}


def _parse_screen_quotes(screen_result: Optional[dict]) -> List[dict]:
    if not screen_result:
        return []
    quotes = screen_result.get("quotes", []) or []
    out = []
    for q in quotes:
        pct = q.get("regularMarketChangePercent")
        price = q.get("regularMarketPrice")
        if pct is None or price is None:
            continue
        out.append({
            "ticker": q.get("symbol"),
            "name": q.get("shortName") or q.get("longName") or q.get("symbol"),
            "price": round(float(price), 2),
            "change_pct": round(float(pct), 2),
            "volume": q.get("regularMarketVolume"),
        })
    return out


def _fetch_market_movers(limit: int = 50) -> List[dict]:
    """Pulls Yahoo's 'day_gainers' and 'day_losers' predefined screeners
    (whole US market, not our tracked universe) and merges them, ranked
    by the size of the move. Requires a reasonably recent yfinance
    version (screen() was added in 0.2.40+)."""
    gainers = losers = None
    try:
        gainers = yf.screen("day_gainers", count=limit)
    except Exception as e:
        print(f"[warn] day_gainers screen failed: {e}")
    try:
        losers = yf.screen("day_losers", count=limit)
    except Exception as e:
        print(f"[warn] day_losers screen failed: {e}")

    combined = _parse_screen_quotes(gainers) + _parse_screen_quotes(losers)
    combined.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    return combined[:limit]


MOVERS_MIN_QUALITY = 5  # a fetch returning fewer than this is treated as a degraded/
                        # partial Yahoo response (e.g. one screener got throttled)
                        # rather than a genuine "only 1 mover today" result


def _fetch_fallback_movers_from_db(limit: int) -> List[dict]:
    """Reliable fallback: movers computed by the scanner itself (our own
    ~90-ticker universe) using .history(), the endpoint proven to work
    from this deployment. Narrower coverage than the whole-market
    screener, but it actually returns data when Yahoo blocks/throttles
    the screener endpoint (which uses a different, stricter code path)."""
    try:
        conn = get_conn()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            """
            SELECT ticker, change_pct, close_price
            FROM universe_movers
            WHERE change_pct IS NOT NULL
            ORDER BY ABS(change_pct) DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"ticker": r["ticker"], "change_pct": float(r["change_pct"]),
                  "price": float(r["close_price"]) if r["close_price"] is not None else None,
                  "name": ""} for r in rows]
    except Exception as e:
        print(f"[warn] universe_movers DB fallback failed: {e}")
        return []


@app.get("/api/market-movers")
def market_movers(limit: int = Query(default=15, le=50)):
    now = time.time()
    if _movers_cache["data"] is not None and (now - _movers_cache["ts"]) < MOVERS_CACHE_TTL_SEC:
        return _movers_cache["data"][:limit]

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_fetch_market_movers, 50)
        result = future.result(timeout=15)

        if result and len(result) >= MOVERS_MIN_QUALITY:
            _movers_cache["data"] = result
            _movers_cache["ts"] = now
            return result[:limit]

        # Degraded/partial result (Yahoo screener likely throttled) — don't let
        # it clobber a previously-good cache; keep serving the last solid list
        # if we have one.
        if result:
            print(f"[warn] market-movers: got only {len(result)} results (< {MOVERS_MIN_QUALITY}), "
                  f"treating as degraded and falling back")
        else:
            print("[warn] market-movers: whole-market screener returned nothing at all "
                  "(Yahoo likely blocking/throttling this endpoint from this IP) — falling back")

        if _movers_cache["data"] is not None:
            return _movers_cache["data"][:limit]

        # No cache to fall back on either — try our own universe as a last resort
        # before giving up, so the strip isn't empty just because the whole-market
        # screener is unavailable.
        fallback = _fetch_fallback_movers_from_db(limit)
        if fallback:
            print(f"[info] market-movers: serving {len(fallback)} results from the universe_movers fallback")
            return fallback
        return result or []
    except FutureTimeoutError:
        if _movers_cache["data"] is not None:
            return _movers_cache["data"][:limit]  # serve stale cache rather than failing outright
        fallback = _fetch_fallback_movers_from_db(limit)
        if fallback:
            return fallback
        raise HTTPException(status_code=504, detail="תם הזמן לשליפת המניות הכי זזות בשוק")
    except Exception as e:
        if _movers_cache["data"] is not None:
            return _movers_cache["data"][:limit]
        fallback = _fetch_fallback_movers_from_db(limit)
        if fallback:
            return fallback
        raise HTTPException(status_code=502, detail=f"שגיאה בשליפת נתוני שוק: {e}")
    finally:
        executor.shutdown(wait=False)


# ------------------------------------------------------------------
# Sector performance (Koyfin-style "US Sectors" view): the 11 SPDR
# sector ETFs, with today's 1D % change for the table and a normalized
# (rebased-to-0%) daily close series per ETF for the comparison chart.
# Cached per-period since a full multi-ticker history download is a
# heavier call than a single quote.
# ------------------------------------------------------------------

SECTOR_ETFS = {
    "XLY": "Cons. Discretionary", "XLP": "Cons. Staples", "XLE": "Energy",
    "XLF": "Financials", "XLV": "Health Care", "XLI": "Industrials",
    "XLB": "Materials", "XLRE": "Real Estate", "XLK": "Technology",
    "XLC": "Communications", "XLU": "Utilities",
}

SECTOR_PERIOD_MAP = {"1M": "1mo", "3M": "3mo", "YTD": "ytd", "1Y": "1y"}
SECTOR_CACHE_TTL_SEC = 600  # sector prices move much slower than individual movers
_sector_cache: dict = {}  # period -> {"data": ..., "ts": ...}


def _fetch_sector_performance(period: str) -> dict:
    yf_period = SECTOR_PERIOD_MAP[period]

    # NOTE: this used to be a single batched yf.download(tickers, ...) call.
    # That's a different code path than yf.Ticker(x).history(), which is the
    # one already confirmed to work reliably from Railway's IP (see the note
    # above the fundamentals section) — the batched/threaded multi-ticker
    # download appears to be more prone to getting throttled by Yahoo, and
    # since it's all-or-nothing, ANY hiccup meant zero sector data and the
    # frontend stuck on "couldn't load sector data" with nothing to show.
    # Looping over individual, proven-reliable single-ticker calls means one
    # ETF failing doesn't take down the other 10.
    per_ticker_closes = {}
    all_dates_set = set()
    for ticker, name in SECTOR_ETFS.items():
        try:
            hist = yf.Ticker(ticker).history(period=yf_period, interval="1d")
            closes = hist["Close"].dropna()
        except Exception as e:
            print(f"[warn] sector fetch failed for {ticker}: {e}")
            continue
        if closes.empty:
            continue
        closes_by_date = {d.strftime("%Y-%m-%d"): float(v) for d, v in closes.items()}
        per_ticker_closes[ticker] = (name, closes_by_date)
        all_dates_set.update(closes_by_date.keys())

    if not per_ticker_closes:
        raise RuntimeError("no sector ETF data could be fetched (all 11 tickers failed)")

    all_dates = sorted(all_dates_set)
    series = []
    table = []

    for ticker, (name, closes_by_date) in per_ticker_closes.items():
        base = next(iter(closes_by_date.values()))
        last_val = None
        normalized = []
        for d in all_dates:
            if d in closes_by_date:
                last_val = closes_by_date[d]
            normalized.append(round((last_val / base - 1) * 100, 2) if last_val is not None else None)
        series.append({"ticker": ticker, "name": name, "values": normalized})

        values_list = list(closes_by_date.values())
        latest_close = values_list[-1]
        prev_close = values_list[-2] if len(values_list) >= 2 else latest_close
        day_chg = round((latest_close / prev_close - 1) * 100, 2) if prev_close else 0.0
        table.append({
            "ticker": ticker, "name": name,
            "price": round(latest_close, 2),
            "change_pct": day_chg,
            "period_change_pct": normalized[-1] if normalized else 0.0,
        })

    table.sort(key=lambda r: r["change_pct"], reverse=True)
    return {"period": period, "dates": all_dates, "series": series, "table": table}


@app.get("/api/heatmap")
def get_heatmap(index: str = Query(default="sp500", pattern="^(sp500|nasdaq100)$")):
    """Index heatmap data (ticker + today's % change) for the Market Pulse
    page. Reuses universe_movers — already populated by every scan run
    with the whole universe's daily change, tagged by index membership —
    so this needs zero extra yfinance calls of its own."""
    column = "in_sp500" if index == "sp500" else "in_nasdaq100"
    with db_cursor(dict_cursor=True) as (conn, cur):
        cur.execute(
            f"""
            SELECT ticker, change_pct, close_price, timestamp
            FROM universe_movers
            WHERE {column} = TRUE AND change_pct IS NOT NULL
            ORDER BY change_pct DESC
            """
        )
        rows = cur.fetchall()
    return {"index": index, "count": len(rows), "stocks": [dict(r) for r in rows]}


# ------------------------------------------------------------------
# Fear & Greed Index — the real CNN number, via the same unofficial JSON
# endpoint CNN's own website widget calls (no official public API exists
# for this; this endpoint is widely used by independent trackers/bots for
# exactly that reason). Since it's undocumented, it could change or get
# blocked without notice — cached for an hour, and on failure this serves
# the last successfully-fetched value rather than nothing, so a temporary
# hiccup upstream doesn't blank out the page.
# ------------------------------------------------------------------

_fear_greed_cache = {"data": None, "ts": 0}
FEAR_GREED_CACHE_TTL = 3600  # 1 hour — this index doesn't move faster than that anyway

FEAR_GREED_COMPONENT_KEYS = [
    "market_momentum_sp500", "market_momentum_sp125",
    "stock_price_strength", "stock_price_breadth",
    "put_call_options", "market_volatility_vix", "market_volatility_vix_50",
    "junk_bond_demand", "safe_haven_demand",
]

@app.get("/api/fear-greed")
def get_fear_greed():
    now = time.time()
    if _fear_greed_cache["data"] and (now - _fear_greed_cache["ts"]) < FEAR_GREED_CACHE_TTL:
        return _fear_greed_cache["data"]

    try:
        resp = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        resp.raise_for_status()
        raw = resp.json()
        main = raw.get("fear_and_greed", {})
        result = {
            "score": main.get("score"),
            "rating": main.get("rating"),
            "previous_close": main.get("previous_close"),
            "previous_1_week": main.get("previous_1_week"),
            "previous_1_month": main.get("previous_1_month"),
            "previous_1_year": main.get("previous_1_year"),
            "components": {},
            "source": "cnn",
        }
        for key in FEAR_GREED_COMPONENT_KEYS:
            comp = raw.get(key)
            if isinstance(comp, dict) and comp.get("score") is not None:
                result["components"][key] = {"score": comp.get("score"), "rating": comp.get("rating")}
        if result["score"] is None:
            raise ValueError("CNN response didn't include a score — endpoint shape may have changed")
        _fear_greed_cache["data"] = result
        _fear_greed_cache["ts"] = now
        return result
    except Exception as e:
        print(f"[warn] CNN Fear & Greed fetch failed: {e}")
        if _fear_greed_cache["data"]:
            return _fear_greed_cache["data"]  # serve the last good value rather than nothing
        raise HTTPException(status_code=502, detail="Fear & Greed index temporarily unavailable")


@app.get("/api/sector-performance")
def sector_performance(period: str = Query(default="YTD", pattern="^(1M|3M|YTD|1Y)$")):
    now = time.time()
    cached = _sector_cache.get(period)
    if cached and (now - cached["ts"]) < SECTOR_CACHE_TTL_SEC:
        return cached["data"]

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_fetch_sector_performance, period)
        result = future.result(timeout=40)
        _sector_cache[period] = {"data": result, "ts": now}
        return result
    except FutureTimeoutError:
        if cached:
            return cached["data"]  # serve stale cache rather than failing outright
        raise HTTPException(status_code=504, detail="תם הזמן לשליפת ביצועי הסקטורים")
    except Exception as e:
        if cached:
            return cached["data"]
        raise HTTPException(status_code=502, detail=f"שגיאה בשליפת ביצועי הסקטורים: {e}")
    finally:
        executor.shutdown(wait=False)




# ------------------------------------------------------------------
# Fundamentals dashboard (per-stock drawer): quarterly financials,
# net debt, margins, EPS, and a historical P/E band.
#
# Core financial statement data comes from the SEC's own free EDGAR
# XBRL API (data.sec.gov) instead of yfinance's quoteSummary-based
# calls (.info / .quarterly_financials / etc). Those hit the SAME
# Yahoo endpoint class that the market-movers screener does, which is
# the one that appears to be blocked/throttled from Railway's IP —
# unlike .history(), which works reliably and is still used here for
# the P/E band price series. SEC EDGAR is an official US government
# API, free, keyless, and not subject to that same blocking.
#
# Trade-off: SEC has no analyst EPS *estimates* (only actual reported
# figures), so "EPS actual vs estimate" is attempted as a best-effort
# bonus via yfinance and simply omitted if that call is unavailable —
# it never blocks the rest of the section.
# ------------------------------------------------------------------

FUNDAMENTALS_TIMEOUT_SEC = 20  # hard cap so one slow call can't hang a request thread
SEC_HEADERS = {"User-Agent": "SwingDesk-Dashboard admin@swingdesk.app"}  # SEC's fair-use policy
                                                                          # asks automated tools to
                                                                          # send *a* descriptive
                                                                          # User-Agent string — their
                                                                          # servers don't verify it's
                                                                          # real or tied to anyone.
                                                                          # This is a generic made-up
                                                                          # placeholder, not personal
                                                                          # info — nothing to change here.

_cik_cache = {"data": None, "ts": 0.0}
CIK_CACHE_TTL_SEC = 24 * 3600  # ticker->CIK mapping barely changes; refresh once a day


def _get_cik_map() -> dict:
    now = time.time()
    if _cik_cache["data"] is not None and (now - _cik_cache["ts"]) < CIK_CACHE_TTL_SEC:
        return _cik_cache["data"]

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=10)
            resp.raise_for_status()
            raw = resp.json()
            mapping = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in raw.values()}
            _cik_cache["data"] = mapping
            _cik_cache["ts"] = now
            return mapping
        except Exception as e:
            last_error = e
            print(f"[warn] SEC ticker->CIK map fetch attempt {attempt+1}/3 failed: {type(e).__name__}: {e}")
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    raise last_error


def _sec_company_facts(cik10: str) -> Optional[dict]:
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json",
                                 headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            # IMPORTANT: log the actual status code — this was previously
            # swallowed entirely, so a 403/429/5xx from SEC was
            # indistinguishable from "this company just has no data".
            print(f"[warn] SEC companyfacts CIK{cik10} attempt {attempt+1}/3: HTTP {resp.status_code}")
            if resp.status_code == 404:
                return None  # genuinely no such CIK — retrying won't help
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = e
            print(f"[warn] SEC companyfacts CIK{cik10} attempt {attempt+1}/3 failed: {type(e).__name__}: {e}")
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    print(f"[warn] SEC companyfacts CIK{cik10}: all 3 attempts failed, last error: {last_error}")
    return None


def _sec_quarterly_series(facts_usgaap: dict, tag_candidates: List[str], instant: bool) -> dict:
    """Returns {end_date_str: value}, merged across ALL of the given XBRL
    tag candidates — not just the first one with any data. Companies
    routinely switch which tag they report a concept under across fiscal
    years (XBRL taxonomy updates), so restricting to a single tag left
    real gaps in the timeline where a company happened to use a
    different (but equally valid) tag for that period.

    Flow measures (revenue, net income, EPS, cash flow) prefer a clean
    single-quarter entry (~75-105 day span). Where that doesn't exist —
    very common for cash-flow-statement items, which many companies only
    disclose as cumulative year-to-date in interim filings — the discrete
    quarter is DERIVED from consecutive YTD deltas sharing the same
    fiscal-year start (e.g. Q4 = full-year total − 9-month YTD). Without
    this, those quarters would just be empty gaps in every chart.

    Instant measures (cash, debt, shares) are point-in-time balance-sheet
    snapshots, kept as-is."""
    out = {}
    raw_all = []  # every valid flow-measure entry (any span), for YTD-delta derivation

    for tag in tag_candidates:
        node = facts_usgaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        unit_key = next(iter(units), None)
        if not unit_key:
            continue
        for e in units[unit_key]:
            if e.get("form") not in ("10-Q", "10-K"):
                continue
            end, val = e.get("end"), e.get("val")
            if end is None or val is None:
                continue
            if instant:
                out.setdefault(end, val)  # keep the first tag's value if two tags both cover this date
            else:
                start = e.get("start")
                if not start:
                    continue
                try:
                    span_days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                except Exception:
                    continue
                raw_all.append({"start": start, "end": end, "val": val})
                if 75 <= span_days <= 105:
                    out.setdefault(end, val)

    if instant or not raw_all:
        return out

    # Derive quarters that only exist as YTD-cumulative figures: group by
    # fiscal-year start, sort by end date, and take consecutive deltas.
    # IMPORTANT: the group includes every entry sharing that start —
    # including ones already accepted directly above (e.g. Q1, which is
    # both a discrete quarter AND the first YTD data point) — so it can
    # serve as the anchor value the next delta is computed against.
    by_start: dict = {}
    for e in raw_all:
        by_start.setdefault(e["start"], []).append(e)

    for start, group in by_start.items():
        group_sorted = sorted(group, key=lambda e: e["end"])
        prev_val = 0.0
        try:
            prev_end_date = date.fromisoformat(start)
        except Exception:
            continue
        for e in group_sorted:
            try:
                end_date = date.fromisoformat(e["end"])
            except Exception:
                continue
            gap_days = (end_date - prev_end_date).days
            # Only accept as a genuine single-quarter delta — not a jump
            # spanning a missing quarter in between.
            if e["end"] not in out and 60 <= gap_days <= 115:
                out.setdefault(e["end"], e["val"] - prev_val)
            prev_val = e["val"]
            prev_end_date = end_date

    return out


def _sec_cumulative_to_quarterly(facts_usgaap: dict, tag_candidates: List[str]) -> dict:
    """For cash-flow-statement items (operating cash flow, capex, etc.),
    which SEC/GAAP convention always reports as FISCAL-YEAR-TO-DATE
    CUMULATIVE figures in 10-Q filings — never a standalone single
    quarter — unlike revenue/net income/EPS, which companies do report
    standalone. Using the same ~90-day span filter as
    _sec_quarterly_series only catches Q1 (which happens to be
    standalone already) and discards Q2/Q3/Q4 entirely, which is why
    FCF charts were coming out almost empty. This derives the real
    standalone quarter value by subtracting the prior cumulative period
    within the same fiscal year, using SEC's own 'fy'/'fp' fields."""
    merged = {}
    for tag in tag_candidates:
        node = facts_usgaap.get(tag)
        if not node:
            continue
        units = node.get("units", {})
        unit_key = next(iter(units), None)
        if not unit_key:
            continue

        by_fy = {}
        for e in units[unit_key]:
            if e.get("form") not in ("10-Q", "10-K"):
                continue
            fy, fp, end, val = e.get("fy"), e.get("fp"), e.get("end"), e.get("val")
            if not (fy and fp and end is not None and val is not None):
                continue
            by_fy.setdefault(fy, {})[fp] = (end, val)  # later filings overwrite (restatements)

        for fy, periods in by_fy.items():
            q1, q2, q3, fyend = periods.get("Q1"), periods.get("Q2"), periods.get("Q3"), periods.get("FY")
            if q1:
                merged.setdefault(q1[0], q1[1])
            if q1 and q2:
                merged.setdefault(q2[0], q2[1] - q1[1])
            if q2 and q3:
                merged.setdefault(q3[0], q3[1] - q2[1])
            if q3 and fyend:
                merged.setdefault(fyend[0], fyend[1] - q3[1])
    return merged


def _compute_pe_band_sec(ticker: str, dates_sorted: List[str], diluted_eps: List[Optional[float]]):
    """Weekly P/E band: yfinance .history() (reliable) for price, matched
    against SEC's trailing-twelve-month diluted EPS via merge_asof."""
    try:
        pairs = [(d, v) for d, v in zip(dates_sorted, diluted_eps) if v is not None]
        if len(pairs) < 4:
            return None
        eps_series = pd.Series([p[1] for p in pairs], index=pd.to_datetime([p[0] for p in pairs])).sort_index()
        ttm_eps = eps_series.rolling(4).sum().dropna()
        if ttm_eps.empty:
            return None

        hist = yf.Ticker(ticker).history(period="5y", interval="1wk")
        if hist.empty:
            return None

        ttm_df = pd.DataFrame({
            "date": pd.to_datetime(ttm_eps.index).tz_localize(None).astype("datetime64[ns]"),
            "ttm_eps": ttm_eps.values,
        }).sort_values("date")

        hist2 = hist.reset_index()
        date_col = "Date" if "Date" in hist2.columns else hist2.columns[0]
        # IMPORTANT: pandas can parse dates at different internal resolutions
        # (datetime64[s] vs [us] vs [ns]) depending on the source — SEC's
        # ISO date strings vs yfinance's DatetimeIndex commonly disagree.
        # merge_asof requires the merge key dtype to match EXACTLY, so both
        # sides are forced to the same resolution here.
        hist2["date"] = pd.to_datetime(hist2[date_col]).dt.tz_localize(None).astype("datetime64[ns]")
        hist2 = hist2.sort_values("date")

        merged = pd.merge_asof(hist2, ttm_df, on="date", direction="backward")
        merged["pe"] = merged["Close"] / merged["ttm_eps"]
        merged = merged[(merged["ttm_eps"] > 0) & merged["pe"].notna() & (merged["pe"] < 500) & (merged["pe"] > 0)]
        if merged.empty:
            return None

        mean = float(merged["pe"].mean())
        std = float(merged["pe"].std() or 0)
        return {
            "dates": merged["date"].dt.strftime("%Y-%m-%d").tolist(),
            "values": merged["pe"].round(2).tolist(),
            "mean": round(mean, 1),
            "std1_upper": round(mean + std, 1),
            "std1_lower": round(max(mean - std, 0), 1),
            "current": round(float(merged["pe"].iloc[-1]), 1),
        }
    except Exception as e:
        print(f"[warn] P/E band calc failed for {ticker}: {e}")
        return None


def _json_safe(obj):
    """Recursively replaces NaN/Infinity/-Infinity with None. Python's
    default JSON encoder serializes these as literal, non-standard
    tokens (NaN, Infinity) — valid Python but NOT valid JSON. A browser's
    fetch().json() throws a SyntaxError on them, which looks exactly like
    'the request failed' even though the server actually returned 200 OK.
    Any division here (margins, P/E ratios, etc.) can produce one of
    these from an unusual data point, so this is applied as a final
    safety net right before any computed dict leaves the server."""
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    return obj


def _build_fundamentals(ticker: str) -> dict:
    ticker = ticker.upper()

    try:
        cik10 = _get_cik_map().get(ticker)
    except Exception as e:
        print(f"[warn] fundamentals({ticker}): SEC ticker->CIK map fetch failed: {e}")
        cik10 = None

    if not cik10:
        print(f"[info] fundamentals({ticker}): no SEC CIK match (likely an ETF/index, not an individual filer)")
        return {"ticker": ticker, "has_fundamentals": False}

    try:
        facts = _sec_company_facts(cik10)
    except Exception as e:
        print(f"[warn] fundamentals({ticker}): SEC companyfacts fetch failed: {e}")
        facts = None

    usgaap = (facts or {}).get("facts", {}).get("us-gaap", {})
    if not usgaap:
        print(f"[warn] fundamentals({ticker}): SEC companyfacts had no us-gaap data")
        return {"ticker": ticker, "has_fundamentals": False}

    revenue_s = _sec_quarterly_series(usgaap, ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"], instant=False)
    ni_s      = _sec_quarterly_series(usgaap, ["NetIncomeLoss"], instant=False)
    gross_s   = _sec_quarterly_series(usgaap, ["GrossProfit"], instant=False)
    eps_s     = _sec_quarterly_series(usgaap, ["EarningsPerShareDiluted", "EarningsPerShareBasic"], instant=False)
    ocf_s     = _sec_cumulative_to_quarterly(usgaap, ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"])
    capex_s   = _sec_cumulative_to_quarterly(usgaap, ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsForCapitalImprovements"])
    cash_s    = _sec_quarterly_series(usgaap, ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations"], instant=True)
    debt_s    = _sec_quarterly_series(usgaap, ["LongTermDebtNoncurrent", "LongTermDebt", "DebtLongtermAndShorttermCombinedAmount"], instant=True)
    shares_s  = _sec_quarterly_series(usgaap, ["CommonStockSharesOutstanding"], instant=True)

    if not revenue_s and not ni_s:
        print(f"[info] fundamentals({ticker}): SEC facts had no usable revenue/net-income tags for this company")
        return {"ticker": ticker, "has_fundamentals": False}

    def _nearest(series: dict, target: str, tolerance_days: int = 6):
        """Finds the value in `series` whose date is within `tolerance_days`
        of `target`, preferring an exact match. Handles the common case
        where a balance-sheet or cash-flow figure was filed a few days
        off from the matching income-statement quarter-end."""
        if target in series:
            return series[target]
        try:
            t = date.fromisoformat(target)
        except Exception:
            return None
        best_val, best_diff = None, tolerance_days + 1
        for d_str, v in series.items():
            try:
                d = date.fromisoformat(d_str)
            except Exception:
                continue
            diff = abs((d - t).days)
            if diff <= tolerance_days and diff < best_diff:
                best_val, best_diff = v, diff
        return best_val

    def _nearest_pair(series_a: dict, series_b: dict, tolerance_days: int = 6):
        """Pairs up two series by nearest matching date (within tolerance),
        returning {date: (val_a, val_b)} using series_a's dates as anchors.
        Used for ratios/differences that need two series at once (margin
        needs gross+revenue; net debt needs debt+cash)."""
        out = {}
        for d, va in series_a.items():
            vb = _nearest(series_b, d, tolerance_days)
            if vb is not None:
                out[d] = (va, vb)
        return out

    def to_chart(date_value_pairs):
        """Sorts a {date: value} dict by date (cap to the most recent 32
        points) and returns (labels, values) ready for the frontend."""
        items = sorted(date_value_pairs.items())[-32:]
        labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d, _ in items]
        values = [round(float(v), 4) if v is not None else None for d, v in items]
        return labels, values

    # Revenue / Net Income / FCF: each chart card gets its OWN compact
    # date axis (its own series' union only) instead of being forced onto
    # one shared axis padded with dates that only exist for unrelated
    # balance-sheet metrics — that shared-axis design was the cause of
    # the many empty/blank columns in the chart.
    fcf_s = {}
    for d, ov in ocf_s.items():
        cv = _nearest(capex_s, d)
        if cv is not None:
            fcf_s[d] = ov - cv

    income_dates = sorted(set(revenue_s) | set(ni_s) | set(fcf_s))[-32:]
    income_labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d in income_dates]
    revenue    = [round(float(v), 2) if (v := _nearest(revenue_s, d)) is not None else None for d in income_dates]
    net_income = [round(float(v), 2) if (v := _nearest(ni_s, d)) is not None else None for d in income_dates]
    fcf        = [round(float(v), 2) if (v := _nearest(fcf_s, d)) is not None else None for d in income_dates]

    margin_pairs = _nearest_pair(gross_s, revenue_s)
    margin_ratio = {d: (g / r * 100) for d, (g, r) in margin_pairs.items() if r}
    margin_labels, gross_margin_pct = to_chart(margin_ratio)

    # Net debt = debt - cash. A company with little/no long-term debt (SHOP,
    # many high-growth tech names) often has NO debt tag at all in its SEC
    # filings — not a zero value, just nothing filed, because there's
    # nothing to report on that line. Previously this meant debt_s was
    # empty, _nearest_pair had nothing to anchor on, and the whole net-debt
    # chart came back blank even though the company clearly does report
    # (cash figures were right there). Now: if debt data exists, pair it
    # with cash as before; if it's missing entirely, treat debt as 0 and
    # chart net debt directly from the cash series (net cash position).
    if debt_s:
        debt_pairs = _nearest_pair(debt_s, cash_s)
        net_debt_map = {d: (dv - cv) for d, (dv, cv) in debt_pairs.items()}
    elif cash_s:
        net_debt_map = {d: -cv for d, cv in cash_s.items()}
    else:
        net_debt_map = {}
    debt_labels, net_debt = to_chart(net_debt_map)

    eps_dates = sorted(eps_s)[-32:]
    diluted_eps_labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d in eps_dates]
    diluted_eps = [round(float(v), 4) if (v := eps_s.get(d)) is not None else None for d in eps_dates]

    latest_shares = None
    if shares_s:
        latest_date = max(shares_s)
        latest_shares = round(float(shares_s[latest_date]), 0)

    pe_band = _compute_pe_band_sec(ticker, eps_dates, diluted_eps)

    # Analyst EPS estimates: SEC doesn't have these. Best-effort bonus via
    # yfinance — if this (Yahoo quoteSummary-based) call is unavailable,
    # we simply skip the "vs estimate" comparison rather than fail anything.
    eps_labels, eps_actual, eps_estimate, eps_surprise_pct = [], [], [], []
    try:
        edates = yf.Ticker(ticker).get_earnings_dates(limit=12)
        if edates is not None and not edates.empty:
            edates = edates.dropna(subset=["Reported EPS"]).sort_index()
            for idx, row in edates.iterrows():
                eps_labels.append(idx.strftime("%b %Y"))
                eps_actual.append(round(float(row.get("Reported EPS")), 2) if pd.notna(row.get("Reported EPS")) else None)
                eps_estimate.append(round(float(row.get("EPS Estimate")), 2) if pd.notna(row.get("EPS Estimate")) else None)
                surprise = row.get("Surprise(%)")
                eps_surprise_pct.append(round(float(surprise) * 100, 1) if pd.notna(surprise) else None)
    except Exception as e:
        print(f"[info] fundamentals({ticker}): yfinance analyst EPS estimates unavailable (non-fatal): {e}")

    trailing_pe = forward_pe = None
    try:
        info = yf.Ticker(ticker).info or {}
        trailing_pe = info.get("trailingPE")
        forward_pe = info.get("forwardPE")
    except Exception as e:
        print(f"[info] fundamentals({ticker}): yfinance trailing/forward P/E unavailable (non-fatal): {e}")

    return _json_safe({
        "ticker": ticker,
        "has_fundamentals": True,
        "data_source": "sec_edgar",
        "cik": cik10,
        "income_labels": income_labels,
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "margin_labels": margin_labels,
        "gross_margin_pct": gross_margin_pct,
        "debt_labels": debt_labels,
        "net_debt": net_debt,
        "latest_shares_outstanding": latest_shares,
        "diluted_eps_labels": diluted_eps_labels,
        "diluted_eps": diluted_eps,
        "eps_labels": eps_labels,
        "eps_actual": eps_actual,
        "eps_estimate": eps_estimate,
        "eps_surprise_pct": eps_surprise_pct,
        "pe_band": pe_band,
        "trailing_pe": trailing_pe,
        "forward_pe": forward_pe,
    })


_fundamentals_cache = {}  # ticker -> (timestamp, data)
FUNDAMENTALS_CACHE_TTL_SEC = 900          # 15 min for a SUCCESSFUL result - fundamentals
                                           # change slowly, and this avoids hammering SEC
                                           # every time a stock drawer reopens
FUNDAMENTALS_NEGATIVE_CACHE_TTL_SEC = 45   # a "no data" result gets a much shorter life —
                                           # this is usually a transient hiccup (rate limit,
                                           # brief network blip), not a permanent fact about
                                           # the ticker, so it should self-heal quickly
                                           # instead of blocking retries for 15 minutes


def _is_valid_fundamentals_shape(data: dict) -> bool:
    """Guards the cache against ever serving a malformed/incomplete response
    (e.g. from an older code version, or a partial result from some edge
    case) for the full 15-minute TTL. If it doesn't look structurally
    sound, treat it as a cache miss and recompute fresh."""
    if not isinstance(data, dict):
        return False
    if not data.get("has_fundamentals"):
        return True  # the "no fundamentals for this ticker" shape is valid as-is
    return isinstance(data.get("income_labels"), list)


@app.get("/api/fundamentals/{ticker}")
def get_fundamentals(ticker: str):
    ticker = ticker.upper().strip()

    cached = _fundamentals_cache.get(ticker)
    if cached:
        cached_ts, cached_data = cached
        ttl = FUNDAMENTALS_CACHE_TTL_SEC if cached_data.get("has_fundamentals") else FUNDAMENTALS_NEGATIVE_CACHE_TTL_SEC
        if (time.time() - cached_ts) < ttl and _is_valid_fundamentals_shape(cached_data):
            return cached_data

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_build_fundamentals, ticker)
        data = future.result(timeout=FUNDAMENTALS_TIMEOUT_SEC)
        _fundamentals_cache[ticker] = (time.time(), data)
        return data
    except FutureTimeoutError:
        raise HTTPException(status_code=504, detail="החישוב לקח יותר מדי זמן, נסה שוב")
    except Exception as e:
        print(f"[warn] /api/fundamentals/{ticker} failed: {e}")
        raise HTTPException(status_code=502, detail=f"שגיאה בשליפת נתונים פונדמנטליים: {e}")
    finally:
        executor.shutdown(wait=False)




class TrackEvent(BaseModel):
    event_type: str            # 'view_stock' / 'view_macro' / 'page_view'
    entity_id: Optional[str] = None
    session_id: Optional[str] = None


@app.post("/api/track")
def track_event(event: TrackEvent):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO analytics_events (event_type, entity_id, session_id) VALUES (%s, %s, %s)",
        (event.event_type, event.entity_id, event.session_id),
    )
    conn.commit()
    cur.close()
    conn.close()
    return {"status": "logged"}


@app.get("/api/analytics/top-interest")
def top_interest(
    event_type: str = Query(default="view_stock", pattern="^(view_stock|view_macro|page_view|search_stock)$"),
    days: int = 7,
    limit: int = 10,
):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT entity_id, COUNT(*) as clicks
        FROM analytics_events
        WHERE event_type = %s AND timestamp >= NOW() - (%s || ' days')::INTERVAL
        GROUP BY entity_id
        ORDER BY clicks DESC
        LIMIT %s
        """,
        (event_type, days, limit),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/summary")
def analytics_summary(days: int = 7):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT date(timestamp) as day, event_type, COUNT(*) as count
        FROM analytics_events
        WHERE timestamp >= NOW() - (%s || ' days')::INTERVAL
        GROUP BY day, event_type
        ORDER BY day DESC
        """,
        (days,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]
