"""
Pipeline A - Swing Trading Scanner
==================================
Runs once daily, ~23:00 Israel time (after US market close).

Flow:
  1. Universe filter   -> S&P 500 (fetched live from a public CSV) + a
                          curated list of liquid extras/ETFs not in the
                          index, then liquid/mid/large-cap NYSE/NASDAQ only
                          (anti pump&dump)
  2. Trend structure    -> SMA50/150/200 position + order + slope, classified
                          into a Weinstein-style Stage 1-4 (see
                          compute_trend_structure) — this is what lets the
                          scan tell a breakout in a genuine uptrend apart
                          from the same pattern firing in a downtrend/basing
                          stock, instead of pattern-matching in a vacuum
  3. Relative Strength   -> IBD/O'Neil-style RS Rating (1-99): weighted
                          performance vs SPY over the past year, ranked as a
                          percentile across the whole universe (see
                          compute_rs_raw / compute_rs_ratings). A technically
                          valid pattern on a market laggard scores lower than
                          the same pattern on a genuine relative leader.
  3. Technical filter   -> descending-trendline breakout / ascending-trendline
                          support bounce / continuation pattern breakout
                          (see detect_* functions below)
  4. Volatility         -> ATR(14), both absolute and as % of price
  5. Earnings awareness -> flags + score penalty if a report is due within
                          the next 7 days (info["earningsTimestamp"] — no
                          extra API call, already part of the per-ticker
                          info dict fetched for market cap/exchange)
  5. Volume filter      -> breakout candle volume >= 20% above 20-day average volume
  6. Support/resistance -> real swing-high/swing-low levels (see
                          compute_support_resistance), not an arbitrary % of price
  7. Social fusion       -> pull recent posts (X / Reddit / Stocktwits), measure mention spike
  8. AI summary          -> OpenAI call with a STRICTLY factual/analytical prompt
                          (no buy/sell recommendations - see LEGAL NOTE below)
  9. DB upsert           -> scanned_stocks table

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
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict

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
MAX_CONSECUTIVE_TIMEOUTS = 8           # circuit breaker: abort the scan early if Yahoo is clearly rate-limiting
                                        # hard right now, instead of piling up abandoned hung threads


def fetch_sp500_tickers() -> List[str]:
    """Pulls the current S&P 500 constituent list from a plain public CSV
    (free, no API key, updated whenever the index changes) — this is what
    takes the universe from ~90 hand-picked names to 500+.

    NOTE: this used to scrape Wikipedia's HTML table via pandas.read_html(),
    which needs lxml (or html5lib) installed to parse HTML. Adding lxml to
    requirements.txt is exactly what broke the Railway deploy — lxml often
    has to compile from source and can fail a build outright depending on
    what's available in the build image, which took down the *entire*
    deployment (API included), not just this one feature. A plain CSV needs
    none of that — pandas.read_csv has zero extra parsing dependencies.

    Falls back to just the curated DEFAULT_UNIVERSE below if the fetch ever
    fails (network issue, source unavailable, etc.) so a bad day for this
    one dependency can't zero out the whole scan.
    """
    try:
        import requests
        import io
        resp = requests.get(
            "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
            timeout=15,
        )
        resp.raise_for_status()
        table = pd.read_csv(io.StringIO(resp.text))
        symbols_col = table["Symbol"].astype(str).tolist()
        # yfinance uses '-' where this source uses '.' for share classes (e.g. BRK.B -> BRK-B)
        return [s.strip().replace(".", "-") for s in symbols_col if s.strip()]
    except Exception as e:
        print(f"[warn] failed to fetch S&P 500 list, using curated list only: {e}")
        return []


def fetch_nasdaq100_tickers() -> List[str]:
    """Same idea as fetch_sp500_tickers, for the Nasdaq-100 — used both to
    widen the scan universe and to tag each ticker's index membership for
    the heatmap feature (see SP500_SET/NASDAQ100_SET below)."""
    try:
        import requests
        import io
        resp = requests.get(
            "https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv",
            timeout=15,
        )
        resp.raise_for_status()
        table = pd.read_csv(io.StringIO(resp.text))
        col = "Symbol" if "Symbol" in table.columns else table.columns[0]
        symbols_col = table[col].astype(str).tolist()
        return [s.strip().replace(".", "-") for s in symbols_col if s.strip()]
    except Exception as e:
        print(f"[warn] failed to fetch Nasdaq-100 list: {e}")
        return []


# Populated once per scan run (see scan_universe) so every ticker's
# universe_movers row can be tagged with which index(es) it belongs to —
# this is what powers the S&P 500 / Nasdaq 100 heatmap toggle without
# needing any separate data fetch for that feature.
SP500_SET: set = set()
NASDAQ100_SET: set = set()


def build_scan_universe() -> List[str]:
    """S&P 500 (broad coverage) + Nasdaq 100 + the curated extras below
    (liquid names and ETFs that aren't in either index, like QQQ/IWM/ARKK
    or newer high-momentum names) — deduplicated."""
    global SP500_SET, NASDAQ100_SET
    sp500 = fetch_sp500_tickers()
    nasdaq100 = fetch_nasdaq100_tickers()
    SP500_SET = set(sp500)
    NASDAQ100_SET = set(nasdaq100)
    combined = list(dict.fromkeys(sp500 + nasdaq100 + DEFAULT_UNIVERSE))  # dedup, preserve order
    return combined



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
    sma50: Optional[float] = None
    sma150: Optional[float] = None
    sma200: Optional[float] = None
    trend_stage: Optional[str] = None
    atr_value: Optional[float] = None
    atr_pct: Optional[float] = None
    rs_rating: Optional[int] = None
    pattern_type: Optional[str] = None
    days_to_earnings: Optional[int] = None


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


# ------------------------------------------------------------------
# Trend structure (Weinstein/Minervini-style "trend template") + ATR
# ------------------------------------------------------------------
# This is the piece that was missing before: pattern detectors below can
# flag a technically-valid breakout on a stock that's still in a long-term
# downtrend, which is a much lower-quality setup than the same breakout
# happening on a stock in a genuine Stage 2 uptrend (price above a RISING
# 150/200-day MA, in the right order). Computing this once per ticker and
# feeding it into both the score and the displayed reasoning is what makes
# the scan precise instead of just pattern-matching in a vacuum.

def compute_trend_structure(hist: pd.DataFrame) -> Optional[dict]:
    """Returns SMA50/150/200, whether price/MAs are in bullish order, and a
    Stage 1-4 classification (Weinstein stage analysis):
      Stage 1 - Basing:      price chopping around a flat/falling 150MA
      Stage 2 - Uptrend:     price > rising SMA50 > SMA150 > SMA200 (the
                              "buy zone" — this is what the Google chart
                              example in the request shows)
      Stage 3 - Topping:     price below/around a flattening 150MA after an uptrend
      Stage 4 - Downtrend:   price < falling SMA150 < SMA200
    """
    if len(hist) < 210:
        return None
    closes = hist["Close"]
    sma50 = closes.rolling(50).mean()
    sma150 = closes.rolling(150).mean()
    sma200 = closes.rolling(200).mean()
    if sma200.isna().iloc[-1]:
        return None

    price = float(closes.iloc[-1])
    s50, s150, s200 = float(sma50.iloc[-1]), float(sma150.iloc[-1]), float(sma200.iloc[-1])
    # slope over the last ~20 bars — sign matters more than magnitude here
    sma150_slope = float(sma150.iloc[-1] - sma150.iloc[-20]) if not sma150.iloc[-20:].isna().any() else 0.0
    sma200_slope = float(sma200.iloc[-1] - sma200.iloc[-20]) if not sma200.iloc[-20:].isna().any() else 0.0

    above_50 = price > s50
    above_150 = price > s150
    above_200 = price > s200
    bullish_order = s50 > s150 > s200  # short-term MA above medium above long-term

    if above_50 and above_150 and above_200 and bullish_order and sma150_slope > 0:
        stage = "Stage 2 - Uptrend"
    elif price < s150 and price < s200 and sma150_slope < 0:
        stage = "Stage 4 - Downtrend"
    elif above_150 and sma150_slope <= 0:
        stage = "Stage 3 - Topping"
    else:
        stage = "Stage 1 - Basing"

    return {
        "sma50": round(s50, 2), "sma150": round(s150, 2), "sma200": round(s200, 2),
        "above_sma50": above_50, "above_sma150": above_150, "above_sma200": above_200,
        "bullish_ma_order": bullish_order, "sma150_rising": sma150_slope > 0,
        "sma200_rising": sma200_slope > 0, "stage": stage,
    }


def compute_atr(hist: pd.DataFrame, period: int = 14) -> Optional[dict]:
    """Average True Range (Wilder's) — the standard volatility yardstick for
    sizing stops/targets, shown here the same way most charting platforms
    display it (absolute $ value + % of price)."""
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
    return {"atr_value": round(atr_val, 2), "atr_pct": round(atr_val / price * 100, 2)}


def find_swing_points(values: np.ndarray, mode: str = "high", window: int = 2) -> List[tuple]:
    """Generalized local-extremum finder shared by every pattern detector
    below (previously each detector reimplemented its own copy of this).
    Returns [(index, value), ...] for swing highs (mode='high') or swing
    lows (mode='low')."""
    points = []
    for i in range(window, len(values) - window):
        if mode == "high":
            if all(values[i] > values[i - k] for k in range(1, window + 1)) and \
               all(values[i] > values[i + k] for k in range(1, window + 1)):
                points.append((i, float(values[i])))
        else:
            if all(values[i] < values[i - k] for k in range(1, window + 1)) and \
               all(values[i] < values[i + k] for k in range(1, window + 1)):
                points.append((i, float(values[i])))
    return points


def compute_support_resistance(hist: pd.DataFrame, lookback: int = 120) -> dict:
    """Real support/resistance from actual swing highs/lows in recent price
    structure, replacing the old placeholder (which was just close*1.05 and
    close*1.10 — not a real technical level at all). Support = the nearest
    genuine swing low below current price; resistance = the nearest 1-2
    genuine swing highs above current price (prior highs price will need to
    clear), falling back to the recent 20-day low/a round % above price only
    if the window doesn't have enough real structure to work with."""
    window = hist.tail(lookback).reset_index(drop=True)
    price = float(window["Close"].iloc[-1])

    swing_lows = find_swing_points(window["Low"].values, mode="low")
    swing_highs = find_swing_points(window["High"].values, mode="high")

    supports_below = sorted([v for _, v in swing_lows if v < price], reverse=True)
    resistances_above = sorted([v for _, v in swing_highs if v > price])

    support = supports_below[0] if supports_below else float(hist["Low"].tail(20).min())
    resistance_targets = resistances_above[:2] if resistances_above else [
        round(price * 1.05, 2), round(price * 1.10, 2)
    ]
    return {"support_level": round(support, 2), "resistance_targets": [round(r, 2) for r in resistance_targets]}


def detect_ascending_trendline_support(hist: pd.DataFrame) -> Optional[dict]:
    """The pattern from the Google chart example in the request: price
    riding/bouncing off a RISING trendline drawn through a series of higher
    swing lows, rather than breaking out of a range. This is a continuation
    signal (good entry timing within an existing uptrend), distinct from
    the breakout-style patterns below.

    Method: fit a line through recent swing lows; require a rising slope
    (confirming it's genuinely an ascending trendline); flag it when
    today's close is within 3% above the trendline's current value (i.e.
    price is bouncing off it right now, not far above or already broken
    below it).
    """
    window = hist.tail(TRENDLINE_LOOKBACK_DAYS).reset_index(drop=True)
    lows = window["Low"].values

    swing_lows = find_swing_points(lows, mode="low")
    if len(swing_lows) < 3:
        return None

    idx = [i for i, _ in swing_lows]
    val = [v for _, v in swing_lows]
    slope, intercept = np.polyfit(idx, val, 1)
    if slope <= 0:
        return None  # lows aren't actually rising -> not this pattern

    today_idx = len(window) - 1
    trendline_value_today = slope * today_idx + intercept
    today_close = float(window["Close"].iloc[-1])

    if trendline_value_today <= 0:
        return None
    distance_pct = (today_close - trendline_value_today) / trendline_value_today * 100
    if 0 <= distance_pct <= 3.0:
        return {"trendline_value": round(float(trendline_value_today), 2), "distance_pct": round(distance_pct, 1)}
    return None


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
    # IMPORTANT: round() on a numpy/pandas scalar returns another numpy
    # scalar, not a native Python float — despite this function's type
    # hint. psycopg2 can't properly adapt numpy types when they land in
    # a query parameter; it silently falls back to their repr() (e.g.
    # "np.float64(76.0)"), which gets embedded as literal invalid SQL
    # text and crashes with "schema np does not exist". Casting to
    # float() here guarantees a real Python float leaves this function.
    return float(round(max(0.0, pct_above), 1))


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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS universe_movers (
            ticker TEXT PRIMARY KEY,
            change_pct REAL,
            close_price REAL,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    cur.execute("ALTER TABLE universe_movers ADD COLUMN IF NOT EXISTS in_sp500 BOOLEAN DEFAULT FALSE;")
    cur.execute("ALTER TABLE universe_movers ADD COLUMN IF NOT EXISTS in_nasdaq100 BOOLEAN DEFAULT FALSE;")
    conn.commit()
    cur.close()
    conn.close()


def _native(v):
    """Converts a numpy scalar (float64, int64, bool_, etc.) to its native
    Python equivalent. psycopg2 doesn't have a proper adapter for numpy
    types — passing one as a query parameter can silently fall back to
    embedding its repr() (e.g. "np.float64(76.0)") as literal, invalid SQL
    text instead of a real value, which crashes with a cryptic Postgres
    parse error. Any pandas/numpy computation anywhere upstream can leak
    one of these in, so this is applied as a blanket safety net right
    before the values hit the database, not just fixed at the one
    known source."""
    if isinstance(v, np.generic):
        return v.item()
    return v


def upsert_scan_result(conn, result):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO scanned_stocks (
            ticker, trigger_text_he, trigger_text_en, swing_score,
            entry_price, support_level, resistance_targets,
            social_volume_spike_pct, ai_summary_he, ai_summary_en,
            market_cap, avg_volume_20d, breakout_volume_pct, exchange,
            change_pct, sma50, sma150, sma200, trend_stage, atr_value, atr_pct,
            rs_rating, pattern_type, days_to_earnings, timestamp
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            result.ticker, result.trigger_text_he, result.trigger_text_en,
            _native(result.swing_score), _native(result.entry_price), _native(result.support_level),
            json.dumps([_native(x) for x in result.resistance_targets]), _native(result.social_volume_spike_pct),
            result.ai_summary_he, result.ai_summary_en, _native(result.market_cap),
            _native(result.avg_volume_20d), _native(result.breakout_volume_pct), result.exchange,
            _native(result.change_pct), _native(result.sma50), _native(result.sma150), _native(result.sma200),
            result.trend_stage, _native(result.atr_value), _native(result.atr_pct), _native(result.rs_rating),
            result.pattern_type, _native(result.days_to_earnings), datetime.now(timezone.utc).isoformat(),
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
    # NOTE: this used to be period="6mo" (~126 trading days). That's LESS
    # than the 210 bars compute_trend_structure() requires for SMA200 + its
    # slope check — meaning the whole trend-stage feature was silently
    # returning None for every single ticker since it was added. 2y gives
    # enough room for SMA200 and for the RS Rating's 12-month lookback below.
    hist = tk.history(period="2y", interval="1d")
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
# Relative Strength (RS) Rating — IBD/O'Neil style: how has this stock
# performed vs. the S&P 500 (SPY) over the past year, weighted so recent
# performance counts more? This is the main lever for scan PRECISION: a
# technically valid breakout on a stock that's been quietly lagging the
# market for a year is a much weaker signal than the same breakout on a
# stock that's been genuinely outperforming. Computed once per scan run
# across the whole universe, then ranked into a 1-99 percentile so "RS 90"
# means "outperforming 90% of everything else scanned today" — the same
# scale IBD uses, which the user may already be familiar with.
# ------------------------------------------------------------------

RS_LOOKBACK_WEIGHTS = {63: 0.4, 126: 0.2, 189: 0.2, 252: 0.2}  # ~3/6/9/12 months, weighted


def fetch_benchmark_returns() -> Dict[int, float]:
    """SPY's own return over each lookback window — the yardstick every
    ticker's RS score gets compared against. Fetched once per scan run,
    not once per ticker."""
    try:
        spy_hist = yf.Ticker("SPY").history(period="2y", interval="1d")
        closes = spy_hist["Close"]
        rets = {}
        for days in RS_LOOKBACK_WEIGHTS:
            if len(closes) > days:
                rets[days] = float(closes.iloc[-1] / closes.iloc[-days - 1] - 1)
        return rets
    except Exception as e:
        print(f"[warn] failed to fetch SPY benchmark for RS Rating: {e}")
        return {}


def compute_rs_raw(hist: pd.DataFrame, benchmark_rets: Dict[int, float]) -> Optional[float]:
    """Weighted excess return vs SPY across the available lookback windows.
    Positive = outperforming the market over that horizon, negative = lagging."""
    if not benchmark_rets:
        return None
    closes = hist["Close"]
    score = 0.0
    total_weight = 0.0
    for days, weight in RS_LOOKBACK_WEIGHTS.items():
        if len(closes) > days and days in benchmark_rets:
            stock_ret = float(closes.iloc[-1] / closes.iloc[-days - 1] - 1)
            score += weight * (stock_ret - benchmark_rets[days])
            total_weight += weight
    if total_weight == 0:
        return None
    return score / total_weight


def compute_rs_ratings(rs_raw_by_ticker: Dict[str, float]) -> Dict[str, int]:
    """Converts raw excess-return scores into a 1-99 percentile rank across
    everything scanned this run — the actual "RS Rating" number."""
    if not rs_raw_by_ticker:
        return {}
    ranked = sorted(rs_raw_by_ticker.items(), key=lambda kv: kv[1])
    n = len(ranked)
    ratings = {}
    for i, (ticker, _) in enumerate(ranked):
        ratings[ticker] = int(round((i / max(n - 1, 1)) * 98)) + 1  # 1..99
    return ratings


# ------------------------------------------------------------------
# Earnings-date awareness — a real risk-management concern for swing
# trades: entering a breakout right before an earnings report means the
# stock can gap hard against you overnight, regardless of how good the
# technical setup looked the day before. Uses info["earningsTimestamp"],
# which is already part of the per-ticker info dict fetched for market cap
# etc. — no extra API call needed.
# ------------------------------------------------------------------

EARNINGS_WARNING_WINDOW_DAYS = 7  # flag + score penalty inside this window


def compute_days_to_earnings(info: dict) -> Optional[int]:
    ts = info.get("earningsTimestamp")
    if not ts:
        return None
    try:
        earnings_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        today = datetime.now(timezone.utc).date()
        return (earnings_date - today).days
    except Exception:
        return None


# ------------------------------------------------------------------
# Orchestration
# ------------------------------------------------------------------

def scan_universe(universe: List[str] = None, target_language: str = "he") -> List[ScanResult]:
    universe = universe or build_scan_universe()
    results: List[ScanResult] = []
    consecutive_timeouts = 0
    all_changes: List[tuple] = []  # (ticker, change_pct, close) for EVERY ticker fetched —
                                     # a reliable fallback for the movers strip if Yahoo's
                                     # whole-market screener endpoint is unavailable, since
                                     # this uses .history(), the endpoint proven to work here.
    benchmark_rets = fetch_benchmark_returns()
    rs_raw_by_ticker: Dict[str, float] = {}  # computed for every ticker fetched, used for
                                              # percentile ranking after the full loop below

    for ticker in universe:
        try:
            hist, info = fetch_ticker_data_with_timeout(ticker)
            consecutive_timeouts = 0  # reset on any successful fetch
        except FutureTimeoutError:
            print(f"[warn] {ticker}: timed out after {PER_TICKER_TIMEOUT_SEC}s, skipping")
            consecutive_timeouts += 1
            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                # Yahoo is very likely heavily rate-limiting right now. Each
                # timeout leaves an abandoned thread still trying to connect
                # in the background — piling up dozens of these can exhaust
                # memory and get the whole container OOM-killed with no
                # Python traceback (which is what made past crashes silent).
                # Better to stop cleanly now and keep whatever we already found.
                print(f"[fatal] {consecutive_timeouts} consecutive timeouts — Yahoo Finance appears to be "
                      f"heavily rate-limiting right now. Aborting scan early to avoid resource exhaustion. "
                      f"Saving the {len(results)} result(s) found so far.")
                break
            continue
        except Exception as e:
            print(f"[warn] failed to fetch {ticker}: {e}")
            consecutive_timeouts = 0
            continue
        finally:
            time.sleep(INTER_TICKER_DELAY_SEC)  # be gentle with Yahoo Finance's rate limits

        if hist is not None and len(hist) >= 2:
            try:
                _close_now = float(hist["Close"].iloc[-1])
                _prev_close = float(hist["Close"].iloc[-2])
                _chg = round(((_close_now - _prev_close) / _prev_close) * 100, 2) if _prev_close else 0.0
                all_changes.append((ticker, _chg, round(_close_now, 2)))
            except Exception as e:
                print(f"[warn] change_pct calc failed for {ticker}: {e}")

        if not passes_liquidity_and_cap_filter(ticker, info, hist):
            continue

        rs_raw = compute_rs_raw(hist, benchmark_rets)
        if rs_raw is not None:
            rs_raw_by_ticker[ticker] = rs_raw

        # Run all pattern detectors
        trendline_break  = detect_descending_trendline_breakout(hist)
        asc_trendline    = detect_ascending_trendline_support(hist)
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
        trend = compute_trend_structure(hist)
        atr = compute_atr(hist)
        days_to_earnings = compute_days_to_earnings(info)

        # Volume confirmation gate: a genuine breakout should show real
        # buying volume behind it. Without this, "breakout" patterns were
        # being flagged even on weak/below-average volume, which is a
        # classic false-breakout setup. Trend/momentum signals (golden
        # cross, MACD cross, RSI bounce, ascending trendline support)
        # don't require the full breakout threshold since they aren't
        # breakout-on-a-volume-spike patterns by nature.
        volume_confirmed = vol_pct >= BREAKOUT_VOLUME_THRESHOLD_PCT
        if not volume_confirmed:
            cup_handle = None
            double_bottom = None
            high_52w = None
            bull_flag = None
            asc_triangle = None
            trendline_break = None

        # Skip if no pattern found at all
        if not any([trendline_break, asc_trendline, high_52w, momentum, cup_handle, bull_flag,
                    asc_triangle, golden_cross, double_bottom, macd_cross, rsi_bounce]):
            continue

        close   = float(hist["Close"].iloc[-1])
        sr = compute_support_resistance(hist)
        support, resistance = sr["support_level"], sr["resistance_targets"]
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        change_pct = round(((close - prev_close) / prev_close) * 100, 2) if prev_close else 0.0

        # Priority: strongest/rarest patterns get highest score
        if cup_handle:
            trigger_en = "Cup & Handle breakout"
            trigger_he = "פריצת תבנית כוס וידית (Cup & Handle)"
            tech_score = min(100, 80 + int(vol_pct / 5))
            pattern_type = "cup_handle"
        elif double_bottom:
            trigger_en = f"Double Bottom breakout above ${double_bottom['neckline']}"
            trigger_he = f"פריצת תבנית תחתית כפולה (W) מעל ${double_bottom['neckline']}"
            tech_score = min(100, 79 + int(vol_pct / 5))
            pattern_type = "double_bottom"
        elif high_52w:
            trigger_en = "Breaking above 52-week high"
            trigger_he = "פריצת שיא 52 שבועות"
            tech_score = min(100, 78 + int(vol_pct / 5))
            pattern_type = "52w_high"
        elif golden_cross:
            trigger_en = "Golden Cross (50D SMA crossed above 200D SMA)"
            trigger_he = "פריצת גולדן קרוס (ממוצע 50 יום חצה מעל ממוצע 200 יום)"
            tech_score = min(100, 74 + int(vol_pct / 5))
            pattern_type = "golden_cross"
        elif bull_flag:
            trigger_en = f"Bull Flag breakout (pole +{bull_flag['pole_pct']}%)"
            trigger_he = f"פריצת דגל שורי (עמוד +{bull_flag['pole_pct']}%)"
            tech_score = min(100, 72 + int(vol_pct / 5))
            pattern_type = "bull_flag"
        elif asc_triangle:
            trigger_en = f"Ascending Triangle breakout above ${asc_triangle['resistance']}"
            trigger_he = f"פריצת משולש עולה מעל ${asc_triangle['resistance']}"
            tech_score = min(100, 68 + int(vol_pct / 5))
            pattern_type = "ascending_triangle"
        elif asc_trendline:
            trigger_en = f"Bouncing off rising trendline support (~${asc_trendline['trendline_value']})"
            trigger_he = f"התאוששות מקו מגמה עולה (סביב ${asc_trendline['trendline_value']})"
            tech_score = 66
            pattern_type = "ascending_trendline"
        elif macd_cross:
            trigger_en = "MACD bullish crossover"
            trigger_he = "חציית MACD שורית"
            tech_score = min(100, 64 + int(vol_pct / 5))
            pattern_type = "macd_cross"
        elif momentum:
            trigger_en = f"Strong momentum surge (+{momentum['pct_change_5d']}% / 5 days)"
            trigger_he = f"מומנטום חזק ב-5 ימים (+{momentum['pct_change_5d']}%)"
            tech_score = min(100, 62 + int(vol_pct / 5))
            pattern_type = "momentum_surge"
        elif rsi_bounce:
            trigger_en = f"RSI oversold bounce (RSI {rsi_bounce['rsi']})"
            trigger_he = f"התאוששות מאזור מכירת יתר (RSI {rsi_bounce['rsi']})"
            tech_score = min(100, 58 + int(vol_pct / 5))
            pattern_type = "rsi_bounce"
        else:
            trigger_en = "Breakout above descending trendline on strong volume"
            trigger_he = "פריצת שיאים יורדים בווליום חזק"
            tech_score = min(100, 55 + int(vol_pct / 4))
            pattern_type = "descending_trendline_breakout"

        # Trend-quality adjustment: the same pattern is a meaningfully
        # better setup when the broader trend structure backs it up (Stage
        # 2 uptrend — price above a rising 150-day MA, in bullish MA order)
        # vs. the same pattern firing on a stock that's still in a downtrend
        # or basing phase. This is the main lever for making the scan more
        # precise rather than just pattern-matching in a vacuum.
        trend_stage = trend["stage"] if trend else None
        if trend:
            if trend["stage"] == "Stage 2 - Uptrend":
                tech_score = min(100, tech_score + 8)
            elif trend["stage"] == "Stage 4 - Downtrend":
                tech_score = max(1, tech_score - 20)  # bullish pattern against a downtrend = much lower conviction
            elif trend["stage"] == "Stage 3 - Topping":
                tech_score = max(1, tech_score - 10)

        # A technically great setup right before earnings is still a much
        # riskier trade — the stock can gap against the position overnight
        # regardless of the chart. Penalize (don't disqualify) so it's a
        # visible warning rather than a hidden risk.
        earnings_soon = days_to_earnings is not None and 0 <= days_to_earnings <= EARNINGS_WARNING_WINDOW_DAYS
        if earnings_soon:
            tech_score = max(1, tech_score - 12)

        ai_summary_en = f"{trigger_en}. Volume {vol_pct:.0f}% above 20d average."
        ai_summary_he = f"{trigger_he}. נפח גבוה ב-{vol_pct:.0f}% מהממוצע."
        if trend_stage:
            ai_summary_en += f" Trend: {trend_stage}."
            stage_he = {
                "Stage 2 - Uptrend": "שלב 2 - מגמת עלייה",
                "Stage 1 - Basing": "שלב 1 - בסיס/איחוד",
                "Stage 3 - Topping": "שלב 3 - היפוך אפשרי מלמעלה",
                "Stage 4 - Downtrend": "שלב 4 - מגמת ירידה",
            }.get(trend_stage, trend_stage)
            ai_summary_he += f" מגמה: {stage_he}."
        # Distance to the nearest resistance target above current price —
        # turns "here's a support/resistance number" into an actual
        # answer to "how much room does this have before it hits a wall".
        targets_above = [r for r in resistance if r > close] if resistance else []
        if targets_above:
            nearest_res = min(targets_above)
            room_pct = round((nearest_res / close - 1) * 100, 1)
            ai_summary_en += f" Room to next resistance (${nearest_res}): {room_pct:+.1f}%."
            ai_summary_he += f" מרחק להתנגדות הקרובה (${nearest_res}): {room_pct:+.1f}%."
        if earnings_soon:
            ai_summary_en += f" ⚠️ Earnings report in {days_to_earnings} day(s) — expect elevated volatility risk."
            ai_summary_he += f" ⚠️ דוחות כספיים בעוד {days_to_earnings} ימים — צפויה תנודתיות מוגברת."

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
            ai_summary_en=ai_summary_en,
            ai_summary_he=ai_summary_he,
            sma50=trend["sma50"] if trend else None,
            sma150=trend["sma150"] if trend else None,
            sma200=trend["sma200"] if trend else None,
            trend_stage=trend_stage,
            atr_value=atr["atr_value"] if atr else None,
            atr_pct=atr["atr_pct"] if atr else None,
            pattern_type=pattern_type,
            days_to_earnings=days_to_earnings,
        )
        results.append(result)

    # RS Rating can only be computed as a percentile AFTER seeing every
    # ticker's raw score, so this pass happens once the whole universe has
    # been scanned — same idea as the trend-stage adjustment above, applied
    # after the fact rather than ticker-by-ticker.
    rs_ratings = compute_rs_ratings(rs_raw_by_ticker)
    for result in results:
        rating = rs_ratings.get(result.ticker)
        result.rs_rating = rating
        if rating is not None:
            if rating >= 80:
                result.swing_score = min(100, result.swing_score + 8)   # genuine market leader
                result.ai_summary_en += f" RS Rating {rating}/99 — outperforming {rating}% of the scanned universe over the past year."
                result.ai_summary_he += f" דירוג RS {rating}/99 — מתעלה על {rating}% מהיקום שנסרק בשנה האחרונה."
            elif rating >= 60:
                result.swing_score = min(100, result.swing_score + 3)
                result.ai_summary_en += f" RS Rating {rating}/99."
                result.ai_summary_he += f" דירוג RS {rating}/99."
            elif rating < 40:
                result.swing_score = max(1, result.swing_score - 15)  # pattern firing on a market laggard = low conviction
                result.ai_summary_en += f" RS Rating {rating}/99 — this pattern is firing on a market laggard, lower conviction."
                result.ai_summary_he += f" דירוג RS {rating}/99 — התבנית מתרחשת על מניה שפחות מובילה את השוק, רמת ביטחון נמוכה יותר."

    upsert_universe_movers(all_changes)
    return results


def upsert_universe_movers(all_changes: List[tuple]):
    """One current row per ticker (whole scanned universe, not just
    pattern-matched stocks) — used by api_server.py's /api/market-movers
    as a fallback when Yahoo's whole-market screener endpoint is
    unavailable, since this data comes from .history(), which is
    proven to work reliably from this deployment. Also tagged with index
    membership (SP500_SET/NASDAQ100_SET, populated by build_scan_universe)
    so /api/heatmap can filter to one index without any separate fetch."""
    if not all_changes:
        return
    conn = get_conn()
    cur = conn.cursor()
    for ticker, chg, close_price in all_changes:
        cur.execute(
            """
            INSERT INTO universe_movers (ticker, change_pct, close_price, in_sp500, in_nasdaq100, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker) DO UPDATE SET
                change_pct = EXCLUDED.change_pct,
                close_price = EXCLUDED.close_price,
                in_sp500 = EXCLUDED.in_sp500,
                in_nasdaq100 = EXCLUDED.in_nasdaq100,
                timestamp = EXCLUDED.timestamp
            """,
            (ticker, _native(chg), _native(close_price), ticker in SP500_SET, ticker in NASDAQ100_SET,
             datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    cur.close()
    conn.close()
    print(f"Updated universe_movers fallback table for {len(all_changes)} tickers.")


# ------------------------------------------------------------------
# Pattern win-rate stats — filling in "what actually happened" after the
# fact. A signal from N trading days ago now has a knowable outcome; this
# looks up each such row's price ~10 and ~20 trading days after it was
# flagged and records the return, so /api/pattern-stats can later show
# real win rates per pattern instead of nothing. Runs at the start of every
# scan (cheap: only touches rows that don't have an outcome yet).
# ------------------------------------------------------------------

FORWARD_RETURN_HORIZONS = {"forward_return_10d": 10, "forward_return_20d": 20}
BACKFILL_BATCH_SIZE = 300  # cap per run so a huge backlog can't blow out scan runtime


def backfill_forward_returns():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # Each horizon is only fetched once enough trading days have actually
    # passed for it — 10 trading days is ~14 calendar days, 20 is ~28.
    # Previously both columns waited on a single 30-day gate, which meant
    # forward_return_10d (and therefore /api/pattern-stats, which requires
    # it) sat empty for 2+ extra weeks per signal for no reason.
    horizon_min_age_days = {"forward_return_10d": 14, "forward_return_20d": 28}

    filled = 0
    for col, min_age in horizon_min_age_days.items():
        cur.execute(
            f"""
            SELECT id, ticker, entry_price, timestamp FROM scanned_stocks
            WHERE pattern_type IS NOT NULL
              AND timestamp <= NOW() - INTERVAL '{min_age} days'
              AND {col} IS NULL
            ORDER BY timestamp ASC
            LIMIT %s
            """,
            (BACKFILL_BATCH_SIZE,),
        )
        rows = cur.fetchall()
        horizon = FORWARD_RETURN_HORIZONS[col]

        for row in rows:
            ticker, entry_price, ts = row["ticker"], row["entry_price"], row["timestamp"]
            if not entry_price:
                continue
            try:
                hist = yf.Ticker(ticker).history(
                    start=ts.date(), end=ts.date() + timedelta(days=45), interval="1d"
                )
                closes = hist["Close"]
                if len(closes) <= horizon:
                    continue
                fwd_close = float(closes.iloc[horizon])
                value = round((fwd_close / float(entry_price) - 1) * 100, 2)
                upd_cur = conn.cursor()
                upd_cur.execute(
                    f"UPDATE scanned_stocks SET {col} = %s WHERE id = %s",
                    (value, row["id"]),
                )
                conn.commit()
                upd_cur.close()
                filled += 1
            except Exception as e:
                print(f"[warn] {col} backfill failed for {ticker} (id={row['id']}): {e}")
            time.sleep(INTER_TICKER_DELAY_SEC)

    cur.close()
    conn.close()
    print(f"Forward-return backfill: updated {filled} column-values.")


def main():
    init_db()
    backfill_forward_returns()
    # scan_universe() takes several minutes (network calls to Yahoo Finance
    # for every ticker) and doesn't touch the database at all during that
    # time. Neon's free tier auto-suspends its compute after ~5 minutes of
    # DB inactivity to stay free — so a connection opened before the scan
    # and reused after it is often already dead by the time we get here
    # (psycopg2.OperationalError: SSL connection has been closed
    # unexpectedly). Opening the connection fresh right before the writes
    # avoids that entirely.
    results = scan_universe()

    conn = get_conn()
    try:
        saved = 0
        for r in results:
            try:
                upsert_scan_result(conn, r)
            except psycopg2.OperationalError:
                # Belt-and-suspenders: if Neon still drops the connection
                # mid-loop (e.g. a slow patch of tickers), reconnect once
                # and retry this single row instead of losing everything
                # saved so far.
                print("[warn] DB connection dropped mid-write, reconnecting...")
                conn.close()
                conn = get_conn()
                upsert_scan_result(conn, r)
            saved += 1
        print(f"Scan complete. {saved}/{len(results)} tickers flagged and saved to Postgres.")
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
