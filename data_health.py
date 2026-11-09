"""
Data health check (v32) — is what the site shows actually right?
================================================================
Runs in GitHub Actions (data-health.yml), started by scheduler.py once a weekday at 07:30 ET. The site does not use it.
READ ONLY on every existing table; the only thing it writes is its own report (table data_health, one row per day,
60 days kept), which /api/data-health serves, and (v38) db_meter.py adds this run's own reads to db_usage, like every
job's. So the first step of a work session: read that report.

Why: tests/ checks the CODE. Nothing checked the DATA, so wrong numbers stayed on the site until someone compared by hand
(3.11.2026: 498 of 503 stocks, EQR showing a figure from August as today's, ALGN lost to a failed download).

The checks (a problem found = a line in the report, never a stop; a check that crashes is reported as fail):
  * freshness: when each table last changed, counted in weekdays so a weekend or a holiday is not a false alarm
  * counts: how many stocks the site really shows per index, and which list members have no fresh data
  * arithmetic: numbers that must add up (breadth: up + down + unchanged = counted, above 50d <= with a 50d average ...)
  * values: impossible figures (a price of 0, a NaN, a one-day move of 50% or more)
  * earnings: the last trading day's reports that still have no result
  * market cap (v34): the stock page's SEC-based figure against the scanner's (BRK-B showed $540M, Visa's P/E was 9)
  * size (v34): how much of the database's storage is used, and by which tables
  * company profiles (v35): an impossible staff count, an encyclopedia text that does not name the company (KO's said
    400 employees, MSFT's described a court case)
  * database reads (v38): yesterday's estimate from db_meter.py, by source (the free Neon plan stops at 5 GB a month)

status: ok / info (worth knowing, nothing to fix: KVUE still on the free list) / warn (look at it) / fail (wrong or stopped).
Exit code 0 even when problems are found (a red run would email the owner, the report is where this is read).
Each new "wrong number" found on the site should become a new rule here.
"""

import csv
import io
import json
import math
import os
import re
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
MAX_BLANK_REPORTS = 3           # yesterday's reports with no result at all: a few can be a moved date, more = a problem
FUND_MCAP_MAX_RATIO = 1.5       # wider than the 1.3 fundamentals.py corrects at: the two are built at different prices
DB_SIZE_WARN_MB, DB_SIZE_FAIL_MB = 700, 900   # the free Neon plan: 1 GB per project (its pricing page, 4.11.2026)
BIG_COMPANY_CAP = 10e9          # a company this large with under PROFILE_MIN_STAFF employees: the figure is wrong
PROFILE_MIN_STAFF = 500
PROFILE_STAFF_CHECKED = {"RGLD"}   # read in the annual report: Royal Gold owns royalties and really has 39 employees (6.11.2026)
KEEP_DAYS = 60
# v38: db_meter.py counts what each part reads; a connection's handshake is not counted there (~5 KB, an estimate: the
# server certificates + login). 5 GB a month = ~160 MB a day; a day over 100 = more than 3 GB a month at that pace.
CONN_OVERHEAD_BYTES = 5000
DB_DAY_WARN_MB = 100

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
    ("analyst_ratings", "seen_at", 2, "warn"),      # v39: every run writes its own row, also on a day with no change
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


MOVERS_MAX_GAP = 60             # stocks. The two are computed apart, and a real gap of a few comes from timing


def movers_gap(up, down, advancers, decliners) -> int:
    """v37e: the heatmap against the breadth of the last session. A wide gap = the heatmap holds another day's moves, with a fresh
    time on its rows (7.11.2026: 73 up and 430 down from Thursday, on a Friday with 184 up and 318 down)."""
    return max(abs((up or 0) - (advancers or 0)), abs((down or 0) - (decliners or 0)))


def digest_problems(payload: dict) -> List[str]:
    """v37: the day's AI summary against the day's real numbers, written by ai_digest.py. A large move the overview does not name
    = the summary missed the main story (5.11.2026: S&P 500 -4.36%, and the overview spoke of "technology and earnings")."""
    market = payload.get("market") or {}
    overview = payload.get("overview_en") or ""
    if market and market.get("same_day") is False:
        # v38b: 7-9.11.2026, a weekend and a Monday morning told with Friday's +0.31% / +1.09% / -0.46%
        told = [f"{i['name_en']} {i['change_pct']}%" for i in market.get("indices") or []
                if i.get("symbol") in ("^GSPC", "^IXIC", "^DJI") and f"{abs(i['change_pct']):.2f}%" in overview]
        return [f"no session that day, and the overview tells the last one: {', '.join(told)}"] if told else []
    if market.get("move") != "extreme":
        return []
    if re.search(r"S&P|Nasdaq|Dow\b|Wall Street", payload.get("overview_en") or ""):
        return []
    sp = next((i["change_pct"] for i in market.get("indices") or [] if i.get("symbol") == "^GSPC"), None)
    return [f"S&P 500 {sp}% and the overview names no index"]


def profile_problems(r: dict) -> List[str]:
    """What is wrong in one company_profiles row, read with its market cap (empty = fine)."""
    out = []
    words =[w for w in re.split(r"[^A-Za-z]+", r["name"] or "") if len(w) >= 3
             and w.lower() not in ("inc", "corp", "corporation", "company", "the", "ltd", "limited", "plc", "holdings", "group")]
    summary = (r["summary"] or "").lower()
    if r["source"] == "wikipedia" and words and summary and not any(w.lower() in summary for w in words):
        out.append("the summary does not name the company")     # the wrong article
    staff, cap = r["employees"], r["market_cap"]
    if staff is not None and staff < PROFILE_MIN_STAFF and (cap or 0) >= BIG_COMPANY_CAP and r.get("ticker") not in PROFILE_STAFF_CHECKED:
        out.append(f"{staff} employees at a market cap of ${cap / 1e9:.0f}B")
    return out


def usage_mb(nbytes, connections) -> float:
    return (float(nbytes or 0) + float(connections or 0) * CONN_OVERHEAD_BYTES) / 1e6   # SUM(bigint) = Decimal


def usage_line(day: date, days: List[dict], top: List[dict]) -> dict:
    """days: [{day, bytes, connections}] newest first (the last week); top: yesterday's sources, largest first."""
    if not days:
        return _line("usage.db", "info", "no counts yet (db_meter.py: from the first run after v38)")
    by_day = {d["day"]: usage_mb(d["bytes"], d["connections"]) for d in days}
    mb = by_day.get(day)
    week = ", ".join(f"{d['day'].strftime('%d.%m')} {usage_mb(d['bytes'], d['connections']):.1f}" for d in days)
    if mb is None:
        return _line("usage.db", "info", f"nothing counted on {day} (MB a day, estimate: {week})")
    parts = ", ".join(f"{t['source']} {usage_mb(t['bytes'], t['connections']):.1f} ({t['connections']} conn.)" for t in top)
    return _line("usage.db", "warn" if mb > DB_DAY_WARN_MB else "ok",
                 f"{day}: ~{mb:.1f} MB read from the database (estimate; largest: {parts}). MB a day: {week}", round(mb, 1))


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
    cur.execute("""SELECT COUNT(*) FILTER (WHERE change_pct > 0) AS up, COUNT(*) FILTER (WHERE change_pct < 0) AS down
                   FROM universe_movers WHERE in_sp500 AND change_pct <> 'NaN'""")
    m = cur.fetchone()
    cur.execute("SELECT day, advancers, decliners FROM market_breadth ORDER BY day DESC LIMIT 1")
    b = cur.fetchone()
    if b:
        gap = movers_gap(m["up"], m["down"], b["advancers"], b["decliners"])
        out.append(_line("value.movers_session", "warn" if gap > MOVERS_MAX_GAP else "ok",
                         f"heatmap {m['up']} up, {m['down']} down; breadth of {b['day']} {b['advancers']} up, {b['decliners']} down", gap))
    cur.execute("SELECT day, spy_close, spy_sma50, spy_sma200, vix FROM market_regime ORDER BY day DESC LIMIT 1")
    r = cur.fetchone()
    if r:
        bad = [k for k in ("spy_close", "spy_sma50", "spy_sma200", "vix")
               if r[k] is None or not math.isfinite(r[k]) or r[k] <= 0]
        out.append(_line("value.regime", "fail" if bad else "ok", f"{r['day']}: " + (f"missing or impossible: {', '.join(bad)}" if bad
                         else f"SPY {r['spy_close']}, VIX {r['vix']}")))
    return out


def check_earnings(cur, now: datetime) -> List[dict]:
    """v33: the last trading day's reports should all have a result by the next morning."""
    day = now.astimezone(ET).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    # v38c: revenue_estimate in [0, 10 x market cap] = a real estimate (api_server._usable_revenue): 0 = none, above = another currency
    cur.execute("""SELECT ticker, eps_actual, revenue_actual, COALESCE(eps_from_release, FALSE) AS rel,
                          (eps_estimate IS NOT NULL OR (revenue_estimate > 0 AND (market_cap IS NULL OR revenue_estimate <= 10 * market_cap)))
                          AS covered
                   FROM earnings_calendar WHERE report_date = %s""", (day,))
    rows = cur.fetchall()
    if not rows:
        return [_line("earnings.results", "info", f"no reports on {day}")]
    empty = [r for r in rows if r["eps_actual"] is None and r["revenue_actual"] is None]
    # v38c: no estimate at all = no analyst follows it, and Yahoo has no results for it either (6.11.2026: NHC, FRHC, BH, and
    # Honda / KT in their own currency). Listed, not counted: only a covered company without results is a missing result.
    blank = [r["ticker"] for r in empty if r["covered"]]
    uncovered = [r["ticker"] for r in empty if not r["covered"]]
    no_eps = sum(1 for r in rows if r["eps_actual"] is None)
    rel = sum(1 for r in rows if r["rel"])
    status = "ok" if not blank else ("info" if len(blank) <= MAX_BLANK_REPORTS else "warn")
    return [_line("earnings.results", status, f"{day}: {len(rows)} reports, no EPS: {no_eps}, EPS still from the release: {rel}, "
                                              f"nothing at all: {', '.join(blank[:20]) or 'none'}"
                                              + (f"; no estimate and no results (not counted): {', '.join(uncovered[:20])}" if uncovered else ""),
                  len(blank))]


GUIDANCE_MISSING_SHARE = 0.3       # v40: more than this share of a day's reports without an answer = the job is not working
GUIDANCE_ODD_PCT = 40              # a guidance this far from the consensus = most likely another period or unit


def guidance_problems(rows: List[dict]) -> List[str]:
    """rows: {ticker, status, items} of one day's reported companies (status None = never read)."""
    out = []
    unread = [r["ticker"] for r in rows if r["status"] in (None, "failed")]
    if len(rows) >= 5 and len(unread) > GUIDANCE_MISSING_SHARE * len(rows):
        out.append(f"{len(unread)} of {len(rows)} reports without an answer: {', '.join(unread[:12])}")
    odd = [f"{r['ticker']} {x.get('metric')} {x['vs_pct']:+.0f}%" for r in rows for x in (r["items"] or [])
           if x.get("vs_pct") is not None and abs(x["vs_pct"]) > GUIDANCE_ODD_PCT]
    if odd:
        out.append("far from the consensus (check the period / unit): " + ", ".join(odd[:8]))
    return out


def check_guidance(cur, now: datetime) -> List[dict]:
    """v40: the last trading day's reports should all have been read for guidance by the next morning."""
    day = now.astimezone(ET).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    cur.execute("SELECT to_regclass('earnings_guidance') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return [_line("earnings.guidance", "info", "no table yet (earnings_guidance.py: from its first run)")]
    cur.execute("""SELECT c.ticker, g.status, g.items FROM earnings_calendar c
                   LEFT JOIN earnings_guidance g USING (ticker, report_date)
                   WHERE c.report_date = %s AND (c.eps_actual IS NOT NULL OR c.revenue_actual IS NOT NULL)""", (day,))
    rows = cur.fetchall()
    if not rows:
        return [_line("earnings.guidance", "info", f"no reports on {day}")]
    odd = guidance_problems(rows)
    found = sum(1 for r in rows if r["status"] == "found")
    none = sum(1 for r in rows if r["status"] == "none")
    return [_line("earnings.guidance", "warn" if odd else "ok",
                  f"{day}: {len(rows)} reports, {found} with guidance, {none} without" + (f"; {'; '.join(odd)}" if odd else ""), len(odd))]


def check_market_caps(cur) -> List[dict]:
    """v34: the market cap on the stock page (fundamentals_cache, from SEC share counts) next to the scanner's (Yahoo).
    Far apart = a wrong share count got through, and the P/E computed from it is wrong too."""
    cur.execute("""SELECT f.ticker, (f.data->>'market_cap')::float8 AS fund, e.market_cap AS ref
                   FROM fundamentals_cache f JOIN stock_estimates e ON e.ticker = f.ticker
                   WHERE f.data->>'market_cap' IS NOT NULL AND e.market_cap > 0
                     AND f.built_at > NOW() - INTERVAL '3 days' AND e.updated_at > NOW() - INTERVAL '3 days'
                     AND (f.data->>'market_cap')::float8 / e.market_cap NOT BETWEEN %s AND %s""",
                (1 / FUND_MCAP_MAX_RATIO, FUND_MCAP_MAX_RATIO))
    odd = cur.fetchall()
    detail = ", ".join(f"{r['ticker']} ({r['fund'] / r['ref']:.2f}x)" for r in odd[:20])
    return [_line("value.market_cap", "warn" if odd else "ok", detail or "the stock page's market caps agree with the scanner's", len(odd))]


def check_ratings(cur, now: datetime) -> List[dict]:
    """v39: the last trading day's analyst changes. Every trading day has some (AMD alone had 8 on 4.11.2026); none at all =
    Yahoo's answer changed shape or the job did not read it."""
    from ai_digest import nyse_holidays
    day = now.astimezone(ET).date() - timedelta(days=1)
    while day.weekday() >= 5 or day in nyse_holidays(day.year):
        day -= timedelta(days=1)
    cur.execute("SELECT to_regclass('analyst_ratings') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return [_line("value.analyst_ratings", "info", "no table yet (analyst_ratings.py: from its first run)")]
    cur.execute("""SELECT COUNT(*) FILTER (WHERE action IN ('up', 'down', 'init')) AS ratings,
                          COUNT(*) FILTER (WHERE action <> 'run') AS all_changes
                   FROM analyst_ratings WHERE day = %s""", (day,))
    r = cur.fetchone()
    return [_line("value.analyst_ratings", "ok" if r["all_changes"] else "warn",
                  f"{day}: {r['ratings']} rating changes, {r['all_changes'] - r['ratings']} target changes", r["all_changes"])]


def check_digest(cur) -> List[dict]:
    """The last closed summary. ~10 KB, once a day."""
    cur.execute("SELECT day, payload FROM ai_daily_digests WHERE is_final ORDER BY day DESC LIMIT 1")
    r = cur.fetchone()
    if not r:
        return [_line("digest.main_story", "warn", "no closed daily summary")]
    p = json.loads(r["payload"])
    odd = digest_problems(p)
    if not p.get("market"):                # v37c: the market prices did not come back, or the old code is still running
        odd.append("the summary was written without market numbers")
    move = (p.get("market") or {}).get("move") or "no numbers saved"
    return [_line("digest.main_story", "warn" if odd else "ok", f"{r['day']}: {'; '.join(odd) or 'market move: ' + move}", len(odd))]


def check_profiles(cur) -> List[dict]:
    """v35: the text a stock page opens with, and what search engines read. Only rows the job has already redone (facts->>'fv' = 2):
    until its first run after v35, every older row would be reported."""
    cur.execute("""SELECT p.ticker, p.name, p.source, p.filing_date, p.profile->'en'->>'summary' AS summary,
                          (p.facts->>'employees')::float8 AS employees, l.market_cap
                   FROM company_profiles p LEFT JOIN stock_leaders l ON l.ticker = p.ticker
                   WHERE p.source = 'wikipedia' OR COALESCE((p.facts->>'fv')::int, 1) >= 2""")
    rows = cur.fetchall()
    odd = [f"{r['ticker']} ({'; '.join(p)})" for r in rows for p in [profile_problems(r)] if p]
    # not an error: GE's 10-K, for one, has only a cross-reference table where the business section would be
    wiki = sorted(r["ticker"] for r in rows if r["source"] == "wikipedia" and r["filing_date"])
    return [_line("value.profiles", "warn" if odd else "ok", ", ".join(odd[:15]) or f"{len(rows)} profiles checked", len(odd)),
            _line("info.profile_source", "info" if wiki else "ok", f"{len(wiki)} files annual reports with no business section found, "
                                                                   f"encyclopedia text used: {', '.join(wiki[:30]) or 'none'}", len(wiki))]


def check_db_size(cur) -> List[dict]:
    """v34: nothing watched the database's size, and news and scan rows are never deleted."""
    cur.execute("SELECT pg_database_size(current_database()) / 1048576 AS mb")
    mb = int(cur.fetchone()["mb"])
    cur.execute("""SELECT relname, pg_total_relation_size(relid) / 1048576 AS mb FROM pg_stat_user_tables
                   ORDER BY pg_total_relation_size(relid) DESC LIMIT 5""")
    top = ", ".join(f"{r['relname']} {int(r['mb'])}" for r in cur.fetchall())
    status = "fail" if mb >= DB_SIZE_FAIL_MB else "warn" if mb >= DB_SIZE_WARN_MB else "ok"
    return [_line("size.database", status, f"{mb} MB used (largest, MB: {top})", mb)]


def check_db_usage(cur, now: datetime) -> List[dict]:
    """v38: yesterday (UTC, as db_meter.py counts) and the last week. ~20 rows a day, a few KB."""
    cur.execute("SELECT to_regclass('db_usage') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return [usage_line(now.date(), [], [])]
    day = now.date() - timedelta(days=1)
    cur.execute("""SELECT day, SUM(bytes) AS bytes, SUM(connections) AS connections FROM db_usage
                   WHERE day >= %s GROUP BY day ORDER BY day DESC""", (day - timedelta(days=6),))
    days = cur.fetchall()
    cur.execute("""SELECT source, bytes, connections FROM db_usage WHERE day = %s
                   ORDER BY bytes + connections * %s DESC LIMIT 6""", (day, CONN_OVERHEAD_BYTES))
    return [usage_line(day, days, cur.fetchall())]


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
                          ("arithmetic", lambda: check_arithmetic(cur)), ("values", lambda: check_values(cur)),
                          ("earnings", lambda: check_earnings(cur, now)), ("guidance", lambda: check_guidance(cur, now)),
                          ("market caps", lambda: check_market_caps(cur)),
                          ("profiles", lambda: check_profiles(cur)), ("digest", lambda: check_digest(cur)),
                          ("ratings", lambda: check_ratings(cur, now)),
                          ("size", lambda: check_db_size(cur)), ("usage", lambda: check_db_usage(cur, now))):
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
    import db_meter                       # v38: its own reads are counted too
    db_meter.install()
    sys.exit(main())
