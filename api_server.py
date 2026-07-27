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

CORS is open for local development. Lock this down (allow_origins) before
deploying publicly.
"""

import json
import math
import os
import time
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://swing-desk-tau.vercel.app",
        "http://localhost:8000",
        "http://localhost:3000",
    ],
    allow_methods=["GET", "POST"],
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
    """)
    # Patches an already-deployed table that predates this column
    # (CREATE TABLE IF NOT EXISTS above is a no-op once the table exists).
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS change_pct REAL;")
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


@app.get("/api/macro-news")
def get_macro_news(limit: int = Query(default=25, le=100)):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM macro_news ORDER BY timestamp DESC LIMIT %s", (limit,))
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

@app.get("/api/lookup/{ticker}")
def lookup_stock(ticker: str):
    ticker = ticker.upper().strip()
    try:
        tk = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="6mo", interval="1d")
    except Exception:
        raise HTTPException(status_code=502, detail="שגיאה בשליפת הנתונים, נסה שוב")

    if hist.empty or not info.get("regularMarketPrice") and not info.get("currentPrice"):
        raise HTTPException(status_code=404, detail=f"הטיקר {ticker} לא נמצא")

    price = info.get("currentPrice") or info.get("regularMarketPrice") or float(hist["Close"].iloc[-1])
    prev_close = info.get("previousClose") or float(hist["Close"].iloc[-2]) if len(hist) > 1 else price
    change_pct = round(((price - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "price": round(float(price), 2),
        "change_pct": change_pct,
        "market_cap": info.get("marketCap"),
        "avg_volume_20d": round(float(hist["Volume"].tail(20).mean()), 0) if len(hist) >= 20 else None,
        "week52_high": info.get("fiftyTwoWeekHigh"),
        "week52_low": info.get("fiftyTwoWeekLow"),
        "exchange": info.get("exchange"),
        "sector": info.get("sector"),
        "pe_ratio": info.get("trailingPE"),
    }


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

    debt_pairs = _nearest_pair(debt_s, cash_s)
    net_debt_map = {d: (dv - cv) for d, (dv, cv) in debt_pairs.items()}
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
FUNDAMENTALS_CACHE_TTL_SEC = 900  # 15 min - fundamentals change slowly, and this avoids
                                  # hammering yfinance every time a stock drawer reopens


def _is_valid_fundamentals_shape(data: dict) -> bool:
    """Guards the cache against ever serving a malformed/incomplete response
    (e.g. from an older code version, or a partial result from some edge
    case) for the full 15-minute TTL. If it doesn't look structurally
    sound, treat it as a cache miss and recompute fresh."""
    if not isinstance(data, dict):
        return False
    if not data.get("has_fundamentals"):
        return True  # the "no fundamentals for this ticker" shape is valid as-is
    return isinstance(data.get("quarter_labels"), list)


@app.get("/api/fundamentals/{ticker}")
def get_fundamentals(ticker: str):
    ticker = ticker.upper().strip()

    cached = _fundamentals_cache.get(ticker)
    if cached and (time.time() - cached[0]) < FUNDAMENTALS_CACHE_TTL_SEC and _is_valid_fundamentals_shape(cached[1]):
        return cached[1]

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
