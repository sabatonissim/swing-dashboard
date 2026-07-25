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
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from typing import List, Optional

import pandas as pd
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
# net debt, margins, EPS actual-vs-estimate, and a historical P/E band.
# All from yfinance - free, no paid data provider.
# ------------------------------------------------------------------

FUNDAMENTALS_TIMEOUT_SEC = 20  # hard cap so one slow yfinance call can't hang a request thread


def _get_row(df: "pd.DataFrame", *names):
    """Return the first matching row (by index label) from a yfinance
    financial statement dataframe, or None if none of the names exist."""
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def _series_to_chart(row, quarters_sorted_cols):
    """Aligns a financial-statement row to the given (ascending) column
    order and returns a list of floats/None, rounded for display."""
    if row is None:
        return [None] * len(quarters_sorted_cols)
    out = []
    for col in quarters_sorted_cols:
        v = row.get(col)
        out.append(round(float(v), 2) if v is not None and pd.notna(v) else None)
    return out


def _compute_pe_band(tk: "yf.Ticker", qf: "pd.DataFrame"):
    """Builds a weekly historical P/E series plus mean/±1-std band, using
    trailing-twelve-month EPS (rolling 4-quarter sum) matched against
    weekly closing price via merge_asof. Returns None if there isn't
    enough data (e.g. ETFs, or tickers with a thin earnings history)."""
    try:
        eps_row = _get_row(qf, "Diluted EPS", "Basic EPS")
        if eps_row is None:
            return None
        eps_row = eps_row.dropna().sort_index()
        if len(eps_row) < 4:
            return None
        ttm_eps = eps_row.rolling(4).sum().dropna()
        if ttm_eps.empty:
            return None

        hist = tk.history(period="5y", interval="1wk")
        if hist.empty:
            return None

        ttm_df = pd.DataFrame({
            "date": pd.to_datetime(ttm_eps.index).tz_localize(None),
            "ttm_eps": ttm_eps.values,
        }).sort_values("date")

        hist2 = hist.reset_index()
        date_col = "Date" if "Date" in hist2.columns else hist2.columns[0]
        hist2["date"] = pd.to_datetime(hist2[date_col]).dt.tz_localize(None)
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
        print(f"[warn] P/E band calc failed: {e}")
        return None


def _first_nonempty_df(*dfs):
    for df in dfs:
        if df is not None and not df.empty:
            return df
    return None


def _build_fundamentals(ticker: str) -> dict:
    tk = yf.Ticker(ticker)

    try:
        info = tk.info or {}
    except Exception as e:
        print(f"[warn] fundamentals({ticker}): .info fetch failed: {e}")
        info = {}

    # yfinance has renamed these properties across versions
    # (quarterly_financials -> quarterly_income_stmt). Try both so we
    # don't silently show "no data" just because of a naming drift.
    def _safe_get(*attr_names):
        for name in attr_names:
            try:
                df = getattr(tk, name, None)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                print(f"[warn] fundamentals({ticker}): .{name} fetch failed: {e}")
        return None

    qf = _safe_get("quarterly_financials", "quarterly_income_stmt")
    qcf = _safe_get("quarterly_cashflow", "quarterly_cash_flow")
    qbs = _safe_get("quarterly_balance_sheet", "quarterly_balancesheet")

    if qf is None:
        print(f"[info] fundamentals({ticker}): no quarterly income statement available (ETF/index or data gap)")
        return {"ticker": ticker, "has_fundamentals": False}

    quarters_sorted = sorted(qf.columns.tolist())
    quarter_labels = [c.strftime("%b %Y") for c in quarters_sorted]

    revenue    = _series_to_chart(_get_row(qf, "Total Revenue"), quarters_sorted)
    net_income = _series_to_chart(_get_row(qf, "Net Income", "Net Income Common Stockholders"), quarters_sorted)
    gross_profit = _get_row(qf, "Gross Profit")
    if gross_profit is not None and _get_row(qf, "Total Revenue") is not None:
        rev_row = _get_row(qf, "Total Revenue")
        gross_margin_pct = []
        for col in quarters_sorted:
            r = rev_row.get(col)
            g = gross_profit.get(col)
            if r and pd.notna(r) and pd.notna(g) and r != 0:
                gross_margin_pct.append(round(float(g) / float(r) * 100, 1))
            else:
                gross_margin_pct.append(None)
    else:
        gross_margin_pct = [None] * len(quarters_sorted)

    op_cf = _get_row(qcf, "Operating Cash Flow", "Total Cash From Operating Activities")
    capex = _get_row(qcf, "Capital Expenditure", "Capital Expenditures")
    fcf_row = _get_row(qcf, "Free Cash Flow")
    if fcf_row is not None:
        fcf = _series_to_chart(fcf_row, quarters_sorted)
    elif op_cf is not None and capex is not None:
        fcf = []
        for col in quarters_sorted:
            o, c = op_cf.get(col), capex.get(col)
            fcf.append(round(float(o) + float(c), 2) if pd.notna(o) and pd.notna(c) else None)  # capex is already negative
    else:
        fcf = [None] * len(quarters_sorted)

    total_debt = _get_row(qbs, "Total Debt")
    cash = _get_row(qbs, "Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments")
    if total_debt is not None and cash is not None:
        net_debt = []
        for col in quarters_sorted:
            d, c = total_debt.get(col), cash.get(col)
            net_debt.append(round(float(d) - float(c), 2) if pd.notna(d) and pd.notna(c) else None)
    else:
        net_debt = [None] * len(quarters_sorted)

    shares_row = _get_row(qbs, "Ordinary Shares Number", "Share Issued")
    shares_outstanding = _series_to_chart(shares_row, quarters_sorted) if shares_row is not None else [None] * len(quarters_sorted)

    # EPS actual vs estimate ("earnings surprises")
    eps_actual, eps_estimate, eps_surprise_pct, eps_labels = [], [], [], []
    try:
        edates = tk.get_earnings_dates(limit=20)
        if edates is not None and not edates.empty:
            edates = edates.dropna(subset=["Reported EPS"]).sort_index()
            for idx, row in edates.iterrows():
                eps_labels.append(idx.strftime("%b %Y"))
                eps_actual.append(round(float(row.get("Reported EPS")), 2) if pd.notna(row.get("Reported EPS")) else None)
                eps_estimate.append(round(float(row.get("EPS Estimate")), 2) if pd.notna(row.get("EPS Estimate")) else None)
                surprise = row.get("Surprise(%)")
                eps_surprise_pct.append(round(float(surprise) * 100, 1) if pd.notna(surprise) else None)
    except Exception as e:
        print(f"[warn] earnings dates fetch failed for {ticker}: {e}")

    pe_band = _compute_pe_band(tk, qf)

    return {
        "ticker": ticker,
        "has_fundamentals": True,
        "quarter_labels": quarter_labels,
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "gross_margin_pct": gross_margin_pct,
        "net_debt": net_debt,
        "shares_outstanding": shares_outstanding,
        "eps_labels": eps_labels,
        "eps_actual": eps_actual,
        "eps_estimate": eps_estimate,
        "eps_surprise_pct": eps_surprise_pct,
        "pe_band": pe_band,
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
    }


_fundamentals_cache = {}  # ticker -> (timestamp, data)
FUNDAMENTALS_CACHE_TTL_SEC = 900  # 15 min - fundamentals change slowly, and this avoids
                                  # hammering yfinance every time a stock drawer reopens


@app.get("/api/fundamentals/{ticker}")
def get_fundamentals(ticker: str):
    ticker = ticker.upper().strip()

    cached = _fundamentals_cache.get(ticker)
    if cached and (time.time() - cached[0]) < FUNDAMENTALS_CACHE_TTL_SEC:
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
