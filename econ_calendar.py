"""
econ_calendar.py — US Economic Calendar ("Upcoming Market Events")
===================================================================
Architecture:   DATA PROVIDERS  ->  PROCESSING / MERGE  ->  UNIFORM EVENT DICT  ->  UI

The UI (index.html) only ever sees the uniform event dict built by
`build_calendar()`; it knows nothing about FRED / any forecast vendor
To replace a provider, edit ONLY the matching function in the
"PROVIDERS" section below (keep its return shape) and, if you like, flip
the `ECON_FORECAST_PROVIDER` env var. Nothing else changes.

PROVIDERS (v11.2 — NO sign-up / API key anywhere; everything is an official public file):
  schedule  : FOMC decision dates (static table, federalreserve.gov)
              + BLS release calendar  (bls.gov/schedule/news_release/bls.ics)
                -> CPI, Core CPI, PPI, NFP, Unemployment, JOLTS
              + BEA release calendar  (bea.gov/news/schedule/ics/online-calendar-subscription.ics)
                -> GDP, PCE, Core PCE
              + Census release schedule page (census.gov/retail/release_schedule.html) -> Retail Sales
              + rules: Jobless Claims (every Thursday), plus ESTIMATES (flagged
                "estimated") for ISM, Conference Board, UMich which have no free official calendar
  actual    : FRED public CSV download (fred.stlouisfed.org/graph/fredgraph.csv?id=SERIES —
              the same file as the "Download CSV" button on every FRED chart; no key).
              The release is accepted only when the series already contains the EXACT reference
              period of that release, so we never show last month's number as the new one.
              % changes (m/m) are computed locally from the index levels.
  forecast  : NONE by default (market consensus is proprietary; no free licensed source).
              A licensed provider can be plugged in via FORECAST_PROVIDERS (contract below).
              While none is active the UI hides the Forecast column.

Every provider failure degrades gracefully: the event is still shown, the
missing field is just "—".  Provider health is returned in `meta.providers`.
"""

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_ICS = "https://bea.gov/news/schedule/ics/online-calendar-subscription.ics"
CENSUS_RETAIL = "https://www.census.gov/retail/release_schedule.html"
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}
# Some government sites turn away browser-like scripts but accept an honest, identified client (and vice versa) — try both.
HTTP_HEADERS_ALT = {"User-Agent": "SwingDesk/1.0 (+https://swing-desk-tau.vercel.app)"}

# which forecast provider to use: "none" (default) or a key of FORECAST_PROVIDERS
FORECAST_PROVIDER = os.environ.get("ECON_FORECAST_PROVIDER", "none").lower()

SCHEDULE_TTL = 6 * 3600  # government calendars change rarely (except shutdowns/reschedules)
FRED_LATEST_TTL = 6 * 3600
FRED_PENDING_TTL = 150  # re-check a just-released number every 2.5 minutes
RESULT_TTL = 90         # cache of the final merged result
ACTUAL_LOOKBACK_DAYS = 3

# --------------------------------------------------------------------------
# CATALOG — the 16 supported events. One entry per event TYPE.
#   fred       : how to read the actual from the FRED public CSV (series, units transform, display fmt)
#   sched      : "fomc" | "bls" | "bea" | "census" | "rule_claims" | "rule_umich" | "rule_ism_mfg" | "rule_ism_svc" | "rule_cb"
#   match      : regexes a forecast provider adapter uses to map ITS event titles to this type
# fmt: pct1 / pct2 (percent), jobs_k (thousands, signed), claims (persons -> K),
#      jolts (thousands -> M), idx1 (index level)
# --------------------------------------------------------------------------
AFFECT_LABELS = {
    "fed": ("Fed rate expectations", "ציפיות לריבית הפד"),
    "yields": ("Treasury yields", "תשואות אג\"ח ממשלתי"),
    "usd": ("USD", "הדולר"),
    "sp500": ("S&P 500", "S&P 500"),
    "nasdaq": ("Nasdaq", "נאסד\"ק"),
    "growth": ("Growth / Tech stocks", "מניות צמיחה וטכנולוגיה"),
    "financials": ("Banks / Financials", "בנקים ופיננסים"),
    "cyclicals": ("Cyclicals / Industrials", "מניות מחזוריות ותעשייה"),
    "consumer": ("Consumer stocks", "מניות צרכנים"),
}

CATALOG = {
    "fomc": dict(
        name_en="Fed Interest Rate Decision (FOMC)", name_he="החלטת הריבית של הפד (FOMC)",
        impact="high", time_et="14:00", sched="fomc",
        fred=dict(series="DFEDTARU", kind="daily_level", fmt="pct2"),
        match=[r"^Federal Funds Rate$"],
        what_en="The Fed's decision on its benchmark interest rate (shown: upper bound of the target range). It sets the cost of money for the whole economy.",
        what_he="החלטת הפד על ריבית הבסיס (מוצג: הגבול העליון של הטווח). היא קובעת את מחיר הכסף לכל המשק.",
        affects=["fed", "yields", "usd", "sp500", "nasdaq", "growth", "financials"]),
    "cpi": dict(
        name_en="CPI (m/m)", name_he="מדד המחירים לצרכן – CPI (חודשי)",
        impact="high", time_et="08:30", sched="bls",
        fred=dict(series="CPIAUCSL", units="pch", fmt="pct1"),
        match=[r"^CPI m/m$"],
        what_en="Monthly change in consumer prices — the main inflation gauge. Hotter than expected means the Fed may keep rates higher for longer.",
        what_he="השינוי החודשי במחירי הצרכן — מדד האינפלציה המרכזי. מעל הצפי = הפד עשוי להחזיק ריבית גבוהה יותר זמן רב יותר.",
        affects=["fed", "yields", "usd", "sp500", "nasdaq", "growth"]),
    "core_cpi": dict(
        name_en="Core CPI (m/m)", name_he="CPI ליבה (חודשי)",
        impact="high", time_et="08:30", sched="bls",
        fred=dict(series="CPILFESL", units="pch", fmt="pct1"),
        match=[r"^Core CPI m/m$"],
        what_en="CPI excluding food and energy, which swing a lot. The Fed watches it as a cleaner read on underlying inflation.",
        what_he="CPI בלי מזון ואנרגיה (שמתנדנדים חזק). הפד מסתכל עליו כקריאה נקייה יותר של האינפלציה הבסיסית.",
        affects=["fed", "yields", "usd", "sp500", "nasdaq", "growth"]),
    "pce": dict(
        name_en="PCE Price Index (m/m)", name_he="מדד PCE (חודשי)",
        impact="high", time_et="08:30", sched="bea",
        fred=dict(series="PCEPI", units="pch", fmt="pct1"),
        match=[r"^PCE Price Index m/m$"],
        what_en="Inflation measured through consumer spending — the Fed's preferred inflation measure.",
        what_he="אינפלציה לפי הוצאות הצרכנים — מדד האינפלציה המועדף על הפד.",
        affects=["fed", "yields", "usd", "sp500", "nasdaq"]),
    "core_pce": dict(
        name_en="Core PCE Price Index (m/m)", name_he="Core PCE (חודשי)",
        impact="high", time_et="08:30", sched="bea",
        fred=dict(series="PCEPILFE", units="pch", fmt="pct1"),
        match=[r"^Core PCE Price Index m/m$"],
        what_en="PCE without food and energy. This is the number the Fed's 2% inflation target is really judged against.",
        what_he="PCE בלי מזון ואנרגיה. זה המדד שמולו נמדד יעד האינפלציה של הפד (2%).",
        affects=["fed", "yields", "usd", "sp500", "nasdaq", "growth"]),
    "nfp": dict(
        name_en="Nonfarm Payrolls (NFP)", name_he="דוח התעסוקה – NFP",
        impact="high", time_et="08:30", sched="bls",
        fred=dict(series="PAYEMS", units="chg", fmt="jobs_k"),
        match=[r"^Non-Farm Employment Change$"],
        what_en="Jobs added to the US economy last month (outside farming). Strong jobs can mean fewer rate cuts; weak jobs raise growth fears.",
        what_he="כמה משרות נוספו במשק האמריקאי בחודש האחרון (ללא חקלאות). תעסוקה חזקה = פחות הורדות ריבית; חלשה = חשש מהאטה.",
        affects=["fed", "yields", "usd", "sp500", "nasdaq", "cyclicals"]),
    "unemployment": dict(
        name_en="Unemployment Rate", name_he="שיעור האבטלה",
        impact="high", time_et="08:30", sched="bls",
        fred=dict(series="UNRATE", units="lin", fmt="pct1"),
        match=[r"^Unemployment Rate$"],
        what_en="Share of the labor force without a job. Rising unemployment signals a cooling economy and pushes rate-cut hopes up.",
        what_he="שיעור המובטלים מכוח העבודה. עלייה באבטלה מסמנת התקררות במשק ומעלה ציפיות להורדת ריבית.",
        affects=["fed", "yields", "usd", "sp500", "consumer"]),
    "gdp": dict(
        name_en="GDP (q/q, annualized)", name_he="תוצר – GDP (רבעוני, מאוּנְתָּן)",
        impact="high", time_et="08:30", sched="bea",
        fred=dict(series="A191RL1Q225SBEA", units="lin", fmt="pct1"),
        match=[r"^(Advance |Prelim |Second |Final )?GDP q/q$"],
        what_en="Growth rate of the whole US economy for the quarter. It frames the 'soft landing vs recession' debate.",
        what_he="קצב הצמיחה של כל המשק האמריקאי ברבעון. הוא מכתיב את הדיון \"נחיתה רכה או מיתון\".",
        affects=["sp500", "nasdaq", "yields", "usd", "cyclicals"]),
    "retail": dict(
        name_en="Retail Sales (m/m)", name_he="מכירות קמעונאיות (חודשי)",
        impact="high", time_et="08:30", sched="census",
        fred=dict(series="RSAFS", units="pch", fmt="pct1"),
        match=[r"^Retail Sales m/m$"],
        what_en="Monthly change in what consumers spend in stores and online. Consumers are ~70% of the economy.",
        what_he="השינוי החודשי בהוצאות הצרכנים בחנויות ובאונליין. הצרכנים הם כ-70% מהמשק.",
        affects=["sp500", "consumer", "yields", "usd"]),
    "ppi": dict(
        name_en="PPI (m/m)", name_he="מדד המחירים ליצרן – PPI (חודשי)",
        impact="medium", time_et="08:30", sched="bls",
        fred=dict(series="PPIFIS", units="pch", fmt="pct1"),
        match=[r"^PPI m/m$"],
        what_en="Price changes at the producer level — often an early hint of where consumer inflation is heading.",
        what_he="שינוי מחירים ברמת היצרן — לעיתים רמז מוקדם לכיוון האינפלציה לצרכן.",
        affects=["fed", "yields", "usd", "sp500"]),
    "ism_mfg": dict(
        name_en="ISM Manufacturing PMI", name_he="מדד ISM תעשייה (PMI)",
        impact="medium", time_et="10:00", sched="rule_ism_mfg", fred=None,
        match=[r"^ISM Manufacturing PMI$"],
        what_en="Survey of factory managers. Above 50 = manufacturing expanding, below 50 = shrinking.",
        what_he="סקר מנהלי רכש בתעשייה. מעל 50 = התרחבות, מתחת ל-50 = התכווצות.",
        affects=["sp500", "cyclicals", "yields", "usd"]),
    "ism_services": dict(
        name_en="ISM Services PMI", name_he="מדד ISM שירותים (PMI)",
        impact="medium", time_et="10:00", sched="rule_ism_svc", fred=None,
        match=[r"^ISM Services PMI$"],
        what_en="Survey of service-sector managers (most of the US economy). Above 50 = expanding.",
        what_he="סקר מנהלי רכש במגזר השירותים (רוב המשק). מעל 50 = התרחבות.",
        affects=["sp500", "yields", "usd"]),
    "jolts": dict(
        name_en="JOLTS Job Openings", name_he="משרות פנויות – JOLTS",
        impact="medium", time_et="10:00", sched="bls",
        fred=dict(series="JTSJOL", units="lin", fmt="jolts"),
        match=[r"^JOLTS Job Openings$"],
        what_en="Number of open jobs. A hot labor market (many openings) can keep wages and inflation elevated.",
        what_he="מספר המשרות הפנויות. שוק עבודה חם (הרבה משרות) יכול להחזיק שכר ואינפלציה גבוהים.",
        affects=["fed", "yields", "usd"]),
    "claims": dict(
        name_en="Initial Jobless Claims", name_he="דרישות סיוע ראשוניות לאבטלה",
        impact="medium", time_et="08:30", sched="rule_claims",
        fred=dict(series="ICSA", units="lin", fmt="claims"),
        match=[r"^Unemployment Claims$"],
        what_en="Weekly count of people newly filing for unemployment benefits — the fastest read on layoffs.",
        what_he="מספר הפונים בשבוע לדמי אבטלה לראשונה — המדד המהיר ביותר לפיטורים.",
        affects=["yields", "usd", "sp500"]),
    "consumer_conf": dict(
        name_en="Consumer Confidence (Conference Board)", name_he="אמון הצרכנים (Conference Board)",
        impact="medium", time_et="10:00", sched="rule_cb", fred=None,
        match=[r"^CB Consumer Confidence$"],
        what_en="How optimistic households feel about jobs and the economy. Confident consumers spend more.",
        what_he="עד כמה משקי הבית אופטימיים לגבי עבודה וכלכלה. צרכנים בטוחים מוציאים יותר.",
        affects=["sp500", "consumer", "usd"]),
    "umich": dict(
        name_en="UMich Consumer Sentiment", name_he="סנטימנט הצרכנים – מישיגן",
        impact="medium", time_et="10:00", sched="rule_umich", fred=None,
        match=[r"^(Prelim |Revised |Final )?UoM Consumer Sentiment$"],
        what_en="University of Michigan survey of consumer mood, including inflation expectations the Fed watches closely.",
        what_he="סקר אוניברסיטת מישיגן על מצב הרוח של הצרכנים, כולל ציפיות אינפלציה שהפד עוקב אחריהן.",
        affects=["sp500", "consumer", "yields", "usd"]),
}
_MATCH_REGEX = [(etype, re.compile(p, re.I)) for etype, c in CATALOG.items() for p in c["match"]]

# FOMC decision days (2nd day of each meeting, statement 14:00 ET).
# Source: federalreserve.gov/monetarypolicy/fomccalendars.htm. 2027 is tentative.
FOMC_DECISIONS = [
    ("2026-01-28", False), ("2026-03-18", False), ("2026-04-29", False), ("2026-06-17", False),
    ("2026-07-29", False), ("2026-09-16", False), ("2026-10-28", False), ("2026-12-09", False),
    ("2027-01-27", True), ("2027-03-17", True), ("2027-04-28", True), ("2027-06-09", True),
    ("2027-07-28", True), ("2027-09-15", True), ("2027-10-27", True), ("2027-12-08", True),
]

# --------------------------------------------------------------------------
# small helpers: state, cache, provider health
# --------------------------------------------------------------------------
_lock = threading.Lock()
_cache = {}                 # key -> (timestamp, value)
_status = {}                # provider -> {"ok": bool, "error": str|None, "ts": float}
_unmatched_titles = []      # provider titles that matched no supported event (for /debug)
_table_ready = False


def _cget(key, ttl):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    return None


def _cset(key, value):
    _cache[key] = (time.time(), value)
    return value


def _set_status(provider, ok, error=None):
    _status[provider] = {"ok": ok, "error": (str(error)[:160] if error else None), "ts": time.time()}


def _et_dt(d, hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET)


# --------------------------------------------------------------------------
# value formatting / parsing (display strings must be comparable)
# --------------------------------------------------------------------------
def format_value(v, fmt):
    if v is None:
        return None
    if fmt == "pct1":
        return f"{v:.1f}%"
    if fmt == "pct2":
        return f"{v:.2f}%"
    if fmt == "jobs_k":
        return f"{v:+,.0f}K"
    if fmt == "claims":
        return f"{v / 1000:,.0f}K"
    if fmt == "jolts":
        return f"{v / 1000:.2f}M"
    if fmt == "idx1":
        return f"{v:.1f}"
    return str(v)


_NUM = re.compile(r"^\s*([-+]?\d[\d,]*\.?\d*)\s*([%KMBT]?)\s*$", re.I)
_MULT = {"": 1.0, "%": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_num(s):
    """'0.3%'->0.3, '175K'->175000, '7.20M'->7200000. None if not numeric."""
    if s is None:
        return None
    m = _NUM.match(str(s))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "")) * _MULT[m.group(2).upper()]
    except ValueError:
        return None


def compute_surprise(actual, forecast):
    a, f = parse_num(actual), parse_num(forecast)
    if a is None or f is None:
        return None
    d = a - f
    if abs(d) < 1e-9:
        return {"direction": "inline", "diff": None}
    suffix = re.sub(r"[\d.,\s+-]", "", str(actual)).upper()
    scale = _MULT.get(suffix, 1.0)
    shown = d / scale
    txt = f"{shown:+.2f}".rstrip("0").rstrip(".") + suffix
    return {"direction": "above" if d > 0 else "below", "diff": txt}


# --------------------------------------------------------------------------
# PROVIDER: schedule
# --------------------------------------------------------------------------
def _us_holidays(year):
    """Federal holidays (observed) — only used to estimate ISM / Conf. Board dates."""
    def nth(month, weekday, n):
        d = date(year, month, 1)
        d += timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
        return d

    def last(month, weekday):
        d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
        return d - timedelta(days=(d.weekday() - weekday) % 7)

    def obs(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d

    return {
        obs(date(year, 1, 1)), nth(1, 0, 3), nth(2, 0, 3), last(5, 0), obs(date(year, 6, 19)),
        obs(date(year, 7, 4)), nth(9, 0, 1), nth(10, 0, 2), obs(date(year, 11, 11)),
        nth(11, 3, 4), obs(date(year, 12, 25)),
    }


def _nth_business_day(year, month, n):
    hol = _us_holidays(year) | _us_holidays(year + 1) | _us_holidays(year - 1)
    d, count = date(year, month, 1), 0
    while True:
        if d.weekday() < 5 and d not in hol:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)


def _last_tuesday(year, month):
    d = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - 1) % 7)


def _http_text(url, timeout=12):
    last = None
    for headers in (HTTP_HEADERS, HTTP_HEADERS_ALT):
        r = requests.get(url, headers=headers, timeout=timeout)
        if r.status_code == 200:
            return r.text
        last = r.status_code
        if r.status_code not in (403, 429):
            break
    raise RuntimeError(f"HTTP {last} for {url.split('/')[2]}")


def _cached_text(key, url):
    """Fetch with a 6h cache; on failure serve the last good copy if we have one."""
    hit = _cget(key, SCHEDULE_TTL)
    if hit is not None:
        return hit
    try:
        return _cset(key, _http_text(url))
    except Exception:
        stale = _cache.get(key)
        if stale:
            return stale[1]
        raise


def parse_ics(text):
    """Minimal iCalendar reader -> [{summary, start}] where start is a tz-aware ET datetime,
    or a plain date for all-day entries."""
    text = re.sub(r"\r?\n[ \t]", "", text)           # unfold wrapped lines
    out = []
    for block in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.S):
        summ = re.search(r"^SUMMARY[^:]*:(.*)$", block, re.M)
        dt = re.search(r"^DTSTART([^:\n]*):(\S+)", block, re.M)
        if not summ or not dt:
            continue
        params, val = dt.group(1), dt.group(2).strip()
        summary = summ.group(1).strip().replace("\\,", ",")
        try:
            if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", val):
                start = date(int(val[:4]), int(val[4:6]), int(val[6:8]))
            else:
                naive = datetime(int(val[:4]), int(val[4:6]), int(val[6:8]), int(val[9:11]), int(val[11:13]), 0)
                start = naive.replace(tzinfo=UTC).astimezone(ET) if val.endswith("Z") else naive.replace(tzinfo=ET)
        except (ValueError, IndexError):
            continue
        out.append({"summary": summary, "start": start})
    return out


def _start_parts(start, etype):
    if isinstance(start, datetime):
        return start.astimezone(ET).date(), start.astimezone(ET)
    return start, _et_dt(start, CATALOG[etype]["time_et"])


def _first_of_prev_month(d):
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return date(y, m, 1)


def _month_of(d):
    return date(d.year, d.month, 1)


_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                       "august", "september", "october", "november", "december"], 1)}


# BLS release titles as they appear in bls.ics / on bls.gov, e.g. "Consumer Price Index for
# September 2026". The title used to be compared for EXACT equality with "Consumer Price Index",
# so every release whose title carries the reference month ("... for September 2026") was
# silently dropped — CPI, PPI, jobs report and JOLTS never reached the calendar.
BLS_TITLES = [
    ("Employment Situation", ["nfp", "unemployment"]),
    ("Consumer Price Index", ["cpi", "core_cpi"]),
    ("Producer Price Index", ["ppi"]),
    ("Job Openings and Labor Turnover Survey", ["jolts"]),
]
_BLS_REF_RE = re.compile(r"^for\s+([A-Za-z]+)\s+(\d{4})$", re.I)
_bls_seen = {"matched": [], "ignored": []}     # last parse, shown in /api/econ-calendar/debug


def _bls_match(title):
    """-> (event types, reference month or None) for a BLS title, or (None, None)."""
    t = " ".join((title or "").split())
    for prefix, types in BLS_TITLES:
        if t.lower().startswith(prefix.lower()):
            rest = t[len(prefix):].strip(" -:()")
            if rest and not rest.lower().startswith("for "):
                continue          # e.g. "Consumer Price Index Detailed Report" is a different publication
            m = _BLS_REF_RE.match(rest) if rest else None
            ref = None
            if m and m.group(1).lower() in _MONTHS:
                ref = date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
            return types, ref
    return None, None


# bls.gov answers cloud servers (Render) with HTTP 403 — both the ICS file and every HTML page. When that happens the
# release dates below are used instead. They are the official BLS dates (bls.gov/schedule/news_release/{cpi,ppi}.htm
# and the monthly schedule pages), checked 19.10.2026. BLS can move a date (e.g. after a government shutdown), and
# the 2027 calendar was not published yet — REFRESH THIS TABLE when BLS publishes it (see CLAUDE.md section 22).
# (release date, reference month) per type.
BLS_STATIC = {
    "cpi":  [("2026-08-12", "2026-07"), ("2026-09-11", "2026-08"), ("2026-10-14", "2026-09"), ("2026-11-10", "2026-10"), ("2026-12-10", "2026-11")],
    "ppi":  [("2026-08-13", "2026-07"), ("2026-09-10", "2026-08"), ("2026-10-15", "2026-09"), ("2026-11-13", "2026-10"), ("2026-12-15", "2026-11")],
    "nfp":  [("2026-08-07", "2026-07"), ("2026-09-04", "2026-08"), ("2026-10-02", "2026-09"), ("2026-11-06", "2026-10"), ("2026-12-04", "2026-11")],
}
# JOLTS (10:00 ET). 2026-11-03 is stated on bls.gov/jlt ("Next Release"); 2026-09-01 / 2026-09-29 are the published past releases;
# 2026-12-01 comes from a secondary calendar that cites BLS — re-check it on bls.gov/schedule/news_release/jolts.htm.
BLS_STATIC["jolts"] = [("2026-09-01", "2026-07"), ("2026-09-29", "2026-08"), ("2026-11-03", "2026-09"), ("2026-12-01", "2026-10")]
BLS_STATIC["core_cpi"] = BLS_STATIC["cpi"]
BLS_STATIC["unemployment"] = BLS_STATIC["nfp"]


def _bls_static(start, end):
    out = []
    for t, rows in BLS_STATIC.items():
        for d_s, ref_s in rows:
            d = date.fromisoformat(d_s)
            if start <= d <= end:
                ref = date.fromisoformat(ref_s + "-01")
                out.append(dict(type=t, date=d, when=_et_dt(d, CATALOG[t]["time_et"]), period=ref, source="bls-static", estimated=False))
    return out


def schedule_bls(start, end):
    """BLS official release calendar (ICS). The reference period comes from the title
    ("... for September 2026") when present, otherwise it is derived from the release date.
    If bls.gov refuses us (HTTP 403 from cloud IPs), the built-in official dates are used."""
    try:
        out = _schedule_bls_ics(start, end)
        _set_status("bls_fallback", True, None)
        return out
    except Exception as e:  # noqa: BLE001
        _set_status("bls_fallback", True, f"bls.ics unavailable ({str(e)[:80]}) — using the built-in official release dates")
        return _bls_static(start, end)


def _schedule_bls_ics(start, end):
    out, matched, ignored = [], [], []
    for ev in parse_ics(_cached_text("ics_bls", BLS_ICS)):
        name = ev["summary"].strip()
        targets, ref = _bls_match(name)
        if not targets:
            if len(ignored) < 60:
                ignored.append(name)
            continue
        d, when = _start_parts(ev["start"], targets[0])
        matched.append(f"{d.isoformat()} {name}")
        if not (start <= d <= end):
            continue
        if ref is not None:
            period = ref
        else:  # JOLTS reports ~5-6 weeks after its reference month; everything else: previous month
            period = _month_of(d - timedelta(days=45)) if name.startswith("Job Openings") else _first_of_prev_month(d)
        for t in targets:
            out.append(dict(type=t, date=d, when=when, period=period, source="bls", estimated=False))
    _bls_seen["matched"], _bls_seen["ignored"] = matched[-80:], ignored
    if not matched:
        raise RuntimeError("bls.ics parsed but contained no CPI/PPI/jobs releases (format changed?)")
    return out


# bea.gov / census.gov may refuse cloud servers exactly like bls.gov does (HTTP 403). Without a fallback, GDP, PCE, Core PCE and
# Retail Sales then vanish from the calendar completely. These are the OFFICIAL dates, checked on 26.10.2026 against
# bea.gov/news/schedule and census.gov/retail/release_schedule.html. Used ONLY when the live fetch fails or returns nothing usable.
# REFRESH when BEA / Census publish the 2027 schedule (CLAUDE.md section 26).
BEA_STATIC = {
    # (release date, reference period). GDP = advance estimate only (the later estimates are revisions and are not shown).
    "gdp":      [("2026-07-30", "2026-04"), ("2026-10-29", "2026-07")],
    "pce":      [("2026-07-30", "2026-06"), ("2026-08-26", "2026-07"), ("2026-09-30", "2026-08"),
                 ("2026-10-29", "2026-09"), ("2026-11-25", "2026-10"), ("2026-12-23", "2026-11")],
}
BEA_STATIC["core_pce"] = BEA_STATIC["pce"]
CENSUS_STATIC = [("2026-10-15", "2026-09"), ("2026-11-17", "2026-10"), ("2026-12-16", "2026-11"), ("2027-01-15", "2026-12")]


def _static_rows(table, start, end, source):
    out = []
    for t, rows in table.items():
        for d_s, ref_s in rows:
            d = date.fromisoformat(d_s)
            if start <= d <= end:
                out.append(dict(type=t, date=d, when=_et_dt(d, CATALOG[t]["time_et"]),
                                period=date.fromisoformat(ref_s + "-01"), source=source, estimated=False))
    return out


def schedule_bea(start, end):
    """BEA release dates: the live ICS first, the built-in official dates when bea.gov refuses us or the file has no usable rows."""
    try:
        out = _schedule_bea_ics(start, end)
        _set_status("bea_fallback", True, None)
        return out
    except Exception as e:  # noqa: BLE001
        _set_status("bea_fallback", True, f"bea.gov unavailable ({str(e)[:80]}) — using the built-in official release dates")
        return _static_rows(BEA_STATIC, start, end, "bea-static")


def _schedule_bea_ics(start, end):
    """BEA official release calendar (ICS). Titles carry the reference period explicitly."""
    out, seen = [], 0
    for ev in parse_ics(_cached_text("ics_bea", BEA_ICS)):
        name = ev["summary"].strip()
        if name.startswith("GDP (") and "Advance Estimate" in name:   # 2nd/3rd estimates are revisions: skipped
            mq = re.search(r"(\d)(?:st|nd|rd|th) Quarter (\d{4})", name)
            targets = ["gdp"]
            period = date(int(mq.group(2)), (int(mq.group(1)) - 1) * 3 + 1, 1) if mq else None
        elif name.startswith("Personal Income and Outlays,"):
            mm = re.search(r"Outlays,\s*([A-Za-z]+)\s+(\d{4})", name)
            targets = ["pce", "core_pce"]
            period = date(int(mm.group(2)), _MONTHS[mm.group(1).lower()], 1) if mm and mm.group(1).lower() in _MONTHS else None
        else:
            continue
        d, when = _start_parts(ev["start"], targets[0])
        seen += 1
        if start <= d <= end:
            for t in targets:
                out.append(dict(type=t, date=d, when=when, period=period, source="bea", estimated=False))
    if not seen:
        raise RuntimeError("bea ICS parsed but contained no GDP / Personal Income and Outlays releases (format changed?)")
    return out


def schedule_census(start, end):
    """Census retail-sales dates: the live schedule page first, the built-in official dates when census.gov refuses us."""
    try:
        out = _schedule_census_page(start, end)
        _set_status("census_fallback", True, None)
        return out
    except Exception as e:  # noqa: BLE001
        _set_status("census_fallback", True, f"census.gov unavailable ({str(e)[:80]}) — using the built-in official release dates")
        return _static_rows({"retail": CENSUS_STATIC}, start, end, "census-static")


def _schedule_census_page(start, end):
    """Census 'Advance Monthly Retail Trade' schedule page (HTML table: data month -> release date)."""
    html = _cached_text("html_census_retail", CENSUS_RETAIL)
    import html as _html
    text = re.sub(r"<[^>]+>", " ", html)
    text = _html.unescape(text).replace("\xa0", " ")          # &nbsp; &middot; &#160; ... -> plain characters
    text = re.sub(r"\s+", " ", text)
    i = text.find("Advance Monthly Retail Trade Report")
    if i < 0:
        raise RuntimeError("Census retail schedule: section not found (page layout changed?)")
    j = text.find("Monthly Retail Trade Report", i + 40)
    section = text[i:j if j > i else i + 2500]
    out, seen = [], 0
    for mon, yr, mon2, day2, yr2 in re.findall(
            r"([A-Z][a-z]+) (\d{4})\s*[·|\-–:]?\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})", section):
        if mon.lower() not in _MONTHS or mon2.lower() not in _MONTHS:
            continue
        d = date(int(yr2), _MONTHS[mon2.lower()], int(day2))
        seen += 1
        if start <= d <= end:
            out.append(dict(type="retail", date=d, when=_et_dt(d, "08:30"),
                            period=date(int(yr), _MONTHS[mon.lower()], 1), source="census", estimated=False))
    if not seen:
        raise RuntimeError("Census retail schedule: no 'data month -> release date' rows found (page layout changed?)")
    return out


def schedule_fomc(start, end):
    out = []
    for ds, tentative in FOMC_DECISIONS:
        d = date.fromisoformat(ds)
        if start <= d <= end:
            out.append(dict(type="fomc", date=d, when=_et_dt(d, "14:00"), period=None, source="fed", estimated=tentative))
    return out


def schedule_rules(start, end):
    """Rule-based schedule for events with no free official machine-readable calendar.
    Jobless claims (every Thursday 08:30 ET; Wednesday if Thursday is a federal holiday)
    are reliable; ISM / Conference Board / UMich dates are flagged ESTIMATED."""
    out, d = [], start
    while d <= end:
        if d.weekday() == 3:                                   # Thursday
            hol = _us_holidays(d.year)
            rd = d - timedelta(days=1) if d in hol else d
            if start <= rd <= end:
                out.append(dict(type="claims", date=rd, when=_et_dt(rd, "08:30"),
                                period=rd - timedelta(days=(rd.weekday() - 5) % 7), source="rule", estimated=False))
        d += timedelta(days=1)
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        fridays = [date(y, m, k) for k in range(1, 29) if date(y, m, k).weekday() == 4]
        rules = [("ism_mfg", _nth_business_day(y, m, 1)), ("ism_services", _nth_business_day(y, m, 3)),
                 ("consumer_conf", _last_tuesday(y, m)), ("umich", fridays[1]), ("umich", fridays[3])]
        for etype, dd in rules:
            if start <= dd <= end:
                out.append(dict(type=etype, date=dd, when=_et_dt(dd, CATALOG[etype]["time_et"]),
                                period=None, source="rule", estimated=True))
        y, m = (y, m + 1) if m < 12 else (y + 1, 1)
    return out


# which event types each schedule provider is responsible for (used for the DB fallback when it is down)
SCHEDULE_PROVIDERS = [
    ("schedule_bls", schedule_bls, ["nfp", "unemployment", "cpi", "core_cpi", "ppi", "jolts"]),
    ("schedule_bea", schedule_bea, ["gdp", "pce", "core_pce"]),
    ("schedule_census", schedule_census, ["retail"]),
    ("schedule_fomc", schedule_fomc, []),
    ("schedule_rules", schedule_rules, []),
]


# --------------------------------------------------------------------------
# PROVIDER: forecast / previous  (swap this to change the consensus source)
# --------------------------------------------------------------------------
# Forecast provider contract
# --------------------------
# A provider is a zero-argument function returning a list of US events:
#     [{"type": <CATALOG key>, "when": <tz-aware datetime>, "forecast": "0.3%"|None,
#       "previous": "0.4%"|None, "source": "<provider name>"}]
# Use match_event_type(title) to map the vendor's event title to a CATALOG key
# (add vendor-specific regexes to CATALOG[...]["match"]). Format numbers like the
# `fmt` of the event ("0.3%", "175K", "7.20M") so compute_surprise() can compare them.
# Respect the vendor's rate limits/caching inside the function (cache with _cget/_cset).
# Register it:  FORECAST_PROVIDERS["name"] = fn   and set ECON_FORECAST_PROVIDER=name.
FORECAST_PROVIDERS = {}


def match_event_type(title, record_unmatched=False):
    title = (title or "").strip()
    etype = next((t for t, rx in _MATCH_REGEX if rx.match(title)), None)
    if etype is None and record_unmatched and title and title not in _unmatched_titles:
        _unmatched_titles.append(title)
        del _unmatched_titles[:-60]
    return etype


def forecast_enabled():
    return FORECAST_PROVIDER not in ("", "none") and FORECAST_PROVIDER in FORECAST_PROVIDERS


def get_forecasts():
    if FORECAST_PROVIDER in ("", "none"):
        return []                      # official-only mode: nothing to fetch, nothing to report
    fn = FORECAST_PROVIDERS.get(FORECAST_PROVIDER)
    if fn is None:
        _set_status("forecast", False, f"unknown provider '{FORECAST_PROVIDER}'")
        return []
    try:
        items = fn() or []
        _set_status("forecast", True)
        return items
    except Exception as e:  # noqa: BLE001 — a provider failure must never break the calendar
        _set_status("forecast", False, e)
        return []


# --------------------------------------------------------------------------
# PROVIDER: actual / previous  (FRED public CSV — no key, no sign-up)
# --------------------------------------------------------------------------
# Second source for the series BLS itself owns (CPI, PPI, jobs, unemployment, JOLTS): the BLS public API (v1, no key;
# limits: 25 requests/day per IP, so ONE request fetches all mapped series and is cached 20+ minutes).
BLS_API_V1 = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_API_V2 = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
# Optional env var BLS_API_KEY (free registration at data.bls.gov/registrationEngine): v2 = 500 requests/day instead of
# 25 — on Render the IP is shared with other customers, so the keyless quota is often already used up.
# Optional env var FRED_API_KEY (free, fredaccount.stlouisfed.org): the official FRED API (host api.stlouisfed.org),
# usually reachable when the CSV download host (fred.stlouisfed.org) times out; covers ALL series.
FRED_API = "https://api.stlouisfed.org/fred/series/observations"
BLS_FALLBACK_SERIES = {            # FRED series id -> BLS series id (same seasonally adjusted series)
    "CPIAUCSL": "CUSR0000SA0", "CPILFESL": "CUSR0000SA0L1E", "PPIFIS": "WPSFD4",
    "PAYEMS": "CES0000000001", "UNRATE": "LNS14000000", "JTSJOL": "JTS000000000000000JOL",
}
BLS_API_MIN_TTL = 20 * 60
FRED_TIMEOUT = 25                 # FRED's CSV host is often slow, not blocked; results are built in the background, so waiting is harmless
FRED_COOLDOWN = 5 * 60           # after a FRED failure, don't make every request wait for the timeout again
_fred_down_until = 0.0
_bls_api_down_until = 0.0


def _bls_api_load():
    """One request for all mapped series -> fills the cache under ('blsapi', <FRED series id>)."""
    global _bls_api_down_until
    if time.time() < _bls_api_down_until:
        raise RuntimeError("BLS API cooling down after a failure")
    year = date.today().year
    api_key = (os.environ.get("BLS_API_KEY") or "").strip()
    body = {"seriesid": list(BLS_FALLBACK_SERIES.values()), "startyear": str(year - 2), "endyear": str(year)}
    if api_key:
        body["registrationkey"] = api_key
    try:
        r = requests.post(BLS_API_V2 if api_key else BLS_API_V1, json=body, headers=HTTP_HEADERS_ALT, timeout=15)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code} for api.bls.gov")
        js = r.json()
        if js.get("status") != "REQUEST_SUCCEEDED":
            raise RuntimeError("BLS API: " + "; ".join(js.get("message") or ["request not succeeded"])[:140])
        by_bls = {s["seriesID"]: s.get("data", []) for s in js["Results"]["series"]}
    except Exception as e:
        if "threshold" in str(e).lower():              # daily quota used up: it resets at 00:00 UTC — don't hammer it
            now = datetime.now(timezone.utc)
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
            _bls_api_down_until = midnight.timestamp()
        else:
            _bls_api_down_until = time.time() + 3600
        raise
    for fred_id, bls_id in BLS_FALLBACK_SERIES.items():
        rows = {}
        for d in by_bls.get(bls_id, []):
            p = d.get("period", "")
            if not (p.startswith("M") and p != "M13"):
                continue
            try:
                rows[date(int(d["year"]), int(p[1:]), 1)] = float(d["value"])
            except (ValueError, KeyError):
                continue
        if rows:
            _cset(("blsapi", fred_id), sorted(rows.items()))


def bls_api_rows(series, ttl):
    key = ("blsapi", series)
    hit = _cget(key, max(ttl, BLS_API_MIN_TTL))
    if hit is not None:
        return hit
    try:
        _bls_api_load()
    except Exception:
        stale = _cache.get(key)
        if stale:
            return stale[1]
        raise
    got = _cache.get(key)
    if not got:
        raise RuntimeError(f"BLS API returned no data for {series}")
    return got[1]


def _parse_fred_csv(text, series):
    rows = []
    for line in text.splitlines()[1:]:
        parts = line.strip().split(",")
        if len(parts) < 2 or parts[1] in ("", "."):
            continue
        try:
            rows.append((date.fromisoformat(parts[0]), float(parts[1])))
        except ValueError:
            continue
    if not rows:
        raise RuntimeError(f"FRED CSV for {series} is empty")
    return rows


FRED_TXT = "https://fred.stlouisfed.org/data/{series}.txt"


def _parse_fred_txt(text, series):
    """[(date, float)] from FRED's static data file (https://fred.stlouisfed.org/data/<SERIES>.txt): a header, then a
    'DATE VALUE' line, then 'YYYY-MM-DD value' rows. Missing observations ('.') are skipped."""
    rows, started = [], False
    for line in (text or "").splitlines():
        if not started:
            started = bool(re.match(r"^\s*DATE\s+VALUE", line))
            continue
        m = re.match(r"^\s*(\d{4}-\d{2}-\d{2})\s+([-+]?\d[\d,]*\.?\d*)\s*$", line)
        if m:
            rows.append((date.fromisoformat(m.group(1)), float(m.group(2).replace(",", ""))))
    if not rows:
        raise RuntimeError(f"FRED {series}: no data rows in the .txt file")
    return rows


def _fred_api_rows(series, cosd):
    r = requests.get(FRED_API, params={"series_id": series, "api_key": os.environ["FRED_API_KEY"].strip(),
                                       "file_type": "json", "observation_start": cosd},
                     headers=HTTP_HEADERS_ALT, timeout=FRED_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for api.stlouisfed.org")
    rows = []
    for o in r.json().get("observations", []):
        try:
            rows.append((date.fromisoformat(o["date"]), float(o["value"])))
        except (ValueError, KeyError):          # "." = no value yet
            continue
    if not rows:
        raise RuntimeError(f"FRED API returned no data for {series}")
    return rows


def fred_csv(series, ttl):
    """[(date, float)] oldest->newest from FRED's public CSV download (the file behind the
    'Download CSV' button of every FRED chart). `ttl` decides how fresh the copy must be.
    If FRED is slow/unreachable, the BLS-owned series fall back to the BLS public API."""
    global _fred_down_until
    key = ("csv", series)
    hit = _cget(key, ttl)
    if hit is not None:
        return hit
    stored = _series_from_db(series, SERIES_FRESH_S)      # filled by the Actions job; no network call needed
    if stored:
        return _cset(key, stored[0])
    cosd = (date.today() - timedelta(days=900)).isoformat()
    try:
        if time.time() < _fred_down_until:
            raise RuntimeError("FRED unavailable (cooling down)")
        if (os.environ.get("FRED_API_KEY") or "").strip():
            return _cset(key, _fred_api_rows(series, cosd))
        text = _http_text(f"{FRED_CSV}?id={series}&cosd={cosd}", timeout=FRED_TIMEOUT)
    except Exception as fred_err:
        if not str(fred_err).startswith("FRED unavailable"):
            _fred_down_until = time.time() + FRED_COOLDOWN
        if series in BLS_FALLBACK_SERIES:
            try:
                rows = bls_api_rows(series, ttl)
                _set_status("actual_fallback", True, "FRED unavailable — using the BLS public API")
                return rows
            except Exception as e:  # noqa: BLE001
                _set_status("actual_fallback", False, e)
        stale = _cache.get(key)
        if stale:
            return stale[1]
        old = _series_from_db(series, SERIES_STALE_S)
        if old:
            return old[0]
        raise fred_err
    return _cset(key, _parse_fred_csv(text, series))


def _transform(rows, idx, units):
    """Value shown for rows[idx]: level, % change vs the previous row, or absolute change."""
    v = rows[idx][1]
    if units == "lin":
        return v
    if idx < 1:
        return None
    prev = rows[idx - 1][1]
    if units == "pch":
        return (v / prev - 1.0) * 100.0 if prev else None
    if units == "chg":
        return v - prev
    return None


def fred_actual(etype, d, period, ttl=FRED_PENDING_TTL):
    """Actual (+ revised previous) of the release whose reference period is `period`, or None
    if FRED does not contain that period yet (= release not ingested yet)."""
    spec = CATALOG[etype]["fred"]
    if not spec:
        return None
    rows = fred_csv(spec["series"], ttl)
    pos = {r[0]: i for i, r in enumerate(rows)}
    if spec.get("kind") == "daily_level":          # FOMC: the new target range applies from the NEXT day
        nxt, cur = d + timedelta(days=1), d
        if nxt not in pos or cur not in pos:
            return None
        return dict(actual=format_value(rows[pos[nxt]][1], spec["fmt"]), previous=format_value(rows[pos[cur]][1], spec["fmt"]))
    if period is None or period not in pos:
        return None
    idx = pos[period]
    a = _transform(rows, idx, spec.get("units", "lin"))
    if a is None:
        return None
    p = _transform(rows, idx - 1, spec.get("units", "lin")) if idx >= 1 else None
    return dict(actual=format_value(a, spec["fmt"]), previous=format_value(p, spec["fmt"]))


def fred_latest(etype, ttl=FRED_LATEST_TTL):
    """Latest published reading = the 'previous' shown for an upcoming release."""
    spec = CATALOG[etype]["fred"]
    if not spec:
        return None
    rows = fred_csv(spec["series"], ttl)
    if spec.get("kind") == "daily_level":
        return format_value(rows[-1][1], spec["fmt"])
    v = _transform(rows, len(rows) - 1, spec.get("units", "lin"))
    return format_value(v, spec["fmt"])


# --------------------------------------------------------------------------
# persistence (Postgres) — forecasts vanish from the free feed after the week,
# actuals are cheap to keep: store them so past events stay comparable.
# --------------------------------------------------------------------------
def ensure_table(get_conn):
    global _table_ready
    if _table_ready or not get_conn:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS econ_events (
            event_key TEXT PRIMARY KEY, etype TEXT NOT NULL, event_date DATE NOT NULL,
            forecast TEXT, previous TEXT, actual TEXT, updated_at TIMESTAMPTZ DEFAULT NOW())""")
        cur.execute("ALTER TABLE econ_events ADD COLUMN IF NOT EXISTS period DATE")
        conn.commit()
        cur.close()
        _table_ready = True
    finally:
        conn.close()


def db_load(get_conn, start, end):
    if not get_conn:
        return {}
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT etype, event_date, forecast, previous, actual, period FROM econ_events "
                    "WHERE event_date BETWEEN %s AND %s", (start, end))
        return {(r[0], r[1]): dict(forecast=r[2], previous=r[3], actual=r[4], period=r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def db_save(get_conn, occs, saved):
    """Upsert only what is new/changed (the calendar is rebuilt every ~90s — don't hammer Neon)."""
    rows = []
    for o in occs:
        old = saved.get((o["type"], o["date"]))
        new = (o.get("forecast"), o.get("previous"), o.get("actual"), o.get("period"))
        if old and (old.get("forecast"), old.get("previous"), old.get("actual"), old.get("period")) == new:
            continue
        rows.append((f"{o['type']}-{o['date'].isoformat()}", o["type"], o["date"], *new))
    if not rows or not get_conn:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        for r in rows:  # forecast: newest wins (revisions); actual: first print wins
            cur.execute("""INSERT INTO econ_events (event_key, etype, event_date, forecast, previous, actual, period)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_key) DO UPDATE SET
                  forecast = COALESCE(EXCLUDED.forecast, econ_events.forecast),
                  previous = COALESCE(econ_events.previous, EXCLUDED.previous),
                  actual   = COALESCE(econ_events.actual, EXCLUDED.actual),
                  period   = COALESCE(EXCLUDED.period, econ_events.period),
                  updated_at = NOW()""", r)
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# PROCESSING — merge providers into one uniform event list
# --------------------------------------------------------------------------
def _merge(start, end, now_utc):
    """Returns (occurrences, types whose official schedule could not be fetched this round)."""
    occ = {}  # (type, date_et) -> occurrence dict
    failed_types = set()

    def put(etype, d, **kw):
        cur = occ.setdefault((etype, d), dict(type=etype, date=d, estimated=False, sources={}))
        for k, v in kw.items():
            if v is not None:
                cur[k] = v
        return cur

    # 1) schedule providers — each guarded independently
    def _run(item):
        name, fn, covers = item
        try:
            return name, covers, fn(start, end), None
        except Exception as e:  # noqa: BLE001
            return name, covers, None, e

    with ThreadPoolExecutor(len(SCHEDULE_PROVIDERS)) as ex:      # network fetches in parallel
        results = list(ex.map(_run, SCHEDULE_PROVIDERS))
    for name, covers, items, err in results:
        if err is not None:
            _set_status(name, False, err)
            failed_types.update(covers)
            continue
        for sc in items:
            o = put(sc["type"], sc["date"], when=sc["when"], period=sc.get("period"),
                    estimated=bool(sc["estimated"]))
            o["sources"]["schedule"] = sc["source"]
        _set_status(name, True)

    # 2) forecast provider (optional) — exact times, forecast, previous; confirms/creates dates
    for f in get_forecasts():
        d = f["when"].astimezone(ET).date()
        if not (start <= d <= end):
            continue
        # a provider-confirmed date replaces a rule-based estimate of the same event within 4 days
        for (t, od), o in list(occ.items()):
            if t == f["type"] and o["estimated"] and od != d and abs((od - d).days) <= 4:
                del occ[(t, od)]
        o = put(f["type"], d, when=f["when"], forecast=f["forecast"], previous=f["previous"])
        o["estimated"] = False
        o["sources"]["forecast"] = f.get("source") or FORECAST_PROVIDER
        o["sources"].setdefault("schedule", f.get("source") or FORECAST_PROVIDER)
    return occ, failed_types


STALE_MAX = 30 * 60      # a cached result older than RESULT_TTL but younger than this is served instantly
SNAPSHOT_MAX_AGE = 24 * 3600   # a stored copy older than this is not served (rebuilt synchronously instead)
_refreshing = set()      # while a background refresh runs
_snap_ready = False
_conn_factory = None     # set by build_calendar/build_month — lets the series loader reach the database


# --------------------------------------------------------------------------
# SNAPSHOTS — the finished result is also kept in Postgres. The server's memory is empty after every
# Render sleep/restart; without this, the first visitor waited for three government calendars and the
# FRED downloads again. Now the stored copy is served at once and refreshed in the background, so the
# data is built only when something may have changed.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# SERIES STORE — the FRED/BLS series (the numbers behind "actual"/"previous") are kept in Postgres too. FRED's CSV
# host does not answer from Render (timeouts even at 25 s), so a GitHub Actions job (econ_refresh.py, workflow
# econ-refresh.yml) downloads them and stores them here; the server reads them from the database.
# --------------------------------------------------------------------------
SERIES_FRESH_S = 36 * 3600          # a stored series younger than this is used without any network call
SERIES_STALE_S = 14 * 24 * 3600     # when the network fails, a stored series up to this old still beats nothing


def series_to_db(get_conn, series, rows):
    snap_save(get_conn, f"series-{series}", [[d.isoformat(), v] for d, v in rows])


def _series_from_db(series, max_age):
    got = snap_load(_conn_factory, f"series-{series}", max_age=max_age)
    if not got:
        return None
    try:
        return [(date.fromisoformat(d), float(v)) for d, v in got[0]], got[1]
    except Exception:  # noqa: BLE001
        return None


def _snap_table(get_conn):
    global _snap_ready
    if _snap_ready or not get_conn:
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS econ_snapshots (
            skey TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW())""")
        conn.commit()
        cur.close()
        _snap_ready = True
    finally:
        conn.close()


def snap_load(get_conn, skey, max_age=None):
    """-> (stored object, age in seconds) or None."""
    if not get_conn:
        return None
    try:
        _snap_table(get_conn)
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("SELECT payload, EXTRACT(EPOCH FROM (NOW() - updated_at)) FROM econ_snapshots WHERE skey = %s", (skey,))
            row = cur.fetchone()
            cur.close()
        finally:
            conn.close()
        if not row or row[1] is None or float(row[1]) > (max_age or SNAPSHOT_MAX_AGE):
            return None
        return json.loads(row[0]), float(row[1])
    except Exception as e:  # noqa: BLE001 — a storage hiccup must never break the calendar
        _set_status("snapshot", False, e)
        return None


def snap_save(get_conn, skey, res):
    if not get_conn or not res or (isinstance(res, dict) and "events" in res and not res.get("events")):
        return                                   # never replace a good stored copy with an empty result
    try:
        _snap_table(get_conn)
        conn = get_conn()
        try:
            cur = conn.cursor()
            cur.execute("""INSERT INTO econ_snapshots (skey, payload, updated_at) VALUES (%s, %s, NOW())
                           ON CONFLICT (skey) DO UPDATE SET payload = EXCLUDED.payload, updated_at = NOW()""",
                        (skey, json.dumps(res, ensure_ascii=False)))
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        _set_status("snapshot", False, e)


def _refresh_bg(key, get_conn, builder, skey):
    """Rebuild in a background thread (one at a time per key); keep the memory copy and the stored copy."""
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def work():
        try:
            res = builder()
            with _lock:
                _cset(key, res)
            snap_save(get_conn, skey, res)
        except Exception as e:  # noqa: BLE001
            _set_status("build", False, e)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=work, daemon=True).start()


def _refresh_async(key, get_conn, days, limit):
    _refresh_bg(key, get_conn, lambda: _build(get_conn, days, limit), f"list-{days}-{limit}")


def _serve_snapshot(key, snap):
    """Put a stored copy into memory as 'stale but servable' (a background refresh follows) and return it."""
    payload, age = snap
    with _lock:
        _cache[key] = (time.time() - min(age, STALE_MAX - 1), payload)
    return payload


def _remember_conn(get_conn):
    global _conn_factory
    if get_conn:
        _conn_factory = get_conn


def build_calendar(get_conn=None, days=21, limit=14):
    """Public entry point. Returns {"events": [...], "meta": {...}} — the ONLY
    structure the frontend depends on. Stale-while-revalidate: a result younger than
    RESULT_TTL is returned as is; one younger than STALE_MAX is returned immediately
    while a background thread rebuilds it; only a cold start builds synchronously."""
    _remember_conn(get_conn)
    key = ("result", days, limit)
    hit = _cache.get(key)
    if hit:
        age = time.time() - hit[0]
        if age < RESULT_TTL:
            return hit[1]
        if age < STALE_MAX:
            _refresh_async(key, get_conn, days, limit)
            return hit[1]
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < RESULT_TTL:
            return hit[1]
    skey = f"list-{days}-{limit}"
    snap = snap_load(get_conn, skey)          # cold memory (server just woke up): serve the stored copy at once
    if snap:
        payload = _serve_snapshot(key, snap)
        _refresh_async(key, get_conn, days, limit)
        return payload
    res = _build(get_conn, days, limit)
    snap_save(get_conn, skey, res)
    with _lock:
        return _cset(key, res)


def warm_up(get_conn=None):
    """Call once at server start: builds the calendar in a background thread so the
    first visitor does not wait for the government calendars + FRED downloads."""
    threading.Thread(target=lambda: build_calendar(get_conn), daemon=True).start()


def _build(get_conn, days, limit):
    """Upcoming list: from 2 days ago to `days` ahead, trimmed to the most relevant `limit`."""
    today = datetime.now(UTC).astimezone(ET).date()
    return _build_range(get_conn, today - timedelta(days=2), today + timedelta(days=days), ACTUAL_LOOKBACK_DAYS, limit)


def _build_range(get_conn, start, end, lookback_days, limit=None):
    now_utc = datetime.now(UTC)
    today = now_utc.astimezone(ET).date()

    try:
        ensure_table(get_conn)
        saved = db_load(get_conn, start, end)
    except Exception as e:  # noqa: BLE001 — DB hiccup (Neon suspended etc.) must not kill the calendar
        saved = {}
        _set_status("storage", False, e)

    occ, failed_types = _merge(start, end, now_utc)

    # 3a) an official schedule source is down -> keep showing what we saved earlier for ITS event types only
    #     (never for types whose source answered: a rescheduled release must not leave a stale duplicate)
    for (t, d), sv in saved.items():
        if t in failed_types and (t, d) not in occ and d >= start:
            occ[(t, d)] = dict(type=t, date=d, estimated=False, when=_et_dt(d, CATALOG[t]["time_et"]),
                               period=sv.get("period"), sources={"schedule": "saved"})

    # 3) saved values fill gaps (forecast that already rolled off the free feed, stored actuals)
    for (t, d), sv in saved.items():
        if (t, d) in occ:
            for k in ("forecast", "previous", "actual"):
                if sv.get(k) and not occ[(t, d)].get(k):
                    occ[(t, d)][k] = sv[k]

    # 4) actuals for events released in the last few days; previous for upcoming ones
    def enrich(o):
        c = CATALOG[o["type"]]
        if not c["fred"]:
            return
        when = o["when"].astimezone(UTC)
        try:
            if when <= now_utc <= when + timedelta(days=lookback_days) and not o.get("actual"):
                ck = ("actual", o["type"], o["date"])
                res = _cget(ck, FRED_PENDING_TTL)
                if res is None:
                    res = _cset(ck, fred_actual(o["type"], o["date"], o.get("period")) or {})
                if res.get("actual"):
                    o["actual"] = res["actual"]
                    if res.get("previous"):
                        o["previous"] = res["previous"]   # vintage-consistent revised previous
                    o["sources"]["actual"] = "fred"
            elif when > now_utc and not o.get("previous"):
                latest = fred_latest(o["type"])
                if latest:
                    o["previous"] = latest
                    o["sources"]["previous"] = "fred"
            _set_status("actual", True)
        except Exception as e:  # noqa: BLE001
            _set_status("actual", False, e)

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(enrich, list(occ.values())))

    try:
        db_save(get_conn, list(occ.values()), saved)
    except Exception as e:  # noqa: BLE001
        _set_status("storage", False, e)

    events = [_serialize(o, now_utc) for o in occ.values()]
    events.sort(key=lambda e: e["datetime_utc"])
    return {"events": _select(events, limit, now_utc) if limit else events, "meta": _meta(now_utc)}


MONTH_LOOKBACK_DAYS = 62      # a month view must show the actual of events released earlier this month


def build_month(get_conn=None, year=None, month=None):
    """Every supported event of one calendar month (past ones with their actual, upcoming
    ones with date/time) for the monthly calendar view. Same uniform event structure."""
    _remember_conn(get_conn)
    today = datetime.now(UTC).astimezone(ET).date()
    year, month = year or today.year, month or today.month
    if not (1 <= month <= 12 and 2000 <= year <= 2100):
        raise ValueError("bad month")
    key = ("month", year, month)
    skey = f"month-{year:04d}-{month:02d}"
    start = date(year, month, 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)

    def builder():
        res = _build_range(get_conn, start, end, MONTH_LOOKBACK_DAYS)
        res["month"] = f"{year:04d}-{month:02d}"
        return res

    hit = _cache.get(key)
    if hit:
        age = time.time() - hit[0]
        if age < RESULT_TTL:
            return hit[1]
        if age < STALE_MAX:
            _refresh_bg(key, get_conn, builder, skey)
            return hit[1]
    snap = snap_load(get_conn, skey)
    if snap:
        payload = _serve_snapshot(key, snap)
        _refresh_bg(key, get_conn, builder, skey)
        return payload
    res = builder()
    snap_save(get_conn, skey, res)
    with _lock:
        return _cset(key, res)


def _serialize(o, now_utc):
    c = CATALOG[o["type"]]
    when = o["when"].astimezone(UTC)
    et = o["when"].astimezone(ET)
    actual = o.get("actual")
    if actual:
        status = "released"
    elif now_utc >= when and not c["fred"]:
        status = "released_no_data"      # no free official source for this actual (ISM / CB / UMich)
    elif now_utc >= when + timedelta(hours=36):
        status = "released_no_data"
    elif now_utc >= when:
        status = "pending"
    else:
        status = "upcoming"
    return {
        "id": f"{o['type']}-{o['date'].isoformat()}",
        "type": o["type"],
        "name_en": c["name_en"], "name_he": c["name_he"],
        "impact": c["impact"],
        "datetime_utc": when.isoformat().replace("+00:00", "Z"),
        "date_et": et.date().isoformat(),
        "time_et": et.strftime("%H:%M"),
        "estimated": bool(o.get("estimated")),
        "forecast": o.get("forecast"), "previous": o.get("previous"), "actual": actual,
        "has_actual_source": bool(c["fred"]),
        "status": status,
        "surprise": compute_surprise(actual, o.get("forecast")) if actual else None,
        "what_en": c["what_en"], "what_he": c["what_he"],
        "affects": [{"key": k, "label_en": AFFECT_LABELS[k][0], "label_he": AFFECT_LABELS[k][1]} for k in c["affects"]],
        "sources": o.get("sources", {}),
    }


def _select(events, limit, now_utc):
    """Recently released (last 36h) + upcoming; all high-impact first, then fill with medium."""
    keep = [e for e in events
            if datetime.fromisoformat(e["datetime_utc"].replace("Z", "+00:00")) >= now_utc - timedelta(hours=36)]
    chosen = [e for e in keep if e["impact"] == "high"][:limit]
    for e in keep:
        if len(chosen) >= limit:
            break
        if e["impact"] != "high":
            chosen.append(e)
    chosen.sort(key=lambda e: e["datetime_utc"])
    return chosen


_health_cache = {"ts": 0.0, "val": {}}


def series_health():
    """Which series econ_refresh.py stored: last period, number of points and how long ago. Shown in meta.series of
    /api/econ-calendar so a missing or stale series (the usual reason for an empty Actual/Previous) is visible at a glance."""
    now = time.time()
    if now - _health_cache["ts"] < 300 and _health_cache["val"]:
        return _health_cache["val"]
    out = {}
    try:
        if _conn_factory:
            _snap_table(_conn_factory)
            conn = _conn_factory()
            try:
                cur = conn.cursor()
                cur.execute("SELECT skey, payload, EXTRACT(EPOCH FROM (NOW() - updated_at)) FROM econ_snapshots WHERE skey LIKE 'series-%'")
                for skey, payload, age in cur.fetchall():
                    try:
                        rows = json.loads(payload)
                    except Exception:  # noqa: BLE001
                        rows = []
                    out[skey[len("series-"):]] = {"last_period": rows[-1][0] if rows else None, "points": len(rows),
                                                  "stored_hours_ago": round(float(age) / 3600.0, 1)}
                cur.close()
            finally:
                conn.close()
        for c in CATALOG.values():
            sid = (c.get("fred") or {}).get("series")
            if sid and sid not in out:
                out[sid] = {"last_period": None, "points": 0, "stored_hours_ago": None}
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)[:80]
    _health_cache.update(ts=now, val=out)
    return out


def _meta(now_utc):
    return {
        "series": series_health(),
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "forecast_provider": FORECAST_PROVIDER if forecast_enabled() else "none",
        "providers": {k: {"ok": v["ok"], "error": v["error"]} for k, v in _status.items()},
        "supported_events": list(CATALOG.keys()),
    }


def debug_info():
    return {"unmatched_provider_titles": list(_unmatched_titles),
            "bls": {"matched_titles": _bls_seen["matched"], "ignored_titles": _bls_seen["ignored"]},
            "meta": _meta(datetime.now(UTC))}
