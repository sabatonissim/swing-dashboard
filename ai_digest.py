"""AI daily digest — ONE summary per DAY, written again as the day's news arrives.

How it works (v37, 6.11.2026; the day the S&P 500 fell 4.36% its digest opened with "technology and earnings dominated" and
listed Philippine inflation next to it. The model had no market numbers, and got 80 of 1,564 headlines picked at even
spacing, two thirds of them small-company earnings):
  * The "day" is a calendar day in DIGEST_TZ (default America/New_York = the US market day; change with the
    DIGEST_TZ variable, e.g. Asia/Jerusalem).
  * market_facts(): the real numbers of the session (indices, VIX, oil, how many stocks rose and fell, the big movers).
    They go to the model and into the payload, and the site shows them as they are, not through the AI.
  * rank_items(): the same story from many outlets becomes one item, and items are ranked for a US-market reader.
    Only the titles of the whole day are read from the database, then the details of those chosen.
  * Every update is written from the whole day. On an extreme day the overview must open with the move and its number.
  * The first news run after the close always updates, so a move shows up the same evening.
  * When the day ends, one last update closes it ("final") and it stays in the history table, so earlier days
    can be read later (api_server.py -> /api/daily-digest?day=YYYY-MM-DD and /api/daily-digest/days).

Runs at the end of pipeline_b (GitHub Actions) — NEVER per website visit. The website only READS the stored result.

Safe by design:
  * no LLM_API_KEY            -> does nothing (the site keeps the rule-based digest)
  * AI call fails / bad JSON  -> nothing is saved, the previous digest stays, pipeline_b is unaffected
  * facts only                -> the model sees ONLY the numbered headlines and the market numbers. Every topic must cite
                                 sources, every number must appear in what it was given, and a rejected answer goes back
                                 to it with the reason. Output that reads like investment advice is rejected.
  * English first, then the model writes the Hebrew; if that fails, the project's free translator (passed in by the caller).

Manual:  python ai_digest.py --force                   (write today's digest now)
         python ai_digest.py --force --day 2026-11-05  (write one earlier day again)
         python ai_digest.py                           (normal update, same as the pipeline does)
         python ai_digest.py --backfill 7              (create summaries for the last 7 days that have none)
         python ai_digest.py --probe                   (which AI models work for my key right now?)
"""
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

import llm_provider
from news_links import is_unwanted_headline, LOCAL_RE, GLOBAL_RE
from translation_fixes import fix_he

MAX_ITEMS = 70          # stories sent to the model
MAX_EARNINGS = 15       # of them, about one company's results
MIN_ITEMS = 5           # a day needs at least this many headlines before its first digest
MIN_NEW = 5             # a normal update needs at least this many new headlines
MAX_TOPICS = 5
BODY_CHARS = 240
MAX_SOURCES = 4
KEEP_DAYS = 90
CLOSE_LOOKBACK_DAYS = 3  # past days that are still closed with one last AI update (older ones are just marked final)
ATTEMPTS = 3            # a rejected answer goes back to the model with the reason, this many times in all
UPDATE_BUDGET_SEC = 480  # one update (the news job stops at 20 minutes and has done its own work by then)
# v37c: a larger model only for this text, read by everyone. The other jobs make dozens of requests a run and keep LLM_MODELS.
DIGEST_CALL_TIMEOUT_SEC, DIGEST_CALL_BUDGET_SEC = "120", "240"
BIG_CAP = 50e9          # "large company" for the movers
MOVERS_SHOWN = 5
# How big the session was. Extreme: the overview must open with it. Fixed rule, the same one data_health.py checks.
EXTREME_SP500, EXTREME_NASDAQ = 2.0, 2.5
NOTABLE_SP500, NOTABLE_NASDAQ = 1.0, 1.5

_ET = ZoneInfo("America/New_York")
AFTER_CLOSE = dtime(16, 20)   # the scan and the breadth job of the close have saved their numbers by then

MARKET_SYMBOLS = [("^GSPC", "S&P 500", "S&P 500"), ("^IXIC", "Nasdaq", 'נאסד"ק'), ("^DJI", "Dow Jones", "דאו ג'ונס"),
                  ("^SOX", "Chip stocks (SOX)", "מניות השבבים (SOX)"), ("^VIX", "VIX", "VIX"),
                  ("CL=F", "Oil (WTI)", "נפט (WTI)"), ("GC=F", "Gold", "זהב"),
                  ("^TNX", "10-year Treasury yield", "תשואת אג\"ח ל-10 שנים"), ("BTC-USD", "Bitcoin", "ביטקוין")]


# ------------------------------------------------------------------ the "day"
def _tz():
    name = (os.environ.get("DIGEST_TZ") or "").strip() or "America/New_York"
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return ZoneInfo("America/New_York")


def today_local(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(_tz()).date()


def day_bounds(day):
    """[start, end) of a calendar day in DIGEST_TZ, as UTC datetimes."""
    tz = _tz()
    start = datetime.combine(day, dtime.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), dtime.min, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


# ------------------------------------------------------------------ the market's own numbers
def bars_from_chart(data):
    """[(UTC date, close)] from Yahoo's chart answer: one bar a date, in order, without an empty last bar."""
    r = data["chart"]["result"][0]
    bars = {}
    for ts, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
        if c is not None:
            bars[datetime.fromtimestamp(ts, tz=timezone.utc).date()] = float(c)
    return sorted(bars.items())


def _history(symbol):
    """[(date, close)] of the last month. v37e: the chart answer itself first: for Bitcoin, yfinance's history gave Thursday's
    -5.55% as Friday's move (the real one: -0.26%). yfinance only if that fails, as the jobs in GitHub Actions already call it."""
    try:
        r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}", params={"range": "1mo", "interval": "1d"},
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}, timeout=20)
        r.raise_for_status()
        bars = bars_from_chart(r.json())
        if len(bars) >= 2:
            return bars
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {symbol}: chart answer failed ({type(e).__name__}), using yfinance")
    import yfinance as yf
    h = yf.Ticker(symbol).history(period="1mo", interval="1d", auto_adjust=False)
    return [(i.date(), float(c)) for i, c in zip(h.index, h["Close"]) if c == c]


def move_size(indices, same_day):
    """extreme / notable / normal, from the S&P 500 and the Nasdaq; closed = the market did not trade that day."""
    if not same_day:
        return "closed"
    pct = {i["symbol"]: abs(i["change_pct"]) for i in indices}
    sp, nq = pct.get("^GSPC", 0), pct.get("^IXIC", 0)
    if sp >= EXTREME_SP500 or nq >= EXTREME_NASDAQ:
        return "extreme"
    return "notable" if sp >= NOTABLE_SP500 or nq >= NOTABLE_NASDAQ else "normal"


def _market_from_db(conn, session_day):
    """How many stocks rose and fell, and the big movers. Under 1 KB. universe_movers holds only the latest session, so an
    earlier day written again has no movers, never another day's. v37f: not by the row's date. A refresh on Saturday stamps the
    rows of Friday's prices with Saturday, and a filter on the date left only the stocks it does not refresh: no AMD (+11%)."""
    out = {}
    cur = conn.cursor()
    try:
        cur.execute("SELECT advancers, decliners, new_highs, new_lows FROM market_breadth WHERE day = %s", (session_day,))
        r = cur.fetchone()
        if r:
            out["breadth"] = {"advancers": r[0], "decliners": r[1], "new_highs": r[2], "new_lows": r[3]}
        cur.execute("SELECT MAX(day) FROM market_breadth")
        if cur.fetchone()[0] == session_day:
            start = datetime.combine(session_day, dtime.min, tzinfo=_ET)
            movers = {}
            for side, order in (("down", "ASC"), ("up", "DESC")):
                cur.execute(f"""SELECT ticker, change_pct FROM universe_movers
                                WHERE market_cap >= %s AND timestamp >= %s AND change_pct <> 'NaN'
                                ORDER BY change_pct {order} LIMIT %s""",   # fixed words, not input
                            (BIG_CAP, start, MOVERS_SHOWN))
                movers[side] = [{"ticker": t, "change_pct": round(float(c), 2)} for t, c in cur.fetchall()]
            movers["down"] = [m for m in movers["down"] if m["change_pct"] < 0]
            movers["up"] = [m for m in movers["up"] if m["change_pct"] > 0]
            if movers["down"] or movers["up"]:
                out["movers"] = movers
    except Exception as e:  # noqa: BLE001
        conn.rollback()
        print(f"[ai_digest] breadth / movers skipped ({type(e).__name__}: {e})")
    finally:
        cur.close()
    return out


MARKET_OPEN = dtime(9, 30)
FIRST_BAR_BY = dtime(10, 0)   # Yahoo has the day's bar by then; no bar after it on a weekday = no trading that day


def nyse_holidays(year):
    """The days the US stock market is closed by its regular rules. TWIN of api_server._nyse_holidays (a test keeps them equal):
    the server is not imported by the jobs."""
    def nth(month, weekday, n):
        d = date(year, month, 1)
        return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))

    def observed(d):
        return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d
    a, b, c = year % 19, year // 100, year % 100                       # Easter (the Gregorian computus), for Good Friday
    h = (19 * a + b - b // 4 - (b - (b + 8) // 25 + 1) // 3 + 15) % 30
    k = (32 + 2 * (b % 4) + 2 * (c // 4) - h - c % 4) % 7
    m = (a + 11 * h + 22 * k) // 451
    month = (h + k - 7 * m + 114) // 31
    day = (h + k - 7 * m + 114) % 31 + 1
    last_may = date(year, 5, 31)
    out = {nth(1, 0, 3), nth(2, 0, 3), date(year, month, day) - timedelta(days=2), last_may - timedelta(days=last_may.weekday()),
           observed(date(year, 6, 19)), observed(date(year, 7, 4)), nth(9, 0, 1), nth(11, 3, 4), observed(date(year, 12, 25))}
    new_year = date(year, 1, 1)                                        # on a Saturday it is not moved back into December
    if new_year.weekday() != 5:
        out.add(observed(new_year))
    return out


def no_session_reason(day, now):
    """Why `day` has no session of its own: weekend / holiday / pre_open (later today). v38b: on Saturday, Sunday and Monday
    morning (7-9.11.2026) the digest told Friday's moves as the day's, and Monday's gave AMD's +11.04% to Nvidia.
    v38c: the holidays by the calendar first; before that, Thanksgiving morning read as "opens at 9:30"."""
    if day.weekday() >= 5:
        return "weekend"
    if day in nyse_holidays(day.year):
        return "holiday"
    et = now.astimezone(_ET)
    if et.date() < day or (et.date() == day and et.time() < FIRST_BAR_BY):
        return "pre_open"
    return "holiday"


def _bar_of(bars, day):
    """(change %, close) of `day`'s own bar, or None."""
    for k in range(1, len(bars)):
        if bars[k][0] == day and bars[k - 1][1]:
            return round((bars[k][1] / bars[k - 1][1] - 1) * 100, 2), round(bars[k][1], 2)
    return None


def market_facts(conn, day, now=None):
    """The session of `day` (or the last one before it). None when there are no prices: the digest is still written.
    No session that day: `status` says why, the last session's numbers stay in the payload for the site (with its date), and
    the model is not given them (_market_text). Bitcoin trades every day, so its own move of the day is in `today`."""
    now = now or datetime.now(timezone.utc)
    try:
        anchor = [b for b in _history("^GSPC") if b[0] <= day]
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] no market numbers ({type(e).__name__}: {e})")
        return None
    if len(anchor) < 2:
        print("[ai_digest] no market numbers: too few S&P 500 prices came back")
        return None
    session_day = anchor[-1][0]
    same_day = session_day == day
    indices, today = [], []
    for symbol, name_en, name_he in MARKET_SYMBOLS:
        try:
            full = anchor if symbol == "^GSPC" else _history(symbol)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {symbol}: {type(e).__name__}: {e}")
            continue
        bars = [b for b in full if b[0] <= session_day]
        if len(bars) >= 2 and bars[-2][1]:
            close, prev = bars[-1][1], bars[-2][1]
            indices.append({"symbol": symbol, "name_en": name_en, "name_he": name_he, "close": round(close, 2),
                            "change_pct": round((close / prev - 1) * 100, 2), "change_points": round(close - prev, 2)})
        own = None if same_day or symbol != "BTC-USD" else _bar_of(full, day)
        if own:
            today.append({"symbol": symbol, "name_en": name_en, "name_he": name_he, "change_pct": own[0], "close": own[1]})
    et = now.astimezone(_ET)
    intraday = same_day and et.date() == day and MARKET_OPEN <= et.time() < dtime(16, 0)
    market = {"day": day.isoformat(), "session_day": session_day.isoformat(), "same_day": same_day, "indices": indices, "intraday": intraday,
              "status": ("open" if intraday else "traded") if same_day else no_session_reason(day, now),
              "move": move_size(indices, same_day)}
    if today:
        market["today"] = today
    market.update(_market_from_db(conn, session_day))
    return market


def _signed(x):
    return f"{x:+.2f}"


_NO_SESSION_TEXT = {
    "weekend": "It is the weekend: the US stock market does not trade today.",
    "holiday": "It is a US market holiday: the US stock market does not trade today.",
    "pre_open": "The US stock market has not opened yet today (it opens at 9:30 New York time).",
}


def _no_session_text(market, day):
    """v38b: no session that day -> no index numbers for the model at all. With the last session's numbers it wrote them as the
    day's on Saturday, Sunday and Monday morning (7-9.11.2026); that session has its own summary."""
    lines = [f"MARKET: {_NO_SESSION_TEXT.get(market.get('status'), _NO_SESSION_TEXT['holiday'])} "
             f"The last session was {market['session_day']} and has its own summary. So on {day.isoformat()} there are NO index "
             "moves, no VIX, no biggest movers and no count of stocks that rose or fell. Do not write that the market, an index or a "
             "stock rose, fell, closed or ended, except what an item says about futures, pre-market trading or the past week.",
             "Write about the news of this day: what happened and what may matter for US stocks when trading resumes."]
    for a in market.get("today") or []:
        lines.append(f"{a['name_en']}, which trades every day: {_signed(a['change_pct'])}% on this day so far")
    return "\n".join(lines)


def _market_text(market, day):
    if not market:
        return "MARKET NUMBERS: not available. Do not state any index level or move that is not in the news items."
    if not market["same_day"]:
        return _no_session_text(market, day)
    head = (f"MARKET NUMBERS, real data for the US session of {market['session_day']}"
            + (" (TRADING IS STILL OPEN: numbers so far. Do not write that the market closed or ended)" if market["intraday"] else "") + ":")
    lines = [head]
    for i in market["indices"]:
        if i["symbol"] == "^TNX":
            lines.append(f"{i['name_en']}: {i['close']:.2f}% (change {_signed(i['change_points'])} points)")
        elif i["symbol"] == "^VIX":
            lines.append(f"{i['name_en']}: {_signed(i['change_pct'])}% (at {i['close']})")
        else:                                              # v37b: with the levels, the overview became a list of "fell 4.36% to 7353.73"
            lines.append(f"{i['name_en']}: {_signed(i['change_pct'])}%")
    b = market.get("breadth")
    if b:
        lines.append(f"S&P 500 stocks that rose / fell: {b['advancers']} / {b['decliners']}")
    mv = market.get("movers") or {}
    for side, label in (("down", "Biggest falls"), ("up", "Biggest rises")):
        if mv.get(side):
            lines.append(f"{label} among large companies: " + ", ".join(f"{m['ticker']} {_signed(m['change_pct'])}%" for m in mv[side]))
    lines.append({"extreme": "SIZE OF THE DAY: EXTREME. This is the main story of the day. The overview must open with it, name the index and give its number.",
                  "notable": "SIZE OF THE DAY: a clearly large move. The overview must mention it.",
                  "normal": "SIZE OF THE DAY: ordinary.", "closed": "SIZE OF THE DAY: no session today."}[market["move"]])
    return "\n".join(lines)


# ------------------------------------------------------------------ which headlines the model sees
_MAJOR = ("reuters", "bloomberg", "cnbc", "wall street journal", "wsj", "associated press", "ap news", "financial times",
          "marketwatch", "barron", "new york times", "axios", "federal reserve", "bls")
_MARKET_RE = re.compile(r"\b(wall street|s&p( 500)?|nasdaq|dow( jones)?|stocks?|stock market|equities|sell-?off|rout|crash\w*|"
                        r"plunge[sd]?|tumble[sd]?|slump\w*|sink[s]?|rall(y|ies|ied)|worst day|best day|record (high|close)|"
                        r"correction|bear market|volatility|vix)\b", re.I)
_US_MACRO_RE = re.compile(r"\b(fed|federal reserve|fomc|powell|warsh|cpi|inflation|payrolls?|jobs report|unemployment|jobless|"
                          r"gdp|pce|treasur\w+|yields?|tariffs?|white house|congress|shutdown|midterms?|recession|consumer)\b", re.I)
_WORLD_RE = re.compile(r"\b(oil|crude|brent|wti|opec|iran|hormuz|china|ecb|bank of japan|bank of england|dollar|gold|bitcoin)\b", re.I)
_BIG_NAME_RE = re.compile(r"\b(apple|microsoft|nvidia|alphabet|google|amazon|meta|tesla|broadcom|amd|tsmc|intel|micron|oracle|"
                          r"netflix|palantir|samsung|sk hynix|jpmorgan|goldman|berkshire|walmart|exxon|chevron|eli lilly|"
                          r"magnificent|mag 7|data centers?|chips?|semiconductors?|ai stocks?)\b", re.I)
_EARNINGS_TAG = "דוחות"
_STOP = {"after", "amid", "with", "from", "that", "this", "than", "into", "over", "what", "when", "will", "says", "said", "have",
         "their", "they", "more", "about", "stock", "stocks", "shares", "market", "markets", "news", "today", "report",
         # the same words in the results of any company: two companies' reports are not one story
         "earnings", "results", "revenue", "revenues", "quarter", "quarterly", "third", "fiscal", "estimates", "miss", "misses",
         "beat", "beats", "profit", "profits", "forecast", "forecasts", "outlook", "sales", "raises", "cuts", "guidance", "reports"}


def _words(title):
    return {w for w in re.findall(r"[a-z0-9&']{4,}", title.lower()) if w not in _STOP}


def item_score(it):
    """How much one story matters to someone who follows the US market. Fixed rules, so a choice can be explained."""
    t = it["title"]
    market, macro, world = bool(_MARKET_RE.search(t)), bool(_US_MACRO_RE.search(t)), bool(_WORLD_RE.search(t))
    big = bool(_BIG_NAME_RE.search(t))
    outlet = (it.get("source") or "").lower()
    score = 6 * market + 5 * macro + 3 * world + 3 * big
    score += 2 * min(it.get("outlets", 1), 8)
    score += 3 if any(m in outlet for m in _MAJOR) else 0
    if it["tag"] == _EARNINGS_TAG and not (big or market or macro):
        score -= 6                                                  # one smaller company's results
    if LOCAL_RE.search(t) and not (market or GLOBAL_RE.search(t)):
        score -= 10                                                 # far-away domestic news
    return score


def rank_items(items, cap=MAX_ITEMS):
    """One entry per story, the most important first. `outlets` = how many different outlets ran it."""
    clusters = []
    for it in items:
        if is_unwanted_headline(it["title"]):
            continue
        words = _words(it["title"])
        home = None
        if len(words) >= 3:
            for c in clusters:
                shared = len(words & c["words"])
                if shared >= 3 and shared >= 0.6 * min(len(words), len(c["words"])):
                    home = c
                    break
        if home is None:
            home = {"words": words, "members": []}
            clusters.append(home)
        home["members"].append(it)
    ranked = []
    for c in clusters:
        outlets = len({(m.get("source") or "").lower() for m in c["members"] if m.get("source")}) or 1
        best = max((dict(m, outlets=outlets) for m in c["members"]), key=lambda m: (item_score(m), m["id"]))
        ranked.append((item_score(best), best))
    ranked.sort(key=lambda p: (-p[0], -p[1]["id"]))
    out, earnings = [], 0
    for score, it in ranked:
        if it["tag"] == _EARNINGS_TAG:
            if earnings >= MAX_EARNINGS:
                continue
            earnings += 1
        out.append(it)
        if len(out) >= cap:
            break
    return out


# ------------------------------------------------------------------ prompts
SYSTEM = """You are the markets editor of a website for people who follow the US stock market. You write the day's summary. A reader who opens only this must understand what happened to the market today and why.

ORDER OF IMPORTANCE:
1. What the US market did: the indices, how big the move was, which stocks and sectors led it. If the MARKET NUMBERS say the day was extreme, this is the first thing in the overview and the first topic, with the numbers.
2. Why it happened, as far as the items say. If several items give the same reason, say it. If no item explains it, say the reason is not clear.
3. What else can move US stocks: the Fed and rates, US data (inflation, jobs), oil and war, trade and tariffs, US politics, the largest companies.
On a day the MARKET part says the US market did not trade (weekend, holiday, before the open), there is no point 1: the overview opens with the most important news of the day, and no topic tells index or stock moves of any session, unless an item tells futures, pre-market trading or the past week.
4. Other countries only if the item ties them to US stocks, oil, the dollar or world markets. The domestic news of a smaller country (its local inflation or local interest rate) is not a topic and is not in the overview. Results of one smaller company are not a topic unless they moved the market.

STRICT RULES:
- Base every statement on the MARKET NUMBERS and the items. Never add numbers, names, causes, dates or background that are not there. Every number you write must appear in what you were given. You may round (4.36% to 4.4%).
- The numbers say what happened; do not let a headline contradict them. Headlines saved today can tell yesterday's move: if a headline says oil tumbled and the numbers say oil rose today, that is yesterday's story, so do not tell it as today's.
- One story per topic: do not attach an unrelated story (a central bank's decision does not belong in a topic about Asian stocks). "(N outlets)" = how many outlets ran that story; many outlets usually means it matters, and the items come most important first.
- Never explain a number yourself. A reason comes only from an item. Do not write "suggests", "possibly", "reflecting", "investors sought" or "investors fled" of your own.
- Keep who did what exactly as the item says. "The Magnificent 7 lost $1 trillion" is not "Nvidia lost $1 trillion".
- The overview tells the story: the main move, 2 or 3 of the most important numbers, and the reason. The site shows all the numbers next to it, so do not list them, and do not write index levels or dates.
- The lists of biggest falls and rises give tickers and numbers only. They do not say what a company does or why it moved: do not call them "AI names" or anything else unless an item says so, and do not read a pattern into them.
- A topic is a news story told by the items. Never a topic that only repeats the market numbers ("gold rose, the VIX rose"). "items" = only items that are about that story.
- Say "430 of the S&P 500 stocks fell", not "trading was broad".
- "impact" says why this matters for US stocks, specific to this story, in words such as "may", "could" or "tends to". It is context, NOT a forecast: never predict a price level or a direction. Empty if you have nothing specific to say. Never a sentence that would fit any story.
- No investment advice: never say or imply that anyone should buy, sell or hold anything.
- The first topic is the story behind the day's main move, built on item [1] when the day was large.
- Plain everyday words (write "sell-off", never "rout"), one idea per sentence, numbers whenever you have them. No market jargon ("beat", "guidance", "print", "risk-on", "hawkish", "bid"): say what happened ("results were better than forecast"). No filler such as "markets digested", "investors weighed", "mixed picture", "dominated the day". Company names exactly as in the items.

Reply with ONLY a valid JSON object (no markdown, no commentary). Use straight double quotes, no trailing commas, and never put a double quote character inside a text value.
Exact shape:
{"overview": "2 or 3 short sentences, max 380 characters: what the market did and why",
 "topics": [{"title": "max 60 characters", "summary": "1 or 2 sentences: what happened, with numbers, max 300 characters", "impact": "one sentence, max 180 characters, or empty", "items": [numbers of the supporting items]}]}
3 to 5 topics, the most important first. Every topic lists the items it comes from."""

SYSTEM_HE = """Translate this market summary from English into natural, simple Israeli Hebrew, as a financial newspaper would write it.
Keep the meaning exactly: who did what must not change, and add nothing. Never leave an ordinary English word in the Hebrew.
Glossary: stocks = מניות; session = יום מסחר (never "מפגש"); sell-off or rout = גל מכירות (never "מסלול"); safe haven = נכס מפלט; crypto = קריפטו; 10-year Treasury yield = תשואת אג"ח ממשלתי ל-10 שנים; index = מדד; chip stocks = מניות השבבים; interest rate = ריבית; rate hike = העלאת ריבית; yield = תשואה; Treasury bond = אג"ח ממשלתי; inflation = אינפלציה; results / earnings = דוחות או רווחים; forecast / outlook = תחזית; oil = נפט; tariffs = מכסים; sanctions = סנקציות; midterm elections = בחירות אמצע הקדנציה; the Fed = הפד; Wall Street = וול סטריט; AI = בינה מלאכותית.
Names of companies, indices and people stay in English letters (Nvidia, S&P 500, Nasdaq, VIX). A company is feminine in Hebrew. Keep every number exactly as written.
Write Hebrew abbreviations with the mark ״ (ארה״ב, נאסד״ק), never with a plain double quote.
Keep the same fields and the same number of topics; an empty field stays empty. Answer with the JSON only, in the same shape as the input."""

_ADVICE = re.compile(r"\b(you should|we recommend|recommend(?:ed|s)? (?:to )?(?:buy|sell)|should (?:buy|sell|hold)|time to (?:buy|sell)|buy now|sell now)\b", re.I)
# v37b, seen in the first real answer (5.11.2026): "suggested investors were not fleeing to bonds, possibly reflecting inflation concerns"
_OWN_GUESS = re.compile(r"\b(suggest(?:s|ed|ing)?|possibly|perhaps|reflect(?:s|ed|ing)?|risk-off|risk-on|investors? (?:sought|fled|fleeing|rushed|were|are)|"
                        r"profit[- ]taking|rotation|breadth was|reacted|relief|safe[- ]haven|across (?:all )?sectors)\b", re.I)
# v37c: more than one of these in a topic = a list of the market numbers the site shows anyway, not a story
_ASSET_WORDS = (r"\boil\b|\bwti\b|\bcrude\b", r"\byields?\b|\btreasur", r"\bgold\b", r"\bvix\b", r"\bbitcoin\b|\bcrypto")
_ENDED = re.compile(r"\b(ended|closed|finished|at the close)\b", re.I)    # 14:06 ET on 6.11.2026: "The US market ended mixed"
_ISO_DATE = re.compile(r"\s*\b(?:on |for )?\d{4}-\d{2}-\d{2}\b")
MAX_OVERVIEW = 450       # the shape asks for 380; the first real answer wrote 480, a list of every number
_SHORT_TERMS = re.compile(r"\b(ai|fed|oil|ecb|sox|vix|gdp|cpi|boj|us)\b", re.I)


# ------------------------------------------------------------------ database
def ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS ai_daily_digests (
        day DATE PRIMARY KEY,
        updated_at TIMESTAMPTZ DEFAULT NOW(),
        item_count INTEGER,
        last_item_id INTEGER,
        updates INTEGER DEFAULT 1,
        since_rebuild INTEGER DEFAULT 0,
        is_final BOOLEAN DEFAULT FALSE,
        model TEXT,
        payload TEXT)""")
    conn.commit()
    cur.close()


def _load_row(conn, day):
    cur = conn.cursor()
    cur.execute("""SELECT updated_at, item_count, last_item_id, updates, since_rebuild, is_final, model, payload,
                          EXTRACT(EPOCH FROM (NOW() - updated_at)) / 3600
                   FROM ai_daily_digests WHERE day = %s""", (day,))
    r = cur.fetchone()
    cur.close()
    if not r:
        return None
    return dict(updated_at=r[0], item_count=r[1] or 0, last_item_id=r[2] or 0, updates=r[3] or 1,
                since_rebuild=r[4] or 0, is_final=bool(r[5]), model=r[6], payload=json.loads(r[7]), age_h=float(r[8] or 0))


def _count_new(conn, day, after_id):
    start, end = day_bounds(day)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM macro_news WHERE timestamp >= %s AND timestamp < %s AND id > %s", (start, end, after_id))
    n = int(cur.fetchone()[0])
    cur.close()
    return n


def _fetch_titles(conn, day):
    """Headlines that ARRIVED during `day` (macro_news.timestamp = when the pipeline saved them). Titles only: ~150 bytes a
    row against ~1.2 KB with the texts, and this runs on every update (Neon's network quota, CLAUDE.md §3)."""
    start, end = day_bounds(day)
    cur = conn.cursor()
    cur.execute("""SELECT id, category_tag, summary_en, source_name, timestamp FROM macro_news
                   WHERE timestamp >= %s AND timestamp < %s ORDER BY id ASC""", (start, end))
    rows = cur.fetchall()
    cur.close()
    return [dict(id=r[0], tag=r[1], title=r[2] or "", source=r[3] or "", ts=r[4]) for r in rows]


def _add_details(conn, items):
    """The texts and links, only of the stories going to the model."""
    cur = conn.cursor()
    cur.execute("SELECT id, body_en, source_url, summary_he FROM macro_news WHERE id = ANY(%s)", ([i["id"] for i in items],))
    extra = {r[0]: r[1:] for r in cur.fetchall()}
    cur.close()
    for it in items:
        body, url, title_he = extra.get(it["id"], ("", "", ""))
        it.update(body=body or "", url=url or "", title_he=title_he or "")
    return items


# ------------------------------------------------------------------ prompts' user part
def _item_lines(items):
    lines = []
    for n, it in enumerate(items, 1):
        body = re.sub(r"\s+", " ", it.get("body") or "").strip()[:BODY_CHARS]
        count = it.get("outlets", 1)
        lines.append(f"[{n}] ({count} outlet{'s' if count != 1 else ''}) {it['title']}" + (f" — {body}" if body else ""))
    return "\n".join(lines)


def _prompt(items, day, market):
    return (f"{_market_text(market, day)}\n\nNews items from {day.isoformat()}, the whole day so far, most important first:\n"
            + _item_lines(items))


# ------------------------------------------------------------------ parsing / validation
def _parse(text):
    """Models often return almost-JSON (trailing commas, curly quotes, code fences). Try strict first, then repair."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in the answer")
    raw = re.sub(r'(?<=[א-ת])"(?=[א-ת])', "״", m.group(0))      # ארה"ב ends a JSON string early
    try:
        return json.loads(raw)
    except ValueError as first:
        fixed = (raw.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'"))
        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)                    # trailing commas
        fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", fixed)       # stray control characters
        try:
            return json.loads(fixed, strict=False)
        except ValueError:
            pos = getattr(first, "pos", 0)
            print(f"[ai_digest] unreadable JSON ({first}); around the error: ...{raw[max(0, pos - 60):pos + 60]!r}...")
            raise ValueError(f"unreadable JSON ({first})")


def _split_for_translation(text, limit=380):
    """The shared translator cuts at 400 characters, so translate long text sentence by sentence."""
    parts, cur = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text.strip()):
        if cur and len(cur) + len(s) + 1 > limit:
            parts.append(cur)
            cur = s
        else:
            cur = (cur + " " + s).strip()
    if cur:
        parts.append(cur)
    return parts or [text]


def _known_translations(prev):
    """English -> Hebrew pairs already in the previous digest, so unchanged text is never translated twice."""
    known = {}
    if not prev:
        return known

    def add(en, he):
        if en and he and en != he:
            known[en] = he
    add(prev.get("overview_en"), prev.get("overview_he"))
    for t in prev.get("topics") or []:
        for k in ("title", "summary", "impact"):
            add(t.get(k + "_en"), t.get(k + "_he"))
    return known


def _he(text, translate_fn, known):
    if not text:
        return ""
    if text in known:
        return known[text]
    try:
        return " ".join(translate_fn(p) for p in _split_for_translation(text))
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] translation failed ({type(e).__name__}) — Hebrew falls back to English")
        return text


def _ints(values, lo, hi):
    out = []
    for v in values or []:
        if isinstance(v, str):
            v = v.strip().lstrip("PpTt")          # accepts "T2" as well as 2
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if lo <= n <= hi and n not in out:
            out.append(n)
    return out


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    return {n.replace(",", "").rstrip(".") for n in _NUMBER.findall(text or "")}


def _rounded(n):
    """A number as it may be written: 4.36 as 4.4 or 4. As taught at school: 12.25 is 12.3 (Python's own format says 12.2,
    which rejected a right answer on 5.11.2026). Both are accepted."""
    out = set()
    try:
        x = Decimal(n)
        for digits in (Decimal("1"), Decimal("0.1"), Decimal("0.01")):
            out.add(str(x.quantize(digits, rounding=ROUND_HALF_UP)))
            out.add(str(x.quantize(digits, rounding=ROUND_HALF_EVEN)))
    except InvalidOperation:           # not a number after all, or far too long to matter
        pass
    return out


def allowed_numbers(market, items):
    """Every number the model was given, also rounded."""
    source = _market_text(market, date(2000, 1, 1)) + " " + " ".join(f"{i['title']} {i.get('body') or ''}" for i in items)
    out = set()
    for n in _numbers(source):
        out.add(n)
        out |= _rounded(n)
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


def invented_numbers(text, allowed):
    """Numbers in the text that the model was not given. Up to 12 can be a count or a quarter ("3 reasons", "Q3")."""
    return sorted(n for n in _numbers(text) if n not in allowed and not (n.isdigit() and int(n) <= 12))


def opens_with_the_move(overview, market):
    """On an extreme day: does the overview name an index and its number?"""
    if not market or market.get("move") != "extreme":
        return True
    wanted = {i["symbol"]: abs(i["change_pct"]) for i in market["indices"] if i["symbol"] in ("^GSPC", "^IXIC", "^DJI")}
    numbers = _numbers(overview)
    if not wanted or not re.search(r"S&P|Nasdaq|Dow\b|Wall Street", overview):
        return False
    return any(numbers & _rounded(str(x)) - {str(round(x))} for x in wanted.values())       # with its decimals, not just "4"


def _forced_lead(market):
    by = {i["symbol"]: i for i in market["indices"]}
    bits = [f"the {by[s]['name_en']} {'rose' if by[s]['change_pct'] > 0 else 'fell'} {abs(by[s]['change_pct']):.2f}%"
            for s in ("^GSPC", "^IXIC") if s in by]
    lead = " and ".join(bits) + "."
    return lead[0].upper() + lead[1:]


_PRICE_NAMES = (("^GSPC", r"s&p(?: 500)?"), ("^IXIC", r"nasdaq"), ("^DJI", r"dow(?: jones)?"), ("CL=F", r"oil|crude|wti|brent"),
                ("GC=F", r"gold"), ("BTC-USD", r"bitcoin"))
_FELL = r"fell|falls?|falling|drop(?:s|ped)?|tumbl\w+|plung\w+|slid|slump\w*|sank|sink\w*|declin\w+|lost|los(?:es|ing)"
_ROSE = r"rose|rises?|rising|climb\w*|jump\w*|surg\w+|soar\w*|gain\w*|rall\w+|advanc\w+"
MOVE_THAT_COUNTS = 1.0  # %; smaller, and "slipped" can be fair in a day that ended up


def contradicts_market(text, market):
    """v37c: what the text says against the day's numbers. 5.11.2026: "Oil prices tumbled" from headlines saved that day about the
    day before (-8.29% on 4.11), when oil rose 2.72%, on the same screen. Only the name right before the verb."""
    if not market or not market.get("same_day"):
        return None
    changes = {i["symbol"]: i["change_pct"] for i in market.get("indices") or []}
    for symbol, name in _PRICE_NAMES:
        pct = changes.get(symbol)
        if pct is None or abs(pct) < MOVE_THAT_COUNTS:
            continue
        verbs = _FELL if pct > 0 else _ROSE
        m = re.search(rf"\b(?:{name})\b(?:\s+\w+){{0,2}}\s+(?:{verbs})\b", text, re.I)
        if m:
            return f"'{m.group(0)}', and the MARKET NUMBERS say {pct:+.2f}% that day"
    return None


_SESSION_NAMES = r"S&P(?: 500)?|Nasdaq|Dow(?: Jones)?|Wall Street|(?:the )?(?:US )?(?:stock )?market|US stocks|chip stocks|VIX"
_SESSION_MOVE = re.compile(rf"\b(?:{_SESSION_NAMES})\b(?:\s+[\w()]+){{0,2}}\s+(?:{_FELL}|{_ROSE}|closed|ended|finished|led|surged)\b"
                           r"|\b(?:led|leading) (?:the )?(?:gains|losses|declines)\b|\bamong (?:the )?(?:large|big)(?:st)?[- ]?(?:cap|companies)\b", re.I)
_OTHER_TIME = re.compile(r"\b(?:futures|pre-?market|premarket|after-?hours|week(?:ly)?|friday|thursday|last|previous|earlier|"
                         r"since|year|month|ahead of)\b", re.I)


def session_told(text, market):
    """v38b: on a day with no session, the first sentence that tells one as if it happened that day. A sentence about futures,
    pre-market trading or the past week is fine: those are in the items."""
    if not market or market.get("same_day"):
        return None
    for s in re.split(r"(?<=[.!?])\s+", text or ""):
        m = _SESSION_MOVE.search(s)
        if m and not _OTHER_TIME.search(s):
            return m.group(0)
    return None


def misattributed_move(text, market, items):
    """v38c: a biggest mover's number in a sentence that does not name that ticker. 9.11.2026: "Nvidia led large-cap gains with an
    11.04% rise", AMD's number. A number that is also in the items may be another story's, so it is not checked."""
    mv = (market or {}).get("movers") or {}
    item_text = " ".join(f"{i['title']} {i.get('body') or ''}" for i in items)
    item_numbers = _numbers(item_text)
    for side in ("up", "down"):
        for m in mv.get(side) or []:
            exact = f"{abs(m['change_pct']):.2f}"
            shown = {x for x in _rounded(exact) | {exact} if "." in x}      # 11.04 or 11.0; a bare "11" is too common
            if shown & item_numbers:
                continue
            for s in re.split(r"(?<=[.!?])\s+", text or ""):
                if shown & _numbers(s) and not re.search(rf"\b{re.escape(m['ticker'])}\b", s):
                    return f"{abs(m['change_pct']):.2f}% is the move of {m['ticker']}, and the sentence does not name {m['ticker']}"
    return None


def _sentences_without_session(text, market):
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if not session_told(s, market)).strip()


def _topic_words(text):
    return _words(text) | {w.lower() for w in _SHORT_TERMS.findall(text)}


def _own_guess(text, source_text=""):
    """The first word of the model's own explanations that is not in what it cites. v37c: "tariff relief hopes" was a headline's,
    and three good answers were rejected for it."""
    for m in _OWN_GUESS.finditer(text):
        if m.group(0).lower() not in source_text.lower():
            return m.group(0)
    return None


def _sentences_without_guess(text, source_text=""):
    return " ".join(s for s in re.split(r"(?<=[.!?])\s+", text) if not _own_guess(s, source_text)).strip()


def _shorter(text, limit):
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if out and len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    return out


def clean_answer(text, items, market, strict=True):
    """The model's answer as the English part of the digest. ValueError (with a reason the model can act on) when rejected.
    strict=False, after the attempts ran out: what can be saved is saved. A topic with an invented number is dropped, a
    sentence with the model's own guess is cut, and an overview that left out an extreme move gets the real numbers in front of it.
    A source that shares no word with its topic is not shown (v37b: the AI selloff cited "Emirates saw its profits plunge")."""
    raw = _parse(text)
    allowed = allowed_numbers(market, items)
    overview = _ISO_DATE.sub("", str(raw.get("overview", ""))).strip()
    topics = []
    for t in (raw.get("topics") or [])[:MAX_TOPICS]:
        title, summary = _ISO_DATE.sub("", str(t.get("title", ""))).strip(), _ISO_DATE.sub("", str(t.get("summary", ""))).strip()
        impact = _ISO_DATE.sub("", str(t.get("impact", ""))).strip()
        about = _topic_words(f"{title} {summary}")
        sources, seen, rank = [], set(), len(items) + 1
        for n in _ints(t.get("items"), 1, len(items)):
            it = items[n - 1]
            k = it["url"] or it["title"]
            if k and k not in seen and _topic_words(it["title"]) & about:
                seen.add(k)
                rank = min(rank, n)
                sources.append({"title_en": it["title"], "title_he": it["title_he"], "url": it["url"], "category": it["tag"]})
        if sum(bool(re.search(w, summary, re.I)) for w in _ASSET_WORDS) >= 2:
            if strict:
                raise ValueError(f"the topic '{title}' only lists market numbers. The site shows them already; a topic must be a news story from the items")
            continue
        if not (title and summary and sources):
            if strict and title and summary:
                raise ValueError(f"the topic '{title}' has no item about that story. A topic must be a story told by the items, "
                                 "with the numbers of those items; drop it if there is none")
            continue
        cited = " ".join(s["title_en"] for s in sources)
        guess = _own_guess(f"{summary} {impact}", cited)
        if guess:
            if strict:
                raise ValueError(f"the topic '{title}' explains things on its own ('{guess}'). Write only what the items say")
            summary, impact = _sentences_without_guess(summary, cited), _sentences_without_guess(impact, cited)
            if not summary:
                continue
        told = session_told(f"{title}. {summary} {impact}", market)
        if told:
            if strict:
                raise ValueError(f"the topic '{title}' says '{told}', and the US market did not trade on this day. "
                                 "Tell the news of the day, without index or stock moves")
            summary, impact = _sentences_without_session(summary, market), _sentences_without_session(impact, market)
            if not summary or session_told(title, market):
                continue
        against = contradicts_market(f"{title}. {summary}", market)
        if against:
            if strict:
                raise ValueError(f"the topic '{title}' says {against}. That headline may be about an earlier day. "
                                 "Tell what happened on this day, or drop the topic")
            continue
        wrong = misattributed_move(f"{title}. {summary} {impact}", market, items)
        if wrong:
            if strict:
                raise ValueError(f"in the topic '{title}', {wrong}. Give each number to its own company, or leave it out")
            keep = lambda x: " ".join(s for s in re.split(r"(?<=[.!?])\s+", x) if not misattributed_move(s, market, items)).strip()
            summary, impact = keep(summary), keep(impact)
            if not summary or misattributed_move(title, market, items):
                continue
        extra = invented_numbers(f"{title} {summary} {impact}", allowed)
        if extra:
            if strict:
                raise ValueError(f"the topic '{title}' has numbers that are in neither the market numbers nor the items: "
                                 f"{extra[:4]}. Use only numbers you were given")
            continue
        topics.append({"title_en": title, "summary_en": summary, "impact_en": impact, "sources": sources[:MAX_SOURCES], "_rank": rank})
    if not overview or not topics:
        raise ValueError("the answer has no usable overview or topics: every topic needs the numbers of its items")
    # v37c: the items come most important first, so the order of the topics follows them. 5.11.2026 opened with oil and bonds.
    topics.sort(key=lambda t: t["_rank"])
    if strict and market and market.get("move") in ("extreme", "notable") and topics[0]["_rank"] != 1:
        raise ValueError("the day's move was large and no topic tells item [1], the most covered story. Make it the first topic")
    for t in topics:
        del t["_rank"]
    extra = invented_numbers(overview, allowed)
    if extra:
        raise ValueError(f"the overview has numbers that are in neither the market numbers nor the items: {extra[:4]}. "
                         "Use only numbers you were given")
    all_titles = " ".join(i["title"] for i in items)
    if strict and _own_guess(overview, all_titles):
        raise ValueError(f"the overview explains things on its own ('{_own_guess(overview, all_titles)}'). Write only what the items say")
    told = session_told(overview, market)
    if told:
        if strict:
            raise ValueError(f"the overview says '{told}', and the US market did not trade on this day. "
                             "Open with the most important news of the day instead")
        overview = _sentences_without_session(overview, market)
        if not overview:
            raise ValueError("the overview only told a session, on a day with no trading")
    wrong = misattributed_move(overview, market, items)
    if wrong:
        if strict:
            raise ValueError(f"in the overview, {wrong}. Give each number to its own company, or leave it out")
        overview = " ".join(s for s in re.split(r"(?<=[.!?])\s+", overview) if not misattributed_move(s, market, items)).strip() or overview
    against = contradicts_market(overview, market)
    if against:
        if strict:
            raise ValueError(f"the overview says {against}. That headline may be about an earlier day")
        overview = " ".join(s for s in re.split(r"(?<=[.!?])\s+", overview) if not contradicts_market(s, market))
    if market and market.get("intraday") and _ENDED.search(overview):
        if strict:
            raise ValueError("trading is still open, and the overview says the market closed or ended. Write 'so far'")
        overview = _ENDED.sub("is so far", overview)
    if strict and len(overview) > MAX_OVERVIEW:
        raise ValueError(f"the overview is {len(overview)} characters, at most 380. Do not list the numbers: the main move, its reason, nothing else")
    overview = _shorter(_sentences_without_guess(overview, all_titles), MAX_OVERVIEW) or overview
    if not opens_with_the_move(overview, market):
        if strict:
            raise ValueError("the MARKET NUMBERS say the day was extreme, and the overview does not say so. Open the overview "
                             "with the index and its number")
        overview = f"{_forced_lead(market)} {overview}"
    blob = " ".join([overview] + [f"{t['title_en']} {t['summary_en']} {t['impact_en']}" for t in topics])
    if _ADVICE.search(blob):
        raise ValueError("the answer reads like investment advice. Describe what happened, with no advice")
    return {"overview_en": overview, "topics": topics}


def _ask(system, user, clean, max_tokens, deadline, fallback=None):
    """A rejected answer goes back to the model with the reason (only one free model works at a time: v30c)."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last, last_text = "no answer", None
    for _ in range(ATTEMPTS):
        if time.time() > deadline:
            last = f"time budget used up — last: {last}"
            break
        try:
            text, model = llm_provider.chat(msgs, max_tokens=max_tokens, temperature=0.1)
        except RuntimeError as e:                    # timeout / the provider is busy — ask again
            last = str(e)
            continue
        last_text = text
        try:
            return clean(text), model
        except ValueError as e:
            last = f"answer rejected — {e}"
            print(f"[ai_digest] {model}: {last}")
            msgs = msgs[:2] + [{"role": "assistant", "content": text[:6000]},
                               {"role": "user", "content": f"Problem: {e}. Fix that and answer again with the complete JSON, nothing else."}]
    if fallback and last_text:
        return fallback(last_text), model
    raise RuntimeError(f"no usable answer — last: {last}")


def _english_blob(payload):
    return {"overview": payload["overview_en"],
            "topics": [{"title": t["title_en"], "summary": t["summary_en"], "impact": t["impact_en"]} for t in payload["topics"]]}


def clean_hebrew(text, en):
    raw = _parse(text)
    topics = raw.get("topics") or []
    if len(topics) != len(en["topics"]):
        raise ValueError(f"there are {len(en['topics'])} topics in the English and {len(topics)} in the answer: keep them all")
    allowed = _numbers(json.dumps(en, ensure_ascii=False))
    pairs = [("overview", str(raw.get("overview", "")).strip(), en["overview"])]
    out = {"overview": pairs[0][1], "topics": []}
    for he_t, en_t in zip(topics, en["topics"]):
        row = {k: str(he_t.get(k, "")).strip() for k in ("title", "summary", "impact")}
        out["topics"].append(row)
        pairs += [(k, row[k], en_t[k]) for k in ("title", "summary", "impact")]
    for field, he, en_text in pairs:
        if en_text and not re.search(r"[א-ת]", he):
            raise ValueError(f"the field '{field}' is not in Hebrew: {en_text[:60]}")
        if "�" in he:                                  # "המהלך נבע�", the live summary of 6.11.2026
            raise ValueError(f"the field '{field}' has a broken character. Write the sentence again")
        extra = _numbers(he) - allowed
        if extra:
            raise ValueError(f"the field '{field}' has numbers that are not in the English: {sorted(extra)[:4]}")
        left = [w for w in re.findall(r"\b[a-z][a-z-]{2,}\b", he)]      # names start with a capital; "typicall risk-off" does not
        if left:
            raise ValueError(f"the field '{field}' has English words left: {left[:4]}. Translate them into Hebrew")
    out["overview"] = fix_he(out["overview"])
    for t in out["topics"]:
        for k in t:
            t[k] = fix_he(t[k])
    return out


def _add_hebrew(payload, translate_fn, cache_from, deadline):
    """The model writes the Hebrew; the translator made "מכירת מניות שבבי לחץ" of a sell-off and "תעריפים" of rates.
    Same English as the last update -> no request. The free translator only if the model fails."""
    known = _known_translations(cache_from)
    en = _english_blob(payload)
    texts = [en["overview"]] + [t[k] for t in en["topics"] for k in ("title", "summary", "impact") if t[k]]
    he = None
    if not all(x in known for x in texts):
        try:
            he, _ = _ask(SYSTEM_HE, json.dumps(en, ensure_ascii=False), lambda t: clean_hebrew(t, en), 3000, deadline)
        except RuntimeError as e:
            print(f"[ai_digest] the Hebrew falls back to the translator ({e})")
    if he:
        payload["overview_he"] = he["overview"]
        for t, h in zip(payload["topics"], he["topics"]):
            t["title_he"], t["summary_he"], t["impact_he"] = h["title"], h["summary"], h["impact"]
        return payload
    payload["overview_he"] = _he(payload["overview_en"], translate_fn, known)
    for t in payload["topics"]:
        t["title_he"] = _he(t["title_en"], translate_fn, known)
        t["summary_he"] = _he(t["summary_en"], translate_fn, known)
        t["impact_he"] = _he(t["impact_en"], translate_fn, known)
    return payload


# ------------------------------------------------------------------ one day
@contextmanager
def _digest_models():
    """LLM_MODELS_DIGEST (a GitHub variable), if set, and a longer wait: a large model can take more than the usual minute."""
    names = {"LLM_TIMEOUT_SECONDS": DIGEST_CALL_TIMEOUT_SEC, "LLM_BUDGET_SECONDS": DIGEST_CALL_BUDGET_SEC}
    models = (os.environ.get("LLM_MODELS_DIGEST") or "").strip()
    if models:
        names["LLM_MODELS"] = models
    saved = {k: os.environ.get(k) for k in names}
    os.environ.update(names)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def after_close_update_due(row, day, now=None):
    """The first run after the close always updates, whatever the time since the last one."""
    et = (now or datetime.now(timezone.utc)).astimezone(_ET)
    if et.date() != day or et.weekday() >= 5 or et.time() < AFTER_CLOSE:
        return False
    return row["updated_at"].astimezone(_ET) < datetime.combine(day, AFTER_CLOSE, tzinfo=_ET)


def after_open_update_due(row, day, now=None):
    """v38b: a digest written before the open has no session in it; the first run after the open writes it again with one."""
    et = (now or datetime.now(timezone.utc)).astimezone(_ET)
    market = (row.get("payload") or {}).get("market") or {}
    return et.date() == day and et.weekday() < 5 and et.time() >= FIRST_BAR_BY and market.get("status") == "pre_open"


@contextmanager
def _connected(get_conn):
    conn = get_conn()
    try:
        yield conn
    finally:
        conn.close()


def _update_day(get_conn, day, translate_fn, force=False, final=False):
    """Create / update / close the digest of one day. Returns True if a new AI digest was saved.
    v37c: a connection for the reads and another for the write. The AI takes minutes and Neon suspends after 5 quiet ones:
    "SSL connection has been closed unexpectedly" at the INSERT threw the finished digest away (5.11.2026, manual run)."""
    min_hours = float(os.environ.get("AI_DIGEST_MIN_HOURS") or 3)
    with _connected(get_conn) as conn:
        row = _load_row(conn, day)
        if row and row["is_final"] and not force:
            return False
        close_due = bool(row) and (after_close_update_due(row, day) or after_open_update_due(row, day))
        if row and not force and not final and not close_due and row["age_h"] < min_hours:
            print(f"[ai_digest] {day}: last update is {row['age_h']:.1f}h old (< {min_hours}h) — skipping")
            return False
        if row and not force and not close_due:
            new = _count_new(conn, day, row["last_item_id"])
            if new < (1 if final else MIN_NEW):
                if final:
                    _mark_final(conn, day)
                    print(f"[ai_digest] {day}: closed (no new headlines since the last update)")
                else:
                    print(f"[ai_digest] {day}: only {new} new headlines (< {MIN_NEW}) — skipping")
                return False

        fetched = _fetch_titles(conn, day)
        if len(fetched) < MIN_ITEMS:
            print(f"[ai_digest] {day}: only {len(fetched)} headlines — too few for a digest")
            return False
        sel = _add_details(conn, rank_items(fetched))
        if not sel:
            print(f"[ai_digest] {day}: no headline left after the filters")
            return False
        market = market_facts(conn, day)
    print(f"[ai_digest] {day}: {len(fetched)} headlines, sending {len(sel)} stories, market move: "
          f"{market['move'] if market else 'no numbers'}; first: {sel[0]['title'][:80]}")

    deadline = time.time() + UPDATE_BUDGET_SEC
    with _digest_models():
        payload, model = _ask(SYSTEM, _prompt(sel, day, market), lambda t: clean_answer(t, sel, market), 3000, deadline,
                              fallback=lambda t: clean_answer(t, sel, market, strict=False))
        payload = _add_hebrew(payload, translate_fn, row["payload"] if row else None, deadline + 120)
    payload["market"] = market

    stamps = [it["ts"] for it in fetched if it.get("ts")]
    if stamps:
        payload["covers_from"], payload["covers_to"] = min(stamps).isoformat(), max(stamps).isoformat()
    payload["day"] = day.isoformat()
    item_count = len(fetched)
    last_id = max(it["id"] for it in fetched)
    updates = row["updates"] + 1 if row else 1
    payload["item_count"] = item_count
    payload["updates"] = updates

    with _connected(get_conn) as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO ai_daily_digests (day, updated_at, item_count, last_item_id, updates, since_rebuild, is_final, model, payload)
                       VALUES (%s, NOW(), %s, %s, %s, 0, %s, %s, %s)
                       ON CONFLICT (day) DO UPDATE SET updated_at = NOW(), item_count = EXCLUDED.item_count,
                           last_item_id = EXCLUDED.last_item_id, updates = EXCLUDED.updates,
                           since_rebuild = EXCLUDED.since_rebuild, is_final = EXCLUDED.is_final,
                           model = EXCLUDED.model, payload = EXCLUDED.payload""",
                    (day, item_count, last_id, updates, bool(final), model, json.dumps(payload, ensure_ascii=False)))
        conn.commit()
        cur.close()
    print(f"[ai_digest] {day}: saved{' (FINAL)' if final else ''} — {item_count} headlines, {len(payload['topics'])} topics, "
          f"update #{updates}, model {model}")
    print(f"[ai_digest] overview: {payload['overview_en']}")
    return True


def _mark_final(conn, day):
    cur = conn.cursor()
    cur.execute("UPDATE ai_daily_digests SET is_final = TRUE WHERE day = %s", (day,))
    conn.commit()
    cur.close()


# ------------------------------------------------------------------ entry points
def maybe_generate(get_conn, translate_fn, force=False):
    """Close finished days, then update today's digest if it is due. Never raises. True if something was saved."""
    try:
        if not llm_provider.is_configured():
            print("[ai_digest] off (LLM_API_KEY is not set)")
            return False
        saved = False
        today = today_local()
        with _connected(get_conn) as conn:
            ensure_table(conn)
            # 1) finish earlier days that were still open (normally just yesterday, once, right after midnight)
            cur = conn.cursor()
            cur.execute("SELECT day FROM ai_daily_digests WHERE NOT is_final AND day < %s ORDER BY day", (today,))
            open_days = [r[0] for r in cur.fetchall()]
            cur.close()
        for d in open_days:
            if (today - d).days <= CLOSE_LOOKBACK_DAYS:
                saved |= _update_day(get_conn, d, translate_fn, final=True)
            with _connected(get_conn) as conn:
                _mark_final(conn, d)
        # 2) today
        saved |= _update_day(get_conn, today, translate_fn, force=force)
        with _connected(get_conn) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM ai_daily_digests WHERE day < %s", (today - timedelta(days=KEEP_DAYS),))
            conn.commit()
            cur.close()
        return saved
    except llm_provider.LLMAuthError as e:
        print(f"[ai_digest] KEY REJECTED — check LLM_API_KEY ({e})")
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] not generated (non-fatal): {type(e).__name__}: {e}")
    return False


def one_day(get_conn, translate_fn, day):
    """Write one earlier day again (manual, to see a change on a day that already happened). Closed again when done."""
    if not llm_provider.is_configured():
        print("[ai_digest] off (LLM_API_KEY is not set)")
        return False
    with _connected(get_conn) as conn:
        ensure_table(conn)
    return _update_day(get_conn, day, translate_fn, force=True, final=day < today_local())


def backfill(get_conn, translate_fn, days=7):
    """Create a (final) digest for each of the last `days` days that has headlines but no digest yet."""
    if not llm_provider.is_configured():
        print("[ai_digest] off (LLM_API_KEY is not set)")
        return False
    made = 0
    today = today_local()
    with _connected(get_conn) as conn:
        ensure_table(conn)
    for k in range(days, 0, -1):
        d = today - timedelta(days=k)
        with _connected(get_conn) as conn:
            exists = _load_row(conn, d)
        if exists:
            print(f"[ai_digest] {d}: already has a digest — skipped")
            continue
        try:
            if _update_day(get_conn, d, translate_fn, force=True, final=True):
                made += 1
        except llm_provider.LLMAuthError:
            raise
        except Exception as e:  # noqa: BLE001
            print(f"[ai_digest] {d}: failed (non-fatal): {type(e).__name__}: {e}")
    print(f"[ai_digest] backfill done — {made} day(s) created")
    return made > 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    if "--probe" in sys.argv:
        sys.exit(0 if llm_provider.probe() else 1)
    import pipeline_b_news_aggregator as pb
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 7
        sys.exit(0 if backfill(pb.get_conn, pb.translate_to_hebrew, n) else 1)
    if "--day" in sys.argv and len(sys.argv) > sys.argv.index("--day") + 1 and sys.argv[sys.argv.index("--day") + 1]:
        sys.exit(0 if one_day(pb.get_conn, pb.translate_to_hebrew, date.fromisoformat(sys.argv[sys.argv.index("--day") + 1])) else 1)
    ok = maybe_generate(pb.get_conn, pb.translate_to_hebrew, force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
