"""
Pipeline B - Macro & World News Aggregator
===========================================
Runs continuously / hourly.

Flow:
  1. Ingest      -> pull RSS feeds from top-tier financial outlets
  2. Filter      -> keep only high-impact macro headlines (keyword matrix)
  3. AI condense -> rewrite into ONE clear practical sentence, in target language
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

# Top-tier financial RSS feeds - expanded list for more coverage
RSS_FEEDS = {
    "Reuters Business":     "https://feeds.reuters.com/reuters/businessNews",
    "Reuters World":        "https://feeds.reuters.com/Reuters/worldNews",
    "Investing.com":        "https://www.investing.com/rss/news.rss",
    "MarketWatch":          "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Yahoo Finance":        "https://finance.yahoo.com/news/rssindex",
    "CNBC":                 "https://www.cnbc.com/id/100003114/device/rss/rss.html",
}

# Broad keyword matrix — includes both macro AND general market/tech/geo news
HIGH_IMPACT_KEYWORDS = [
    # מאקרו קלאסי
    "CPI", "PPI", "Federal Reserve", "Fed ", "Interest Rate", "Rate Hike",
    "Rate Cut", "Inflation", "OPEC", "Employment Report", "Non-Farm Payrolls",
    "Jobs Report", "GDP", "Recession", "Yield Curve",
    # גאופוליטיקה
    "War", "Sanctions", "Conflict", "Tension", "Crisis", "Attack",
    # שווקים כלליים
    "Stock Market", "S&P 500", "Nasdaq", "Dow Jones", "Bull Market", "Bear Market",
    "Earnings", "Quarterly Results", "Revenue", "Profit", "Guidance",
    "Merger", "Acquisition", "IPO", "Bankruptcy",
    # טכנולוגיה ו-AI
    "Artificial Intelligence", "AI", "Semiconductor", "Tech", "Chip",
    "Apple", "Microsoft", "Google", "Amazon", "Nvidia", "Meta",
    # אנרגיה וסחורות
    "Oil", "Crude", "Natural Gas", "Gold", "Commodity",
    # קריפטו
    "Bitcoin", "Crypto", "Ethereum", "Regulation",
]

KEYWORD_TO_TAG = {
    "CPI": "CPI", "PPI": "PPI",
    "Federal Reserve": "ריבית", "Fed ": "ריבית",
    "Interest Rate": "ריבית", "Rate Hike": "ריבית", "Rate Cut": "ריבית",
    "Inflation": "אינפלציה", "OPEC": "סחורות",
    "War": "גאופוליטי", "Sanctions": "גאופוליטי", "Conflict": "גאופוליטי",
    "Tension": "גאופוליטי", "Crisis": "גאופוליטי", "Attack": "גאופוליטי",
    "Employment Report": "תעסוקה", "Non-Farm Payrolls": "תעסוקה",
    "Jobs Report": "תעסוקה", "GDP": "GDP", "Recession": "מאקרו",
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
    "Gold": "סחורות", "Commodity": "סחורות",
    "Bitcoin": "קריפטו", "Crypto": "קריפטו", "Ethereum": "קריפטו",
    "Regulation": "רגולציה",
}

CRITICAL_KEYWORDS = {
    "War", "Sanctions", "Attack", "Federal Reserve", "Rate Hike", "Rate Cut",
    "Recession", "Bankruptcy", "Crisis",
}

def matches_high_impact(title: str) -> Optional[str]:
    """Returns the matched keyword if the title is high-impact, else None."""
    for kw in HIGH_IMPACT_KEYWORDS:
        if re.search(re.escape(kw), title, re.IGNORECASE):
            return kw
    return None


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


def translate_to_hebrew(text: str) -> str:
    """Translate to Hebrew using deep-translator — free, no API key needed."""
    try:
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source='en', target='he').translate(text[:500])
    except Exception as e:
        print(f"[warn] translation failed: {e}")
        return text  # fallback: return original English


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
