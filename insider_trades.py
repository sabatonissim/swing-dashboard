"""
Insider trades (v22) — open-market buys and sells by officers, directors and 10% owners (SEC Form 4)
====================================================================================================
Runs in GitHub Actions (insider-refresh.yml), started by scheduler.py every 30 minutes on weekdays.
Writes Postgres tables insider_trades / insider_filings_seen / insider_sweep_state; the API only reads them.

Companies: S&P 500 + Nasdaq 100 + scanner hits of the last 60 days + watchlists (mapped to SEC CIKs).
Each run, within RUN_BUDGET_SEC:
  1. recent — SEC's live feed of new Form 4 filings (newest first), pages until FEED_LOOKBACK_H back;
  2. sweep  — the companies swept longest ago (never-swept first): their SEC filing list (submissions JSON), every
              Form 4 of the last BACKFILL_DAYS not seen yet. This fills six months of history over the first runs
              and catches anything the feed missed.
Kept: transaction code P (open-market purchase) and S (open-market sale) from the non-derivative table only.
Grants (A), option exercises (M), tax withholding (F), gifts (G) etc. are not market trades and are skipped.
"planned" = the sale is under a pre-arranged Rule 10b5-1 plan (the form's 10b5-1 box, or a footnote saying so).
SEC fair access: at most ~8 requests per second, with a descriptive User-Agent.
"""

import io
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import pandas as pd
import psycopg2
import psycopg2.extras
import requests

SEC_HEADERS = {"User-Agent": "SwingDesk-Dashboard admin@swingdesk.app", "Accept-Encoding": "gzip, deflate"}
MIN_INTERVAL_SEC = 0.125
RUN_BUDGET_SEC = 7 * 60
FEED_LOOKBACK_H = 3
FEED_MAX_PAGES = 10
BACKFILL_DAYS = 183

_last_request = [0.0]


def sec_get(url: str, timeout: int = 20) -> Optional[requests.Response]:
    """GET with the SEC rate limit; None on 404 or after 3 failed attempts."""
    for attempt in range(3):
        wait = MIN_INTERVAL_SEC - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        try:
            r = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            print(f"[warn] SEC {r.status_code} for {url} (attempt {attempt + 1})")
            if r.status_code in (403, 429):
                time.sleep(10 * (attempt + 1))      # rate limited — back off hard
        except Exception as e:
            print(f"[warn] SEC request failed ({type(e).__name__}: {e}) for {url}")
        time.sleep(1.5 * (attempt + 1))
    return None


# ------------------------------------------------------------------ database
def _db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


_shared = {"conn": None}


@contextmanager
def cursor(dict_cursor: bool = False):
    """One connection for the whole run (a run writes after nearly every SEC request, so Neon does not suspend
    in between; a new connection per filing would double the run time). Reopened if it dropped."""
    conn = _shared["conn"]
    if conn is None or conn.closed:
        conn = _shared["conn"] = psycopg2.connect(_db_url())
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) if dict_cursor else conn.cursor()
    try:
        yield conn, cur
    except Exception:
        try:
            conn.rollback()
        except Exception:
            _shared["conn"] = None
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass


def ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            accession TEXT NOT NULL,
            seq INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            issuer_cik TEXT,
            filed_date DATE,
            trade_date DATE,
            owner_name TEXT,
            owner_title TEXT,
            is_director BOOLEAN,
            is_officer BOOLEAN,
            is_ten_pct BOOLEAN,
            code TEXT,
            shares DOUBLE PRECISION,
            price DOUBLE PRECISION,
            value DOUBLE PRECISION,
            owned_after DOUBLE PRECISION,
            direct TEXT,
            planned BOOLEAN,
            url TEXT,
            PRIMARY KEY (accession, seq)
        );
        CREATE INDEX IF NOT EXISTS insider_trades_ticker_idx ON insider_trades (ticker, trade_date DESC);
        CREATE INDEX IF NOT EXISTS insider_trades_filed_idx ON insider_trades (filed_date DESC);
        CREATE TABLE IF NOT EXISTS insider_filings_seen (
            accession TEXT PRIMARY KEY,
            issuer_cik TEXT,
            seen_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS insider_sweep_state (
            cik TEXT PRIMARY KEY,
            swept_at TIMESTAMPTZ
        );
    """)


# ------------------------------------------------------------------ which companies
def _csv_symbols(url: str) -> List[str]:
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        col = next((c for c in ("Symbol", "Ticker", "symbol", "ticker") if c in df.columns), None)
        return [str(s).strip().upper().replace(".", "-") for s in df[col].dropna()] if col else []
    except Exception as e:
        print(f"[warn] ticker list {url} failed: {e}")
        return []


def universe() -> Dict[str, str]:
    """issuer CIK (10 digits) -> our ticker."""
    tickers: List[str] = []
    for q in ("SELECT DISTINCT ticker FROM watchlist",
              "SELECT DISTINCT ticker FROM scanned_stocks WHERE timestamp > NOW() - INTERVAL '60 days'"):
        try:
            with cursor() as (conn, cur):
                cur.execute(q)
                tickers += [str(r[0]).upper().replace(".", "-") for r in cur.fetchall() if r[0]]
        except Exception as e:
            print(f"[warn] universe query failed: {e}")
    tickers += _csv_symbols("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv")
    tickers += _csv_symbols("https://yfiua.github.io/index-constituents/constituents-nasdaq100.csv")

    r = sec_get("https://www.sec.gov/files/company_tickers.json")
    if r is None:
        raise RuntimeError("SEC ticker->CIK map unavailable")
    by_ticker = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in r.json().values()}
    out: Dict[str, str] = {}
    for t in tickers:
        cik = by_ticker.get(t) or by_ticker.get(t.replace("-", ""))
        if cik and cik not in out:        # GOOGL/GOOG share one CIK: the first ticker in our list wins
            out[cik] = t
    return out


# ------------------------------------------------------------------ one Form 4
def _val(el, path: str) -> Optional[str]:
    """Text of <path>/<value> (Form 4 wraps most fields in <value>) or of <path> itself."""
    node = el.find(path)
    if node is None:
        return None
    v = node.find("value")
    txt = (v.text if v is not None else node.text) or ""
    txt = txt.strip()
    return txt or None


def _flag(el, path: str) -> bool:
    return (_val(el, path) or "").lower() in ("1", "true")


def _num(s: Optional[str]) -> Optional[float]:
    try:
        return float(s.replace(",", "")) if s is not None else None
    except Exception:
        return None


def _nice_name(name: str) -> str:
    """SEC writes many names in capitals ("SMITH JOHN A"); title-case those, keep mixed-case names as filed."""
    name = re.sub(r"\s+", " ", name or "").strip()
    return name.title() if name.isupper() else name


def parse_form4(xml_bytes: bytes) -> Optional[dict]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception as e:
        print(f"[warn] Form 4 XML did not parse: {e}")
        return None
    if root.tag != "ownershipDocument":
        return None
    owners = root.findall("reportingOwner")
    names, titles = [], []
    is_dir = is_off = is_ten = False
    for o in owners:
        n = _val(o, "reportingOwnerId/rptOwnerName")
        if n:
            names.append(_nice_name(n))
        rel = o.find("reportingOwnerRelationship")
        if rel is not None:
            is_dir = is_dir or _flag(rel, "isDirector")
            is_off = is_off or _flag(rel, "isOfficer")
            is_ten = is_ten or _flag(rel, "isTenPercentOwner")
            t = _val(rel, "officerTitle") or _val(rel, "otherText")
            if t and t not in titles:
                titles.append(t)
    plan_box = _flag(root, "aff10b5One")
    notes = {fn.get("id"): (fn.text or "") for fn in root.iter("footnote")}

    trades = []
    for i, tx in enumerate(root.findall("nonDerivativeTable/nonDerivativeTransaction")):
        code = _val(tx, "transactionCoding/transactionCode")
        if code not in ("P", "S"):
            continue
        shares = _num(_val(tx, "transactionAmounts/transactionShares"))
        price = _num(_val(tx, "transactionAmounts/transactionPricePerShare"))
        if not shares or shares <= 0:
            continue
        refs = {f.get("id") for f in tx.iter("footnoteId")}
        planned = plan_box or any("10b5-1" in notes.get(ref, "") for ref in refs)
        td = (_val(tx, "transactionDate") or "")[:10]
        trades.append({
            "seq": i,
            "trade_date": td if re.fullmatch(r"\d{4}-\d{2}-\d{2}", td) else None,
            "code": code,
            "shares": shares,
            "price": price,
            "value": round(shares * price, 2) if price else None,
            "owned_after": _num(_val(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction")),
            "direct": _val(tx, "ownershipNature/directOrIndirectOwnership"),
            "planned": bool(planned) if code == "S" else False,
        })
    return {
        "owner_name": "; ".join(names)[:300] or None,
        "owner_title": "; ".join(titles)[:200] or None,
        "is_director": is_dir, "is_officer": is_off, "is_ten_pct": is_ten,
        "trades": trades,
    }


def _filing_folder(issuer_cik: str, accession: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{int(issuer_cik)}/{accession.replace('-', '')}"


def process_filing(issuer_cik: str, ticker: str, accession: str, filed: str, xml_name: Optional[str]) -> int:
    """Fetch + parse + store one filing. Returns trades stored; -1 = could not be read (not marked seen)."""
    folder = _filing_folder(issuer_cik, accession)
    if not xml_name:
        r = sec_get(folder + "/index.json")
        if r is None:
            return -1
        items = [it.get("name", "") for it in (r.json().get("directory", {}).get("item") or [])]
        xmls = [n for n in items if n.lower().endswith(".xml") and "index" not in n.lower()]
        if not xmls:
            return -1
        xml_name = xmls[0]
    r = sec_get(f"{folder}/{xml_name.split('/')[-1]}")
    if r is None:
        return -1
    doc = parse_form4(r.content)
    url = f"{folder}/{accession}-index.htm"
    stored = 0
    new_rows = []
    with cursor() as (conn, cur):
        if doc:
            for t in doc["trades"]:
                cur.execute("""INSERT INTO insider_trades (accession, seq, ticker, issuer_cik, filed_date, trade_date,
                                   owner_name, owner_title, is_director, is_officer, is_ten_pct, code, shares, price,
                                   value, owned_after, direct, planned, url)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (accession, seq) DO NOTHING""",
                            (accession, t["seq"], ticker, issuer_cik, filed, t["trade_date"], doc["owner_name"],
                             doc["owner_title"], doc["is_director"], doc["is_officer"], doc["is_ten_pct"], t["code"],
                             t["shares"], t["price"], t["value"], t["owned_after"], t["direct"], t["planned"], url))
                if cur.rowcount == 1:
                    new_rows.append(t)
                stored += 1
        cur.execute("INSERT INTO insider_filings_seen (accession, issuer_cik) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (accession, issuer_cik))
        conn.commit()
    if new_rows and filed and filed >= (date.today() - timedelta(days=PUSH_MAX_AGE_DAYS)).isoformat():
        _queue_push(ticker, accession, doc, new_rows)
    return stored


# v22b: new filings of the last few days -> push notification to people who watch the stock (push_alerts.py).
# Six months of backfill must not ring anyone's phone, hence the age limit.
PUSH_MAX_AGE_DAYS = 3
_push_queue: List[dict] = []


def _queue_push(ticker: str, accession: str, doc: dict, rows: List[dict]) -> None:
    for code in ("P", "S"):
        part = [r for r in rows if r["code"] == code]
        if not part:
            continue
        shares = sum(r["shares"] for r in part)
        priced = [r for r in part if r["price"]]
        value = sum(r["shares"] * r["price"] for r in priced)
        priced_shares = sum(r["shares"] for r in priced)
        _push_queue.append({
            "ticker": ticker, "accession": accession, "code": code,
            "owner_name": doc["owner_name"], "owner_title": doc["owner_title"],
            "is_director": doc["is_director"], "is_ten_pct": doc["is_ten_pct"],
            "shares": shares, "value": value or None,
            "price": (value / priced_shares) if priced_shares else None,
            "planned": any(r["planned"] for r in part),
        })


def send_pushes() -> None:
    if not _push_queue:
        return
    try:
        import push_alerts
        with cursor() as (conn, cur):
            push_alerts.notify_watchlist_insider(conn, _push_queue)
    except Exception as e:
        print(f"[warn] insider push notifications failed: {type(e).__name__}: {e}")


def _seen(accessions: List[str]) -> set:
    if not accessions:
        return set()
    with cursor() as (conn, cur):
        cur.execute("SELECT accession FROM insider_filings_seen WHERE accession = ANY(%s)", (accessions,))
        return {r[0] for r in cur.fetchall()}


# ------------------------------------------------------------------ 1. the live feed
_FEED_ISSUER_RE = re.compile(r"\((\d{10})\)\s*\(Issuer\)")
_ACC_RE = re.compile(r"/(\d{10}-\d{2}-\d{6})-index\.htm")


def run_recent(univ: Dict[str, str], deadline: float, stats: dict) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=FEED_LOOKBACK_H)
    ns = {"a": "http://www.w3.org/2005/Atom"}
    todo = []                                    # (cik, accession, filed date)
    for page in range(FEED_MAX_PAGES):
        r = sec_get("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=only"
                    f"&count=100&start={page * 100}&output=atom")
        if r is None:
            break
        try:
            feed = ET.fromstring(r.content)
        except Exception as e:
            print(f"[warn] feed page {page} did not parse: {e}")
            break
        entries = feed.findall("a:entry", ns)
        if not entries:
            break
        oldest = None
        for e in entries:
            title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
            updated = e.findtext("a:updated", default="", namespaces=ns)
            try:
                ts = datetime.fromisoformat(updated)
                oldest = ts if oldest is None or ts < oldest else oldest
            except Exception:
                ts = None
            if not title.startswith("4 -"):            # "4/A" amendments are skipped
                continue
            m = _FEED_ISSUER_RE.search(title)
            link = e.find("a:link", ns)
            href = link.get("href") if link is not None else ""
            ma = _ACC_RE.search(href or "")
            if m and ma and m.group(1) in univ:
                todo.append((m.group(1), ma.group(1), (updated or "")[:10] or date.today().isoformat()))
        if oldest is not None and oldest < cutoff:
            break
    seen = _seen([a for _, a, _ in todo])
    for cik, acc, filed in dict.fromkeys(todo):
        if acc in seen or time.time() > deadline:
            continue
        n = process_filing(cik, univ[cik], acc, filed, None)
        stats["feed_filings"] += 1
        stats["trades"] += max(n, 0)
        seen.add(acc)


# ------------------------------------------------------------------ 2. the per-company sweep
def run_sweep(univ: Dict[str, str], deadline: float, stats: dict) -> None:
    with cursor() as (conn, cur):
        cur.execute("SELECT cik, swept_at FROM insider_sweep_state")
        swept = {r[0]: r[1] for r in cur.fetchall()}
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    order = sorted(univ, key=lambda c: swept.get(c) or epoch)
    since = (date.today() - timedelta(days=BACKFILL_DAYS)).isoformat()
    for cik in order:
        if time.time() > deadline:
            break
        r = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
        if r is None:
            continue
        rec = (r.json().get("filings") or {}).get("recent") or {}
        forms, accs = rec.get("form") or [], rec.get("accessionNumber") or []
        dates, docs = rec.get("filingDate") or [], rec.get("primaryDocument") or []
        cand = [(accs[i], dates[i], docs[i] if i < len(docs) else None)
                for i in range(min(len(forms), len(accs), len(dates)))
                if forms[i] == "4" and dates[i] >= since]
        seen = _seen([a for a, _, _ in cand])
        finished = True
        for acc, filed, doc in cand:
            if acc in seen:
                continue
            if time.time() > deadline:
                finished = False
                break
            n = process_filing(cik, univ[cik], acc, filed, doc)
            stats["sweep_filings"] += 1
            stats["trades"] += max(n, 0)
        if finished:
            with cursor() as (conn, cur):
                cur.execute("""INSERT INTO insider_sweep_state (cik, swept_at) VALUES (%s, NOW())
                               ON CONFLICT (cik) DO UPDATE SET swept_at = NOW()""", (cik,))
                conn.commit()
            stats["companies_swept"] += 1


def main() -> int:
    started = time.time()
    deadline = started + RUN_BUDGET_SEC
    with cursor() as (conn, cur):
        ensure_tables(cur)
        conn.commit()
    univ = universe()
    stats = {"feed_filings": 0, "sweep_filings": 0, "trades": 0, "companies_swept": 0}
    run_recent(univ, deadline, stats)
    run_sweep(univ, deadline, stats)
    send_pushes()
    with cursor() as (conn, cur):
        cur.execute("SELECT COUNT(*) FROM insider_sweep_state WHERE swept_at IS NOT NULL")
        covered = cur.fetchone()[0]
    print(f"insider trades: {len(univ)} companies; feed filings {stats['feed_filings']}, sweep filings "
          f"{stats['sweep_filings']}, buy/sell rows stored {stats['trades']}, companies swept {stats['companies_swept']} "
          f"(ever swept {covered}/{len(univ)}), {int(time.time() - started)}s")
    if _shared["conn"] is not None:
        _shared["conn"].close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
