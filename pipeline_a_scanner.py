"""
Pipeline A - Swing Trading Scanner
==================================
Runs once daily, ~23:00 Israel time (after US market close).

Flow:
  1. Universe filter   -> liquid, mid/large-cap NYSE/NASDAQ stocks only (anti pump&dump)
  2. Technical filter  -> descending-trendline breakout / continuation pattern breakout
  3. Volume filter     -> breakout candle volume >= 40% above 20-day average volume
  4. Social fusion     -> pull recent posts (X / Reddit / Stocktwits), measure mention spike
  5. AI summary        -> OpenAI call with a STRICTLY factual/analytical prompt
                          (no buy/sell recommendations - see LEGAL NOTE below)
  6. DB upsert         -> scanned_stocks table

LEGAL NOTE
----------
Per project requirements, this system must never output direct buy/sell signals
or trade recommendations. All AI-generated text is instructed to stay factual
and descriptive (e.g. "the asset is drawing renewed interest" instead of
"the asset is a buy"). See disclaimer.md for the standard disclaimer text
to display on the site.

Requirements (pip install --break-system-packages):
    yfinance pandas numpy openai praw requests python-dotenv
"""

import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

# ------------------------------------------------------------------
# Config
# ------------------------------------------------------------------

DB_PATH = os.environ.get("SWING_DB_PATH", "swing_dashboard.db")

# Universe to scan. This starter list covers major large/mid-cap names across
# several sectors so the live scan doesn't come back too sparse. For full
# market coverage, replace this with a complete NYSE/NASDAQ ticker list pulled
# from an exchange listing API (e.g. Financial Modeling Prep's /stock/list
# endpoint, or Polygon's /v3/reference/tickers) - that requires its own
# (free-tier) API key.
DEFAULT_UNIVERSE = [
    # Software / Cloud
    "MSFT", "CRM", "NOW", "SNOW", "PANW", "CRWD", "DDOG", "NET", "IGV",
    # Semiconductors / Hardware
    "NVDA", "AMD", "AVGO", "SMH", "MU", "QCOM",
    # Consumer / E-commerce
    "AMZN", "SHOP", "ABNB", "UBER", "SBUX",
    # Fintech / Crypto-adjacent
    "SQ", "COIN", "PYPL", "MSTR",
    # AI / Data
    "PLTR", "AI", "GOOGL", "META",
    # Industrials / Energy
    "CAT", "XLE", "XOM",
]

MIN_AVG_VOLUME_20D = 1_500_000       # shares
MIN_PRICE = 10.0                      # USD
MIN_MARKET_CAP = 1_500_000_000        # USD -- anti pump & dump filter
ALLOWED_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM"}  # yfinance exchange codes for NASDAQ/NYSE variants
BREAKOUT_VOLUME_THRESHOLD_PCT = 40.0   # breakout candle must be >= 40% above 20d avg vol
TRENDLINE_LOOKBACK_DAYS = 40           # window used to fit the descending trendline


@dataclass
class ScanResult:
    ticker: str
    trigger_text_en: str
    trigger_text_he: str
    swing_score: int
    entry_price: float
    support_level: float
    resistance_targets: List[float]
    social_volume_spike_pct: float
    market_cap: float
    avg_volume_20d: float
    breakout_volume_pct: float
    exchange: str
    ai_summary_en: Optional[str] = None
    ai_summary_he: Optional[str] = None


# ------------------------------------------------------------------
# Step 1+2+3: Liquidity + Technical + Volume filters
# ------------------------------------------------------------------

def passes_liquidity_and_cap_filter(ticker: str, info: dict, hist: pd.DataFrame) -> bool:
    """Anti pump-and-dump gate: real exchange, real size, real liquidity."""
    if hist.empty or len(hist) < TRENDLINE_LOOKBACK_DAYS:
        return False

    price = hist["Close"].iloc[-1]
    avg_vol_20d = hist["Volume"].tail(20).mean()
    market_cap = info.get("marketCap") or 0
    exchange = info.get("exchange", "")

    if price < MIN_PRICE:
        return False
    if avg_vol_20d < MIN_AVG_VOLUME_20D:
        return False
    if market_cap < MIN_MARKET_CAP:
        return False
    if exchange not in ALLOWED_EXCHANGES:
        return False
    return True


def detect_descending_trendline_breakout(hist: pd.DataFrame) -> Optional[dict]:
    """
    Heuristic descending-trendline breakout detector.

    Method:
      1. Take the last TRENDLINE_LOOKBACK_DAYS daily highs.
      2. Find local swing highs (a bar higher than its 2 neighbors on each side).
      3. Fit a line through the swing highs -> this is the "lower highs" trendline.
      4. If today's close is above the trendline's extrapolated value AND the
         trendline slope is negative (confirming it was actually descending),
         flag a breakout.

    This is intentionally simple for an MVP. For production, consider a
    dedicated TA library (e.g. `scipy.signal.argrelextrema` for swing
    detection, or a pattern-recognition library) for more robust detection.
    """
    window = hist.tail(TRENDLINE_LOOKBACK_DAYS).reset_index(drop=True)
    highs = window["High"].values

    swing_idx, swing_val = [], []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and \
           highs[i] > highs[i + 1] and highs[i] > highs[i + 2]:
            swing_idx.append(i)
            swing_val.append(highs[i])

    if len(swing_idx) < 2:
        return None  # not enough structure to define a trendline

    slope, intercept = np.polyfit(swing_idx, swing_val, 1)
    if slope >= 0:
        return None  # highs are rising, not descending -> not this pattern

    today_idx = len(window) - 1
    trendline_value_today = slope * today_idx + intercept
    today_close = window["Close"].iloc[-1]

    if today_close > trendline_value_today:
        return {
            "trendline_value": float(trendline_value_today),
            "breakout_price": float(today_close),
        }
    return None


def check_breakout_volume(hist: pd.DataFrame) -> Optional[float]:
    """Returns the % by which today's volume exceeds the 20d average, or None."""
    avg_vol_20d = hist["Volume"].iloc[-21:-1].mean()  # 20 days prior to today
    today_vol = hist["Volume"].iloc[-1]
    if avg_vol_20d <= 0:
        return None
    pct_above = ((today_vol - avg_vol_20d) / avg_vol_20d) * 100
    if pct_above >= BREAKOUT_VOLUME_THRESHOLD_PCT:
        return round(pct_above, 1)
    return None


# ------------------------------------------------------------------
# Step 4: Social & sentiment data collection (stubs w/ clear TODOs)
# ------------------------------------------------------------------

def fetch_social_posts(ticker: str, limit: int = 50) -> List[str]:
    """
    Pulls the latest posts mentioning `ticker` from X, Reddit, Stocktwits.

    TODO before going live, fill in real credentials:
      - X (Twitter) API v2: requires a developer account + bearer token.
        https://developer.x.com/en/docs/x-api
      - Reddit: use PRAW with a registered app (client_id/secret).
        https://praw.readthedocs.io/
      - Stocktwits: public REST endpoint, lightly rate-limited:
        https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json

    Returns a flat list of post text bodies (max `limit`).
    """
    posts: List[str] = []

    # --- Stocktwits (simplest - no auth needed for the public endpoint) ---
    try:
        import requests
        resp = requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json",
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            for msg in data.get("messages", [])[:limit]:
                posts.append(msg.get("body", ""))
    except Exception as e:
        print(f"[warn] Stocktwits fetch failed for {ticker}: {e}")

    # --- Reddit (requires praw + credentials) ---
    # import praw
    # reddit = praw.Reddit(
    #     client_id=os.environ["REDDIT_CLIENT_ID"],
    #     client_secret=os.environ["REDDIT_CLIENT_SECRET"],
    #     user_agent="swing-dashboard/0.1",
    # )
    # for submission in reddit.subreddit("stocks+investing").search(ticker, limit=limit):
    #     posts.append(submission.title + " " + (submission.selftext or ""))

    # --- X / Twitter (requires a developer bearer token) ---
    # headers = {"Authorization": f"Bearer {os.environ['X_BEARER_TOKEN']}"}
    # resp = requests.get(
    #     "https://api.x.com/2/tweets/search/recent",
    #     params={"query": f"${ticker}", "max_results": min(limit, 100)},
    #     headers=headers,
    # )
    # posts += [t["text"] for t in resp.json().get("data", [])]

    return posts[:limit]


def compute_social_volume_spike(ticker: str, current_24h_count: int, baseline_30d_avg: float) -> float:
    """% jump in mentions vs the 30-day daily average baseline."""
    if baseline_30d_avg <= 0:
        return 0.0
    return round(((current_24h_count - baseline_30d_avg) / baseline_30d_avg) * 100, 1)


# ------------------------------------------------------------------
# Step 5: AI summary generation (OpenAI) - legally-safe prompt
# ------------------------------------------------------------------

SENTIMENT_SYSTEM_PROMPT = """You are a financial content assistant for a swing-trading dashboard.
You must NEVER issue a buy/sell recommendation, price target, or any instruction to trade.
Only describe what is observably happening (price action, volume, community attention) using
neutral, factual, analytical language. Acceptable phrasing style: "the asset is drawing renewed
attention", "a positive trend was observed", "community discussion has increased". Do NOT use
phrasing like "buy", "sell", "you should", "recommended", or give price targets framed as advice.
"""

def build_social_sentiment_prompt(ticker: str, posts: List[str], target_language: str) -> str:
    joined_posts = "\n".join(f"- {p}" for p in posts if p.strip())[:6000]
    return f"""Analyze the sentiment of these traders regarding ticker {ticker}.
Provide a score from 1-100 (100 = extreme bullishness) based purely on the tone of the posts.
Write a condensed, FACTUAL and ANALYTICAL summary of up to 40 words in {target_language}
explaining what the community narrative is and why attention has increased. Do not include
fluff, and do not phrase anything as a recommendation to buy or sell. Return strict JSON:
{{"score": <int>, "summary": "<string>"}}

Posts:
{joined_posts}
"""


def call_openai_sentiment(ticker: str, posts: List[str], target_language: str) -> dict:
    """
    Calls OpenAI's API to score + summarize sentiment.
    Requires OPENAI_API_KEY in the environment.
    """
    from openai import OpenAI
    client = OpenAI()  # reads OPENAI_API_KEY from env

    user_prompt = build_social_sentiment_prompt(ticker, posts, target_language)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.3,
    )
    content = response.choices[0].message.content
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"score": 50, "summary": ""}


# ------------------------------------------------------------------
# Step 6: DB upsert
# ------------------------------------------------------------------

def init_db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    with open(os.path.join(os.path.dirname(__file__), "database_schema.sql")) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def upsert_scan_result(conn: sqlite3.Connection, result: ScanResult):
    conn.execute(
        """
        INSERT INTO scanned_stocks (
            ticker, trigger_text_he, trigger_text_en, swing_score,
            entry_price, support_level, resistance_targets,
            social_volume_spike_pct, ai_summary_he, ai_summary_en,
            market_cap, avg_volume_20d, breakout_volume_pct, exchange, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.ticker,
            result.trigger_text_he,
            result.trigger_text_en,
            result.swing_score,
            result.entry_price,
            result.support_level,
            json.dumps(result.resistance_targets),
            result.social_volume_spike_pct,
            result.ai_summary_he,
            result.ai_summary_en,
            result.market_cap,
            result.avg_volume_20d,
            result.breakout_volume_pct,
            result.exchange,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def scan_universe(universe: List[str] = None, target_language: str = "he") -> List[ScanResult]:
    universe = universe or DEFAULT_UNIVERSE
    results: List[ScanResult] = []

    for ticker in universe:
        try:
            tk = yf.Ticker(ticker)
            hist = tk.history(period="6mo", interval="1d")
            info = tk.info
        except Exception as e:
            print(f"[warn] failed to fetch {ticker}: {e}")
            continue

        if not passes_liquidity_and_cap_filter(ticker, info, hist):
            continue

        breakout = detect_descending_trendline_breakout(hist)
        if breakout is None:
            continue

        vol_pct = check_breakout_volume(hist)
        if vol_pct is None:
            continue

        # --- social fusion ---
        posts = fetch_social_posts(ticker)
        sentiment = call_openai_sentiment(ticker, posts, "Hebrew" if target_language == "he" else "English")
        sentiment_en = sentiment if target_language == "en" else \
            call_openai_sentiment(ticker, posts, "English")
        sentiment_he = sentiment if target_language == "he" else \
            call_openai_sentiment(ticker, posts, "Hebrew")

        close = float(hist["Close"].iloc[-1])
        support = float(hist["Low"].tail(20).min())
        resistance = [round(close * 1.05, 2), round(close * 1.10, 2)]  # placeholder targets

        result = ScanResult(
            ticker=ticker,
            trigger_text_en="Breakout above descending trendline on strong volume",
            trigger_text_he="פריצת שיאים יורדים בווליום חזק",
            swing_score=int(sentiment_he.get("score", 50)),
            entry_price=close,
            support_level=support,
            resistance_targets=resistance,
            social_volume_spike_pct=0.0,  # fill in once compute_social_volume_spike has real baseline data
            market_cap=float(info.get("marketCap") or 0),
            avg_volume_20d=float(hist["Volume"].tail(20).mean()),
            breakout_volume_pct=vol_pct,
            exchange=info.get("exchange", ""),
            ai_summary_en=sentiment_en.get("summary", ""),
            ai_summary_he=sentiment_he.get("summary", ""),
        )
        results.append(result)

    return results


def main():
    conn = init_db()
    results = scan_universe()
    for r in results:
        upsert_scan_result(conn, r)
    print(f"Scan complete. {len(results)} tickers flagged and saved to {DB_PATH}.")
    conn.close()


if __name__ == "__main__":
    main()
