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
    r = requests.get(url, headers=HTTP_HEADERS, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url.split('/')[2]}")
    return r.text


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


def schedule_bls(start, end):
    """BLS official release calendar (ICS). The reference period of each release is
    derived from its date — the same convention BLS uses."""
    out = []
    for ev in parse_ics(_cached_text("ics_bls", BLS_ICS)):
        name = ev["summary"].strip()
        targets = {"Employment Situation": ["nfp", "unemployment"],
                   "Consumer Price Index": ["cpi", "core_cpi"],
                   "Producer Price Index": ["ppi"],
                   "Job Openings and Labor Turnover Survey": ["jolts"]}.get(name)
        if not targets:
            continue
        d, when = _start_parts(ev["start"], targets[0])
        if not (start <= d <= end):
            continue
        # JOLTS reports ~5-6 weeks after its reference month; everything else: previous month
        period = _month_of(d - timedelta(days=45)) if name.startswith("Job Openings") else _first_of_prev_month(d)
        for t in targets:
            out.append(dict(type=t, date=d, when=when, period=period, source="bls", estimated=False))
    return out


def schedule_bea(start, end):
    """BEA official release calendar (ICS). Titles carry the reference period explicitly."""
    out = []
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
        if start <= d <= end:
            for t in targets:
                out.append(dict(type=t, date=d, when=when, period=period, source="bea", estimated=False))
    return out


def schedule_census(start, end):
    """Census 'Advance Monthly Retail Trade' schedule page (HTML table: data month -> release date)."""
    html = _cached_text("html_census_retail", CENSUS_RETAIL)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    i = text.find("Advance Monthly Retail Trade Report")
    if i < 0:
        raise RuntimeError("Census retail schedule: section not found (page layout changed?)")
    j = text.find("Monthly Retail Trade Report", i + 40)
    section = text[i:j if j > i else i + 2500]
    out = []
    for mon, yr, mon2, day2, yr2 in re.findall(
            r"([A-Z][a-z]+) (\d{4})\s*[·|\-–:]?\s*([A-Z][a-z]+) (\d{1,2}), (\d{4})", section):
        if mon.lower() not in _MONTHS or mon2.lower() not in _MONTHS:
            continue
        d = date(int(yr2), _MONTHS[mon2.lower()], int(day2))
        if start <= d <= end:
            out.append(dict(type="retail", date=d, when=_et_dt(d, "08:30"),
                            period=date(int(yr), _MONTHS[mon.lower()], 1), source="census", estimated=False))
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
def fred_csv(series, ttl):
    """[(date, float)] oldest->newest from FRED's public CSV download (the file behind the
    'Download CSV' button of every FRED chart). `ttl` decides how fresh the copy must be."""
    key = ("csv", series)
    hit = _cget(key, ttl)
    if hit is not None:
        return hit
    cosd = (date.today() - timedelta(days=900)).isoformat()
    try:
        text = _http_text(f"{FRED_CSV}?id={series}&cosd={cosd}")
    except Exception:
        stale = _cache.get(key)
        if stale:
            return stale[1]
        raise
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
    return _cset(key, rows)


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
_refreshing = set()      # while a background refresh runs


def _refresh_async(key, get_conn, days, limit):
    with _lock:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def work():
        try:
            res = _build(get_conn, days, limit)
            with _lock:
                _cset(key, res)
        except Exception as e:  # noqa: BLE001
            _set_status("build", False, e)
        finally:
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=work, daemon=True).start()


def build_calendar(get_conn=None, days=21, limit=14):
    """Public entry point. Returns {"events": [...], "meta": {...}} — the ONLY
    structure the frontend depends on. Stale-while-revalidate: a result younger than
    RESULT_TTL is returned as is; one younger than STALE_MAX is returned immediately
    while a background thread rebuilds it; only a cold start builds synchronously."""
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
    res = _build(get_conn, days, limit)
    with _lock:
        return _cset(key, res)


def warm_up(get_conn=None):
    """Call once at server start: builds the calendar in a background thread so the
    first visitor does not wait for the government calendars + FRED downloads."""
    threading.Thread(target=lambda: build_calendar(get_conn), daemon=True).start()


def _build(get_conn, days, limit):
    now_utc = datetime.now(UTC)
    today = now_utc.astimezone(ET).date()
    start, end = today - timedelta(days=2), today + timedelta(days=days)

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
        if t in failed_types and (t, d) not in occ and d >= today - timedelta(days=2):
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
            if when <= now_utc <= when + timedelta(days=ACTUAL_LOOKBACK_DAYS) and not o.get("actual"):
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
    return {"events": _select(events, limit, now_utc), "meta": _meta(now_utc)}


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


def _meta(now_utc):
    return {
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "forecast_provider": FORECAST_PROVIDER if forecast_enabled() else "none",
        "providers": {k: {"ok": v["ok"], "error": v["error"]} for k, v in _status.items()},
        "supported_events": list(CATALOG.keys()),
    }


def debug_info():
    return {"unmatched_provider_titles": list(_unmatched_titles), "meta": _meta(datetime.now(UTC))}
