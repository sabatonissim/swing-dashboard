"""
Analyst rating changes (v39) — what the brokerages changed today, for the last section of the daily summary
==========================================================================================================
Runs in GitHub Actions (analyst-ratings.yml), started by scheduler.py on weekdays at 09:00 ET (v57; most changes come out before
the open) and 17:40 ET (the rest of the day). Reads Yahoo's rating history of each large company (yfinance, which works from
Actions; Yahoo blocks Render) and keeps the changes of the last few days in `analyst_ratings`. The site shows them as they
are, not through the AI: the firm, the old and new rating, the price target.

What is kept: upgrades, downgrades and new coverage (action up / down / init), and price-target changes (main / reit with
Raises / Lowers). A rating repeated with the same target says nothing and is skipped.

Which companies: stock_estimates (the scanner's table, fresh) with a market cap of at least MIN_CAP, largest first, at
most MAX_TICKERS. ~500 requests to Yahoo a run, one connection to the database at the start and one at the end
(~20 KB read, ~10 KB written).

Manual: python analyst_ratings.py            (normal run)
        python analyst_ratings.py AMD NVDA  (only these, printed and saved)
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2

ET = ZoneInfo("America/New_York")
MIN_CAP = 10e9
MAX_TICKERS = 600
LOOKBACK_DAYS = 5        # a change can show up in Yahoo's history hours late; the key keeps it once. v56: 3 -> 5, so
                         # Monday's runs still reach a Friday whose runs read nothing (13.11.2026: 0 changes saved)
MAX_EMPTY_SHARE = 0.5    # v56: more companies than this with no history at all = Yahoo did not answer -> the run fails
KEEP_DAYS = 90           # as long as the daily summaries and the news
PAUSE_SEC = 0.35
BUDGET_SEC = 20 * 60     # the workflow stops at 30 minutes
RATING_ACTIONS = ("up", "down", "init")
TARGET_ACTIONS = ("Raises", "Lowers")


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def ensure_table(cur):
    """Also created by api_server.init_db, so the daily summary can read it before the first run."""
    cur.execute("""CREATE TABLE IF NOT EXISTS analyst_ratings (
        ticker TEXT NOT NULL,
        firm TEXT NOT NULL,
        graded_at TIMESTAMPTZ NOT NULL,
        day DATE NOT NULL,
        action TEXT,
        from_grade TEXT,
        to_grade TEXT,
        pt_action TEXT,
        pt_now REAL,
        pt_prior REAL,
        market_cap REAL,
        seen_at TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (ticker, firm, graded_at))""")
    cur.execute("CREATE INDEX IF NOT EXISTS analyst_ratings_day ON analyst_ratings (day)")


def pick_tickers(cur):
    """[(ticker, market cap)], largest first. Only rows the scanner refreshed lately (a stale cap = a company that left)."""
    cur.execute("""SELECT ticker, market_cap FROM stock_estimates
                   WHERE market_cap >= %s AND updated_at > NOW() - INTERVAL '10 days'
                   ORDER BY market_cap DESC LIMIT %s""", (MIN_CAP, MAX_TICKERS))
    return [(t, float(c)) for t, c in cur.fetchall()]


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None        # NaN and Yahoo's 0 = no target


def _text(v):
    s = "" if v is None else str(v).strip()
    return "" if s.lower() == "nan" else s


def rows_from_history(ticker, df, market_cap, since):
    """Yahoo's history (yfinance's upgrades_downgrades) -> the rows to keep. Column names differ between yfinance releases
    (Firm / firm, ToGrade / toGrade ...), so they are read case-insensitively."""
    out = []
    if df is None or getattr(df, "empty", True):
        return out
    df = df.reset_index()
    cols = {c.lower(): c for c in df.columns}
    get = lambda r, name: r[cols[name]] if name in cols else None
    for _, r in df.iterrows():
        when = get(r, "gradedate") if "gradedate" in cols else get(r, "epochgradedate")
        try:
            ts = when.to_pydatetime() if hasattr(when, "to_pydatetime") else datetime.fromtimestamp(int(when), tz=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < since:
            continue
        action, pt_action = _text(get(r, "action")).lower(), _text(get(r, "pricetargetaction"))
        if action not in RATING_ACTIONS and pt_action not in TARGET_ACTIONS:
            continue
        firm = _text(get(r, "firm"))
        if not firm:
            continue
        out.append(dict(ticker=ticker, firm=firm, graded_at=ts, day=ts.astimezone(ET).date(), action=action,
                        from_grade=_text(get(r, "fromgrade")), to_grade=_text(get(r, "tograde")), pt_action=pt_action,
                        pt_now=_num(get(r, "currentpricetarget")), pt_prior=_num(get(r, "priorpricetarget")), market_cap=market_cap))
    return out


def latest_grade(df):
    """The newest grade time in one company's history (UTC), None when there is none."""
    if df is None or getattr(df, "empty", True):
        return None
    try:
        ts = df.index.max()
        ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except Exception:  # noqa: BLE001
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


PROBE_TICKERS = ("NVDA", "AAPL", "MSFT")   # rated by many firms: something new every trading day


def _variants():
    """v57c: ways to ask Yahoo for the same history. 16.11.2026: yfinance's own request, from GitHub Actions, came back with
    a history that stopped at 12.11 19:53 UTC (the run said so: 'an old answer'), while from home every one of these was
    fresh. Each run asks the probe tickers in every way and uses the freshest one (cache_bust = a parameter that changes
    every run, so no stored copy of the address can be served)."""
    bust = str(int(time.time()))
    q1, q2 = "https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com"
    base = {"modules": "upgradeDowngradeHistory", "corsDomain": "finance.yahoo.com", "formatted": "false",
            "lang": "en-US", "region": "US"}
    return [("yfinance", None, None),
            ("q2 cache_bust", q2 + "/v10/finance/quoteSummary/{t}", dict(base, _cb=bust)),
            ("q1 cache_bust", q1 + "/v10/finance/quoteSummary/{t}", dict(base, _cb=bust)),
            ("q1 plain", q1 + "/v10/finance/quoteSummary/{t}", {"modules": "upgradeDowngradeHistory"}),
            ("q2 GB", q2 + "/v10/finance/quoteSummary/{t}", dict(base, lang="en-GB", region="GB", _cb=bust)),
            (VERCEL, None, None)]


# v73: every way of asking from GitHub gave the old answer (16-23.11.2026), from home all were fresh. api/ratings.js on Vercel asks
# from another network; it is used only when its newest change is newer than what GitHub gets, like any other way above.
VERCEL = "vercel"
SITE_URL = (os.environ.get("SITE_URL") or "https://swing-desk-tau.vercel.app").rstrip("/")
VERCEL_BATCH = 20                    # = MAX_TICKERS in api/ratings.js
_vercel_rows = {}                    # ticker -> Yahoo's rows, filled a batch at a time


def _vercel_load(tickers):
    """A batch of companies from api/ratings.js into _vercel_rows. A company it could not read is left out."""
    import requests
    r = requests.get(f"{SITE_URL}/api/ratings?t={','.join(tickers)}", timeout=60, headers={"User-Agent": "swing-desk-jobs"})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:80]}")
    body = r.json()
    _vercel_rows.update(body.get("results") or {})
    if body.get("failed"):
        print(f"[warn] vercel: {len(body['failed'])} not read: {', '.join(f'{t} {e}' for t, e in list(body['failed'].items())[:5])}")


def _vercel_history(ticker, upcoming=()):
    if ticker not in _vercel_rows:
        _vercel_load([ticker] + [t for t in upcoming if t != ticker and t not in _vercel_rows][:VERCEL_BATCH - 1])
    if ticker not in _vercel_rows:
        raise RuntimeError("not in the answer")
    return history_from_json({"quoteSummary": {"result": [{"upgradeDowngradeHistory": {"history": _vercel_rows[ticker]}}]}})


def history_from_json(result):
    """quoteSummary JSON -> the same table yfinance makes of it (GradeDate index, Firm, ToGrade ...)."""
    import pandas as pd
    try:
        data = result["quoteSummary"]["result"][0]["upgradeDowngradeHistory"]["history"]
    except (KeyError, IndexError, TypeError):
        return pd.DataFrame()
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).rename(columns={"epochGradeDate": "GradeDate", "firm": "Firm", "toGrade": "ToGrade",
                                            "fromGrade": "FromGrade", "action": "Action"})
    df = df.set_index("GradeDate")
    df.index = pd.to_datetime(df.index, unit="s")
    return df


def _history(ticker, variant, upcoming=()):
    import yfinance as yf
    name, url, params = variant
    if name == VERCEL:
        return _vercel_history(ticker, upcoming)
    if url is None:
        return yf.Ticker(ticker).upgrades_downgrades
    from yfinance.data import YfData
    return history_from_json(YfData().get_raw_json(url.format(t=ticker), params=dict(params, symbol=ticker)))


def pick_variant(probe=PROBE_TICKERS):
    """The way of asking whose newest change (over the probe tickers) is the newest; printed for every way."""
    best, best_ts = None, None
    for v in _variants():
        newest = None
        for t in probe:
            try:
                ts = latest_grade(_history(t, v, probe))
            except Exception as e:  # noqa: BLE001
                print(f"[probe] {v[0]:14} {t}: {type(e).__name__}: {str(e)[:120]}")
                continue
            if ts and (newest is None or ts > newest):
                newest = ts
            time.sleep(PAUSE_SEC)
        print(f"[probe] {v[0]:14} newest change {newest}")
        if newest and (best_ts is None or newest > best_ts):
            best, best_ts = v, newest
    best = best or _variants()[0]
    print(f"[info] reading every company with: {best[0]}")
    return best


def fetch(tickers, now=None, variant=None):
    """-> (rows, stats). stats: asked, empty, failed, rows, newest (the newest grade of ALL the histories read)."""
    variant = variant or _variants()[0]
    since = (now or datetime.now(timezone.utc)) - timedelta(days=LOOKBACK_DAYS)
    rows, failed, empty, asked, newest, t0 = [], [], 0, 0, None, time.time()
    for n, (ticker, cap) in enumerate(tickers):
        if time.time() - t0 > BUDGET_SEC:
            print(f"[warn] time budget used up after {n} of {len(tickers)} companies; the rest wait for the next run")
            break
        asked += 1
        try:
            df = _history(ticker, variant, [t for t, _ in tickers[n:n + VERCEL_BATCH]])
            if df is None or getattr(df, "empty", True):
                empty += 1                   # every large company has a history; none = Yahoo refused (no exception)
            last = latest_grade(df)
            if last and (newest is None or last > newest):
                newest = last
            rows += rows_from_history(ticker, df, cap, since)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{ticker} ({type(e).__name__})")
        time.sleep(0.05 if variant[0] == VERCEL else PAUSE_SEC)
    if failed:
        print(f"[warn] {len(failed)} companies failed: {', '.join(failed[:30])}")
    print(f"[info] {empty} of {asked} companies came back with no rating history; newest change read: {newest}")
    return rows, dict(asked=asked, empty=empty, failed=len(failed), rows=len(rows), newest=newest)


def run_failed(bad_share: float, only=None) -> bool:
    """v56: True = Yahoo did not answer for most companies; the workflow then ends red (visible in the Actions run list)."""
    return not only and bad_share > MAX_EMPTY_SHARE


def previous_trading_day(now):
    day = now.astimezone(ET).date() - timedelta(days=1)
    try:
        from ai_digest import nyse_holidays
        holidays = set(nyse_holidays(day.year)) | set(nyse_holidays(day.year - 1))
    except Exception:  # noqa: BLE001
        holidays = set()
    while day.weekday() >= 5 or day in holidays:
        day -= timedelta(days=1)
    return day


def answer_is_old(newest, now, only=None) -> bool:
    """v57: since 13.11.2026 the runs read histories (most not empty, the run green) but saved nothing, while Yahoo showed
    new changes from home: the answer Actions got stopped at 12.11. Among ~500 of the largest companies some rating or
    target changes every trading day, so the newest change of all of them must be from the previous trading day or later."""
    if only:
        return False
    return newest is None or newest.astimezone(ET).date() < previous_trading_day(now)


def save(cur, rows, stats=None):
    for r in rows:
        cur.execute("""INSERT INTO analyst_ratings (ticker, firm, graded_at, day, action, from_grade, to_grade, pt_action,
                                                    pt_now, pt_prior, market_cap)
                       VALUES (%(ticker)s, %(firm)s, %(graded_at)s, %(day)s, %(action)s, %(from_grade)s, %(to_grade)s,
                               %(pt_action)s, %(pt_now)s, %(pt_prior)s, %(market_cap)s)
                       ON CONFLICT (ticker, firm, graded_at) DO UPDATE SET seen_at = NOW(), to_grade = EXCLUDED.to_grade,
                           pt_now = EXCLUDED.pt_now, pt_prior = EXCLUDED.pt_prior, pt_action = EXCLUDED.pt_action""", r)
    cur.execute("DELETE FROM analyst_ratings WHERE day < CURRENT_DATE - %s", (KEEP_DAYS,))
    # the run itself, so the health check sees a run on a day with no change at all. v57: with what it read
    # (from_grade = the newest change of all histories, to_grade = the counts), so data_health.py can say why a day is empty
    s = stats or {}
    newest = s.get("newest")
    counts = (f"asked {s.get('asked', 0)}, empty {s.get('empty', 0)}, failed {s.get('failed', 0)}, kept {s.get('rows', 0)}"
              f", via {s.get('way', '?')}")
    cur.execute("""INSERT INTO analyst_ratings (ticker, firm, graded_at, day, action, from_grade, to_grade)
                   VALUES ('_run', '_run', NOW(), CURRENT_DATE, 'run', %s, %s) ON CONFLICT DO NOTHING""",
                (newest.isoformat(timespec="minutes") if newest else "none", counts))


def main(only=None):
    conn = get_conn()
    try:
        cur = conn.cursor()
        ensure_table(cur)
        conn.commit()
        tickers = [(t, None) for t in only] if only else pick_tickers(cur)
    finally:
        conn.close()                    # Yahoo takes minutes; Neon would drop an idle connection
    print(f"[info] reading the rating history of {len(tickers)} companies")
    variant = pick_variant()
    rows, stats = fetch(tickers, variant=variant)
    stats["way"] = variant[0]
    bad_share = (stats["empty"] + stats["failed"]) / stats["asked"] if stats["asked"] else 0.0
    ratings = [r for r in rows if r["action"] in RATING_ACTIONS]
    print(f"[info] {len(rows)} changes in the last {LOOKBACK_DAYS} days: {len(ratings)} ratings, {len(rows) - len(ratings)} targets only")
    for r in sorted(ratings, key=lambda r: -(r["market_cap"] or 0))[:15]:
        print(f"  {r['day']} {r['ticker']:6} {r['firm']}: {r['action']} {r['from_grade'] or '-'} -> {r['to_grade']} target {r['pt_now']}")
    conn = get_conn()
    try:
        cur = conn.cursor()
        save(cur, rows, stats)
        conn.commit()
    finally:
        conn.close()
    print("[info] saved")
    if run_failed(bad_share, only):
        print(f"[error] {bad_share:.0%} of the companies came back empty or failed: Yahoo did not answer this run")
        sys.exit(1)
    if answer_is_old(stats["newest"], datetime.now(timezone.utc), only):
        print(f"[error] the newest change Yahoo gave is {stats['newest']}, before the last trading day: an old answer")
        sys.exit(1)


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    main([a.upper() for a in sys.argv[1:]] or None)
