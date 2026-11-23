"""Visitor log (v70): what people do on the site, as a table in the database that the owner reads in Neon. No page, no password.

Owner, 23.11.2026: "I want to know about everyone who enters the site what they did and looked at"; "can't it be a table in Neon?".

How it works:
  - the browser (index.html, "visitor log") keeps the steps of the visit and sends the WHOLE visit so far, again and again
    (POST /api/track). Only people who allowed it in the banner; a random id, no name, no email, no IP address.
  - here it waits in memory, the newest copy of each visit. The database gets ONE ROW PER VISIT in the table `visits`, in plain
    Hebrew, written only in the first minutes of an hour (and of a half hour on working days), when the news job has the database
    awake anyway, only if somebody visited, and once more when the server goes down (every upload to GitHub restarts it). Nothing in the site reads it.
  - moving to a domain: nothing here changes (only ALLOWED_ORIGINS in Render, which the whole site needs anyway).

Numbers (docs/HISTORY.md 27.3 v70): 20 visits a day = ~40 KB; 500 = ~1 MB; kept 90 days, at most MAX_ROWS rows (~20 MB
typical, ~120 MB at the very worst). Opening the table in Neon wakes the database for ~5 minutes = ~0.02 CU-hours.
"""
import json
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

KEEP_DAYS = 90
MAX_ROWS = 10_000
MAX_EVENTS = 200               # steps kept per visit
MAX_STEPS_TEXT = 6_000         # characters of the steps column
MAX_BODY = 40_000              # a longer message is not from our page
MAX_WAITING = 1_000            # visits in memory between two writes
MAX_NEW_PER_DAY = 2_000        # new visits accepted in one day (UTC): nobody can fill the database from outside
MAX_NEW_PER_IP_HOUR = 30       # (the address is only in memory, for this count; it is never stored)
FLUSH_MINUTES = range(1, 9)    # the news job starts at hh:00 and wakes the database; it sleeps ~5 minutes after the last query
HALF_MINUTES = range(31, 39)   # ...and at hh:30 on working days, 06:00-20:00 New York (scheduler.py: news, quick, insider)
IL, ET = ZoneInfo("Asia/Jerusalem"), ZoneInfo("America/New_York")

# the steps the site sends (index.html, track()); anything else is dropped
EVENTS = {"screen", "view_stock", "view_macro", "search_stock", "search_none", "watchlist_add", "compare_stock", "trade_saved",
          "dd_tab", "digest", "alert_saved", "signin", "install", "share", "section", "tour", "lang", "error",
          "watchlist_remove", "news_search", "tap", "pick", "link", "scroll", "chart"}
DEVICES = {"phone", "tablet", "desktop"}
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_BOT_RE = re.compile(r"bot|crawl|spider|slurp|lighthouse|headless|facebookexternalhit|whatsapp|preview|python|curl", re.I)
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮⁦-⁩\ud800-\udfff]")


def _text(v, limit: int) -> str:
    return _CTRL_RE.sub("", v)[:limit].strip() if isinstance(v, str) else ""


def _int(v, lo: int, hi: int) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 1e15:
        return lo
    return max(lo, min(hi, int(v)))


def is_bot(user_agent: str) -> bool:
    return not user_agent or bool(_BOT_RE.search(user_agent.replace("CUBOT", "")))


def browser(ua: str) -> str:
    """"Chrome / Windows": the kind only, no versions (never enough to recognize a person)."""
    def first(names):
        return next((name for sign, name in names if sign in (ua or "")), "")
    kind = first((("SamsungBrowser", "Samsung"), ("Edg", "Edge"), ("OPR/", "Opera"), ("FBAN", "Facebook"), ("FBAV", "Facebook"),
                  ("Instagram", "Instagram"), ("Firefox", "Firefox"), ("FxiOS", "Firefox"), ("CriOS", "Chrome"),
                  ("Chrome/", "Chrome"), ("Safari/", "Safari")))
    system = first((("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Windows", "Windows"), ("Macintosh", "Mac"),
                    ("CrOS", "ChromeOS"), ("Linux", "Linux")))
    return " / ".join(x for x in (kind, system) if x)


def parse(body: bytes, now: datetime = None) -> dict | None:
    """The message of one visit -> a clean dict, {"forget": id} for a person who took the permission back, or None."""
    if not body or len(body) > MAX_BODY or body.count(b"[") + body.count(b"{") > 2 * MAX_EVENTS:    # (no deep nesting either)
        return None
    try:
        d = json.loads(body.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(d, dict):
        return None
    forget = d.get("forget")
    if isinstance(forget, str) and _UUID_RE.match(forget):
        return {"forget": forget}
    if not all(isinstance(d.get(k), str) and _UUID_RE.match(d[k]) for k in ("v", "p")):
        return None
    if not isinstance(d.get("ev"), list):
        return None
    now = now or datetime.now(timezone.utc)
    ago = _int(d.get("ago"), 0, 12 * 3600)        # seconds since the visit began, by the visitor's own clock differences
    events = []
    for e in d["ev"][:MAX_EVENTS]:
        if not (isinstance(e, list) and 2 <= len(e) <= 3 and isinstance(e[1], str) and e[1] in EVENTS):
            continue
        events.append([_int(e[0], 0, 12 * 3600), e[1], _text(e[2], 80) if len(e) == 3 else ""])
    if not events:
        return None
    return {"v": d["v"], "p": d["p"], "started": now - timedelta(seconds=ago), "seconds": _int(d.get("w"), 0, 12 * 3600),
            "n": _int(d.get("n"), 1, 100_000), "src": _text(d.get("src"), 60), "land": _text(d.get("land"), 60),
            "dev": d.get("dev") if d.get("dev") in DEVICES else "", "lang": d.get("lang") if d.get("lang") in ("he", "en") else "",
            "reg": 1 if d.get("reg") else 0, "pwa": 1 if d.get("pwa") else 0, "tz": _text(d.get("tz"), 40), "ld": _int(d.get("ld"), 0, 600), "ev": events}


class Log:
    """The visits waiting in memory (one server process)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.waiting: dict = {}        # visit id -> the newest copy
        self.changed: set = set()      # visit ids not written yet
        self.forget: set = set()       # visitor ids to delete
        self.day, self.new_today = None, 0
        self.ip_hour, self.ip_new = None, Counter()
        self.flushed_at = 0.0
        self.written, self.last_write = 0, None      # since the server started
        self.failed = 0                # writes that failed in a row (/api/data-health -> live.visits)

    def add(self, visit: dict | None, country: str = "", ip: str = "", now: datetime = None, ua: str = "") -> bool:
        if not visit:
            return False
        now = now or datetime.now(timezone.utc)
        with self.lock:
            if "forget" in visit:
                pid = visit["forget"]
                for k in [k for k, x in self.waiting.items() if x["p"] == pid]:
                    self.waiting.pop(k, None)
                    self.changed.discard(k)
                if len(self.forget) < 50:
                    self.forget.add(pid)
                return True
            visit["browser"] = browser(ua)
            old = self.waiting.get(visit["v"])
            if old is not None:
                # a late copy of the same visit never replaces a longer one
                if old["p"] != visit["p"] or (len(visit["ev"]), visit["seconds"]) < (len(old["ev"]), old["seconds"]):
                    return False
                visit["started"], visit["country"] = old["started"], old["country"]
                if visit == old:
                    return False
            else:
                if self.day != now.date():
                    self.day, self.new_today = now.date(), 0
                if self.ip_hour != (now.date(), now.hour):
                    self.ip_hour, self.ip_new = (now.date(), now.hour), Counter()
                if (self.new_today >= MAX_NEW_PER_DAY or len(self.waiting) >= MAX_WAITING
                        or self.ip_new[ip] >= MAX_NEW_PER_IP_HOUR):
                    return False
                self.new_today += 1
                self.ip_new[ip] += 1
                visit["country"] = country if re.match(r"^[A-Z]{2}$", country or "") else ""
            self.waiting[visit["v"]] = visit
            self.changed.add(visit["v"])
            return True

    def due(self, now: datetime = None) -> bool:
        now = now or datetime.now(timezone.utc)
        et = now.astimezone(ET)
        awake = now.minute in FLUSH_MINUTES or (now.minute in HALF_MINUTES and et.weekday() < 5 and 6 <= et.hour < 20)
        return bool(self.changed or self.forget) and awake and time.time() - self.flushed_at > 1200

    def state(self) -> dict:
        """For /api/data-health: numbers only."""
        return {"status": "warn" if self.failed >= 2 else "ok", "failed_writes": self.failed, "waiting": len(self.changed),
                "written": self.written, "last_write": self.last_write}

    def flush(self, connect) -> int:
        """Writes what changed. connect() -> a database connection. A failed write keeps everything for the next time."""
        with self.lock:
            ids, forget = set(self.changed), set(self.forget)
            rows = [dict(self.waiting[i]) for i in ids if i in self.waiting]
        self.flushed_at = time.time()
        self.failed += 1               # back to 0 below, once it is written
        if not rows and not forget:
            self.failed = 0
            return 0
        conn = connect()
        try:
            cur = conn.cursor()
            ensure_tables(cur)
            for pid in forget:
                cur.execute("DELETE FROM visits WHERE visitor_id = %s", (pid,))
            for r in rows:
                save(cur, r)
            prune(cur)
            conn.commit()
        finally:
            conn.close()
        self.failed = 0
        self.written += len(rows)
        self.last_write = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.lock:
            for r in rows:
                if self.waiting.get(r["v"]) == r:          # changed again meanwhile = stays for the next write
                    self.changed.discard(r["v"])
            self.forget -= forget
            old = datetime.now(timezone.utc) - timedelta(hours=2)
            for k in [k for k, x in self.waiting.items() if k not in self.changed and x["started"] < old]:
                del self.waiting[k]
        return len(rows)


LOG = Log()


# ------------------------------------------------------------------ the row, in words the owner can read in Neon
SCREENS_HE = {"home": "דף הבית", "market": "השוק: סקירה", "market/stocks": "השוק: מניות", "market/calendar": "השוק: יומן דוחות",
              "market/events": "השוק: אירועים כלכליים", "performance": "ביצועים: תיק דמו", "performance/stats": "ביצועים: סטטיסטיקה",
              "portfolio": "התיק שלי", "stock": "ניתוח מניה (בלי מניה)"}
TABS_HE = {"tech": "מבנה טכני", "fund": "פונדמנטלס", "own": "מי קונה ומוכר", "news": "חדשות"}
SECTIONS_HE = {"today": "היום בשוק", "scans": "סריקות", "news": "חדשות", "key": "אירועים מרכזיים", "movers": "הכי זזות",
               "chart": "גרף המדדים", "breadth": "רוחב השוק", "insider": "עסקאות בעלי עניין", "footer": "תחתית העמוד"}
EVENTS_HE = {"view_stock": "פתח סריקה של", "view_macro": "פתח ידיעה בנושא", "search_stock": "חיפש", "search_none": "חיפש ולא מצא",
             "watchlist_add": "הוסיף למעקב", "compare_stock": "השווה", "trade_saved": "רשם עסקה", "dd_tab": "לשונית",
             "digest": "פתח סיכום יומי", "alert_saved": "הגדיר התראה על", "signin": "התחבר",
             "install": "התקין את האפליקציה", "share": "שיתף", "section": "ראה", "tour": "סיור באתר", "lang": "החליף שפה",
             "error": "תקלה", "watchlist_remove": "הסיר ממעקב", "news_search": "חיפש בחדשות", "tap": "לחץ על",
             "pick": "בחר", "link": "יצא לקישור", "scroll": "גלל", "chart": "השתמש בגרף TradingView"}
# what the buttons are called in words (index.html sends the name of the function; one that is not here is shown by that name)
TAPS_HE = {"clearNewsSearch": "ניקוי חיפוש חדשות", "clearSelectedCategories": "ניקוי קטגוריות חדשות", "toggleSelectedCategory": "קטגוריית חדשות",
           "toggleMacroMore": "עוד מקורות לידיעה", "openKeyNews": "אירוע מרכזי", "openDigestItem": "ידיעה בסיכום היומי",
           "openInfoModal": "חלון מידע", "toggleInfoSection": "סעיף בחלון המידע", "openLegal": "מדיניות / תנאים",
           "openAlertsPanel": "חלון ההתראות", "openLineAlert": "פעמון על קו בגרף", "deleteLineAlert": "מחיקת התראה", "deletePriceAlert": "מחיקת התראה",
           "enablePush": "הפעלת התראות", "enablePushForLine": "הפעלת התראות", "disablePush": "כיבוי התראות", "testPush": "התראת ניסיון",
           "openTradeForm": "טופס עסקה", "deleteTrade": "מחיקת עסקה", "exportTradesCsv": "הורדת העסקאות", "setPfView": "תצוגה בתיק שלי",
           "pfOpenSignIn": "כפתור התחברות", "toggleProfileMenu": "תפריט הפרופיל", "signOut": "התנתקות", "signOutOtherDevices": "ניתוק מכשירים אחרים",
           "exportMyData": "הורדת המידע שלי", "deleteMyAccount": "מחיקת חשבון", "installApp": "התקנת האפליקציה", "setThemePref": "החלפת עיצוב בהיר / כהה",
           "openCompare": "השוואת מניות", "openAddToCompare": "הוספה להשוואה", "removeFromCompare": "הסרה מההשוואה", "switchComparePeriod": "תקופת השוואה",
           "switchDeepDiveSectorPeriod": "תקופת השוואת סקטור", "switchSectorPeriod": "תקופת סקטורים", "showAllSectors": "כל הסקטורים",
           "switchChart": "גרף מדד", "setChartMode": "סוג גרף", "toggleFundTip": "הסבר על נתון פונדמנטלי", "econGoToday": "היום בלוח האירועים",
           "selectEconDay": "יום בלוח האירועים", "setEconView": "תצוגת לוח האירועים", "shiftEconMonth": "חודש בלוח האירועים",
           "toggleEconCard": "כרטיס אירוע כלכלי", "selectEarnDay": "יום ביומן הדוחות", "toggleEarnDetail": "שורת דוח", "toggleEarnSession": "בוקר / ערב ביומן",
           "showAllEarnSession": "כל הדוחות של היום", "loadHeatmap": "טעינת מפת החום", "setHeatmapIndex": "מדד במפת החום",
           "openBreadthPage": "רוחב השוק", "openRegimePage": "מצב השוק", "openFocusTicker": "המוקד של היום", "renderFocus": "המוקד של היום",
           "openWatchlistSetups": "סריקות במעקב", "setMoversPanelMode": "הכי זזות / מעקב", "toggleStockGroup": "כל הסריקות", "toggleScanAdv": "מסנני הסריקה",
           "resetScanFilters": "ניקוי מסננים", "setScanFilter": "מסנן סריקה", "setScanPatternFilter": "מסנן תבנית", "renderStocks": "סינון סריקות",
           "renderPortfolio": "תקופה בתיק שלי", "renderCorpCalendar": "דיבידנדים / הנפקות", "renderFunds": "קרנות", "renderLeaders": "מובילות",
           "setDemoParam": "הגדרת תיק הדמו", "setBacktestHorizon": "טווח בסטטיסטיקה", "setBacktestPattern": "תבנית בסטטיסטיקה",
           "setBacktestPeriodView": "תקופה בסטטיסטיקה", "setWatchSignals": "התראות סריקה", "setWatchInsider": "התראות בעלי עניין",
           "setWatchMacro": "התראות מאקרו", "setWatchMorning": "תדריך בוקר", "homeGo": "קיצור בדף הבית", "nudgeGo": "הצעה בדף הבית: כן",
           "nudgeOff": "הצעה בדף הבית: לא עכשיו", "endTour": "סגירת הסיור", "openRiskCalc": "מחשבון סיכון"}
DEV_HE = {"phone": "טלפון", "tablet": "טאבלט", "desktop": "מחשב"}
COUNTRY_HE = {"IL": "ישראל", "US": "ארה״ב", "GB": "בריטניה", "DE": "גרמניה", "FR": "צרפת", "CA": "קנדה", "IN": "הודו",
              "NL": "הולנד", "ES": "ספרד", "IT": "איטליה", "AU": "אוסטרליה", "BR": "ברזיל"}


def screen_he(label: str) -> str:
    if label.startswith("stock/"):
        return "ניתוח " + label[6:]
    return SCREENS_HE.get(label, label or "?")


def event_he(kind: str, label: str) -> str:
    if kind == "screen":
        return screen_he(label)
    if kind == "dd_tab":
        label = TABS_HE.get(label, label)
    if kind == "section":
        label = SECTIONS_HE.get(label, label)
    if kind == "scroll":
        label = "עד הסוף" if label == "100" else "עד האמצע"
    if kind in ("tap", "pick"):
        name, _, rest = label.partition(" ")
        label = (TAPS_HE.get(name, name) + " " + rest).strip()
    return (EVENTS_HE.get(kind, kind) + " " + label).strip()


def place_he(r: dict) -> str:
    c = r.get("country")
    if c:
        return COUNTRY_HE.get(c, c)
    tz = r.get("tz") or ""
    return "ישראל" if tz in ("Asia/Jerusalem", "Asia/Tel_Aviv") else tz.split("/")[-1].replace("_", " ")


def source_he(r: dict) -> str:
    s = r.get("src") or ""
    if s.startswith("utm:"):
        return "קמפיין " + s[4:]
    if s.startswith("site:"):
        return "מדריך באתר: " + s[5:]
    return s or "ישירות"


def dur(s) -> str:
    s = int(s or 0)
    return f"{s // 60}:{s % 60:02d}"


def steps(r: dict) -> list:
    """[(kind, label, seconds on it or None)]: a screen lasts until the next screen, other steps have no duration."""
    ev = r.get("ev") or []
    marks = [i for i, e in enumerate(ev) if e[1] == "screen"]
    end = {}
    for a, b in zip(marks, marks[1:] + [None]):
        stop = ev[b][0] if b is not None else max(r.get("seconds") or 0, ev[-1][0])
        end[a] = max(0, stop - ev[a][0])
    return [(e[1], e[2] if len(e) > 2 else "", end.get(i)) for i, e in enumerate(ev)]


def _joined(values, limit: int) -> str:
    return ", ".join(dict.fromkeys(v for v in values if v))[:limit]


# the columns of the table, in the order Neon shows them
COLUMNS = ("time_il", "visitor", "visit_no", "country", "device", "browser", "source", "duration", "load_sec", "stocks", "steps",
           "last_screen", "not_found", "errors", "signed_in", "lang", "landing", "started_at", "seconds", "n_events", "visitor_id", "visit_id")


def row(r: dict) -> tuple:
    """One visit -> the values of COLUMNS."""
    st = steps(r)
    stocks = _joined([lb[6:] if k == "screen" else lb for k, lb, _ in st
                      if (k == "screen" and lb.startswith("stock/")) or k in ("view_stock", "search_stock")], 300)
    told = " | ".join(event_he(k, lb) + (f" ({dur(s)})" if s is not None else "") for k, lb, s in st)[:MAX_STEPS_TEXT]
    out = (r["started"].astimezone(IL).strftime("%Y-%m-%d %H:%M"), r["p"][:6], r.get("n") or 1, place_he(r),
           DEV_HE.get(r.get("dev"), "") + (" (אפליקציה)" if r.get("pwa") else ""), r.get("browser") or "", source_he(r),
           dur(r.get("seconds")), r.get("ld") or None, stocks, told,
           next((screen_he(lb) for k, lb, _ in reversed(st) if k == "screen"), ""),
           _joined([lb for k, lb, _ in st if k == "search_none"], 300), _joined([lb for k, lb, _ in st if k == "error"], 300),
           "כן" if r.get("reg") or any(k == "signin" for k, _, _ in st) else "לא",
           {"he": "עברית", "en": "אנגלית"}.get(r.get("lang"), ""), r.get("land") or "",
           r["started"], r.get("seconds") or 0, len(r["ev"]), r["p"], r["v"])
    # a browser's text never begins with a sign that a spreadsheet would run as a formula (the table may be exported)
    return tuple(v.lstrip("=+-@ ") if isinstance(v, str) else v for v in out)


def ensure_tables(cur) -> None:
    cur.execute("""CREATE TABLE IF NOT EXISTS visits (time_il TEXT, visitor TEXT, visit_no INTEGER, country TEXT, device TEXT,
                   browser TEXT, source TEXT, duration TEXT, load_sec INTEGER, stocks TEXT, steps TEXT, last_screen TEXT,
                   not_found TEXT, errors TEXT, signed_in TEXT, lang TEXT, landing TEXT, started_at TIMESTAMPTZ NOT NULL, seconds INTEGER, n_events INTEGER, visitor_id TEXT NOT NULL,
                   visit_id TEXT PRIMARY KEY)""")
    cur.execute("CREATE INDEX IF NOT EXISTS visits_started_idx ON visits (started_at)")


def save(cur, r: dict) -> None:
    kept = ("time_il", "started_at", "visitor", "visitor_id", "visit_id", "visit_no", "source", "landing")      # as first written
    cur.execute(f"""INSERT INTO visits ({', '.join(COLUMNS)}) VALUES ({', '.join(['%s'] * len(COLUMNS))})
                    ON CONFLICT (visit_id) DO UPDATE SET {', '.join(f'{c} = EXCLUDED.{c}' for c in COLUMNS if c not in kept)}
                    WHERE visits.visitor_id = EXCLUDED.visitor_id AND visits.n_events <= EXCLUDED.n_events""", row(r))


def prune(cur) -> None:
    cur.execute("DELETE FROM visits WHERE started_at < NOW() - make_interval(days => %s)", (KEEP_DAYS,))
    cur.execute("DELETE FROM visits WHERE visit_id IN (SELECT visit_id FROM visits ORDER BY started_at DESC OFFSET %s)", (MAX_ROWS,))
    cur.execute("DELETE FROM analytics_events WHERE timestamp < NOW() - INTERVAL '180 days'")     # the old log, as promised
