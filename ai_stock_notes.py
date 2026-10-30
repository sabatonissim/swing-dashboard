"""
AI stock notes (v25) — a short plain-language summary per stock, written once a day by the AI
===========================================================================================
Runs in GitHub Actions (ai-stock-notes.yml), started by scheduler.py on weekdays at 17:30 ET (after the evening scan),
with the same LLM settings as the daily digest (LLM_API_KEY secret, LLM_MODELS / LLM_BASE_URL variables — llm_provider.py).
Nothing new on Render: the API only reads the table (ai_stock_notes) and the stock page shows the note when it exists.

Which stocks (at most MAX_STOCKS per run, each once a day): the last scan's signals (highest score first), then the
tickers on anyone's watchlist, then tickers of open trades in "my portfolio".

The AI gets ONLY facts already in our database (scan signal, RS/trend/distance from the high, fundamentals, analyst
targets/short interest, insider trades, next earnings, market state) and is told to describe them — no advice, no
forecasts, no numbers that are not in the facts. A note that fails the checks (not JSON, too long, a "buy/sell" word)
is not saved; the next model is tried.
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone

import psycopg2
import psycopg2.extras

import llm_provider

MAX_STOCKS = 45
NOTE_MAX_CHARS = 900
# advice words the note must never contain (the site describes, it does not recommend)
_BANNED = re.compile(r"\b(buy|sell|should|recommend|strong buy|price target of ours)\b|לקנות|למכור|כדאי|ממליצ|המלצת קנייה|המלצת מכירה",
                     re.I)

SYSTEM = (
    "You write short factual summaries of a US stock for a swing-trading dashboard, in Hebrew and in English. "
    "Use ONLY the facts in the JSON you are given; never add numbers, events or news that are not there. "
    "Describe — do not advise: no buy/sell/hold, no 'should', no forecasts of the price, no opinions about valuation "
    "being cheap or expensive. Mention what stands out: the trend and the scanner signal if any, strength vs. the "
    "market (RS), notable fundamentals (growth, margins, P/E), and risk facts (earnings within days, high short "
    "interest, insider selling, the market state). 3 to 5 short sentences per language, plain words, no lists, no "
    "markdown. Hebrew: natural Israeli Hebrew, keep tickers and numbers as they are. "
    'Answer with JSON only: {"he": "...", "en": "..."}'
)


def get_conn():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg2.connect(url)


def _exists(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (table,))
    return cur.fetchone()["ok"]


def pick_tickers(cur):
    out = []

    def add(rows):
        for (t,) in rows:
            if t and t not in out:
                out.append(t)

    cur.execute("""SELECT ticker FROM (SELECT DISTINCT ON (ticker) ticker, swing_score FROM scanned_stocks
                   WHERE timestamp > (SELECT MAX(timestamp) FROM scanned_stocks) - INTERVAL '2 hours'
                   ORDER BY ticker, timestamp DESC) s ORDER BY swing_score DESC NULLS LAST""")
    add([(r["ticker"],) for r in cur.fetchall()])
    cur.execute("SELECT ticker, COUNT(*) AS n FROM watchlist WHERE user_id IS NOT NULL GROUP BY ticker ORDER BY n DESC")
    add([(r["ticker"],) for r in cur.fetchall()])
    if _exists(cur, "trades"):
        cur.execute("SELECT DISTINCT ticker FROM trades WHERE closed_on IS NULL")
        add([(r["ticker"],) for r in cur.fetchall()])
    return out


def _r(v, d=2):
    try:
        return None if v is None else round(float(v), d)
    except (TypeError, ValueError):
        return None


def gather_facts(cur, t):
    f = {"ticker": t}
    cur.execute("""SELECT timestamp, pattern_type, trigger_text_en, swing_score, rs_rating, trend_stage, change_pct,
                          atr_pct, days_to_earnings, entry_price, support_level
                   FROM scanned_stocks WHERE ticker = %s AND timestamp > NOW() - INTERVAL '10 days'
                   ORDER BY timestamp DESC LIMIT 1""", (t,))
    s = cur.fetchone()
    if s:
        f["scanner_signal"] = {"date": s["timestamp"].date().isoformat(), "pattern": s["pattern_type"],
                               "description": s["trigger_text_en"], "score_0_100": s["swing_score"],
                               "atr_pct": _r(s["atr_pct"]), "entry": _r(s["entry_price"]), "support": _r(s["support_level"])}
    if _exists(cur, "stock_leaders"):
        cur.execute("SELECT * FROM stock_leaders WHERE ticker = %s AND updated_at > NOW() - INTERVAL '4 days'", (t,))
        L = cur.fetchone()
        if L:
            f.update({"name": L["name"], "sector": L["sector"], "price": _r(L["close"]), "rs_rating_1_99": L["rs_rating"],
                      "trend_stage": L["stage"], "pct_from_52w_high": _r(L["pct_from_high"], 1),
                      "change_1m_pct": _r(L["chg_1m"], 1), "change_3m_pct": _r(L["chg_3m"], 1)})
    elif s:
        f.update({"rs_rating_1_99": s["rs_rating"], "trend_stage": s["trend_stage"]})
    if _exists(cur, "fundamentals_cache"):
        cur.execute("SELECT data FROM fundamentals_cache WHERE ticker = %s", (t,))
        row = cur.fetchone()
        d = row["data"] if row else None
        if isinstance(d, str):
            d = json.loads(d)
        if d and d.get("has_fundamentals"):
            fund = {"trailing_pe": _r(d.get("trailing_pe"), 1), "forward_pe": _r(d.get("forward_pe"), 1),
                    "pe_note": d.get("trailing_pe_nm"), "market_cap_usd": _r(d.get("market_cap"), 0)}
            rev = [x for x in (d.get("revenue") or []) if x is not None]
            if len(rev) >= 5 and rev[-5]:
                fund["revenue_growth_yoy_pct_latest_quarter"] = _r((rev[-1] / rev[-5] - 1) * 100, 1)
            gm = [x for x in (d.get("gross_margin_pct") or []) if x is not None]
            if gm:
                fund["gross_margin_pct_latest_quarter"] = _r(gm[-1], 1)
            ni = [x for x in (d.get("net_income") or []) if x is not None]
            if ni:
                fund["net_income_latest_quarter_usd"] = _r(ni[-1], 0)
            f["fundamentals"] = {k: v for k, v in fund.items() if v is not None}
    if _exists(cur, "stock_estimates"):
        cur.execute("""SELECT target_mean, num_analysts, short_pct_float, short_ratio FROM stock_estimates
                       WHERE ticker = %s AND updated_at > NOW() - INTERVAL '10 days'""", (t,))
        e = cur.fetchone()
        if e:
            if e.get("target_mean") and (e.get("num_analysts") or 0) >= 3:
                f["analysts"] = {"average_12m_target": _r(e["target_mean"]), "count": e["num_analysts"]}
            if e.get("short_pct_float") is not None and 0 <= e["short_pct_float"] <= 1:
                f["short_interest_pct_of_float"] = _r(100 * e["short_pct_float"], 1)
    if _exists(cur, "insider_trades"):
        cur.execute("""SELECT code, COUNT(*) AS n, SUM(shares * COALESCE(price, 0)) AS v FROM insider_trades
                       WHERE ticker = %s AND filed_date >= CURRENT_DATE - 90 AND code IN ('P', 'S') GROUP BY code""", (t,))
        ins = {r["code"]: {"filings": r["n"], "value_usd": _r(r["v"], 0)} for r in cur.fetchall()}
        if ins:
            f["insider_trades_90d"] = {"open_market_buys": ins.get("P"), "open_market_sells": ins.get("S")}
    cur.execute("""SELECT report_date, session FROM earnings_calendar WHERE ticker = %s AND report_date >= CURRENT_DATE
                   ORDER BY report_date LIMIT 1""", (t,))
    er = cur.fetchone()
    if er:
        f["next_earnings"] = {"date": er["report_date"].isoformat(), "days_from_today": (er["report_date"] - date.today()).days,
                              "session": er["session"]}
    return f


def market_facts(cur):
    out = {}
    if _exists(cur, "market_regime"):
        cur.execute("SELECT * FROM market_regime ORDER BY day DESC LIMIT 1")
        m = cur.fetchone()
        if m and m["spy_close"] and m["spy_sma50"] and m["spy_sma200"]:
            c, a, b = m["spy_close"], m["spy_sma50"], m["spy_sma200"]
            # same rule as api_server._trend_label (the "market state" chip)
            out["sp500_trend"] = "up" if (c > a and c > b and a > b) else "down" if (c < a and c < b and a < b) else "mixed"
        if m and m["vix"]:
            out["vix"] = _r(m["vix"], 1)
    return out


def _validate(text):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("no JSON")
    d = json.loads(m.group(0))
    he, en = (d.get("he") or "").strip(), (d.get("en") or "").strip()
    if len(he) < 40 or len(en) < 40:
        raise ValueError("too short")
    if len(he) > NOTE_MAX_CHARS or len(en) > NOTE_MAX_CHARS:
        raise ValueError("too long")
    if _BANNED.search(he) or _BANNED.search(en):
        raise ValueError("advice wording")
    return d


def main() -> int:
    if not llm_provider.is_configured():
        print("ai stock notes: LLM_API_KEY is not set — nothing to do")
        return 0
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""CREATE TABLE IF NOT EXISTS ai_stock_notes (
        ticker TEXT PRIMARY KEY, note_he TEXT NOT NULL, note_en TEXT NOT NULL, facts JSONB, model TEXT,
        created_at TIMESTAMPTZ DEFAULT NOW())""")
    conn.commit()
    cur.execute("SELECT ticker FROM ai_stock_notes WHERE created_at > NOW() - INTERVAL '20 hours'")
    fresh = {r["ticker"] for r in cur.fetchall()}
    tickers = [t for t in pick_tickers(cur) if t not in fresh][:MAX_STOCKS]
    market = market_facts(cur)
    facts = {t: gather_facts(cur, t) for t in tickers}
    conn.commit()
    cur.close()
    conn.close()                                   # the AI calls take minutes; the DB is reopened only to write
    print(f"ai stock notes: {len(tickers)} stock(s) to write ({len(fresh)} already fresh today)")

    done, failed = [], 0
    started = time.time()
    for t in tickers:
        if time.time() - started > 25 * 60:
            print("ai stock notes: time budget used up, the rest waits for tomorrow")
            break
        payload = dict(facts[t], market=market, today=date.today().isoformat())
        try:
            text, model = llm_provider.chat(
                [{"role": "system", "content": SYSTEM},
                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
                max_tokens=900, temperature=0.2, validate=_validate)
            d = _validate(text)
            done.append((t, d["he"].strip(), d["en"].strip(), json.dumps(payload, ensure_ascii=False, default=str), model))
        except llm_provider.LLMAuthError as e:
            print(f"ai stock notes: the AI key was rejected — stopping ({e})")
            break
        except Exception as e:
            failed += 1
            print(f"[warn] ai stock note {t}: {type(e).__name__}: {e}")
    if done:
        conn = get_conn()
        cur = conn.cursor()
        for t, he, en, fj, model in done:
            cur.execute("""INSERT INTO ai_stock_notes (ticker, note_he, note_en, facts, model, created_at)
                           VALUES (%s, %s, %s, %s::jsonb, %s, NOW())
                           ON CONFLICT (ticker) DO UPDATE SET note_he = EXCLUDED.note_he, note_en = EXCLUDED.note_en,
                               facts = EXCLUDED.facts, model = EXCLUDED.model, created_at = NOW()""", (t, he, en, fj, model))
        conn.commit()
        cur.close()
        conn.close()
    print(f"ai stock notes: saved {len(done)}, failed {failed} ({int(time.time() - started)}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
