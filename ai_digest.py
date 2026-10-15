"""AI daily digest — turns the last 24h of macro_news headlines into a short factual summary.

Runs at the end of pipeline_b (GitHub Actions) — NEVER per website visit, so cost/rate limits stay tiny
(at most one AI call every AI_DIGEST_MIN_HOURS hours, default 3). The website only READS the stored
result (api_server.py -> /api/daily-digest -> field `ai_summary`).

Safe by design:
  * no LLM_API_KEY            -> does nothing (the site keeps the rule-based digest)
  * AI call fails / bad JSON  -> nothing is saved, the previous digest stays, pipeline_b is unaffected
  * facts only                -> the model sees ONLY the numbered headlines; every topic must cite item numbers,
                                 and topics with no valid source are dropped. Output that reads like
                                 investment advice is rejected.
  * English first, then Hebrew through the project's existing free translator (passed in by the caller)

Manual test:   python ai_digest.py --force
"""
import json
import os
import re
import sys

import llm_provider

WINDOW_HOURS = 24
MAX_ITEMS = 50
MIN_ITEMS = 5
BODY_CHARS = 240

SYSTEM_PROMPT = """You are a neutral news editor for a market-data website. You write a short daily digest.
STRICT RULES:
- Use ONLY facts stated in the numbered news items you are given. Never add numbers, names, causes, dates or background that are not in the items.
- No investment advice: never say or imply to buy, sell or hold anything; no price targets; no predictions about what prices will do.
- Neutral, plain language. If the items are thin or contradict each other, say so briefly instead of guessing.
- Group related items into 3 to 5 topics, most important/most covered first.
Reply with ONLY a JSON object (no markdown, no commentary) in exactly this shape:
{"overview": "2-3 sentences describing the day in markets/macro news, max 350 characters",
 "topics": [{"title": "max 70 characters", "summary": "1-2 sentences, max 280 characters", "items": [numbers of the supporting items]}]}"""

_ADVICE = re.compile(r"\b(you should|we recommend|recommend(?:ed|s)? (?:to )?(?:buy|sell)|should (?:buy|sell|hold)|time to (?:buy|sell)|buy now|sell now)\b", re.I)


def ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS ai_digests (
        id SERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        window_hours INTEGER,
        item_count INTEGER,
        model TEXT,
        payload TEXT)""")
    conn.commit()
    cur.close()


def _fetch_items(conn):
    cur = conn.cursor()
    cur.execute("""SELECT id, category_tag, impact_level, summary_en, body_en, source_url, summary_he, timestamp
                   FROM macro_news
                   WHERE timestamp > NOW() - make_interval(hours => %s)
                   ORDER BY (impact_level = 'Critical') DESC, timestamp DESC
                   LIMIT %s""", (WINDOW_HOURS, MAX_ITEMS))
    rows = cur.fetchall()
    cur.close()
    return [dict(id=r[0], tag=r[1], impact=r[2], title=r[3] or "", body=r[4] or "", url=r[5] or "",
                 title_he=r[6] or "", ts=r[7]) for r in rows]


def _prompt(items):
    lines = []
    for n, it in enumerate(items, 1):
        body = re.sub(r"\s+", " ", it["body"]).strip()[:BODY_CHARS]
        lines.append(f"[{n}] ({it['tag']}, {it['impact']}) {it['title']}" + (f" — {body}" if body else ""))
    return "News items from the last 24 hours:\n" + "\n".join(lines)


def _parse(text):
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in the answer")
    return json.loads(m.group(0))


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


def _he(text, translate_fn):
    if not text:
        return ""
    try:
        return " ".join(translate_fn(p) for p in _split_for_translation(text))
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] translation failed ({type(e).__name__}) — Hebrew falls back to English")
        return text


def _build_payload(raw, items, translate_fn):
    overview = str(raw.get("overview", "")).strip()
    topics = []
    for t in raw.get("topics", [])[:5]:
        nums = []
        for n in t.get("items", []):
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(items) and n not in nums:
                nums.append(n)
        title, summary = str(t.get("title", "")).strip(), str(t.get("summary", "")).strip()
        if not (title and summary and nums):
            continue                                        # no valid source -> dropped
        topics.append({"title_en": title, "summary_en": summary,
                       "sources": [{"title_en": items[n - 1]["title"], "title_he": items[n - 1]["title_he"],
                                    "url": items[n - 1]["url"], "category": items[n - 1]["tag"]} for n in nums[:3]]})
    if not overview or not topics:
        raise ValueError("answer had no usable overview/topics")
    blob = overview + " " + " ".join(t["title_en"] + " " + t["summary_en"] for t in topics)
    if _ADVICE.search(blob):
        raise ValueError("answer looked like investment advice — rejected")
    payload = {"overview_en": overview, "overview_he": _he(overview, translate_fn), "topics": topics}
    for t in topics:
        t["title_he"] = _he(t["title_en"], translate_fn)
        t["summary_he"] = _he(t["summary_en"], translate_fn)
    return payload


def maybe_generate(get_conn, translate_fn, force=False):
    """Generate + store a digest if the AI is configured and one is due. Never raises."""
    try:
        if not llm_provider.is_configured():
            print("[ai_digest] off (LLM_API_KEY is not set)")
            return False
        min_hours = float(os.environ.get("AI_DIGEST_MIN_HOURS") or 3)
        conn = get_conn()
        try:
            ensure_table(conn)
            cur = conn.cursor()
            cur.execute("SELECT EXTRACT(EPOCH FROM (NOW() - MAX(generated_at))) / 3600 FROM ai_digests")
            age_h = cur.fetchone()[0]
            cur.close()
            if not force and age_h is not None and age_h < min_hours:
                print(f"[ai_digest] last digest is {age_h:.1f}h old (< {min_hours}h) — skipping")
                return False
            items = _fetch_items(conn)
            if len(items) < MIN_ITEMS:
                print(f"[ai_digest] only {len(items)} items in the window — skipping")
                return False
            text, model = llm_provider.chat(
                [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _prompt(items)}],
                max_tokens=900, temperature=0.2)
            payload = _build_payload(_parse(text), items, translate_fn)
            cur = conn.cursor()
            cur.execute("INSERT INTO ai_digests (window_hours, item_count, model, payload) VALUES (%s, %s, %s, %s)",
                        (WINDOW_HOURS, len(items), model, json.dumps(payload, ensure_ascii=False)))
            cur.execute("DELETE FROM ai_digests WHERE generated_at < NOW() - INTERVAL '7 days'")
            conn.commit()
            cur.close()
            print(f"[ai_digest] saved — {len(items)} items, {len(payload['topics'])} topics, model {model}")
            return True
        finally:
            conn.close()
    except llm_provider.LLMAuthError as e:
        print(f"[ai_digest] KEY REJECTED — check LLM_API_KEY ({e})")
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] not generated (non-fatal): {type(e).__name__}: {e}")
    return False


if __name__ == "__main__":
    import pipeline_b_news_aggregator as pb
    ok = maybe_generate(pb.get_conn, pb.translate_to_hebrew, force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
