"""
Analyst rating changes (v39) — what the brokerages changed today, for the last section of the daily summary
==========================================================================================================
Runs in GitHub Actions (analyst-ratings.yml), started by scheduler.py on weekdays at 09:50 ET (most changes come out before
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
LOOKBACK_DAYS = 3        # a change can show up in Yahoo's history hours late; the key keeps it once
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


def fetch(tickers, now=None):
    import yfinance as yf
    since = (now or datetime.now(timezone.utc)) - timedelta(days=LOOKBACK_DAYS)
    rows, failed, t0 = [], [], time.time()
    for n, (ticker, cap) in enumerate(tickers):
        if time.time() - t0 > BUDGET_SEC:
            print(f"[warn] time budget used up after {n} of {len(tickers)} companies; the rest wait for the next run")
            break
        try:
            rows += rows_from_history(ticker, yf.Ticker(ticker).upgrades_downgrades, cap, since)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{ticker} ({type(e).__name__})")
        time.sleep(PAUSE_SEC)
    if failed:
        print(f"[warn] {len(failed)} companies failed: {', '.join(failed[:30])}")
    return rows


def save(cur, rows):
    for r in rows:
        cur.execute("""INSERT INTO analyst_ratings (ticker, firm, graded_at, day, action, from_grade, to_grade, pt_action,
                                                    pt_now, pt_prior, market_cap)
                       VALUES (%(ticker)s, %(firm)s, %(graded_at)s, %(day)s, %(action)s, %(from_grade)s, %(to_grade)s,
                               %(pt_action)s, %(pt_now)s, %(pt_prior)s, %(market_cap)s)
                       ON CONFLICT (ticker, firm, graded_at) DO UPDATE SET seen_at = NOW(), to_grade = EXCLUDED.to_grade,
                           pt_now = EXCLUDED.pt_now, pt_prior = EXCLUDED.pt_prior, pt_action = EXCLUDED.pt_action""", r)
    cur.execute("DELETE FROM analyst_ratings WHERE day < CURRENT_DATE - %s", (KEEP_DAYS,))
    # the run itself, so the health check sees a run on a day with no change at all
    cur.execute("""INSERT INTO analyst_ratings (ticker, firm, graded_at, day, action) VALUES ('_run', '_run', NOW(), CURRENT_DATE, 'run')
                   ON CONFLICT DO NOTHING""")


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
    rows = fetch(tickers)
    ratings = [r for r in rows if r["action"] in RATING_ACTIONS]
    print(f"[info] {len(rows)} changes in the last {LOOKBACK_DAYS} days: {len(ratings)} ratings, {len(rows) - len(ratings)} targets only")
    for r in sorted(ratings, key=lambda r: -(r["market_cap"] or 0))[:15]:
        print(f"  {r['day']} {r['ticker']:6} {r['firm']}: {r['action']} {r['from_grade'] or '-'} -> {r['to_grade']} target {r['pt_now']}")
    conn = get_conn()
    try:
        cur = conn.cursor()
        save(cur, rows)
        conn.commit()
    finally:
        conn.close()
    print("[info] saved")


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    main([a.upper() for a in sys.argv[1:]] or None)
