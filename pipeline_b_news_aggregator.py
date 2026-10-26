"""
Pipeline B - Macro & World News Aggregator
===========================================
Runs continuously / hourly.

Flow:
  1. Ingest      -> pull RSS feeds from top-tier financial outlets, plus two
                    broad Google News topic-search feeds for volume/diversity
                    (a single outlet's feed can go quiet; a live topic search
                    across all of Google News rarely does)
  2. Filter      -> keep only capital-markets-relevant headlines using a
                    two-tier keyword matrix, weighted by frequency across
                    title+summary (see classify_headline)
  3. Translate   -> Hebrew via free Google Translate, with a large glossary of
                    macro/finance terms forced to a correct fixed rendering
                    (see translate_to_hebrew). No paid API involved.
  4. Hook        -> a free, static "why this matters" one-liner looked up by
                    category tag (see get_category_hook) — not per-article
                    generated, but zero cost per headline.
  5. DB upsert   -> macro_news table, with ON CONFLICT DO NOTHING on
                    source_url so the same recurring story stops flooding the
                    "latest N" view on every hourly re-run.

Requirements (pip install --break-system-packages):
    feedparser
"""

import os
import re
import html
import psycopg2
from datetime import datetime, timezone
from typing import List, Optional

import feedparser

DB_URL = os.environ.get("SWING_DB_PATH") or os.environ.get("DATABASE_URL")

# פידים אמינים ועובדים שמביאים חדשות פיננסיות גדולות
#
# NOTE: Reuters killed its public RSS feeds back in 2020 — feeds.reuters.com
# is dead and was silently returning zero items every run. That, combined
# with having only ~8 single-outlet feeds total, is why the feed dried up to
# the same 1-2 recurring stories: when a couple of feeds go quiet there's
# nothing else to fall back on. Fixed by:
#   1. Swapping the dead Reuters feeds for a Google News RSS search scoped to
#      reuters.com — still genuine Reuters stories, but served through a feed
#      that's actually alive.
#   2. Adding two broad Google News topic-search feeds, which aggregate the
#      same story across dozens of outlets instead of relying on any single
#      site's feed staying up — this is the main fix for "not enough news".
RSS_FEEDS = {
    "Reuters (via Google News)": "https://news.google.com/rss/search?q=when:12h+allinurl:reuters.com&hl=en-US&gl=US&ceid=US:en",
    "CNBC Top News":         "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CNBC Markets":          "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "CNBC World":            "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "Yahoo Finance":         "https://finance.yahoo.com/news/rssindex",
    "Seeking Alpha Markets": "https://seekingalpha.com/market_currents.xml",
    "MarketWatch":           "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "Investing.com":         "https://www.investing.com/rss/news_301.rss",
    "Markets & Macro (Google News)": "https://news.google.com/rss/search?q=(stocks+OR+earnings+OR+%22Federal+Reserve%22+OR+inflation+OR+%22interest+rates%22+OR+%22rate+cut%22+OR+%22rate+hike%22)+when:8h&hl=en-US&gl=US&ceid=US:en",
    "Geopolitics & Markets (Google News)": "https://news.google.com/rss/search?q=(war+OR+sanctions+OR+%22oil+prices%22+OR+OPEC+OR+conflict+OR+tariffs)+when:8h&hl=en-US&gl=US&ceid=US:en",
    # --- Official primary sources (US government / central bank) — real article links, real summaries, no licensing issue ---
    "Federal Reserve - Monetary Policy": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "Federal Reserve - Speeches & Testimony": "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
    "BLS - CPI": "https://www.bls.gov/feed/cpi.rss",
    "BLS - PPI": "https://www.bls.gov/feed/ppi.rss",
    "BLS - Employment Situation": "https://www.bls.gov/feed/empsit.rss",
    "BLS - JOLTS": "https://www.bls.gov/feed/jolts.rss",
    "BLS - Employment Cost Index": "https://www.bls.gov/feed/eci.rss",
}

# For the official feeds the category is known from the feed itself (a CPI release has no need to be guessed
# from keywords), so (publisher, tag, impact) are fixed here. Only entries from the last `max_age_h` hours are
# taken — otherwise the first run would dump the feed's whole back-catalogue in as "new" news.
# Feeds without a forced tag (speeches) still go through the normal keyword classifier.
OFFICIAL_FEEDS = {
    "Federal Reserve - Monetary Policy":      dict(publisher="Federal Reserve", tag="ריבית",     impact="Critical", max_age_h=72),
    "Federal Reserve - Speeches & Testimony": dict(publisher="Federal Reserve", tag=None,         impact=None,       max_age_h=72),
    "BLS - CPI":                              dict(publisher="BLS",             tag="אינפלציה",  impact="Critical", max_age_h=72),
    "BLS - PPI":                              dict(publisher="BLS",             tag="אינפלציה",  impact="High",     max_age_h=72),
    "BLS - Employment Situation":             dict(publisher="BLS",             tag="תעסוקה",    impact="Critical", max_age_h=72),
    "BLS - JOLTS":                            dict(publisher="BLS",             tag="תעסוקה",    impact="High",     max_age_h=72),
    "BLS - Employment Cost Index":            dict(publisher="BLS",             tag="תעסוקה",    impact="High",     max_age_h=72),
}
# Government sites reject anonymous scrapers; identify ourselves honestly.
OFFICIAL_USER_AGENT = os.environ.get("NEWS_USER_AGENT") or "SwingDesk/1.0 (+https://swing-desk-tau.vercel.app)"

# ------------------------------------------------------------------
# Keyword matrix — split into two tiers:
#
#   TIER 1 ("always relevant"): unambiguous macro/finance terms. If one
#   of these appears anywhere in the headline, it's capital-markets news.
#
#   TIER 2 ("needs market context"): broad/ambiguous words (AI, Tech,
#   individual company names, etc.) that also show up constantly in
#   pure science/culture/product stories with nothing to do with
#   markets (e.g. a Nobel Prize story that happens to mention "AI").
#   These only count as a hit if the SAME headline also contains a
#   market-context word (stock, shares, Nasdaq, earnings, rally, %...).
#
# All matching uses word boundaries (\b) so short/ambiguous tokens like
# "AI" or "Meta" can't match as a substring inside unrelated words
# (e.g. "metabolic", "domain", "explain") — this was the source of the
# irrelevant Nobel-Prize-style stories showing up before.
# ------------------------------------------------------------------

TIER1_KEYWORDS = [
    # מאקרו קלאסי
    "CPI", "PPI", "Federal Reserve", "Fed", "Interest Rate", "Rate Hike",
    "Rate Cut", "Inflation", "OPEC", "Employment Report", "Non-Farm Payrolls",
    "Nonfarm Payrolls", "Jobs Report", "Jobless Claims", "Initial Claims",
    "GDP", "Recession", "Yield Curve",
    # גאופוליטיקה חד-משמעית (סנקציות תמיד כלכליות/מדיניות במהותן — לא צריך שער הקשר)
    "Sanctions",
    # שווקים כלליים
    "Stock Market", "S&P 500", "Nasdaq", "Dow Jones", "Bull Market", "Bear Market",
    "Earnings", "Quarterly Results", "Revenue", "Profit", "Guidance",
    "Merger", "Acquisition", "IPO", "Bankruptcy",
    # סחורות ואנרגיה
    "Oil", "Crude", "Natural Gas", "Gold", "Commodity",
    # קריפטו ורגולציה
    "Bitcoin", "Ethereum", "Crypto",
]

# Ambiguous terms: only counted as high-impact if a market-context word
# also appears in the same headline (see MARKET_CONTEXT_WORDS below).
TIER2_KEYWORDS = [
    "Artificial Intelligence", "AI", "Semiconductor", "Tech", "Chip",
    "Apple", "Microsoft", "Google", "Amazon", "Nvidia", "Meta",
    "Regulation",
]

# "War"/"Conflict"/"Tension"/"Crisis"/"Attack" are broad enough that they
# show up constantly with nothing to do with markets — local political
# disputes, a personal-life "crisis" story, a sports "attack" play, a
# "tension headache" (see EXCLUSION_PHRASES for that last one). Even a
# genuine geopolitical conflict story often has no real market angle
# unless it's tied to something with actual economic stakes. Gated the
# same way Tier 2 is, but against economic/strategic-impact words
# (GEO_CONTEXT_WORDS below) rather than pure trading vocabulary — this
# was the main source of "the geopolitical category is full of
# irrelevant stuff".
GEO_AMBIGUOUS_KEYWORDS = ["War", "Conflict", "Tension", "Crisis", "Attack"]

GEO_CONTEXT_WORDS = [
    "oil", "energy", "trade", "tariff", "tariffs", "embargo",
    "supply chain", "opec", "economy", "economic", "gdp", "market", "markets",
    "stocks", "stock", "dollar", "currency", "central bank", "inflation",
    "exports", "imports", "shipping", "sanctions",
]

MARKET_CONTEXT_WORDS = [
    "stock", "stocks", "shares", "share price", "market", "markets",
    "nasdaq", "s&p", "dow", "rally", "surge", "plunge", "tumble", "slump",
    "earnings", "ipo", "trading", "investor", "investors", "wall street",
    "valuation", "revenue", "guidance", "buyback", "downgrade", "upgrade",
    "analyst", "analysts", "%", "quarter", "profit", "sell-off", "selloff",
]

KEYWORD_TO_TAG = {
    "CPI": "CPI", "PPI": "PPI",
    "Federal Reserve": "ריבית", "Fed": "ריבית",
    "Interest Rate": "ריבית", "Rate Hike": "ריבית", "Rate Cut": "ריבית",
    "Inflation": "אינפלציה", "OPEC": "סחורות", "OPEC+": "סחורות",
    "War": "גאופוליטי", "Sanctions": "גאופוליטי", "Conflict": "גאופוליטי",
    "Tension": "גאופוליטי", "Crisis": "גאופוליטי", "Attack": "גאופוליטי",
    "Employment Report": "תעסוקה", "Non-Farm Payrolls": "תעסוקה",
    "Nonfarm Payrolls": "תעסוקה", "Jobs Report": "תעסוקה",
    "Jobless Claims": "תעסוקה", "Initial Claims": "תעסוקה",
    "GDP": "GDP", "Recession": "מאקרו",
    "Yield Curve": "אג\"ח", "Stock Market": "שוק", "S&P 500": "שוק",
    "Nasdaq": "שוק", "Dow Jones": "שוק", "Bull Market": "שוק", "Bear Market": "שוק",
    "Earnings": "דוחות", "Quarterly Results": "דוחות", "Revenue": "דוחות",
    "Profit": "דוחות", "Guidance": "דוחות",
    "Merger": "M&A", "Acquisition": "M&A", "IPO": "IPO", "Bankruptcy": "פשיטת רגל",
    "Artificial Intelligence": "AI", "AI": "AI", "Semiconductor": "שבבים",
    "Tech": "טכנולוגיה", "Chip": "שבבים",
    "Apple": "טכנולוגיה", "Microsoft": "טכנולוגיה", "Google": "טכנולוגיה",
    "Amazon": "טכנולוגיה", "Nvidia": "שבבים", "Meta": "טכנולוגיה",
    "Oil": "אנרגיה", "Crude": "אנרגיה", "Natural Gas": "אנרגיה",
    "Gold": "סחורות",
    "Bitcoin": "קריפטו", "Crypto": "קריפטו", "Ethereum": "קריפטו",
    "Regulation": "רגולציה",
}

CRITICAL_KEYWORDS = {
    "War", "Sanctions", "Attack", "Federal Reserve", "Rate Hike", "Rate Cut",
    "Recession", "Bankruptcy", "Crisis",
}

# Idioms/contexts where a keyword above is a common FALSE POSITIVE — these
# veto a match even though a keyword technically appears in the text. This
# is meant to grow over time: add a phrase here whenever you spot a junk
# headline that slipped through, rather than trying to guess every case
# up front.
EXCLUSION_PHRASES = [
    "heart attack", "panic attack", "shark attack", "attack ad", "attacking play",
    "identity crisis", "midlife crisis", "opioid crisis",
    "price war", "bidding war", "culture war", "war of words", "star wars",
    "muscle tension", "sexual tension", "tension headache",
    "poker chip", "chocolate chip", "fish and chips", "chip on his shoulder", "chip on her shoulder",
    "gold medal", "golden retriever", "gold standard",
    "tech support", "meta-analysis", "metaverse",
]


def _is_excluded(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in EXCLUSION_PHRASES)

def _has_market_context(title: str) -> bool:
    lowered = title.lower()
    return any(w in lowered for w in MARKET_CONTEXT_WORDS)


def _has_geo_context(title: str) -> bool:
    lowered = title.lower()
    return any(w in lowered for w in GEO_CONTEXT_WORDS)


def classify_headline(title: str, summary: str) -> Optional[dict]:
    """Returns {"tag": ..., "impact": ...}, or None if not market-relevant.

    Two improvements over the original single-title, first-match approach:
    1. Uses the RSS summary/description too, not just the title — titles
       are often vague or clickbait-y, and the summary usually has the
       actual substance of the story.
    2. Picks the category by which tag's keywords appear MOST (Tier-1 hits
       weighted double), not "whichever keyword happens to appear earliest
       in the title". A headline mentioning "Fed" once in passing but
       "earnings"/"revenue"/"guidance" three times is an earnings story,
       not a rates story — the old approach would have tagged it wrong.
    """
    combined = f"{title} {summary}"
    if _is_excluded(combined):
        return None

    tag_scores: dict = {}
    earliest_position: dict = {}
    matched_critical = False

    for kw in TIER1_KEYWORDS:
        for m in re.finditer(r'\b' + re.escape(kw) + r'\b', combined, re.IGNORECASE):
            tag = KEYWORD_TO_TAG.get(kw, "מאקרו")
            tag_scores[tag] = tag_scores.get(tag, 0) + 2
            earliest_position.setdefault(tag, m.start())
            if kw in CRITICAL_KEYWORDS:
                matched_critical = True

    if _has_market_context(combined):
        for kw in TIER2_KEYWORDS:
            for m in re.finditer(r'\b' + re.escape(kw) + r'\b', combined, re.IGNORECASE):
                tag = KEYWORD_TO_TAG.get(kw, "מאקרו")
                tag_scores[tag] = tag_scores.get(tag, 0) + 1
                earliest_position.setdefault(tag, m.start())

    if _has_geo_context(combined):
        for kw in GEO_AMBIGUOUS_KEYWORDS:
            for m in re.finditer(r'\b' + re.escape(kw) + r'\b', combined, re.IGNORECASE):
                tag = KEYWORD_TO_TAG.get(kw, "גאופוליטי")
                tag_scores[tag] = tag_scores.get(tag, 0) + 2
                earliest_position.setdefault(tag, m.start())
                if kw in CRITICAL_KEYWORDS:
                    matched_critical = True

    if not tag_scores:
        return None

    best_tag = max(tag_scores.items(), key=lambda kv: (kv[1], -earliest_position[kv[0]]))[0]
    return {"tag": best_tag, "impact": "Critical" if matched_critical else "High"}


def _clean_google_news_title(title: str) -> str:
    """Google News RSS titles end with ' - PublisherName' (e.g. 'Fed cuts
    rates - Reuters'). Strip that suffix so matching/translation work on the
    actual headline, not the source tag."""
    return re.sub(r'\s+-\s+[^-]{2,40}$', '', title).strip()


# Several feeds are different sections of one outlet — show the outlet, not the section.
_PUBLISHER_BY_FEED = {
    "CNBC Top News": "CNBC", "CNBC Markets": "CNBC", "CNBC World": "CNBC",
    "Seeking Alpha Markets": "Seeking Alpha",
}


def _publisher_name(feed_name: str, entry, raw_title: str) -> str:
    """The outlet a headline really comes from. Google News feeds list the outlet in the entry's
    <source> tag (and as a ' - Outlet' suffix on the title); for them the feed name itself ('Google News')
    would be useless to the reader. Direct outlet feeds use the outlet's own name."""
    if "Google News" in feed_name:
        try:
            name = (entry.get("source") or {}).get("title", "")
        except Exception:  # noqa: BLE001
            name = ""
        if not name:
            m = re.search(r'\s+-\s+([^-]{2,40})$', raw_title or "")
            name = m.group(1).strip() if m else ""
        return name[:60]
    return _PUBLISHER_BY_FEED.get(feed_name, feed_name)[:60]


def _clean_rss_body(raw_summary: str, title: str) -> str:
    """The RSS <description>/<summary> field, cleaned into plain readable
    text — this is publisher-provided teaser text, meant to be excerpted
    (that's what RSS summaries are FOR), not full-article scraping. Strips
    HTML tags/entities (Google News wraps its summary in an <a> tag plus a
    trailing <font> with the source name). Returns "" when there's nothing
    beyond the headline itself worth showing — several of our feeds are
    Google News search queries whose "summary" is just the title re-wrapped
    in a link, so showing it as a separate "body" would just repeat the
    headline back with no added value."""
    if not raw_summary:
        return ""
    text = re.sub(r'<[^>]+>', ' ', raw_summary)   # strip tags
    text = html.unescape(text)                     # &amp; -> &, etc.
    text = re.sub(r'\s+', ' ', text).strip()
    if not text or len(text) < 15:
        return ""
    cleaned_title = re.sub(r'\s+', ' ', title).strip()
    if cleaned_title and len(text.replace(cleaned_title, '').strip()) < 20:
        return ""
    return text


def _entry_age_hours(entry):
    """Hours since the entry was published, or None when the feed gives no usable date."""
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if not t:
        return None
    try:
        dt = datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001
        return None
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def fetch_raw_headlines() -> List[dict]:
    items = []
    for source_name, url in RSS_FEEDS.items():
        official = OFFICIAL_FEEDS.get(source_name)
        try:
            feed = feedparser.parse(url, agent=OFFICIAL_USER_AGENT) if official else feedparser.parse(url)
            for entry in feed.entries:
                link = entry.get("link", "")
                if not link:
                    continue  # no usable source URL — can't dedupe or display it
                if official:
                    age = _entry_age_hours(entry)
                    if age is None or age > official["max_age_h"]:
                        continue  # old back-catalogue entry (or undated) — not news
                item = {
                    "source": source_name,
                    "publisher": official["publisher"] if official else _publisher_name(source_name, entry, entry.get("title", "")),
                    # the " - Outlet" suffix exists only on Google News titles; elsewhere a trailing " - September 2026" is real
                    "title": _clean_google_news_title(entry.get("title", "")) if "Google News" in source_name else entry.get("title", "").strip(),
                    "summary": entry.get("summary", ""),
                    "link": link,
                }
                if official and official["tag"]:
                    item["forced"] = {"tag": official["tag"], "impact": official["impact"]}
                items.append(item)
        except Exception as e:
            print(f"[warn] failed to fetch feed {source_name}: {e}")
    return items


# Translation fixes (v18): idioms rewritten before Google, known bad Hebrew renderings fixed after — shared with
# api_server.py; see translation_fixes.py for the lists and for why the old placeholder masking was dropped.
from translation_fixes import prep_en as _prep_en, fix_he as _fix_he


def translate_to_hebrew(text: str) -> str:
    """Translate to Hebrew — free, no API key, with two independent
    providers tried in order and clear logging of exactly what failed.

    Both providers are unofficial/free endpoints, which occasionally
    block or rate-limit requests coming from cloud/datacenter IP ranges
    (which is exactly what a GitHub Actions runner is) — that shows up
    as every headline silently staying in English, with only a generic
    "[warn] translation failed" in the logs and no way to tell which
    provider or HTTP status caused it. This version logs the specific
    status/exception per provider, and falls back to a second provider
    instead of giving up immediately, so a block on ONE of them doesn't
    mean 100% of headlines go untranslated.

    The whole sentence is translated (idioms rewritten first, known bad
    renderings fixed after) — see translation_fixes.py for why the old
    placeholder masking was dropped.
    """
    import urllib.parse, urllib.request, urllib.error, json as _json, random, time

    clean = re.sub(r'<[^>]+>', '', text).strip()[:400]
    if not clean:
        return text

    def _restore(result: str) -> str:
        return _fix_he(result)

    encoded = urllib.parse.quote(_prep_en(clean))

    # A brief random delay before calling out — free/unofficial endpoints
    # rate-limit by request *pattern* as much as by volume, and this runs
    # hourly for a whole batch of headlines back-to-back, which looks
    # exactly like the bursty traffic they're built to block. Spacing
    # calls out a little, plus retrying each provider once on failure
    # before giving up on it, clears a lot of transient blocks that a
    # single immediate attempt wouldn't.
    time.sleep(random.uniform(0.4, 1.2))

    def _try_google_gtx() -> Optional[str]:
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=he&dt=t&q={encoded}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': '*/*',
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        return ''.join(part[0] for part in data[0] if part[0]) or None

    def _try_mymemory() -> Optional[str]:
        url = f"https://api.mymemory.translated.net/get?q={encoded}&langpair=en|he"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        result = (data.get("responseData") or {}).get("translatedText")
        status = data.get("responseStatus")
        if result and status in (200, "200"):
            return result
        print(f"[warn] translation (MyMemory): no usable translatedText — responseStatus={status}")
        return None

    for provider_name, provider_fn in (("Google gtx", _try_google_gtx), ("MyMemory", _try_mymemory)):
        for attempt in (1, 2):
            try:
                result = provider_fn()
                if result:
                    return _restore(result)
                print(f"[warn] translation ({provider_name}) attempt {attempt}: empty result")
            except urllib.error.HTTPError as e:
                print(f"[warn] translation ({provider_name}) attempt {attempt} failed: HTTP {e.code}")
            except Exception as e:
                print(f"[warn] translation ({provider_name}) attempt {attempt} failed: {type(e).__name__}: {e}")
            if attempt == 1:
                time.sleep(random.uniform(1.5, 3.0))  # brief backoff before the same provider's 2nd try
        print(f"[warn] translation: {provider_name} failed twice, trying next provider")

    print(f"[warn] translation: all providers failed, keeping original English text: {clean[:80]!r}")
    return text


# ------------------------------------------------------------------
# "Why it matters" hooks — a free alternative to per-article LLM generation.
# Rather than calling a paid API for every headline (which would run 24/7
# and add up cost-wise), this looks up a short, genuinely informative
# one-liner keyed to the story's category tag. It won't be as specific as
# a per-article summary, but it directly explains why *this kind* of news
# moves markets, at zero marginal cost per headline.
# ------------------------------------------------------------------
CATEGORY_HOOKS = {
    "CPI": "מדד המחירים הוא הנתון שהפד עוקב אחריו הכי מקרוב לצורך החלטות ריבית — הפתעה בו יכולה להזיז את כל השוק באותו יום.",
    "PPI": "מדד מחירי היצרן מקדים לרוב את ה-CPI בחודש-חודשיים, ולכן משמש כאינדיקטור מוקדם ללחצי אינפלציה.",
    "ריבית": "שינוי בריבית משפיע ישירות על עלות המימון של חברות ועל תמחור מניות הצמיחה והטכנולוגיה.",
    "אינפלציה": "אינפלציה גבוהה מהצפוי מגבירה את הסיכוי להעלאות ריבית נוספות, מה שפוגע בדרך כלל במניות הצמיחה.",
    "גאופוליטי": "אירועים גאופוליטיים משפיעים לרוב על מחירי הנפט, הזהב וה-VIX (מדד הפחד) בטווח הקצר.",
    "תעסוקה": "דוח תעסוקה חזק מדי יכול להרתיע את הפד מהורדת ריבית, בעוד דוח חלש מגביר את הציפייה להקלה מוניטרית.",
    "GDP": "קצב הצמיחה של הכלכלה משפיע על ציפיות הרווחים של החברות ועל מדיניות הריבית של הפד.",
    "מאקרו": "נתון מאקרו-כלכלי רחב שיכול להשפיע על הסנטימנט הכללי בשוק המניות.",
    "אג\"ח": "עקום תשואות הפוך נחשב היסטורית לאינדיקטור מקדים למיתון, ולכן נעקב מקרוב על ידי משקיעים.",
    "שוק": "תנועה במדדים המובילים משקפת את הסנטימנט הכללי של המשקיעים כלפי הכלכלה כולה.",
    "דוחות": "דוחות רבעוניים חזקים או חלשים מהצפוי יכולים להזיז מניה בעשרות אחוזים ביום המסחר שלמחרת.",
    "M&A": "עסקאות מיזוג ורכישה משפיעות לרוב הן על מניית הרוכשת (בדרך כלל יורדת) והן על מניית הנרכשת (בדרך כלל עולה בחדות).",
    "IPO": "הנפקות ראשוניות משפיעות על הסנטימנט כלפי הסקטור כולו, במיוחד כשמדובר בחברה בולטת.",
    "פשיטת רגל": "פשיטת רגל של חברה גדולה יכולה להשפיע על ספקים, מתחרים ומשקיעים בסקטור כולו.",
    "AI": "חדשות סביב בינה מלאכותית מזיזות לרוב את כל סקטור הטכנולוגיה והשבבים, לא רק את החברה הספציפית.",
    "שבבים": "סקטור השבבים רגיש מאוד לשינויים בביקוש הגלובלי ולמגבלות יצוא, ולכן מגיב בחדות לחדשות כאלה.",
    "טכנולוגיה": "חברות טכנולוגיה גדולות מהוות משקל משמעותי במדדים המובילים, כך שחדשות עליהן משפיעות על השוק הרחב.",
    "אנרגיה": "מחירי האנרגיה משפיעים הן על האינפלציה הכללית והן ישירות על רווחיות חברות האנרגיה.",
    "קריפטו": "שוק הקריפטו נוטה לתגובות חדות וסינכרוניות לחדשות, גם בהשוואה לשוק המניות.",
    "רגולציה": "שינויי רגולציה יכולים לשנות את מודל הרווחיות של סקטור שלם, לא רק של חברה בודדת.",
}

def get_category_hook(tag: str) -> str:
    return CATEGORY_HOOKS.get(tag, "")


def get_conn():
    return psycopg2.connect(DB_URL)


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS macro_news (
            id SERIAL PRIMARY KEY,
            category_tag TEXT NOT NULL,
            summary_he TEXT,
            summary_en TEXT,
            impact_level TEXT,
            source_url TEXT,
            timestamp TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    # ADD COLUMN IF NOT EXISTS patches a table that was already created
    # before this column existed (CREATE TABLE IF NOT EXISTS above is a
    # no-op once the table exists, so this is needed for already-deployed DBs).
    cur.execute("ALTER TABLE macro_news ADD COLUMN IF NOT EXISTS hook_he TEXT;")
    cur.execute("ALTER TABLE macro_news ADD COLUMN IF NOT EXISTS body_en TEXT;")
    cur.execute("ALTER TABLE macro_news ADD COLUMN IF NOT EXISTS body_he TEXT;")
    cur.execute("ALTER TABLE macro_news ADD COLUMN IF NOT EXISTS source_name TEXT;")   # real publisher shown on the site (v15)
    conn.commit()

    # --- one-time cleanup, required before the UNIQUE constraint below can
    # actually be added ---
    # The table already has duplicate source_url values: for ~2 days the
    # same recurring story was re-inserted as a fresh row on every hourly
    # run with zero dedup. Adding a UNIQUE constraint on a column that still
    # contains duplicates raises a hard error (a *different* error than
    # "constraint already exists" — this is Postgres refusing to build the
    # constraint at all) which was never caught and crashed every run. Fixed
    # by cleaning up first, every time (a no-op once already cleaned):
    #   1. blank '' source_url values -> NULL (Postgres treats every NULL as
    #      distinct, so they never collide under a UNIQUE constraint)
    #   2. delete older duplicate rows, keeping the earliest id per URL
    try:
        cur.execute("UPDATE macro_news SET source_url = NULL WHERE source_url = '';")
        cur.execute("""
            DELETE FROM macro_news a USING macro_news b
            WHERE a.id > b.id
              AND a.source_url = b.source_url
              AND a.source_url IS NOT NULL;
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[warn] macro_news duplicate cleanup failed (non-fatal): {e}")

    # Wrapped in DO/EXCEPTION so re-running this on an already-patched DB
    # doesn't error out, AND wrapped in a Python try/except so that even an
    # unexpected failure here can never take down the whole pipeline run —
    # worst case, dedup just silently doesn't happen this run.
    try:
        cur.execute("""
            DO $$
            BEGIN
                ALTER TABLE macro_news ADD CONSTRAINT macro_news_source_url_key UNIQUE (source_url);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $$;
        """)
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[warn] could not ensure macro_news.source_url UNIQUE constraint (non-fatal): {e}")

    cur.close()
    conn.close()


def upsert_macro_news(category_tag: str, summary_he: str,
                      summary_en: str, impact_level: str, source_url: str,
                      hook_he: str = "", body_en: str = "", body_he: str = "", source_name: str = "") -> bool:
    """Returns True if a new row was inserted, False if this source_url was
    already saved (duplicate story — skipped instead of piling up).

    Tries the fast atomic ON CONFLICT path first (needs the UNIQUE
    constraint from init_db). If that constraint somehow isn't present —
    e.g. init_db's cleanup above hit an edge case — falls back to a plain
    check-then-insert instead of crashing the run on every single item.
    """
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO macro_news (category_tag, summary_he, summary_en, impact_level, source_url, hook_he, body_en, body_he, source_name, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_url) DO NOTHING
            RETURNING id
            """,
            (category_tag, summary_he, summary_en, impact_level, source_url, hook_he, body_en, body_he, source_name or None,
             datetime.now(timezone.utc).isoformat()),
        )
        inserted = cur.fetchone() is not None
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[warn] ON CONFLICT insert failed, falling back to manual dedup check: {e}")
        cur.execute("SELECT 1 FROM macro_news WHERE source_url = %s LIMIT 1", (source_url,))
        if cur.fetchone() is not None:
            inserted = False
        else:
            cur.execute(
                """
                INSERT INTO macro_news (category_tag, summary_he, summary_en, impact_level, source_url, hook_he, body_en, body_he, source_name, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (category_tag, summary_he, summary_en, impact_level, source_url, hook_he, body_en, body_he, source_name or None,
                 datetime.now(timezone.utc).isoformat()),
            )
            inserted = True
        conn.commit()
    finally:
        cur.close()
        conn.close()
    return inserted


def retry_failed_translations(max_rows: int = 30):
    """Self-healing pass, run at the start of every pipeline run: because
    upsert_macro_news uses ON CONFLICT (source_url) DO NOTHING, a story
    whose translation failed when it was FIRST seen stays stuck in
    English forever — it never gets touched again once the row exists.
    This finds recent rows where summary_he has no Hebrew characters at
    all (a reliable sign translate_to_hebrew fell through to returning
    the original English) and gives them one more shot. Capped at
    max_rows per run so a bad stretch doesn't turn into a giant retry
    burst on top of the run's normal translation load.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, summary_en FROM macro_news
        WHERE timestamp > NOW() - INTERVAL '72 hours'
          AND summary_en IS NOT NULL
          AND summary_he !~ '[\u05d0-\u05ea]'
        ORDER BY timestamp DESC
        LIMIT %s
        """,
        (max_rows,),
    )
    stuck = cur.fetchall()
    if not stuck:
        cur.close()
        conn.close()
        return
    fixed = 0
    for row_id, summary_en in stuck:
        retried = translate_to_hebrew(summary_en)
        if re.search(r'[\u05d0-\u05ea]', retried):
            cur.execute("UPDATE macro_news SET summary_he = %s WHERE id = %s", (retried, row_id))
            conn.commit()
            fixed += 1
    cur.close()
    conn.close()
    print(f"[info] translation retry: fixed {fixed}/{len(stuck)} previously-stuck-in-English headlines.")


def run_pipeline_b():
    init_db()
    retry_failed_translations()
    raw_items = fetch_raw_headlines()
    saved = 0
    skipped_dupe = 0
    skipped_no_match = 0

    for item in raw_items:
        classification = item.get("forced") or classify_headline(item["title"], item.get("summary", ""))
        if not classification:
            skipped_no_match += 1
            continue

        tag = classification["tag"]
        impact = classification["impact"]

        title = item["title"]
        headline_he = translate_to_hebrew(title)
        hook_he = get_category_hook(tag)
        summary_en = title  # keep original English as-is

        body_en = _clean_rss_body(item.get("summary", ""), title)
        body_he = translate_to_hebrew(body_en) if body_en else ""

        was_new = upsert_macro_news(tag, headline_he, summary_en, impact, item["link"], hook_he, body_en, body_he, item.get("publisher", ""))
        if was_new:
            saved += 1
        else:
            skipped_dupe += 1

    print(f"Pipeline B complete. {saved} new high-impact items saved, "
          f"{skipped_dupe} duplicates skipped, {skipped_no_match} not market-relevant.")

    # AI daily digest (optional): only runs when LLM_API_KEY is set, at most every few hours,
    # and can never break this pipeline. See ai_digest.py / llm_provider.py.
    try:
        import ai_digest
        ai_digest.maybe_generate(get_conn, translate_to_hebrew)
    except Exception as e:
        print(f"[ai_digest] skipped (non-fatal): {type(e).__name__}: {e}")


if __name__ == "__main__":
    run_pipeline_b()
