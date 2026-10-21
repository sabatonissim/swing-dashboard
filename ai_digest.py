"""AI daily digest — ONE running summary per DAY, updated as the day's news arrives.

How it works (v16):
  * The "day" is a calendar day in DIGEST_TZ (default America/New_York = the US market day; change with the
    DIGEST_TZ variable, e.g. Asia/Jerusalem).
  * First run of a day: the model summarises the day's headlines so far (Critical first, then spread over the day).
  * Every later run: the model receives the digest it already wrote + ONLY the headlines that arrived since, and
    returns the UPDATED digest for the whole day (keeps what is still true, folds in the new, re-ranks topics).
  * Every REBUILD_EVERY updates the day is rebuilt from scratch from all its headlines, so small drifts cannot
    pile up over a long day.
  * When the day ends, one last update closes it ("final") and it stays in the history table, so earlier days
    can be read later (api_server.py -> /api/daily-digest?day=YYYY-MM-DD and /api/daily-digest/days).

Runs at the end of pipeline_b (GitHub Actions) — NEVER per website visit. The website only READS the stored result.

Safe by design (unchanged from before):
  * no LLM_API_KEY            -> does nothing (the site keeps the rule-based digest)
  * AI call fails / bad JSON  -> nothing is saved, the previous digest stays, pipeline_b is unaffected
  * facts only                -> the model sees ONLY the numbered headlines (and its own previous, already
                                 validated digest); every topic must cite sources, topics with none are dropped.
                                 Output that reads like investment advice is rejected.
  * English first, then Hebrew through the project's existing free translator (passed in by the caller);
    text that did not change since the last update is NOT translated again.

Manual:  python ai_digest.py --force          (rebuild today's digest from scratch now)
         python ai_digest.py                  (normal update, same as the pipeline does)
         python ai_digest.py --backfill 7     (create summaries for the last 7 days that have none)
         python ai_digest.py --probe          (which AI models work for my key right now?)
"""
import json
import os
import re
import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import llm_provider

MAX_ITEMS = 80          # headlines sent when a day is (re)built from scratch
MAX_NEW = 60            # headlines sent in one incremental update
MIN_ITEMS = 5           # a day needs at least this many headlines before its first digest
MIN_NEW = 5             # an incremental update needs at least this many new headlines
REBUILD_EVERY = 8       # every N-th update rebuilds the day from scratch (prevents drift)
BODY_CHARS = 240
MAX_SOURCES = 4
KEEP_DAYS = 90
CLOSE_LOOKBACK_DAYS = 3  # past days that are still closed with one last AI update (older ones are just marked final)


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


# ------------------------------------------------------------------ prompts
_RULES = """STRICT RULES:
- Base every statement on the information you are given. Never add numbers, names, causes, dates or background that are not there.
- "impact" explains in plain words which parts of the market (sectors, asset classes, currencies, interest rates) this kind of news typically matters for, and why. Word it with "may", "could" or "tends to". It is context, NOT a forecast: never predict a price level or a direction, never give a target.
- No investment advice: never say or imply that anyone should buy, sell or hold anything.
- "takeaways" are neutral observations about the day's themes (what dominated, what is uncertain, where items disagree). They are not recommendations.
- "watch" lists up to 3 things worth keeping an eye on, taken from the information (for example an upcoming decision or data release mentioned in it). Use an empty list if there are none.
- If the information is thin or contradicts itself, say so briefly instead of guessing.
- Group related items into 3 to 6 topics, most important / most covered first.
Reply with ONLY a valid JSON object (no markdown, no commentary). Use straight double quotes, no trailing commas, and never put a double quote character inside a text value."""

_SHAPE_FULL = """Exact shape:
{"overview": "2-3 sentences describing the whole day in markets/macro news, max 350 characters",
 "topics": [{"title": "max 70 characters", "summary": "1-2 sentences, max 280 characters", "impact": "1-2 sentences, max 280 characters", "items": [numbers of the supporting items]}],
 "takeaways": ["max 200 characters each, 2 to 3 entries"],
 "watch": ["max 120 characters each, 0 to 3 entries"]}"""

_SHAPE_UPDATE = """Exact shape:
{"overview": "2-3 sentences describing the WHOLE day so far, max 350 characters",
 "topics": [{"title": "max 70 characters", "summary": "1-2 sentences, max 280 characters", "impact": "1-2 sentences, max 280 characters", "from_topics": [numbers of the current-digest topics this builds on, [] if brand new], "items": [numbers of the NEW items that support it, [] if none]}],
 "takeaways": ["max 200 characters each, 2 to 3 entries"],
 "watch": ["max 120 characters each, 0 to 3 entries"]}"""

SYSTEM_FULL = ("You are a neutral news editor for a market-data website. You write a short digest with context of "
               "everything that happened in one day, from the numbered news items you are given.\n" + _RULES + "\n" + _SHAPE_FULL)

SYSTEM_UPDATE = ("You are a neutral news editor for a market-data website. You maintain ONE running digest for the "
                 "current day. You are given (A) the digest as written so far, with numbered topics P1, P2..., and "
                 "(B) NEW numbered news items that arrived since. Produce the UPDATED digest for the whole day.\n"
                 "UPDATE RULES:\n"
                 "- Keep what is still true and important from (A); do not drop an earlier major theme just because it is quiet now (you may shorten or merge it).\n"
                 "- When new items continue a topic, update that topic and say how it developed. New themes become new topics.\n"
                 "- Re-rank topics by importance and coverage across the whole day. Overview, takeaways and watch must describe the whole day so far, not only the new items.\n"
                 "- Every topic must list in from_topics the P numbers it builds on and/or in items the NEW item numbers that support it (at least one of them).\n"
                 + _RULES + "\n" + _SHAPE_UPDATE)

_ADVICE = re.compile(r"\b(you should|we recommend|recommend(?:ed|s)? (?:to )?(?:buy|sell)|should (?:buy|sell|hold)|time to (?:buy|sell)|buy now|sell now)\b", re.I)


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


def _fetch_items(conn, day, after_id=0):
    """Headlines that ARRIVED during `day` (macro_news.timestamp = when the pipeline saved them), oldest first."""
    start, end = day_bounds(day)
    cur = conn.cursor()
    cur.execute("""SELECT id, category_tag, impact_level, summary_en, body_en, source_url, summary_he, timestamp
                   FROM macro_news
                   WHERE timestamp >= %s AND timestamp < %s AND id > %s
                   ORDER BY id ASC""", (start, end, after_id))
    rows = cur.fetchall()
    cur.close()
    return [dict(id=r[0], tag=r[1], impact=r[2], title=r[3] or "", body=r[4] or "", url=r[5] or "",
                 title_he=r[6] or "", ts=r[7]) for r in rows]


def _select(items, cap):
    """At most `cap` headlines: all Critical ones first (up to a third of the cap), the rest spread evenly over the
    period so the whole day is represented — not just its most recent hours."""
    if len(items) <= cap:
        return items
    crit = [i for i in items if i["impact"] == "Critical"][:max(1, cap // 3)]
    crit_ids = {i["id"] for i in crit}
    rest = [i for i in items if i["id"] not in crit_ids]
    slots = cap - len(crit)
    step = len(rest) / slots
    picked = [rest[int(k * step)] for k in range(slots)]
    return sorted(crit + picked, key=lambda i: i["id"])


# ------------------------------------------------------------------ prompts' user part
def _item_lines(items):
    lines = []
    for n, it in enumerate(items, 1):
        body = re.sub(r"\s+", " ", it["body"]).strip()[:BODY_CHARS]
        lines.append(f"[{n}] ({it['tag']}, {it['impact']}) {it['title']}" + (f" — {body}" if body else ""))
    return "\n".join(lines)


def _prompt_full(items, day):
    return f"News items from {day.isoformat()} (the whole day so far):\n" + _item_lines(items)


def _prompt_update(prev, items, day):
    tops = "\n".join(f"[P{n}] {t['title_en']} — {t['summary_en']}" + (f" | impact: {t['impact_en']}" if t.get("impact_en") else "")
                     for n, t in enumerate(prev["topics"], 1))
    take = "; ".join(prev.get("takeaways_en") or []) or "none"
    watch = "; ".join(prev.get("watch_en") or []) or "none"
    return (f"(A) CURRENT DIGEST for {day.isoformat()} (covers {prev.get('item_count', '?')} headlines so far)\n"
            f"Overview: {prev.get('overview_en', '')}\nTopics:\n{tops}\nTakeaways: {take}\nWatch: {watch}\n\n"
            f"(B) NEW news items since then:\n" + _item_lines(items))


# ------------------------------------------------------------------ parsing / validation
def _parse(text):
    """Models often return almost-JSON (trailing commas, curly quotes, code fences). Try strict first, then repair."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("no JSON object in the answer")
    raw = m.group(0)
    try:
        return json.loads(raw)
    except ValueError as first:
        fixed = (raw.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'"))
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
    for en, he in zip(prev.get("takeaways_en") or [], prev.get("takeaways_he") or []):
        add(en, he)
    for en, he in zip(prev.get("watch_en") or [], prev.get("watch_he") or []):
        add(en, he)
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
            v = v.strip().lstrip("PpTt")          # accepts "P2" as well as 2
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if lo <= n <= hi and n not in out:
            out.append(n)
    return out


def _build_payload(raw, items, prev, translate_fn, cache_from=None):
    """raw = the model's JSON; items = the numbered items it was shown; prev = previous payload (update mode) or None."""
    prev_topics = (prev or {}).get("topics") or []
    overview = str(raw.get("overview", "")).strip()
    topics = []
    for t in (raw.get("topics") or [])[:6]:
        new_nums = _ints(t.get("items"), 1, len(items))
        old_nums = _ints(t.get("from_topics"), 1, len(prev_topics)) if prev else []
        sources, seen = [], set()
        for n in reversed(new_nums):                       # newest headlines first
            it = items[n - 1]
            sources.append({"title_en": it["title"], "title_he": it["title_he"], "url": it["url"], "category": it["tag"]})
        for n in old_nums:
            sources.extend(prev_topics[n - 1].get("sources") or [])
        uniq = []
        for s in sources:
            k = s.get("url") or s.get("title_en")
            if k and k not in seen:
                seen.add(k)
                uniq.append(s)
        title, summary = str(t.get("title", "")).strip(), str(t.get("summary", "")).strip()
        if not (title and summary and uniq):
            continue                                        # no valid source -> dropped
        topics.append({"title_en": title, "summary_en": summary, "impact_en": str(t.get("impact", "")).strip(),
                       "sources": uniq[:MAX_SOURCES]})
    if not overview or not topics:
        raise ValueError("answer had no usable overview/topics")

    def _strings(key, n):
        return [str(x).strip() for x in (raw.get(key) or []) if str(x).strip()][:n]
    takeaways, watch = _strings("takeaways", 3), _strings("watch", 3)
    blob = " ".join([overview] + takeaways + watch + [t["title_en"] + " " + t["summary_en"] + " " + t["impact_en"] for t in topics])
    if _ADVICE.search(blob):
        raise ValueError("answer looked like investment advice — rejected")
    known = _known_translations(cache_from if cache_from is not None else prev)
    payload = {"overview_en": overview, "overview_he": _he(overview, translate_fn, known), "topics": topics,
               "takeaways_en": takeaways, "takeaways_he": [_he(x, translate_fn, known) for x in takeaways],
               "watch_en": watch, "watch_he": [_he(x, translate_fn, known) for x in watch]}
    for t in topics:
        t["title_he"] = _he(t["title_en"], translate_fn, known)
        t["summary_he"] = _he(t["summary_en"], translate_fn, known)
        t["impact_he"] = _he(t["impact_en"], translate_fn, known)
    return payload


# ------------------------------------------------------------------ one day
def _update_day(conn, day, translate_fn, force=False, final=False):
    """Create / update / close the digest of one day. Returns True if a new AI digest was saved."""
    min_hours = float(os.environ.get("AI_DIGEST_MIN_HOURS") or 3)
    row = _load_row(conn, day)
    if row and row["is_final"] and not force:
        return False
    if row and not force and not final and row["age_h"] < min_hours:
        print(f"[ai_digest] {day}: last update is {row['age_h']:.1f}h old (< {min_hours}h) — skipping")
        return False

    rebuild = force or row is None or row["since_rebuild"] >= REBUILD_EVERY
    if rebuild:
        all_items = _fetch_items(conn, day, 0)
        fetched = all_items
        need = MIN_ITEMS
    else:
        fetched = _fetch_items(conn, day, row["last_item_id"])
        need = 1 if final else MIN_NEW
    if not rebuild and len(fetched) < need:
        if final:
            _mark_final(conn, day)
            print(f"[ai_digest] {day}: closed (no new headlines since the last update)")
        else:
            print(f"[ai_digest] {day}: only {len(fetched)} new headlines (< {need}) — skipping")
        return False
    if rebuild and len(fetched) < need:
        print(f"[ai_digest] {day}: only {len(fetched)} headlines — too few for a digest")
        return False

    if rebuild:
        sel = _select(fetched, MAX_ITEMS)
        messages = [{"role": "system", "content": SYSTEM_FULL}, {"role": "user", "content": _prompt_full(sel, day)}]
        prev = None
    else:
        sel = _select(fetched, MAX_NEW)
        prev = dict(row["payload"], item_count=row["item_count"])
        messages = [{"role": "system", "content": SYSTEM_UPDATE}, {"role": "user", "content": _prompt_update(prev, sel, day)}]
    mode = "rebuild" if rebuild else f"update (+{len(fetched)} new)"
    print(f"[ai_digest] {day}: {mode}, sending {len(sel)} headlines")

    cache_from = row["payload"] if row else None          # Hebrew of unchanged sentences is reused
    text, model = llm_provider.chat(
        messages, max_tokens=2500, temperature=0.1,
        validate=lambda t: _build_payload(_parse(t), sel, prev, lambda x: x))
    payload = _build_payload(_parse(text), sel, prev, translate_fn, cache_from)

    stamps = [it["ts"] for it in fetched if it.get("ts")]
    prev_from = (row["payload"].get("covers_from") if row and not rebuild else None)
    prev_to = (row["payload"].get("covers_to") if row and not rebuild else None)
    if stamps:
        cf, ct = min(stamps).isoformat(), max(stamps).isoformat()
        payload["covers_from"] = min(x for x in (prev_from, cf) if x)
        payload["covers_to"] = max(x for x in (prev_to, ct) if x)
    payload["day"] = day.isoformat()
    item_count = len(fetched) if rebuild else row["item_count"] + len(fetched)
    last_id = max(it["id"] for it in fetched)
    updates = 1 if rebuild and (row is None or force) else (row["updates"] + 1 if row else 1)
    since_rebuild = 0 if rebuild else row["since_rebuild"] + 1
    payload["item_count"] = item_count
    payload["updates"] = updates

    cur = conn.cursor()
    cur.execute("""INSERT INTO ai_daily_digests (day, updated_at, item_count, last_item_id, updates, since_rebuild, is_final, model, payload)
                   VALUES (%s, NOW(), %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (day) DO UPDATE SET updated_at = NOW(), item_count = EXCLUDED.item_count,
                       last_item_id = EXCLUDED.last_item_id, updates = EXCLUDED.updates,
                       since_rebuild = EXCLUDED.since_rebuild, is_final = EXCLUDED.is_final,
                       model = EXCLUDED.model, payload = EXCLUDED.payload""",
                (day, item_count, last_id, updates, since_rebuild, bool(final), model, json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    cur.close()
    print(f"[ai_digest] {day}: saved{' (FINAL)' if final else ''} — {item_count} headlines, {len(payload['topics'])} topics, "
          f"update #{updates}, model {model}")
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
        conn = get_conn()
        saved = False
        try:
            ensure_table(conn)
            today = today_local()
            # 1) finish earlier days that were still open (normally just yesterday, once, right after midnight)
            cur = conn.cursor()
            cur.execute("SELECT day FROM ai_daily_digests WHERE NOT is_final AND day < %s ORDER BY day", (today,))
            open_days = [r[0] for r in cur.fetchall()]
            cur.close()
            for d in open_days:
                if (today - d).days <= CLOSE_LOOKBACK_DAYS:
                    saved |= _update_day(conn, d, translate_fn, final=True)
                _mark_final(conn, d)
            # 2) today
            saved |= _update_day(conn, today, translate_fn, force=force)
            cur = conn.cursor()
            cur.execute("DELETE FROM ai_daily_digests WHERE day < %s", (today - timedelta(days=KEEP_DAYS),))
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return saved
    except llm_provider.LLMAuthError as e:
        print(f"[ai_digest] KEY REJECTED — check LLM_API_KEY ({e})")
    except Exception as e:  # noqa: BLE001
        print(f"[ai_digest] not generated (non-fatal): {type(e).__name__}: {e}")
    return False


def backfill(get_conn, translate_fn, days=7):
    """Create a (final) digest for each of the last `days` days that has headlines but no digest yet."""
    if not llm_provider.is_configured():
        print("[ai_digest] off (LLM_API_KEY is not set)")
        return False
    conn = get_conn()
    made = 0
    try:
        ensure_table(conn)
        today = today_local()
        for k in range(days, 0, -1):
            d = today - timedelta(days=k)
            if _load_row(conn, d):
                print(f"[ai_digest] {d}: already has a digest — skipped")
                continue
            try:
                if _update_day(conn, d, translate_fn, force=True, final=True):
                    made += 1
            except llm_provider.LLMAuthError:
                raise
            except Exception as e:  # noqa: BLE001
                print(f"[ai_digest] {d}: failed (non-fatal): {type(e).__name__}: {e}")
    finally:
        conn.close()
    print(f"[ai_digest] backfill done — {made} day(s) created")
    return made > 0


if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(0 if llm_provider.probe() else 1)
    import pipeline_b_news_aggregator as pb
    if "--backfill" in sys.argv:
        i = sys.argv.index("--backfill")
        n = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].isdigit() else 7
        sys.exit(0 if backfill(pb.get_conn, pb.translate_to_hebrew, n) else 1)
    ok = maybe_generate(pb.get_conn, pb.translate_to_hebrew, force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
