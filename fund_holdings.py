"""
Big-fund holdings (v25) — what well-known funds hold, from their quarterly 13F filings to the SEC
================================================================================================
Runs in GitHub Actions (fund-holdings.yml), started daily by scheduler.py; a fund is re-read only when it has filed a
NEW 13F (so most runs make ~11 small requests and stop). 13F = US institutions with $100M+ must list their US stock
positions within 45 days after each quarter — so the data is up to ~4.5 months old by design (shown on the site).

Per fund: the latest 13F-HR and the one before it -> positions summed per CUSIP (several lines per stock are common;
option lines — putCall — are left out) -> the top TOP_N by value, each marked new / added / reduced / unchanged vs the
previous quarter, plus the previous top positions that were sold out completely.
CUSIP -> ticker: OpenFIGI's public mapping API (free, no key: up to 10 codes per request, ~25 requests a minute),
remembered forever in cusip_map so every code is asked about once.
SEC fair access: a descriptive User-Agent and a pause between requests.
"""
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

import psycopg2
import psycopg2.extras
import requests

SEC_HEADERS = {"User-Agent": "SwingDesk-Dashboard admin@swingdesk.app", "Accept-Encoding": "gzip, deflate"}
TOP_N = 40
FUNDS = [   # (CIK, display name, manager) — checked on 30.10.2026 that each still files 13F-HR
    ("1067983", "Berkshire Hathaway", "Warren Buffett"),
    ("1336528", "Pershing Square", "Bill Ackman"),
    ("1656456", "Appaloosa", "David Tepper"),
    ("1536411", "Duquesne Family Office", "Stanley Druckenmiller"),
    ("1040273", "Third Point", "Dan Loeb"),
    ("1061768", "Baupost Group", "Seth Klarman"),
    ("1029160", "Soros Fund Management", "George Soros"),
    ("1350694", "Bridgewater Associates", "Ray Dalio"),
    ("1167483", "Tiger Global", "Chase Coleman"),
    ("1061165", "Lone Pine Capital", "Stephen Mandel"),
    ("1135730", "Coatue Management", "Philippe Laffont"),
]


def sec_get(url, as_json=True):
    for attempt in range(3):
        try:
            r = requests.get(url, headers=SEC_HEADERS, timeout=30)
            time.sleep(0.25)
            if r.status_code == 200:
                return r.json() if as_json else r.content
            print(f"[warn] SEC {r.status_code} for {url}")
        except Exception as e:
            print(f"[warn] SEC request failed ({type(e).__name__}): {url}")
        time.sleep(2 * (attempt + 1))
    return None


def latest_13f(cik: str) -> List[dict]:
    """The two newest original 13F-HR filings: [{accession, period, filed}] (newest first)."""
    sub = sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
    if not sub:
        return []
    f = sub.get("filings", {}).get("recent", {})
    out = []
    for i, form in enumerate(f.get("form", [])):
        if form == "13F-HR":
            out.append({"accession": f["accessionNumber"][i], "period": f["reportDate"][i], "filed": f["filingDate"][i]})
        if len(out) == 2:
            break
    return out


def holdings(cik: str, accession: str) -> Dict[str, dict]:
    """{cusip: {name, shares, value}} summed over the filing's lines (options excluded)."""
    acc = accession.replace("-", "")
    idx = sec_get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/index.json")
    if not idx:
        return {}
    xmls = [it for it in idx.get("directory", {}).get("item", [])
            if it.get("name", "").lower().endswith(".xml") and it.get("name") != "primary_doc.xml"]
    if not xmls:
        return {}
    xmls.sort(key=lambda it: int(it.get("size") or 0), reverse=True)        # the information table is the big one
    raw = sec_get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{xmls[0]['name']}", as_json=False)
    if not raw:
        return {}
    root = ET.fromstring(raw)
    strip = lambda tag: tag.split("}", 1)[-1]
    out: Dict[str, dict] = {}
    for row in root.iter():
        if strip(row.tag) != "infoTable":
            continue
        vals = {}
        for el in row.iter():
            vals[strip(el.tag)] = (el.text or "").strip()
        if vals.get("putCall") or vals.get("sshPrnamtType", "SH") != "SH":
            continue
        cusip = vals.get("cusip", "").upper()
        try:
            shares, value = float(vals.get("sshPrnamt") or 0), float(vals.get("value") or 0)
        except ValueError:
            continue
        if not cusip:
            continue
        h = out.setdefault(cusip, {"name": vals.get("nameOfIssuer", "")[:80], "shares": 0.0, "value": 0.0})
        h["shares"] += shares
        h["value"] += value
    return out


def map_cusips(cur, cusips: List[str]) -> Dict[str, Optional[str]]:
    cur.execute("SELECT cusip, ticker FROM cusip_map WHERE cusip = ANY(%s)", (cusips,))
    known = {r["cusip"]: r["ticker"] for r in cur.fetchall()}
    todo = [c for c in cusips if c not in known]
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        try:
            r = requests.post("https://api.openfigi.com/v3/mapping", timeout=30,
                              json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch])
            if r.status_code == 429:
                print("[warn] OpenFIGI rate limit — the rest is mapped on the next run")
                break
            r.raise_for_status()
            res = r.json()
        except Exception as e:
            print(f"[warn] OpenFIGI failed: {type(e).__name__}: {e}")
            break
        for c, item in zip(batch, res):
            data = (item or {}).get("data") or []
            tk = (data[0].get("ticker") or "").replace("/", "-").upper()[:12] if data else None
            known[c] = tk or None
            cur.execute("""INSERT INTO cusip_map (cusip, ticker, name) VALUES (%s, %s, %s)
                           ON CONFLICT (cusip) DO UPDATE SET ticker = EXCLUDED.ticker, name = EXCLUDED.name""",
                        (c, known[c], (data[0].get("name") if data else None)))
        time.sleep(2.6)                    # ~23 requests a minute, under the keyless limit
    return known


def ensure_tables(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS cusip_map (cusip TEXT PRIMARY KEY, ticker TEXT, name TEXT)""")
    cur.execute("""CREATE TABLE IF NOT EXISTS fund_filings (
        cik TEXT PRIMARY KEY, fund TEXT, manager TEXT, accession TEXT, period DATE, filed DATE,
        total_value DOUBLE PRECISION, n_positions INTEGER, updated_at TIMESTAMPTZ DEFAULT NOW())""")
    cur.execute("""CREATE TABLE IF NOT EXISTS fund_holdings (
        cik TEXT NOT NULL, cusip TEXT NOT NULL, ticker TEXT, name TEXT, rank INTEGER,
        shares DOUBLE PRECISION, value DOUBLE PRECISION, pct REAL, prev_shares DOUBLE PRECISION,
        change TEXT, PRIMARY KEY (cik, cusip))""")


def main() -> int:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    ensure_tables(cur)
    conn.commit()
    cur.execute("SELECT cik, accession FROM fund_filings")
    stored = {r["cik"]: r["accession"] for r in cur.fetchall()}
    cur.close()
    conn.close()

    updated = 0
    for cik, fund, manager in FUNDS:
        filings = latest_13f(cik)
        if not filings:
            print(f"[warn] {fund}: no 13F-HR found")
            continue
        if stored.get(cik) == filings[0]["accession"]:
            continue                                        # nothing new since the last run
        cur_h = holdings(cik, filings[0]["accession"])
        prev_h = holdings(cik, filings[1]["accession"]) if len(filings) > 1 else {}
        if not cur_h:
            print(f"[warn] {fund}: could not read the holdings of {filings[0]['accession']}")
            continue
        total = sum(h["value"] for h in cur_h.values()) or 1.0
        top = sorted(cur_h.items(), key=lambda kv: kv[1]["value"], reverse=True)[:TOP_N]
        prev_top = sorted(prev_h.items(), key=lambda kv: kv[1]["value"], reverse=True)[:TOP_N]
        sold = [(c, h) for c, h in prev_top if c not in cur_h]
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        tickers = map_cusips(cur, [c for c, _ in top] + [c for c, _ in sold])
        cur.execute("DELETE FROM fund_holdings WHERE cik = %s", (cik,))
        for rank, (c, h) in enumerate(top, 1):
            p = prev_h.get(c)
            if not p:
                change = "new"
            elif h["shares"] > p["shares"] * 1.02:
                change = "added"
            elif h["shares"] < p["shares"] * 0.98:
                change = "reduced"
            else:
                change = "same"
            cur.execute("""INSERT INTO fund_holdings (cik, cusip, ticker, name, rank, shares, value, pct, prev_shares, change)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (cik, c, tickers.get(c), h["name"], rank, h["shares"], h["value"], round(100 * h["value"] / total, 2),
                         p["shares"] if p else None, change))
        for c, h in sold:
            cur.execute("""INSERT INTO fund_holdings (cik, cusip, ticker, name, rank, shares, value, pct, prev_shares, change)
                           VALUES (%s, %s, %s, %s, NULL, 0, 0, 0, %s, 'sold')""", (cik, c, tickers.get(c), h["name"], h["shares"]))
        cur.execute("""INSERT INTO fund_filings (cik, fund, manager, accession, period, filed, total_value, n_positions, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (cik) DO UPDATE SET fund = EXCLUDED.fund, manager = EXCLUDED.manager,
                           accession = EXCLUDED.accession, period = EXCLUDED.period, filed = EXCLUDED.filed,
                           total_value = EXCLUDED.total_value, n_positions = EXCLUDED.n_positions, updated_at = NOW()""",
                    (cik, fund, manager, filings[0]["accession"], filings[0]["period"], filings[0]["filed"], total, len(cur_h)))
        conn.commit()
        cur.close()
        conn.close()
        updated += 1
        print(f"{fund}: period {filings[0]['period']}, {len(cur_h)} positions, top {len(top)} saved, {len(sold)} sold out")
    # codes left without a ticker because OpenFIGI was busy last time get another try (one run at a time)
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""SELECT DISTINCT cusip FROM fund_holdings
                   WHERE ticker IS NULL AND cusip NOT IN (SELECT cusip FROM cusip_map)""")
    missing = [r["cusip"] for r in cur.fetchall()]
    if missing:
        map_cusips(cur, missing)
    cur.execute("""UPDATE fund_holdings h SET ticker = m.ticker FROM cusip_map m
                   WHERE h.cusip = m.cusip AND h.ticker IS NULL AND m.ticker IS NOT NULL""")
    conn.commit()
    cur.close()
    conn.close()
    print(f"fund holdings: {updated} fund(s) updated, {len(FUNDS) - updated} unchanged or skipped"
          + (f", {len(missing)} code(s) re-tried" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
