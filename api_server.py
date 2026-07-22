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
from typing import List, Optional

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
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM scanned_stocks ORDER BY timestamp DESC LIMIT %s", (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
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
# Analytics: "what interests people" (not general traffic stats -
# see DEPLOYMENT_GUIDE.md for that)
# ------------------------------------------------------------------

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
