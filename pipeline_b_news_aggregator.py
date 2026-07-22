"""
Pipeline B - Macro & World News Aggregator
===========================================
Runs continuously / hourly.

Flow:
  1. Ingest      -> pull RSS feeds from top-tier financial outlets
  2. Filter      -> keep only capital-markets-relevant headlines using a
                    two-tier keyword matrix (see matches_high_impact)
  3. Translate   -> Hebrew via free Google Translate, with a glossary of
                    macro/finance terms forced to a correct fixed rendering
  4. DB upsert   -> macro_news table

Requirements (pip install --break-system-packages):
    feedparser openai python-dotenv
"""

import os
import re
import psycopg2
from datetime import datetime, timezone
from typing import List, Optional

import feedparser

DB_URL = os.environ.get("SWING_DB_PATH") or os.environ.get("DATABASE_URL")

# פידים אמינים ועובדים שמביאים חדשות פיננסיות גדולות
RSS_FEEDS = {
    "Reuters Top News":      "https://feeds.reuters.com/reuters/topNews",
    "Reuters Business":      "https://feeds.reuters.com/reuters/businessNews",
    "CNBC Top News":         "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "CNBC Markets":          "https://www.cnbc.com/id/20910258/device/rss/rss.html",
    "Yahoo Finance":         "https://finance.yahoo.com/news/rssindex",
    "Seeking Alpha Markets": "https://seekingalpha.com/market_currents.xml",
    "MarketWatch":           "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "Investing.com":         "https://www.investing.com/rss/news_301.rss",
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


def fetch_raw_headlines() -> List[dict]:
    items = []
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries:
                items.append({
                    "source": source_name,
                    "title": entry.get("title", ""),
                    "summary": entry.get("summary", ""),
                    "link": entry.get("link", ""),
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
}
GLOSSARY_TERMS = sorted(TERM_GLOSSARY.items(), key=lambda kv: -len(kv[0]))


def translate_to_hebrew(text: str) -> str:
    """Translate to Hebrew using Google Translate via direct HTTP request — free, no API key.

    Known finance/macro terms are masked with placeholders first and
    restored with a fixed correct Hebrew phrase afterward, instead of
    trusting Google's sentence-dependent guess for those terms.
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
    conn.commit()
    cur.close()
    conn.close()


def upsert_macro_news(category_tag: str, summary_he: str,
                      summary_en: str, impact_level: str, source_url: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO macro_news (category_tag, summary_he, summary_en, impact_level, source_url, timestamp)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (category_tag, summary_he, summary_en, impact_level, source_url,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    cur.close()
    conn.close()


def run_pipeline_b():
    init_db()
    raw_items = fetch_raw_headlines()
    saved = 0

    for item in raw_items:
        matched_kw = matches_high_impact(item["title"])
        if not matched_kw:
            continue

        tag = KEYWORD_TO_TAG.get(matched_kw, "מאקרו")
        impact = "Critical" if matched_kw in CRITICAL_KEYWORDS else "High"

        title = item["title"]
        summary_he = translate_to_hebrew(title)
        summary_en = title  # keep original English as-is

        upsert_macro_news(tag, summary_he, summary_en, impact, item["link"])
        saved += 1

    print(f"Pipeline B complete. {saved} high-impact items saved to Postgres.")


if __name__ == "__main__":
    run_pipeline_b()
