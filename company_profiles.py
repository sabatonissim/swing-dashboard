"""
Company profiles (v30) — "what the company does and how it makes money", at the top of the stock page
=====================================================================================================
Runs in GitHub Actions (company-profiles.yml), started by scheduler.py twice a day; can also be run by hand.

Owner's request (3.11.2026): a fuller "about the company" first on the stock page — how it makes money, its products —
organised by AI, and WITHOUT depending on Yahoo (a real product would lose the data). So the source is the company's own
annual report to the SEC: the "Item 1. Business" section of the latest 10-K (foreign companies: "Item 4. Information
on the Company" of the 20-F). SEC filings are public and free to reuse. Only when a company files neither, the
Wikipedia summary is used instead (CC BY-SA — the page names the source). Checked 3.11.2026 from home: NVDA, KO and JPM
10-Ks each had a 40-55k-character business section, found by the rule in business_section().

The AI (same settings as the daily digest — llm_provider.py) gets the first SOURCE_MAX_CHARS of that section and
writes 5 short fields in Hebrew and English. It may use ONLY the source: an answer with a number that is not in the
source, advice wording, or a missing/too-long field is rejected and the next model is tried (validate()).

Which companies, in this order, at most MAX_PER_RUN AI calls per run: stocks someone opened on the site that had no
profile yet (company_profile_requests, written by the API), watchlists, open trades, the last scan, the S&P 500, the
leaders list. A profile is checked again after RECHECK_DAYS: when the company filed a newer annual report it is
rewritten, otherwise only checked_at moves.

Table company_profiles: ticker, name, profile (JSONB {"he": {...}, "en": {...}}), facts (JSONB: industry, hq,
employees, fiscal_year_end), source ('sec-10k' | 'sec-20f' | 'wikipedia'), source_url, filing_date, model,
built_at, checked_at.
"""
import html
import json
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras
import requests

import llm_provider

SEC_HEADERS = {"User-Agent": "SwingDesk-Dashboard admin@swingdesk.app", "Accept-Encoding": "gzip, deflate"}
SEC_MIN_INTERVAL = 0.15            # SEC fair use: at most 10 requests a second
SOURCE_MAX_CHARS = 14000           # the start of the business section: overview, segments, products, customers
MAX_PER_RUN = 70                   # AI calls per run (about 15-25 s each)
RUN_BUDGET_SEC = 25 * 60
RECHECK_DAYS = 30
FIELD_MAX = {"summary": 300, "what": 600, "revenue": 600, "products": 500, "customers": 500}
FIELDS = list(FIELD_MAX)

# advice / valuation words a profile must never contain (the site describes, it does not recommend)
_BANNED = re.compile(r"\b(recommend\w*|undervalued|overvalued|attractive (stock|investment|price)|buy the stock|"
                     r"investors should|good investment|price target)\b|כדאי|ממליצ|המלצ|אטרקטיב|הזדמנות השקעה", re.I)

SYSTEM = (
    "You write a short company profile for a stock-research website, in English and in Hebrew. "
    "Use ONLY the source text you are given (the company's own annual report to the SEC, or an encyclopedia summary). "
    "Never add facts, numbers, names, years or products that are not in the source. No opinions, no advice, no "
    "forecasts, no marketing adjectives ('leading', 'innovative', 'world-class') unless they describe a fact in the source. "
    "Fields: "
    "summary = 1-2 sentences: what the company is and what it does. "
    "what = 2-3 sentences: how the business works (what it makes or provides, and for whom). "
    "revenue = 2-3 sentences: where the money comes from — its reporting segments or business lines and how each one "
    "earns (sales of products, subscriptions, fees, interest, licensing...). Amounts or percentages ONLY if they are "
    "written in the source. "
    "products = the main products, brands, platforms or services, as one sentence listing them. "
    "customers = who the customers are and, if the source names them, the main competitors; empty string if the source "
    "says nothing about it. "
    "Write any number with exactly the same digits as in the source (\"36,000\", not \"36 thousand\"); when unsure, "
    "leave the number out. "
    "Hebrew: natural Israeli Hebrew; keep company, product and brand names in English letters exactly as in the source; "
    "plain words a non-expert understands. "
    'Answer with JSON only: {"en": {"summary": "", "what": "", "revenue": "", "products": "", "customers": ""}, '
    '"he": {"summary": "", "what": "", "revenue": "", "products": "", "customers": ""}}'
)

_last_sec = [0.0]


def sec_get(url, timeout=40):
    wait = SEC_MIN_INTERVAL - (time.time() - _last_sec[0])
    if wait > 0:
        time.sleep(wait)
    _last_sec[0] = time.time()
    r = requests.get(url, headers=SEC_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def db_exec(sql, params):
    """One write on its own short connection (Neon: open right next to the write, close right after)."""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def init_db(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS company_profiles (
        ticker TEXT PRIMARY KEY, name TEXT, profile JSONB NOT NULL, facts JSONB, source TEXT, source_url TEXT,
        filing_date DATE, model TEXT, built_at TIMESTAMPTZ DEFAULT NOW(), checked_at TIMESTAMPTZ DEFAULT NOW())""")
    cur.execute("""CREATE TABLE IF NOT EXISTS company_profile_requests (
        ticker TEXT PRIMARY KEY, requested_at TIMESTAMPTZ DEFAULT NOW())""")


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,))
    return cur.fetchone()["ok"]


def sp500_tickers():
    try:
        r = requests.get("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
                         timeout=20)
        r.raise_for_status()
        lines = r.text.splitlines()
        col = lines[0].split(",").index("Symbol")
        return [ln.split(",")[col].strip().replace(".", "-") for ln in lines[1:] if ln.strip()]
    except Exception as e:  # noqa: BLE001
        print(f"[warn] S&P 500 list unavailable: {type(e).__name__}: {e}")
        return []


def pick_tickers(cur):
    """Candidates in priority order (no duplicates)."""
    out, seen = [], set()

    def add(ts):
        for t in ts:
            t = (t or "").strip().upper()
            if t and t not in seen and re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", t):
                seen.add(t)
                out.append(t)

    cur.execute("SELECT ticker FROM company_profile_requests ORDER BY requested_at")
    add(r["ticker"] for r in cur.fetchall())
    if _exists(cur, "watchlist"):
        cur.execute("SELECT ticker, COUNT(*) AS n FROM watchlist GROUP BY ticker ORDER BY n DESC")
        add(r["ticker"] for r in cur.fetchall())
    if _exists(cur, "trades"):
        cur.execute("SELECT DISTINCT ticker FROM trades WHERE closed_on IS NULL")
        add(r["ticker"] for r in cur.fetchall())
    cur.execute("""SELECT DISTINCT ticker FROM scanned_stocks
                   WHERE timestamp > (SELECT MAX(timestamp) FROM scanned_stocks) - INTERVAL '2 days'""")
    add(r["ticker"] for r in cur.fetchall())
    add(sp500_tickers())
    if _exists(cur, "stock_leaders"):
        cur.execute("SELECT ticker FROM stock_leaders")
        add(r["ticker"] for r in cur.fetchall())
    return out


# ---------------------------------------------------------------- the source text
def cik_map():
    j = sec_get("https://www.sec.gov/files/company_tickers.json").json()
    return {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in j.values()}


def latest_annual_report(cik):
    """(form, filing_date, url, submissions json) of the newest 10-K or 20-F, or (None, None, None, sub)."""
    sub = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    rec = (sub.get("filings") or {}).get("recent") or {}
    for i, form in enumerate(rec.get("form") or []):
        if form in ("10-K", "20-F"):
            acc = rec["accessionNumber"][i].replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{rec['primaryDocument'][i]}"
            return form, rec["filingDate"][i], url, sub
    return None, None, None, sub


def html_to_text(raw):
    raw = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)             # hidden XBRL facts of inline filings
    raw = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<(br|/p|/div|/tr|/li|/h\d)\b[^>]*>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw)).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def business_section(text, form):
    """The business section of the annual report: a heading at the START of a line followed by a long section.
    Skipped: the table of contents (the same heading with the next item a few lines below) and references inside
    sentences ("see Item 4. Information on the Company" in TSM's risk factors was taken for the heading before)."""
    flags = re.I | re.M
    if form == "20-F":
        head = re.compile(r"^\s*item\s*4\s*[.:\-–—]?\s*information\s+on\s+the\s+company", flags)
        stop = re.compile(r"^\s*(item\s*4\s*a\b|item\s*5\s*[.:\-–—]?\s*operating)", flags)
    else:
        head = re.compile(r"^\s*item\s*1\s*[.:\-–—]?\s*business\b", flags)
        stop = re.compile(r"^\s*(item\s*1\s*a\s*[.:\-–—]?\s*risk|item\s*2\s*[.:\-–—]?\s*properties)", flags)
    for m in head.finditer(text):
        nxt = stop.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        if end - m.end() > 3000:
            return text[m.end():end].strip(" .\n")
    return None


def employees_from(text):
    m = re.search(r"(?:approximately|about|had|employed)\s+([\d,]{3,9})\s+(?:full[- ]time\s+)?(?:employees|people)",
                  text, re.I)
    if not m:
        return None
    try:
        n = int(m.group(1).replace(",", ""))
        return n if 10 <= n <= 5_000_000 else None
    except ValueError:
        return None


def wikipedia_summary(name):
    try:
        s = requests.get("https://en.wikipedia.org/w/api.php", headers=SEC_HEADERS, timeout=10,
                         params={"action": "opensearch", "search": name, "limit": 1, "namespace": 0, "format": "json"}).json()
        titles = s[1] if len(s) > 1 else []
        if not titles:
            return None, None
        title = titles[0]
        j = requests.get("https://en.wikipedia.org/w/api.php", headers=SEC_HEADERS, timeout=10,
                         params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                 "titles": title, "format": "json"}).json()
        page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
        text = (page.get("extract") or "").strip()
        if len(text) < 200:
            return None, None
        return text, "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] Wikipedia unavailable for {name!r}: {type(e).__name__}: {e}")
        return None, None


def gather_source(ticker, cik):
    """(source_text, meta) for one company — meta: name, source, source_url, filing_date, facts."""
    form, filed, url, sub = latest_annual_report(cik)
    name = sub.get("name") or ticker
    addr = ((sub.get("addresses") or {}).get("business") or {})
    city = (addr.get("city") or "").strip()
    city = city.title() if city.isupper() else city                         # "SANTA CLARA" -> "Santa Clara"
    hq = ", ".join(x for x in (city, (addr.get("stateOrCountryDescription") or addr.get("stateOrCountry") or "").strip()) if x)
    fye = sub.get("fiscalYearEnd")          # "0131" = January 31
    facts = {"industry": sub.get("sicDescription"), "hq": hq or None,
             "fiscal_year_end": f"{fye[2:]}/{fye[:2]}" if fye and len(fye) == 4 else None}
    if url:
        try:
            text = html_to_text(sec_get(url, timeout=90).text)
            section = business_section(text, form)
            if section:
                facts["employees"] = employees_from(section)
                return section[:SOURCE_MAX_CHARS], dict(name=name, source="sec-20f" if form == "20-F" else "sec-10k",
                                                        source_url=url, filing_date=filed, facts=facts)
            print(f"[warn] {ticker}: no business section found in the {form} ({url})")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {ticker}: {form} unreadable: {type(e).__name__}: {e}")
    text, wurl = wikipedia_summary(name)
    if text:
        return text[:SOURCE_MAX_CHARS], dict(name=name, source="wikipedia", source_url=wurl, filing_date=filed, facts=facts)
    return None, dict(name=name, source=None, source_url=None, filing_date=filed, facts=facts)


# ---------------------------------------------------------------- the AI answer
def _numbers(s):
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def make_validator(source):
    allowed = _numbers(source)

    def validate(text):
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("no JSON")
        d = json.loads(m.group(0))
        for lang in ("en", "he"):
            p = d.get(lang)
            if not isinstance(p, dict):
                raise ValueError(f"no {lang} part")
            for f in FIELDS:
                v = p.get(f)
                if not isinstance(v, str):
                    raise ValueError(f"{lang}.{f} missing")
                p[f] = re.sub(r"\s+", " ", v).strip()
                if len(p[f]) > FIELD_MAX[f]:
                    raise ValueError(f"{lang}.{f} too long")
                if _BANNED.search(p[f]):
                    raise ValueError(f"{lang}.{f}: advice/valuation wording")
                extra = _numbers(p[f]) - allowed
                if extra:
                    raise ValueError(f"{lang}.{f}: number(s) not in the source: {sorted(extra)[:3]}")
            for f in ("summary", "what", "revenue"):
                if len(p[f]) < 20:
                    raise ValueError(f"{lang}.{f} too short")
        if not re.search(r"[א-ת]", d["he"]["summary"] + d["he"]["what"]):
            raise ValueError("the Hebrew part is not in Hebrew")
        return d
    return validate


def write_profile(source, name):
    validate = make_validator(source)
    text, model = llm_provider.chat(
        [{"role": "system", "content": SYSTEM},
         {"role": "user", "content": f"Company: {name}\n\nSOURCE TEXT:\n{source}"}],
        max_tokens=3500, temperature=0.1, validate=validate)
    d = validate(text)
    return {"en": d["en"], "he": d["he"]}, model


# ---------------------------------------------------------------- main
def main() -> int:
    if not llm_provider.is_configured():
        print("company profiles: LLM_API_KEY is not set — nothing to do")
        return 0
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    init_db(cur)
    conn.commit()
    candidates = pick_tickers(cur)
    cur.execute("SELECT ticker, filing_date, checked_at, built_at FROM company_profiles")
    have = {r["ticker"]: r for r in cur.fetchall()}
    cur.close()
    conn.close()                                   # the AI calls take minutes; the DB is reopened only to write

    now = time.time()
    due = [t for t in candidates
           if t not in have or (now - have[t]["checked_at"].timestamp()) > RECHECK_DAYS * 86400]
    print(f"company profiles: {len(candidates)} candidates, {len(have)} profiles saved, {len(due)} due")
    if not due:
        return 0
    ciks = cik_map()

    written, unchanged, skipped, failed = 0, 0, 0, 0
    started = time.time()
    for t in due:
        if time.time() - started > RUN_BUDGET_SEC or written + failed >= MAX_PER_RUN:
            print("company profiles: run budget used up, the rest waits for the next run")
            break
        done_request = True
        try:
            cik = ciks.get(t)
            if not cik:
                skipped += 1                                         # not an SEC filer (ETF, foreign without SEC filings)
                continue
            old = have.get(t)
            if old and old["filing_date"]:
                form, filed, _, _ = latest_annual_report(cik)
                if filed and str(old["filing_date"]) == filed:              # no newer annual report: keep the profile
                    db_exec("UPDATE company_profiles SET checked_at = NOW() WHERE ticker = %s", (t,))
                    unchanged += 1
                    continue
            source, meta = gather_source(t, cik)
            if not source:
                skipped += 1
                print(f"[warn] {t}: no annual report section and no Wikipedia summary")
                continue
            profile, model = write_profile(source, meta["name"])
            facts = {k: v for k, v in meta["facts"].items() if v}
            db_exec("""INSERT INTO company_profiles (ticker, name, profile, facts, source, source_url, filing_date,
                                                     model, built_at, checked_at)
                       VALUES (%s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, NOW(), NOW())
                       ON CONFLICT (ticker) DO UPDATE SET name = EXCLUDED.name, profile = EXCLUDED.profile,
                           facts = EXCLUDED.facts, source = EXCLUDED.source, source_url = EXCLUDED.source_url,
                           filing_date = EXCLUDED.filing_date, model = EXCLUDED.model, built_at = NOW(),
                           checked_at = NOW()""",
                    (t, meta["name"], json.dumps(profile, ensure_ascii=False), json.dumps(facts, ensure_ascii=False),
                     meta["source"], meta["source_url"], meta["filing_date"], model))
            written += 1
            print(f"  {t}: profile written ({meta['source']}, {model})")
        except llm_provider.LLMAuthError as e:
            print(f"company profiles: the AI key was rejected — stopping ({e})")
            done_request = False
            break
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[warn] {t}: {type(e).__name__}: {str(e)[:200]}")
        finally:
            if done_request:
                try:
                    db_exec("DELETE FROM company_profile_requests WHERE ticker = %s", (t,))
                except Exception:  # noqa: BLE001
                    pass
    print(f"company profiles: written {written}, unchanged {unchanged}, skipped {skipped}, failed {failed} "
          f"({int(time.time() - started)}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
