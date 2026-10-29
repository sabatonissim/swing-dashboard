"""
Market breadth (v22) — how many S&P 500 stocks take part in the market's move
=============================================================================
Runs in GitHub Actions (market-breadth.yml), started by scheduler.py every hour 10:00-16:00 ET and at 16:20 ET.
Stores one row per trading day in Postgres (market_breadth); the API (/api/market-breadth) only reads it.

Per day, over today's S&P 500 members (survivorship: the list is today's, as on most free breadth pages):
  * above 50 / 200: close above its 50-day / 200-day simple moving average (stocks with enough history)
  * new highs / lows: the day's HIGH is the highest high of the last 252 trading days (LOW: lowest low)
  * advancers / decliners: close above / below the previous close
Prices are as traded (auto_adjust=False, like the scanner and TradingView); Yahoo already adjusts for splits.
Two years of prices are downloaded each run, so the last ~130 days are all recomputed — the history fills in on the
first run and a missed run heals itself. During market hours today's row uses the live (partial) daily bar.
"""

import io
import os
import sys
import time
from typing import List

import pandas as pd
import psycopg2
import requests
import yfinance as yf

HISTORY_DAYS = 130            # rows (re)written each run ~ 6 months
CHUNK = 100
MIN_STOCKS = 300              # fewer usable stocks on a day = a data problem, that day is not stored


def sp500_tickers() -> List[str]:
    resp = requests.get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
                        timeout=15)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    return sorted({str(s).strip().upper().replace(".", "-") for s in df["Symbol"].dropna()})


def download(tickers: List[str]):
    """(close, high, low) DataFrames: dates x tickers."""
    closes, highs, lows = [], [], []
    for i in range(0, len(tickers), CHUNK):
        chunk = tickers[i:i + CHUNK]
        for attempt in range(3):
            try:
                df = yf.download(chunk, period="2y", interval="1d", group_by="column", auto_adjust=False,
                                 threads=True, progress=False)
                if df is not None and not df.empty:
                    closes.append(df["Close"])
                    highs.append(df["High"])
                    lows.append(df["Low"])
                    break
            except Exception as e:
                print(f"[warn] breadth: download chunk {i // CHUNK + 1} attempt {attempt + 1} failed: {e}")
            time.sleep(3 * (attempt + 1))
    if not closes:
        raise RuntimeError("no price data downloaded")
    def tidy(parts):
        df = pd.concat(parts, axis=1)
        idx = pd.to_datetime(df.index)
        df.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return df.loc[:, ~df.columns.duplicated()].apply(pd.to_numeric, errors="coerce")

    close, high, low = tidy(closes), tidy(highs), tidy(lows)
    return close, high.reindex(index=close.index, columns=close.columns), low.reindex(index=close.index, columns=close.columns)


def compute(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame) -> pd.DataFrame:
    sma50 = close.rolling(50, min_periods=50).mean()
    sma200 = close.rolling(200, min_periods=200).mean()
    hh = high.rolling(252, min_periods=252).max()
    ll = low.rolling(252, min_periods=252).min()
    prev = close.shift(1)

    has50, has200 = sma50.notna() & close.notna(), sma200.notna() & close.notna()
    has_hl = hh.notna() & high.notna() & low.notna()
    has_chg = close.notna() & prev.notna()
    out = pd.DataFrame({
        "n": close.notna().sum(axis=1),
        "n50": has50.sum(axis=1),
        "above50": ((close > sma50) & has50).sum(axis=1),
        "n200": has200.sum(axis=1),
        "above200": ((close > sma200) & has200).sum(axis=1),
        "n_hl": has_hl.sum(axis=1),
        "new_highs": ((high >= hh) & has_hl).sum(axis=1),
        "new_lows": ((low <= ll) & has_hl).sum(axis=1),
        "advancers": ((close > prev) & has_chg).sum(axis=1),
        "decliners": ((close < prev) & has_chg).sum(axis=1),
    })
    out = out[out["n"] >= MIN_STOCKS]
    return out.tail(HISTORY_DAYS)


def save(rows: pd.DataFrame) -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set")
    conn = psycopg2.connect(db_url)          # opened only now, right before writing (Neon auto-suspend)
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS market_breadth (
            day DATE PRIMARY KEY,
            n INTEGER, n50 INTEGER, above50 INTEGER, n200 INTEGER, above200 INTEGER,
            n_hl INTEGER, new_highs INTEGER, new_lows INTEGER, advancers INTEGER, decliners INTEGER,
            updated_at TIMESTAMPTZ DEFAULT NOW())""")
        for day, r in rows.iterrows():
            cur.execute("""INSERT INTO market_breadth (day, n, n50, above50, n200, above200, n_hl, new_highs, new_lows,
                                                       advancers, decliners, updated_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (day) DO UPDATE SET n=EXCLUDED.n, n50=EXCLUDED.n50, above50=EXCLUDED.above50,
                               n200=EXCLUDED.n200, above200=EXCLUDED.above200, n_hl=EXCLUDED.n_hl,
                               new_highs=EXCLUDED.new_highs, new_lows=EXCLUDED.new_lows,
                               advancers=EXCLUDED.advancers, decliners=EXCLUDED.decliners, updated_at=NOW()""",
                        (day.date(), *[int(r[c]) for c in ("n", "n50", "above50", "n200", "above200", "n_hl",
                                                           "new_highs", "new_lows", "advancers", "decliners")]))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return len(rows)


def main() -> int:
    started = time.time()
    tickers = sp500_tickers()
    close, high, low = download(tickers)
    rows = compute(close, high, low)
    if rows.empty:
        print("market breadth: nothing to store (too few stocks with data)")
        return 1
    n = save(rows)
    last = rows.iloc[-1]
    print(f"market breadth: saved {n} days, latest {rows.index[-1].date()}: "
          f"{int(last['above50'])}/{int(last['n50'])} above 50d, {int(last['above200'])}/{int(last['n200'])} above 200d, "
          f"highs {int(last['new_highs'])} lows {int(last['new_lows'])}, "
          f"adv {int(last['advancers'])} dec {int(last['decliners'])} ({len(tickers)} tickers, {int(time.time() - started)}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
