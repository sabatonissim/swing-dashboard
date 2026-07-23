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
import time
import traceback
import psycopg2
import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

DB_URL = os.environ.get("SWING_DB_PATH") or os.environ.get("DATABASE_URL")

# Universe to scan. This starter list covers major large/mid-cap names across
# several sectors so the live scan doesn't come back too sparse. For full
# market coverage, replace this with a complete NYSE/NASDAQ ticker list pulled
# from an exchange listing API (e.g. Financial Modeling Prep's /stock/list
# endpoint, or Polygon's /v3/reference/tickers) - that requires its own
# (free-tier) API key.
DEFAULT_UNIVERSE = [
    # Software / Cloud
    "MSFT", "CRM", "NOW", "SNOW", "PANW", "CRWD", "DDOG", "NET", "IGV",
    "OKTA", "ZS", "WDAY", "ADBE", "INTU", "TEAM", "HUBS", "MDB", "S",
    # Semiconductors
    "NVDA", "AMD", "AVGO", "SMH", "MU", "QCOM", "AMAT", "LRCX", "KLAC",
    "ARM", "ON", "MRVL", "TXN",
    # Consumer / E-commerce
    "AMZN", "SHOP", "ABNB", "UBER", "SBUX", "MELI", "CMG", "LULU", "DASH",
    "BKNG", "NKE",
    # Fintech
    "XYZ", "COIN", "PYPL", "AFRM", "HOOD", "SOFI",
    # AI / Data / Big Tech
    "PLTR", "GOOGL", "META", "AAPL", "MSTR", "IONQ",
    # Biotech / Health
    "MRNA", "ISRG", "DXCM", "LLY", "VRTX", "REGN",
    # Energy
    "XLE", "XOM", "CVX", "OXY", "SLB",
    # Industrials / Defense
    "CAT", "DE", "LMT", "RTX", "BA", "GE",
    # Financials / Banks
    "JPM", "GS", "MS", "SCHW", "XLF",
    # Airlines / Travel / Homebuilders
    "DAL", "LEN", "DHI", "NVR",
    # Retail
    "WMT", "COST", "TGT", "HD",
    # ETFs
    "QQQ", "SPY", "ARKK", "IWM", "XLK", "XBI",
]

MIN_AVG_VOLUME_20D = 1_500_000       # shares
MIN_PRICE = 10.0                      # USD
MIN_MARKET_CAP = 1_500_000_000        # USD -- anti pump & dump filter
ALLOWED_EXCHANGES = {"NMS", "NYQ", "NGM", "NCM"}  # yfinance exchange codes for NASDAQ/NYSE variants
BREAKOUT_VOLUME_THRESHOLD_PCT = 20.0   # lowered: breakout candle >= 20% above 20d avg vol
TRENDLINE_LOOKBACK_DAYS = 60           # wider window to catch more patterns
PER_TICKER_TIMEOUT_SEC = 15            # hard cap so a single hung yfinance call can't hang the whole container
INTER_TICKER_DELAY_SEC = 0.3           # small delay to avoid Yahoo Finance rate-limiting on a large universe


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
    change_pct: float = 0.0
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


def detect_52w_high_breakout(hist: pd.DataFrame) -> Optional[dict]:
    """Detects when price breaks above its 52-week high — very bullish signal."""
    if len(hist) < 252:
        return None
    week52_high = hist["High"].iloc[-252:-1].max()
    today_close = float(hist["Close"].iloc[-1])
    if today_close > week52_high:
        return {"breakout_price": today_close, "week52_high": float(week52_high)}
    return None


def detect_momentum_surge(hist: pd.DataFrame) -> Optional[dict]:
    """Detects a strong 5-day momentum surge with rising volume."""
    if len(hist) < 25:
        return None
    close_5d_ago = float(hist["Close"].iloc[-6])
    close_today = float(hist["Close"].iloc[-1])
    pct_change = ((close_today - close_5d_ago) / close_5d_ago) * 100
    avg_vol = hist["Volume"].iloc[-21:-1].mean()
    recent_vol = hist["Volume"].iloc[-5:].mean()
    vol_surge = ((recent_vol - avg_vol) / avg_vol) * 100 if avg_vol > 0 else 0
    if pct_change >= 5.0 and vol_surge >= 15.0:
        return {"pct_change_5d": round(pct_change, 1), "vol_surge_pct": round(vol_surge, 1)}
    return None


def check_breakout_volume(hist: pd.DataFrame) -> float:
    """Returns the % by which today's volume exceeds the 20d average (0 if below)."""
    avg_vol_20d = hist["Volume"].iloc[-21:-1].mean()
    today_vol = hist["Volume"].iloc[-1]
    if avg_vol_20d <= 0:
        return 0.0
    pct_above = ((today_vol - avg_vol_20d) / avg_vol_20d) * 100
    return round(max(0.0, pct_above), 1)


def detect_cup_and_handle(hist: pd.DataFrame) -> Optional[dict]:
    """
    Cup & Handle heuristic:
    - Left rim: local high in the first third of the window
    - Cup bottom: price drops at least 15% from left rim, then recovers
    - Right rim: price recovers to within 5% of left rim high
    - Handle: last 5-15 bars consolidate (range < 8% of cup depth)
    - Breakout: today's close above the right rim
    Uses a 60-bar window (approx 3 months daily).
    """
    if len(hist) < 65:
        return None
    window = hist.tail(65).reset_index(drop=True)
    closes = window["Close"].values
    n = len(closes)

    # left rim = highest close in first third
    left_rim = closes[:n//3].max()
    left_rim_idx = int(np.argmax(closes[:n//3]))

    # cup bottom = lowest close after left rim
    cup_section = closes[left_rim_idx:]
    if len(cup_section) < 20:
        return None
    cup_bottom = cup_section.min()
    cup_depth = left_rim - cup_bottom
    if cup_depth / left_rim < 0.15:  # must drop at least 15%
        return None

    # right rim = recovery to within 5% of left rim
    right_section = closes[left_rim_idx + int(len(cup_section) * 0.4):]
    if len(right_section) < 5:
        return None
    right_rim = right_section.max()
    if right_rim < left_rim * 0.95:  # must recover to within 5% of left rim
        return None

    # handle = last 5-15 bars consolidate tightly
    handle = closes[-12:]
    handle_range = (handle.max() - handle.min()) / right_rim
    if handle_range > 0.08:  # handle consolidation should be tight
        return None

    # breakout = today close above right rim
    today_close = closes[-1]
    if today_close >= right_rim:
        return {"left_rim": round(float(left_rim), 2), "cup_bottom": round(float(cup_bottom), 2)}
    return None


def detect_bull_flag(hist: pd.DataFrame) -> Optional[dict]:
    """
    Bull Flag heuristic:
    - Pole: strong rally of 8%+ over 5-15 bars
    - Flag: tight consolidation (pullback < 50% of pole) over 5-20 bars
    - Breakout: today's close breaks above the flag's upper boundary
    """
    if len(hist) < 30:
        return None
    closes = hist["Close"].values

    # scan for a pole: find the best 10-bar rally in the last 50 bars
    best_pole_pct = 0.0
    best_pole_end = -1
    scan = closes[-50:]
    for i in range(5, len(scan) - 5):
        for pole_len in [5, 7, 10, 12, 15]:
            if i - pole_len < 0:
                continue
            pct = (scan[i] - scan[i - pole_len]) / scan[i - pole_len] * 100
            if pct > best_pole_pct:
                best_pole_pct = pct
                best_pole_end = i

    if best_pole_pct < 8.0 or best_pole_end < 0:
        return None

    # flag = consolidation after the pole top
    pole_top = scan[best_pole_end]
    flag_section = scan[best_pole_end:]
    if len(flag_section) < 5:
        return None

    flag_low = flag_section.min()
    flag_high = flag_section.max()
    pullback_pct = (pole_top - flag_low) / pole_top * 100

    # pullback should be less than 50% of the pole
    if pullback_pct > best_pole_pct * 0.5:
        return None

    # breakout: today's close above the flag high
    today_close = float(closes[-1])
    if today_close >= flag_high and len(flag_section) >= 5:
        return {"pole_pct": round(best_pole_pct, 1), "pullback_pct": round(pullback_pct, 1)}
    return None


def detect_ascending_triangle(hist: pd.DataFrame) -> Optional[dict]:
    """
    Ascending Triangle heuristic:
    - Flat resistance: top 3 highs in last 40 bars within 2% of each other
    - Rising support: each successive low is higher than the previous
    - Breakout: today's close above the flat resistance
    """
    if len(hist) < 45:
        return None
    window = hist.tail(45).reset_index(drop=True)
    highs = window["High"].values
    lows = window["Low"].values
    closes = window["Close"].values

    # find local swing highs
    swing_highs = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and \
           highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append(highs[i])

    if len(swing_highs) < 3:
        return None

    # check flat resistance: top 3 highs within 2%
    top3 = sorted(swing_highs)[-3:]
    resistance = np.mean(top3)
    if (max(top3) - min(top3)) / resistance > 0.02:
        return None

    # check rising lows: find swing lows and verify they're rising
    swing_lows = []
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and \
           lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append(lows[i])

    if len(swing_lows) < 2:
        return None
    if swing_lows[-1] <= swing_lows[-2]:  # lows must be rising
        return None

    # breakout above resistance
    today_close = float(closes[-1])
    if today_close > resistance:
        return {"resistance": round(float(resistance), 2)}
    return None


def detect_golden_cross(hist: pd.DataFrame) -> Optional[dict]:
    """
    Golden Cross heuristic:
    - 50-day SMA crosses above the 200-day SMA within the last 3 bars.
    - Classic long-term bullish trend-change signal.
    """
    if len(hist) < 210:
        return None
    closes = hist["Close"]
    sma50 = closes.rolling(50).mean()
    sma200 = closes.rolling(200).mean()
    if sma50.isna().iloc[-4:].any() or sma200.isna().iloc[-4:].any():
        return None

    diff_today = float(sma50.iloc[-1] - sma200.iloc[-1])
    # look back up to 3 bars for the actual cross-over point
    for lookback in range(0, 3):
        prev = float(sma50.iloc[-2 - lookback] - sma200.iloc[-2 - lookback])
        if prev <= 0 and diff_today > 0:
            return {
                "sma50": round(float(sma50.iloc[-1]), 2),
                "sma200": round(float(sma200.iloc[-1]), 2),
            }
    return None


def detect_double_bottom(hist: pd.DataFrame) -> Optional[dict]:
    """
    Double Bottom ("W") heuristic:
    - Two swing lows in the last ~60 bars within 3% of each other.
    - A peak (the "neckline") between them, at least 8% above the lows.
    - Breakout: today's close above the neckline.
    """
    if len(hist) < 60:
        return None
    window = hist.tail(60).reset_index(drop=True)
    lows = window["Low"].values
    closes = window["Close"].values
    n = len(lows)

    swing_idx, swing_val = [], []
    for i in range(3, n - 3):
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and \
           lows[i] < lows[i + 1] and lows[i] < lows[i + 2]:
            swing_idx.append(i)
            swing_val.append(lows[i])

    if len(swing_idx) < 2:
        return None

    # take the two lowest swing lows, far enough apart to be distinct bottoms
    pairs = sorted(zip(swing_idx, swing_val), key=lambda p: p[1])
    first_idx, first_low = pairs[0]
    second = next(((i, v) for i, v in pairs[1:] if abs(i - first_idx) >= 8), None)
    if second is None:
        return None
    second_idx, second_low = second
    lo_idx, hi_idx = sorted([first_idx, second_idx])
    lo_low, hi_low = (first_low, second_low) if first_idx < second_idx else (second_low, first_low)

    if abs(lo_low - hi_low) / max(lo_low, hi_low) > 0.03:
        return None  # bottoms must be roughly equal depth

    neckline = float(closes[lo_idx:hi_idx + 1].max())
    if lo_low <= 0 or (neckline - lo_low) / lo_low < 0.08:
        return None  # not enough of a bounce between the two bottoms

    today_close = float(closes[-1])
    if today_close > neckline:
        return {"neckline": round(neckline, 2), "bottom_price": round(float(min(lo_low, hi_low)), 2)}
    return None


def _macd_lines(closes: pd.Series):
    ema12 = closes.ewm(span=12, adjust=False).mean()
    ema26 = closes.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd, signal


def detect_macd_bullish_crossover(hist: pd.DataFrame) -> Optional[dict]:
    """
    MACD Bullish Crossover:
    - The MACD line crosses above its signal line within the last 2 bars.
    - Standard 12/26/9 EMA settings.
    """
    if len(hist) < 40:
        return None
    macd, signal = _macd_lines(hist["Close"])
    diff_today = float(macd.iloc[-1] - signal.iloc[-1])
    diff_yesterday = float(macd.iloc[-2] - signal.iloc[-2])
    if diff_yesterday <= 0 and diff_today > 0:
        return {"macd": round(float(macd.iloc[-1]), 3), "signal": round(float(signal.iloc[-1]), 3)}
    return None


def _rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def detect_rsi_oversold_bounce(hist: pd.DataFrame) -> Optional[dict]:
    """
    RSI Oversold Bounce:
    - RSI(14) dipped below 30 at some point in the last 10 bars.
    - Today RSI closes back above 30, with price ticking up -> early reversal signal.
    """
    if len(hist) < 30:
        return None
    rsi = _rsi(hist["Close"])
    recent = rsi.tail(11)
    if not (recent.iloc[:-1] < 30).any():
        return None
    if rsi.iloc[-1] <= 30:
        return None
    today_close = float(hist["Close"].iloc[-1])
    yesterday_close = float(hist["Close"].iloc[-2])
    if today_close > yesterday_close:
        return {"rsi": round(float(rsi.iloc[-1]), 1)}
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

def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
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
    """)
    # ADD COLUMN IF NOT EXISTS patches a table that was already created
    # before this column existed (CREATE TABLE IF NOT EXISTS above is a
    # no-op once the table exists, so this is needed for already-deployed DBs).
    cur.execute("ALTER TABLE scanned_stocks ADD COLUMN IF NOT EXISTS change_pct REAL;")
    conn.commit()
    cur.close()
    conn.close()


def upsert_scan_result(conn, result):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO scanned_stocks (
            ticker, trigger_text_he, trigger_text_en, swing_score,
            entry_price, support_level, resistance_targets,
            social_volume_spike_pct, ai_summary_he, ai_summary_en,
            market_cap, avg_volume_20d, breakout_volume_pct, exchange,
            change_pct, timestamp
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            result.ticker, result.trigger_text_he, result.trigger_text_en,
            result.swing_score, result.entry_price, result.support_level,
            json.dumps(result.resistance_targets), result.social_volume_spike_pct,
            result.ai_summary_he, result.ai_summary_en, result.market_cap,
            result.avg_volume_20d, result.breakout_volume_pct, result.exchange,
            result.change_pct, datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    cur.close()


# ------------------------------------------------------------------
# Resilient data fetch — a single hung yfinance call (Yahoo rate-limit,
# network stall, etc.) must never be able to hang the whole container.
# Every fetch runs in a worker thread with a hard wall-clock timeout;
# if it doesn't return in time we abandon it and move to the next ticker.
# ------------------------------------------------------------------

def _fetch_ticker_data(ticker: str):
    tk = yf.Ticker(ticker)
    hist = tk.history(period="6mo", interval="1d")
    info = tk.info
    return hist, info


def fetch_ticker_data_with_timeout(ticker: str, timeout: int = PER_TICKER_TIMEOUT_SEC):
    """Runs the yfinance fetch in a worker thread with a hard timeout.
    Raises on failure or timeout — caller is expected to catch and skip.
    Uses shutdown(wait=False) so a hung request doesn't block us waiting
    for it to finish; the orphaned thread is abandoned and cleaned up
    by the interpreter once it eventually returns (or never does)."""
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_fetch_ticker_data, ticker)
        return future.result(timeout=timeout)  # raises FutureTimeoutError if it hangs
    finally:
        executor.shutdown(wait=False)


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def scan_universe(universe: List[str] = None, target_language: str = "he") -> List[ScanResult]:
    universe = universe or DEFAULT_UNIVERSE
    results: List[ScanResult] = []

    for ticker in universe:
        try:
            hist, info = fetch_ticker_data_with_timeout(ticker)
        except FutureTimeoutError:
            print(f"[warn] {ticker}: timed out after {PER_TICKER_TIMEOUT_SEC}s, skipping")
            continue
        except Exception as e:
            print(f"[warn] failed to fetch {ticker}: {e}")
            continue
        finally:
            time.sleep(INTER_TICKER_DELAY_SEC)  # be gentle with Yahoo Finance's rate limits

        if not passes_liquidity_and_cap_filter(ticker, info, hist):
            continue

        # Run all pattern detectors
        trendline_break  = detect_descending_trendline_breakout(hist)
        high_52w         = detect_52w_high_breakout(hist)
        momentum         = detect_momentum_surge(hist)
        cup_handle       = detect_cup_and_handle(hist)
        bull_flag        = detect_bull_flag(hist)
        asc_triangle     = detect_ascending_triangle(hist)
        golden_cross     = detect_golden_cross(hist)
        double_bottom    = detect_double_bottom(hist)
        macd_cross       = detect_macd_bullish_crossover(hist)
        rsi_bounce       = detect_rsi_oversold_bounce(hist)

        vol_pct = check_breakout_volume(hist)

        # Volume confirmation gate: a genuine breakout should show real
        # buying volume behind it. Without this, "breakout" patterns were
        # being flagged even on weak/below-average volume, which is a
        # classic false-breakout setup. Trend/momentum signals (golden
        # cross, MACD cross, RSI bounce) don't require the full breakout
        # threshold since they aren't breakout patterns by nature.
        volume_confirmed = vol_pct >= BREAKOUT_VOLUME_THRESHOLD_PCT
        if not volume_confirmed:
            cup_handle = None
            double_bottom = None
            high_52w = None
            bull_flag = None
            asc_triangle = None
            trendline_break = None

        # Skip if no pattern found at all
        if not any([trendline_break, high_52w, momentum, cup_handle, bull_flag,
                    asc_triangle, golden_cross, double_bottom, macd_cross, rsi_bounce]):
            continue

        close   = float(hist["Close"].iloc[-1])
        support = float(hist["Low"].tail(20).min())
        resistance = [round(close * 1.05, 2), round(close * 1.10, 2)]
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        change_pct = round(((close - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

        # Priority: strongest/rarest patterns get highest score
        if cup_handle:
            trigger_en = "Cup & Handle breakout"
            trigger_he = "פריצת תבנית כוס וידית (Cup & Handle)"
            tech_score = min(100, 80 + int(vol_pct / 5))
        elif double_bottom:
            trigger_en = f"Double Bottom breakout above ${double_bottom['neckline']}"
            trigger_he = f"פריצת תבנית תחתית כפולה (W) מעל ${double_bottom['neckline']}"
            tech_score = min(100, 79 + int(vol_pct / 5))
        elif high_52w:
            trigger_en = "Breaking above 52-week high"
            trigger_he = "פריצת שיא 52 שבועות"
            tech_score = min(100, 78 + int(vol_pct / 5))
        elif golden_cross:
            trigger_en = "Golden Cross (50D SMA crossed above 200D SMA)"
            trigger_he = "פריצת גולדן קרוס (ממוצע 50 יום חצה מעל ממוצע 200 יום)"
            tech_score = min(100, 74 + int(vol_pct / 5))
        elif bull_flag:
            trigger_en = f"Bull Flag breakout (pole +{bull_flag['pole_pct']}%)"
            trigger_he = f"פריצת דגל שורי (עמוד +{bull_flag['pole_pct']}%)"
            tech_score = min(100, 72 + int(vol_pct / 5))
        elif asc_triangle:
            trigger_en = f"Ascending Triangle breakout above ${asc_triangle['resistance']}"
            trigger_he = f"פריצת משולש עולה מעל ${asc_triangle['resistance']}"
            tech_score = min(100, 68 + int(vol_pct / 5))
        elif macd_cross:
            trigger_en = "MACD bullish crossover"
            trigger_he = "חציית MACD שורית"
            tech_score = min(100, 64 + int(vol_pct / 5))
        elif momentum:
            trigger_en = f"Strong momentum surge (+{momentum['pct_change_5d']}% / 5 days)"
            trigger_he = f"מומנטום חזק ב-5 ימים (+{momentum['pct_change_5d']}%)"
            tech_score = min(100, 62 + int(vol_pct / 5))
        elif rsi_bounce:
            trigger_en = f"RSI oversold bounce (RSI {rsi_bounce['rsi']})"
            trigger_he = f"התאוששות מאזור מכירת יתר (RSI {rsi_bounce['rsi']})"
            tech_score = min(100, 58 + int(vol_pct / 5))
        else:
            trigger_en = "Breakout above descending trendline on strong volume"
            trigger_he = "פריצת שיאים יורדים בווליום חזק"
            tech_score = min(100, 55 + int(vol_pct / 4))

        result = ScanResult(
            ticker=ticker,
            trigger_text_en=trigger_en,
            trigger_text_he=trigger_he,
            swing_score=tech_score,
            entry_price=close,
            support_level=support,
            resistance_targets=resistance,
            social_volume_spike_pct=0.0,
            market_cap=float(info.get("marketCap") or 0),
            avg_volume_20d=float(hist["Volume"].tail(20).mean()),
            breakout_volume_pct=vol_pct,
            exchange=info.get("exchange", ""),
            change_pct=change_pct,
            ai_summary_en=f"{trigger_en}. Volume {vol_pct:.0f}% above 20d average.",
            ai_summary_he=f"{trigger_he}. נפח גבוה ב-{vol_pct:.0f}% מהממוצע.",
        )
        results.append(result)

    return results



def main():
    init_db()
    conn = get_conn()
    try:
        results = scan_universe()
        for r in results:
            upsert_scan_result(conn, r)
        print(f"Scan complete. {len(results)} tickers flagged and saved to Postgres.")
    except Exception:
        # Make sure a crash is actually visible in the logs with a full
        # traceback, instead of the process just going quiet (which is
        # what made the last crash impossible to diagnose from the logs).
        print("[fatal] scan crashed:")
        print(traceback.format_exc())
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
