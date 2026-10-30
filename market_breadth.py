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

v23 — market state in one line, same run (each part fails on its own, breadth is saved first):
  * market_regime: SPY close + its 50/200-day averages and the VIX close (Yahoo, same download style)
  * treasury_yields: the US Treasury's daily par yield curve (home.treasury.gov CSV, official and keyless), this year
    and last year, so the site can show today's curve next to a month and a year ago and the 10Y-2Y spread history
"""

import csv
import io
import json
import os
import sys
import time
from datetime import date, datetime
from typing import Dict, List

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


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None    # NaN / inf never reach the database


def regime_rows() -> pd.DataFrame:
    """SPY close, its 50/200-day simple averages and the VIX close, one row per day (last HISTORY_DAYS)."""
    df = yf.download(["SPY", "^VIX"], period="2y", interval="1d", group_by="column", auto_adjust=False,
                     threads=True, progress=False)
    if df is None or df.empty:
        raise RuntimeError("no SPY/VIX data")
    close = df["Close"]

    def daily(col):
        # Each series on its own, one value per calendar day. Together in one table the SPY and VIX rows came with
        # different times of day, so SPY had a gap on every other row and the 50/200-day averages were always empty
        # (seen live 29.10.2026: spy_sma50 = null with 130 days stored).
        s = pd.to_numeric(close[col], errors="coerce").dropna()
        idx = pd.to_datetime(s.index)
        s.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
        return s.groupby(level=0).last().sort_index()

    spy = daily("SPY")
    vix = daily("^VIX")
    out = pd.DataFrame({
        "spy_close": spy,
        "spy_sma50": spy.rolling(50, min_periods=50).mean(),
        "spy_sma200": spy.rolling(200, min_periods=200).mean(),
        "vix": vix.reindex(spy.index).ffill(limit=3),     # the VIX can miss a day that SPY has
    })
    return out.tail(HISTORY_DAYS)


TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
                "{y}/all?type=daily_treasury_yield_curve&field_tdr_date_value={y}&page&_format=csv")
# CSV column -> the label the site shows; the odd maturities (1.5/2/4 months) are left out on purpose
TREASURY_COLS = {"1 Mo": "1M", "3 Mo": "3M", "6 Mo": "6M", "1 Yr": "1Y", "2 Yr": "2Y", "3 Yr": "3Y", "5 Yr": "5Y",
                 "7 Yr": "7Y", "10 Yr": "10Y", "20 Yr": "20Y", "30 Yr": "30Y"}


def treasury_curves() -> Dict[date, dict]:
    """{day: {"3M": 4.18, "2Y": 4.67, ...}} for this year and last year (official Treasury par yield curve)."""
    out: Dict[date, dict] = {}
    this_year = date.today().year
    for y in (this_year - 1, this_year):
        try:
            resp = requests.get(TREASURY_URL.format(y=y), timeout=30, headers={"User-Agent": "Mozilla/5.0 swing-desk"})
            resp.raise_for_status()
        except Exception as e:
            print(f"[warn] treasury yields {y}: {type(e).__name__}: {e}")
            continue
        for row in csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig", errors="replace"))):   # BOM-safe "Date"
            try:
                day = datetime.strptime((row.get("Date") or "").strip(), "%m/%d/%Y").date()
            except ValueError:
                continue
            curve = {label: _num(row.get(col)) for col, label in TREASURY_COLS.items()}
            curve = {k: v for k, v in curve.items() if v is not None}
            if "10Y" in curve and "2Y" in curve:
                out[day] = curve
    return out


def save_regime(regime: pd.DataFrame, curves: Dict[date, dict]) -> None:
    db_url = os.environ.get("DATABASE_URL")
    conn = psycopg2.connect(db_url)          # opened right before writing (Neon auto-suspend)
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS market_regime (
            day DATE PRIMARY KEY, spy_close REAL, spy_sma50 REAL, spy_sma200 REAL, vix REAL,
            updated_at TIMESTAMPTZ DEFAULT NOW())""")
        cur.execute("""CREATE TABLE IF NOT EXISTS treasury_yields (
            day DATE PRIMARY KEY, curve JSONB NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW())""")
        for day, r in regime.iterrows():
            cur.execute("""INSERT INTO market_regime (day, spy_close, spy_sma50, spy_sma200, vix, updated_at)
                           VALUES (%s,%s,%s,%s,%s,NOW())
                           ON CONFLICT (day) DO UPDATE SET spy_close=EXCLUDED.spy_close, spy_sma50=EXCLUDED.spy_sma50,
                               spy_sma200=EXCLUDED.spy_sma200, vix=COALESCE(EXCLUDED.vix, market_regime.vix),
                               updated_at=NOW()""",
                        (day.date(), _num(r["spy_close"]), _num(r["spy_sma50"]), _num(r["spy_sma200"]), _num(r["vix"])))
        for day, curve in curves.items():
            cur.execute("""INSERT INTO treasury_yields (day, curve, updated_at) VALUES (%s, %s, NOW())
                           ON CONFLICT (day) DO UPDATE SET curve=EXCLUDED.curve, updated_at=NOW()""",
                        (day, json.dumps(curve)))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def main() -> int:
    started = time.time()
    tickers = sp500_tickers()
    close, high, low = download(tickers)
    rows = compute(close, high, low)
    status = 0
    if rows.empty:
        print("market breadth: nothing to store (too few stocks with data)")
        status = 1
    else:
        n = save(rows)
        last = rows.iloc[-1]
        print(f"market breadth: saved {n} days, latest {rows.index[-1].date()}: "
              f"{int(last['above50'])}/{int(last['n50'])} above 50d, {int(last['above200'])}/{int(last['n200'])} above 200d, "
              f"highs {int(last['new_highs'])} lows {int(last['new_lows'])}, "
              f"adv {int(last['advancers'])} dec {int(last['decliners'])} ({len(tickers)} tickers, {int(time.time() - started)}s)")
    # v23: market state (SPY trend, VIX) + Treasury yield curve — a failure here never loses the breadth above
    try:
        regime = regime_rows()
    except Exception as e:
        print(f"[warn] market regime: SPY/VIX download failed: {type(e).__name__}: {e}")
        regime = pd.DataFrame()
    curves = treasury_curves()
    if regime.empty and not curves:
        print("[warn] market regime: nothing to store")
        return status
    save_regime(regime, curves)
    msg = f"market regime: saved {len(regime)} days"
    if not regime.empty:
        r = regime.iloc[-1]
        fmt = lambda v: "—" if _num(v) is None else f"{_num(v):.2f}"
        msg += (f", latest {regime.index[-1].date()}: SPY {fmt(r['spy_close'])} (50d {fmt(r['spy_sma50'])}, "
                f"200d {fmt(r['spy_sma200'])}), VIX {fmt(r['vix'])}")
    if curves:
        d = max(curves)
        msg += f"; treasury yields: {len(curves)} days, latest {d}: 2Y {curves[d].get('2Y')} 10Y {curves[d].get('10Y')}"
    else:
        msg += "; treasury yields: none"
    print(msg)
    return status


if __name__ == "__main__":
    sys.exit(main())
