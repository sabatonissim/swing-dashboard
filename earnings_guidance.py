"""
v40: the company's own guidance (its forecast for the coming quarter / fiscal year), from its earnings press release.

Owner, 9.11.2026: "a company can report good results and drop hard because of the guidance". The release is the one the
scanner already reads for EPS and revenue (SEC 8-K Item 2.02, Exhibit 99.1; 6-K for foreign companies). The AI (same
settings as the daily digest, llm_provider.py) copies out the figures, and every figure is checked against the text:
  * the quote it gives must be in the release word for word, and the numbers must be in the quote;
  * revenue / EPS must carry a dollar sign (a yen or won forecast is not compared with a dollar estimate);
  * the size must fit the company (a quarter's revenue guidance within 0.5-2x of this quarter's consensus, ...);
  * "raised / lowered / reaffirmed" only when the text says so.
Next to it: the analysts' consensus for that period from BEFORE the report (Yahoo's +1q / 0y / +1y rows roll to the next
period once the company reports, so they are saved while the report is still ahead: snapshot_estimates).
Facts only: the range, the consensus, the difference. Guidance given only on the conference call is not in the release.

Runs as a second step of earnings-fast.yml (every 5 minutes in the report hours) and earnings-refresh.yml (the quick
refresh): the database is awake then anyway. Each run: one short read, the work (SEC, Yahoo, AI) with no connection open,
one short write. Nothing to do = one read.
"""
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import psycopg2.extras

ET = ZoneInfo("America/New_York")
RUN_BUDGET_SEC = 150             # earnings-fast starts every 5 minutes; a run must end before the next one is due
LOOKBACK_DAYS = 3                # reports this many days back still get their guidance read
ESTIMATE_DAYS_AHEAD = 3          # consensus saved for reports up to this many days ahead
ESTIMATE_REFRESH_HOURS = 20      # ... once a day per report (it changes little)
MAX_ATTEMPTS = 6                 # no filing yet / the AI failed: tried again after attempts x 20 minutes
SOURCE_MAX_CHARS = 14000         # the parts of the release that can hold guidance, sent to the AI
KEEP_DAYS = 120
MAX_ITEMS = 8
LLM_BUDGET_SEC = "90"            # per AI request, across its fallback models (llm_provider's LLM_BUDGET_SECONDS)
REJECTED_NOTE = "AI answered, no figure passed"   # every answer rejected: "none" after the last attempt, not "failed"

METRICS = ("revenue", "eps", "revenue_growth")
UNITS = {"billion": 1e9, "million": 1e6, "thousand": 1e3, "dollars": 1.0, "percent": 1.0}


# ---------------------------------------------------------------- the text
_HEADING = re.compile(r"\b(outlook|guidance)\b", re.I)
_FORWARD = re.compile(r"\b(outlook|guidance|expects?|expected|anticipates?|forecasts?|projects?|estimates?|sees|targets?)\b", re.I)
_BOILER = re.compile(r"forward[-\s]looking\s+statements?|safe\s+harbor|cautionary\s+statement", re.I)


def guidance_source(text: str) -> str:
    """The parts of the release that can carry guidance (an "Outlook"/"Guidance" passage first, then any "expects"),
    at most SOURCE_MAX_CHARS; '' when there is none — then the AI is not asked at all."""
    if not text:
        return ""
    spans = []
    for m in _FORWARD.finditer(text):
        a, b = max(0, m.start() - 300), min(len(text), m.end() + 2200)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    scored = []
    for a, b in spans:
        chunk = text[a:b]
        if not re.search(r"\d", chunk) or (_BOILER.search(chunk) and not re.search(r"\$\s?\d", chunk)):
            continue
        scored.append((0 if _HEADING.search(chunk) else 1, a, chunk))
    out, total = [], 0
    for _, _, chunk in sorted(scored):
        if total >= SOURCE_MAX_CHARS:
            break
        chunk = chunk[:SOURCE_MAX_CHARS - total]
        out.append(chunk)
        total += len(chunk)
    return "\n...\n".join(out)


def _norm(s: str) -> str:
    s = (s or "").replace("–", "-").replace("—", "-").replace("−", "-").replace("’", "'") \
        .replace("“", '"').replace("”", '"').replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _numbers(s: str) -> set:
    return {n.replace(",", "").rstrip(".") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", s or "")}


def _num(s):
    try:
        return float(str(s).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- the AI answer, checked
SYSTEM = """You read a company's earnings press release and copy out its FORWARD guidance: the company's own forecast for a
coming quarter or fiscal year. Only these metrics:
- "revenue": total revenue / net sales / total revenues (not bookings, not a segment, not gross margin, not EBITDA)
- "eps": earnings per share (GAAP or adjusted / non-GAAP)
- "revenue_growth": growth of total revenue or net sales in percent
Never this quarter's results, never analysts' figures, never targets beyond the next fiscal year.
Answer with JSON only, nothing else:
{"guidance":[{"period":"quarter" or "year","quarter":1-4 or null,"fiscal_year":2026,"metric":"revenue"|"eps"|"revenue_growth",
"basis":"adjusted"|"gaap"|null,"low":"3.22","high":"3.28","unit":"billion"|"million"|"dollars"|"percent",
"change":"raised"|"lowered"|"reaffirmed"|null,"quote":"..."}]}
Rules:
- low / high: the numbers exactly as written in the text, without $ or commas. One figure: the same value in both. Never
  compute a number yourself ("approximately $17 billion, plus or minus 3%" -> low "17", high "17").
- unit: what the figure is in ("$3.9 - $4.0 billion" -> billion, "$1.26 - $1.30" per share -> dollars, "16% to 18%" -> percent).
- quote: copy the exact words of the text that hold the figures (at most 300 characters), character for character.
- change: only when the text itself says this guidance was raised, lowered or reaffirmed / maintained; otherwise null.
- basis: "adjusted" for non-GAAP / adjusted / core figures, "gaap" when the text says GAAP, otherwise null.
- fiscal_year: the company's own fiscal year of that period ("fiscal 2027", "FY27" -> 2027; "fourth quarter of 2026" -> 2026).
- No such guidance in the text: {"guidance":[]}"""


def _json(text: str):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise ValueError("no JSON object in the answer")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise ValueError(f"broken JSON ({e.msg})")
    g = data.get("guidance") if isinstance(data, dict) else None
    if not isinstance(g, list):
        raise ValueError('the JSON has no "guidance" list')
    return g


def _check_item(it: dict, src: str, ref: dict, report_date: date):
    """One guidance figure -> a clean dict, or a reason (str) why it is not taken."""
    if not isinstance(it, dict):
        return "not an object"
    metric, period, unit = it.get("metric"), it.get("period"), str(it.get("unit") or "").lower()
    if metric not in METRICS:
        return f"metric {metric!r} is not one we show"
    if period not in ("quarter", "year"):
        return f"period {period!r}"
    try:
        fy = int(it.get("fiscal_year"))
    except (TypeError, ValueError):
        return "no fiscal_year"
    if not report_date.year - 1 <= fy <= report_date.year + 2:
        return f"fiscal_year {fy} is not a coming period"
    q = it.get("quarter")
    if period == "quarter":
        try:
            q = int(q)
        except (TypeError, ValueError):
            return "a quarter without its number"
        if q not in (1, 2, 3, 4):
            return f"quarter {q}"
    else:
        q = None
    quote = str(it.get("quote") or "")
    nq = _norm(quote)
    if len(nq) < 12:
        return "no quote"
    pieces = [p.strip() for p in re.split(r"\.\.\.|…", nq) if len(p.strip()) >= 8]
    if not pieces or any(p not in src for p in pieces):
        return f"the quote is not in the release: {quote[:80]!r}"
    pos = src.find(pieces[0])
    lo_s, hi_s = str(it.get("low") or "").replace(",", "").replace("$", "").strip(), \
        str(it.get("high") or it.get("low") or "").replace(",", "").replace("$", "").strip()
    lo, hi = _num(lo_s), _num(hi_s)
    if lo is None or hi is None:
        return "low/high are not numbers"
    in_quote = [_num(n) for n in _numbers(nq)]
    for v, s in ((lo, lo_s), (hi, hi_s)):
        if not any(n is not None and abs(n - v) < 1e-9 for n in in_quote):
            return f"{s} is not in the quote"
    if lo > hi:
        lo, hi = hi, lo
    if not (_FORWARD.search(nq) or _FORWARD.search(src[max(0, pos - 700):pos])):
        return "no forecast wording around the quote (a result, not guidance)"
    if metric == "revenue_growth":
        if unit != "percent" or ("%" not in nq and "percent" not in nq):
            return "growth without a percent"
        if not re.search(r"revenue|sales", nq):
            return "growth of something other than revenue"
        mult = 1.0
    else:
        if "$" not in nq and "dollar" not in nq:
            return "no dollar sign (another currency?)"
        if metric == "revenue":
            if unit not in ("billion", "million", "thousand"):
                return f"revenue in {unit!r}"
            if not re.search(r"revenue|sales", nq):
                return "the quote does not say revenue"
            if unit == "billion" and not re.search(r"billion|\bbn\b|\d\s?b\b", nq):
                return "billion is not in the quote"
            if unit == "million" and not re.search(r"million|\bmm\b|\d\s?m\b", nq):
                return "million is not in the quote"
            mult = UNITS[unit]
            base = ref.get("revenue_estimate")
            if base and base > 0:
                r = (lo * mult) / (base * (1 if period == "quarter" else 4))
                if not (0.5 <= r <= 2.0 if period == "quarter" else 0.4 <= r <= 2.5):
                    return f"revenue guidance {lo * mult:,.0f} does not fit this company's size (consensus {base:,.0f})"
        else:
            if unit != "dollars":
                return f"EPS in {unit!r}"
            if not re.search(r"per\s+(?:diluted\s+)?share|\beps\b", nq):
                return "the quote does not say per share"
            mult = 1.0
            base = ref.get("eps_estimate")
            if base is not None and abs(base) >= 0.05:
                b = base * (1 if period == "quarter" else 4)
                if abs(lo - b) > max(1.5 * abs(b), 0.5 if period == "quarter" else 2.0):
                    return f"EPS guidance {lo} does not fit (this quarter's consensus {base})"
    change = it.get("change")
    words = {"raised": r"rais|increas|lift|boost|improv|upgrad|higher",
             "lowered": r"lower|reduc|cut\b|decreas|trim",
             "reaffirmed": r"reaffirm|maintain|reiterat|confirm|unchanged"}
    if change not in words or not re.search(words[change], nq + " " + src[max(0, pos - 250):pos]):
        change = None
    basis = it.get("basis") if it.get("basis") in ("adjusted", "gaap") else None
    if metric == "eps" and basis is None and re.search(r"non[-\s]?gaap|adjusted", nq):
        basis = "adjusted"
    return {"period": period, "quarter": q, "fiscal_year": fy, "metric": metric, "basis": basis,
            "low": round(lo * mult, 4), "high": round(hi * mult, 4), "change": change, "quote": quote.strip()[:300]}


def clean_guidance(answer: str, release_text: str, ref: dict, report_date: date) -> list:
    """The AI's answer -> checked guidance figures. An empty list is a real answer ("no guidance in the release");
    figures given but none passing raise ValueError (the AI is asked again / the next model tried)."""
    raw = _json(answer)
    src = _norm(release_text)
    out, reasons, seen = [], [], set()
    for it in raw[:20]:
        got = _check_item(it, src, ref, report_date)
        if isinstance(got, str):
            reasons.append(got)
            continue
        key = (got["period"], got["quarter"], got["fiscal_year"], got["metric"])
        if key in seen:
            # adjusted EPS wins over GAAP (the analysts' estimate is on that basis)
            if got["metric"] == "eps" and got["basis"] == "adjusted":
                out = [o for o in out if (o["period"], o["quarter"], o["fiscal_year"], o["metric"]) != key]
            else:
                continue
        seen.add(key)
        out.append(got)
    if raw and not out:
        raise ValueError("no figure passed the checks: " + "; ".join(reasons[:4]))
    if reasons:
        print(f"[info] guidance: {len(reasons)} figure(s) dropped: {'; '.join(reasons[:4])}")
    order = {"quarter": 0, "year": 1}
    out.sort(key=lambda o: (order[o["period"]], o["fiscal_year"], METRICS.index(o["metric"])))
    return out[:MAX_ITEMS]


def with_estimates(items: list, est: dict) -> list:
    """Each figure gets the consensus for the same period from before the report, and how far the middle of the range is
    from it. Quarter = Yahoo's next quarter (+1q); year = the current fiscal year (0y) when its year matches the year in
    fy_end, the next one (+1y) when it is a year later; otherwise no comparison. Growth in % is not compared."""
    fy_end = est.get("fy_end")
    fy0 = fy_end.year if isinstance(fy_end, date) else None
    for it in items:
        key = None
        if it["period"] == "quarter":
            key = "nq"
        elif fy0 and it["fiscal_year"] == fy0:
            key = "fy0"
        elif fy0 and it["fiscal_year"] == fy0 + 1:
            key = "fy1"
        e = None
        if key and it["metric"] == "revenue":
            e = est.get(f"est_{key}_rev")
        elif key and it["metric"] == "eps" and it["basis"] != "gaap":
            e = est.get(f"est_{key}_eps")
        it["est"] = round(float(e), 4) if e is not None else None
        mid = (it["low"] + it["high"]) / 2
        it["vs_pct"] = round((mid / e - 1) * 100, 2) if e is not None and e > 0 else None
    return items


# ---------------------------------------------------------------- the work
def _connect():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def ensure_table(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS earnings_guidance (
        ticker TEXT NOT NULL, report_date DATE NOT NULL,
        est_nq_eps REAL, est_nq_rev DOUBLE PRECISION, est_fy0_eps REAL, est_fy0_rev DOUBLE PRECISION,
        est_fy1_eps REAL, est_fy1_rev DOUBLE PRECISION, fy_end DATE, est_at TIMESTAMPTZ,
        status TEXT, items JSONB, accession TEXT, model TEXT, attempts INTEGER DEFAULT 0, note TEXT, done_at TIMESTAMPTZ,
        PRIMARY KEY (ticker, report_date))""")


def _report_moment(d: date, session: str) -> datetime:
    hh, mm = (8, 0) if session == "bmo" else (16, 30) if session == "amc" else (12, 0)   # as fetch_nasdaq_earnings
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


def fetch_estimates(ticker: str, fy_end):
    """Yahoo's consensus for the next quarter and the current / next fiscal year (works from Actions, not from Render)."""
    import pandas as pd
    import yfinance as yf
    t = yf.Ticker(ticker)
    out = {}
    for attr, kind in (("earnings_estimate", "eps"), ("revenue_estimate", "rev")):
        try:
            df = getattr(t, attr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] guidance estimates {ticker}: {attr}: {type(e).__name__}: {e}")
            continue
        if df is None or df.empty or "avg" not in df.columns:
            continue
        for per, key in (("+1q", "nq"), ("0y", "fy0"), ("+1y", "fy1")):
            if per in df.index and pd.notna(df.loc[per, "avg"]):
                out[f"est_{key}_{kind}"] = float(df.loc[per, "avg"])
    if out and not fy_end:
        try:
            ts = (t.info or {}).get("nextFiscalYearEnd")
            fy_end = datetime.fromtimestamp(int(ts), tz=timezone.utc).date() if ts else None
        except Exception as e:  # noqa: BLE001
            print(f"[info] guidance estimates {ticker}: no fiscal year end ({type(e).__name__})")
    out["fy_end"] = fy_end
    return out


def read_guidance(ticker: str, report_date: date, ref: dict):
    """-> (status, items, accession, model, note). status None = nothing to say yet (no filing, AI failed)."""
    import llm_provider
    import pipeline_a_scanner as sc
    cik = sc._sec_cik_for(ticker)
    if not cik:
        return None, None, None, None, "no CIK"
    for acc in sc._sec_release_accessions(cik, report_date):
        text = sc._sec_release_text(cik, acc)
        if not text:
            continue
        src = guidance_source(text)
        if not src:
            return "none", [], acc, None, "no forecast wording in the release"
        os.environ.setdefault("LLM_BUDGET_SECONDS", LLM_BUDGET_SEC)
        user = f"Company: {ticker}. Report date: {report_date.isoformat()}.\n\nTEXT:\n{src}"
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
        rejected = []                    # v40e: answers whose every figure failed the checks (ROIV 9.11: a result, not guidance)

        def _validate(a):
            try:
                return clean_guidance(a, text, ref, report_date)
            except ValueError as e:
                if str(e).startswith("no figure passed"):
                    rejected.append(str(e))
                raise
        try:
            answer, model = llm_provider.chat(msgs, max_tokens=1500, temperature=0.1, validate=_validate)
        except (RuntimeError, llm_provider.LLMNotConfigured) as e:
            if rejected:                 # main() turns this into "none" on the last attempt
                return None, None, acc, None, f"{REJECTED_NOTE}: {rejected[-1][:130]}"
            return None, None, acc, None, f"AI: {str(e)[:150]}"
        items = clean_guidance(answer, text, ref, report_date)
        return ("found" if items else "none"), items, acc, model, None
    return None, None, None, None, "no press release filed yet"


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL is not set")
        return 1
    t0 = time.time()
    today = datetime.now(ET).date()
    conn = _connect()
    try:
        conn.autocommit = True
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        ensure_table(cur)
        cur.execute("DELETE FROM earnings_guidance WHERE report_date < %s", (today - timedelta(days=KEEP_DAYS),))
        cur.execute("""SELECT c.ticker, c.report_date, c.eps_estimate, c.revenue_estimate, c.market_cap,
                              COALESCE(g.attempts, 0) AS attempts, g.est_nq_eps, g.est_nq_rev, g.est_fy0_eps, g.est_fy0_rev,
                              g.est_fy1_eps, g.est_fy1_rev, g.fy_end
                       FROM earnings_calendar c LEFT JOIN earnings_guidance g USING (ticker, report_date)
                       WHERE c.report_date BETWEEN %s AND %s AND (c.eps_actual IS NOT NULL OR c.revenue_actual IS NOT NULL)
                         AND (g.status IS NULL OR g.status = 'failed')
                         AND COALESCE(g.attempts, 0) < %s
                         AND (g.done_at IS NULL OR g.done_at < NOW() - make_interval(mins => 20 * COALESCE(g.attempts, 0)))
                       ORDER BY c.report_date DESC, c.market_cap DESC NULLS LAST""",
                    (today - timedelta(days=LOOKBACK_DAYS), today, MAX_ATTEMPTS))
        todo = cur.fetchall()
        cur.execute("""SELECT c.ticker, c.report_date, c.session, s.fy_end
                       FROM earnings_calendar c LEFT JOIN earnings_guidance g USING (ticker, report_date)
                       LEFT JOIN stock_estimates s ON s.ticker = c.ticker
                       WHERE c.report_date BETWEEN %s AND %s AND c.eps_actual IS NULL AND c.revenue_actual IS NULL
                         AND (g.est_at IS NULL OR g.est_at < NOW() - make_interval(hours => %s))
                       ORDER BY c.report_date, c.market_cap DESC NULLS LAST""",
                    (today, today + timedelta(days=ESTIMATE_DAYS_AHEAD), ESTIMATE_REFRESH_HOURS))
        upcoming = cur.fetchall()
        cur.close()
    finally:
        conn.close()                     # no connection open during SEC / Yahoo / the AI (Neon auto-suspend)
    print(f"[info] guidance: {len(todo)} report(s) to read, {len(upcoming)} upcoming report(s) need the consensus")

    results, estimates = [], []
    for r in todo:                       # guidance first: it is what the site waits for
        if time.time() - t0 > RUN_BUDGET_SEC:
            print("[info] guidance: time budget used, the rest on the next run")
            break
        mcap = r["market_cap"] or 0
        rev = r["revenue_estimate"] if r["revenue_estimate"] and (not mcap or r["revenue_estimate"] <= 10 * mcap) else None
        ref = {"revenue_estimate": rev, "eps_estimate": r["eps_estimate"]}
        try:
            status, items, acc, model, note = read_guidance(r["ticker"], r["report_date"], ref)
        except Exception as e:  # noqa: BLE001
            status, items, acc, model, note = None, None, None, None, f"{type(e).__name__}: {str(e)[:150]}"
        if status is None and note and note.startswith(REJECTED_NOTE) and r["attempts"] + 1 >= MAX_ATTEMPTS:
            status, items = "none", []   # the AI did read it each time and found only results: no guidance, not a failure
        if items:
            est = {k: r[k] for k in ("est_nq_eps", "est_nq_rev", "est_fy0_eps", "est_fy0_rev", "est_fy1_eps", "est_fy1_rev", "fy_end")}
            if est.get("est_nq_rev") and mcap and est["est_nq_rev"] > 10 * mcap:      # another currency (as _usable_revenue)
                est = {k: (None if k.endswith("_rev") else v) for k, v in est.items()}
            items = with_estimates(items, est)
        print(f"[info] guidance {r['ticker']} {r['report_date']}: {status or 'failed'}"
              + (f", {len(items)} figure(s)" if items else "") + (f" — {note}" if note else "") + (f" ({model})" if model else ""))
        results.append((r["ticker"], r["report_date"], status or "failed", items, acc, model, note))

    for r in upcoming:
        if time.time() - t0 > RUN_BUDGET_SEC:
            print("[info] guidance: time budget used before all estimates were saved")
            break
        if datetime.now(ET) >= _report_moment(r["report_date"], r["session"]):
            continue                     # the report may be out: Yahoo's rows may have rolled already
        try:
            e = fetch_estimates(r["ticker"], r["fy_end"])
        except Exception as ex:  # noqa: BLE001
            print(f"[warn] guidance estimates {r['ticker']}: {type(ex).__name__}: {ex}")
            continue
        if len(e) > 1:
            estimates.append((r["ticker"], r["report_date"], e))
        time.sleep(0.3)

    if not results and not estimates:
        return 0
    conn = _connect()
    try:
        cur = conn.cursor()
        ensure_table(cur)
        for ticker, d, status, items, acc, model, note in results:
            cur.execute("""INSERT INTO earnings_guidance (ticker, report_date, status, items, accession, model, attempts, note, done_at)
                           VALUES (%s, %s, %s, %s, %s, %s, 1, %s, NOW())
                           ON CONFLICT (ticker, report_date) DO UPDATE SET status = EXCLUDED.status, items = EXCLUDED.items,
                               accession = EXCLUDED.accession, model = EXCLUDED.model, note = EXCLUDED.note, done_at = NOW(),
                               attempts = earnings_guidance.attempts + 1""",
                        (ticker, d, status, psycopg2.extras.Json(items) if items is not None else None, acc, model, note))
        for ticker, d, e in estimates:
            cols = ("est_nq_eps", "est_nq_rev", "est_fy0_eps", "est_fy0_rev", "est_fy1_eps", "est_fy1_rev", "fy_end")
            cur.execute(f"""INSERT INTO earnings_guidance (ticker, report_date, {', '.join(cols)}, est_at)
                            VALUES (%s, %s, {', '.join(['%s'] * len(cols))}, NOW())
                            ON CONFLICT (ticker, report_date) DO UPDATE SET
                            {', '.join(f'{c} = COALESCE(EXCLUDED.{c}, earnings_guidance.{c})' for c in cols)}, est_at = NOW()""",
                        (ticker, d, *[e.get(c) for c in cols]))
        conn.commit()
        cur.close()
    finally:
        conn.close()
    found = sum(1 for x in results if x[2] == "found")
    print(f"[info] guidance: saved {len(results)} report(s) ({found} with guidance), consensus for {len(estimates)}")
    return 0


if __name__ == "__main__":
    import db_meter                       # v38: what this run reads from the database (data_health.py reports it)
    db_meter.install()
    sys.exit(main())
