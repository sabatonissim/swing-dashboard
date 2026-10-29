"""
Company fundamentals (v21) — shared by the API server and the nightly warm-up job
==================================================================================
Used by:
  * api_server.py (Render)  -> /api/fundamentals, sector comparison, lookup
  * GitHub Actions (fundamentals-refresh.yml) -> `python -u fundamentals.py`
    builds the numbers in advance for S&P 500 + Nasdaq 100 + scanner hits + watchlists and stores them in
    Postgres (table fundamentals_cache), so a stock page opens with its fundamentals at once.

Sources: the company's own SEC filings (XBRL companyfacts, 10-Q/10-K); Yahoo only for extras (analyst EPS
estimates, P/E band price history, last price) and for foreign filers that file no quarterly XBRL.

v21 fixes (checked on real SEC data, 29.10.2026 — HISTORY §27.3טז):
  * revenue tag list: CRWD reports RevenueFromContractWithCustomerIncludingAssessedTax, GS/banks
    RevenuesNetOfInterestExpense, utilities RegulatedAndUnregulatedOperatingRevenue. Before, their revenue
    was empty (CRWD, GS) or stopped years ago (WFC 2020, DUK 2018, NEE 2013). Tags are now tried freshest-first.
  * cash flow quarters come from start/end dates (year-to-date minus the previous year-to-date of the SAME
    fiscal year), per tag. The old fy/fp grouping mixed in prior-year comparatives: CRWD Q4 FY24 operating
    cash flow showed 498M instead of 347M. New method: 4 quarters always add up to the 10-K total (CRWD, AAPL).
  * P/E: a loss or a P/E above PE_NM_ABOVE is "not meaningful" (CRWD showed 4611.7); the P/E band's last
    value is only used when it is recent.
  * slow Yahoo extras run in parallel with a time limit — a slow extra is skipped, the SEC data still comes back.
  * price_at_build: market cap / P/E are re-scaled to the current price when a stored copy is served.
"""

import io
import json
import math
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from datetime import date, datetime as dt, timezone
from typing import Callable, List, Optional

import pandas as pd
import requests
import yfinance as yf

SEC_HEADERS = {"User-Agent": "SwingDesk-Dashboard admin@swingdesk.app"}   # SEC fair-use: a descriptive User-Agent

CACHE_VERSION = 2              # bump when the numbers' logic changes, so stored copies are rebuilt, not served
PE_NM_ABOVE = 300              # trailing P/E above this = earnings near zero -> shown as "not meaningful"
SIDE_CALLS_BUDGET_SEC = 9      # all Yahoo extras together; whatever is slower is skipped (SEC data still returned)

_CLASS_SHARE_DOT_RE = re.compile(r"^([A-Z]{1,5})\.([A-Z])$")


def canon_ticker(raw: str) -> str:
    """Same rule as api_server._canon_ticker: BRK.B -> BRK-B."""
    t = (raw or "").upper().strip()
    m = _CLASS_SHARE_DOT_RE.match(t)
    return f"{m.group(1)}-{m.group(2)}" if m else t


def json_safe(obj):
    """NaN/Infinity -> None (a browser's JSON parser rejects them), numpy numbers -> plain floats."""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, bool) or obj is None or isinstance(obj, str):
        return obj
    if isinstance(obj, (int, float)) or hasattr(obj, "item"):
        try:
            f = float(obj)
        except Exception:
            return None
        if math.isnan(f) or math.isinf(f):
            return None
        return int(obj) if isinstance(obj, int) else f
    return obj


# ------------------------------------------------------------------ SEC
def fetch_cik_map() -> dict:
    """ticker -> 10-digit CIK, straight from SEC (the server has its own DB-cached version)."""
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=SEC_HEADERS, timeout=15)
            resp.raise_for_status()
            return {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in resp.json().values()}
        except Exception as e:
            last_error = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"SEC ticker->CIK map failed: {last_error}")


def sec_company_facts(cik10: str) -> Optional[dict]:
    """The parsed companyfacts file; {} = SEC has none for this CIK; None = SEC did not answer (outage/limit)."""
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json",
                                headers=SEC_HEADERS, timeout=15)
            if resp.status_code == 200:
                return resp.json()
            # log the real status: a 403/429/5xx from SEC must not look like "this company has no data"
            print(f"[warn] SEC companyfacts CIK{cik10} attempt {attempt+1}/3: HTTP {resp.status_code}")
            if resp.status_code == 404:
                return {}    # genuinely no XBRL for this CIK (not an outage) — retrying won't help
            last_error = f"HTTP {resp.status_code}"
        except Exception as e:
            last_error = e
            print(f"[warn] SEC companyfacts CIK{cik10} attempt {attempt+1}/3 failed: {type(e).__name__}: {e}")
        if attempt < 2:
            time.sleep(1.5 * (attempt + 1))
    print(f"[warn] SEC companyfacts CIK{cik10}: all 3 attempts failed, last error: {last_error}")
    return None


def _main_unit_rows(node: dict) -> list:
    """Rows of the unit with the MOST entries, not simply the first one: Coca-Cola's EarningsPerShareDiluted lists
    an old mis-tagged unit "pure" (4 rows, 2008-09) before "USD/shares" (213 rows), so the EPS history stopped in
    2009 and the P/E came back empty (KO, KMB, PEP) or wrong (HSY 84.4 instead of ~23)."""
    units = (node or {}).get("units") or {}
    unit_key = max(units, key=lambda k: len(units[k] or []), default=None)
    return (units.get(unit_key) or []) if unit_key else []


def _latest_end(node: dict, need_start: bool) -> str:
    best = ""
    for rows in ((node or {}).get("units") or {}).values():
        for e in rows or []:
            if e.get("form") not in ("10-Q", "10-K") or not e.get("end"):
                continue
            if need_start and not e.get("start"):
                continue
            if e["end"] > best:
                best = e["end"]
    return best


def _order_by_freshness(facts: dict, tags: List[str], need_start: bool) -> List[str]:
    """Tags that exist, the one with the most recent period first; ties keep the given order (stable sort).
    WFC still has 'Revenues' rows, but only up to 2020 — its current revenue lives under another tag."""
    present = [(t, _latest_end(facts[t], need_start)) for t in tags if facts.get(t)]
    return [t for t, _ in sorted(present, key=lambda p: p[1], reverse=True)]


def _flow_quarters(rows: list) -> dict:
    """{period end: value} for ONE tag of a flow measure (revenue, profit, EPS, cash flow).
    A clean single-quarter row (~75-105 days) is taken as is. Otherwise the quarter is derived from year-to-date
    totals of the same fiscal year (same start date): Q2 = 6-month YTD - 3-month YTD, Q4 = full year - 9-month YTD.
    Grouping by start date (not by SEC's fy/fp fields, which belong to the FILING and also tag the prior-year
    comparatives) is what makes the four quarters add up exactly to the 10-K total."""
    out = {}
    entries = []
    for e in rows:
        if e.get("form") not in ("10-Q", "10-K"):
            continue
        start, end, val = e.get("start"), e.get("end"), e.get("val")
        if not start or not end or val is None:
            continue
        try:
            span = (date.fromisoformat(end) - date.fromisoformat(start)).days
        except Exception:
            continue
        entries.append((start, end, val))
        if 75 <= span <= 105:
            out.setdefault(end, val)

    by_start: dict = {}
    for start, end, val in entries:
        by_start.setdefault(start, []).append((end, val))
    for start, group in by_start.items():
        prev_end = date.fromisoformat(start)
        prev_val = 0.0
        for end, val in sorted(group, key=lambda x: x[0]):
            end_d = date.fromisoformat(end)
            gap = (end_d - prev_end).days
            if gap <= 0:
                continue        # the same period filed again (comparative / restated) — the first one is kept
            if end not in out and 60 <= gap <= 115:
                out[end] = val - prev_val
            prev_val, prev_end = val, end_d
    return out


def sec_quarterly_series(facts: dict, tag_candidates: List[str], instant: bool) -> dict:
    """{end date: value} merged across the candidate tags (companies switch tags between years).
    Flow measures: per tag (never subtracting one tag's YTD from another's), freshest tag first, older tags only
    fill dates the fresher ones lack. Instant measures (cash, debt, shares): point-in-time, given order."""
    out = {}
    if instant:
        for tag in tag_candidates:
            for e in _main_unit_rows(facts.get(tag)):
                if e.get("form") not in ("10-Q", "10-K"):
                    continue
                end, val = e.get("end"), e.get("val")
                if end is not None and val is not None:
                    out.setdefault(end, val)
        return out
    for tag in _order_by_freshness(facts, tag_candidates, need_start=True):
        for end, val in _flow_quarters(_main_unit_rows(facts[tag])).items():
            out.setdefault(end, val)
    return out


# Order matters on a tie (same latest quarter): the TOTAL-revenue tags first. Banks and utilities also file
# contract revenue, which for them is only a part (fees; NEE's is a rounded footnote figure).
REVENUE_TAGS = [
    "Revenues",
    "RevenuesNetOfInterestExpense",                            # banks / brokers (GS, JPM, WFC)
    "RegulatedAndUnregulatedOperatingRevenue",                 # utilities (NEE, DUK)
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",     # CRWD
    "SalesRevenueNet",
    "Revenue",                                                 # IFRS
]
NET_INCOME_TAGS = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                   "ProfitLossAttributableToOwnersOfParent"]
EPS_TAGS = ["EarningsPerShareDiluted", "EarningsPerShareBasic", "DilutedEarningsLossPerShare", "BasicEarningsLossPerShare"]
OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            "CashFlowsFromUsedInOperatingActivities"]
CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsForCapitalImprovements",
    # some large filers use one of these instead (one megacap had no FCF at all with the first two only)
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForProceedsFromProductiveAssets",
    "PaymentsToAcquireMachineryAndEquipment",
    "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    "PurchaseOfPropertyPlantAndEquipment",  # IFRS naming
]


# ------------------------------------------------------------------ Yahoo extras
def last_price(ticker: str) -> Optional[float]:
    h = yf.Ticker(ticker).history(period="5d")
    if h is None or h.empty:
        return None
    p = float(h["Close"].iloc[-1])
    return p if p > 0 else None


def _analyst_eps(ticker: str):
    """(labels, actual, estimate, surprise %) from Yahoo's earnings dates — SEC has no estimates."""
    labels, actual, estimate, surprise = [], [], [], []
    ed = yf.Ticker(ticker).get_earnings_dates(limit=12)
    if ed is not None and not ed.empty:
        ed = ed.dropna(subset=["Reported EPS"]).sort_index()
        for idx, row in ed.iterrows():
            labels.append(idx.strftime("%b %Y"))
            actual.append(round(float(row.get("Reported EPS")), 2) if pd.notna(row.get("Reported EPS")) else None)
            estimate.append(round(float(row.get("EPS Estimate")), 2) if pd.notna(row.get("EPS Estimate")) else None)
            sp = row.get("Surprise(%)")
            surprise.append(round(float(sp), 1) if pd.notna(sp) else None)
    return labels, actual, estimate, surprise


def compute_pe_band_sec(ticker: str, dates_sorted: List[str], diluted_eps: List[Optional[float]]):
    """Weekly P/E band: yfinance .history() for price, matched against SEC trailing-twelve-month diluted EPS."""
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
        # merge_asof needs the exact same datetime resolution on both sides (SEC strings vs yfinance index)
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


def _run_side_calls(calls: dict, budget_sec: float) -> dict:
    """Runs {name: zero-arg function} in parallel; returns {name: result} for the ones that finished in time
    without an error. NOT a `with` block: that would wait for a hung Yahoo call after the budget ran out."""
    ex = ThreadPoolExecutor(max_workers=max(1, len(calls)))
    futures = {name: ex.submit(fn) for name, fn in calls.items()}
    deadline = time.time() + budget_sec
    out = {}
    try:
        for name, fut in futures.items():
            try:
                out[name] = fut.result(timeout=max(0.1, deadline - time.time()))
            except FutureTimeoutError:
                print(f"[info] fundamentals: {name} took longer than {budget_sec}s — skipped")
            except Exception as e:
                print(f"[info] fundamentals: {name} unavailable (non-fatal): {type(e).__name__}: {e}")
    finally:
        ex.shutdown(wait=False)
    return out


# ------------------------------------------------------------------ foreign filers (Yahoo statements)
# SEC companyfacts only has quarterly numbers for 10-Q/10-K filers. Foreign private issuers (TSM, NVO, ASML) file
# an annual 20-F + unstructured 6-Ks, often in a local currency. Fallback: Yahoo's quarterly statements converted
# to USD at each quarter-end rate. Not shown for them, on purpose: EPS history, P/E band and share count (they
# depend on the unknown ADR ratio). Trailing P/E = market cap / LTM net income, independent of that ratio.
def _yf_row(df, names: List[str]) -> dict:
    """{date: float} for the first row name that exists with data."""
    if df is None or getattr(df, "empty", True):
        return {}
    for name in names:
        if name in df.index:
            out = {}
            for col, v in df.loc[name].items():
                try:
                    d = pd.Timestamp(col).strftime("%Y-%m-%d")
                    if pd.notna(v):
                        out[d] = float(v)
                except Exception:
                    continue
            if out:
                return out
    return {}


def _sec_reporting_currency(combined: dict) -> Optional[str]:
    """The currency of SEC's own XBRL units for revenue/profit (e.g. 'TWD' for TSMC)."""
    for tag in ("Revenues", "Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "ProfitLoss", "NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent"):
        node = (combined or {}).get(tag)
        if not node:
            continue
        for unit in node.get("units", {}):
            if re.fullmatch(r"[A-Z]{3}", unit):
                return unit
    return None


def _fx_to_usd_series(cur: str) -> Optional[pd.Series]:
    """Daily 'USD per 1 unit of cur'. None if unavailable — callers then return NO data, not mis-scaled numbers."""
    for sym, invert in ((f"{cur}USD=X", False), (f"USD{cur}=X", True)):
        try:
            h = yf.Ticker(sym).history(period="5y", interval="1d")
            if h is None or h.empty:
                continue
            ser = h["Close"].astype(float)
            ser.index = pd.to_datetime(ser.index).tz_localize(None).normalize()
            ser = ser[~ser.index.duplicated(keep="last")].sort_index()
            ser = ser[ser > 0]
            if ser.empty:
                continue
            return (1.0 / ser) if invert else ser
        except Exception as e:
            print(f"[info] fx {sym} failed: {e}")
    return None


def _build_fundamentals_yf(ticker: str, cik10: Optional[str], sec_currency: Optional[str],
                           info_getter: Optional[Callable]) -> Optional[dict]:
    tk = yf.Ticker(ticker)

    def grab(*attrs):
        for a in attrs:
            try:
                df = getattr(tk, a)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                print(f"[info] fundamentals({ticker}): yfinance {a} failed: {e}")
        return None

    side = _run_side_calls({
        "income": lambda: grab("quarterly_income_stmt", "quarterly_financials"),
        "cashflow": lambda: grab("quarterly_cashflow", "quarterly_cash_flow"),
        "balance": lambda: grab("quarterly_balance_sheet"),
        "info": lambda: (info_getter(tk, ticker) if info_getter else {}) or {},
        "price": lambda: last_price(ticker),
        "analyst_eps": lambda: _analyst_eps(ticker),
    }, SIDE_CALLS_BUDGET_SEC + 6)
    inc, cf, bs = side.get("income"), side.get("cashflow"), side.get("balance")
    info = side.get("info") or {}
    if inc is None:
        return None

    revenue_s = _yf_row(inc, ["Total Revenue", "Operating Revenue", "Revenue"])
    ni_s = _yf_row(inc, ["Net Income", "Net Income Common Stockholders",
                         "Net Income From Continuing Operation Net Minority Interest"])
    gross_s = _yf_row(inc, ["Gross Profit"])
    if not revenue_s and not ni_s:
        return None
    ocf_s = _yf_row(cf, ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"])
    fcf_s = _yf_row(cf, ["Free Cash Flow"])
    if not fcf_s and ocf_s:
        capex_s = _yf_row(cf, ["Capital Expenditure", "Purchase Of PPE"])
        fcf_s = {d: v + capex_s[d] for d, v in ocf_s.items() if d in capex_s}  # capex is reported negative
    cash_s = _yf_row(bs, ["Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"])
    debt_s = _yf_row(bs, ["Long Term Debt", "Total Debt", "Long Term Debt And Capital Lease Obligation"])

    cur = (sec_currency or info.get("financialCurrency") or "").upper() or None
    if not cur:
        print(f"[info] fundamentals({ticker}): reporting currency unknown — refusing to show possibly mis-scaled numbers")
        return None
    fx = None
    if cur != "USD":
        fx = _fx_to_usd_series(cur)
        if fx is None:
            print(f"[info] fundamentals({ticker}): no {cur}->USD rate — refusing to show unconverted numbers")
            return None

    def usd(series: dict) -> dict:
        if fx is None:
            return dict(series)
        out = {}
        for d, v in series.items():
            try:
                rate = fx.asof(pd.Timestamp(d))
                if pd.isna(rate):
                    rate = fx.iloc[0]
                out[d] = v * float(rate)
            except Exception:
                continue
        return out

    revenue_s, ni_s, gross_s, fcf_s, cash_s, debt_s = (usd(x) for x in (revenue_s, ni_s, gross_s, fcf_s, cash_s, debt_s))

    income_dates = sorted(set(revenue_s) | set(ni_s) | set(fcf_s))[-32:]
    fmt = lambda d: dt.strptime(d, "%Y-%m-%d").strftime("%b %Y")
    rnd = lambda x: round(float(x), 2) if x is not None else None
    revenue = [rnd(revenue_s.get(d)) for d in income_dates]
    net_income = [rnd(ni_s.get(d)) for d in income_dates]
    fcf = [rnd(fcf_s.get(d)) for d in income_dates]

    margin_dates = sorted(d for d in gross_s if revenue_s.get(d))[-32:]
    gross_margin_pct = [round(gross_s[d] / revenue_s[d] * 100, 4) for d in margin_dates]

    if debt_s:
        nd = {d: debt_s[d] - cash_s[d] for d in debt_s if d in cash_s}
    else:
        nd = {d: -v for d, v in cash_s.items()}
    nd_dates = sorted(nd)[-32:]

    # analyst EPS vs estimate: already on the ADR/USD basis Yahoo reports it in
    eps_labels, eps_actual, eps_estimate, eps_surprise_pct = side.get("analyst_eps") or ([], [], [], [])

    price = side.get("price")
    market_cap = info.get("marketCap")
    if not market_cap:
        try:
            market_cap = tk.fast_info.get("market_cap") or tk.fast_info["marketCap"]
        except Exception:
            market_cap = None

    ltm = lambda vals: round(sum(vals[-4:]), 2) if len(vals) >= 4 and all(v is not None for v in vals[-4:]) else None
    revenue_ltm, net_income_ltm, fcf_ltm = ltm(revenue), ltm(net_income), ltm(fcf)

    trailing_pe = info.get("trailingPE")
    if trailing_pe is None and market_cap and net_income_ltm and net_income_ltm > 0:
        trailing_pe = round(float(market_cap) / net_income_ltm, 1)  # ADR-ratio independent

    return json_safe({
        "ticker": ticker,
        "has_fundamentals": True,
        "data_source": "yfinance",
        "reporting_currency": cur,
        "fx_converted": cur != "USD",
        "cik": cik10,
        "income_labels": [fmt(d) for d in income_dates],
        "revenue": revenue, "net_income": net_income, "fcf": fcf,
        "revenue_ltm": revenue_ltm, "net_income_ltm": net_income_ltm, "fcf_ltm": fcf_ltm,
        "margin_labels": [fmt(d) for d in margin_dates], "gross_margin_pct": gross_margin_pct,
        "debt_labels": [fmt(d) for d in nd_dates], "net_debt": [round(float(nd[d]), 4) for d in nd_dates],
        "latest_shares_outstanding": None,
        "market_cap": market_cap,
        "price_at_build": price,
        "diluted_eps_labels": [], "diluted_eps": [],
        "eps_labels": eps_labels, "eps_actual": eps_actual,
        "eps_estimate": eps_estimate, "eps_surprise_pct": eps_surprise_pct,
        "pe_band": None,
        "trailing_pe": trailing_pe, "forward_pe": info.get("forwardPE"),
    })


def _fundamentals_fallback(ticker: str, cik10: Optional[str], combined: Optional[dict],
                           info_getter: Optional[Callable]) -> dict:
    """Wherever the SEC path has nothing: Yahoo's statements; else the 'no fundamentals' answer."""
    try:
        res = _build_fundamentals_yf(ticker, cik10, _sec_reporting_currency(combined or {}), info_getter)
    except Exception as e:
        print(f"[warn] fundamentals({ticker}): yfinance fallback failed: {type(e).__name__}: {e}")
        res = None
    if res:
        print(f"[info] fundamentals({ticker}): served from yfinance fallback ({res.get('reporting_currency')})")
        return res
    return {"ticker": ticker, "has_fundamentals": False}


# ------------------------------------------------------------------ the build
def _nearest(series: dict, target: str, tolerance_days: int = 6):
    """Value whose date is within tolerance_days of target (exact match preferred): a balance-sheet or cash-flow
    figure is often filed a few days off the income-statement quarter end."""
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
    """{date: (a, b)} anchored on series_a's dates (margin = gross+revenue; net debt = debt+cash)."""
    out = {}
    for d, va in series_a.items():
        vb = _nearest(series_b, d, tolerance_days)
        if vb is not None:
            out[d] = (va, vb)
    return out


def _to_chart(date_value_pairs: dict):
    """Most recent 32 points sorted by date -> (labels, values)."""
    items = sorted(date_value_pairs.items())[-32:]
    labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d, _ in items]
    values = [round(float(v), 4) if v is not None else None for d, v in items]
    return labels, values


def build_fundamentals(ticker: str, cik_map: dict, info_getter: Optional[Callable] = None) -> dict:
    """Full fundamentals for one ticker. info_getter(tk, ticker) -> dict is Yahoo's .info (the server passes its
    circuit-breaker version; None = skip .info, our own numbers are used)."""
    ticker = canon_ticker(ticker)
    # SEC's ticker file has no separator for class shares (BRK-B is "BRKB" there)
    cik10 = (cik_map or {}).get(ticker) or (cik_map or {}).get(ticker.replace("-", "").replace(".", ""))
    if not cik10:
        print(f"[info] fundamentals({ticker}): no SEC CIK match")
        return _fundamentals_fallback(ticker, None, None, info_getter)

    try:
        facts = sec_company_facts(cik10)
    except Exception as e:
        print(f"[warn] fundamentals({ticker}): SEC companyfacts fetch failed: {e}")
        facts = None
    if facts is None:
        # SEC itself did not answer (not "this company has no data"): Yahoo's statements for now, flagged so the
        # caller prefers a stored SEC copy and never stores this one
        res = _fundamentals_fallback(ticker, cik10, None, info_getter)
        res["sec_unavailable"] = True
        return res

    usgaap = facts.get("facts", {}).get("us-gaap", {})
    deifacts = facts.get("facts", {}).get("dei", {})
    # foreign private issuers often file under IFRS ("ifrs-full"); the IFRS tag names are in every candidate list
    ifrsfacts = facts.get("facts", {}).get("ifrs-full", {})
    combined = {**ifrsfacts, **usgaap}
    del facts, usgaap, ifrsfacts           # the parsed file is the big memory item on Render (512 MB)
    if not combined:
        print(f"[warn] fundamentals({ticker}): SEC companyfacts had no us-gaap or ifrs-full data")
        return _fundamentals_fallback(ticker, cik10, None, info_getter)

    revenue_s = sec_quarterly_series(combined, REVENUE_TAGS, instant=False)
    ni_s      = sec_quarterly_series(combined, NET_INCOME_TAGS, instant=False)
    gross_s   = sec_quarterly_series(combined, ["GrossProfit"], instant=False)
    eps_s     = sec_quarterly_series(combined, EPS_TAGS, instant=False)
    ocf_s     = sec_quarterly_series(combined, OCF_TAGS, instant=False)
    capex_s   = sec_quarterly_series(combined, CAPEX_TAGS, instant=False)
    cash_s    = sec_quarterly_series(combined, ["CashAndCashEquivalentsAtCarryingValue", "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations", "CashAndCashEquivalents"], instant=True)
    debt_s    = sec_quarterly_series(combined, ["LongTermDebtNoncurrent", "LongTermDebt", "DebtLongtermAndShorttermCombinedAmount", "NoncurrentBorrowings", "Borrowings"], instant=True)
    shares_s  = sec_quarterly_series(combined, ["CommonStockSharesOutstanding"], instant=True)
    # dei:EntityCommonStockSharesOutstanding is on every 10-Q/10-K cover page — the fallback for market cap
    for d, v in sec_quarterly_series(deifacts, ["EntityCommonStockSharesOutstanding"], instant=True).items():
        shares_s.setdefault(d, v)

    if not revenue_s and not ni_s:
        print(f"[info] fundamentals({ticker}): SEC facts had no usable quarterly (10-Q/10-K) revenue/net-income — "
              f"typical for foreign filers (20-F/6-K); trying yfinance statements")
        return _fundamentals_fallback(ticker, cik10, combined, info_getter)

    fcf_s = {}
    for d, ov in ocf_s.items():
        cv = _nearest(capex_s, d)
        if cv is not None:
            fcf_s[d] = ov - cv
    if ocf_s and not fcf_s:
        capex_like_tags = [k for k in combined if "Payments" in k and ("Property" in k or "Capital" in k or "Productive" in k or "Equipment" in k)]
        print(f"[warn] fundamentals({ticker}): OCF present but no CapEx match — candidate tags on file: {capex_like_tags}")

    # each chart card gets its own compact date axis (a shared one padded with balance-sheet dates gave blank columns)
    income_dates = sorted(set(revenue_s) | set(ni_s) | set(fcf_s))[-32:]
    income_labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d in income_dates]
    revenue    = [round(float(v), 2) if (v := _nearest(revenue_s, d)) is not None else None for d in income_dates]
    net_income = [round(float(v), 2) if (v := _nearest(ni_s, d)) is not None else None for d in income_dates]
    fcf        = [round(float(v), 2) if (v := _nearest(fcf_s, d)) is not None else None for d in income_dates]

    margin_ratio = {d: (g / r * 100) for d, (g, r) in _nearest_pair(gross_s, revenue_s).items() if r}
    margin_labels, gross_margin_pct = _to_chart(margin_ratio)

    # no debt tag at all (SHOP and many growth names) = nothing to report, not missing: net debt = -cash
    if debt_s:
        net_debt_map = {d: (dv - cv) for d, (dv, cv) in _nearest_pair(debt_s, cash_s).items()}
    elif cash_s:
        net_debt_map = {d: -cv for d, cv in cash_s.items()}
    else:
        net_debt_map = {}
    debt_labels, net_debt = _to_chart(net_debt_map)

    eps_dates = sorted(eps_s)[-32:]
    diluted_eps_labels = [dt.strptime(d, "%Y-%m-%d").strftime("%b %Y") for d in eps_dates]
    diluted_eps = [round(float(v), 4) if (v := eps_s.get(d)) is not None else None for d in eps_dates]

    latest_shares = None
    if shares_s:
        latest_shares = round(float(shares_s[max(shares_s)]), 0)
    del combined

    # Yahoo extras, in parallel, within one time budget (each used to run one after the other)
    calls = {
        "pe_band": lambda: compute_pe_band_sec(ticker, eps_dates, diluted_eps),
        "analyst_eps": lambda: _analyst_eps(ticker),
        "price": lambda: last_price(ticker),
    }
    if info_getter:
        calls["info"] = lambda: info_getter(yf.Ticker(ticker), ticker) or {}
    side = _run_side_calls(calls, SIDE_CALLS_BUDGET_SEC)
    pe_band = side.get("pe_band")
    eps_labels, eps_actual, eps_estimate, eps_surprise_pct = side.get("analyst_eps") or ([], [], [], [])
    info = side.get("info") or {}
    price = side.get("price")

    # LTM = sum of the last 4 quarters (how Koyfin and most terminals show it), only when all 4 are there
    def _ltm_sum(values):
        recent = [v for v in values[-4:] if v is not None]
        return round(sum(recent), 2) if len(recent) == 4 else None

    revenue_ltm = _ltm_sum(revenue)
    net_income_ltm = _ltm_sum(net_income)
    fcf_ltm = _ltm_sum(fcf)

    # market cap = SEC shares x last close (.info is usually blocked from Render; its marketCap only as fallback)
    market_cap = round(price * latest_shares, 0) if (price and latest_shares) else None
    if market_cap is None:
        market_cap = info.get("marketCap")

    # trailing P/E: Yahoo's if it came through, else market cap / LTM net income (needs no per-share history),
    # else the P/E band's last point — only if that point is recent (it skips loss weeks, so it can be years old)
    trailing_pe = info.get("trailingPE")
    if trailing_pe is None and market_cap and net_income_ltm is not None:
        if net_income_ltm > 0:
            trailing_pe = round(float(market_cap) / net_income_ltm, 1)
    elif trailing_pe is None and pe_band and pe_band.get("current") and pe_band.get("dates"):
        try:
            if (date.today() - date.fromisoformat(pe_band["dates"][-1])).days <= 21:
                trailing_pe = pe_band["current"]
        except Exception:
            pass

    return json_safe({
        "ticker": ticker,
        "has_fundamentals": True,
        "data_source": "sec_edgar",
        "cik": cik10,
        "income_labels": income_labels,
        "revenue": revenue,
        "net_income": net_income,
        "fcf": fcf,
        "revenue_ltm": revenue_ltm,
        "net_income_ltm": net_income_ltm,
        "fcf_ltm": fcf_ltm,
        "margin_labels": margin_labels,
        "gross_margin_pct": gross_margin_pct,
        "debt_labels": debt_labels,
        "net_debt": net_debt,
        "latest_shares_outstanding": latest_shares,
        "market_cap": market_cap,
        "price_at_build": price,
        "diluted_eps_labels": diluted_eps_labels,
        "diluted_eps": diluted_eps,
        "eps_labels": eps_labels,
        "eps_actual": eps_actual,
        "eps_estimate": eps_estimate,
        "eps_surprise_pct": eps_surprise_pct,
        "pe_band": pe_band,
        "trailing_pe": trailing_pe,
        "forward_pe": info.get("forwardPE"),
    })


# ------------------------------------------------------------------ after the build
_ESTIMATES_MAX_AGE_DAYS = 10


def apply_stock_estimates(data: dict, ticker: str, cursor_ctx) -> None:
    """Forward P/E for the next twelve months (NTM) from the analyst estimates the scanner stores twice a day
    (pipeline_a_scanner.save_stock_estimates — Yahoo's .info works there, not on Render):
        f = days left in the current fiscal year / 365   (0 once it has ended but is not reported yet)
        NTM EPS = f * EPS(current FY) + (1 - f) * EPS(next FY)
    e.g. KO on 26.10.2026: 66 days left -> 0.18*3.31 + 0.82*3.53 = 3.49 -> $92.6 / 3.49 = 26.5. Only when no P/E
    came from elsewhere; the stored trailing P/E / market cap fill gaps too. cursor_ctx(dict_cursor=True) yields
    (conn, cur) — api_server.db_cursor or the warm-up job's own."""
    if not isinstance(data, dict) or not data.get("has_fundamentals"):
        return
    with cursor_ctx(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT to_regclass('stock_estimates') IS NOT NULL AS ok")
        if not cur.fetchone()["ok"]:
            return
        cur.execute("""SELECT eps_cur_fy, eps_next_fy, fy_end, trailing_pe, market_cap FROM stock_estimates
                       WHERE ticker = %s AND updated_at > NOW() - make_interval(days => %s)""",
                    (ticker, _ESTIMATES_MAX_AGE_DAYS))
        row = cur.fetchone()
    if not row:
        return
    if data.get("market_cap") is None and row["market_cap"]:
        data["market_cap"] = float(row["market_cap"])
    if data.get("trailing_pe") is None and row["trailing_pe"] and not (data.get("net_income_ltm") is not None
                                                                       and data["net_income_ltm"] <= 0):
        data["trailing_pe"] = round(float(row["trailing_pe"]), 1)
    if data.get("forward_pe") is not None:
        return
    eps_next = row["eps_next_fy"]
    eps_cur = row["eps_cur_fy"]
    if eps_next is None and eps_cur is None:
        return
    f = 0.0
    if row["fy_end"] and eps_cur is not None:
        f = min(max((row["fy_end"] - date.today()).days, 0), 365) / 365.0
    if eps_next is None:
        eps_ntm = float(eps_cur)
    elif eps_cur is None:
        eps_ntm = float(eps_next)
    else:
        eps_ntm = f * float(eps_cur) + (1 - f) * float(eps_next)
    price = data.get("price_at_build")
    shares = data.get("latest_shares_outstanding")
    if not price and data.get("market_cap") and shares:
        price = float(data["market_cap"]) / float(shares)
    if not price:
        try:
            price = last_price(ticker)
        except Exception:
            price = None
    if price and eps_ntm > 0:
        data["forward_pe"] = round(price / eps_ntm, 1)
        data["forward_pe_basis"] = "ntm"


def mark_pe(data: dict) -> dict:
    """A loss (LTM net income <= 0) or a P/E above PE_NM_ABOVE has no meaningful P/E: the number is dropped and
    trailing_pe_nm says why ("loss" / "high") — the site shows a word instead of a misleading figure (CRWD 4611.7)."""
    data.pop("trailing_pe_nm", None)
    ni = data.get("net_income_ltm")
    pe = data.get("trailing_pe")
    if ni is not None and ni <= 0:
        data["trailing_pe"] = None
        data["trailing_pe_nm"] = "loss"
    elif pe is not None and (pe <= 0 or pe > PE_NM_ABOVE):
        data["trailing_pe"] = None
        data["trailing_pe_nm"] = "high"
    fpe = data.get("forward_pe")
    if fpe is not None and (fpe <= 0 or fpe > PE_NM_ABOVE):
        data["forward_pe"] = None
    return data


def carry_over_extras(new: dict, old: Optional[dict]) -> None:
    """A Yahoo extra that failed this time (rate limit, slow) is taken from the previous good copy instead of
    disappearing until the next rebuild. The SEC numbers themselves are never carried over."""
    if not old or not old.get("has_fundamentals") or not new.get("has_fundamentals"):
        return
    if not new.get("eps_labels") and old.get("eps_labels"):
        for k in ("eps_labels", "eps_actual", "eps_estimate", "eps_surprise_pct"):
            new[k] = old.get(k) or []
    if not new.get("pe_band") and old.get("pe_band") and new.get("data_source") == "sec_edgar":
        new["pe_band"] = old["pe_band"]


def finalize(data: dict) -> dict:
    """Stamp + P/E sanity. Call after apply_stock_estimates."""
    if data.get("has_fundamentals"):
        mark_pe(data)
        data["built_at"] = dt.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["built_ts"] = time.time()
        data["cache_version"] = CACHE_VERSION
    return data


def rescale_to_price(data: dict, price: Optional[float]) -> dict:
    """A stored copy's market cap and P/Es move with the price (shares and earnings do not): scaled by
    price now / price_at_build. Returns a new dict; the stored one is left alone."""
    base = data.get("price_at_build") if isinstance(data, dict) else None
    if not price or not base or base <= 0:
        return data
    r = float(price) / float(base)
    if abs(r - 1) < 0.0005:
        return data
    out = dict(data)
    if out.get("market_cap"):
        out["market_cap"] = round(float(out["market_cap"]) * r, 0)
    for k in ("trailing_pe", "forward_pe"):
        if out.get(k):
            out[k] = round(float(out[k]) * r, 1)
    out["price_now"] = round(float(price), 4)
    if out.get("trailing_pe") is not None and out["trailing_pe"] > PE_NM_ABOVE:
        out["trailing_pe"] = None
        out["trailing_pe_nm"] = "high"
    if out.get("forward_pe") is not None and out["forward_pe"] > PE_NM_ABOVE:
        out["forward_pe"] = None
    return out


# ------------------------------------------------------------------ Postgres cache
def ensure_table(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS fundamentals_cache (
        ticker TEXT PRIMARY KEY,
        data JSONB NOT NULL,
        version INTEGER NOT NULL DEFAULT 0,
        built_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")


def db_save(cur, ticker: str, data: dict) -> None:
    """Only real SEC-or-foreign-filer results are stored; a 'no data' answer is often a passing hiccup, and a
    Yahoo stand-in built while SEC was down must not replace the SEC copy."""
    if not data.get("has_fundamentals") or data.get("sec_unavailable"):
        return
    cur.execute("""INSERT INTO fundamentals_cache (ticker, data, version, built_at) VALUES (%s, %s::jsonb, %s, NOW())
                   ON CONFLICT (ticker) DO UPDATE SET data = EXCLUDED.data, version = EXCLUDED.version,
                                                      built_at = EXCLUDED.built_at""",
                (ticker, json.dumps(json_safe(data), allow_nan=False), CACHE_VERSION))


def db_load(cur, ticker: str):
    """(data, age in seconds) of the stored copy of THIS logic version, or (None, None)."""
    cur.execute("""SELECT data, EXTRACT(EPOCH FROM (NOW() - built_at)) AS age FROM fundamentals_cache
                   WHERE ticker = %s AND version = %s""", (ticker, CACHE_VERSION))
    row = cur.fetchone()
    if not row:
        return None, None
    data, age = (row["data"], row["age"]) if isinstance(row, dict) else (row[0], row[1])
    if isinstance(data, str):
        data = json.loads(data)
    return data, float(age)


# ------------------------------------------------------------------ nightly warm-up (GitHub Actions)
WARM_SKIP_IF_YOUNGER_H = 18     # built more recently than this -> skipped (a re-run the same day is cheap)
WARM_MAX_TICKERS = 1500
WARM_WORKERS = 4                # SEC allows 10 requests/s; each build makes one SEC call + a few Yahoo calls
WARM_BUDGET_MIN = 45            # stop starting new tickers after this (the workflow times out at 60)


def _csv_tickers(url: str, col_names=("Symbol", "Ticker", "symbol", "ticker")) -> List[str]:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        for c in col_names:
            if c in df.columns:
                return [canon_ticker(str(x)) for x in df[c].dropna()]
    except Exception as e:
        print(f"[warn] warm-up: ticker list {url} failed: {e}")
    return []


def _warm_tickers(cursor_ctx) -> List[str]:
    """Priority order: watchlists, recent scanner hits, S&P 500, Nasdaq 100, the scanner's estimate universe."""
    ordered: List[str] = []
    queries = [
        "SELECT DISTINCT ticker FROM watchlist",
        "SELECT ticker FROM scanned_stocks WHERE timestamp > NOW() - INTERVAL '60 days' GROUP BY ticker ORDER BY MAX(timestamp) DESC",
    ]
    for q in queries:
        try:
            with cursor_ctx() as (conn, cur):
                cur.execute(q)
                ordered += [canon_ticker(r[0]) for r in cur.fetchall() if r[0]]
        except Exception as e:
            print(f"[warn] warm-up: {q[:40]}... failed: {e}")
    ordered += _csv_tickers("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv")
    ordered += _csv_tickers("https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv")
    try:
        with cursor_ctx() as (conn, cur):
            cur.execute("SELECT to_regclass('stock_estimates') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT ticker FROM stock_estimates ORDER BY market_cap DESC NULLS LAST")
                ordered += [canon_ticker(r[0]) for r in cur.fetchall() if r[0]]
    except Exception as e:
        print(f"[warn] warm-up: stock_estimates list failed: {e}")
    seen, out = set(), []
    for t in ordered:
        if t and re.fullmatch(r"[A-Z]{1,5}(-[A-Z])?", t) and t not in seen:
            seen.add(t)
            out.append(t)
    return out[:WARM_MAX_TICKERS]


def warm_main() -> int:
    import psycopg2
    import psycopg2.extras
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set")
        return 1

    @contextmanager
    def cursor_ctx(dict_cursor: bool = False):
        conn = psycopg2.connect(db_url)         # Neon auto-suspends: a fresh short connection per use
        cur = None
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if dict_cursor else conn.cursor()
            yield conn, cur
        finally:
            try:
                if cur is not None:
                    cur.close()
            finally:
                conn.close()

    started = time.time()
    with cursor_ctx() as (conn, cur):
        ensure_table(cur)
        conn.commit()
    tickers = _warm_tickers(cursor_ctx)
    with cursor_ctx() as (conn, cur):
        cur.execute("""SELECT ticker FROM fundamentals_cache WHERE version = %s
                       AND built_at > NOW() - make_interval(hours => %s)""", (CACHE_VERSION, WARM_SKIP_IF_YOUNGER_H))
        fresh = {r[0] for r in cur.fetchall()}
    todo = [t for t in tickers if t not in fresh]
    print(f"fundamentals warm-up: {len(tickers)} tickers, {len(fresh & set(tickers))} already fresh, {len(todo)} to build")
    cik_map = fetch_cik_map()
    stats = {"saved": 0, "no_data": 0, "failed": 0, "skipped_time": 0}

    def one(t: str):
        if time.time() - started > WARM_BUDGET_MIN * 60:
            stats["skipped_time"] += 1
            return
        try:
            data = build_fundamentals(t, cik_map, info_getter=None)
            if data.get("sec_unavailable"):
                stats["failed"] += 1
                print(f"[warn] warm-up {t}: SEC did not answer — not stored")
                return
            if not data.get("has_fundamentals"):
                stats["no_data"] += 1
                return
            apply_stock_estimates(data, t, cursor_ctx)
            with cursor_ctx(dict_cursor=True) as (conn, cur):
                old, _ = db_load(cur, t)
            carry_over_extras(data, old)
            finalize(data)
            with cursor_ctx() as (conn, cur):
                db_save(cur, t, data)
                conn.commit()
            stats["saved"] += 1
        except Exception as e:
            stats["failed"] += 1
            print(f"[warn] warm-up {t}: {type(e).__name__}: {e}")

    with ThreadPoolExecutor(max_workers=WARM_WORKERS) as ex:
        list(ex.map(one, todo))
    print(f"fundamentals warm-up: saved {stats['saved']}, no data {stats['no_data']}, failed {stats['failed']}, "
          f"not reached (time budget) {stats['skipped_time']}, {int(time.time() - started)}s")
    return 0


if __name__ == "__main__":
    sys.exit(warm_main())
