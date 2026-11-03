"""
Data health check (v32) — is what the site shows actually right?
================================================================
Runs in GitHub Actions (data-health.yml), started by scheduler.py once a weekday at 07:30 ET. The site does not use it.
READ ONLY on every existing table; the only thing it writes is its own report (table data_health, one row per day,
60 days kept), which /api/data-health serves. So the first step of a work session: read that report.

Why: tests/ checks the CODE. Nothing checked the DATA, so wrong numbers stayed on the site until someone compared by hand
(3.11.2026: 498 of 503 stocks, EQR showing a figure from August as today's, ALGN lost to a failed download).

The checks (a problem found = a line in the report, never a stop; a check that crashes is reported as fail):
  * freshness: when each table last changed, counted in weekdays so a weekend or a holiday is not a false alarm
  * counts: how many stocks the site really shows per index, and which list members have no fresh data
  * arithmetic: numbers that must add up (breadth: up + down + unchanged = counted, above 50d <= with a 50d average ...)
  * values: impossible figures (a price of 0, a NaN, a one-day move of 50% or more)

status: ok / info (worth knowing, nothing to fix: KVUE still on the free list) / warn (look at it) / fail (wrong or stopped).
Exit code 0 even when problems are found (a red run would email the owner, the report is where this is read).
Each new "wrong number" found on the site should become a new rule here.
"""

import csv
import io
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Set
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras
import requests

ET = ZoneInfo("America/New_York")
SP500_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
NASDAQ100_URL = "https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv"

MOVERS_MAX_AGE_DAYS = 4         # same rule as api_server.py (a test keeps the two equal)
SP500_RANGE = (495, 510)        # 503 shares of ~500 companies; the free list can lag by a few
NASDAQ100_RANGE = (97, 105)
MAX_ABSENT_INFO = 5             # up to this many list members without fresh data = probably delisted, more = a problem
BREADTH_MAX_NO_CHANGE = 5       # stocks counted without a change figure (first trading day, a gap in yesterday's bar)
BREADTH_MIN_PRICED = 0.98       # share of the list with a price on the latest day
BIG_MOVE_PCT = 50               # a one-day move this big is rare enough to be looked at
NEWS_MAX_HOURS = 6              # the news job runs every 30 minutes, every day
KEEP_DAYS = 60

# (table, time column, most weekdays since the last change, status when older). Normal = 1 at 07:30 ET (yesterday's
# evening job), 2 = one market holiday in between, so 2 is the limit for what runs every trading day.
FRESHNESS = [
    ("universe_movers", "timestamp", 2, "fail"),
    ("earnings_calendar", "updated_at", 2, "fail"),
    ("market_breadth", "updated_at", 2, "fail"),
    ("market_regime", "updated_at", 2, "fail"),
    ("treasury_yields", "day", 3, "warn"),          # bond-market holidays, and the curve is published with a lag
    ("stock_estimates", "updated_at", 2, "fail"),
    ("stock_leaders", "updated_at", 2, "fail"),
    ("fundamentals_cache", "built_at", 2, "warn"),
    ("insider_filings_seen", "seen_at", 2, "warn"),
    ("ai_daily_digests", "updated_at", 2, "warn"),
    ("ai_stock_notes", "created_at", 2, "warn"),
    ("econ_snapshots", "updated_at", 2, "warn"),
    ("scanned_stocks", "timestamp", 3, "warn"),     # the quality gate can leave a quiet day with no row at all
]
_RANK = {"ok": 0, "info": 0, "warn": 1, "fail": 2}


# ------------------------------------------------------------------ pure rules (tests/test_core.py)
def weekdays_since(day: date, today: date) -> int:
    """Weekdays after `day`, up to and including `today`: Friday -> Monday = 1, Saturday -> Monday = 1, same day = 0."""
    n = 0
    while day < today:
        day += timedelta(days=1)
        if day.weekday() < 5:
            n += 1
    return n


def breadth_problems(r: dict) -> List[str]:
    """What does not add up in one market_breadth row (empty = fine)."""
    out = []
    n = r["n"] or 0
    moved = (r["advancers"] or 0) + (r["decliners"] or 0) + (r.get("unchanged") or 0)
    if moved > n:
        out.append(f"up+down+unchanged {moved} > counted {n}")
    elif n - moved > BREADTH_MAX_NO_CHANGE:
        out.append(f"{n - moved} of {n} stocks are neither up, down nor unchanged")
    for part, whole in (("above50", "n50"), ("above200", "n200"), ("new_highs", "n_hl"), ("new_lows", "n_hl")):
        if (r[part] or 0) > (r[whole] or 0):
            out.append(f"{part} {r[part]} > {whole} {r[whole]}")
    for whole in ("n50", "n200", "n_hl"):
        if (r[whole] or 0) > n:
            out.append(f"{whole} {r[whole]} > counted {n}")
    total = r.get("total")
    if total and n > total:
        out.append(f"counted {n} > list {total}")
    return out


def overall_status(results: List[dict]) -> str:
    worst = max((_RANK.get(x["status"], 2) for x in results), default=0)
    return {0: "ok", 1: "warn", 2: "fail"}[worst]


def _line(check: str, status: str, detail: str, value=None) -> dict:
    return {"check": check, "status": status, "value": value, "detail": detail}


def _as_et_date(v) -> date:
    return v.astimezone(ET).date() if isinstance(v, datetime) else v


# ------------------------------------------------------------------ the checks
def check_freshness(cur, now: datetime) -> List[dict]:
    today, out = now.astimezone(ET).date(), []
    for table, col, limit, bad in FRESHNESS:
        check = f"fresh.{table}"
        try:                      # names come from the list above, never from outside
            cur.execute(f'SELECT MAX("{col}") AS v FROM {table}')
            last = cur.fetchone()["v"]
        except Exception as e:
            out.append(_line(check, "fail", f"could not read: {type(e).__name__}: {e}"))
            continue
        if last is None:
            out.append(_line(check, "fail", "the table is empty"))
            continue
        age = weekdays_since(_as_et_date(last), today)
        out.append(_line(check, "ok" if age <= limit else bad, f"last change {last}, {age} weekday(s) ago (limit {limit})", age))
    try:
        cur.execute('SELECT MAX("timestamp") AS v FROM macro_news')
        last = cur.fetchone()["v"]
        hours = round((now - last).total_seconds() / 3600, 1) if last else None
        ok = hours is not None and hours <= NEWS_MAX_HOURS
        out.append(_line("fresh.macro_news", "ok" if ok else "fail", f"last item {last}, {hours} hour(s) ago (limit {NEWS_MAX_HOURS})", hours))
    except Exception as e:
        out.append(_line("fresh.macro_news", "fail", f"could not read: {type(e).__name__}: {e}"))
    return out


def fetch_members(url: str) -> Optional[Set[str]]:
    """Today's members from the same free list the scanner uses; None = unavailable (the check says so, no false alarm)."""
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        col = "Symbol" if "Symbol" in (reader.fieldnames or []) else reader.fieldnames[0]
        return {row[col].strip().upper().replace(".", "-") for row in reader if (row.get(col) or "").strip()}
    except Exception as e:
        print(f"[warn] data health: member list {url}: {type(e).__name__}: {e}")
        return None


def check_counts(cur, members: Dict[str, Optional[Set[str]]]) -> List[dict]:
    """The same rows /api/heatmap serves — so this is the number of stocks the site really shows."""
    cur.execute("""SELECT ticker, in_sp500, in_nasdaq100 FROM universe_movers
                   WHERE change_pct IS NOT NULL AND change_pct <> 'NaN'
                     AND "timestamp" >= (SELECT MAX("timestamp") FROM universe_movers) - make_interval(days => %s)""",
                (MOVERS_MAX_AGE_DAYS,))
    rows = cur.fetchall()
    fresh = {r["ticker"] for r in rows}
    out = []
    counts = {}
    for name, flag, (lo, hi) in (("sp500", "in_sp500", SP500_RANGE), ("nasdaq100", "in_nasdaq100", NASDAQ100_RANGE)):
        n = counts[name] = sum(1 for r in rows if r[flag])
        out.append(_line(f"count.{name}", "ok" if lo <= n <= hi else "fail", f"{n} stocks shown (expected {lo}-{hi})", n))
        listed = members.get(name)
        if listed is None:
            out.append(_line(f"absent.{name}", "info", "member list unavailable, not checked"))
            continue
        absent = sorted(listed - fresh)
        status = "ok" if not absent else ("info" if len(absent) <= MAX_ABSENT_INFO else "warn")
        out.append(_line(f"absent.{name}", status, f"{len(absent)} of {len(listed)} list members have no fresh data: "
                                                   f"{', '.join(absent[:30]) or 'none'}", len(absent)))
    cur.execute("SELECT day, total FROM market_breadth ORDER BY day DESC LIMIT 1")
    r = cur.fetchone()
    if r and r["total"]:
        gap = abs(r["total"] - counts["sp500"])
        out.append(_line("count.breadth_vs_heatmap", "ok" if gap <= 5 else "warn",
                         f"breadth counts {r['total']} stocks, the heatmap {counts['sp500']}", gap))
    return out


def check_arithmetic(cur) -> List[dict]:
    cur.execute("SELECT * FROM market_breadth ORDER BY day DESC LIMIT 5")
    rows = cur.fetchall()
    if not rows:
        return [_line("sum.breadth", "fail", "no breadth rows")]
    problems = [f"{r['day']}: {p}" for r in rows for p in breadth_problems(r)]
    out = [_line("sum.breadth", "fail" if problems else "ok", "; ".join(problems) or f"last {len(rows)} days add up")]
    last = rows[0]
    if last.get("total"):
        share = (last["n"] or 0) / last["total"]
        out.append(_line("sum.breadth_priced", "ok" if share >= BREADTH_MIN_PRICED else "warn",
                         f"{last['n']} of {last['total']} stocks had a price on {last['day']}", round(share * 100, 1)))
    return out


def check_values(cur) -> List[dict]:
    cur.execute("""SELECT ticker, change_pct, close_price FROM universe_movers
                   WHERE "timestamp" >= (SELECT MAX("timestamp") FROM universe_movers) - make_interval(days => %s)
                     AND (close_price IS NULL OR close_price <= 0 OR close_price = 'NaN'
                          OR change_pct = 'NaN' OR ABS(change_pct) >= %s)""", (MOVERS_MAX_AGE_DAYS, BIG_MOVE_PCT))
    odd = cur.fetchall()
    detail = ", ".join(f"{r['ticker']} ({r['change_pct']}%, ${r['close_price']})" for r in odd[:20])
    out = [_line("value.movers", "warn" if odd else "ok", detail or f"no price of 0, no NaN, no move of {BIG_MOVE_PCT}%+", len(odd))]
    cur.execute("SELECT day, spy_close, spy_sma50, spy_sma200, vix FROM market_regime ORDER BY day DESC LIMIT 1")
    r = cur.fetchone()
    if r:
        bad = [k for k in ("spy_close", "spy_sma50", "spy_sma200", "vix")
               if r[k] is None or not math.isfinite(r[k]) or r[k] <= 0]
        out.append(_line("value.regime", "fail" if bad else "ok", f"{r['day']}: " + (f"missing or impossible: {', '.join(bad)}" if bad
                         else f"SPY {r['spy_close']}, VIX {r['vix']}")))
    return out


# ------------------------------------------------------------------ report
def save(conn, day: date, status: str, results: List[dict]) -> None:
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS data_health (
        day DATE PRIMARY KEY, checked_at TIMESTAMPTZ DEFAULT NOW(), status TEXT NOT NULL, results JSONB NOT NULL)""")
    cur.execute("""INSERT INTO data_health (day, checked_at, status, results) VALUES (%s, NOW(), %s, %s)
                   ON CONFLICT (day) DO UPDATE SET checked_at=NOW(), status=EXCLUDED.status, results=EXCLUDED.results""",
                (day, status, psycopg2.extras.Json(results)))
    cur.execute("DELETE FROM data_health WHERE day < %s", (day - timedelta(days=KEEP_DAYS),))
    cur.close()


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL is not set")
        return 1
    members = {"sp500": fetch_members(SP500_URL), "nasdaq100": fetch_members(NASDAQ100_URL)}
    now = datetime.now(timezone.utc)
    conn = psycopg2.connect(db_url)        # opened only now, after the downloads (Neon auto-suspend)
    conn.autocommit = True                 # a failed query must not block the checks after it
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        results = []
        for name, run in (("freshness", lambda: check_freshness(cur, now)), ("counts", lambda: check_counts(cur, members)),
                          ("arithmetic", lambda: check_arithmetic(cur)), ("values", lambda: check_values(cur))):
            try:
                results += run()
            except Exception as e:
                results.append(_line(name, "fail", f"the check crashed: {type(e).__name__}: {e}"))
        cur.close()
        status = overall_status(results)
        save(conn, now.astimezone(ET).date(), status, results)
    finally:
        conn.close()
    for x in results:
        if x["status"] != "ok":
            print(f"[{x['status']}] {x['check']}: {x['detail']}")
    print(f"data health: {status} — {sum(x['status'] == 'ok' for x in results)} of {len(results)} checks ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
