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
import sqlite3
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yfinance as yf

DB_PATH = os.environ.get("SWING_DB_PATH", "swing_dashboard.db")

app = FastAPI(title="Swing Desk API")

app.add_middleware(
    CORSMiddleware,
    # SECURITY: only your own site is allowed to talk to this API.
    # Once you have your real Railway/Vercel/Cloudflare Pages address,
    # replace the placeholder below with it. Until then, "*" (open to
    # everyone) is kept as a safe default so local testing still works.
    allow_origins=["*"],   # <-- replace with e.g. ["https://your-site.up.railway.app"]
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/stocks")
def get_stocks(limit: int = Query(default=25, le=100)):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM scanned_stocks
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        d = dict(r)
        # resistance_targets is stored as a JSON string - parse it back to a list
        try:
            d["resistance_targets"] = json.loads(d["resistance_targets"] or "[]")
        except (json.JSONDecodeError, TypeError):
            d["resistance_targets"] = []
        result.append(d)
    return result


@app.get("/api/macro-news")
def get_macro_news(limit: int = Query(default=25, le=100)):
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT * FROM macro_news
        ORDER BY timestamp DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/ui-strings")
def get_ui_strings(lang: str = Query(default="he", pattern="^(he|en)$")):
    conn = get_conn()
    rows = conn.execute("SELECT string_key, he, en FROM ui_strings").fetchall()
    conn.close()
    return {r["string_key"]: r[lang] for r in rows}


@app.get("/api/health")
def health():
    return {"status": "ok", "db_path": DB_PATH}


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
    conn.execute(
        "INSERT INTO analytics_events (event_type, entity_id, session_id) VALUES (?, ?, ?)",
        (event.event_type, event.entity_id, event.session_id),
    )
    conn.commit()
    conn.close()
    return {"status": "logged"}


@app.get("/api/analytics/top-interest")
def top_interest(
    event_type: str = Query(default="view_stock", pattern="^(view_stock|view_macro|page_view|search_stock)$"),
    days: int = 7,
    limit: int = 10,
):
    """Which tickers/news categories got the most clicks in the last N days."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT entity_id, COUNT(*) as clicks
        FROM analytics_events
        WHERE event_type = ? AND timestamp >= datetime('now', ?)
        GROUP BY entity_id
        ORDER BY clicks DESC
        LIMIT ?
        """,
        (event_type, f"-{days} days", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/analytics/summary")
def analytics_summary(days: int = 7):
    """Quick daily event-count summary, useful for a simple internal chart."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT date(timestamp) as day, event_type, COUNT(*) as count
        FROM analytics_events
        WHERE timestamp >= datetime('now', ?)
        GROUP BY day, event_type
        ORDER BY day DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
