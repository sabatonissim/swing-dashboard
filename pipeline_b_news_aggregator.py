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
import sqlite3
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
from openai import OpenAI

DB_PATH = os.environ.get("SWING_DB_PATH", "swing_dashboard.db")

# Top-tier financial RSS feeds. Add/replace with licensed feeds as needed -
# check each outlet's terms of use before scraping in production.
RSS_FEEDS = {
    "Reuters Business": "https://www.reutersagency.com/feed/?best-topics=business-finance",
    "Investing.com":     "https://www.investing.com/rss/news.rss",
}

# High-impact keyword matrix (case-insensitive). Company-specific gossip
# and low-tier news are discarded if none of these appear in the title.
HIGH_IMPACT_KEYWORDS = [
    "CPI", "PPI", "Federal Reserve", "Fed ", "Interest Rate", "Rate Hike",
    "Rate Cut", "Inflation", "OPEC", "War", "Sanctions", "Employment Report",
    "Non-Farm Payrolls", "Jobs Report", "GDP", "Recession", "Yield Curve",
]

KEYWORD_TO_TAG = {
    "CPI": "CPI", "PPI": "PPI", "Federal Reserve": "Interest Rate",
    "Fed ": "Interest Rate", "Interest Rate": "Interest Rate",
    "Rate Hike": "Interest Rate", "Rate Cut": "Interest Rate",
    "Inflation": "Inflation", "OPEC": "Commodities", "War": "Geo",
    "Sanctions": "Geo", "Employment Report": "Employment",
    "Non-Farm Payrolls": "Employment", "Jobs Report": "Employment",
    "GDP": "GDP", "Recession": "Macro", "Yield Curve": "Rates",
}

CRITICAL_KEYWORDS = {"War", "Sanctions", "Federal Reserve", "Rate Hike", "Rate Cut"}

NEWS_SYSTEM_PROMPT = """You are a financial news condenser for a swing-trading dashboard.
You rewrite raw headlines/snippets into a single, neutral, factual sentence. Never add
a buy/sell opinion, price target, or trading instruction - purely report what happened
and its stated market implication (e.g. "reduces inflation concern"), not what a trader
should do about it.
"""


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


def condense_news(raw_text: str, target_language: str) -> str:
    """
    Calls OpenAI to translate/condense/rewrite into exactly one practical sentence.
    Requires OPENAI_API_KEY in the environment.
    """
    client = OpenAI()
    user_prompt = (
        f"Translate, condense, and rewrite this financial news into exactly ONE "
        f"practical, clear sentence in {target_language} suitable for a swing trader. "
        f"Stay strictly factual/analytical - no trading advice. "
        f"Example output format: 'US CPI rose 2.4%, matching forecasts, easing inflation concern.'\n\n"
        f"Raw news:\n{raw_text}"
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": NEWS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
    )
    return response.choices[0].message.content.strip()


def init_db(db_path: str = DB_PATH):
    conn = sqlite3.connect(db_path)
    with open(os.path.join(os.path.dirname(__file__), "database_schema.sql")) as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def upsert_macro_news(conn: sqlite3.Connection, category_tag: str, summary_he: str,
                       summary_en: str, impact_level: str, source_url: str):
    conn.execute(
        """
        INSERT INTO macro_news (category_tag, summary_he, summary_en, impact_level, source_url, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (category_tag, summary_he, summary_en, impact_level, source_url,
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def run_pipeline_b():
    conn = init_db()
    raw_items = fetch_raw_headlines()
    saved = 0

    for item in raw_items:
        matched_kw = matches_high_impact(item["title"])
        if not matched_kw:
            continue  # discard company-specific / low-tier news

        tag = KEYWORD_TO_TAG.get(matched_kw, "Macro")
        impact = "Critical" if matched_kw in CRITICAL_KEYWORDS else "High"

        raw_text = f"{item['title']}. {item['summary']}"
        summary_he = condense_news(raw_text, "Hebrew")
        summary_en = condense_news(raw_text, "English")

        upsert_macro_news(conn, tag, summary_he, summary_en, impact, item["link"])
        saved += 1

    print(f"Pipeline B complete. {saved} high-impact items saved to {DB_PATH}.")
    conn.close()


if __name__ == "__main__":
    run_pipeline_b()
