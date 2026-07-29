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
                    two-tier keyword matrix (see matches_high_impact)
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
}

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
    # גאופוליטיקה (תמיד רלוונטי לשווקים)
    "War", "Sanctions", "Conflict", "Tension", "Crisis", "Attack",
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

def _has_market_context(title: str) -> bool:
    lowered = title.lower()
    return any(w in lowered for w in MARKET_CONTEXT_WORDS)


def matches_high_impact(title: str) -> Optional[str]:
    """
    Returns the single best-matching keyword for a headline, or None if
    it isn't capital-markets relevant.

    - Word-boundary matching only, so short/ambiguous keywords (AI, Meta)
      can't match as a substring inside unrelated words.
    - Tier 2 keywords (AI, Tech, individual company names, etc.) only
      count if the headline also contains a market-context word —
      filters out non-market stories that merely mention a company or
      "AI" in passing (e.g. a Nobel Prize / research announcement).
    - When several keywords match, picks the one appearing earliest in
      the headline, tie-broken by the longest (most specific) phrase —
      so the category tag reflects what the headline is actually about,
      not just keyword-list order.
    """
    candidates = []  # (position, -length, keyword)

    for kw in TIER1_KEYWORDS:
        m = re.search(r'\b' + re.escape(kw) + r'\b', title, re.IGNORECASE)
        if m:
            candidates.append((m.start(), -len(kw), kw))

    if _has_market_context(title):
        for kw in TIER2_KEYWORDS:
            m = re.search(r'\b' + re.escape(kw) + r'\b', title, re.IGNORECASE)
            if m:
                candidates.append((m.start(), -len(kw), kw))

    if not candidates:
        return None

    candidates.sort()
    return candidates[0][2]


def _clean_google_news_title(title: str) -> str:
    """Google News RSS titles end with ' - PublisherName' (e.g. 'Fed cuts
    rates - Reuters'). Strip that suffix so matching/translation work on the
    actual headline, not the source tag."""
    return re.sub(r'\s+-\s+[^-]{2,40}$', '', title).strip()


def fetch_raw_headlines() -> List[dict]:
    items = []
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                link = entry.get("link", "")
                if not link:
                    continue  # no usable source URL — can't dedupe or display it
                items.append({
                    "source": source_name,
                    "title": _clean_google_news_title(entry.get("title", "")),
                    "summary": entry.get("summary", ""),
                    "link": link,
                })
        except Exception as e:
            print(f"[warn] failed to fetch feed {source_name}: {e}")
    return items


# ------------------------------------------------------------------
# Glossary of common macro/finance terms with a fixed, correct Hebrew
# rendering. Google's free auto-translate sometimes renders these
# inconsistently depending on surrounding sentence context (e.g. a
# "jobless" reference coming out oddly). We mask these phrases with
# placeholders before translation and restore the correct Hebrew
# afterward, so the important terms are always right regardless of
# what Google does with the rest of the sentence.
# Ordered longest-phrase-first so e.g. "Jobless Claims" is masked
# before a shorter overlapping term could grab part of it.
# ------------------------------------------------------------------
TERM_GLOSSARY = {
    "Non-Farm Payrolls": "משרות חוץ-חקלאיות",
    "Nonfarm Payrolls": "משרות חוץ-חקלאיות",
    "Jobless Claims": "תביעות דמי אבטלה",
    "Initial Claims": "תביעות אבטלה ראשוניות",
    "Employment Report": "דוח התעסוקה",
    "Jobs Report": "דוח התעסוקה",
    "Interest Rate": "ריבית",
    "Rate Hike": "העלאת ריבית",
    "Rate Cut": "הורדת ריבית",
    "Federal Reserve": "הפדרל ריזרב",
    "Yield Curve": "עקום התשואות",
    "Bull Market": "שוק שורי",
    "Bear Market": "שוק דובי",
    "Stock Market": "שוק המניות",
    "Quarterly Results": "דוחות רבעוניים",
    "Recession": "מיתון",
    "Inflation": "אינפלציה",
    "Bankruptcy": "פשיטת רגל",
    "CPI": "מדד המחירים לצרכן",
    "PPI": "מדד מחירי היצרן",
    "GDP": "התמ\"ג",
    "IPO": "הנפקה ראשונית",
    # --- expanded terms (broader coverage = fewer awkward literal renderings) ---
    "Rate Decision": "החלטת ריבית",
    "Basis Points": "נקודות בסיס",
    "Soft Landing": "נחיתה רכה",
    "Hard Landing": "נחיתה קשה",
    "Stagflation": "סטגפלציה",
    "Quantitative Easing": "הרחבה כמותית",
    "Quantitative Tightening": "כיווץ כמותי",
    "Balance Sheet": "מאזן",
    "Trade Deficit": "גירעון סחר",
    "Trade Surplus": "עודף סחר",
    "Current Account": "חשבון שוטף",
    "Consumer Confidence": "אמון הצרכנים",
    "Consumer Sentiment": "סנטימנט הצרכנים",
    "Retail Sales": "מכירות קמעונאיות",
    "Manufacturing PMI": "מדד מנהלי הרכש בתעשייה",
    "Services PMI": "מדד מנהלי הרכש בשירותים",
    "Durable Goods": "מוצרים בני-קיימא",
    "Housing Starts": "התחלות בנייה",
    "Existing Home Sales": "מכירות בתים קיימים",
    "Debt Ceiling": "תקרת החוב",
    "Government Shutdown": "השבתת הממשלה",
    "Credit Rating": "דירוג אשראי",
    "Credit Downgrade": "הורדת דירוג אשראי",
    "Short Squeeze": "לחיצת שורטים",
    "Circuit Breaker": "שובר מעגלים",
    "Stock Split": "פיצול מניות",
    "Share Buyback": "רכישה עצמית של מניות",
    "Buyback Program": "תוכנית רכישה עצמית",
    "Dividend Cut": "קיצוץ דיבידנד",
    "Profit Warning": "אזהרת רווח",
    "Guidance Cut": "הורדת תחזית",
    "Beat Expectations": "עקפו את התחזיות",
    "Miss Expectations": "לא עמדו בתחזיות",
    "Price Target": "מחיר יעד",
    "Analyst Upgrade": "שדרוג המלצת אנליסט",
    "Analyst Downgrade": "הורדת המלצת אנליסט",
    "Antitrust": "הגבלים עסקיים",
    "Regulatory Approval": "אישור רגולטורי",
    "Supply Chain": "שרשרת האספקה",
    "Chip Shortage": "מחסור בשבבים",
    "Export Controls": "מגבלות ייצוא",
    "Tariffs": "מכסי מסחר",
    "Trade War": "מלחמת סחר",
    "Ceasefire": "הפסקת אש",
    "Peace Talks": "שיחות שלום",
    "Central Bank": "בנק מרכזי",
    "Monetary Policy": "מדיניות מוניטרית",
    "Fiscal Policy": "מדיניות פיסקלית",
    "Safe Haven": "מקלט בטוח",
    "Risk-Off": "בריחה מסיכון",
    "Risk-On": "נטילת סיכון",
    "All-Time High": "שיא כל הזמנים",
    "Record High": "שיא היסטורי",
    "Record Low": "שפל היסטורי",
}
GLOSSARY_TERMS = sorted(TERM_GLOSSARY.items(), key=lambda kv: -len(kv[0]))


def translate_to_hebrew(text: str) -> str:
    """Translate to Hebrew using Google Translate via direct HTTP request — free, no API key.

    Known finance/macro terms are masked with placeholders first and
    restored with a fixed correct Hebrew phrase afterward, instead of
    trusting Google's sentence-dependent guess for those terms. This is
    the only translation path — no paid API involved anywhere.
    """
    try:
        import urllib.parse, urllib.request, json as _json
        clean = re.sub(r'<[^>]+>', '', text).strip()[:400]

        placeholders = {}
        working = clean
        for idx, (phrase, hebrew) in enumerate(GLOSSARY_TERMS):
            token = f"Q{idx}Q"
            working, count = re.subn(r'\b' + re.escape(phrase) + r'\b', token, working, flags=re.IGNORECASE)
            if count:
                placeholders[token] = hebrew

        encoded = urllib.parse.quote(working)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=he&dt=t&q={encoded}"
        req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = _json.loads(resp.read())
        result = ''.join(part[0] for part in data[0] if part[0])
        if not result:
            return text

        for token, hebrew in placeholders.items():
            result = result.replace(token, hebrew)
        return result
    except Exception as e:
        print(f"[warn] translation failed: {e}")
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
    # Without this, the same recurring story got re-inserted as a fresh row
    # on every hourly run and flooded the "latest 25" view — that's why the
    # top news list looked stuck on 1-2 stories for days. A UNIQUE constraint
    # + ON CONFLICT DO NOTHING (see upsert_macro_news) stops that at the DB
    # level. Wrapped in DO/EXCEPTION so re-running this on an already-patched
    # DB doesn't error out.
    cur.execute("""
        DO $$
        BEGIN
            ALTER TABLE macro_news ADD CONSTRAINT macro_news_source_url_key UNIQUE (source_url);
        EXCEPTION
            WHEN duplicate_object THEN NULL;
        END $$;
    """)
    conn.commit()
    cur.close()
    conn.close()


def upsert_macro_news(category_tag: str, summary_he: str,
                      summary_en: str, impact_level: str, source_url: str,
                      hook_he: str = "") -> bool:
    """Returns True if a new row was inserted, False if this source_url was
    already saved (duplicate story — skipped instead of piling up)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO macro_news (category_tag, summary_he, summary_en, impact_level, source_url, hook_he, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (source_url) DO NOTHING
        RETURNING id
        """,
        (category_tag, summary_he, summary_en, impact_level, source_url, hook_he,
         datetime.now(timezone.utc).isoformat()),
    )
    inserted = cur.fetchone() is not None
    conn.commit()
    cur.close()
    conn.close()
    return inserted


def run_pipeline_b():
    init_db()
    raw_items = fetch_raw_headlines()
    saved = 0
    skipped_dupe = 0
    skipped_no_match = 0

    for item in raw_items:
        matched_kw = matches_high_impact(item["title"])
        if not matched_kw:
            skipped_no_match += 1
            continue

        tag = KEYWORD_TO_TAG.get(matched_kw, "מאקרו")
        impact = "Critical" if matched_kw in CRITICAL_KEYWORDS else "High"

        title = item["title"]
        headline_he = translate_to_hebrew(title)
        hook_he = get_category_hook(tag)
        summary_en = title  # keep original English as-is

        was_new = upsert_macro_news(tag, headline_he, summary_en, impact, item["link"], hook_he)
        if was_new:
            saved += 1
        else:
            skipped_dupe += 1

    print(f"Pipeline B complete. {saved} new high-impact items saved, "
          f"{skipped_dupe} duplicates skipped, {skipped_no_match} not market-relevant.")


if __name__ == "__main__":
    run_pipeline_b()
