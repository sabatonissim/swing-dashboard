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
SOURCE_MAX_CHARS = 10000           # the start of the business section: overview, segments, products, customers
MAX_PER_RUN = 70                   # companies per run
RUN_BUDGET_SEC = 25 * 60
RECHECK_DAYS = 30
LLM_CALL_BUDGET_SEC = "100"        # per AI request, across its fallback models (llm_provider's LLM_BUDGET_SECONDS)
PROFILE_VERSION = 2                # stored as profile["v"]; profiles of an older version are written again
HEBREW_VERSION = 2                 # profile["hv"]; an older Hebrew is translated again from the saved English (v30d)
SOURCE_VERSION = 2                 # profile["sv"]; v35: Wikipedia profiles from before the article was checked are redone
FACTS_VERSION = 3                  # facts["fv"]; v35: the employee count is read again from the report (no AI needed); v36: AES
FIELD_MAX = {"summary": 300, "what": 600, "revenue": 650, "products": 500, "customers": 500}
FIELDS = list(FIELD_MAX)
REQUIRED = ("summary", "what", "revenue")

# advice / valuation words a profile must never contain (the site describes, it does not recommend)
_BANNED = re.compile(r"\b(recommend\w*|undervalued|overvalued|attractive (stock|investment|price)|buy the stock|"
                     r"investors should|good investment|price target)\b|כדאי|ממליצ|המלצ|אטרקטיב|הזדמנות השקעה", re.I)
# sentences about the text instead of the company ("The source does not name specific customers...") — dropped
_META = re.compile(r"\b(the )?(source|source text|filing|report|document) (does not|doesn't|did not|is not|isn't|"
                   r"says|states|mentions|notes|indicates)\b|\bnot (specified|mentioned|stated) in the\b|"
                   r"^\s*it (states|says|notes|mentions|indicates)\b|המקור", re.I)

# v30b (first run 3.11.2026: 20 written, 18 failed — one long answer in two languages timed out or was thrown away
# whole; the Hebrew kept "valves", "transcatheter" and called a heart valve "סתימה"): two shorter requests —
# the English from the report, then the Hebrew from that English with a glossary.
SYSTEM_EN = (
    "You write a short company profile for a stock-research website. "
    "Use ONLY the source text you are given (the company's own annual report to the SEC, or an encyclopedia summary). "
    "Never add facts, numbers, names, years or products that are not in the source. No opinions, no advice, no "
    "forecasts, no marketing adjectives ('leading', 'innovative', 'breakthrough') unless they describe a fact in the source. "
    "Write about the company only — never about the source text itself. "
    "Fields: "
    "summary = 1-2 sentences: what the company is and what it does. "
    "what = 2-3 sentences: how the business works (what it makes or provides, and for whom). "
    "revenue = 2-3 sentences: where the money comes from — its reporting segments or business lines and how each one "
    "earns (sales of products, subscriptions, fees, interest, licensing...). Amounts or percentages ONLY if they are "
    "written in the source, with exactly the same digits (\"36,000\", not \"36 thousand\"). "
    "products = the main products, brands, platforms or services, as one sentence listing them. "
    "customers = who the customers are and, only if the source names them, the main competitors; an empty string "
    "when the source says nothing about customers. "
    "Each field at most 3 short sentences. "
    'Answer with JSON only: {"summary": "", "what": "", "revenue": "", "products": "", "customers": ""}'
)
SYSTEM_HE = (
    "Translate this company profile from English into natural, simple Israeli Hebrew for a stock-research website. "
    "Translate everything except names: company names, product names, brand names and abbreviations of segments stay "
    "in English letters exactly as written (\"SAPIEN\", \"PASCAL\", \"CUDA\", \"TAVR\"). Ordinary English words must be "
    "translated — never leave a lowercase English word in the Hebrew. "
    "Glossary: segment = חטיבה; net sales = מכירות נטו; revenue = הכנסות; subscription = מנוי; license = רישיון; "
    "data center = מרכז נתונים; cloud = ענן; semiconductor = שבב; chip = שבב; GPU = מעבד גרפי (GPU); "
    "software = תוכנה; platform = פלטפורמה; customer = לקוח; distributor = מפיץ; retailer = קמעונאי; "
    "wholesale = סיטונאי; hospital = בית חולים; heart valve = מסתם לב; transcatheter = בצנתר; "
    "medical device = מכשור רפואי; drug = תרופה; clinical trial = ניסוי קליני; loan = הלוואה; deposit = פיקדון; "
    "asset management = ניהול נכסים; investment banking = בנקאות השקעות; insurance = ביטוח; reactor = כור; "
    "fiscal year = שנת כספים; competitor = מתחרה. "
    "A company is feminine in Hebrew ('החברה היא', 'Nvidia מפתחת'). Keep every number exactly as written. "
    "Write Hebrew abbreviations with the Hebrew mark ״ (ארה״ב, מנכ״ל), never with a plain double quote. "
    "Keep the same fields; an empty field stays empty. "
    'Answer with JSON only: {"summary": "", "what": "", "revenue": "", "products": "", "customers": ""}'
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
    # v35: inline tags do not separate letters. Microsoft's heading is "B<span small-caps>USINESS</span>": it became
    # "B USINESS", no section was found, and the Wikipedia fallback made a court case its profile (4.11.2026).
    raw = re.sub(r"(?i)</?(?:span|font|b|i|u|em|strong|small|a|sup|sub|ix:[a-z]+)\b[^>]*>", "", raw)
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
        # v30c: also "Items 1 and 2. Business and Properties" (DK's 10-K — no section was found)
        head = re.compile(r"^\s*items?\s*1\s*(?:(?:and|&)\s*2\s*)?[.:\-–—]?\s*business(?:es)?"
                          r"(?:\s*(?:and|&)\s*properties)?", flags)
        stop = re.compile(r"^\s*(items?\s*1\s*a\s*[.:\-–—]?\s*risk|item\s*2\s*[.:\-–—]?\s*properties|"
                          r"item\s*3\s*[.:\-–—]?\s*legal)", flags)
    for m in head.finditer(text):
        nxt = stop.search(text, m.end())
        end = nxt.start() if nxt else len(text)
        if end - m.end() > 3000:
            return text[m.end():end].strip(" .\n")
    return None


# v35: "65,900 and 69,700 employees, respectively" (the first is the latest year), "2.1 million associates",
# "167,000 full-time equivalent employees", "318,512 employees"
_HEADCOUNT = re.compile(
    r"(?<![\d,.$])(\d{1,3}(?:,\d{3})+|\d{2,7}|\d+(?:\.\d+)?\s+million)(?:\s+and\s+\d[\d,.]*(?:\s+million)?)?\s+"
    r"(?:(?:(?:full|part)[- ]time|seasonal|temporary|equivalent|fte|regular|global|worldwide|total|permanent|active|and|or)(?:,?\s+|/)){0,5}"
    r"(employees|associates|team\s*members|teammates|colleagues|workers|persons|people)\b", re.I)
_HEADCOUNT_CUE = re.compile(r"\b(employ\w*|had|have|has|workforce|headcount|staff|total of|comprised|consisted|grew|increased)\b", re.I)
_NOT_HEADCOUNT = re.compile(r"\b(cover\w*|represented|unions?|bargaining|councils?|contracts?)\b", re.I)


def employees_from(text):
    """The company's headcount: the largest figure in the section, in a sentence that counts its staff. v35: the first match
    was taken, and for Coca-Cola that was "approximately 400 employees in North America were covered by a union contract".
    Nothing is better than a wrong number: AT&T's "a contract covering approximately 9,000 employees" must not pass."""
    best = None
    for m in _HEADCOUNT.finditer(text):
        before = text[max(0, m.start() - 120):m.start()].rsplit(". ", 1)[-1]      # the same sentence only
        after = text[m.end():m.end() + 80].split(". ", 1)[0]
        if not _HEADCOUNT_CUE.search(before) or _NOT_HEADCOUNT.search(before) or _NOT_HEADCOUNT.search(after):
            continue
        if m.group(2).lower() == "people" and not re.search(r"\b(employ|workforce|staff)", before, re.I):
            continue                                       # "we have reached 5,000,000 people" is not the staff
        raw = m.group(1).lower()
        n = round(float(raw.split()[0]) * 1_000_000) if "million" in raw else int(raw.replace(",", ""))
        if "," not in raw and 1900 <= n <= 2100:           # "in 2025 employees ..." is a year
            continue
        if 10 <= n <= 5_000_000 and (best is None or n > best):
            best = n
    return best


_WIKI_API = "https://en.wikipedia.org/w/api.php"
_WIKI_COMPANY = re.compile(r"\b(?:is|are|was)\s+(?:an?|the|one of)\b(?:[^.]|(?<=[A-Z])\.){0,160}?\b(?:company|corporation|"
                           r"conglomerate|bank|banking|holding|manufacturer|producer|retailer|insurer|airline|firm|provider|"
                           r"operator|developer|trust|REIT|utility|enterprise|business|supplier|maker|chain|group)\b", re.I)


def is_company_article(title, text, word):
    """v35: is this Wikipedia article about the company? The first search result used to be taken as it was:
    "MICROSOFT CORP" -> "Microsoft Corp. v Commission of the European Communities", a court case, became MSFT's profile."""
    head = text[:400]
    if re.search(r"\sv\.?\s", title) or "disambiguation" in title.lower() or "may refer to" in head:
        return False
    if word and word.lower() not in f"{title} {head}".lower():
        return False
    return bool(_WIKI_COMPANY.search(head))


def wikipedia_summary(name):
    query = " ".join(_NAME_NOISE.sub(" ", re.sub(r"[/\\][A-Z]{2,3}[/\\]?", " ", name or "")).split())
    query = query.title() if query.isupper() else query                     # "MICROSOFT CORP" -> "Microsoft"
    word = company_word(name)
    try:
        s = requests.get(_WIKI_API, headers=SEC_HEADERS, timeout=10,
                         params={"action": "opensearch", "search": query, "limit": 5, "namespace": 0, "format": "json"}).json()
        for title in (s[1] if len(s) > 1 else []):
            j = requests.get(_WIKI_API, headers=SEC_HEADERS, timeout=10,
                             params={"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1, "redirects": 1,
                                     "titles": title, "format": "json"}).json()
            page = next(iter(((j.get("query") or {}).get("pages") or {}).values()), {})
            text = (page.get("extract") or "").strip()
            real = page.get("title") or title
            if len(text) >= 200 and is_company_article(real, text, word):
                return text, "https://en.wikipedia.org/wiki/" + real.replace(" ", "_")
        return None, None
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


def _sentences(s):
    return [x for x in re.split(r"(?<=[.!?;])\s+", s) if x.strip()]


def _fit(s, limit):
    """At most `limit` characters, cut at a sentence end (a too-long field used to throw the whole answer away)."""
    if len(s) <= limit:
        return s
    out = ""
    for x in _sentences(s):
        if len(out) + len(x) + 1 > limit:
            break
        out = (out + " " + x).strip()
    return out


def _json_fields(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("the answer has no JSON object — answer with the JSON only")
    raw = m.group(0)
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        # a Hebrew abbreviation written with a plain double quote ("ארה"ב") ends the JSON string early —
        # the second run lost two translations to "Expecting ',' delimiter". Use the Hebrew gershayim instead.
        try:
            d = json.loads(re.sub(r'(?<=[א-ת])"(?=[א-ת])', "״", raw))
        except json.JSONDecodeError as e:
            raise ValueError(f"the JSON is broken ({e}) — escape double quotes inside the texts")
    if not isinstance(d, dict):
        raise ValueError("the answer must be one JSON object with the five fields")
    if isinstance(d.get("en"), dict):            # a model that still answers {"en": {...}}
        d = d["en"]
    out = {}
    for f in FIELDS:
        v = d.get(f, "")
        if isinstance(v, list) and all(isinstance(x, str) for x in v):     # "products": ["A", "B"]
            v = "; ".join(v)
        if v is None:
            v = ""
        if not isinstance(v, str):
            raise ValueError(f"{f} is not text")
        out[f] = re.sub(r"\s+", " ", v).strip()
    return out


def clean_english(text, source):
    """The English answer, made safe: sentences with a number that is not in the source or about the source text
    itself are dropped (not the whole answer), fields cut to their size. Rejected (ValueError -> the AI is asked again
    with the reason) only for advice wording or when a required field ends up empty."""
    allowed = _numbers(source)
    p = _json_fields(text)
    for f in FIELDS:
        if _BANNED.search(p[f]):
            raise ValueError(f"the field '{f}' contains advice or valuation wording — describe facts only")
        bad = set()
        kept = []
        for x in _sentences(p[f]):
            extra = _numbers(x) - allowed
            bad |= extra
            if not extra and not _META.search(x):
                kept.append(x)
        p[f] = _fit(" ".join(kept), FIELD_MAX[f])
        if f in REQUIRED and len(p[f]) < 20:
            why = (f"its numbers {sorted(bad)[:4]} do not appear in the source text — copy numbers exactly as written "
                   "there or leave them out") if bad else "it is empty or only talks about the source text"
            raise ValueError(f"the field '{f}' was removed: {why}")
    return p


_NAME_NOISE = re.compile(r"\([^)]*\)|\.com\b|[,.]|\b(inc|incorporated|corp|corporation|co|company|plc|ltd|limited|"
                         r"holdings?|group|n\.?v|s\.?a|ag|se|the|class [a-c])\b", re.I)


def company_word(name):
    """The first word of the company's name ("Alphabet Inc." -> "Alphabet", "Edwards Lifesciences Corp" -> "Edwards")."""
    words = [w for w in _NAME_NOISE.sub(" ", name or "").split() if len(w) >= 3 and re.match(r"[A-Za-z]", w)]
    return words[0] if words else None


def names_in(en):
    """Names in the English profile (capitalised words that do not start a sentence, abbreviations) — the translator is
    told to keep exactly these in English letters."""
    out = []
    for s in _sentences(" ".join(en.values())):
        for i, w in enumerate(re.findall(r"[A-Za-z][A-Za-z0-9&+.\-]*", s)):
            w = w.rstrip(".-")
            if (i > 0 and w[:1].isupper()) or (len(w) > 1 and w.isupper()):
                if w not in out:
                    out.append(w)
    return out[:30]


def clean_hebrew(text, en, company=None):
    """The Hebrew translation: every field Hebrew, no number that is not in the English, no ordinary English word
    left untranslated (names may stay: they are capitalised / abbreviations and appear in the English), and the
    company's own name in English letters (v30d: GOOGL came out as "אלקטיב אינק'" for Alphabet)."""
    allowed = _numbers(" ".join(en.values()))
    # names compared without case — "Youtube" for the English "YouTube" was rejected in the second run
    en_words = {w.rstrip(".-").lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9&+.\-]*", " ".join(en.values()))}
    p = _json_fields(text)
    for f in FIELDS:
        if en[f] and not p[f]:
            raise ValueError(f"the field '{f}' is missing — translate it too")
        if not en[f]:
            p[f] = ""
            continue
        if not re.search(r"[א-ת]", p[f]):
            raise ValueError(f"the field '{f}' is not in Hebrew — translate it into Hebrew")
        if _BANNED.search(p[f]):
            raise ValueError(f"the field '{f}' contains advice wording")
        extra = _numbers(p[f]) - allowed
        if extra:
            raise ValueError(f"the field '{f}' has numbers that are not in the English: {sorted(extra)[:4]}")
        untranslated, invented = [], []
        for w in re.findall(r"[A-Za-z][A-Za-z0-9&+.\-]*", p[f]):
            w = w.rstrip(".-")
            if w.islower() and len(w) > 2:
                untranslated.append(w)
            elif w.lower() not in en_words and not (w.isupper() and len(w) <= 6):   # names must come from the English
                invented.append(w)
        if untranslated:
            raise ValueError(f"in the field '{f}' these English words were not translated into Hebrew: "
                             f"{sorted(set(untranslated))[:6]} — translate them (only names stay in English)")
        if invented:
            raise ValueError(f"in the field '{f}' these names do not appear in the English text: "
                             f"{sorted(set(invented))[:6]} — use only names from the English text")
        p[f] = _fit(p[f], int(FIELD_MAX[f] * 1.3))
    if company and company.lower() not in p["summary"].lower():
        raise ValueError(f"the company name must stay in English letters — write '{company}' in the field 'summary', "
                         "not a Hebrew transliteration")
    return p


ATTEMPTS = 3


def _ask(system, user, clean, max_tokens):
    """v30c (second run 3.11.2026: of the three models in LLM_MODELS only the first answered — the second is
    "Not found for account" (404), the third timed out every time after 60 s — so each small rejection cost a minute
    and the run ended after 25 companies): only the first model, and a rejected answer goes BACK to it with the
    reason ("these English words were not translated: stack, interconnect"), up to ATTEMPTS times."""
    models = [m.strip() for m in (os.environ.get("LLM_MODELS") or llm_provider.DEFAULT_MODELS).split(",") if m.strip()]
    saved = os.environ.get("LLM_MODELS")
    os.environ["LLM_MODELS"] = models[0]
    try:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last = "no answer"
        for _ in range(ATTEMPTS):
            try:
                text, model = llm_provider.chat(msgs, max_tokens=max_tokens, temperature=0.1)
            except RuntimeError as e:                    # timeout / the provider is busy — ask again
                last = str(e)
                continue
            try:
                return clean(text), model
            except ValueError as e:
                last = f"answer rejected — {e}"
                print(f"[llm] {model}: {last} — asking again")
                msgs = msgs[:2] + [{"role": "assistant", "content": text[:4000]},
                                   {"role": "user", "content": f"Problem: {e}. Fix only that and answer again "
                                                               "with the complete JSON, nothing else."}]
        raise RuntimeError(f"{ATTEMPTS} attempts failed — last: {last}")
    finally:
        if saved is None:
            os.environ.pop("LLM_MODELS", None)
        else:
            os.environ["LLM_MODELS"] = saved


def write_english(source, name):
    return _ask(SYSTEM_EN, f"Company: {name}\n\nSOURCE TEXT:\n{source}", lambda t: clean_english(t, source), 1300)


def write_hebrew(en, name=None):
    company = company_word(name)
    if company and company.lower() not in en["summary"].lower():
        company = None                       # the English summary does not name it — nothing to keep
    keep = names_in(en)
    user = (f"Keep these names exactly as written, in English letters: {', '.join(keep)}.\n\n" if keep else "") \
        + json.dumps(en, ensure_ascii=False)
    return _ask(SYSTEM_HE, user, lambda t: clean_hebrew(t, en, company), 2200)


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
    cur.execute("""SELECT ticker, name, filing_date, checked_at, source, COALESCE((profile->>'v')::int, 1) AS v,
                          COALESCE((profile->>'sv')::int, 1) AS sv, COALESCE((facts->>'fv')::int, 1) AS fv,
                          CASE WHEN profile->'he' IS NULL OR jsonb_typeof(profile->'he') = 'null'
                                    OR COALESCE((profile->>'hv')::int, 1) < %s THEN profile->'en' END AS en,
                          (profile->'he' IS NULL OR jsonb_typeof(profile->'he') = 'null'
                           OR COALESCE((profile->>'hv')::int, 1) < %s) AS no_he
                   FROM company_profiles""", (HEBREW_VERSION, HEBREW_VERSION))
    have = {r["ticker"]: r for r in cur.fetchall()}
    cur.close()
    conn.close()                                   # the AI calls take minutes; the DB is reopened only to write
    os.environ["LLM_BUDGET_SECONDS"] = LLM_CALL_BUDGET_SEC
    reasons = {}

    now = time.time()

    def old_wiki(r):                               # v35: written before the article was checked — may be the wrong article
        return r["source"] == "wikipedia" and r["sv"] < SOURCE_VERSION

    def old_facts(r):                              # v35: the employee count was the first figure found (KO: 400)
        return str(r["source"] or "").startswith("sec-") and r["fv"] < FACTS_VERSION

    # due: no profile, an older version (v30's one-request profiles: weak Hebrew), Hebrew still missing, or time to
    # look for a newer annual report. Missing Hebrew first — it is one short request each.
    due = [t for t in candidates
           if t not in have or have[t]["v"] != PROFILE_VERSION or have[t]["no_he"] or old_wiki(have[t]) or old_facts(have[t])
           or (now - have[t]["checked_at"].timestamp()) > RECHECK_DAYS * 86400]
    # v35b: the employee fix comes first (seconds each, no AI). Behind the new profiles it would have waited for weeks
    # (~20 AI companies fit in a run), with KO still showing 400.
    due.sort(key=lambda t: 2 if t not in have or have[t]["v"] != PROFILE_VERSION else 1 if have[t]["no_he"] else 0 if old_facts(have[t]) else 2)
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
            current = bool(old) and old["v"] == PROFILE_VERSION and not old_wiki(old)
            if current and old["no_he"] and isinstance(old["en"], dict):      # only the Hebrew is missing / outdated
                he, model = write_hebrew(old["en"], old["name"])
                db_exec("""UPDATE company_profiles SET profile = profile || jsonb_build_object('he', %s::jsonb, 'hv', %s),
                                                       checked_at = NOW()
                           WHERE ticker = %s""", (json.dumps(he, ensure_ascii=False), HEBREW_VERSION, t))
                written += 1
                print(f"  {t}: Hebrew added ({model})")
                continue
            if current and old["filing_date"]:
                form, filed, url, _ = latest_annual_report(cik)
                if filed and str(old["filing_date"]) == filed:              # no newer annual report: keep the profile
                    if old_facts(old) and url:                              # v35: only the count is read again, no AI
                        section = business_section(html_to_text(sec_get(url, timeout=90).text), form)
                        n = employees_from(section) if section else None
                        db_exec("""UPDATE company_profiles SET checked_at = NOW(),
                                          facts = (COALESCE(facts, '{}'::jsonb) - 'employees') || %s::jsonb
                                   WHERE ticker = %s""", (json.dumps({"fv": FACTS_VERSION, **({"employees": n} if n else {})}), t))
                    else:
                        db_exec("UPDATE company_profiles SET checked_at = NOW() WHERE ticker = %s", (t,))
                    unchanged += 1
                    continue
            source, meta = gather_source(t, cik)
            if not source:
                skipped += 1
                print(f"[warn] {t}: no annual report section and no Wikipedia summary")
                if old and old_wiki(old):                                   # better no profile than a wrong article
                    db_exec("DELETE FROM company_profiles WHERE ticker = %s", (t,))
                continue
            en, model = write_english(source, meta["name"])
            try:
                he, _ = write_hebrew(en, meta["name"])
            except (RuntimeError, ValueError) as e:                         # the English is kept; Hebrew next run
                he = None
                reasons["hebrew later"] = reasons.get("hebrew later", 0) + 1
                print(f"[warn] {t}: Hebrew failed, saved in English for now ({str(e)[:150]})")
            profile = {"v": PROFILE_VERSION, "en": en, "he": he, "hv": HEBREW_VERSION, "sv": SOURCE_VERSION}
            facts = {k: v for k, v in meta["facts"].items() if v}
            facts["fv"] = FACTS_VERSION
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
            msg = str(e)
            why = ("AI timeout" if "Timeout" in msg else "AI answer rejected" if "rejected" in msg
                   else "AI unavailable" if "all models failed" in msg else type(e).__name__)
            reasons[why] = reasons.get(why, 0) + 1
            print(f"[warn] {t}: {type(e).__name__}: {msg[:200]}")
        finally:
            if done_request:
                try:
                    db_exec("DELETE FROM company_profile_requests WHERE ticker = %s", (t,))
                except Exception:  # noqa: BLE001
                    pass
    print(f"company profiles: written {written}, unchanged {unchanged}, skipped {skipped}, failed {failed} "
          f"({int(time.time() - started)}s)")
    if reasons:
        print("company profiles: problems — " + ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items())))
    return 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    sys.exit(main())
