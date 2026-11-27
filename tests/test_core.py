"""Automatic checks (v25) — run by GitHub Actions (tests.yml) on every upload to main, and by hand from the Actions tab.

Why: there is no Python on the owner's computer, so a typo in a .py file used to show up only when Render failed to
start. These tests catch, before anyone visits the site:
  1. every .py file compiles (syntax errors);
  2. the API server module imports (a missing helper file such as translation_fixes.py / news_links.py, or a name
     error at import time, = "the server does not start");
  3. the scanner and the stock page (Deep Dive) detectors are still TWINS (CLAUDE.md §3): same constants, and the same
     trendline / cup result on fixed real price histories (tests/fixtures/*.csv, as traded, downloaded 30.10.2026);
  4. a few fixed rules that the site shows as labels.
"""
import ast
import importlib
import os
import pathlib
import py_compile
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

# Importing the server must not need the real database or secrets: a closed local port fails at once (init_db is
# wrapped in try/except), and no scheduler token = the scheduler stays off.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:1/test")
os.environ.setdefault("JWT_SECRET", "x" * 40)
os.environ.pop("GITHUB_DISPATCH_TOKEN", None)


# ------------------------------------------------------------------ 1. syntax
@pytest.mark.parametrize("path", sorted(p.name for p in ROOT.glob("*.py")))
def test_python_file_compiles(path):
    py_compile.compile(str(ROOT / path), doraise=True)


# ------------------------------------------------------------------ 2. the server imports
@pytest.fixture(scope="module")
def api():
    return importlib.import_module("api_server")


@pytest.fixture(scope="module")
def scanner():
    return importlib.import_module("pipeline_a_scanner")


def test_api_server_imports(api):
    assert api.app is not None


# ------------------------------------------------------------------ 3. twins
def _constants(path):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    return out


TWIN_CONSTANTS = [
    ("TRENDLINE_SEARCH_DAYS", "_TL_SEARCH_DAYS"), ("TRENDLINE_TOLERANCE", "_TL_TOLERANCE"),
    ("TRENDLINE_TOUCH_TOL", "_TL_TOUCH_TOL"), ("TRENDLINE_MIN_SPAN_BARS", "_TL_MIN_SPAN_BARS"),
    ("TRENDLINE_MIN_TOUCHES", "_TL_MIN_TOUCHES"), ("TRENDLINE_WICK_BARS", "_TL_WICK_BARS"),
    ("FLIP_TEST_BARS", "_FLIP_TEST_BARS"), ("FLIP_MAX_PIERCE", "_FLIP_MAX_PIERCE"),
    ("BOUNCE_MIN_CLOSE_POS", "_BOUNCE_MIN_CLOSE_POS"),                                    # v65
    ("CUP_SEARCH_BARS", "_CUP_SEARCH_BARS"), ("CUP_MIN_BARS", "_CUP_MIN_BARS"), ("CUP_MAX_BARS", "_CUP_MAX_BARS"),
    ("CUP_MIN_DEPTH", "_CUP_MIN_DEPTH"), ("CUP_MAX_DEPTH", "_CUP_MAX_DEPTH"), ("CUP_RIM_TOL", "_CUP_RIM_TOL"),
    ("CUP_FRESH_BARS", "_CUP_FRESH_BARS"), ("CUP_MAX_EXTENSION", "_CUP_MAX_EXTENSION"),
]


def test_twin_constants_match():
    sc, ap = _constants("pipeline_a_scanner.py"), _constants("api_server.py")
    for s_name, a_name in TWIN_CONSTANTS:
        assert s_name in sc and a_name in ap, f"missing constant {s_name} / {a_name}"
        assert sc[s_name] == ap[a_name], f"twins differ: {s_name}={sc[s_name]} but {a_name}={ap[a_name]}"


def _load(ticker):
    return pd.read_csv(FIXTURES / f"{ticker}.csv")


def _norm(v):
    """Comparable form of a detector result (numpy -> plain, floats rounded)."""
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_norm(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float):
        return round(v, 6)
    return v


FIXTURE_TICKERS = ["KO", "ANET", "NOW", "NVDA", "ZS", "AVGO", "RBRK"]     # the last three: downloaded 16.11.2026 (v55)
END_OFFSETS = range(0, 120, 7)       # 18 different "today"s per stock


@pytest.mark.parametrize("ticker", FIXTURE_TICKERS)
@pytest.mark.parametrize("kind", ["support", "resistance"])
def test_trendline_twins_agree(scanner, api, ticker, kind):
    df = _load(ticker)
    found = 0
    for off in END_OFFSETS:
        window = df.iloc[:len(df) - off].tail(scanner.TRENDLINE_SEARCH_DAYS).reset_index(drop=True)
        a = scanner.fit_trendline(window, kind)
        b = api._fit_trendline(window, kind)
        assert _norm(a) == _norm(b), f"{ticker} {kind} (today = {off} bars before the last): scanner and stock page differ"
        if kind == "resistance":     # v55: also with the last bars left free (a line broken a few sessions ago)
            assert _norm(scanner.fit_trendline(window, kind, None, 4)) == _norm(api._fit_trendline(window, kind, None, 4)), \
                f"{ticker} broken line (today = {off} bars before the last): scanner and stock page differ"
        found += a is not None
    assert found >= 0     # the point is agreement; a stock may have no line on some days


@pytest.mark.parametrize("ticker", FIXTURE_TICKERS)
def test_cup_twins_agree(scanner, api, ticker):
    df = _load(ticker)
    for off in END_OFFSETS:
        closes = df["Close"].iloc[:len(df) - off].tail(scanner.CUP_SEARCH_BARS).to_numpy(dtype=float)
        assert _norm(scanner.find_cup(closes)) == _norm(api._find_cup(closes)), \
            f"{ticker} cup (today = {off} bars before the last): scanner and stock page differ"


def _hist(ticker):
    df = _load(ticker)
    return df.set_index(pd.to_datetime(df["Date"]))[["Open", "High", "Low", "Close", "Volume"]]


@pytest.mark.parametrize("ticker", FIXTURE_TICKERS)
def test_signal_twins_agree(scanner, api, ticker):
    """v27: for 150 different "today"s — when the scanner fires bull flag / double bottom / support bounce / 150-MA
    reclaim, the stock page finds the same pattern, as a breakout of TODAY, at the same level (and the other way round)."""
    df = _hist(ticker)
    for off in range(150):
        h = df.iloc[:len(df) - off]
        where = f"{ticker}, today = {off} bars before the last"

        s, a = scanner.detect_bull_flag(h), api._setup_bull_flag(h)
        assert (s is not None) == bool(a and a["stage"] == "triggered" and a["days_since_breakout"] == 0), f"bull flag: {where}"

        s, a = scanner.detect_double_bottom(h), api._setup_double_bottom(h)
        assert (s is not None) == bool(a and a["stage"] == "triggered" and a["days_since_breakout"] == 0), f"double bottom: {where}"
        if s:
            assert s["neckline"] == a["key_level"], f"double bottom level: {where}"

        s, a = scanner.detect_horizontal_level_bounce(h), api._scanner_rule_level_bounce(h)
        assert (s is None) == (a is None), f"support bounce: {where}"
        if s:
            assert (s["level"], s["touches"]) == (a["key_level"], a["touches"]), f"support bounce level: {where}"
            assert bool(s.get("role_reversal")) == bool(a.get("role_reversal")), f"support bounce kind: {where}"

        s, a = scanner.detect_ma150_breakout(h), api._scanner_rule_ma150_breakout(h)
        assert (s is None) == (a is None), f"150-MA reclaim: {where}"
        if s:
            assert a["days_since_cross"] == (1 if s["held"] else 0) and s["ma150"] == a["key_level"], f"150-MA reclaim: {where}"


def _setup_answer(api, monkeypatch, ticker, drop_last=0):
    hist = _hist(ticker)
    hist = hist.iloc[:len(hist) - drop_last]
    monkeypatch.setattr(api.yf, "Ticker", lambda t: type("T", (), {"history": lambda self, **k: hist})())
    monkeypatch.setattr(api, "_latest_scan_signal", lambda t: None)
    return getattr(api.get_technical_setup, "__wrapped__", api.get_technical_setup)(ticker)


def test_drawings_the_owner_made_by_hand(api, scanner, monkeypatch):
    """v55 (owner's TradingView screenshots, 13.11.2026). What the stock page has to find by itself:
    ZS: the May/August highs at $191.25, broken in September, tested from above now (the page showed nothing at all).
    AVGO: the line of falling highs from the $495 high of 3 June, broken on 12.11 and tested on 13.11, and the support
    at the 30 March low, $289.96, touched on 5-6 November. No rectangle through the middle of the sideways months."""
    zs = _setup_answer(api, monkeypatch, "ZS")
    c = zs["candidates"][0]
    assert zs["has_setup"] and c["pattern"] == "horizontal_support_bounce" and c["role_reversal"] and c["key_level"] == 191.25
    assert [(z["kind"], z["lo"], z["hi"]) for z in zs["zones"]] == [("support", 190.47, 191.25)]
    assert "התנגדות" in c["explanation_he"] and "resistance" in c["explanation_en"]

    av = _setup_answer(api, monkeypatch, "AVGO")
    by = {c["pattern"]: c for c in av["candidates"]}
    line = by["descending_trendline_breakout"]
    assert av["candidates"][0] is line and line["days_since_breakout"] == 1 and line["retest"]
    assert line["lines"][0]["points"][0] == ["2026-06-03", 495.0] and 340 < line["key_level"] < 347
    sup = by["horizontal_support_bounce"]
    assert sup["key_level"] == 289.96 and sup["lines"][0]["from"] == "2026-03-30" and sup["days_since_test"] == 5
    assert not [z for z in av["zones"] if z["lo"] < 347.01 < z["hi"] * 1.03], "a zone drawn through the sideways months"
    assert av["price_series"][0][0] <= "2026-03-30"          # the chart reaches back to the line's first wick

    # the scanner: the breakout day itself is found although the day before poked its high over the line
    h = _hist("AVGO")
    assert scanner.detect_descending_trendline_breakout(h.iloc[:-1]) is not None
    assert scanner.detect_descending_trendline_breakout(h) is None           # the next day it is not a new breakout
    assert api._scanner_rule_desc_trendline_breakout(h.iloc[:-1])["stage"] == "triggered"


def test_scanner_flags_old_resistance_tested_as_support(scanner, api):
    """v55b (approved by the owner 16.11.2026): ZS on 13.11.2026 — the $191.25 level of the May/August highs, broken in
    September, tested from above — is a scan result, and the stock page shows the same level."""
    h = _hist("ZS")
    s = scanner.detect_horizontal_level_bounce(h)
    assert s and s["role_reversal"] and s["level"] == 191.25 and s["touches"] == 3
    a = api._scanner_rule_level_bounce(h)
    assert a["key_level"] == 191.25 and a["role_reversal"] and a["lines"][0]["from"] == "2026-05-26"
    assert scanner.detect_horizontal_level_bounce(h.iloc[:-1]) is None       # 12.11: the low went 3.5% under the level
    assert (scanner.close_crossings(h["Close"].values, 190.0, 192.0)
            == api._crossings(h["Close"].values, 190.0, 192.0))
    # the score still reads the number of tests from the text
    assert scanner.swing_score_of("horizontal_level_bounce", 0, None, 50, None,
                                  "Testing old resistance at $191.25 as support (tested 4x as resistance before the breakout)") \
        > scanner.swing_score_of("horizontal_level_bounce", 0, None, 50, None, "")


def test_stock_with_no_pattern_still_gets_its_chart(api, monkeypatch):
    """v55: no named pattern used to mean no chart and no zones. The chart and what was found near the price are returned
    for every stock, and has_setup only says whether a pattern is named."""
    monkeypatch.setattr(api, "_compute_setup_candidates", lambda hist, ticker: [])
    r = _setup_answer(api, monkeypatch, "ZS")
    assert r["has_setup"] is False and r["candidates"] == [] and len(r["price_series"]) >= 160
    assert r["zones"] and r["zones"][0]["kind"] == "support"


def test_a_level_the_price_keeps_crossing_is_not_a_level(api):
    import numpy as _np
    c = _np.array([10, 12, 10, 12, 10, 12, 11, 12.0])
    assert api._crossings(c, 10.9, 11.1) == 5 and api._crossings(_np.array([10, 10.5, 12, 13.0]), 10.9, 11.1) == 1
    assert api._crossings(_np.array([11.0]), 10.9, 11.1) == 0


# ------------------------------------------------------------------ 4. fixed rules shown on the site
def test_market_state_labels(api):
    assert api._trend_label(110, 105, 100) == "up"
    assert api._trend_label(90, 95, 100) == "down"
    assert api._trend_label(102, 105, 100) == "mixed"
    assert api._trend_label(None, 1, 1) is None
    assert [api._vix_label(v) for v in (12, 15, 19.9, 20, 29.9, 30)] == ["calm", "normal", "normal", "elevated", "elevated", "high"]
    assert [api._curve_label(v) for v in (-0.1, 0, 0.49, 0.5)] == ["inverted", "flat", "flat", "normal"]


def test_signal_context_slice(api):
    sigs = [{"ret": r, "spy": 1.0} for r in (5, -2, 3, 4, -1, 2, 6, -3, 1, 2)]
    s = api._sigctx_slice(sigs)
    assert s["n"] == 10 and s["enough"] and s["win_rate"] == 70 and s["avg"] == 1.7 and s["spy_avg"] == 1.0
    assert api._sigctx_slice([]) == {"n": 0}


def test_earnings_move_states(api):
    """v40: the reaction to a report says whether it is final, live, after-hours or pre-market — and is measured from the
    last close before the report reached the market (Friday for a Monday-morning report, Monday for a Monday-evening one)."""
    import datetime as _d
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    D = _d.date
    thu, fri, mon = D(2026, 11, 5), D(2026, 11, 6), D(2026, 11, 9)

    def at(day, hh, mm=0):
        return _d.datetime(day.year, day.month, day.day, hh, mm, tzinfo=ny)

    def intra(points):
        return pd.Series([p for _, p in points], index=pd.DatetimeIndex([pd.Timestamp(t) for t, _ in points]))

    daily_mon = pd.Series([100.0, 110.0, 120.0], index=[thu, fri, mon])     # Monday's bar is there from the open
    ticks = intra([(at(fri, 15, 45), 110), (at(fri, 18), 111), (at(mon, 7), 115), (at(mon, 10), 118),
                   (at(mon, 15, 45), 120), (at(mon, 17, 30), 132)])
    mv = api._earnings_move

    m = mv(daily_mon, ticks, mon, "amc", at(mon, 18))           # evening report, the same evening
    assert m["state"] == "post" and m["pct"] == 10.0 and m["base_day"] == "2026-11-09"
    assert mv(daily_mon, ticks, mon, "amc", at(mon, 14)) is None  # before the close: not out yet
    m = mv(daily_mon, ticks[ticks.index <= at(mon, 10)], mon, "bmo", at(mon, 10, 5))   # morning report, market open
    assert m["state"] == "live" and m["pct"] == round((118 / 110 - 1) * 100, 2) and m["base_day"] == "2026-11-06"
    m = mv(daily_mon.iloc[:2], ticks[ticks.index <= at(mon, 7)], mon, "bmo", at(mon, 7, 10))   # before the open
    assert m["state"] == "pre" and m["pct"] == round((115 / 110 - 1) * 100, 2)
    m = mv(daily_mon, ticks, thu, "amc", at(mon, 11))           # Thursday evening -> Friday's session, closed
    assert m["state"] == "closed" and m["day"] == "2026-11-06" and m["pct"] == 10.0 and "since_pct" not in m
    m = mv(daily_mon, ticks, thu, "amc", at(mon, 17))           # ...and once Monday closed, the move since then too
    assert m["since_day"] == "2026-11-09" and m["since_pct"] == 20.0
    m = mv(daily_mon, ticks, mon, "bmo", at(mon, 16, 10))       # morning report after the bell = final
    assert m["state"] == "closed" and m["pct"] == round((120 / 110 - 1) * 100, 2)
    assert mv(daily_mon, ticks, D(2026, 11, 10), "bmo", at(mon, 18)) is None   # tomorrow's report


def test_guidance_figures_are_checked_against_the_release():
    """v40: what the AI copies out of a press release is taken only when the release says it (checked 9.11.2026 on the real
    releases of AMD, ANET, DDOG, NET, UBER, QCOM, EMR, CEG, ABNB and MCD; these lines are shortened from them)."""
    import datetime as _d
    import json as _j
    import earnings_guidance as eg
    rel = ("Third Quarter Financial Highlights Revenue of $3.66 billion, an increase of 20.5% compared to the second quarter. "
           "Financial Outlook For the fourth quarter of 2026, we expect: Revenue of $3.9 - $4.0 billion; Non-GAAP diluted net "
           "income per share of $1.26 - $1.30. Gross Bookings of $63.25 billion to $65.25 billion. Raising full-year revenue "
           "guidance to $14.5 - $14.6 billion. Net sales in Japan are expected to reach 950 billion.")
    ref, day = {"revenue_estimate": 3.33e9, "eps_estimate": 1.08}, _d.date(2026, 11, 3)

    def ask(**kw):
        it = dict(period="quarter", quarter=4, fiscal_year=2026, metric="revenue", basis=None, low="3.9", high="4.0",
                  unit="billion", change=None, quote="Revenue of $3.9 - $4.0 billion;")
        it.update(kw)
        return eg.clean_guidance(_j.dumps({"guidance": [it]}), rel, ref, day)

    got = ask()
    assert got[0]["low"] == 3.9e9 and got[0]["high"] == 4.0e9
    assert ask(metric="eps", low="1.26", high="1.30", unit="dollars", quote="Non-GAAP diluted net income per share of $1.26 - $1.30.")[0]["basis"] == "adjusted"
    year = ask(period="year", quarter=None, low="14.5", high="14.6", change="raised", quote="Raising full-year revenue guidance to $14.5 - $14.6 billion.")
    assert year[0]["change"] == "raised"
    for bad in (dict(quote="Revenue of $4.1 - $4.2 billion;", low="4.1", high="4.2"),          # not in the release
                dict(low="3.85", high="4.05"),                                               # numbers it computed itself
                dict(quote="Revenue of $3.66 billion, an increase of 20.5%", low="3.66", high="3.66"),   # this quarter's result
                dict(quote="Gross Bookings of $63.25 billion to $65.25 billion.", low="63.25", high="65.25"),   # not revenue
                dict(unit="million"),                                                        # wrong unit
                dict(quote="Net sales in Japan are expected to reach 950 billion.", low="950", high="950")):  # no dollar sign
        with pytest.raises(ValueError):
            ask(**bad)
    assert eg.clean_guidance('{"guidance": []}', rel, ref, day) == []          # no guidance = a real answer
    assert eg.guidance_source("Revenue was $5 billion. Net income was $1 billion.") == ""   # nothing to ask the AI
    # the consensus of the same period: quarter = next quarter, year = by the fiscal year's end
    items = eg.with_estimates(got + year, {"est_nq_rev": 3.9e9, "est_fy0_rev": 14.0e9, "fy_end": _d.date(2026, 12, 31)})
    assert items[0]["est"] == 3.9e9 and items[0]["vs_pct"] == 1.28 and items[1]["vs_pct"] == round((14.55 / 14.0 - 1) * 100, 2)


def test_guidance_only_results_is_no_guidance(monkeypatch):
    """v40e: ROIV 9.11.2026: the one model that answered copied this quarter's results (rejected), the others were 404 / timed
    out, and after 6 attempts the report stayed "failed". An answer that was read and rejected says so in the note."""
    import datetime as _d
    import json as _j
    import earnings_guidance as eg
    import llm_provider
    import pipeline_a_scanner as sc
    rel = "Revenue was $50 million. Outlook: we expect the trial to read out next year, in 2027, as planned."
    monkeypatch.setattr(sc, "_sec_cik_for", lambda t: "1")
    monkeypatch.setattr(sc, "_sec_release_accessions", lambda cik, d: ["acc-1"])
    monkeypatch.setattr(sc, "_sec_release_text", lambda cik, acc: rel)
    result = {"guidance": [dict(period="year", quarter=None, fiscal_year=2026, metric="revenue", basis=None, low="50", high="50",
                                unit="million", change=None, quote="Revenue was $50 million.")]}

    def chat(msgs, validate=None, **kw):
        try:
            validate(_j.dumps(result))
        except ValueError:
            pass
        raise RuntimeError("all models failed — last: google/gemma-4-31b-it: ReadTimeout")
    monkeypatch.setattr(llm_provider, "chat", chat)
    status, items, acc, model, note = eg.read_guidance("ROIV", _d.date(2026, 11, 9), {"revenue_estimate": 5e7, "eps_estimate": None})
    assert status is None and note.startswith(eg.REJECTED_NOTE) and acc == "acc-1"

    def timeout(msgs, validate=None, **kw):
        raise RuntimeError("all models failed — last: ReadTimeout")
    monkeypatch.setattr(llm_provider, "chat", timeout)
    assert eg.read_guidance("ROIV", _d.date(2026, 11, 9), {})[4].startswith("AI: ")      # never answered = tried again


def test_guidance_health_rule():
    import data_health
    rows = [{"ticker": t, "status": "found", "items": [{"metric": "revenue", "vs_pct": 2.0}]} for t in "ABCD"]
    assert data_health.guidance_problems(rows + [{"ticker": "E", "status": "none", "items": []}]) == []
    assert data_health.guidance_problems(rows + [{"ticker": x, "status": None, "items": None} for x in "EFG"])
    assert data_health.guidance_problems([{"ticker": "Z", "status": "found", "items": [{"metric": "eps", "vs_pct": -70.0}]}])


def test_fundamentals_stale_series():
    """v27: a series the company stopped reporting (JPM debt 2014, BRK EPS 2013) is dropped, a recent one is kept."""
    import fundamentals
    assert fundamentals._drop_if_stale({"2014-06-30": 1.0}, "2026-09-30") == {}
    assert fundamentals._drop_if_stale({"2026-06-30": 1.0}, "2026-09-30") == {"2026-06-30": 1.0}
    assert fundamentals._drop_if_stale({}, "2026-09-30") == {}


def test_fundamentals_warm_up_uses_one_connection(monkeypatch):
    """v48: the nightly warm-up opened three connections per stock (2,192 on 10.11.2026 = the day's largest reader of
    Neon's network quota). One shared connection now, reopened if it drops; the old copy is read only when the new
    build lacks something that can be carried over, and then only those fields."""
    import psycopg2
    import fundamentals as f
    opened, sql = [], []

    class Cur:
        def execute(self, q, params=None):
            sql.append(" ".join(q.split()))
            if "drop-me" in str(params):
                raise psycopg2.Error("connection dropped")

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def close(self):
            pass

    class Conn:
        closed = 0

        def cursor(self, **kw):
            return Cur()

        def commit(self):
            pass

        def close(self):
            self.closed = 1

    def connect(url):
        opened.append(Conn())
        return opened[-1]
    monkeypatch.setattr(psycopg2, "connect", connect)
    monkeypatch.setenv("DATABASE_URL", "postgres://test")
    monkeypatch.setattr(f, "_warm_tickers", lambda ctx: ["KO", "PEP", "MSFT", "AES"])
    monkeypatch.setattr(f, "fetch_cik_map", lambda: {})

    class Inline:                              # the workers, one after another (and the browser check has no threads)
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def map(self, fn, items):
            return [fn(i) for i in items]
    monkeypatch.setattr(f, "ThreadPoolExecutor", Inline)
    monkeypatch.setattr(f, "apply_stock_estimates", lambda data, t, ctx: None)
    full = {"has_fundamentals": True, "eps_labels": ["Q3"], "pe_band": {"lo": 1}}
    monkeypatch.setattr(f, "build_fundamentals", lambda t, m, info_getter=None:
                        dict(full, ticker=t) if t != "AES" else {"has_fundamentals": True, "ticker": t})
    saved = []
    monkeypatch.setattr(f, "db_save", lambda cur, t, data: saved.append(t))
    assert f.warm_main() == 0
    assert len(opened) == 1 and opened[0].closed and saved == ["KO", "PEP", "MSFT", "AES"]
    extras = [s for s in sql if "jsonb_build_object" in s]
    assert len(extras) == 1 and "'pe_band', data->'pe_band'" in extras[0]      # only AES lacked something
    assert not any(s.startswith("SELECT data,") for s in sql)                  # the whole stored document is never read

    # a dropped connection costs that one stock; the next one opens a new connection
    opened.clear()
    saved.clear()
    monkeypatch.setattr(f, "_warm_tickers", lambda ctx: ["KO", "PEP"])

    def save(cur, t, data):
        cur.execute("INSERT", ("drop-me" if t == "KO" else t,))
        saved.append(t)
    monkeypatch.setattr(f, "db_save", save)
    assert f.warm_main() == 0 and saved == ["PEP"] and len(opened) == 2 and opened[0].closed


def test_touch_alert_range():
    """v27: a "touch" alert looks at the low..high of the bars since it was set — the bar running at that moment counts,
    earlier bars do not."""
    import datetime as _dt
    import push_alerts
    utc, five = _dt.timezone.utc, _dt.timedelta(minutes=5)
    t0 = _dt.datetime(2026, 11, 2, 15, 0, tzinfo=utc)
    bars = [(t0, 95.0, 96.0, five), (t0 + five, 97.0, 98.0, five), (t0 + 2 * five, 98.5, 99.5, five)]
    assert push_alerts.range_since(bars, t0 + _dt.timedelta(minutes=7)) == (97.0, 99.5)   # set during the 2nd bar
    assert push_alerts.range_since(bars, t0 + _dt.timedelta(minutes=20)) is None           # nothing traded since
    lo, hi = push_alerts.range_since(bars, t0 - five)
    assert lo <= 95.5 <= hi and not (lo <= 100 <= hi)


def test_line_alerts_follow_the_line():
    """v60 (owner, 17.11.2026): an alert on a line of the chart. A trend line moves one step per session of the stock
    (a holiday is not a step), touch = a 5-minute bar met the line of its own day, or the price jumped over it; a
    zone counts from its bottom to its top; above / below of a zone = over its top / under its bottom."""
    import datetime as _dt
    import push_alerts
    D = _dt.date
    ses = [D(2026, 11, 20), D(2026, 11, 23), D(2026, 11, 24), D(2026, 11, 25), D(2026, 11, 27)]   # 26.11 = Thanksgiving
    assert push_alerts.line_value(100.0, -1.0, D(2026, 11, 20), D(2026, 11, 20), ses) == 100.0
    assert push_alerts.line_value(100.0, -1.0, D(2026, 11, 20), D(2026, 11, 27), ses) == 96.0      # 4 sessions, not 5
    assert push_alerts.line_value(100.0, -1.0, D(2026, 11, 20), D(2026, 11, 30), ses) == 95.0      # a session just opened
    assert push_alerts.line_value(100.0, -1.0, D(2026, 11, 1), D(2026, 11, 27), ses) is None       # anchor before the bars

    utc, five = _dt.timezone.utc, _dt.timedelta(minutes=5)
    t0 = _dt.datetime(2026, 11, 27, 15, 0, tzinfo=utc)                  # 10:00 New York, on 27.11 (line = 96)
    level_of = lambda st: (push_alerts.line_value(100.0, -1.0, D(2026, 11, 20), push_alerts._et_date(st), ses),) * 2
    above = [(t0, 97.0, 98.0, five), (t0 + five, 96.5, 97.5, five)]
    assert push_alerts.line_touched(above, t0 - five, level_of) is False
    assert push_alerts.line_touched(above + [(t0 + 2 * five, 95.5, 96.4, five)], t0 - five, level_of) is True
    assert push_alerts.line_touched(above + [(t0 + 2 * five, 94.0, 95.0, five)], t0 - five, level_of) is True   # gap
    assert push_alerts.line_touched(above, t0 + _dt.timedelta(hours=1), level_of) is None                      # no bars yet
    zone = lambda st: (90.0, 92.0)
    assert push_alerts.line_touched([(t0, 92.5, 93.0, five)], t0 - five, zone) is False
    assert push_alerts.line_touched([(t0, 91.5, 93.0, five)], t0 - five, zone) is True

    class Cur:
        def __init__(self, rows):
            self.rows, self.sql = rows, []

        def execute(self, q, params=None):
            self.sql.append((" ".join(q.split()), params))

        def fetchall(self):
            return self.rows

        def close(self):
            pass

    class Conn:
        def __init__(self, rows):
            self.c = Cur(rows)

        def cursor(self):
            return self.c

        def commit(self):
            pass

    created = t0 - _dt.timedelta(days=1)
    rows = [  # id, user, ticker, direction, price, created, kind, anchor, slope, lo, hi, he, en, ma_days
        (1, 7, "KO", "below", 100.0, created, "trend", D(2026, 11, 20), -1.0, None, None, "תמיכה עולה", "Rising support", None),
        (2, 7, "KO", "above", 92.0, created, "zone", None, None, 90.0, 92.0, "אזור", "Zone", None),
        (3, 7, "KO", "touch", 100.0, created, "trend", D(2026, 11, 20), -1.0, None, None, "קו", "Line", None),
        (4, 7, "KO", "above", 97.0, created, None, None, None, None, None, "התנגדות אופקית", "Horizontal resistance", None),
    ]
    sent = []
    old_send, old_ready = push_alerts._send_all, push_alerts.ready
    try:
        push_alerts.ready = lambda: True
        push_alerts._send_all = lambda cur, uid, payload: (sent.append((payload("he"), payload("en"))) or (1, 0))
        conn = Conn(rows)
        n = push_alerts.check_price_alerts(conn, {"KO": 95.5}, {"KO": above + [(t0 + 2 * five, 95.5, 96.4, five)]}, {"KO": ses})
    finally:
        push_alerts._send_all, push_alerts.ready = old_send, old_ready
    closed = [p[1] for q, p in conn.c.sql if q.startswith("UPDATE price_alerts")]
    # 1: the line is 96 today, the price 95.5 = below; 2: 95.5 is over the zone's top; 3: touched; 4: 95.5 < 97 = no
    assert n == 3 and closed == [1, 2, 3]
    he, en = sent[0]
    assert "ירדה מתחת לקו: תמיכה עולה" in he["title"] and "$96.00" in he["body"] and "fell below the line" in en["title"]

    # v60b: a moving average. 4 sessions of closes 100 before today (a 5-day average for the test, via MA_DAYS):
    # the price is AT its average when P = sum of the 4 earlier closes / 4 = 100
    closes = [(D(2026, 11, d), 100.0) for d in (20, 23, 24, 25)] + [(D(2026, 11, 27), 97.0)]
    assert push_alerts.ma_base(closes, D(2026, 11, 27), 5) == 400.0
    assert push_alerts.ma_base(closes, D(2026, 11, 25), 5) is None                       # not enough closes
    ma_rows = [(5, 7, "KO", "below", 100.0, created, "ma", None, None, None, None, "ממוצע נע 5", "5-day MA", 5),
               (6, 7, "KO", "touch", 100.0, created, "ma", None, None, None, None, "ממוצע נע 5", "5-day MA", 5),
               (7, 7, "KO", "above", 100.0, created, "ma", None, None, None, None, "ממוצע נע 5", "5-day MA", 5)]
    sent.clear()
    old_days = push_alerts.MA_DAYS
    try:
        push_alerts.MA_DAYS = (5,)
        push_alerts.ready = lambda: True
        push_alerts._send_all = lambda cur, uid, payload: (sent.append((payload("he"), payload("en"))) or (1, 0))
        conn = Conn(ma_rows)
        bars27 = [(t0, 98.0, 99.0, five), (t0 + five, 96.5, 98.2, five)]                  # under 100 all day: no touch
        n = push_alerts.check_price_alerts(conn, {"KO": 97.0}, {"KO": bars27}, {"KO": [d for d, _ in closes]}, {"KO": closes})
    finally:
        push_alerts._send_all, push_alerts.ready, push_alerts.MA_DAYS = old_send, old_ready, old_days
    closed = [p[1] for q, p in conn.c.sql if q.startswith("UPDATE price_alerts")]
    assert n == 1 and closed == [5]
    assert "$99.40" in sent[0][0]["body"]                     # the average shown = (400 + 97) / 5


def test_release_history_dates_are_trading_days(api):
    """v61: every copied release day is a weekday (a typo would put the S&P 500's move of a wrong day next to the
    release), the static schedules join the history once their day passes, nothing twice. A market holiday is allowed
    only where it really happened — the jobs report of Good Friday 3.4.2026 (no session: _event_stats skips it)."""
    import datetime as _dt
    import econ_calendar as ec
    on_holiday = []
    for kind, days in ec.RELEASE_HISTORY.items():
        assert days == sorted(days) and len(set(days)) == len(days), kind
        for s in days:
            d = _dt.date.fromisoformat(s)
            assert d.weekday() < 5, (kind, s)
            if d in api._nyse_holidays(d.year):
                on_holiday.append((kind, s))
    assert on_holiday == [("nfp", "2026-04-03")]
    cpi = ec.past_release_dates("cpi", _dt.date(2026, 12, 31))
    assert _dt.date(2026, 12, 10) in cpi and len(cpi) == len(set(cpi)) and cpi == sorted(cpi)   # from BLS_STATIC
    assert _dt.date(2026, 12, 10) not in ec.past_release_dates("cpi", _dt.date(2026, 11, 17))
    assert _dt.date(2026, 12, 9) in ec.past_release_dates("fomc", _dt.date(2026, 12, 9))         # FOMC_DECISIONS
    assert set(ec.HISTORY_KIND.values()) == set(ec.RELEASE_HISTORY)
    for t in ec.HISTORY_KIND:
        assert t in ec.CATALOG


def test_event_history_numbers(api):
    """v61: the S&P 500 on release days vs an ordinary day: close vs the previous close, the last N releases."""
    import datetime as _dt
    days = pd.bdate_range("2026-01-01", periods=80)
    closes = pd.Series(100.0, index=[d.date() for d in days])
    # ordinary days: +0.5% / -0.5% alternately; release days (every 8th): +2% / -1%
    v, rel = 100.0, []
    for i, d in enumerate(closes.index):
        if i and i % 8 == 0:
            v *= 1.02 if (i // 8) % 2 else 0.99
            rel.append(d)
        elif i:
            v *= 1.005 if i % 2 else 0.995
        closes[d] = v
    s = api._event_stats(closes, rel)
    assert s["n"] == 9 and s["up"] == 5 and s["down"] == 4
    assert abs(s["avg_abs"] - (5 * 2 + 4 * 1) / 9) < 0.02 and abs(s["normal_abs"] - 0.5) < 0.01
    assert s["last"][0][0] == rel[-1].isoformat() and len(s["last"]) == 3
    assert api._event_stats(closes, rel[:7]) is None                                   # too few
    assert api._event_stats(closes, rel + [_dt.date(2030, 1, 1)])["n"] == 9           # a day with no close is skipped


def test_move_size_against_atr(api):
    """v61: a move measured in the stock's own ATR, from the sessions before today (today's bar is still running)."""
    import datetime as _dt
    assert [api.move_size_word(r) for r in (None, 0.4, 1.49, 1.5, 2.9, 3.0)] == [None, "normal", "normal", "large", "large", "rare"]
    idx = pd.bdate_range("2026-09-01", periods=40)
    h = pd.DataFrame({"High": 102.0, "Low": 98.0, "Close": 100.0}, index=idx)
    h.loc[idx[-1], ["High", "Low"]] = [130.0, 90.0]                 # today: a huge running bar
    today = idx[-1].date()
    assert api._atr_pct_before_today(h, today) == 4.0               # the range of the earlier days only
    assert api._atr_pct_before_today(h, today + _dt.timedelta(days=1)) > 4.0


def test_stock_news_block_is_reported(api):
    """v62: Google News refusing most of today's stock-news searches = warn in /api/data-health (live.stock_news)."""
    h = api.stock_news_health
    assert h({})["status"] == "info"
    assert h({"ok": 20, "failed": 2})["status"] == "ok"            # a hiccup
    assert h({"ok": 20, "failed": 5})["status"] == "ok"            # under half
    assert h({"ok": 2, "failed": 2})["status"] == "ok"             # too few to tell
    assert h({"ok": 3, "failed": 3})["status"] == "warn"
    assert h({"ok": 0, "failed": 4})["status"] == "warn"
    saved = dict(api._stock_news_stats)
    try:
        api._stock_news_stats.clear()
        api._news_stat(True)
        api._news_stat(False, "HTTPError: 429")
        s = api._stock_news_stats
        assert (s["ok"], s["failed"], s["last_error"]) == (1, 1, "HTTPError: 429")
    finally:
        api._stock_news_stats.clear()
        api._stock_news_stats.update(saved)


def test_move_headlines_that_explain_nothing_are_left_out(api):
    """v61b: the first live answer (17.11.2026) put predictions, filings and old stories next to today's move."""
    bad = ["Applied Optoelectronics (AAOI) Stock Price Prediction: Bearish structure persists as AAOI loses the 20-day zone",
           "Form 4 Forgent Power Solutions: Neel Bhatia (Director) For: 16 November By Investing.com",
           "Why Is Natera Stock Up Nearly 41% Since May?",
           "NIO (NIO) schedules Q3 results for December 1, with an earnings call at 7 a.m. Eastern.",
           "Credo Technology Group Holding Ltd (CRDO) Stock Down 8.2% -- Now Undervalued? GF Score: 77/100",
           "Caris Life Sciences Presented Research Demonstrating the Value of Whole Transcriptome Sequencing",
           # v66 (19.11.2026, next to SLS -23%)
           "Is SELLAS Life Sciences Group (SLS) Fully Valued As REGAL Trial Success Lifts Expectations?",
           "Palantir (PLTR) Valuation Check After Shares Slide 9% In A Week",
           "Assessing Oracle (ORCL) Valuation After Its Cloud Contract Win"]
    good = ["B.Riley cuts Lumentum stock price target on Marvell proxy valuation",
            "Palo Alto Networks Acquires Applied Compute for US$4.5bn",
            "Merck to buy biotech in deal valued at $10 billion",
            "Nasdaq Composite Falls 2.49% as Tempus AI Leads AI Stock Declines",
            "Nvidia shares jump after earnings beat", "FDA approves Natera test, shares soar"]
    assert [t for t in bad if api.move_headline_ok(t)] == []
    assert all(api.move_headline_ok(t) for t in good)


def test_line_alert_input_is_checked(api):
    """v60: what the site sends for a line alert is checked before it is saved."""
    import pytest as _pt
    A = api.PriceAlertIn
    assert api._line_alert_fields(A(ticker="KO", direction="touch", price=60)) == (None,) * 8
    lv = api._line_alert_fields(A(ticker="KO", direction="above", price=60, kind="level", label_he="התנגדות <b>", label_en="Res"))
    assert lv == (None, None, None, None, None, "התנגדות b", "Res", None)
    assert api._line_alert_fields(A(ticker="KO", direction="touch", price=60, kind="ma", ma_days=150))[::7] == ("ma", 150)
    with _pt.raises(api.HTTPException):
        api._line_alert_fields(A(ticker="KO", direction="touch", price=60, kind="ma", ma_days=20))
    z = api._line_alert_fields(A(ticker="KO", direction="touch", price=61, kind="zone", zone_lo=60, zone_hi=62))
    assert z[0] == "zone" and z[3:5] == (60, 62)
    today = api.dt.now(api.timezone.utc).date().isoformat()
    tr = api._line_alert_fields(A(ticker="KO", direction="below", price=60, kind="trend", anchor_date=today, slope=-0.25))
    assert tr[0] == "trend" and tr[2] == -0.25
    for bad in (dict(kind="zone", zone_lo=62, zone_hi=60), dict(kind="trend", anchor_date="2020-01-01", slope=0.1),
                dict(kind="trend", anchor_date=today, slope=50), dict(kind="trend", slope=0.1)):
        with _pt.raises(api.HTTPException):
            api._line_alert_fields(A(ticker="KO", direction="touch", price=60, **bad))


def test_push_not_lost_on_temporary_failure():
    """v46: a notification is logged before it is sent (so two servers never send it twice). If no device got it and
    the failure was temporary, the log row is taken back so the next run sends it; a refusal stays logged."""
    import push_alerts

    class Cur:
        def __init__(self):
            self.sql, self.rowcount = [], 1

        def execute(self, q, params=None):
            self.sql.append(" ".join(q.split()))

        def fetchall(self):
            return [(1, "https://fcm.googleapis.com/x", "k", "a", "he", True, True, True, True)]

    def run(result):
        cur = Cur()
        push_alerts.last_unsent = 0
        n = push_alerts._push_once(cur, 7, "sig:KO:2026-11-11", lambda lang: {"title": "t"})
        return n, any(s.startswith("DELETE FROM push_log") for s in cur.sql), push_alerts.last_unsent

    old = push_alerts._send_one
    try:
        push_alerts._send_one = lambda sub, payload: "ok"
        assert run("ok") == (1, False, 0)
        push_alerts._send_one = lambda sub, payload: "retry"
        assert run("retry") == (0, True, 1)
        push_alerts._send_one = lambda sub, payload: "error"       # refused: trying again would change nothing
        assert run("error") == (0, False, 0)
    finally:
        push_alerts._send_one = old

    # one failed attempt with no answer, then success = delivered, after exactly two attempts
    calls = []

    def flaky(**kw):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("timed out")

    saved = (push_alerts.webpush, push_alerts.VAPID_PRIVATE_KEY, push_alerts.RETRY_PAUSE_SEC)
    try:
        push_alerts.webpush, push_alerts.VAPID_PRIVATE_KEY, push_alerts.RETRY_PAUSE_SEC = flaky, "test-key", 0
        sub = {"endpoint": "https://fcm.googleapis.com/x", "p256dh": "k", "auth": "a"}
        assert push_alerts._send_one(sub, {"title": "t"}) == "ok" and len(calls) == 2
    finally:
        push_alerts.webpush, push_alerts.VAPID_PRIVATE_KEY, push_alerts.RETRY_PAUSE_SEC = saved
    assert push_alerts._temporary(None) and push_alerts._temporary(503) and push_alerts._temporary(429)
    assert not push_alerts._temporary(403) and not push_alerts._temporary(400)


def test_press_release_eps(scanner):
    """v33: EPS from the company's release — real sentences of 3.11.2026 and before. Only the adjusted figure of THIS
    quarter counts; a wrong number is worse than a blank."""
    p = scanner._parse_press_release_eps
    amd = ("net income was $2.4 billion and diluted earnings per share was $1.46. On a non-GAAP(*) basis, gross margin was 56%, "
           "operating income was $3.9 billion, net income was $3.5 billion and diluted earnings per share was $2.09. Next")
    assert p(amd, 1.94) == 2.09                                                   # not the GAAP $1.46
    assert p("• GAAP and non-GAAP diluted earnings per share of $1.22 and $1.20, respectively, compared to", 1.08) == 1.20
    assert p("Non-GAAP operating margin of 45.0% to 46.0% • Non-GAAP diluted earnings per share of $5.90 to $6.20 We", 4.22) is None
    assert p("the non-GAAP EPS included $4.57 of charges", 6.35) is None
    assert p("EPS $3.66, down 11%; adjusted EPS $4.59, up 9% YoY", 4.17) == 4.59
    assert p("Earnings per share (non-GAAP) $ 7.48", 6.35) == 7.48
    assert p("• Non-GAAP EPS increased 14% from $5.64 to $6.45 for the third quarter", 5.78) == 6.45   # not last year's $5.64
    assert p("raised adjusted EPS guidance from $5.60 to $5.80", 5.7) is None
    assert p("Adjusted earnings for the third quarter were a record $2.21 per diluted share", 2.11) == 2.21
    assert p("Diluted EPS of $0.56 for the quarter", 0.74) is None                 # GAAP only: stays empty
    assert p("expects adjusted EPS of $4.10 next year", 4.0) is None               # guidance
    assert p("Adjusted EPS of $9.99", 4.0) is None                                 # too far from the estimate
    assert p("Adjusted EPS of ($0.05)", -0.01) == -0.05                            # a loss
    assert p("Adjusted EPS of $1.00", None) is None and p("", 1.0) is None         # nothing to check it against


def test_data_health_rules(api):
    """v32: the read-only data check — a weekend is not a false alarm, and numbers that do not add up are found."""
    import datetime as _dt
    import data_health as dh
    d = _dt.date
    assert dh.weekdays_since(d(2026, 10, 30), d(2026, 11, 2)) == 1      # Friday -> Monday
    assert dh.weekdays_since(d(2026, 10, 31), d(2026, 11, 2)) == 1      # Saturday -> Monday
    assert dh.weekdays_since(d(2026, 10, 30), d(2026, 11, 3)) == 2      # Monday was a holiday
    assert dh.weekdays_since(d(2026, 11, 3), d(2026, 11, 3)) == 0
    good = dict(n=502, n50=501, above50=328, n200=499, above200=306, n_hl=497, new_highs=10, new_lows=1,
                advancers=189, decliners=313, unchanged=0, total=502)
    assert dh.breadth_problems(good) == []
    assert dh.breadth_problems(dict(good, unchanged=None)) == []        # a row from before v31
    assert dh.breadth_problems(dict(good, advancers=500))               # more than were counted
    assert dh.breadth_problems(dict(good, advancers=170))               # 19 stocks went missing
    assert dh.breadth_problems(dict(good, above50=600))
    assert dh.breadth_problems(dict(good, total=490))
    line = lambda s: {"status": s}
    assert dh.overall_status([line("ok"), line("info")]) == "ok"
    assert dh.overall_status([line("ok"), line("warn")]) == "warn"
    assert dh.overall_status([line("warn"), line("fail")]) == "fail"
    assert dh.MOVERS_MAX_AGE_DAYS == api.MOVERS_MAX_AGE_DAYS            # the check counts what the site really shows


def test_share_count_rules():
    """v34: the numbers behind the market cap. Real cases of 4.11.2026: the SEC's facts for companies with several share
    classes end years ago or cover one class only, and they were multiplied by today's price as if current."""
    import fundamentals as f
    q = "2026-09-30"
    assert f.pick_shares({"2010-01-27": 469_280_842}, {}, q) is None                                   # Visa: 2010, no diluted tag
    assert f.pick_shares({"2010-10-27": 122_530_193}, {q: 884_000_000}, q) == 884_000_000              # Mastercard
    assert f.pick_shares({"2019-03-18": 1}, {q: 428_000_000}, q) == 428_000_000                        # Fox: "1" share
    assert f.pick_shares({q: 880_000_000}, {q: 1_480_000_000}, q) == 1_480_000_000                     # one class only (Nike)
    assert f.pick_shares({q: 4_300_000_000}, {q: 4_310_000_000}, q) == 4_300_000_000                   # KO: the count stays
    assert f.pick_shares({q: 4_300_000_000}, {}, q) == 4_300_000_000
    assert f.pick_shares({}, {}, q) is None
    assert f.pick_shares({q: 1_000}, {"2015-03-31": 9_000_000}, q) == 1_000                            # a stale diluted figure is ignored


def test_market_cap_reference():
    """v34: far from the scanner's stored figure -> the stored figure, and the P/E computed from it."""
    import fundamentals as f
    brk = {"market_cap": 540_000_000.0, "net_income_ltm": 80e9, "trailing_pe": 0.0, "latest_shares_outstanding": 941_481}
    f.reconcile_market_cap(brk, 1.2e12)
    assert brk["market_cap"] == 1.2e12 and brk["trailing_pe"] == 15.0 and brk["latest_shares_outstanding"] is None
    v = {"market_cap": None, "net_income_ltm": 20e9, "trailing_pe": None}                              # SEC has no count at all
    f.reconcile_market_cap(v, 8e11)
    assert v["market_cap"] == 8e11 and v["trailing_pe"] == 40.0
    ko = {"market_cap": 4.1e11, "net_income_ltm": 1.5e10, "trailing_pe": 27.3}                         # close enough: SEC's stays
    f.reconcile_market_cap(ko, 4.0e11)
    assert ko["market_cap"] == 4.1e11 and ko["trailing_pe"] == 27.3 and "market_cap_source" not in ko
    loss = {"market_cap": 1.0, "net_income_ltm": -5e8, "trailing_pe": None}
    f.reconcile_market_cap(loss, 3e10)
    assert loss["market_cap"] == 3e10 and loss["trailing_pe"] is None
    same = {"market_cap": 1e9, "net_income_ltm": 1e8, "trailing_pe": 10.0}                             # nothing to compare with
    f.reconcile_market_cap(same, None)
    assert same["market_cap"] == 1e9
    band = f.mark_pe({"net_income_ltm": 1e9, "trailing_pe": 15.0, "pe_band": {"current": 0.03}})       # BRK-B's price / BRK-A's EPS
    assert band["pe_band"] is None and band["trailing_pe"] == 15.0


def test_refresh_mark(api):
    """v34: answers kept "until the next refresh" run out at hh:07 and hh:37, when the database is awake anyway."""
    m = lambda s: api._next_refresh_mark(s)
    assert m(0) == 7 * 60                          # 00:00 -> 00:07
    assert m(7 * 60 - 1) == 7 * 60
    assert m(7 * 60) == 37 * 60                    # exactly on the mark -> the next one
    assert m(36 * 60) == 37 * 60
    assert m(37 * 60 + 1) == 67 * 60


def test_cached_answer(api):
    """v34: answered from memory, per set of parameters; a failure serves the last good answer; a first failure is an error."""
    calls = {"n": 0, "fail": False}

    @api._cached_answer(ttl_sec=60, max_keys=2)
    def fn(a=1):
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("database asleep")
        return {"a": a, "n": calls["n"]}

    assert fn(a=1)["n"] == 1 and fn(a=1)["n"] == 1      # the second call came from memory
    assert fn(a=2)["n"] == 2                            # other parameters: computed

    @api._cached_answer(ttl_sec=-1, max_keys=2)         # expired at once
    def gone(a=1):
        calls["n"] += 1
        if calls["fail"]:
            raise RuntimeError("database asleep")
        return calls["n"]

    calls["fail"] = False
    good = gone(a=1)
    calls["fail"] = True
    assert gone(a=1) == good                            # the last good answer
    with pytest.raises(RuntimeError):
        gone(a=2)                                       # nothing to fall back on


def test_cached_endpoints_keep_their_parameters(api):
    """v34: the decorator must not hide an endpoint's query parameters from FastAPI."""
    params = {r.path: {p.name for p in r.dependant.query_params} for r in api.app.routes if hasattr(r, "dependant")}
    assert params["/api/stocks"] == {"limit"}
    assert params["/api/macro-news/search"] == {"q"}            # v50: days and limit are fixed (7, 60)
    assert params["/api/daily-digest"] == {"day"}               # v50: top_n is fixed
    assert params["/api/heatmap"] == {"index"}
    assert params["/api/demo-portfolio"] == {"min_score", "top_per_day", "amount", "capital"}
    assert params["/api/insider-buys"] == {"days", "min_value"} and params["/api/backtest"] == {"pattern", "horizon"}
    paths = {r.path: {p.name for p in r.dependant.path_params} for r in api.app.routes if hasattr(r, "dependant")}
    assert paths["/api/lookup/{ticker}"] == {"ticker"}
    assert paths["/api/sector-comparison/{ticker}"] == {"ticker"}


def test_alert_thread_leaves_the_database_asleep(api, monkeypatch):
    """v34: the check that runs every 5 minutes, day and night, must not touch the database while no release is near —
    it used to rebuild the calendar each time, so Neon never reached its 5 idle minutes and never slept."""
    import time
    import econ_calendar

    def forbidden(*a, **k):
        raise AssertionError("the alert thread woke the database")
    monkeypatch.setattr(api, "get_conn", forbidden)
    monkeypatch.setattr(api.push_alerts, "ready", lambda: True)
    monkeypatch.setattr(econ_calendar, "build_calendar", forbidden)
    copy = {"events": [{"impact": "high", "datetime_utc": "2099-01-01T12:00:00Z", "estimated": False, "actual": None}]}
    monkeypatch.setitem(econ_calendar._cache, ("result", 21, 14), (time.time() - 3600, copy))     # an hour old: still used
    api._macro_alert_check()


def test_put_is_allowed_by_cors(api):
    """v34: without PUT in the list the browser refuses PUT /api/trades/{id}: no trade could be edited or closed."""
    cors = next(m for m in api.app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    args = getattr(cors, "kwargs", None) or getattr(cors, "options", {})
    assert {"GET", "POST", "PUT", "DELETE"} <= set(args["allow_methods"])


def test_employee_count():
    """v35: sentences from real 10-Ks (4.11.2026). The first figure found used to be taken: KO showed 400 employees."""
    import company_profiles as cp
    n = cp.employees_from
    assert n("our Company had approximately 65,900 and 69,700 employees, respectively, of which approximately 32,000 were "
             "located in the United States. As of December 31, 2025, approximately 400 employees in North America were "
             "covered by collective bargaining agreements.") == 65900
    assert n("As of June 30, 2026, we employed approximately 223,000 people on a full-time basis, 121,000 in the U.S.") == 223000
    assert n("the Company had approximately 167,000 full-time equivalent employees.") == 167000
    assert n("JPMorganChase had 318,512 employees globally.") == 318512
    assert n("We grew our global workforce from approximately 31,600 in fiscal 2024 to approximately 34,100 employees in fiscal "
             "2025. As of September 30, 2025, it has been embraced by nearly 26,000 employees launching over 261,000 agents") == 34100
    assert n("we had more than 390,000 employees, of whom nearly 165,000 were") == 390000
    assert n("we employ approximately 2.1 million associates worldwide") == 2100000
    assert n("we employed approximately 440,000 full-time, part-time, and seasonal team members") == 440000
    assert n("Lowe's employed approximately 167,000 full-time associates and 109,000 part-time associates") == 167000
    assert n("A contract covering approximately 9,000 employees across 36 states is set to expire") is None      # AT&T
    assert n("we have reached 5,000,000 people every month") is None           # not the staff
    assert n("about 400 employees in Canada are represented by a union") is None
    assert n("The company has 2025 employees programs") is None                # a year
    assert n("no number here") is None
    # v36: AES's report (6.11.2026). The slash hid the real figure, and a smaller one elsewhere was shown: 100
    assert n("the Company and its subsidiaries had 8,336 full time/permanent employees. We have 100 employees in Ohio") == 8336


def test_business_heading_in_small_caps():
    """v35: Microsoft's heading is "B<span>USINESS</span>": read as "B USINESS", no section, and the fallback described a
    court case. Inline tags do not separate letters; a table cell or a paragraph still separates words."""
    import company_profiles as cp
    body = "<p>" + "Microsoft makes software. " * 200 + "</p>"
    raw = f'<div><span>ITEM 1. B<span style="font-size:8pt">USINESS</span></span></div>{body}<div>ITEM 1A. RISK FACTORS</div>'
    assert cp.business_section(cp.html_to_text(raw), "10-K").startswith("Microsoft makes software.")
    assert cp.html_to_text("<table><tr><td>Total</td><td>12</td></tr></table>").strip() == "Total 12"
    assert "<" not in cp.html_to_text('<ix:nonFraction name="x">5</ix:nonFraction> shares')


def test_wikipedia_article_is_about_the_company():
    import company_profiles as cp
    ok = cp.is_company_article
    assert ok("Microsoft", "Microsoft Corporation is an American multinational technology conglomerate in Redmond.", "MICROSOFT")
    assert not ok("Microsoft Corp. v Commission of the European Communities",
                  "Microsoft Corp. v Commission of the European Communities (2007) is a case brought by the company.", "MICROSOFT")
    assert not ok("Microsoft Windows", "Microsoft Windows is a product line of graphical operating systems by Microsoft.", "MICROSOFT")
    assert not ok("Carnival", "Carnival is a festive season that occurs before Lent in many countries.", "CARNIVAL")
    assert not ok("Mercury", "Mercury may refer to: a planet, a metal, a company", "MERCURY")
    assert ok("Shopify", "Shopify Inc., stylized as shopify, is a Canadian multinational e-commerce company in Ottawa.", "SHOPIFY")
    assert ok("Scotiabank", "The Bank of Nova Scotia, operating as Scotiabank, is a Canadian multinational banking company.", "BANK")
    assert ok("Acme", "Acme Ltd. is a U.S. holding company based in Ohio.", "ACME")
    assert not ok("Other Thing", "Other Thing is a company that has a different name altogether.", "ACME")


def test_profile_rules():
    """v35: data_health: what a wrong company profile looks like."""
    import datetime as _dt
    import data_health as dh
    p = dict(name="COCA COLA CO", source="sec-10k", filing_date=_dt.date(2026, 2, 20), summary="Coca-Cola makes drinks.",
             employees=65900.0, market_cap=3e11)
    assert dh.profile_problems(p) == []
    assert dh.profile_problems(dict(p, employees=400.0))                                       # KO, 4.11.2026
    assert dh.profile_problems(dict(p, employees=400.0, market_cap=2e9)) == []                 # a small company can be small
    assert dh.profile_problems(dict(p, employees=39.0, ticker="RGLD")) == []                   # checked in its report
    assert dh.profile_problems(dict(p, employees=None)) == []
    assert dh.profile_problems(dict(p, source="wikipedia")) == []
    assert dh.profile_problems(dict(p, source="wikipedia", summary="A court case in Europe."))


def _seo_inputs():
    import datetime as _dt
    ts = lambda m, d: _dt.datetime(2026, m, d, 21, 0, tzinfo=_dt.timezone.utc)
    leader = dict(sector="Technology", close=100.126, pct_from_high=-3.21, chg_1m=1.5, chg_3m=float("nan"),
                  stage="Stage 2 - Uptrend", rs_rating=72, market_cap=5e11, updated_at=ts(11, 3))
    leaders = [dict(leader, ticker="AAA", name="Aaa Corporation", market_cap=9e11),
               dict(leader, ticker="BBB", name="Bbb Inc.", market_cap=8e11),
               dict(leader, ticker="CCC", name=None, market_cap=7e11),
               dict(leader, ticker="OLD", name="Old Co", updated_at=ts(10, 1))]
    prof = lambda he=True: {"v": 2, "en": {"summary": "It makes things."}, "he": {"summary": "החברה מייצרת."} if he else None}
    row = dict(source="sec-10k", source_url="https://www.sec.gov/x.htm", filing_date=_dt.date(2026, 2, 1))
    profiles = [dict(row, ticker="AAA", name="AAA CORP", profile=prof(),
                     facts={"hq": "Ohio", "employees": 5000, "fv": 2}),
                dict(row, ticker="BBB", name="BBB INC", profile=prof(False), facts={"employees": 400}),    # before v35
                dict(row, ticker="WIK", name="WIK LTD", source="wikipedia", profile=prof(), facts={}),     # not checked yet
                dict(row, ticker="NEW", name="NEW THING CORP", profile='{"en": {"summary": "From JSON."}}', facts=None)]
    scans = [dict(ticker="AAA", pattern_type="cup_handle", timestamp=ts(11, 2)), dict(ticker="ZZZ", pattern_type="x", timestamp=ts(11, 2))]
    earn = [dict(ticker="AAA", report_date=_dt.date(2026, 11, 20))]
    return leaders, profiles, scans, earn, _dt.date(2026, 11, 4)


def test_seo_rows(api):
    """v35: the facts in a stock's page. An old price is not shown as current, a NaN does not break the JSON, a profile
    from before the fixes is not given to search engines."""
    import json
    rows = api._seo_rows(*_seo_inputs())
    a = rows["AAA"]
    assert a["known"] and a["name"] == "Aaa Corporation" and a["sector"] == "Information Technology"
    assert a["numbers"] == {"as_of": "2026-11-03", "close": 100.13, "from_high": -3.2, "chg_1m": 1.5, "chg_3m": None, "rs": 72, "stage": 2}
    assert a["facts"] == {"hq": "Ohio", "employees": 5000} and a["source"]["filed"] == "2026-02-01"
    assert a["scan"] == {"pattern": "cup_handle", "day": "2026-11-02"} and a["next_report"] == "2026-11-20"
    assert a["peers"] == [{"ticker": "BBB", "name": "Bbb Inc.", "langs": ["en"]}]
    assert "numbers" not in rows["OLD"] and "profile" not in rows["OLD"]
    assert "employees" not in rows["BBB"]["facts"] and sorted(rows["BBB"]["profile"]) == ["en"]
    assert "WIK" not in rows and "ZZZ" not in rows
    assert rows["CCC"]["name"] == "CCC"
    assert rows["NEW"]["name"] == "New Thing Corp" and rows["NEW"]["profile"]["en"]["summary"] == "From JSON."
    json.dumps(rows, allow_nan=False)


def test_seo_endpoints(api, monkeypatch):
    rows = api._seo_rows(*_seo_inputs())
    monkeypatch.setattr(api, "_seo_table", lambda: rows)
    assert api.get_seo_stock("aaa")["ticker"] == "AAA"
    assert api.get_seo_stock("NOPE") == {"known": False, "ticker": "NOPE"}
    assert api._TICKER_PATH_RE.match("/api/seo/stock/KO")
    listed = api.get_seo_index()["stocks"]
    assert [s["ticker"] for s in listed] == ["AAA", "BBB", "NEW"]         # only the ones with a profile
    assert listed[0] == {"ticker": "AAA", "name": "Aaa Corporation", "sector": "Information Technology",
                         "langs": ["en", "he"], "day": "2026-11-03"}
    assert listed[2]["day"] == "2026-02-01"


def test_backup_is_encrypted_and_reads_back():
    pytest.importorskip("cryptography")
    import backup_personal as b
    data = {"made_at": "2026-11-05", "tables": {"users": {"columns": ["id", "email"], "rows": [[1, "someone@example.com"]]}}}
    blob = b.encrypt(data, "a-long-enough-password")
    assert b"example.com" not in blob and blob.startswith(b.MAGIC)      # the repository is public
    assert b.decrypt(blob, "a-long-enough-password") == data
    with pytest.raises(Exception):
        b.decrypt(blob, "the-wrong-password-here")
    with pytest.raises(ValueError):
        b.encrypt(data, "short")
    with pytest.raises(ValueError):
        b.encrypt(data, "")                                             # the secret is not set: red, not an unencrypted file


def test_backup_reads_and_restores_only_missing_rows():
    pytest.importorskip("cryptography")
    import datetime as _dt
    import backup_personal as b

    class Cur:
        def __init__(self):
            self.sql, self.description, self.rowcount, self._last = [], [("id",), ("opened_on",)], 1, []

        def execute(self, sql, params=None):
            self.sql.append((sql, params))
            if "to_regclass" in sql:
                self._last = [(params[0] != "watchlist",)]              # a table that does not exist is skipped
            elif "information_schema" in sql:
                self._last = [("id",), ("email",)]
            else:
                self._last = [(1, _dt.date(2026, 11, 5))]

        def fetchone(self):
            return self._last[0]

        def fetchall(self):
            return self._last

    data = b.read_tables(Cur())
    assert list(data["tables"]) == ["users", "trades", "price_alerts"] and data["tables"]["users"]["rows"] == [[1, "2026-11-05"]]
    assert b.counts(data) == {"users": 1, "trades": 1, "price_alerts": 1}
    cur = Cur()
    old = {"tables": {"trades": {"columns": ["id", "dropped_later"], "rows": [[7, "x"]]},
                      "users": {"columns": ["id", "email"], "rows": [[1, "a@b.c"]]}}}
    assert b.restore_tables(cur, old) == {"users": 1, "trades": 1}
    inserts = [s for s in cur.sql if s[0].startswith("INSERT")]
    assert "INTO users" in inserts[0][0] and "ON CONFLICT DO NOTHING" in inserts[0][0]     # users first
    assert "dropped_later" not in inserts[1][0] and inserts[1][1] == [7]                   # a column that no longer exists
    assert not any(s[0].lstrip().upper().startswith(("DELETE", "UPDATE", "DROP", "TRUNCATE")) for s in cur.sql)


def test_versions_are_fixed_and_workflows_agree():
    """v35c: numpy 2.5.4 needs Python 3.12: one workflow on 3.11 would fail at its install step, every run."""
    req = ROOT / "requirements.txt"
    flows = ROOT / ".github" / "workflows" if (ROOT / ".github" / "workflows").exists() else ROOT / "WORKFLOW"
    if not req.exists() or not flows.exists():
        pytest.skip("not in this copy")
    lines = [ln.strip() for ln in req.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    assert lines and all("==" in ln for ln in lines), "every line of requirements.txt has an exact version"
    import re
    for f in flows.glob("*.yml"):
        assert not re.search(r"python-version:\s*[\"']?3\.11", f.read_text(encoding="utf-8")), f.name


def test_backup_is_scheduled_at_a_time_the_database_is_awake():
    import scheduler as s
    job = next(j for j in s.JOBS if j["name"] == "backup")
    assert job["workflow"] == "backup-personal.yml" and job["kind"] == "daily" and job["at"][1] < 35


def test_market_holidays_and_trading_days(api):
    """v35e: published NYSE closures."""
    import datetime as _dt
    d = _dt.date
    assert api._nyse_holidays(2026) == {d(2026, 1, 1), d(2026, 1, 19), d(2026, 2, 16), d(2026, 4, 3), d(2026, 5, 25), d(2026, 6, 19),
                                        d(2026, 7, 3), d(2026, 9, 7), d(2026, 11, 26), d(2026, 12, 25)}
    h27 = api._nyse_holidays(2027)
    assert d(2027, 3, 26) in h27 and d(2027, 7, 5) in h27 and d(2027, 12, 24) in h27 and d(2027, 6, 18) in h27
    assert d(2027, 12, 31) not in h27                       # New Year's Day 2028 is a Saturday: not moved back
    assert api._nyse_holidays(2025) >= {d(2025, 4, 18), d(2025, 11, 27)}
    n = api._trading_days_until
    today = d(2026, 11, 5)                                  # a Thursday
    assert n(today, today) == 0 and n(today, d(2026, 11, 6)) == 1
    assert n(today, d(2026, 11, 9)) == 2                    # over the weekend
    assert n(d(2026, 11, 25), d(2026, 11, 27)) == 1         # over Thanksgiving
    import ai_digest                                         # v38c: the twin in the daily summary
    assert all(ai_digest.nyse_holidays(y) == api._nyse_holidays(y) for y in range(2024, 2036))


def test_next_report(api, monkeypatch):
    import datetime as _dt
    far = _dt.date.today() + _dt.timedelta(days=60)
    db = _FakeDb([None, {"next_report": far, "next_report_est": True}])
    monkeypatch.setattr(api, "db_cursor", db)
    r = api._next_report("KO")
    assert r["date"] == far.isoformat() and r["estimate"] is True and r["session"] is None and r["trading_days"] > 30
    db = _FakeDb([{"report_date": far, "session": "amc"}])
    monkeypatch.setattr(api, "db_cursor", db)
    r = api._next_report("KO")
    assert r["session"] == "amc" and r["estimate"] is False and len(db.sql) == 1      # the calendar comes first
    monkeypatch.setattr(api, "db_cursor", _FakeDb([None, None]))
    assert api._next_report("KO") is None
    past = _dt.date.today() - _dt.timedelta(days=10)                                  # v36: reports, next date not out yet
    monkeypatch.setattr(api, "db_cursor", _FakeDb([None, {"next_report": past, "next_report_est": False}]))
    assert api._next_report("KO") == {"date": None}
    monkeypatch.setattr(api, "db_cursor", lambda dict_cursor=False: (_ for _ in ()).throw(RuntimeError("down")))
    assert api._next_report("KO") is None                                             # the quote still answers


def test_estimates_row_carries_the_next_report(scanner):
    info = {"forwardEps": 3.0, "earningsTimestampStart": 1800000000, "isEarningsDateEstimate": True}
    row = scanner._estimates_row("KO", info)
    assert len(row) == 20 and row[18] == "2027-01-15" and row[19] is True
    assert scanner._estimates_row("KO", {"forwardEps": 3.0})[18] is None


class _FakeDb:
    """db_cursor for one test: answers from a list, and records what was asked."""
    def __init__(self, answers):
        self.answers, self.sql = list(answers), []

    def __call__(self, dict_cursor=False):
        import contextlib

        @contextlib.contextmanager
        def ctx():
            yield (None, self)
        return ctx()

    def execute(self, sql, params=None):
        self.sql.append((sql, params))

    def fetchall(self):
        return self.answers.pop(0)

    def fetchone(self):
        return self.answers.pop(0)


def test_key_events_read_only_new_rows(api, monkeypatch):
    """v35: data leaving Neon is limited: the second refresh must ask only for rows after the last one, and full rows only
    for the headlines shown."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    row = lambda i, title, src: dict(id=i, summary_en=title, source_name=src, timestamp=now - _dt.timedelta(hours=1))
    first = [row(1, "Fed cuts interest rates by a quarter point", "Reuters"), row(2, "Fed cuts rates again", "CNBC"),
             row(3, "Fed rate cut lifts stocks", "MarketWatch"), row(4, "A company opened a shop", "Reuters")]
    monkeypatch.setattr(api, "_key_cache", {"until": 0.0, "events": None, "rows": {}, "last_id": 0})
    event = api._key_event_of(first[0]["summary_en"])
    assert event, "the rules changed: pick another headline"
    db = _FakeDb([first, [dict(first[0], summary_he="הפד מוריד את הריבית ברבע נקודת אחוז")]])     # v77c: a real length (cut_short)
    monkeypatch.setattr(api, "db_cursor", db)
    events = api._recent_key_events()
    assert api._key_cache["last_id"] == 4 and 4 not in api._key_cache["rows"]        # kept in memory: only key-event headlines
    assert db.sql[0][1][0] == 0 and "SELECT *" not in db.sql[0][0]
    if events:
        assert "ANY" in db.sql[1][0] and events[0]["row"].get("summary_he") == "הפד מוריד את הריבית ברבע נקודת אחוז"
    api._key_cache["until"] = 0.0
    db2 = _FakeDb([[], []])
    monkeypatch.setattr(api, "db_cursor", db2)
    api._recent_key_events()
    assert db2.sql[0][1][0] == 4
    old = dict(first[0], id=9, timestamp=now - _dt.timedelta(hours=api._KEY_WINDOW_H + 1))
    api._key_merge(api._key_cache, [old], now)
    assert 9 not in api._key_cache["rows"] and api._key_cache["last_id"] == 9
    api._key_cache["until"] = 0.0
    monkeypatch.setattr(api, "db_cursor", lambda dict_cursor=False: (_ for _ in ()).throw(RuntimeError("database suspended")))
    assert api._recent_key_events() == api._key_cache["events"]                      # the last answer, not an error


def test_daily_digest_reads_full_rows_only_for_what_it_shows(api, monkeypatch):
    """v40f: the day's headlines in five columns, SELECT * only for the key events and topic headlines shown."""
    import datetime as _dt
    now = _dt.datetime(2026, 11, 9, 15, tzinfo=_dt.timezone.utc)
    row = lambda i, title, src, tag: dict(id=i, summary_en=title, source_name=src, category_tag=tag,
                                          timestamp=now - _dt.timedelta(minutes=i))
    light = [row(1, "Fed cuts interest rates by a quarter point", "Reuters", "Fed"),
             row(2, "Fed cuts rates again", "CNBC", "Fed"), row(3, "Fed rate cut lifts stocks", "MarketWatch", "Fed"),
             row(4, "Oil slips as supply rises", "Reuters", "Energy"), row(5, "Oil prices edge lower", "CNBC", "Energy")]

    class Conn:
        def __init__(self):
            self.db = _FakeDb([light, None, None])       # light rows, then the full rows (filled below), AI digest, ratings
            self.db.close = lambda: None
        def cursor(self, cursor_factory=None):
            return self.db
        def rollback(self):
            pass
        def close(self):
            pass
    conn = Conn()
    real_fetchall = conn.db.fetchall

    def fetchall():
        sql, params = conn.db.sql[-1]
        if "ANY" in sql:
            conn.db.answers.pop(0)
            return [dict(r, summary_he="הפד מוריד את הריבית ברבע נקודת אחוז", body_en="body") for r in light if r["id"] in params[0]]
        return real_fetchall()
    conn.db.fetchall = fetchall
    conn.db.fetchone = lambda: (False,) if "to_regclass" in conn.db.sql[-1][0] else None
    monkeypatch.setattr(api, "get_conn", lambda: conn)
    out = api.get_daily_digest.__wrapped__(day="2026-11-09", top_n=6)
    first = conn.db.sql[0][0]
    assert "SELECT *" not in first and "summary_en" in first
    full_reads = [p for s, p in conn.db.sql if "SELECT * FROM macro_news" in s]
    assert len(full_reads) == 1 and set(full_reads[0][0]) <= {1, 2, 3, 4, 5}
    assert out["total_items"] == 5 and [h["tag"] for h in out["hot_topics"]] == ["Fed", "Energy"]
    for h in out["hot_topics"]:
        assert h["representative"]["summary_he"] == "הפד מוריד את הריבית ברבע נקודת אחוז" and h["representative"]["impact_level"] in ("Critical", "High")
    for c in out["critical"]:
        assert c["impact_level"] == "Critical" and c["body_en"] == "body"


def test_insider_asks_only_for_what_is_new(monkeypatch):
    """v40f: the database answers with the accessions not seen yet, and the sweep takes only companies that are due."""
    import contextlib
    import time
    import insider_trades as it
    db = _FakeDb([[("0001-26-000002",)], [("0000000002",)]])

    @contextlib.contextmanager
    def cursor(dict_cursor=False):
        yield (None, db)
    monkeypatch.setattr(it, "cursor", cursor)
    assert it._unseen(["0001-26-000001", "0001-26-000002"]) == {"0001-26-000002"}
    assert "NOT EXISTS" in db.sql[0][0]
    asked = []
    monkeypatch.setattr(it, "sec_get", lambda url, timeout=20: asked.append(url))
    stats = {"sweep_filings": 0, "trades": 0, "companies_swept": 0}
    it.run_sweep({"0000000001": "AAA", "0000000002": "BBB"}, time.time() + 60, stats)
    assert db.sql[1][1] == (["0000000001", "0000000002"], it.SWEEP_EVERY_DAYS, it.SWEEP_MAX_PER_RUN)
    assert len(asked) == 1 and "CIK0000000002" in asked[0]


def test_stocks_read_again_only_after_a_scan(api, monkeypatch):
    import datetime as _dt
    t1, t2 = _dt.datetime(2026, 11, 4, 20), _dt.datetime(2026, 11, 5, 14)
    stock = dict(ticker="KO", sector="Energy", resistance_targets="[]", timestamp=t1)
    get = api.get_stocks.__wrapped__
    monkeypatch.setattr(api, "_stocks_last", {})
    db = _FakeDb([{"t": t1}, [stock], {"t": t1}, {"t": t2}, [stock]])
    monkeypatch.setattr(api, "db_cursor", db)
    assert get(limit=150)[0]["ticker"] == "KO" and len(db.sql) == 2
    assert get(limit=150)[0]["ticker"] == "KO" and len(db.sql) == 3      # same scan: only the small question
    get(limit=150)
    assert len(db.sql) == 5                                              # a new scan: read


def test_heatmap_read_again_only_after_new_prices(api, monkeypatch):
    """v36: the same idea as /api/stocks."""
    import datetime as _dt
    t1, t2 = _dt.datetime(2026, 11, 6, 20), _dt.datetime(2026, 11, 9, 11)
    row = dict(ticker="KO", change_pct=1.0, close_price=95.0, market_cap=4e11, timestamp=t1)
    get = api.get_heatmap.__wrapped__
    monkeypatch.setattr(api, "_heatmap_last", {})
    monkeypatch.setattr(api, "get_sp500_info_map", lambda: {})
    db = _FakeDb([{"t": t1}, [row], {"t": t1}, {"t": t2}, [row]])
    monkeypatch.setattr(api, "db_cursor", db)
    assert get(index="sp500")["count"] == 1 and len(db.sql) == 2
    assert get(index="sp500")["count"] == 1 and len(db.sql) == 3         # nothing new: only the small question
    get(index="sp500")
    assert len(db.sql) == 5


def test_earnings_calendar_read_again_only_when_it_changed(api, monkeypatch):
    """v36: the database compares; the rows (~50 KB) come back only after a change."""
    import datetime as _dt
    row = dict(ticker="KO", report_date=_dt.date(2026, 11, 6), session="bmo", market_cap=4e11, eps_estimate=1.0, eps_actual=None,
               surprise_pct=None, revenue_estimate=None, revenue_actual=None, revenue_surprise_pct=None, implied_move_pct=None,
               implied_move_expiry=None, company_name="Coca-Cola", eps_from_release=False)
    get = api.get_earnings_calendar.__wrapped__
    monkeypatch.setattr(api, "_earnings_last", {"fingerprint": None, "answer": None})
    monkeypatch.setattr(api, "get_sp500_info_map", lambda: {})
    db = _FakeDb([{"fp": "a"}, [row], {"fp": "a"}, {"fp": "b"}, [dict(row, eps_actual=1.1)]])
    monkeypatch.setattr(api, "db_cursor", db)
    assert get()["items"][0]["reported"] is False and len(db.sql) == 2
    assert get()["count"] == 1 and len(db.sql) == 3                      # same summary: no rows read
    assert get()["items"][0]["reported"] is True and len(db.sql) == 5    # a result arrived


def test_macro_news_reads_only_rows_not_in_memory(api, monkeypatch):
    """v36: a refresh asks for the ids, and for full rows only of those it does not hold yet. The order stays the database's."""
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    # v47: three different stories (near-identical headlines are now folded into one line)
    titles = {1: "Fed officials signal patience on interest rates", 2: "Copper miners report record quarterly output",
              3: "Treasury auction draws weak demand from foreign buyers"}
    full = lambda i: dict(id=i, summary_en=titles[i], summary_he="כותרת", source_name="Reuters",
                          impact_level="High", category_tag="Macro", timestamp=now - _dt.timedelta(minutes=i))
    get = api.get_macro_news.__wrapped__
    monkeypatch.setattr(api, "_news_rows", {"at": 0.0, "rows": {}})
    monkeypatch.setattr(api, "_recent_key_events", lambda: [])
    db = _FakeDb([[], [{"id": 1}, {"id": 2}], [full(1), full(2)],                       # first refresh: both rows
                  [], [{"id": 3}, {"id": 1}, {"id": 2}], [full(3)]])                    # second: only the new one
    monkeypatch.setattr(api, "db_cursor", db)
    assert [r["id"] for r in get(limit=25)] == [1, 2] and all("SELECT id" in s for s, _ in db.sql[:2])
    assert db.sql[2][1] == ([1, 2],)
    second = get(limit=25)
    assert db.sql[-1][1] == ([3],) and {r["id"] for r in second} == {1, 2, 3}
    assert second[0] is not api._news_rows["rows"].get(second[0]["id"])                 # the answer's rows are copies


def test_news_uses_one_connection_and_reopens_a_dropped_one(monkeypatch):
    """v36: every saved headline used to open a connection (~5 KB of Neon's monthly quota each)."""
    pb = importlib.import_module("pipeline_b_news_aggregator")
    import psycopg2

    class Conn:
        closed = 0

        def close(self):
            self.closed = 1
    opened = []

    def connect():
        opened.append(Conn())
        return opened[-1]
    monkeypatch.setattr(pb, "get_conn", connect)
    monkeypatch.setattr(pb, "_run_conn", {"conn": None})
    assert [pb._with_run_conn(lambda c: "saved") for _ in range(3)] == ["saved"] * 3 and len(opened) == 1
    calls = []

    def drops_once(conn):
        calls.append(conn)
        if len(calls) == 1:
            raise psycopg2.OperationalError("SSL connection has been closed unexpectedly")
        return "saved"
    assert pb._with_run_conn(drops_once) == "saved" and len(opened) == 2 and opened[0].closed
    pb._close_run_conn()
    assert opened[1].closed and pb._run_conn["conn"] is None


_CRASH_MARKET = {"session_day": "2026-11-05", "same_day": True, "intraday": False, "move": "extreme",
                 "indices": [{"symbol": "^GSPC", "name_en": "S&P 500", "close": 7353.73, "change_pct": -4.36, "change_points": -334.89},
                             {"symbol": "^IXIC", "name_en": "Nasdaq", "close": 24315.0, "change_pct": -5.52, "change_points": -1421.51},
                             {"symbol": "^TNX", "name_en": "10-year Treasury yield", "close": 4.5, "change_pct": -1.0, "change_points": -0.05}],
                 "breadth": {"advancers": 73, "decliners": 430}, "movers": {"down": [{"ticker": "NVDA", "change_pct": -9.1}], "up": []}}


def test_digest_ranks_the_market_above_small_company_earnings_and_far_away_news():
    """v37: 5.11.2026. 80 of 1,564 headlines at even spacing, two thirds of them earnings, and Philippine inflation in the overview."""
    import ai_digest as d
    item = lambda i, title, src="Yahoo Finance", tag="שוק": dict(id=i, title=title, source=src, tag=tag)
    items = [item(1, "Philippine Inflation Surges to 7.8%, Boosting Case for Rate Hike", "Bloomberg.com", "אינפלציה"),
             item(2, "Magal: Q3 Earnings Snapshot", "KTVB", "דוחות"),
             item(3, "What Wendy's (WEN) Said on Its Q3 Earnings Call", tag="דוחות"),
             item(4, "Docebo Inc. (DCBO) Q3 Earnings and Revenues Surpass Estimates", tag="דוחות"),
             item(5, "A brutal sell-off for AI stocks sends Wall Street reeling, and the S&P 500 tumbles 4.4%", "AP News"),
             item(6, "A brutal sell-off for AI stocks sends Wall Street reeling; S&P 500 sees worst day since 2025", "WAFB"),
             item(7, "Brutal sell-off for AI stocks sends Wall Street reeling as S&P 500 tumbles", "CNBC"),
             item(8, "Fed's Hammack favors gradual rate-hike path to lower inflation", "Reuters", "ריבית")]
    ranked = d.rank_items(items)
    titles = [r["title"] for r in ranked]
    assert "sell-off" in titles[0] and ranked[0]["outlets"] == 3
    assert not any("Philippine" in t or "Snapshot" in t or "Said on Its" in t for t in titles)
    assert titles.index(next(t for t in titles if "Docebo" in t)) > titles.index(next(t for t in titles if "Hammack" in t))
    many = [item(100 + i, f"Northwind{i} Holdings{i} Q3 earnings beat estimates on higher quarterly revenue", tag="דוחות") for i in range(40)]
    assert sum(r["tag"] == "דוחות" for r in d.rank_items(many)) == d.MAX_EARNINGS          # also: two companies are two stories


def test_digest_answer_must_carry_the_move_and_only_given_numbers():
    import ai_digest as d
    import json as _json
    items = [dict(id=1, title="Wall Street falls as AI stocks tumble 7%", tag="שוק", url="https://x.example/1", title_he="", body="")]
    answer = lambda overview, summary="AI stocks fell 7%.": _json.dumps(
        {"overview": overview, "why": "AI stocks tumbled 7%.", "why_items": [1],
         "topics": [{"title": "AI selloff", "summary": summary, "impact": "", "items": [1]}]})
    good = d.clean_answer(answer("The S&P 500 fell 4.4% and the Nasdaq 5.52%, led by AI stocks."), items, _CRASH_MARKET)
    assert good["topics"][0]["sources"][0]["url"] == "https://x.example/1"
    with pytest.raises(ValueError, match="extreme"):
        d.clean_answer(answer("Technology and earnings dominated the day."), items, _CRASH_MARKET)
    saved = d.clean_answer(answer("Technology and earnings dominated the day."), items, _CRASH_MARKET, strict=False)
    assert saved["overview_en"].startswith("The S&P 500 fell 4.36% and the Nasdaq fell 5.52%.")
    with pytest.raises(ValueError, match="numbers"):
        d.clean_answer(answer("The S&P 500 fell 4.4%, the worst since 1987."), items, _CRASH_MARKET)
    with pytest.raises(ValueError, match="numbers"):
        d.clean_answer(answer("The S&P 500 fell 4.4%.", "AI stocks fell 31%."), items, _CRASH_MARKET)
    normal = dict(_CRASH_MARKET, move="normal")
    assert d.clean_answer(answer("Stocks moved little."), items, normal)
    with pytest.raises(ValueError, match="advice"):
        d.clean_answer(answer("You should buy now."), items, normal)


def test_digest_rejects_what_the_first_real_answer_did():
    """v37b: the rerun of 5.11.2026 had the crash on top, and also these."""
    import ai_digest as d
    import json as _json
    items = [dict(id=1, title="Wall Street falls as AI stocks tumble 7%", tag="שוק", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Emirates saw its profits plunge as the Iran war drives up fuel costs", tag="דוחות", url="https://x.example/2", title_he="", body="")]
    good = "The S&P 500 fell 4.4%, led by AI stocks."

    def answer(overview=good, summary="AI stocks fell 7% and Wall Street followed.", impact="", nums=(1,)):
        return _json.dumps({"overview": overview, "why": d.NO_REASON,
                            "topics": [{"title": "AI selloff", "summary": summary, "impact": impact, "items": list(nums)}]})
    # a source about something else is not shown
    out = d.clean_answer(answer(nums=(1, 2)), items, _CRASH_MARKET)
    assert [s["url"] for s in out["topics"][0]["sources"]] == ["https://x.example/1"]
    # a topic with no item about it
    gold = _json.dumps({"overview": good, "topics": [{"title": "Gold and safe havens", "summary": "Gold gained.", "impact": "", "items": [2]}]})
    with pytest.raises(ValueError, match="no item about"):
        d.clean_answer(gold, items, _CRASH_MARKET)
    # the model's own explanation
    with pytest.raises(ValueError, match="on its own"):
        d.clean_answer(answer(impact="Stable yields suggested investors were not fleeing to bonds."), items, _CRASH_MARKET)
    saved = d.clean_answer(answer(impact="Stable yields suggested investors were not fleeing to bonds."), items, _CRASH_MARKET, strict=False)
    assert saved["topics"][0]["impact_en"] == ""
    # an overview that lists everything, and a date as a machine writes it
    long = good + " " + "AI stocks fell 7% and Wall Street followed them down all day long. " * 8
    with pytest.raises(ValueError, match="characters"):
        d.clean_answer(answer(overview=long), items, _CRASH_MARKET)
    assert len(d.clean_answer(answer(overview=long), items, _CRASH_MARKET, strict=False)["overview_en"]) <= d.MAX_OVERVIEW + len(d.NO_REASON) + 1
    dated = d.clean_answer(answer(overview="The S&P 500 fell 4.4% on 2026-11-05."), items, _CRASH_MARKET)
    assert dated["overview_en"] == "The S&P 500 fell 4.4%. " + d.NO_REASON
    # the third answer: a topic that only lists the numbers, and the main story last
    lists = _json.dumps({"overview": good, "topics": [{"title": "Wall Street moves", "summary": "On Wall Street oil rose 2.72% and gold 0.99%.", "impact": "", "items": [1]}]})
    with pytest.raises(ValueError, match="lists market numbers"):
        d.clean_answer(lists, items, _CRASH_MARKET)
    with pytest.raises(ValueError, match="no usable"):
        d.clean_answer(lists, items, _CRASH_MARKET, strict=False)
    two = [dict(items[0]), dict(id=3, title="Oil climbs on Iran worries as tankers wait", tag="אנרגיה", url="https://x.example/3", title_he="", body="")]
    topic = lambda title, summary, n: {"title": title, "summary": summary, "impact": "", "items": [n]}
    oil_first = [topic("Oil climbs", "Oil climbs on Iran worries.", 2), topic("AI selloff", "AI stocks fell 7% on Wall Street.", 1)]
    assert [t["title_en"] for t in d.clean_answer(_json.dumps({"overview": good, "why": d.NO_REASON, "topics": oil_first}), two, _CRASH_MARKET)["topics"]] == ["AI selloff", "Oil climbs"]
    with pytest.raises(ValueError, match="item \\[1\\]"):
        d.clean_answer(_json.dumps({"overview": good, "topics": oil_first[:1]}), two, _CRASH_MARKET)
    assert d.clean_answer(_json.dumps({"overview": good, "topics": oil_first[:1]}), two, dict(_CRASH_MARKET, move="normal"))
    # 14:06 ET on 6.11.2026: "The US market ended mixed"
    open_market = dict(_CRASH_MARKET, intraday=True)
    with pytest.raises(ValueError, match="still open"):
        d.clean_answer(answer(overview="The S&P 500 ended down 4.4%."), items, open_market)
    assert "so far" in d.clean_answer(answer(overview="The S&P 500 ended down 4.4%."), items, open_market, strict=False)["overview_en"]
    assert d.clean_answer(answer(overview="The S&P 500 ended down 4.4%."), items, _CRASH_MARKET)     # after the close, it did
    with pytest.raises(ValueError, match="on its own"):
        d.clean_answer(answer(summary="AI stocks fell 7% on profit-taking."), items, _CRASH_MARKET)


def test_movers_refresh_keeps_a_row_when_the_session_is_missing(scanner):
    """v37e: the 01:00 UTC run wrote Thursday's moves over Friday's."""
    import datetime as _dt
    idx = pd.to_datetime(["2026-11-04", "2026-11-05", "2026-11-06"])
    closes = pd.Series([488.78, 404.40, 449.04], index=idx)
    assert scanner.movers_row(closes, _dt.date(2026, 11, 6)) == (11.04, 449.04)
    assert scanner.movers_row(closes.iloc[:2], _dt.date(2026, 11, 6)) is None             # no Friday: keep what is there
    assert scanner.movers_row(closes.iloc[:2], None) == (-17.26, 404.4)                    # nothing to compare with
    assert scanner.movers_row(closes.iloc[:1], None) is None


def test_digest_movers_only_of_the_latest_session():
    """v37f: a refresh on Saturday stamps Friday's prices with Saturday: the date of a row says nothing about the session."""
    import datetime as _dt
    import ai_digest as d
    friday, thursday = _dt.date(2026, 11, 6), _dt.date(2026, 11, 5)

    class Cur:
        def __init__(self):
            self.sql, self.rows = "", []

        def execute(self, sql, params=None):
            self.sql = sql
            self.rows = {"market_breadth WHERE": [(184, 318, 11, 2)], "MAX(day)": [(friday,)], "ASC": [("NTRA", -14.6)], "DESC": [("AMD", 11.04)]}[
                next(k for k in ("market_breadth WHERE", "MAX(day)", "ASC", "DESC") if k in sql)]

        def fetchone(self):
            return self.rows[0]

        def fetchall(self):
            return self.rows

        def close(self):
            pass

    class Conn:
        def cursor(self):
            return Cur()

        def rollback(self):
            pass
    latest = d._market_from_db(Conn(), friday)
    assert latest["movers"]["up"] == [{"ticker": "AMD", "change_pct": 11.04}] and latest["breadth"]["advancers"] == 184
    assert "movers" not in d._market_from_db(Conn(), thursday)                 # an earlier day written again


def test_movers_gap_in_health():
    import data_health as dh
    assert dh.movers_gap(73, 430, 184, 318) > dh.MOVERS_MAX_GAP                            # 7.11.2026
    assert dh.movers_gap(180, 320, 184, 318) <= dh.MOVERS_MAX_GAP


def test_chart_bars_by_date():
    """v37e: Friday's move of Bitcoin came out as Thursday's -5.55%."""
    import ai_digest as d
    import datetime as _dt
    day = lambda s: int(_dt.datetime.fromisoformat(s).replace(tzinfo=_dt.timezone.utc).timestamp())
    data = {"chart": {"result": [{"timestamp": [day("2026-11-05T00:00:00"), day("2026-11-06T00:00:00"), day("2026-11-06T18:00:00"), day("2026-11-07T00:00:00")],
                                  "indicators": {"quote": [{"close": [100.0, 99.0, 99.74, None]}]}}]}}
    assert d.bars_from_chart(data) == [(_dt.date(2026, 11, 5), 100.0), (_dt.date(2026, 11, 6), 99.74)]


def test_digest_does_not_tell_yesterdays_move_as_todays():
    """v37c: 5.11.2026: "Oil prices tumbled", from headlines about 4.11 (-8.29%), and oil +2.72% on the same screen."""
    import ai_digest as d
    import json as _json
    market = dict(_CRASH_MARKET, indices=_CRASH_MARKET["indices"] + [{"symbol": "CL=F", "name_en": "Oil (WTI)", "close": 90.5, "change_pct": 2.72, "change_points": 2.4}])
    f = d.contradicts_market
    assert "Oil prices tumbled" in f("Oil prices tumbled as the results sparked bets.", market)
    assert "2.72" in f("WTI crude fell sharply.", market)
    assert f("Oil rose 2.72%.", market) is None and f("Energy stocks fell while oil rose.", market) is None
    assert "Nasdaq" in f("The Nasdaq surged.", market)
    assert f("Oil prices tumbled.", dict(market, same_day=False)) is None and f("Oil prices tumbled.", None) is None
    items = [dict(id=1, title="Oil prices tumble after midterms", tag="אנרגיה", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Wall Street falls as AI stocks tumble 7%", tag="שוק", url="https://x.example/2", title_he="", body="")]
    topic = lambda title, summary, n: {"title": title, "summary": summary, "impact": "", "items": [n]}
    text = _json.dumps({"overview": "The S&P 500 fell 4.4%.", "topics": [topic("Oil", "Oil prices tumbled after the midterms.", 1), topic("AI", "AI stocks fell 7% on Wall Street.", 2)]})
    with pytest.raises(ValueError, match="earlier day"):
        d.clean_answer(text, items, market)
    assert [t["title_en"] for t in d.clean_answer(text, items, market, strict=False)["topics"]] == ["AI"]


def test_digest_guess_words_are_fine_when_the_item_says_them():
    """v37c: "Housing Stocks Jumped on Tariff Relief Hopes" is a headline, and three good answers were rejected over "relief"."""
    import ai_digest as d
    import json as _json
    items = [dict(id=1, title="Housing Stocks Jumped on Tariff Relief Hopes", tag="שוק", url="https://x.example/1", title_he="", body="")]
    answer = lambda s: _json.dumps({"overview": "Stocks moved little.", "topics": [{"title": "Housing stocks", "summary": s, "impact": "", "items": [1]}]})
    normal = dict(_CRASH_MARKET, move="normal")
    assert d.clean_answer(answer("Housing stocks jumped on tariff relief hopes."), items, normal)
    with pytest.raises(ValueError, match="on its own"):
        d.clean_answer(answer("Housing stocks jumped, possibly on tariffs."), items, normal)


def test_digest_saves_on_a_new_connection_after_the_ai(monkeypatch):
    """v37c: the AI takes minutes, and Neon closed the connection opened before it: the finished digest was lost at the INSERT."""
    import datetime as _dt
    import ai_digest as d

    class Conn:
        def __init__(self):
            self.closed, self.wrote = False, False

        def cursor(self):
            conn = self

            class Cur:
                def execute(self, sql, params=None):
                    assert not conn.closed
                    if "INSERT INTO ai_daily_digests" in sql:
                        conn.wrote = True

                def close(self):
                    pass
            return Cur()

        def commit(self):
            pass

        def close(self):
            self.closed = True
    conns = []

    def connect():
        conns.append(Conn())
        return conns[-1]
    titles = [dict(id=i, tag="שוק", title=f"Wall Street headline number {i}", source="Reuters", ts=_dt.datetime(2026, 11, 5, 20, tzinfo=_dt.timezone.utc)) for i in range(1, 7)]
    monkeypatch.setattr(d, "_load_row", lambda conn, day: None)
    monkeypatch.setattr(d, "_fetch_titles", lambda conn, day: titles)
    monkeypatch.setattr(d, "_add_details", lambda conn, items: items)
    monkeypatch.setattr(d, "market_facts", lambda conn, day: None)

    def ai(*a, **k):
        assert all(c.closed for c in conns), "a connection stays open during the AI"
        return {"overview_en": "x", "topics": []}, "model"
    monkeypatch.setattr(d, "_ask", ai)
    monkeypatch.setattr(d, "_add_hebrew", lambda payload, *a: payload)
    assert d._update_day(connect, _dt.date(2026, 11, 5), lambda x: x, force=True, final=True)
    assert len(conns) == 2 and conns[1].wrote and all(c.closed for c in conns)


def test_digest_numbers_may_be_rounded_either_way():
    import ai_digest as d
    allowed = d.allowed_numbers(dict(_CRASH_MARKET, indices=[{"symbol": "^SOX", "name_en": "SOX", "close": 9011.14, "change_pct": -12.25, "change_points": -1}]), [])
    assert d.invented_numbers("SOX fell 12.3%, or 12.25%, or 12.2%, or 12%", allowed) == []
    assert d.invented_numbers("SOX fell 13.1%", allowed) == ["13.1"]
    assert d.opens_with_the_move("The Nasdaq fell 5.5%.", _CRASH_MARKET) and not d.opens_with_the_move("The Nasdaq fell 6%.", _CRASH_MARKET)


def test_digest_models_only_for_the_digest(monkeypatch):
    import ai_digest as d
    import os as _os
    monkeypatch.setenv("LLM_MODELS", "small")
    monkeypatch.setenv("LLM_MODELS_DIGEST", "large,small")
    monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
    with d._digest_models():
        assert _os.environ["LLM_MODELS"] == "large,small" and _os.environ["LLM_TIMEOUT_SECONDS"] == d.DIGEST_CALL_TIMEOUT_SEC
    assert _os.environ["LLM_MODELS"] == "small" and "LLM_TIMEOUT_SECONDS" not in _os.environ
    monkeypatch.delenv("LLM_MODELS_DIGEST")
    with d._digest_models():
        assert _os.environ["LLM_MODELS"] == "small"          # not set: the same models as the other jobs


def test_digest_hebrew_has_no_english_words_and_known_fixes():
    import ai_digest as d
    import json as _json
    en = {"overview": "Nvidia fell.", "topics": [{"title": "AI", "summary": "Chips fell.", "impact": ""}]}
    he = lambda text: _json.dumps({"overview": text, "topics": [{"title": "בינה מלאכותית", "summary": "השבבים ירדו.", "impact": ""}]})
    with pytest.raises(ValueError, match="English words"):
        d.clean_hebrew(he("התנהגות typicall risk-off של השוק"), en)
    assert d.clean_hebrew(he("מניות Nvidia ו-VIX ירדו"), en)["overview"].startswith("מניות Nvidia")
    with pytest.raises(ValueError, match="broken"):
        d.clean_hebrew(he("המהלך נבע� מהתאוששות"), en)
    with pytest.raises(ValueError, match="English words"):
        d.clean_hebrew(he("אם כי breadth היה צר"), en)
    no_name = dict(en, overview="An unusual sell-off after the midterm elections.")
    assert d.clean_hebrew(he("מכירה חריגה אחרי הבחירות האמצעיות"), no_name)["overview"] == "גל מכירות חריג אחרי בחירות אמצע הקדנציה"
    with pytest.raises(ValueError, match="lost the name Nvidia"):          # v51
        d.clean_hebrew(he("ניידות נמוטרון ירדה"), en)


def test_stock_note_numbers_must_be_in_the_facts():
    import ai_stock_notes as n
    import json as _json
    facts = _json.dumps({"rs_rating_1_99": 83, "price": 95.73, "fundamentals": {"market_cap_usd": 2100000000.0, "gross_margin_pct": 61.26}})
    ok = {"he": "RS 83, מחיר 95.73, שווי 2.1 מיליארד, רווח גולמי 61.3% ב-3 רבעונים", "en": "RS 83, price 95.7, cap 2.1 billion."}
    assert n.invented_numbers(ok, facts) == []
    assert n.invented_numbers({"he": "הכנסות עלו 45%", "en": "Revenue rose 45%, price 96.5."}, facts) == ["45", "96.5"]


def test_digest_market_text_and_size():
    import ai_digest as d
    text = d._market_text(_CRASH_MARKET, __import__("datetime").date(2026, 11, 5))
    assert "S&P 500: -4.36%" in text and "73 / 430" in text and "NVDA -9.10%" in text and "EXTREME" in text and "4.50% (change -0.05" in text
    closed = dict(_CRASH_MARKET, same_day=False, move="closed", status="weekend")
    weekend = d._market_text(closed, __import__("datetime").date(2026, 11, 7))
    # v67: the last session's index numbers are back, labelled with their day; still no movers and no breadth
    assert "weekend" in weekend and "NVDA" not in weekend and "73 / 430" not in weekend
    assert "LAST SESSION: Thursday 2026-11-05 (NOT today)" in weekend and "S&P 500 -4.36%" in weekend and "Today is Saturday" in weekend
    assert "not available" in d._market_text(None, __import__("datetime").date(2026, 11, 7))
    size = lambda sp, nq: d.move_size([{"symbol": "^GSPC", "change_pct": sp}, {"symbol": "^IXIC", "change_pct": nq}], True)
    assert size(-4.36, -5.52) == "extreme" and size(-0.5, 2.6) == "extreme" and size(1.2, 1.0) == "notable" and size(0.2, 0.3) == "normal"
    assert d.move_size([], False) == "closed"


def test_digest_on_a_day_without_a_session():
    """v38b: 7-9.11.2026. Saturday, Sunday and Monday morning told Friday's +0.31% / +1.09% / -0.46% as the day's,
    and Monday's gave AMD's +11.04% to Nvidia."""
    import datetime as _dt
    import json as _json
    from zoneinfo import ZoneInfo
    import ai_digest as d
    et = ZoneInfo("America/New_York")
    sat, mon = _dt.date(2026, 11, 7), _dt.date(2026, 11, 9)
    assert d.no_session_reason(sat, _dt.datetime(2026, 11, 7, 12, 0, tzinfo=et)) == "weekend"
    assert d.no_session_reason(mon, _dt.datetime(2026, 11, 9, 7, 41, tzinfo=et)) == "pre_open"
    assert d.no_session_reason(_dt.date(2026, 11, 26), _dt.datetime(2026, 11, 26, 13, 0, tzinfo=et)) == "holiday"
    assert d.no_session_reason(_dt.date(2026, 11, 26), _dt.datetime(2026, 11, 26, 7, 0, tzinfo=et)) == "holiday"   # v38c: Thanksgiving morning
    # the numbers: Friday's stay for the site, the model gets none, Bitcoin's own day is kept
    friday = [(_dt.date(2026, 11, 5), 7353.73), (_dt.date(2026, 11, 6), 7376.86)]
    btc = [(_dt.date(2026, 11, 6), 75746.93), (_dt.date(2026, 11, 7), 76504.4), (_dt.date(2026, 11, 8), 76000.0)]
    hist = lambda s: btc if s == "BTC-USD" else friday

    class NoTables:                      # breadth and movers are not what this test is about
        def cursor(self):
            return self
        def execute(self, *a):
            raise RuntimeError("no table")
        def rollback(self): pass
        def close(self): pass
    real = d._history
    d._history = hist
    try:
        m = d.market_facts(NoTables(), sat, now=_dt.datetime(2026, 11, 7, 12, 0, tzinfo=et))
    finally:
        d._history = real
    assert m["same_day"] is False and m["status"] == "weekend" and m["session_day"] == "2026-11-06" and m["day"] == "2026-11-07"
    assert next(i for i in m["indices"] if i["symbol"] == "^GSPC")["change_pct"] == 0.31
    assert m["today"] == [{"symbol": "BTC-USD", "name_en": "Bitcoin", "name_he": "ביטקוין", "change_pct": 1.0, "close": 76504.4}]
    assert "+1.00% on this day" in d._market_text(m, sat) and "on Friday" in d._market_text(m, sat)
    assert m["last_move"] == "normal" and "do not retell it" in d._market_text(m, sat)
    # the answer
    items = [dict(id=1, title="China extends tariff suspension on US goods to January", tag="מכסים", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Nvidia shares fall 5% in premarket after board probe", tag="שוק", url="https://x.example/2", title_he="", body="")]
    answer = lambda overview, summary: _json.dumps({"overview": overview, "topics": [
        {"title": "China extends tariff suspension", "summary": summary, "impact": "", "items": [1]},
        {"title": "Nvidia board probe", "summary": "Nvidia shares fell 5% in premarket trading after a board probe.", "impact": "", "items": [2]}]})
    good = d.clean_answer(answer("China extended its tariff suspension on US goods to January. Nvidia fell 5% in premarket trading.",
                                 "China extended the suspension of tariffs on US goods to January."), items, m)
    assert len(good["topics"]) == 2
    with pytest.raises(ValueError, match="did not trade"):
        d.clean_answer(answer("The US market fell, with the Dow down. China extended its tariff suspension.",
                              "China extended the suspension of tariffs on US goods."), items, m)
    with pytest.raises(ValueError, match="numbers"):      # Friday's numbers are not in what it was given any more
        d.clean_answer(answer("Chip stocks rose 4.21% on Friday. China extended its tariff suspension.",
                              "China extended the suspension of tariffs on US goods."), items, m)
    with pytest.raises(ValueError, match="did not trade"):
        d.clean_answer(answer("China extended its tariff suspension on US goods.",
                              "China extended the tariff suspension. Chip stocks surged and Nvidia led gains among large companies."), items, m)
    saved = d.clean_answer(answer("The US market fell. China extended its tariff suspension on US goods.",
                                  "China extended the tariff suspension. Nvidia led gains among large companies."), items, m, strict=False)
    assert saved["overview_en"] == "China extended its tariff suspension on US goods." and "led gains" not in saved["topics"][0]["summary_en"]
    assert d.session_told("The S&P 500 fell 2% last week.", m) is None and d.session_told("Stock futures rose on Sunday.", m) is None
    assert d.session_told("The US market fell.", dict(m, same_day=True)) is None
    # written before the open -> written again once after it; and the health check sees the old mistake
    row = {"payload": {"market": {"status": "pre_open"}}}
    assert d.after_open_update_due(row, mon, _dt.datetime(2026, 11, 9, 10, 5, tzinfo=et))
    assert not d.after_open_update_due(row, mon, _dt.datetime(2026, 11, 9, 9, 40, tzinfo=et))
    assert not d.after_open_update_due({"payload": {"market": {"status": "open"}}}, mon, _dt.datetime(2026, 11, 9, 11, 0, tzinfo=et))
    import data_health as dh
    told = {"overview_en": "The US market fell, with the Dow Jones dropping 0.46% while the S&P 500 rose 0.31%.", "market": m}
    assert dh.digest_problems(told) and "S&P 500 0.31%" in dh.digest_problems(told)[0]
    assert dh.digest_problems({"overview_en": "China extended its tariff suspension.", "market": m}) == []


def test_digest_gives_each_move_to_its_own_company():
    """v38c: 9.11.2026, "Nvidia led large-cap gains with an 11.04% rise": AMD's number."""
    import json as _json
    import ai_digest as d
    market = dict(_CRASH_MARKET, move="normal", movers={"up": [{"ticker": "AMD", "change_pct": 11.04}], "down": [{"ticker": "NTRA", "change_pct": -14.59}]})
    items = [dict(id=1, title="Nvidia board launches probe into CEO", tag="שוק", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Chip stocks rally as AMD jumps on data center deal", tag="שוק", url="https://x.example/2", title_he="", body="")]
    answer = lambda summary: _json.dumps({"overview": "Stocks moved little.", "topics": [
        {"title": "Nvidia board probe", "summary": summary, "impact": "", "items": [1]}]})
    with pytest.raises(ValueError, match="AMD"):
        d.clean_answer(answer("Nvidia led large-cap gains with an 11.04% rise. Its board launched a probe."), items, market)
    with pytest.raises(ValueError, match="AMD"):
        d.clean_answer(answer("Nvidia rose 11.0%. Its board launched a probe."), items, market)
    assert d.clean_answer(_json.dumps({"overview": "Stocks moved little. AMD rose 11.04%.", "topics": [
        {"title": "Nvidia board probe", "summary": "Nvidia's board launched a probe into its CEO.", "impact": "", "items": [1]}]}), items, market)
    with pytest.raises(ValueError, match="none of its items"):     # v67: AMD is not this topic's story
        d.clean_answer(answer("AMD rose 11.04%. Nvidia's board launched a probe into its CEO."), items, market)
    saved = d.clean_answer(answer("Nvidia led gains with an 11.04% rise. Its board launched a probe."), items, market, strict=False)
    assert saved["topics"][0]["summary_en"] == "Its board launched a probe."
    # the same number in an item belongs to that item's story, and is not checked
    with_number = items + [dict(id=3, title="Nvidia shares up 11.04% in a month", tag="שוק", url="https://x.example/3", title_he="", body="")]
    assert d.misattributed_move("Nvidia is up 11.04% in a month.", market, with_number) is None
    assert "NTRA" in d.misattributed_move("Natera fell 14.59%.", market, items)


_FRIDAY_20_11 = {"day": "2026-11-20", "session_day": "2026-11-20", "same_day": True, "intraday": False, "status": "traded", "move": "extreme",
                 "indices": [{"symbol": "^GSPC", "name_en": "S&P 500", "close": 6910.39, "change_pct": -3.7, "change_points": -265.44},
                             {"symbol": "^IXIC", "name_en": "Nasdaq", "close": 22617.73, "change_pct": -5.28, "change_points": -1261.47},
                             {"symbol": "^SOX", "name_en": "Chip stocks (SOX)", "close": 8408.59, "change_pct": -10.73, "change_points": -1011.13}],
                 "breadth": {"advancers": 124, "decliners": 376},
                 "movers": {"down": [{"ticker": "NVDA", "change_pct": -14.01}], "up": [{"ticker": "HDB", "change_pct": 4.5}]},
                 "week": [{"symbol": "^GSPC", "name_en": "S&P 500", "change_pct": -6.12}]}


def test_digest_says_why_a_big_day_moved():
    """v67: Friday 20.11.2026, S&P 500 -3.7%, Nasdaq -5.28%: the reason was spread over the topics and never said as the reason,
    "HDB up 4.5%" (an Indian bank) was an example of consumer staples, and Saturday opened with the inflation data of the week."""
    import datetime as _dt
    import json as _json
    import ai_digest as d
    import data_health as dh
    items = [dict(id=1, title="Wall St ends sharply lower on AI selloff, S&P 500, Dow in correction", tag="שוק", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Fed's Cook says rate hikes needed to lower high inflation", tag="ריבית", url="https://x.example/2", title_he="", body=""),
             dict(id=3, title="AI tech rout prompts US investors to rush into value stocks", tag="AI", url="https://x.example/3", title_he="", body="")]
    overview = "The S&P 500 fell 3.7% and the Nasdaq 5.3%, and 376 of its stocks fell."
    topics = [{"title": "AI selloff deepens", "summary": "Wall Street ended sharply lower on the AI selloff.", "impact": "", "items": [1]},
              {"title": "Value stocks", "summary": "Value stocks gained as the AI selloff hit tech. HDB rose 4.5%.", "impact": "", "items": [3]}]
    answer = lambda **k: _json.dumps(dict({"overview": overview, "topics": topics[:1]}, **k))
    # a big day with no reason is sent back
    with pytest.raises(ValueError, match="'why' is empty"):
        d.clean_answer(answer(), items, _FRIDAY_20_11)
    why = "Stocks fell on an AI selloff, and Fed governor Cook said rate hikes are needed to lower high inflation."
    out = d.clean_answer(answer(why=why, why_items=[1, 2]), items, _FRIDAY_20_11)
    assert out["why_en"] == why and out["overview_en"] == f"{overview} Why: {why}"
    # the reason must come from the items it names
    with pytest.raises(ValueError, match="no items"):
        d.clean_answer(answer(why=why), items, _FRIDAY_20_11)
    with pytest.raises(ValueError, match="on its own"):
        d.clean_answer(answer(why="Investors fled risk as profit-taking hit chips.", why_items=[1]), items, _FRIDAY_20_11)
    assert d.clean_answer(answer(why=d.NO_REASON), items, _FRIDAY_20_11)["overview_en"].endswith(d.NO_REASON)
    # a mover's ticker as an example of a group
    with pytest.raises(ValueError, match="HDB"):
        d.clean_answer(_json.dumps({"overview": overview, "why": why, "why_items": [1, 2], "topics": topics}), items, _FRIDAY_20_11)
    saved = d.clean_answer(_json.dumps({"overview": overview, "why": why, "why_items": [1, 2], "topics": topics}), items, _FRIDAY_20_11, strict=False)
    assert saved["topics"][1]["summary_en"] == "Value stocks gained as the AI selloff hit tech."
    # the week's own number may be told
    assert "S&P 500 -6.12%" in d._market_text(_FRIDAY_20_11, _dt.date(2026, 11, 20))
    assert d.week_change([(_dt.date(2026, 11, 13), 100.0), (_dt.date(2026, 11, 16), 99.0), (_dt.date(2026, 11, 20), 94.0)], _dt.date(2026, 11, 20)) == -6.0
    assert d.week_change([(_dt.date(2026, 11, 13), 100.0), (_dt.date(2026, 11, 16), 99.0)], _dt.date(2026, 11, 16)) is None   # Monday
    # Saturday after it
    sat = dict(_FRIDAY_20_11, day="2026-11-21", same_day=False, status="weekend", move="closed", last_move="extreme")
    text = d._market_text(sat, _dt.date(2026, 11, 21))
    assert "LAST SESSION: Friday" in text and "EXTREME" in text and "never 'last week'" in text and "NVDA" not in text
    weekend = lambda ov, w="": _json.dumps({"overview": ov, "why": w, "why_items": [1, 2] if w else [],
                                             "topics": [{"title": "Inflation data ahead", "summary": "Rate hikes are needed to lower high inflation, Cook said.", "impact": "", "items": [2]}]})
    with pytest.raises(ValueError, match="extreme"):
        d.clean_answer(weekend("Inflation data is in focus this week.", why), items, sat)
    lead = d.clean_answer(weekend("Inflation data is in focus this week."), items, sat, strict=False)["overview_en"]
    assert lead.startswith("On Friday, the S&P 500 fell 3.7% and the Nasdaq fell 5.3%.")
    with pytest.raises(ValueError, match="this past week"):
        d.clean_answer(weekend("On Friday the S&P 500 fell 3.7%, ending last week in a correction.", why.replace("Stocks fell", "Stocks fell on Friday")), items, sat)
    good = d.clean_answer(weekend("On Friday the S&P 500 fell 3.7%, and 6.1% in the week.", why.replace("Stocks fell", "Stocks fell on Friday")), items, sat)
    assert "Why:" in good["overview_en"]
    with pytest.raises(ValueError, match="without its day"):        # a reason that tells the move as Saturday's
        d.clean_answer(weekend("On Friday the S&P 500 fell 3.7%.", "The S&P 500 fell on an AI selloff."), items, sat)
    # the health check
    assert any("no reason" in p for p in dh.digest_problems({"overview_en": overview, "market": _FRIDAY_20_11}))
    assert dh.digest_problems({"overview_en": out["overview_en"], "why_en": why, "market": _FRIDAY_20_11}) == []
    assert dh.digest_problems({"overview_en": "On Friday, the S&P 500 fell 3.70%. Why: x", "why_en": "x", "market": sat}) == []
    assert dh.digest_problems({"overview_en": "The S&P 500 fell 3.70%.", "why_en": "x", "market": sat})
    # the Hebrew keeps the reason as a reason
    en = {"overview": out["overview_en"], "topics": [{"title": "AI selloff deepens", "summary": "Wall Street ended sharply lower.", "impact": ""}]}
    he_topic = [{"title": "גל המכירות בבינה מלאכותית מעמיק", "summary": "וול סטריט ננעלה בירידות חדות.", "impact": ""}]
    with pytest.raises(ValueError, match="הסיבה"):
        d.clean_hebrew(_json.dumps({"overview": "מדד S&P 500 ירד 3.7% והנאסד״ק 5.3%, ו-376 מניותיו ירדו. למה: גל מכירות.", "topics": he_topic}), en)
    with pytest.raises(ValueError, match="טריטוריה"):
        d.clean_hebrew(_json.dumps({"overview": "מדד S&P 500 ירד 3.7% והנאסד״ק 5.3%, ו-376 מניות ירדו, בטריטוריית תיקון. הסיבה: גל מכירות.", "topics": he_topic}), en)
    assert d.clean_hebrew(_json.dumps({"overview": "מדד S&P 500 ירד 3.7% והנאסד״ק 5.3%, ו-376 מניות ירדו. הסיבה: גל מכירות.", "topics": he_topic}), en)
    # the MarketBeat lists on the home page of 21.11.2026
    from news_links import is_unwanted_headline
    for t in ("Best Luxury Goods Stocks To Research - November 21st", "Promising Fintech Stocks Worth Watching - November 21st",
              "Value Stocks To Consider - November 21st"):
        assert is_unwanted_headline(t), t
    assert not is_unwanted_headline("Wall St ends sharply lower on AI selloff, S&P 500, Dow in correction")


def test_v67b_weekend_order_and_headline_translations():
    """v67b: Saturday 21.11.2026 kept "inflation data in focus" above the week's crash; the key events had "שמן מפרסם ירידה",
    "קנדה נטעה", "תצוגה מיידית:", "אנתרופי", "J.P. מורגן מחירים" (each rewrite checked against Google from home)."""
    import json as _json
    import ai_digest as d
    from translation_fixes import prep_en, fix_he
    items = [dict(id=1, title="Inflation, growth and consumer sentiment in focus this week", tag="אינפלציה", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Tech stocks end week at low as Nasdaq sell-off deepens", tag="שוק", url="https://x.example/2", title_he="", body="")]
    sat = dict(_FRIDAY_20_11, day="2026-11-21", same_day=False, status="weekend", move="closed", last_move="extreme")
    answer = _json.dumps({"overview": "On Friday the S&P 500 fell 3.7%.", "why": "Tech stocks fell on Friday as the Nasdaq sell-off deepened.",
                          "why_items": [2], "topics": [
                              {"title": "Inflation data in focus", "summary": "Inflation, growth and consumer sentiment are in focus this week.", "impact": "", "items": [1]},
                              {"title": "Tech sell-off deepens", "summary": "Tech stocks ended the week at a low as the Nasdaq sell-off deepened.", "impact": "", "items": [2]}]})
    assert [t["title_en"] for t in d.clean_answer(answer, items, sat)["topics"]] == ["Tech sell-off deepens", "Inflation data in focus"]
    assert [t["title_en"] for t in d.clean_answer(answer, items, dict(sat, last_move="normal"))["topics"]][0] == "Inflation data in focus"
    assert d._own_guess("Yields fell as traders sought safety.") == "traders sought"
    # the English sent to the translator
    assert prep_en("Oil Posts Third Weekly Drop as Ample Supply Outweighs War Risk").startswith("Oil prices record Third")
    assert prep_en("Gold posts record high") == "Gold prices reach a record high"
    assert prep_en("Oil prices edge higher as Houthi attacks raise supply concerns").startswith("Oil prices rise slightly")
    assert "Auto factory" in prep_en("Canada Auto Plant The 'Front Line' In Trump's Trade War")
    assert prep_en("Instant view: Nasdaq plunges 5%") == "Nasdaq plunges 5%"
    assert prep_en("J.P. Morgan prices in stunning shift on Fed rate-hike call") == "J.P. Morgan now expect stunning shift on Fed interest rate hike forecast"
    assert prep_en("Oil prices in Asia rose") == "Oil prices in Asia rose"                       # not the verb
    # the Hebrew that came back, also for rows already saved (api_server._tidy_news_row)
    assert fix_he("שמן מפרסם ירידה שבועית שלישית מכיוון שהאספקה ​​בשפע") == "הנפט רושם ירידה שבועית שלישית מכיוון שהאספקה בשפע"
    assert fix_he("שמן זית התייקר") == "שמן זית התייקר"
    assert fix_he("לאחר הופעת הבכורה של אנתרופי, אבל") == "לאחר הופעת הבכורה של אנתרופיק, אבל"
    assert fix_he("הוצאה אנתרופית") == "הוצאה של אנתרופיק" and fix_he(fix_he("הוצאה אנתרופית")) == "הוצאה של אנתרופיק"


def test_v68_earnings_time_from_the_sec_filing(scanner):
    """v68: 16-19.11.2026 had 17-19 of ~22 reports a day with no time: Nasdaq answers "time-not-supplied" once the day passed,
    and each run wrote it over the time it had. The SEC filings of 18.11 (acceptance times as the SEC gives them)."""
    import datetime as _dt
    from translation_fixes import fix_he
    d18, d19 = _dt.date(2026, 11, 18), _dt.date(2026, 11, 19)
    sub = lambda *rows: {"filings": {"recent": {"form": [r[0] for r in rows], "items": [r[1] for r in rows],
                                                 "acceptanceDateTime": [r[2] for r in rows], "accessionNumber": [r[3] for r in rows]}}}
    no_read = lambda acc: (_ for _ in ()).throw(AssertionError("the release should not be read"))
    f = lambda *rows: scanner.earnings_filings(sub(*rows))
    # same day: the filing's minute decides
    assert scanner.confirm_report_time(f(("8-K", "2.02,9.01", "2026-11-18T14:19:28.000Z", "tjx")), d18, no_read) == (d18, "bmo", "tjx")
    assert scanner.confirm_report_time(f(("8-K", "2.02,9.01", "2026-11-18T21:00:15.000Z", "wwd")), d18, no_read) == (d18, "amc", "wwd")
    assert scanner.confirm_report_time(f(("6-K", "", "2026-11-18T12:10:44.000Z", "zim")), d18, no_read) == (d18, "bmo", "zim")
    # the next morning: the release's own date decides
    ugi = f(("8-K", "7.01,9.01", "2026-11-19T12:48:00.000Z", "ugi-701"), ("8-K", "2.02,7.01,9.01", "2026-11-19T12:45:46.000Z", "ugi"),
            ("8-K", "1.01,2.03,9.01", "2026-11-17T21:30:46.000Z", "ugi-debt"))
    assert len(ugi) == 1
    assert scanner.confirm_report_time(ugi, d18, lambda acc: d18) == (d18, "amc", "ugi")
    joyy = f(("6-K", "", "2026-11-19T11:01:26.000Z", "joyy"))
    assert scanner.confirm_report_time(joyy, d18, lambda acc: d19) == (d19, "bmo", "joyy")
    assert scanner.confirm_report_time(joyy, d18, lambda acc: None) is None                 # cannot tell: left as it is
    # during trading, two 6-Ks, nothing near: not settled
    assert scanner.confirm_report_time(f(("8-K", "2.02", "2026-11-18T15:30:00.000Z", "x")), d18, no_read) is None
    assert scanner.confirm_report_time(f(("6-K", "", "2026-11-18T12:00:00.000Z", "a"), ("6-K", "", "2026-11-18T22:00:00.000Z", "b")), d18, no_read) is None
    assert scanner.confirm_report_time(f(("8-K", "2.02", "2026-11-10T21:00:00.000Z", "old")), d18, no_read) is None
    # the evening before the listed day
    assert scanner.confirm_report_time(f(("8-K", "2.02", "2026-11-17T21:20:51.000Z", "nv")), d18, no_read) == (_dt.date(2026, 11, 17), "amc", "nv")
    # the release's date line
    assert scanner.release_dateline("November 18, 2026 VALLEY FORGE, PA - UGI Corporation (NYSE: UGI) today reported") == d18
    assert scanner.release_dateline("BEIJING, Nov. 19, 2026 (GLOBE NEWSWIRE) -- JOYY Inc") == d19
    assert scanner.release_dateline("no date here") is None and scanner.release_dateline(None) is None
    # the Hebrew of 22.11 (key events)
    assert fix_he("הנפט מפרסם ירידה שבועית שלישית") == "הנפט רושם ירידה שבועית שלישית"
    assert fix_he("תצוגה מיידית: נאסד\"ק צוללת ב-5%") == "נאסד\"ק צוללת ב-5%"


def test_v69_days_since_the_report_reaction(scanner):
    """v69: a momentum surge whose 5 days hold the report's reaction is the report. Mon 16.11 - Fri 20.11.2026 bars."""
    import datetime as _dt
    import pandas as pd
    idx = pd.DatetimeIndex(pd.date_range("2026-11-09", "2026-11-20", freq="B"), tz="America/New_York")
    hist = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    d = lambda day: _dt.date(2026, 11, day)
    assert scanner.days_since_report(hist, d(20), "bmo") == 0          # morning release: the day itself
    assert scanner.days_since_report(hist, d(20), "amc") is None       # evening release: reacts on Monday
    assert scanner.days_since_report(hist, d(19), "amc") == 0          # Thursday evening -> Friday
    assert scanner.days_since_report(hist, d(16), "bmo") == 4          # still inside a 5-day surge
    assert scanner.days_since_report(hist, d(16), None) == 4           # unknown time: counted from the report day
    assert scanner.days_since_report(hist, d(13), "amc") == 4          # Friday evening -> Monday
    assert scanner.days_since_report(hist, d(13), "bmo") == 5          # tagged, no longer inside the surge
    assert scanner.days_since_report(hist, d(12), "bmo") is None       # too long ago
    assert scanner.days_since_report(hist, d(10), "bmo") is None
    assert scanner.days_since_report(hist, None, "bmo") is None
    assert scanner.POST_REPORT_SURGE_DAYS == 4 and scanner.POST_REPORT_TAG_DAYS == 5


def test_v68_health_counts_reports_without_a_time():
    import datetime as _dt
    import data_health as dh

    class Cur:
        def __init__(self, rows): self.rows = rows
        def execute(self, *a): pass
        def fetchall(self): return self.rows
    row = lambda t, s: {"ticker": t, "eps_actual": 1.0, "revenue_actual": 1.0, "rel": False, "covered": True, "session": s}
    now = _dt.datetime(2026, 11, 24, 12, 0, tzinfo=_dt.timezone.utc)
    lines = {l["check"]: l for l in dh.check_earnings(Cur([row("A", "bmo"), row("B", "amc"), row("C", "unknown"), row("D", None)]), now)}
    assert lines["earnings.time"]["status"] == "warn" and "C, D" in lines["earnings.time"]["detail"]
    lines = {l["check"]: l for l in dh.check_earnings(Cur([row("A", "bmo")] * 9 + [row("C", "unknown")]), now)}
    assert lines["earnings.time"]["status"] == "info"


def test_revenue_in_another_currency_or_zero_is_not_shown(scanner, api):
    """v38c: 6.11.2026, Honda's revenue estimate in yen shown as "$5,816B", and 0 for companies no analyst covers."""
    honda_cap = 46425510000.0
    for f in (lambda v, c: api._usable_revenue(v, c), lambda v, c: scanner._usable_revenue("X", v, c)):
        assert f(5816042000000.0, honda_cap) is None and f(0.0, 3e9) is None and f(None, 3e9) is None
        assert f(38e9, honda_cap) == 38e9 and f(12e9, None) == 12e9
    assert api._REV_MCAP_MAX == scanner.REV_MCAP_MAX
    import datetime as _dt
    import data_health as dh

    class Cur:
        def execute(self, *a): pass
        def fetchall(self):
            row = lambda t, eps, cov: {"ticker": t, "eps_actual": eps, "revenue_actual": None, "rel": False, "covered": cov}
            return [row("AAPL", 1.2, True), row("NHC", None, False), row("HMC", None, False), row("XYZ", None, True)]
    line = dh.check_earnings(Cur(), _dt.datetime(2026, 11, 9, 12, 0, tzinfo=_dt.timezone.utc))[0]
    assert line["value"] == 1 and "nothing at all: XYZ" in line["detail"] and "not counted): NHC, HMC" in line["detail"]


def test_old_news_is_deleted_in_small_steps():
    """v38c: 90 days, like the daily summaries; at most NEWS_PRUNE_BATCH rows a run."""
    import pipeline_b_news_aggregator as pb
    import ai_digest
    seen = {}

    class Cur:
        rowcount = 2000
        def execute(self, sql, params):
            seen.update(sql=sql, params=params)
        def close(self): pass

    class Conn:
        def cursor(self): return Cur()
        def commit(self): seen["committed"] = True
    assert pb.prune_old_news(Conn()) == 2000 and seen["committed"]
    assert seen["params"] == (pb.NEWS_KEEP_DAYS, pb.NEWS_PRUNE_BATCH) and "LIMIT" in seen["sql"] and "DELETE FROM macro_news" in seen["sql"]
    assert pb.NEWS_KEEP_DAYS == ai_digest.KEEP_DAYS


def test_analyst_rating_changes(api):
    """v39: Yahoo's rating history -> rows; what the daily summary sends; the health check."""
    import datetime as _dt
    import pandas as pd
    import analyst_ratings as ar
    utc = _dt.timezone.utc
    # AMD's history as Yahoo gave it on 9.11.2026 (yfinance names: GradeDate index, Firm, ToGrade ...)
    df = pd.DataFrame({"Firm": ["Oppenheimer", "UBS", "RBC Capital", "Old Firm"],
                       "ToGrade": ["Outperform", "Buy", "Sector Perform", "Buy"], "FromGrade": ["Perform", "Buy", "Sector Perform", ""],
                       "Action": ["up", "main", "reit", "init"], "priceTargetAction": ["Announces", "Lowers", "Maintains", "Announces"],
                       "currentPriceTarget": [600.0, 700.0, 540.0, 100.0], "priorPriceTarget": [0.0, 730.0, 540.0, 0.0]},
                      index=pd.DatetimeIndex([_dt.datetime(2026, 11, 9, 10, 17), _dt.datetime(2026, 11, 8, 22, 46),
                                              _dt.datetime(2026, 11, 8, 15, 44), _dt.datetime(2026, 10, 1, 12, 0)], name="GradeDate"))
    rows = ar.rows_from_history("AMD", df, 4e11, _dt.datetime(2026, 11, 6, tzinfo=utc))
    assert [(r["firm"], r["action"], r["pt_action"]) for r in rows] == [("Oppenheimer", "up", "Announces"), ("UBS", "main", "Lowers")]
    up = rows[0]
    assert up["day"] == _dt.date(2026, 11, 9) and up["pt_now"] == 600.0 and up["pt_prior"] is None and up["from_grade"] == "Perform"
    assert rows[1]["day"] == _dt.date(2026, 11, 8)        # 22:46 UTC = 17:46 in New York, the same day
    assert ar.rows_from_history("X", pd.DataFrame(), 1e11, _dt.datetime(2026, 11, 6, tzinfo=utc)) == []
    # v56: 13.11.2026 saved nothing; a run where Yahoo did not answer most companies now ends red, and Monday reaches Friday
    assert ar.run_failed(0.9) and not ar.run_failed(0.1) and not ar.run_failed(0.9, only=["AMD"])
    assert ar.LOOKBACK_DAYS >= 4
    # v57: the answer Actions got stopped at 12.11 while Yahoo had newer changes -> an old answer = the run ends red
    mon = _dt.datetime(2026, 11, 16, 14, 50, tzinfo=utc)                       # Monday 09:50 ET
    assert ar.previous_trading_day(mon) == _dt.date(2026, 11, 13)
    assert ar.answer_is_old(_dt.datetime(2026, 11, 12, 18, 17, tzinfo=utc), mon)
    assert not ar.answer_is_old(_dt.datetime(2026, 11, 13, 21, 32, tzinfo=utc), mon)
    assert ar.answer_is_old(None, mon) and not ar.answer_is_old(None, mon, only=["AMD"])
    assert ar.previous_trading_day(_dt.datetime(2026, 11, 27, 14, 0, tzinfo=utc)) == _dt.date(2026, 11, 25)   # Thanksgiving
    assert ar.latest_grade(df) == _dt.datetime(2026, 11, 9, 10, 17, tzinfo=utc)
    assert ar.latest_grade(pd.DataFrame()) is None
    # v57c: our own request makes the same table as yfinance (NVDA as Yahoo gave it from home, 16.11.2026)
    js = {"quoteSummary": {"result": [{"upgradeDowngradeHistory": {"history": [
        {"epochGradeDate": 1794833564, "firm": "TD Cowen", "toGrade": "Buy", "fromGrade": "Buy", "action": "main",
         "priceTargetAction": "Lowers", "currentPriceTarget": 200.0, "priorPriceTarget": 275.0},
        {"epochGradeDate": 1794580480, "firm": "Truist Securities", "toGrade": "Hold", "fromGrade": "Buy", "action": "down",
         "priceTargetAction": "Lowers", "currentPriceTarget": 174.0, "priorPriceTarget": 0}]}}]}}
    own = ar.history_from_json(js)
    got = ar.rows_from_history("NVDA", own, 4e12, _dt.datetime(2026, 11, 11, tzinfo=utc))
    assert [(r["firm"], r["action"], r["day"]) for r in got] == [("TD Cowen", "main", _dt.date(2026, 11, 16)),
                                                                  ("Truist Securities", "down", _dt.date(2026, 11, 13))]
    assert ar.latest_grade(own) == _dt.datetime(2026, 11, 16, 12, 52, 44, tzinfo=utc)
    assert ar.history_from_json({"quoteSummary": {"result": None}}).empty
    names = [v[0] for v in ar._variants()]
    assert names[0] == "yfinance" and len(set(names)) == len(names)
    # the API: rows and counts
    class Cur:
        def __init__(self): self.n = 0
        def execute(self, *a): self.n += 1
        def fetchone(self): return (True,) if self.n == 1 else (14, 23)
        def fetchall(self): return [("AMD", "Oppenheimer", "up", "Perform", "Outperform", 600.0, None)] * 13
        def close(self): pass

    class Conn:
        def cursor(self): return Cur()
    out = api._analyst_ratings_for_day(Conn(), _dt.date(2026, 11, 9))
    assert len(out["changes"]) == 12 and out["more"] == 2 and out["target_changes"] == 23
    assert out["changes"][0] == {"ticker": "AMD", "firm": "Oppenheimer", "action": "up", "from": "Perform", "to": "Outperform",
                                 "target": 600.0, "prior_target": None}
    # the health check: no change at all on a trading day = a warning; the day before a Monday is Friday
    import data_health as dh
    seen = {}

    class HCur:
        def execute(self, sql, params=None):
            seen["sql"] = sql
            if params is not None:
                seen["params"] = params
        def fetchone(self):
            if "to_regclass" in seen["sql"]:
                return {"ok": True}
            if "'_run'" in seen["sql"]:      # v57: what the last run read
                return {"from_grade": "2026-11-12T18:17+00:00", "to_grade": "asked 480, empty 3, failed 0, kept 0"}
            return {"ratings": 0, "all_changes": 0}
    line = dh.check_ratings(HCur(), _dt.datetime(2026, 11, 9, 12, 30, tzinfo=utc))[0]
    assert line["status"] == "warn" and seen["params"] == (_dt.date(2026, 11, 6),)
    assert "newest change 2026-11-12T18:17" in line["detail"] and "kept 0" in line["detail"]
    dh.check_ratings(HCur(), _dt.datetime(2026, 11, 27, 12, 30, tzinfo=utc))
    assert seen["params"] == (_dt.date(2026, 11, 25),)    # Thanksgiving in between


def test_quality_gate_giant_company_exception():
    """v58 (owner, 16.11.2026, AVGO): companies over $300B pass for structure-repair patterns even below the 150 MA."""
    from types import SimpleNamespace as R
    import pipeline_a_scanner as sc
    avgo = dict(ticker="AVGO", market_cap=1.6e12, rs_rating=35, trend_stage="Stage 4 - Downtrend", atr_pct=2.4)
    assert sc.passes_quality_gate(R(pattern_type="descending_trendline_breakout", **avgo))
    assert sc.passes_quality_gate(R(pattern_type="horizontal_level_bounce", **avgo))
    assert sc.passes_quality_gate(R(pattern_type="double_bottom", **avgo))
    assert not sc.passes_quality_gate(R(pattern_type="descending_trendline_breakout", **avgo), mega=False)
    assert not sc.passes_quality_gate(R(pattern_type="momentum_surge", **avgo))             # not a repair pattern
    small = dict(avgo, market_cap=250e9)                                                      # under the owner's $300B
    assert not sc.passes_quality_gate(R(pattern_type="descending_trendline_breakout", **small))
    # "today's focus" still needs a Stage 2 uptrend
    assert sc.pick_focus([R(pattern_type="double_bottom", swing_score=80, days_to_earnings=None, **avgo)]) == []


def test_company_names_stay_in_english():
    """v38c: Google made "כדור" of Ball and "תלושי ויזה" of "Visa slips"; with " Inc" after the name it keeps it (9.11.2026)."""
    import translation_fixes as tf
    p = tf.prep_en
    assert p("Ball and Progressive beat estimates") == "Ball Inc and Progressive Inc came in above estimates"
    assert p("Visa slips after earnings") == "Visa Inc falls slightly after earnings"
    assert p("First Solar jumps; Public Storage to buy rival") == "First Solar Inc jumps; Public Storage Inc to buy rival"
    assert p("Target Cuts Forecast As Shoppers Pull Back").startswith("Target Inc Cuts")
    assert p("Shares of Carnival") == "Shares of Carnival Inc"
    # a word, not the company; and a name that already has its suffix
    for same in ("Fed's Target Rate Holds", "the inflation target is 2%", "Fed Holds Rate Target", "Ball Corp beats", "an aircraft Carrier sails",
                 "Gaps in the market", "Shell company deals"):
        assert p(same) == same, same
    assert tf.fix_he("מניות Ball Inc ו-Progressive Inc עלו") == "מניות Ball ו-Progressive עלו"
    assert tf.fix_he('אפל ובלוק בע"מ עולות') == "אפל ובלוק עולות"


def test_digest_updates_after_the_close_once():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    import ai_digest as d
    et = ZoneInfo("America/New_York")
    day = _dt.date(2026, 11, 5)
    at = lambda h, m: _dt.datetime(2026, 11, 5, h, m, tzinfo=et)
    assert d.after_close_update_due({"updated_at": at(15, 0)}, day, at(16, 30))
    assert not d.after_close_update_due({"updated_at": at(16, 31)}, day, at(17, 0))     # already done
    assert not d.after_close_update_due({"updated_at": at(13, 0)}, day, at(15, 30))     # still open
    assert not d.after_close_update_due({"updated_at": at(13, 0)}, _dt.date(2026, 11, 4), at(16, 30))


def test_digest_hebrew_check():
    import ai_digest as d
    import json as _json
    en = {"overview": "The S&P 500 fell 4.36%.", "topics": [{"title": "AI selloff", "summary": "Chips fell.", "impact": ""}]}
    good = _json.dumps({"overview": "מדד S&P 500 ירד 4.36% בארה״ב.", "topics": [{"title": "גל מכירות", "summary": "השבבים ירדו.", "impact": ""}]})
    assert d.clean_hebrew(good, en)["topics"][0]["title"] == "גל מכירות"
    plain_quote = good.replace("ארה״ב", 'ארה"ב')                                        # would end the JSON string
    assert "S&P" in d.clean_hebrew(plain_quote, en)["overview"]
    with pytest.raises(ValueError, match="numbers"):
        d.clean_hebrew(good.replace("4.36", "5.0"), en)
    with pytest.raises(ValueError, match="Hebrew"):
        d.clean_hebrew(_json.dumps({"overview": "The S&P fell 4.36%.", "topics": en["topics"]}), en)
    with pytest.raises(ValueError, match="topics"):
        d.clean_hebrew(_json.dumps({"overview": "ירידה", "topics": []}), en)
    glued = _json.dumps({"overview": "מדד S&P 500 ירד 4.36%, ותוצאות בחירות אמצע הקדנציהערערו את וול סטריט.", "topics": [{"title": "גל מכירות", "summary": "השבבים ירדו.", "impact": ""}]})
    assert "הקדנציה ערערו" in d.clean_hebrew(glued, en)["overview"]          # v40e, the digest of 9.11.2026


def test_digest_dates_and_old_stories_v40e():
    """v40e: 10.11.2026 "On the US stock market had not opened yet" (the date was cut, "On" was left); 9.11.2026, S&P +2.35%:
    "midterm election results rattled Wall Street", the week before's story."""
    import ai_digest as d
    assert d._no_dates("On 2026-11-10 the US stock market had not opened yet.") == "The US stock market had not opened yet."
    assert d._no_dates("Oil fell. On 2026-11-10, the market waited.") == "Oil fell. The market waited."
    up = {"same_day": True, "indices": [{"symbol": "^GSPC", "change_pct": 2.35}]}
    assert "rattled Wall Street" in d.contradicts_market("Midterm election results rattled Wall Street.", up)
    assert d.contradicts_market("Midterm election results rattled Wall Street.", dict(up, indices=[{"symbol": "^GSPC", "change_pct": -2.0}])) is None
    assert d.contradicts_market("The rally rocketed past records.", up) is None


def test_digest_health_rule():
    import data_health as dh
    assert dh.digest_problems({"overview_en": "Technology dominated.", "market": _CRASH_MARKET})
    assert dh.digest_problems({"overview_en": "The S&P 500 fell 4.4%.", "why_en": "x", "market": _CRASH_MARKET}) == []
    assert dh.digest_problems({"overview_en": "Technology dominated.", "market": dict(_CRASH_MARKET, move="normal")}) == []
    assert dh.digest_problems({"overview_en": "An older day."}) == []


def test_home_page_of_15_11():
    """v51: what a visitor saw on the home page on Sunday 15.11.2026."""
    import news_links as nl
    import translation_fixes as tf
    import ai_digest as dg
    for junk in ("A 64-year-old farmer plans to lease land to a neighbor on crop share and start Social Security as crop values climb 7%",
                 "Disney's new CEO reportedly plans thousands of cuts ahead of Nov. 17 earnings. Here's what it means for a 72-year-old widow with $150,000 of Disney",
                 "Investors: If a Market Correction Arrives, This Move Will Make or Break Your Portfolio",
                 "Bitcoin vs. Stocks: Are Crypto Really the Best Investment. The Results May Surprise You",
                 "Laird Superfood: 3 Key Options For Long-Term Investors",
                 "Stocks Have Risen the Year After Every Midterm Since 1950. Here's What That Means and Doesn't Mean for You.",
                 "FILE PHOTO: The Federal Reserve Board building is seen in Washington",
                 "SA Asks: What are the most attractive big pharma stocks right now? (LLY:NYSE)",
                 "Bank of America names 5 bullish stocks amid market fears",
                 "Saudi stocks fell 0.66% as inflation eased to 1.7%",
                 "Israel inflation rate drops to 1.1% in October, lowest since April 2021",
                 "Contrary to forecasts, CPI falls, inflation drops and home prices fall across Israel"):
        assert nl.is_unwanted_headline(junk), junk
    for real in ("Fed's Schmid says Fed has more work to do to bring down inflation",
                 "Nvidia reports earnings on Nov. 17. Here's what investors should expect.",
                 "Trump pushes for 'total victory' in Iran conflict",
                 "Israel strikes Iran oil facilities, crude jumps 4%",
                 "SoFi expects about 50% earnings growth in 2027 and says it will not need to raise new capital",
                 "US inflation cools to 2.9% in October"):
        assert not nl.is_unwanted_headline(real), real
    for name in ("The Motley Fool", "24/7 Wall St.", "Zacks Investment Research", "Stocks Down Under"):
        assert nl.is_advice_outlet(name), name
    for name in ("Reuters", "Yahoo Finance", "Bloomberg.com", "Seeking Alpha", ""):
        assert not nl.is_advice_outlet(name), name
    # translations
    assert "Republican midterm election losses" in tf.prep_en("Pence calls Vance 'wrong' on Iran war, GOP midterm losses")
    assert tf.prep_en("What the midterm elections mean") == "What the midterm elections mean"
    assert "Fed meeting protocol" in tf.prep_en("Can Nvidia earnings and Fed minutes spark a Nasdaq rally?")
    assert tf.fix_he("רווחי Nvidia ודקות הפד") == "רווחי Nvidia ופרוטוקול הפד"
    assert tf.half_translated("רווחי Nvidia ו-Anthropic IPO Lead Market Week")
    assert not tf.half_translated("מניות Taiwan Semiconductor Manufacturing Company עלו ב-3%")
    # the summary: a name must survive the Hebrew, another country's market is out, and no session = not bitcoin first
    assert dg.lost_name("Nvidia faces a $109 billion earnings test", "ניידות נמוטרון נותרת במבחן רווחים של 109 מיליארד דולר") == "Nvidia"
    assert dg.lost_name("Nvidia faces a $109 billion earnings test", "Nvidia עומדת במבחן רווחים של 109 מיליארד דולר") is None
    assert dg.lost_name("Walmart prepares for a test as Trump speaks", "וולמארט נערכת למבחן בזמן שטראמפ נואם") is None
    assert dg.lost_name("Gold retreated from record highs", "הזהב נסוג מהשיא") is None
    en = ("Bitcoin rose 1.03%. Nvidia faces a $109 billion earnings test. Gold retreated from record highs. "
          "Saudi stocks fell 0.66% as inflation eased to 1.7%.")
    assert dg._home_first(en, {"same_day": False}) == \
        "Nvidia faces a $109 billion earnings test. Gold retreated from record highs. Bitcoin rose 1.03%."
    assert dg._home_first("Bitcoin rose 1.03%. Nvidia faces a test.", {"same_day": True}) == "Bitcoin rose 1.03%. Nvidia faces a test."
    assert dg._home_first("Saudi stocks fell 0.66%.", None) == "Saudi stocks fell 0.66%."        # nothing else to say: kept
    ranked = dg.rank_items([{"id": 1, "title": "Fed holds rates as inflation cools", "tag": "ריבית", "source": "Reuters"},
                            {"id": 2, "title": "Vanguard economists say inflation may be better than it looks", "tag": "אינפלציה",
                             "source": "The Motley Fool"}])
    assert [i["id"] for i in ranked] == [1]


def test_news_of_the_live_feed_of_11_11(api):
    """v47: the 100 headlines the site held on 11.11.2026 (tests/fixtures/news_2026_11_11.json). What must not be shown,
    which outlets the geopolitics feed takes, and the same story from several outlets folded into one line."""
    import json, pathlib
    import datetime as _dt
    import news_links as nl
    import translation_fixes as tf
    for junk in ("Poland passes bill targeting crypto assets under sanctions",
                 "Lukashenka calls for dialogue with Poland, criticizes sanctions",
                 "France threatens sanctions against EPR organisations over low packaging fees",
                 "Moldova to fully join EU sanctions against Russia and Belarus",
                 "+102%, +40%: As CoreWeave lifts tech, earnings beats power these AI-picked winners",
                 "We told you first: these AI-picked small-cap stocks are up +49% and +28% MTD",
                 "Top 3 Real Estate Stocks That Could Blast Off In Q4",
                 "New Strong Buy Stocks for November 11th",
                 "Crude Oil Dec 26 (CL=F) Stock Price, News, Quote & History",
                 "AM Markets Need to Know: Fed faces inflation test, Anthropic IPO demand surges, and more",
                 "Lowe's Companies, Inc. to Host Third Quarter 2026 Earnings Conference Call on Nov. 18",
                 "How Investors May Respond To Ross Stores Stock Ahead Of Earnings Growth Expectations",
                 "Irish annual CPI inflation rises to 4.3% in October"):
        assert nl.is_unwanted_headline(junk), junk
    for real in ("US Congress Urged To Tighten Russia Sanctions, Speed Military Aid",
                 "U.S. Sanctions Iran's oil tankers as talks stall",
                 "Oil prices rise amid concerns of shipping disruptions in the Strait of Hormuz",
                 "CoreWeave stock pops 15% after outlook raise eases AI slowdown concerns",
                 "German inflation eases to 2.8% in October as expected",
                 "Goldman adds March rate hike to Fed forecast after another hot inflation read"):
        assert not nl.is_unwanted_headline(real), real

    for name in ("Reuters", "Bloomberg.com", "WSJ", "AP News", "Yahoo Finance Australia", "The Jerusalem Post", "NPR",
                 "The New York Times", "Financial Times", "Semafor", "Benzinga"):
        assert nl.is_trusted_outlet(name), name
    for name in ("تسنیم", "Belsat", "Palestine Chronicle", "KBOI", "Plastics News", "Anadolu Ajansı", "The Times of India",
                 "Riviera Radio", "Middle East Eye", "", "Aptitude News"):
        assert not nl.is_trusted_outlet(name), name

    # the translation that said the opposite: "pops 15%" came out as "צונחת"
    assert tf.prep_en("CoreWeave stock pops 15% after outlook raise eases AI slowdown concerns").startswith("CoreWeave stock jumps 15%")
    assert tf.prep_en("Pop star sells her catalog") == "Pop star sells her catalog"
    assert "senior Democrat in the Senate" in tf.prep_en("Top Senate Dem says 'looking at all options' to end Iran war")
    assert "Democrats Reveal" in tf.prep_en("Dems Reveal Stunning Iran War Plan To Counter Trump Action")

    assert nl.story_move("Strait of Hormuz Shipping Risks Push Oil Prices Up From Two-Month Lows") == ("oil", "up")
    assert nl.story_move("Oil Rebounds After Two-Day Drop on U.S.-Iran Conflict Uncertainty") == ("oil", "up")
    assert nl.story_move("Gold steadies after inflation-driven selloff amid Fed hike bets") is None
    assert nl.story_move("VLE: Higher oil prices lifted Q3 earnings and cash flow") is None

    rows = json.loads(pathlib.Path(__file__).with_name("fixtures").joinpath("news_2026_11_11.json").read_text(encoding="utf-8-sig"))
    rows = [r for r in rows if not nl.is_unwanted_headline(r["en"])]
    titles = [r["en"] for r in rows]
    groups = nl.story_groups(titles)
    where = {titles[i]: n for n, g in enumerate(groups) for i in g}

    def together(*parts):
        found = {where[t] for t in titles if any(p in t for p in parts)}
        return len(found) == 1

    assert together("Oil prices rise as investors monitor", "Oil Prices Rise as Iran Peace Hopes", "Push Oil Prices Up",
                    "Oil prices rose after Iran", "Oil Prices Rise More Than 1%", "Oil prices rise amid concerns",
                    "Oil Rebounds After Two-Day Drop")
    assert together("Nvidia shares rebound 4%", "Nvidia shares rebound 5%")
    assert together("Dollar rises toward two-week high", "US dollar nears two-week high")
    # different stories that share the words of the day
    assert not together("Trump, Netanyahu clash", "Trump says US won Iran war")
    assert not together("Gold steadies after", "Falling US Bond Futures")
    assert not together("Iran warns of domestic gas shortages", "Oil prices rise amid concerns")
    assert not together("European stocks near record high", "Nvidia shares rebound 4%")
    assert len(groups) < len(titles) - 8
    # opposite directions never meet; and far apart in time is another day's story
    assert len(nl.story_groups(["Oil prices rise as Hormuz shipping risks grow", "Oil prices fall as Hormuz shipping risks ease"])) == 2
    two = ["Oil prices rise as Hormuz shipping risks grow", "Oil prices rise as Hormuz shipping risks persist"]
    assert len(nl.story_groups(two, [1.0, 2.0])) == 1 and len(nl.story_groups(two, [1.0, 60.0])) == 2

    # the server: one row per story, the most established outlet leads, one line per other outlet
    now = _dt.datetime(2026, 11, 11, 12, 0, tzinfo=_dt.timezone.utc)

    def row(i, src, title, mins):
        return {"id": i, "source_name": src, "summary_en": title, "summary_he": "ע", "category_tag": "אנרגיה",
                "source_url": f"https://news.google.com/{i}", "article_url": "" if i == 2 else f"https://x.example/{i}",
                "timestamp": now - _dt.timedelta(minutes=mins)}
    folded = api._fold_stories([row(1, "KBOI", "Strait of Hormuz Shipping Risks Push Oil Prices Up From Two-Month Lows", 5),
                                row(2, "Benzinga", "Oil Prices Rise as Iran Peace Hopes Clash With Strait of Hormuz Tensions", 10),
                                row(3, "CNBC", "Nvidia shares rebound 5% as Huang faces scrutiny ahead of earnings", 20),
                                row(4, "Reuters", "Oil prices rise amid concerns of shipping disruptions in the Strait of Hormuz", 30),
                                row(5, "CNBC", "Nvidia shares rebound 4% as Huang faces scrutiny ahead of earnings", 40),
                                row(6, "KBOI", "Oil prices rise as investors monitor shipping disruption in the Strait of Hormuz", 50)], now)
    assert [s["id"] for s in folded] == [4, 3]                       # newest story first; Reuters leads the oil story
    oil, nvda = folded
    assert [m["source_name"] for m in oil["more_sources"]] == ["KBOI", "Benzinga"] and oil["story_ids"] == [1, 2, 4, 6]
    assert oil["more_sources"][0]["url"] == "https://x.example/1" and oil["more_sources"][1]["url"] == "https://news.google.com/2"
    assert nvda["more_sources"] == [] and nvda["story_ids"] == [3, 5]   # the same outlet twice = its newest headline only
    # v56: a row saved before v51 from an advice outlet is not shown (16.11: Motley Fool under "more sources")
    kept = api._feed_clean([row(7, "The Motley Fool", "Tariffs and inflation: what the data says about prices", 5),
                            row(8, "MarketWatch", "Tariffs and inflation: what the data says about prices now", 6)], set())
    assert [r["id"] for r in kept] == [8]
    assert api._feed_clean([{"summary_en": "Nvidia stock: 3 reasons it can rally", "publisher": "The Motley Fool"}],
                           set(), promo_only=True) == []


def test_headline_filters_of_this_round():
    from news_links import is_unwanted_headline as bad
    assert bad("Philippine Inflation Surges to 7.8%, Boosting Case for Rate Hike")
    assert bad("Magal: Q3 Earnings Snapshot") and bad("Under Armour: Fiscal Q2 Earnings Snapshot")
    assert bad("Aflac Q3 Earnings Call Highlights") and bad("What Wendy's (WEN) Said on Its Q3 Earnings Call")
    assert not bad("ConocoPhillips Q3 Earnings Beat Estimates on Higher Commodity Prices")
    assert not bad("Asian stocks fall as India and Indonesia follow Wall Street lower")


def test_news_quality_of_the_live_feed_of_10_11():
    """v44: headlines of the live feed of 10.11.2026 — what should not be shown, which category each belongs to, and
    the translations that came out wrong."""
    from news_links import is_unwanted_headline as bad
    import pipeline_b_news_aggregator as pb
    import translation_fixes as tf
    for junk in ("As Crude Oil Prices Retreat, Sell Soybean Oil Now",
                 "Home Depot (HD) Reports Next Week: Wall Street Expects Earnings Growth",
                 "Earnings Preview: Spire (SR) Q4 Earnings Expected to Decline",
                 "Brazil's central bank signals high bar for rate hikes despite inflation risks",
                 "Poland's Maslowska sees growing case for January rate hike",
                 "Canadian stock market today: TSX futures drift higher in critical US inflation test",
                 "TSX futures muted ahead of US inflation data, oil dips",
                 "Hot inflation report puts Fed on track for December rate hike; The best banks to work for in 2026—and more in Bankers' Hours",
                 "Nasdaq AI chip stocks have crashed 50%+! Is this a golden buying opportunity?"):
        assert bad(junk), junk
    for real in ("S&P 500 futures fall sharply after October inflation data",
                 "Investors sell stocks now that inflation is back",
                 "European Stocks Approach Record Highs as Oil Prices Fall and Technology Shares Advance: DAX, CAC, FTSE100",
                 "Brazil's real slides as the dollar jumps on US inflation",
                 "Sea Limited Shares Fall Nearly 9% After Q3 Earnings Miss Despite 43% Revenue Growth"):
        assert not bad(real), real

    def tag(title, summary=""):
        return pb.classify_headline(title, summary)["tag"]
    assert tag("US stocks drift lower after a discouraging update on inflation") == "שוק"
    assert tag("European Stocks Approach Record Highs as Oil Prices Fall and Technology Shares Advance: DAX, CAC, FTSE100") == "שוק"
    assert tag("S&P 500 futures fall sharply after October inflation data") == "שוק"
    assert tag("Core CPI rose 0.3% in October versus 0.2% expected; stocks slide") == "CPI"
    assert tag("Bitcoin Falls Below $74K After Hot US Inflation Fuels Fed Rate Hike Bets – Solana, Ethereum, XRP Hit Harder") == "קריפטו"
    assert tag("Gold, Silver Fall as Rate-Hike Betting Jumps on Strong Inflation Data") == "סחורות"
    assert tag("Trump Said Resuming the Iran war is 'Highly Unlikely.' Oil Prices Fell Below a Key Threshold.") == "גאופוליטי"
    # the summary's passing words do not outvote the headline
    assert tag("Trump says Iran war could end 'pretty soon' after Qatar talks",
               "Tech stocks and chip shares rallied. Apple, Microsoft and Nvidia stock rose as tech investors cheered.") == "גאופוליטי"
    assert tag("Rivian CEO says new R2 SUV, autonomy make 2027 an ‘inflection year’ as EV maker marks five years since IPO",
               "Rivian stock rose after earnings.") != "IPO"
    assert tag("Cerebras files for IPO as AI chip demand soars") == "IPO"
    # a headline with no market word of its own still does not get in through "stocks" alone
    assert pb.classify_headline("Grocery stocks run low before the storm", "") is None

    p = tf.prep_en
    assert p("U.S. equities drift lower after a hotter CPI print") == "U.S. equities drift lower after a hotter CPI reading"
    assert p("Gold, Silver Fall as Rate-Hike Betting Jumps") == "Gold, Silver Fall as interest rate hike Betting Jumps"
    assert p("an interest rate hike in December") == "an interest rate hike in December"
    assert p("Hot US Inflation Fuels Fed Rate Hike Bets") == "Hot US Inflation increase Fed interest rate hike Bets"
    assert p("US CPI cements December rate hike bets") == "US CPI strengthens December interest rate hike bets"
    assert p("Core CPI rose; stocks slide") == "Core CPI rose; stocks fall"
    assert p("jet fuel prices raise fears of inflation") == "jet fuel prices raise fears of inflation"
    assert p("fossil fuels stay in demand despite concerns") == "fossil fuels stay in demand despite concerns"
    assert tf.fix_he("ההימורים על העלאת שיעור קופצים") == "ההימורים על העלאת ריבית קופצים"
    assert tf.half_translated('טראמפ אמר שחידוש המלחמה באיראן הוא "לא סביר ביותר". Oil Prices Fell Below a Key Threshold.')
    assert not tf.half_translated("מניות Taiwan Semiconductor Manufacturing Company עלו ב-3%")
    assert not tf.half_translated("ביטקוין יורד מתחת ל-74 אלף דולר - סולנה, Ethereum, XRP נפגעו")
    assert not tf.half_translated("Stocks fell as the Fed held rates")          # no Hebrew at all: a failed translation, handled elsewhere


def test_ai_headline_translation_is_checked_before_it_is_used(monkeypatch):
    """v45 (trial): an AI translation that drops or adds a number, leaves English in, or is not Hebrew is not used."""
    import pipeline_b_news_aggregator as pb
    ok = pb.ai_translation_ok
    en = "Sea Limited Shares Fall Nearly 9% After Q3 Earnings Miss Despite 43% Revenue Growth"
    assert ok(en, "מניית Sea Limited יורדת בכמעט 9% אחרי דוחות הרבעון השלישי, למרות צמיחה של 43% בהכנסות") is None
    assert ok(en, "מניית Sea Limited יורדת אחרי דוחות הרבעון השלישי, למרות צמיחה של 43% בהכנסות").startswith("number missing: 9")
    assert ok(en, "מניית Sea Limited יורדת בכמעט 9% ב-2026 אחרי הדוחות, למרות צמיחה של 43% בהכנסות").startswith("number added: 2026")
    assert ok(en, "Sea Limited shares fall nearly 9% despite 43% growth") == "no Hebrew"
    assert ok(en, "מניית Sea Limited יורדת 9% after the earnings miss despite 43% growth") == "part left in English"
    assert ok(en, "ירידה 9% 43%") == "length"
    assert ok("Bitcoin Falls Below $74,000", "הביטקוין יורד מתחת ל-74000 דולר") is None

    import llm_provider
    titles = [f"Stocks rise {i}% as the Fed holds rates" for i in range(1, 4)]
    assert ok("Jobless claims near the lowest since the 1960s", "תביעות האבטלה קרובות לשפל מאז שנות ה-60") is None
    assert ok("Inflation forecast to stay high into 2030s", "האינפלציה צפויה להישאר גבוהה עד שנות ה-2030") is None
    assert ok("Jobless claims near the lowest since the 1960s", "תביעות האבטלה קרובות לשפל של עשרות שנים").startswith("number missing")
    # second trial, 11.11.2026
    assert ok("BOE Should Raise Key Rate to Around 5%, U.K. Economic Think Tank Says",
              "בנק ישראל צריך להעלות את הריבית המרכזית לכ-5%, אומר מכון מחקר כלכלי בבריטניה") == "wrong central bank"
    assert ok("Bank of Israel holds rates at 4%", "בנק ישראל מותיר את הריבית על 4%") is None
    assert ok("Infineon expects an AI infrastructure boom to drive data center revenue higher",
              "אינפיניון צופה עלייה בהכנסות מרכזי מידע על רקע boom של תשתיות AI").startswith("word left in English: boom")
    assert ok("Stocks gain as CoreWeave reignites AI trade", "המניות עולות כש-CoreWeave מציתה מחדש את המסחר ב-AI") is None
    from news_links import is_unwanted_headline
    assert is_unwanted_headline("Tech Sell-Off: Two AI Stocks That Are Screaming Buys Right Now")
    # numbered lines, because Hebrew abbreviations carry a double quote that broke JSON
    answers = iter(['1. המניות בארה"ב עולות 1% כשהפד מותיר את הריבית\n2. המניות עולות כשהפד מותיר את הריבית\n3) המניות עולות 3% כשהפד מותיר את הריבית'])
    monkeypatch.setattr(llm_provider, "chat", lambda messages, **kw: (kw["validate"](a := next(answers)), (a, "test-model"))[1])
    got = pb.ai_translate_headlines(titles)
    assert got[0] and got[2] and got[1] is None            # the second lost its number
    monkeypatch.setattr(llm_provider, "chat", lambda messages, **kw: (kw["validate"]("1. רק שורה אחת"), ("x", "m"))[1])
    assert pb.ai_translate_headlines(titles) == [None, None, None]        # a line is missing: the whole batch stays with Google
    monkeypatch.setenv("LLM_MODELS", "small-model")
    seen = []
    monkeypatch.setattr(llm_provider, "chat", lambda messages, **kw: (seen.append(os.environ["LLM_MODELS"]), ("1. א\n2. ב\n3. ג", "m"))[1])
    pb.ai_translate_headlines(titles, "large-model")
    assert seen == ["large-model"] and os.environ["LLM_MODELS"] == "small-model"

    # v45b: a provider that keeps refusing is left out for the rest of the run
    pb._provider_fails.clear()
    pb._provider_fails["Google gtx"] = pb.PROVIDER_SKIP_AFTER
    import urllib.request
    asked = []

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'["\xd7\xa9\xd7\x9c\xd7\x95\xd7\x9d"]'

    def fake_open(req, timeout=0):
        asked.append(req.full_url)
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda s: None)
    assert pb.translate_to_hebrew("Hello") == "שלום"
    assert len(asked) == 1 and "clients5" in asked[0]
    pb._provider_fails.clear()

    def broken(messages, **kw):
        raise RuntimeError("all models failed")
    monkeypatch.setattr(llm_provider, "chat", broken)
    assert pb.ai_translate_headlines(titles) == [None, None, None]


def test_key_events_of_this_round(api):
    of = api._key_event_of
    assert of("Stray Drones Spawn a Growing Area of the Insurance Market") is None
    assert of("A prolonged war in Iran is the market's real black swan, says this strategist") is None
    assert of("Dollar edges up as oil prices bounce, payrolls eyed") is None
    assert of("What CPI Card Group (PMTS) said on its earnings call") is None
    assert of("He Sees the Nasdaq Plunging 5.5% at 63. Claiming Social Security to Avoid Selling Stocks Could Lock In a Smaller Check for Life") is None
    assert of("Nasdaq AI chip stocks have crashed 50%+! Is this a golden buying opportunity?") is None
    assert of("US payrolls rose by 150,000 in October")[0] == "jobs"
    assert of("Oil jumps as missiles hit tankers in the Strait of Hormuz")[0].startswith("geo")
    crash = of("A brutal sell-off sends Wall Street reeling, and the S&P 500 tumbles 4.4%")
    assert crash[0] == "market" and crash[1] > of("ECB holds rates steady, Lagarde says")[1]


def test_demo_stop_is_not_closer_than_one_atr(scanner):
    """v41: a support 1% under the entry was the stop, and ordinary daily movement closed the trade the next day
    (ANET 4.11.2026: entry $178.48, support $176.77)."""
    stop, target = scanner.sim_levels(178.48, 176.77, [188.99], 6.0)
    assert stop == round(178.48 - 1.5 * 6.0, 4) and target >= 178.48 + (178.48 - stop)
    assert scanner.sim_levels(100.0, 92.0, [], 3.0)[0] == 92.0            # a support 8% away (more than 1 ATR) is the stop
    assert scanner.sim_levels(100.0, 99.0, [], None)[0] == 93.0           # no ATR: closer than 2% is not a stop
    assert scanner.sim_levels(100.0, 97.0, [], None)[0] == 97.0
    assert scanner.sim_levels(100.0, 99.0, [], float("nan"))[0] == 93.0


def test_scan_score_follows_rs_not_volume(scanner):
    """v42: the first 8 results of 9.11.2026 were all exactly 100 — volume added up to +20 (measured in v27: no effect
    on the outcome) and RS one step of +8. Real rows of that scan."""
    of = scanner.swing_score_of
    s2 = "Stage 2 - Uptrend"
    sgry = of("52w_high", 168, s2, 98, None, "Breaking above 52-week high")
    kymr = of("52w_high", 32, s2, 97, None, "Breaking above 52-week high")
    jhx = of("bull_flag", 330, s2, 88, None, "Bull Flag breakout (pole +12.3%)")
    assert (sgry, kymr, jhx) == (98, 96, 88)                       # were 100, 100, 100
    assert of("52w_high", 0, s2, 99, None) > of("52w_high", 500, s2, 90, None)      # RS outweighs any volume
    assert of("52w_high", 100, s2, 90, None) - of("52w_high", 0, s2, 90, None) == 2
    # the parts that did not change
    res = "Breaking above horizontal resistance at $31.03 (tested {}x)"
    assert of("horizontal_resistance_breakout", 0, s2, 90, None, res.format(4)) - of("horizontal_resistance_breakout", 0, s2, 90, None, res.format(2)) == 4
    assert of("horizontal_resistance_breakout", 0, s2, 90, None, res.format(9)) - of("horizontal_resistance_breakout", 0, s2, 90, None, res.format(2)) == 8
    assert of("52w_high", 0, s2, 90, 3) == of("52w_high", 0, s2, 90, None) - 12     # a report within a week
    assert of("52w_high", 0, s2, 90, 30) == of("52w_high", 0, s2, 90, None)
    assert of("52w_high", 0, "Stage 4 - Downtrend", 90, None) == of("52w_high", 0, None, 90, None) - 20
    held = "Back above the 150-day MA (~$50) — second close above it"
    assert of("ma150_breakout", 300, s2, 80, None, held) == of("ma150_breakout", 0, s2, 80, None, held)
    assert of("ma150_support_bounce", 300, s2, 80, None) == of("ma150_support_bounce", 0, s2, 80, None)
    assert of("momentum_surge", 900, s2, 99, None) < of("horizontal_level_bounce", 0, None, 70, None)   # indicators stay below structure
    # RS 40-69 neither adds nor takes (a 150-MA reclaim has a middling RS by nature); under 40 it takes, point by point
    assert of("ma150_breakout", 0, None, 46, None) == of("ma150_breakout", 0, None, 69, None) == 73
    assert of("ma150_breakout", 0, None, 38, None) > of("ma150_breakout", 0, None, 30, None) > of("ma150_breakout", 0, None, 20, None) == of("ma150_breakout", 0, None, 0, None) == 58
    assert of("52w_high", None, None, None, None) == 78 and of("52w_high", float("nan"), None, None, None) == 78
    assert 1 <= of("rsi_bounce", 0, "Stage 4 - Downtrend", 1, 2) <= of("cup_handle", 999, s2, 99, None) <= 100


def test_every_pattern_of_the_scan_has_a_base_score(scanner):
    import re
    src = (ROOT / "pipeline_a_scanner.py").read_text(encoding="utf-8")
    names = set(re.findall(r'^\s+pattern_type = "(\w+)"', src, re.M))
    assert len(names) >= 15 and not names - set(scanner.SCORE_PATTERN_BASE)


def test_today_line_comes_from_the_digest_kept_in_memory(api, monkeypatch):
    """v43: the home page's "market today" reads the daily digest's answer — no database call of its own; a digest of
    an earlier day is marked as such, and no overview = nothing to show."""
    digest = {"day": "2026-11-10", "is_today": True, "ai_summary": {
        "overview_he": "עברית", "overview_en": "English", "generated_at": "2026-11-10T14:00:00+00:00",
        "market": {"status": "pre_open", "day": "2026-11-10", "session_day": "2026-11-09"}}}
    calls = []
    monkeypatch.setattr(api, "get_daily_digest", lambda *a, **kw: calls.append((a, kw)) or digest)
    line = api.get_today_line.__wrapped__()
    assert calls == [((None,), {})]                 # v50: exactly how get_daily_digest_endpoint calls it = the same kept answer
    assert line["available"] and line["is_today"] and line["overview_he"] == "עברית" and line["status"] == "pre_open"
    digest["is_today"] = False
    assert api.get_today_line.__wrapped__()["is_today"] is False
    digest["ai_summary"] = None
    assert api.get_today_line.__wrapped__() == {"available": False, "day": "2026-11-10"}

    def down(*a, **kw):
        raise RuntimeError("no database")
    monkeypatch.setattr(api, "get_daily_digest", down)
    assert api.get_today_line.__wrapped__() == {"available": False}


# ---- v50 (security review 15.11.2026): nobody outside can use up the database's monthly limits ----
def test_public_requests_stop_at_the_daily_database_budget(api, monkeypatch):
    """Past the day's limit a visitor's request gets no connection (503 / the answer in memory); a signed-in person's own
    data, the scheduler and the alert loop always do."""
    import db_meter
    import data_health
    assert db_meter.CONNECT_BYTES == data_health.CONN_OVERHEAD_BYTES
    assert issubclass(api.DatabaseBudgetReached, api.psycopg2.OperationalError)        # -> the 503 handler
    for src in ("server /api/macro-news", "server /api/daily-digest", "server", "server /api/fundamentals"):
        assert api._budget_allows(src, 19.9, 20) and not api._budget_allows(src, 20.0, 20)
    for src in ("server /api/watchlist", "server /api/watchlist-setups", "server /api/trades", "server /api/alerts",
                "server /api/push", "server /api/auth", "server scheduler", "server alerts"):
        assert api._budget_allows(src, 500, 20)

    opened = []
    monkeypatch.setattr(api.psycopg2, "connect", lambda *a, **k: opened.append(1) or "conn")
    monkeypatch.setattr(api, "PUBLIC_DB_BUDGET_MB", 20.0)
    monkeypatch.setattr(api, "_public_db_mb_today", lambda: 25.0)
    token = db_meter.set_source("server /api/macro-news")
    try:
        with pytest.raises(api.DatabaseBudgetReached):
            api.get_conn()
        assert not opened
    finally:
        db_meter.reset_source(token)
    token = db_meter.set_source("server /api/trades")
    try:
        assert api.get_conn() == "conn" and opened == [1]
    finally:
        db_meter.reset_source(token)


# ---- v64 (security review 18.11.2026): one signed-in account cannot use up the database or bring the server down ----
def test_signed_in_requests_have_their_own_limits(api, monkeypatch):
    import db_meter
    from fastapi import HTTPException
    for src in ("server /api/watchlist", "server /api/trades", "server /api/alerts", "server /api/push", "server /api/auth"):
        assert api._budget_allows(src, 999, 20, 29.9, 30) and not api._budget_allows(src, 0, 20, 30.0, 30)
    for src in ("server scheduler", "server alerts"):
        assert api._budget_allows(src, 999, 20, 999, 30)
    assert api._budget_allows("server /api/stocks", 1, 20, 999, 30)      # the visitors' budget is its own

    saved = dict(db_meter._today)
    db_meter._today.update(day=None, bytes={})
    try:
        db_meter.add("server /api/stocks", nbytes=1_000_000)
        db_meter.add("server /api/trades", nbytes=2_000_000)
        db_meter.add("server /api/watchlist", nbytes=500_000)
        db_meter.add("server scheduler", nbytes=9_000_000)
        assert api._personal_db_mb_today() == pytest.approx(2.5)
        assert api._public_db_mb_today() == pytest.approx(1.0)
        opened = []
        monkeypatch.setattr(api.psycopg2, "connect", lambda *a, **k: opened.append(1) or "conn")
        monkeypatch.setattr(api, "PERSONAL_DB_BUDGET_MB", 2.0)
        token = db_meter.set_source("server /api/watchlist")
        try:
            with pytest.raises(api.DatabaseBudgetReached):
                api.get_conn()
        finally:
            db_meter.reset_source(token)
        token = db_meter.set_source("server scheduler")
        try:
            assert api.get_conn() == "conn"
        finally:
            db_meter.reset_source(token)
    finally:
        db_meter._today.clear()
        db_meter._today.update(saved)

    # requests per account: a minute and a day
    monkeypatch.setattr(api, "_rl_hits", {})
    monkeypatch.setattr(api, "_user_day", {"day": None, "n": {}})
    monkeypatch.setattr(api, "USER_REQ_PER_MIN", 3)
    for _ in range(3):
        api._check_user_rate(5)
    with pytest.raises(HTTPException) as e:
        api._check_user_rate(5)
    assert e.value.status_code == 429
    api._check_user_rate(6)                                          # another account is not affected
    monkeypatch.setattr(api, "USER_REQ_PER_MIN", 1000)
    monkeypatch.setattr(api, "USER_REQ_PER_DAY", 6)
    with pytest.raises(HTTPException) as e:
        for _ in range(10):
            api._check_user_rate(6)
    assert "daily" in e.value.detail


def test_watchlist_answer_is_built_once_per_person(api, monkeypatch):
    monkeypatch.setattr(api, "_user_answers", {})
    monkeypatch.setattr(api, "_user_locks", {})
    builds = []
    build = lambda: builds.append(1) or ["row"]                      # noqa: E731
    assert api._per_user_answer("watchlist", 1, build) == ["row"]
    assert api._per_user_answer("watchlist", 1, build) == ["row"] and len(builds) == 1     # kept, not built again
    api._per_user_answer("watchlist", 2, build)
    assert len(builds) == 2                                          # each person their own
    api._forget_user_answers(1)                                      # a stock added or removed
    api._per_user_answer("watchlist", 1, build)
    assert len(builds) == 3
    assert api._user_locks[("watchlist", 1)][1] == 0                 # nobody left waiting
    from fastapi import HTTPException
    api._user_locks[("watchlist", 9)] = [api.threading.Lock(), api._USER_MAX_WAITING]
    with pytest.raises(HTTPException) as e:
        api._per_user_answer("watchlist", 9, build)
    assert e.value.status_code == 429


def test_rate_limiter_does_not_forget_everybody(api, monkeypatch):
    """v64: past _RL_MAX_KEYS only quiet counters go; an address still sending keeps its count."""
    now = api.time.time()
    hits = {("default", f"ip{i}"): api.deque([now - 300]) for i in range(30)}
    hits[("default", "busy")] = api.deque([now] * 5)
    monkeypatch.setattr(api, "_rl_hits", hits)
    monkeypatch.setattr(api, "_RL_MAX_KEYS", 10)
    monkeypatch.setattr(api, "_rl_swept", {"at": 0.0})
    assert api._rate_limited("default", "busy", 6, 60) is False      # the 6th request: allowed
    assert ("default", "busy") in api._rl_hits and len(api._rl_hits[("default", "busy")]) == 6
    assert len(api._rl_hits) == 1
    assert api._rate_limited("default", "busy", 6, 60) is True       # the 7th: still counted, refused


# ---- v65 (18.11.2026): splits Yahoo has not applied yet, and bounces that closed at the day's low ----
def test_split_not_applied_by_yahoo_yet_is_corrected():
    """EMBJ split 4:1 on 17.11.2026; Yahoo still sent 78.73 for 13.11 next to 18.90 for 17.11 (the site: "-75.99%")."""
    from price_splits import fix_unadjusted_splits, split_not_applied
    idx = pd.to_datetime(["2026-11-12", "2026-11-13", "2026-11-16", "2026-11-17"])
    raw = pd.DataFrame({"Open": [79.0, 79.5, None, 19.6], "High": [80.0, 80.0, None, 19.9], "Low": [78.0, 78.5, None, 18.5],
                        "Close": [79.48, 78.73, None, 18.90], "Volume": [1000, 1200, 0, 5000],
                        "Stock Splits": [0.0, 0.0, 4.0, 0.0]}, index=idx)
    fixed = fix_unadjusted_splits(raw)
    assert fixed["Close"].iloc[1] == pytest.approx(78.73 / 4) and fixed["Volume"].iloc[1] == 4800
    closes = fixed["Close"].dropna()
    assert -6 < (closes.iloc[-1] / closes.iloc[-2] - 1) * 100 < -3                     # about -4%, not -76%
    assert raw["Close"].iloc[1] == 78.73                                                # the input is not changed
    # Yahoo already adjusted the older rows: nothing changes
    done = raw.copy()
    for c in ("Open", "High", "Low", "Close"):
        done.loc[done.index[:3], c] = done[c].iloc[:3] / 4
    assert fix_unadjusted_splits(done)["Close"].iloc[1] == pytest.approx(78.73 / 4)
    # a reverse split (1 for 10 = 0.1): $2 -> $20
    assert split_not_applied(2.0, 20.5, 0.1) and not split_not_applied(20.0, 20.5, 0.1)
    no_split = raw.drop(columns=["Stock Splits"])
    assert fix_unadjusted_splits(no_split) is no_split


def test_a_bounce_closes_off_the_days_low(scanner, api):
    """v65: 17.11.2026, a falling market, 118 bounce scans — most closed near the day's low. The close has to be at
    least 40% up the day's range; ZS 13.11 (0.497, the owner's example) still counts."""
    assert scanner.BOUNCE_MIN_CLOSE_POS == api._BOUNCE_MIN_CLOSE_POS == 0.4
    zs = _hist("ZS")
    assert scanner.closed_off_the_low(zs) and scanner.detect_horizontal_level_bounce(zs)
    low_close = zs.copy()
    low_close.iloc[-1, low_close.columns.get_loc("Close")] = float(zs["Low"].iloc[-1]) + 0.385 * (
        float(zs["High"].iloc[-1]) - float(zs["Low"].iloc[-1]))
    assert float(low_close["Close"].iloc[-1]) > 191.25                                  # still above the level
    assert not scanner.closed_off_the_low(low_close) and not api._closed_off_the_low(low_close)
    assert scanner.detect_horizontal_level_bounce(low_close) is None
    assert api._scanner_rule_level_bounce(low_close) is None
    for f in (scanner.detect_ma150_support_bounce, scanner.detect_ascending_trendline_support,
              api._scanner_rule_ma150_bounce, api._scanner_rule_asc_trendline):
        assert f(low_close) is None


def test_db_meter_counts_the_day_by_source():
    import db_meter
    saved = dict(db_meter._today)
    db_meter._today.update(day=None, bytes={})
    try:
        db_meter.add("server /api/stocks", connections=1, nbytes=1000)
        db_meter.add("server /api/trades", nbytes=700)
        db_meter.add("server scheduler", connections=2)
        db_meter.take_pending()                              # a write to db_usage does not empty the day's count
        assert db_meter.bytes_today() == 1000 + 700 + 3 * db_meter.CONNECT_BYTES
        assert db_meter.bytes_today(skip=("server scheduler", "server /api/trades")) == 1000 + db_meter.CONNECT_BYTES
        db_meter._today["day"] = None                        # a new day starts from zero
        assert db_meter.bytes_today() == 0
    finally:
        db_meter._today.update(saved)
        db_meter.take_pending()


def test_public_endpoints_take_only_the_sites_own_values(api, monkeypatch):
    """Each free parameter was a way to make the server read the database again and again."""
    import re
    assert api._nearest_choice(77, (70, 75, 80)) == 75 and api._nearest_choice(1e9, (0, 100000)) == 100000
    assert api._nearest_choice(0.3, api.INSIDER_BUY_MIN_VALUES) == 0

    # the demo portfolio's buttons in the page = the server's list
    page = ROOT / "index.html"
    if page.exists():                                        # not among the files of the browser check (tests/local_check)
        src = page.read_text(encoding="utf-8")
        for key, choices in api.DEMO_CHOICES.items():
            m = re.search(r"chips\('%s',\[([\d,]+)\]" % key, src)
            assert m and tuple(int(x) for x in m.group(1).split(",")) == choices, key

    calls = []
    monkeypatch.setattr(api, "get_demo_portfolio", lambda **kw: calls.append(kw) or "demo")
    assert api.get_demo_portfolio_endpoint(min_score=77, top_per_day=10, amount=99999, capital=1) == "demo"
    assert calls == [{"min_score": 75, "top_per_day": 5, "amount": 5000, "capital": 10000}]

    calls = []
    monkeypatch.setattr(api, "get_insider_buys", lambda *a: calls.append(a) or "buys")
    api.get_insider_buys_endpoint(days=31, min_value=123456.789)
    api.get_insider_buys_endpoint(days=30, min_value=100000)
    assert calls == [(30, 100000), (30, 100000)]

    calls = []
    monkeypatch.setattr(api, "search_macro_news", lambda q: calls.append(q) or [])
    api.search_macro_news_endpoint(q="  Fed   RATE ")
    assert calls == ["fed rate"]

    monkeypatch.setattr(api, "get_backtest_patterns", lambda: ["cup_handle"])
    monkeypatch.setattr(api, "get_backtest", lambda p, h: (p, h))
    assert api.get_backtest_endpoint(pattern="cup_handle", horizon=20) == ("cup_handle", 20)
    assert api.get_backtest_endpoint(pattern="all", horizon=10) == ("all", 10)
    with pytest.raises(api.HTTPException):
        api.get_backtest_endpoint(pattern="x' OR 1=1", horizon=10)
    with pytest.raises(api.HTTPException):
        api.get_backtest_endpoint(pattern="all", horizon=15)

    calls = []
    monkeypatch.setattr(api, "get_stocks", lambda n: calls.append(n) or list(range(n)))
    assert len(api.get_stocks_endpoint(limit=7)) == 7 and len(api.get_stocks_endpoint(limit=150)) == 150
    assert len(api.get_stocks_endpoint(limit=151)) == 151 and calls == [25, 150, 300]

    import datetime as _dt
    import ai_digest
    today = ai_digest.today_local()
    monkeypatch.setattr(api, "get_daily_digest", lambda day: day)
    assert api.get_daily_digest_endpoint(day=None) is None
    assert api.get_daily_digest_endpoint(day=today.isoformat()) == today.isoformat()
    for bad in (today + _dt.timedelta(days=1), today - _dt.timedelta(days=api.DIGEST_MAX_DAYS_BACK + 1)):
        with pytest.raises(api.HTTPException):
            api.get_daily_digest_endpoint(day=bad.isoformat())


# ------------------------------------------------------------------ v70: the visitor log (visits.py)
import datetime as _vdt
import json as _vjson
import re as _vre
import uuid as _vuuid

import visits


def _visit_msg(v=None, p=None, n=1, ev=None, **more):
    d = {"v": v or str(_vuuid.uuid4()), "p": p or str(_vuuid.uuid4()), "n": n, "ago": 60, "w": 50, "src": "google.com", "land": "/he",
         "dev": "phone", "lang": "he", "reg": 0, "pwa": 0, "tz": "Asia/Jerusalem", "ev": ev if ev is not None else [[0, "screen", "home"]]}
    d.update(more)
    return _vjson.dumps(d).encode()


def test_visit_message_is_cleaned():
    v = visits.parse(_visit_msg(ev=[[0, "screen", "home"], [9, "screen", "stock/NVDA"], [5, "hack", "x"], "junk", [1], [2, "share", {"a": 1}],
                                    [3, "search_none", "a\u0000b\u202e" + "z" * 200]], src="<b>", w=1e99, dev="toaster", tz=5))
    assert [e[1] for e in v["ev"]] == ["screen", "screen", "share", "search_none"]
    assert v["ev"][2][2] == "" and len(v["ev"][3][2]) <= 80 and "\u0000" not in v["ev"][3][2]
    assert v["dev"] == "" and v["tz"] == "" and v["seconds"] <= 12 * 3600
    for bad in (b"", b"x", b"[]", b"{}", b"\xff", _visit_msg(v="not-an-id"), _visit_msg(ev=[]), _visit_msg(ev="x"),
                b"[" * 5000, _visit_msg(land="x" * 50000), b'{"event_type":"page_view","session_id":"a"}'):
        assert visits.parse(bad) is None, bad[:60]
    assert visits.parse(_visit_msg(w=float("nan")))["seconds"] == 0


def test_visit_log_keeps_the_longest_copy_and_its_limits():
    log, a, p = visits.Log(), str(_vuuid.uuid4()), str(_vuuid.uuid4())
    two = [[0, "screen", "home"], [8, "view_stock", "KO"]]
    assert log.add(visits.parse(_visit_msg(a, p)), "IL", "1.1.1.1")
    assert log.add(visits.parse(_visit_msg(a, p, ev=two)), "US", "1.1.1.1")
    assert not log.add(visits.parse(_visit_msg(a, p)), "IL", "1.1.1.1")                    # a late, shorter copy
    assert not log.add(visits.parse(_visit_msg(a, ev=two + [[9, "share", "KO"]])), "", "2.2.2.2")     # somebody else's id
    assert len(log.waiting[a]["ev"]) == 2 and log.waiting[a]["country"] == "IL"
    for _ in range(100):
        log.add(visits.parse(_visit_msg()), "XX", "9.9.9.9")
    assert len(log.waiting) == 1 + visits.MAX_NEW_PER_IP_HOUR
    assert log.add(visits.parse(_vjson.dumps({"forget": p}).encode()))
    assert a not in log.waiting and a not in log.changed and p in log.forget
    at = lambda m: _vdt.datetime(2026, 11, 23, 10, m, tzinfo=_vdt.timezone.utc)
    assert log.due(at(3)) and not log.due(at(20)) and not log.due(at(45))
    half = lambda d, h: log.due(_vdt.datetime(2026, 11, d, h, 33, tzinfo=_vdt.timezone.utc))
    assert half(23, 18) and not half(22, 18) and not half(23, 3)       # Monday 13:33 New York; Sunday; the night
    # the alert loop looks every 5 minutes: every hour has a look inside the window
    assert all(any((s + 5 * k) % 60 in visits.FLUSH_MINUTES for k in range(12)) for s in range(60))


class _VisitCursor:
    def __init__(self, fail): self.sql, self.fail = [], fail
    def execute(self, q, a=None):
        if self.fail:
            raise RuntimeError("database down")
        self.sql.append((q, a))


class _VisitConn:
    def __init__(self, fail=False): self.cur, self.closed, self.done = _VisitCursor(fail), False, False
    def cursor(self): return self.cur
    def commit(self): self.done = True
    def close(self): self.closed = True


def test_visit_log_writes_once_and_survives_a_failed_write():
    log = visits.Log()
    log.add(visits.parse(_visit_msg()), "IL", "1")
    bad = _VisitConn(fail=True)
    with pytest.raises(RuntimeError):
        log.flush(lambda: bad)
    assert bad.closed and len(log.changed) == 1 and log.failed == 1
    good = _VisitConn()
    assert log.flush(lambda: good) == 1 and good.done and good.closed and not log.changed and log.failed == 0 and log.state()["written"] == 1
    assert sum("INSERT INTO visits " in q for q, _ in good.cur.sql) == 1
    assert log.flush(lambda: (_ for _ in ()).throw(AssertionError("nothing to write"))) == 0
    assert len(log.waiting) == 1                 # the next copy of the visit is compared with it


def test_visit_row_is_readable_in_neon():
    now = _vdt.datetime(2026, 11, 23, 12, 0, tzinfo=_vdt.timezone.utc)
    v = visits.parse(_visit_msg(n=3, w=200, reg=1, src="=HYPERLINK(1)", ev=[[0, "screen", "home"], [2, "section", "scans"],
                     [40, "screen", "stock/NVDA"], [60, "dd_tab", "fund"], [90, "search_none", "TEVA.TA"], [95, "tap", "switchChart QQQ"], [96, "tap", "newButton x"],
                     [97, "scroll", "100"], [98, "pick", "setScanFilter rs = 80+"], [99, "link", "reuters.com"], [170, "view_stock", "KO"],
                     [171, "error", "not loaded"]]), now)
    assert [s for k, _, s in visits.steps(v) if k == "screen"] == [40, 160]
    r = dict(zip(visits.COLUMNS, visits.row(dict(v, country="IL", browser="Chrome / Windows", ld=4))))
    for said in ("לחץ על גרף מדד QQQ", "לחץ על newButton x", "גלל עד הסוף", "בחר מסנן סריקה rs = 80+", "יצא לקישור reuters.com"):
        assert said in r["steps"], said
    assert r["last_screen"] == "ניתוח NVDA" and r["load_sec"] == 4 and r["browser"] == "Chrome / Windows"
    assert visits.browser("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) CriOS/140 Safari/604") == "Chrome / iPhone"
    assert visits.browser("") == ""
    assert r["time_il"] == "2026-11-23 13:59" and r["visit_no"] == 3 and r["country"] == "ישראל" and r["device"] == "טלפון"
    assert r["stocks"] == "NVDA, KO" and r["not_found"] == "TEVA.TA" and r["errors"] == "not loaded" and r["signed_in"] == "כן"
    assert r["steps"].startswith("דף הבית (0:40) | ראה סריקות | ניתוח NVDA (2:40) | לשונית פונדמנטלס")
    assert r["duration"] == "3:20" and r["source"] == "HYPERLINK(1)"          # an exported table runs nothing
    assert v["p"].startswith(r["visitor"]) and r["visitor_id"] == v["p"]
    long = visits.parse(_visit_msg(ev=[[i, "search_none", f"{i}" + "x" * 50] for i in range(120)]), now)
    assert len(dict(zip(visits.COLUMNS, visits.row(long)))["steps"]) <= visits.MAX_STEPS_TEXT
    cur = _VisitCursor(False)
    visits.ensure_tables(cur)
    visits.save(cur, v)
    made = cur.sql[0][0]
    assert all(_vre.search(rf"\b{c} (TEXT|INTEGER|TIMESTAMPTZ)", made) for c in visits.COLUMNS)
    assert cur.sql[-1][0].count("%s") == len(cur.sql[-1][1]) == len(visits.COLUMNS)

def test_what_waits_is_written_when_the_server_goes_down(api, monkeypatch):
    log, conn = visits.Log(), _VisitConn()
    monkeypatch.setattr(api.visits, "LOG", log)
    monkeypatch.setattr(api, "get_conn", lambda: conn)
    log.add(visits.parse(_visit_msg()), "IL", "1")
    cm = api._lifespan(api.app)
    with pytest.raises(StopIteration):
        cm.__aenter__().send(None)
    assert not conn.done                             # nothing at the start
    with pytest.raises(StopIteration):
        cm.__aexit__(None, None, None).send(None)
    assert conn.done and conn.closed and not log.changed
    assert api.app.router.lifespan_context is not None
    made = [q for q, _ in conn.cur.sql if "CREATE OR REPLACE VIEW" in q]
    assert len(made) == 2 and all(q.count("(") == q.count(")") and q.count(chr(39)) % 2 == 0 for q in made)
    for word in ("באנר ההסכמה", "אישרו", "סירבו", "כניסה לאתר"):                # the same words the totals are written with
        assert chr(39) + word + chr(39) in made[0]
        assert word in set(visits.COUNT_HE.values()) | set(visits.BANNER_HE.values())


def test_anonymous_totals_know_nothing_about_a_person():
    now = _vdt.datetime(2026, 11, 23, 22, 30, tzinfo=_vdt.timezone.utc)          # after midnight in Israel
    log = visits.Log()
    msg = lambda c: visits.parse(_vjson.dumps({"c": c}).encode())
    m = msg([["visit", "phone"], ["from", "google.com"], ["screen", "stock/NVDA"], ["search_none", "my name"], ["news_search", "x"],
             ["tap", "switchChart QQQ"], ["banner", "no"], ["hack", "x"], "junk", ["scroll", "100"], ["link", "=cmd.com"]])
    assert log.add(m, "IL", "1.1.1.1", now) and log.add(msg([["screen", "stock/NVDA"], ["screen", "home"]]), "IL", "1.1.1.1", now)
    day = _vdt.date(2026, 11, 24)
    want = {("כניסה לאתר", "טלפון"): 1, ("הגיעו מ", "google.com"): 1, ("מסך", "ניתוח NVDA"): 2, ("מסך", "דף הבית"): 1,
            ("חיפש ולא מצא", ""): 1, ("חיפש בחדשות", ""): 1, ("לחץ על", "גרף מדד QQQ"): 1, ("באנר ההסכמה", "סירבו"): 1,
            ("גלל", "עד הסוף"): 1, ("יצא לקישור", "cmd.com"): 1, ("מדינה", "ישראל"): 1}
    assert dict(log.counts) == {(day, *k): n for k, n in want.items()}
    assert not log.waiting and not log.changed                       # nothing of a visit is kept
    bad = _VisitConn(fail=True)
    with pytest.raises(RuntimeError):
        log.flush(lambda: bad)
    assert sum(log.counts.values()) == 12                            # a failed write loses nothing...
    good = _VisitConn()
    log.flush(lambda: good)
    assert not log.counts                                            # ...and a good one is not counted twice
    q, a = next(x for x in good.cur.sql if "INSERT INTO daily_counts" in x[0])
    assert q.count("%s") == len(a) == 4 and sum(a[3]) == 12 and len(set(zip(*a[:3]))) == len(a[0])
    for i in range(50):
        log.add(msg([["tap", f"made_up_{i}_{j}"] for j in range(100)]), "US", f"10.0.0.{i}", now)
    assert len(log.counts) <= visits.MAX_COUNT_KEYS
    one = visits.Log()
    assert sum(one.add(msg([["screen", "home"]] * 100), "", "9.9.9.9", now) for _ in range(20)) == 6      # 600 an hour
    assert msg([]) is None and msg([["screen", {"a": 1}]]) == {"counts": [("screen", "")]}


def test_every_step_the_site_sends_is_known():
    src = (ROOT / "index.html").read_text(encoding="utf-8")
    sent = set(_vre.findall(r"\btrack\('([a-z_]+)'", src)) | set(_vre.findall(r"\badd\('([a-z_]+)'", src)) & visits.EVENTS
    assert {"view_stock", "dd_tab", "digest", "signin"} <= sent
    assert sent <= visits.EVENTS, sent - visits.EVENTS
    assert visits.EVENTS - {"screen"} == set(visits.EVENTS_HE)
    assert set(_vre.findall(r"\bcount\('([a-z]+)'", src)) <= visits.EVENTS | set(visits.COUNT_HE)
    parts = _vre.search(r"const parts = \{(.*?)\};", src, _vre.S).group(1)
    assert set(_vre.findall(r"(\w+): '", parts)) == set(visits.SECTIONS_HE)


def test_key_news_and_headlines_of_23_11(api):
    import news_links, translation_fixes
    assert api._KEY_JUNK.search("COMMENTARY: Oil's Iran war recovery masks a stubborn diesel shortage")
    assert api._KEY_JUNK.search("Watch Fed’s Inflation Fight May Require More Hikes")
    assert not api._KEY_JUNK.search("S&P 500 correction: Five forces behind the market sell-off")
    assert news_links.is_unwanted_headline("Spanish stocks - Factors to watch on November 23")
    assert news_links.is_unwanted_headline("Dollar Tree earnings: what to look for")
    assert not news_links.is_unwanted_headline("Taiwan stocks rebound after a record weekly plunge as AI selloff eases")
    out = translation_fixes.prep_en("The S&P 500 closed in correction territory, off 10% from their records.")
    assert "record highs" in out


def test_usage_events_and_health_do_not_touch_the_database(api, monkeypatch):
    def forbidden(*a, **k):
        raise AssertionError("the database was opened")
    monkeypatch.setattr(api, "get_conn", forbidden)
    monkeypatch.setattr(api, "db_cursor", forbidden)
    log = visits.Log()
    monkeypatch.setattr(api.visits, "LOG", log)
    for i in range(50):
        log.add(visits.parse(_visit_msg(n=i)), "IL", f"10.0.0.{i}")
    assert len(log.waiting) == 50
    assert "/api/visits" not in {getattr(r, "path", "") for r in api.app.routes}      # the log is never served
    assert api.health() == {"status": "ok"}
    assert "/api/ui-strings" not in {getattr(r, "path", "") for r in api.app.routes}


def test_statistics_start_date_is_the_same_in_both_files(scanner, api):
    assert scanner.STATS_SINCE == api.STATS_SINCE


def test_new_highs_are_counted_by_the_close():
    """v37b: a stock that touches its high at the open and closes far below it is not a new high (77 of them on 5.11.2026)."""
    import market_breadth as mb
    days = pd.date_range("2025-01-01", periods=260, freq="B")
    base = np.linspace(100, 120, 260)
    close = pd.DataFrame({f"S{i}": base for i in range(mb.MIN_STOCKS)}, index=days)
    close["TOUCHED"] = base
    close.loc[days[-1], "TOUCHED"] = base[-1] - 15            # fell hard on the last day...
    high = close + 1
    high.loc[days[-1], "TOUCHED"] = 200                       # ...after a new high at the open
    low = close - 1
    last = mb.compute(close, high, low).iloc[-1]
    assert last["new_highs"] == mb.MIN_STOCKS and last["new_lows"] == 0


def test_breadth_day_label(api):
    """v37: 5.11.2026: S&P 500 -4.36%, 73 up, 430 down, and the chip said "broad participation" in green."""
    f = api._breadth_day_label
    assert f(73, 430) == "broad_decline" and f(430, 73) == "broad_rise"
    assert f(250, 250) is None and f(0, 0) is None and f(None, None) is None
    assert f(151, 349) is None and f(150, 350) == "broad_decline"              # exactly 70%


def test_api_robots_opens_only_answers_from_memory(api):
    """v35: Google obeys the API's robots.txt when it runs the page. What it may ask for must not reach the database or
    Yahoo on every request (v34 closed everything, and the home page it saw was empty)."""
    body = api.robots_txt().body.decode()
    assert body.endswith("Disallow: /\n") and "Allow: /api/stocks\n" in body
    assert "lookup" not in body and "fundamentals" not in body
    fns = {r.path: r.endpoint for r in api.app.routes if hasattr(r, "endpoint")}
    for path in api._ROBOTS_ALLOW:
        fn = fns[path]
        if fn.__name__.endswith("_endpoint"):                # v50: the endpoint tidies the parameters, then asks the kept function
            fn = getattr(api, fn.__name__[:-len("_endpoint")])
        assert hasattr(fn, "__wrapped__") or path == "/api/key-news", f"{path} is not kept in memory"


def test_news_schedule_never_doubles():
    """v34: a week of minutes: every 30 minutes on weekdays 06:00-22:00 ET, hourly otherwise, and never both at once."""
    import datetime as _dt
    import scheduler as s
    news = next(j for j in s.JOBS if j["name"] == "news")
    quiet = next(j for j in s.JOBS if j["name"] == "news_quiet")
    t = _dt.datetime(2026, 11, 2, 0, 0, tzinfo=s.ET)     # a Monday
    busy, calm = 0, 0
    for i in range(7 * 24 * 60):
        now = t + _dt.timedelta(minutes=i)
        a, b = s._is_slot(news, now), s._is_slot(quiet, now)
        assert not (a and b), f"both news jobs at {now}"
        busy += a
        calm += b
    assert busy == 5 * 33                                # 06:00 ... 22:00 = 33 half hours, 5 days
    assert calm == 5 * 7 + 2 * 24                        # 00:00-05:00 and 23:00 on weekdays, every hour on the weekend
    weekday_morning = t + _dt.timedelta(hours=8)
    assert s._is_slot(news, weekday_morning) and not s._is_slot(quiet, weekday_morning)
    sunday_noon = t + _dt.timedelta(days=6, hours=12)
    assert s._is_slot(quiet, sunday_noon) and not s._is_slot(news, sunday_noon)


def test_nasdaq_numbers(scanner):
    """v27: Nasdaq's calendar / screener text fields (market cap, price, EPS forecast in parentheses = negative)."""
    assert scanner._nasdaq_num("$1,171,562,950,000") == 1171562950000.0
    assert scanner._nasdaq_num("49300898699.00") == 49300898699.0
    assert scanner._nasdaq_num("($0.05)") == -0.05
    assert scanner._nasdaq_num("") is None and scanner._nasdaq_num("N/A") is None


def test_morning_brief_text():
    import datetime as _dt
    import push_alerts
    when = _dt.datetime(2026, 10, 29, 12, 30, tzinfo=_dt.timezone.utc)
    ev = [{"name_he": "מדד המחירים לצרכן", "name_en": "CPI", "impact": "high", "when": when}]
    he = push_alerts._morning_payload("he", ev, [("KO", "bmo", True)], 5, 12, ["KO"])
    en = push_alerts._morning_payload("en", ev, [("KO", "bmo", True)], 5, 12, ["KO"])
    assert "14:30" in he["body"] and "KO" in he["body"] and "12" in he["body"]   # Israel is UTC+2 after 25.10.2026
    assert "08:30 ET" in en["body"] and "+4" in en["body"]


# ------------------------------------------------------------------ v38: scheduler connections, db meter, sign out other devices
class _FakeCur:
    def __init__(self, log, claimed=None, fail_on=None):
        self.log, self.claimed, self.fail_on, self._rows = log, claimed, fail_on, []

    def execute(self, sql, params=None):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("SSL connection has been closed unexpectedly")
        self.log.append((" ".join(sql.split()), params))
        if "RETURNING slot_key" in sql:
            self._rows = [(k,) for k in params[0] if self.claimed is None or k in self.claimed]

    def fetchall(self):
        return self._rows

    def close(self):
        pass


class _FakeConn:
    def __init__(self, log, **k):
        self.log, self.k, self.closed, self.autocommit = log, k, 0, False

    def cursor(self, *a, **k):
        return _FakeCur(self.log, **self.k)

    def commit(self):
        self.log.append(("COMMIT", None))

    def rollback(self):
        self.log.append(("ROLLBACK", None))

    def close(self):
        self.closed = 1


def _fresh_scheduler(monkeypatch):
    import scheduler as s
    monkeypatch.setattr(s, "_done", {})
    monkeypatch.setattr(s, "_attempts", {})
    monkeypatch.setattr(s, "_last_prune", {"at": 0.0})
    monkeypatch.setattr(s, "_table_ready", {"v": False})
    return s


def test_scheduler_uses_one_connection_per_minute(monkeypatch):
    """v38: every job due in the same minute is claimed, then recorded, on ONE connection (was two per job)."""
    import datetime as _dt
    s = _fresh_scheduler(monkeypatch)
    log, opened, fired = [], [], []
    monkeypatch.setattr(s, "_dispatch", lambda wf: (fired.append(wf), (204, ""))[1])

    def get_conn():
        opened.append(1)
        return _FakeConn(log)
    now = _dt.datetime(2026, 11, 9, 10, 0, tzinfo=s.ET).astimezone(s.UTC)       # a Monday: news, quick, insider, econ, breadth
    did = s.tick(get_conn, now)
    assert len(opened) == 1
    assert {"news-aggregator.yml", "earnings-refresh.yml", "insider-refresh.yml", "econ-refresh.yml", "market-breadth.yml"} <= set(fired)
    assert len(did) == len(fired)
    sql = [q for q, _ in log]
    assert sum("INSERT INTO scheduler_runs" in q for q in sql) == 1
    assert sum("SET http_status = 204" in q for q in sql) == 1
    assert any("INTERVAL '30 days'" in q for q in sql)                           # the daily clean-up rides along
    s.tick(get_conn, now + _dt.timedelta(seconds=30))                           # the next tick: all done, no connection
    assert len(opened) == 1


def test_scheduler_skips_fired_slots_and_releases_failed_ones(monkeypatch):
    import datetime as _dt
    s = _fresh_scheduler(monkeypatch)
    now = _dt.datetime(2026, 11, 7, 3, 0, tzinfo=s.ET).astimezone(s.UTC)        # a Saturday night: news_quiet only
    keys = [s.slot_key(j, s.latest_due_slot(j, now)) for j in s.JOBS if s.latest_due_slot(j, now)]
    news = next(k for k in keys if k.startswith("news_quiet|"))
    log = []
    monkeypatch.setattr(s, "_dispatch", lambda wf: (500, "boom") if wf == "news-aggregator.yml" else (204, ""))
    s.tick(lambda: _FakeConn(log), now)
    deletes = [p for q, p in log if q.startswith("DELETE FROM scheduler_runs WHERE slot_key")]
    assert deletes and news in deletes[0][0]                                   # given back: the next tick retries
    # already fired elsewhere: not dispatched again
    s = _fresh_scheduler(monkeypatch)
    fired = []
    monkeypatch.setattr(s, "_dispatch", lambda wf: (fired.append(wf), (204, ""))[1])
    s.tick(lambda: _FakeConn([], claimed=set()), now)
    assert fired == []


def test_scheduler_gives_back_a_claim_on_a_new_connection(monkeypatch):
    """The connection dropped during the dispatch: the claim of a failed slot must still be given back, or it never runs."""
    import datetime as _dt
    s = _fresh_scheduler(monkeypatch)
    now = _dt.datetime(2026, 11, 7, 3, 0, tzinfo=s.ET).astimezone(s.UTC)
    monkeypatch.setattr(s, "_dispatch", lambda wf: (502, "bad gateway"))
    conns, log2 = [], []

    def get_conn():
        conns.append(1)
        return _FakeConn([], fail_on="DELETE") if len(conns) == 1 else _FakeConn(log2)
    s.tick(get_conn, now)
    assert len(conns) == 2 and any(q.startswith("DELETE FROM scheduler_runs") for q, _ in log2)


def test_scheduler_fires_when_the_database_is_down(monkeypatch):
    import datetime as _dt
    s = _fresh_scheduler(monkeypatch)
    fired = []
    monkeypatch.setattr(s, "_dispatch", lambda wf: (fired.append(wf), (204, ""))[1])

    def down():
        raise RuntimeError("no database")
    s.tick(down, _dt.datetime(2026, 11, 7, 3, 0, tzinfo=s.ET).astimezone(s.UTC))
    assert "news-aggregator.yml" in fired


def test_db_meter_counts_rows_and_sources():
    import db_meter as m
    m.take_pending()
    assert m.row_bytes(("KO", 61.5, None)) == 7 + (4 + 2) + (4 + 4) + 4
    assert m.row_bytes({"t": "שלום"}) == 7 + 4 + 8                               # Hebrew = 2 bytes a letter in UTF-8
    assert m.value_bytes({"a": 1}) == len('{"a": 1}')
    assert m.request_source("/api/stock/KO") == "server /api/stock"
    assert m.request_source("/robots.txt") == "server /robots.txt"

    class Base:
        def __init__(self):
            self.rows = [("KO", 1), ("PEP", 2)]

        def execute(self, *a):
            pass

        def fetchall(self):
            return self.rows

        def fetchone(self):
            return self.rows[0]

        def __iter__(self):
            return iter(self.rows)
    cur = m._counting_class(Base)()
    token = m.set_source("test source")
    try:
        cur.execute("SELECT 1")
        cur.fetchall()
        cur.fetchone()
        list(cur)
    finally:
        m.reset_source(token)
    got = m.take_pending()["test source"]
    assert got[1] == 1 and got[2] == 5 and got[3] > 0


def test_db_meter_writes_on_the_closing_connection():
    import db_meter as m
    m.take_pending()
    m.add("job a", connections=1, queries=3, rows=10, nbytes=500)
    m.add("job b", rows=2, nbytes=40)
    log = []

    class Info:
        transaction_status = 2                                   # in a transaction nobody committed: closing rolls it back

    conn = _FakeConn(log)
    conn.info = Info()
    old_plain, m._plain_cursor = m._plain_cursor, lambda c: c.cursor()
    try:
        assert m.flush_through(conn, force=True)
    finally:
        m._plain_cursor = old_plain
    sql = [q for q, _ in log]
    assert sql[0] == "ROLLBACK" and sql[-1] == "COMMIT"
    insert = next(p for q, p in log if q.startswith("INSERT INTO db_usage"))
    assert len(insert) == 12 and "job a" in insert and "job b" in insert
    assert m.take_pending() == {}
    m.add("job c", rows=1, nbytes=9)                             # a closed connection: the counts wait for the next one
    conn.closed = 1
    assert not m.flush_through(conn, force=True)
    assert m.take_pending()["job c"][2] == 1


def test_usage_line_in_health():
    import datetime as _dt
    import data_health as dh
    d = _dt.date(2026, 11, 9)
    assert dh.usage_line(d, [], [])["status"] == "info"
    days = [{"day": d, "bytes": 20_000_000, "connections": 1000}, {"day": d - _dt.timedelta(days=1), "bytes": 5_000_000, "connections": 100}]
    top = [{"source": "server /api/stocks", "bytes": 15_000_000, "connections": 10}]
    line = dh.usage_line(d, days, top)
    assert line["status"] == "ok" and line["value"] == 25.0 and "server /api/stocks 15.1" in line["detail"]
    days[0]["bytes"] = 200_000_000
    assert dh.usage_line(d, days, top)["status"] == "warn"
    import decimal
    days[0]["bytes"] = decimal.Decimal(20_000_000)                # what psycopg2 returns for SUM(bigint)
    assert dh.usage_line(d, days, top)["value"] == 25.0


def test_db_meter_hooks_the_real_psycopg2():
    """Without a database: the classes psycopg2 will build connections and cursors from (GitHub Actions has psycopg2)."""
    ext = pytest.importorskip("psycopg2.extensions")
    import psycopg2.extras
    import db_meter as m
    conn_class = m._make_connection_class(ext)
    assert issubclass(conn_class, ext.connection)
    for base in (ext.cursor, psycopg2.extras.RealDictCursor, psycopg2.extras.DictCursor):
        cls = m._counting_class(base)
        assert issubclass(cls, base) and cls.__mro__[1] is m._Counting
    assert m._counting_class(ext.cursor) is m._counting_class(ext.cursor)


def test_sign_out_other_devices_ends_older_tokens(api, monkeypatch):
    """v38: a token carries the account's token version; a newer version (or a deleted account) = 401."""
    pytest.importorskip("jwt")
    if api.pyjwt is None:
        pytest.skip("PyJWT not loaded by the server here")
    from fastapi import HTTPException
    monkeypatch.setattr(api, "_auth_ready", lambda: True)
    monkeypatch.setattr(api, "_token_versions", {})
    api._remember_token_version(7, 2)
    assert api.current_user_id("Bearer " + api._issue_token(7, 2)) == 7
    for token, why in ((api._issue_token(7, 1), "signed out on this device"), (api._issue_token(7), "signed out on this device")):
        with pytest.raises(HTTPException) as e:
            api.current_user_id("Bearer " + token)
        assert e.value.status_code == 401 and e.value.detail == why
    api._remember_token_version(7, None)                           # the account was deleted
    with pytest.raises(HTTPException) as e:
        api.current_user_id("Bearer " + api._issue_token(7, 2))
    assert e.value.detail == "user not found"
    api._remember_token_version(8, 0)                              # a token from before v38 has no "tv": still good
    old = api.pyjwt.encode({"sub": "8", "exp": 4102444800}, api.JWT_SECRET, algorithm="HS256")
    assert api.current_user_id("Bearer " + old) == 8

def test_index_members_tag_lists(api, monkeypatch):
    """v59: the S&P 500 / Nasdaq-100 tag. From the two public lists in memory, never the database; a cut list (a failed
    download) is not served, so a real member is never shown as "not in the index"."""
    monkeypatch.setattr(api, "_index_members_cache", {"data": None, "ts": 0.0, "tried": 0.0})
    monkeypatch.setattr(api, "db_cursor", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no database")))
    sp = {f"S{i}": {} for i in range(500)}
    sp["BRK-B"] = {}
    monkeypatch.setattr(api, "get_sp500_info_map", lambda: sp)

    class _Resp:
        def __init__(self, text): self.text = text
        def raise_for_status(self): pass
    ndx_csv = "Symbol,Name\n" + "".join(f"N{i},x\n" for i in range(100)) + "BRK.B,y\n"
    monkeypatch.setattr(api.requests, "get", lambda url, timeout=15: _Resp(ndx_csv))
    d = api.get_index_members()
    assert d["available"] and "BRK-B" in d["sp500"] and "BRK-B" in d["nasdaq100"] and len(d["nasdaq100"]) == 101
    monkeypatch.setattr(api, "_index_members_cache", {"data": None, "ts": 0.0, "tried": 0.0})
    monkeypatch.setattr(api.requests, "get", lambda url, timeout=15: _Resp("Symbol\nAAPL\n"))     # cut list
    assert api.get_index_members() == {"available": False}


def test_stock_news_google_items_carry_the_outlet_site(api, monkeypatch):
    """v59: a Google News link can open Google's "We're sorry" page on a phone — the site searches the headline on the
    outlet's site instead, so the item carries that site (from <source url=...>) and the English headline."""
    rss = (b'<rss><channel><item><title>Zeta rises - The Motley Fool</title><link>https://news.google.com/rss/articles/X</link>'
           b'<source url="https://www.fool.com">The Motley Fool</source><pubDate>Sun, 15 Nov 2026 15:21:35 GMT</pubDate></item>'
           b'</channel></rss>')

    class _Resp:
        content = rss
        def raise_for_status(self): pass
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: _Resp())
    it = api._rss_items("https://news.google.com/rss/search?q=x", google=True)[0]
    assert it["title"] == "Zeta rises" and it["site"] == "fool.com"


# ------------------------------------------------------------------ v73: holidays, moved reports, the weekend's reason
def test_v73_no_market_jobs_on_a_holiday(scanner):
    import datetime as _dt
    import scheduler as s
    day = _dt.datetime(2026, 11, 26, 0, 0, tzinfo=s.ET)                 # Thanksgiving, a Thursday
    fired = {}
    for i in range(24 * 60):
        now = day + _dt.timedelta(minutes=i)
        for j in s.JOBS:
            fired[j["name"]] = fired.get(j["name"], 0) + s._is_slot(j, now)
    for name in ("scan_open", "scan_close", "quick", "earn_am", "earn_pm", "breadth", "breadth_eod", "insider", "ai_notes",
                 "ratings_am", "ratings_pm", "news"):
        assert fired[name] == 0, name
    assert fired["news_quiet"] == 24 and fired["data_health"] == 1 and fired["backup"] == 1 and fired["econ"] == 6
    friday = {j["name"]: sum(s._is_slot(j, day + _dt.timedelta(days=1, minutes=i)) for i in range(24 * 60)) for j in s.JOBS}
    assert friday["scan_open"] == friday["scan_close"] == 1 and friday["news"] == 33 and friday["news_quiet"] == 7
    assert s.next_slot(next(j for j in s.JOBS if j["name"] == "scan_open"), day.astimezone(s.UTC)).astimezone(s.ET).date() == _dt.date(2026, 11, 27)
    # every job that needs the market says so, one way or the other
    assert all(j.get("market_days_only") or "weekdays_only" in j for j in s.JOBS)
    # a scan started by hand that day does nothing either (New York's date, not UTC's)
    utc = _dt.timezone.utc
    assert scanner.market_holiday_today(_dt.datetime(2026, 11, 26, 14, 45, tzinfo=utc))
    assert scanner.market_holiday_today(_dt.datetime(2026, 11, 27, 1, 0, tzinfo=utc))          # Thursday 20:00 in New York
    assert not scanner.market_holiday_today(_dt.datetime(2026, 11, 27, 14, 45, tzinfo=utc))


def test_v73_scan_stops_on_a_holiday(scanner, monkeypatch):
    monkeypatch.setattr(scanner, "market_holiday_today", lambda now=None: True)
    monkeypatch.setattr(sys, "argv", ["pipeline_a_scanner.py"])
    monkeypatch.setattr(scanner, "init_db", lambda: pytest.fail("the scan started"))
    assert scanner.main() is None


def test_v73_moved_reports(scanner):
    import datetime as _dt
    d = lambda n: _dt.date(2026, 11, n)
    rows = [("BHP", d(17), "listed"), ("BHP", d(24), "listed"), ("GFF", d(17), "sec"), ("GFF", d(18), "listed"),
            ("DY", d(18), None), ("DY", d(24), "listed"), ("NVDA", d(17), "sec"), ("OLD", d(17), "listed"),
            ("SEC", d(17), "sec"), ("SEC", d(24), "listed"), ("TWO", d(18), "listed"), ("TWO", d(20), "listed")]
    listed = {("BHP", d(24)), ("GFF", d(17)), ("DY", d(24)), ("SEC", d(24)), ("TWO", d(18)), ("TWO", d(20)), ("NVDA", d(17))}
    got = scanner.moved_reports(rows, listed, listed)
    assert sorted(got) == [("BHP", d(17), d(24)), ("DY", d(18), d(24)), ("GFF", d(18), d(17))]
    # nothing is dropped for a company this run did not store (a source that did not answer), or listed on both days
    assert scanner.moved_reports(rows, listed, set()) == []
    sql = []

    class Cur:
        def execute(self, q, p=None): sql.append((" ".join(q.split()), p))
        def fetchall(self): return rows
    scanner._drop_moved_reports(Cur(), listed, {("BHP", d(24))}, d(17), d(30))
    assert sql[1][0].startswith("UPDATE earnings_calendar n SET eps_estimate = COALESCE(n.eps_estimate, o.eps_estimate)")
    assert sql[1][1] == ("BHP", d(24), "BHP", d(17)) and sql[2][0].startswith("DELETE") and sql[2][1] == ("BHP", d(17)) and len(sql) == 3


def test_v73_health_counts_companies_listed_twice():
    import datetime as _dt
    import data_health as dh

    class Cur:
        def __init__(self, twice): self.twice, self.n = twice, 0
        def execute(self, *a): self.n += 1
        def fetchall(self):
            if self.n == 1:
                return [{"ticker": "A", "eps_actual": 1.0, "revenue_actual": 1.0, "rel": False, "covered": True, "session": "bmo"}]
            return [{"ticker": t} for t in self.twice]
    now = _dt.datetime(2026, 11, 24, 12, 30, tzinfo=_dt.timezone.utc)
    get = lambda twice: {l["check"]: l for l in dh.check_earnings(Cur(twice), now)}["earnings.twice"]
    assert get([])["status"] == "ok" and get(["BHP"])["status"] == "info"
    line = get(["BHP", "PDD", "BZ"])
    assert line["status"] == "warn" and "BHP, PDD, BZ" in line["detail"]


def test_v73_weekend_takes_the_reason_from_an_earlier_digest():
    """Sunday 22.11.2026: Friday's -3.7%, no reason in Sunday's headlines, while Friday's digest had one."""
    import copy
    import ai_digest as d
    market = {"day": "2026-11-22", "session_day": "2026-11-20", "same_day": False, "status": "weekend", "move": "closed", "last_move": "extreme",
              "indices": [{"symbol": "^GSPC", "name_en": "S&P 500", "name_he": "S&P 500", "change_pct": -3.7, "close": 1, "change_points": 1},
                          {"symbol": "^IXIC", "name_en": "Nasdaq", "name_he": "x", "change_pct": -5.28, "close": 1, "change_points": 1}]}
    why = "Nvidia's forecast disappointed investors."
    base = {"overview_en": "On Friday the S&P 500 fell 3.7%.", "topics": []}
    out = d.add_earlier_why(copy.deepcopy(base), market, why)
    assert out["overview_en"] == "On Friday the S&P 500 fell 3.7%. Why: " + why and out["why_en"] == why
    # the model wrote that there is no reason: replaced, not added to
    none = {"overview_en": "On Friday the S&P 500 fell 3.7%. " + d.NO_REASON, "why_en": d.NO_REASON, "topics": []}
    assert d.add_earlier_why(none, market, why)["overview_en"] == out["overview_en"]
    # an overview that does not name the day gets the move first
    far = d.add_earlier_why({"overview_en": "Inflation data is due.", "topics": []}, market, why)["overview_en"]
    assert far.startswith("Inflation data is due. On Friday, the S&P 500 fell 3.7%") and far.endswith("Why: " + why)
    # its own reason stays; nothing on a day with its own session, after an ordinary session, or with no earlier reason
    own = {"overview_en": "On Friday ... Why: X.", "why_en": "X.", "topics": []}
    assert d.add_earlier_why(dict(own), market, why) == own
    for m in (dict(market, same_day=True, move="extreme"), dict(market, last_move="normal")):
        assert d.add_earlier_why(dict(base), m, why) == base
    assert d.add_earlier_why(dict(base), market, None) == base

    class Conn:
        def cursor(self): return self
        def execute(self, q, p): self.p = p
        def fetchall(self): return [(d.NO_REASON, "2026-11-20"), ("another session's", "2026-11-19"), (why, "2026-11-20")]
        def close(self): pass
        def rollback(self): pass
    import datetime as _dt
    c = Conn()
    assert d._earlier_why(c, _dt.date(2026, 11, 22), market) == why and c.p == ("2026-11-20", _dt.date(2026, 11, 22))


def test_v73_hebrew_words_of_23_11(api):
    import json as _json
    import ai_digest as d
    import pipeline_b_news_aggregator as pb
    import translation_fixes as tf
    en = {"overview": "The S&P 500 surged 4.7% in an extreme rebound.", "topics": []}
    he = lambda t: _json.dumps({"overview": t, "topics": []}, ensure_ascii=False)
    with pytest.raises(ValueError, match="rebound"):
        d.clean_hebrew(he("מדד S&P 500 זינק 4.7% בתיקון חד."), en)
    assert d.clean_hebrew(he("מדד S&P 500 זינק 4.7% בהתאוששות חדה."), en)
    with pytest.raises(ValueError, match="rout"):
        d.clean_hebrew(he("הזהב עלה לאחר מסלול שבועי של 9%."), {"overview": "Gold rose after a 9% weekly rout.", "topics": []})
    # strikes of a war and of workers
    p = tf.prep_en
    assert "new attacks" in p("Yemen's Houthis claim attacks on Saudi oil refinery as Trump warns of new strikes next year")
    assert "Israel strikes Iran" in p("Israel strikes Iran") and "new strikes" in p("Boeing workers plan new strikes")
    assert "new strikes" in p("Drone maker's workers plan new strikes") and "attacks" not in p("US and Iran: Trump strikes deal")
    assert "weekly plunge" in p("Gold bounces nearly 2% after 9% weekly rout")
    assert "תקיפות חדשות" in tf.fix_he("טראמפ מזהיר מפני שביתות חדשות בשנה הבאה") and tf.fix_he(tf.fix_he("מסלול שבועי של 9%")) == "ירידה שבועית של 9%"
    # a company whose name holds "CPI" is not the inflation report
    assert api._key_event_of("CPI Aero Q3 Earnings Rise Y/Y on Robust Defense Demand") is None
    got = pb.classify_headline("CPI Aero Q3 Earnings Rise Y/Y on Robust Defense Demand", "")
    assert got and got["tag"] == "דוחות"
    assert pb.classify_headline("US CPI rises 0.3% in October", "")["tag"] == "CPI"


def test_v73_ratings_through_vercel(monkeypatch):
    import datetime as _dt
    import analyst_ratings as ar
    calls = []

    class R:
        status_code, text = 200, ""
        def __init__(self, tickers): self.tickers = tickers
        def json(self):
            row = {"epochGradeDate": 1795454940, "firm": "TD Cowen", "toGrade": "Buy", "fromGrade": "Hold", "action": "up",
                   "priceTargetAction": "Raises", "currentPriceTarget": 250.0, "priorPriceTarget": 200.0}
            return {"results": {t: [row] for t in self.tickers if t != "BAD"}, "failed": {"BAD": "HTTP 404"} if "BAD" in self.tickers else {}}

    def get(url, **k):
        calls.append(url.split("?t=")[1].split(","))
        return R(calls[-1])
    import requests
    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(ar, "_vercel_rows", {})
    monkeypatch.setattr(ar.time, "sleep", lambda s: None)
    tickers = [(f"T{i}", 1e11) for i in range(45)] + [("BAD", 1e10)]
    rows, stats = ar.fetch(tickers, now=_dt.datetime(2026, 11, 24, 14, 0, tzinfo=_dt.timezone.utc), variant=(ar.VERCEL, None, None))
    assert len(rows) == 45 and stats["failed"] == 1 and stats["asked"] == 46
    assert [len(c) for c in calls[:3]] == [20, 20, 6] and max(len(c) for c in calls) <= ar.VERCEL_BATCH
    assert rows[0]["to_grade"] == "Buy" and rows[0]["pt_now"] == 250.0 and stats["newest"].year == 2026
    assert ar._variants()[-1][0] == ar.VERCEL and "yfinance" == ar._variants()[0][0]


def test_v74_guidance_compared_with_the_same_thing():
    """24.11.2026 on the site: ZM "next quarter's EPS +63%" (a wrong saved consensus) and J "revenue -35%" (net against gross)."""
    import copy
    import datetime as _dt
    import data_health as dh
    import earnings_guidance as eg
    item = lambda metric, period, q, est, lo, hi, quote: {
        "metric": metric, "period": period, "quarter": q, "fiscal_year": 2027, "est": est, "low": lo, "high": hi, "basis": "adjusted",
        "quote": quote, "change": None, "vs_pct": round(((lo + hi) / 2 / est - 1) * 100, 2)}
    zm = [item("revenue", "quarter", 4, 1297465330.0, 1.293e9, 1.298e9, "Total revenue is expected to be between $1.293 billion and $1.298 billion"),
          item("eps", "quarter", 4, 0.9559, 1.55, 1.57, "Non-GAAP diluted EPS is expected to be between $1.55 and $1.57"),
          item("eps", "year", None, 6.1362, 6.24, 6.25, "Non-GAAP diluted EPS is expected to be between $6.24 and $6.25")]
    before = copy.deepcopy(zm)
    got = eg.fair_comparisons(zm)
    assert zm == before                                                  # the stored row is not changed
    assert got[1]["est"] is None and got[1]["vs_pct"] is None and got[1]["unfair"].endswith("(+63%)") and got[1]["low"] == 1.55
    assert got[0]["vs_pct"] == -0.15 and got[2]["vs_pct"] == 1.77 and eg.fair_comparisons(got) == got
    # Jacobs: net revenue against all revenue; its EPS stays
    j = [item("revenue", "year", None, 15651434390.0, 10.0e9, 10.35e9, "adjusted net revenue to range from $10.0B to $10.35B"),
         item("eps", "year", None, 8.2369, 8.05, 8.45, "adjusted EPS to range from $8.05 to $8.45")]
    got = eg.fair_comparisons(j)
    assert got[0]["est"] is None and "another revenue measure" in got[0]["unfair"] and got[1]["vs_pct"] is not None
    # "net revenue" that IS the consensus's measure, a large real gap in plain revenue, and a real gap that the year shares: all stay
    ok = [item("revenue", "quarter", 1, 1.0e9, 1.02e9, 1.04e9, "net revenues of $1.02 - $1.04 billion"),
          item("revenue", "year", None, 5.0e9, 7.0e9, 7.2e9, "revenue of $7.0 - $7.2 billion"),
          item("eps", "quarter", 4, 1.00, 1.50, 1.60, "EPS of $1.50 to $1.60"), item("eps", "year", None, 4.00, 4.50, 4.60, "EPS of $4.50 to $4.60")]
    assert eg.fair_comparisons(ok) == ok
    # an early quarter up 30% with the year unchanged can be real; a quarter alone has nothing to be checked against
    early = [item("eps", "quarter", 1, 1.00, 1.30, 1.30, "EPS of $1.30"), item("eps", "year", None, 4.00, 4.00, 4.00, "EPS of $4.00")]
    assert eg.fair_comparisons(early) == early and eg.fair_comparisons(before[1:2]) == before[1:2]
    assert eg.fair_comparisons(None) == [] and eg.fair_comparisons([{"metric": "revenue_growth", "vs_pct": None, "est": None}])
    # ... on every save
    s = eg.with_estimates(copy.deepcopy(zm), {"est_nq_eps": 0.9559, "est_nq_rev": 1297465330.0, "est_fy0_eps": 6.1362, "fy_end": _dt.date(2027, 1, 31)})
    assert [x["vs_pct"] is None for x in s] == [False, True, False]

    class Cur:
        def execute(self, *a): pass
        def fetchone(self): return {"ok": True}
        def fetchall(self): return [{"ticker": "ZM", "status": "found", "items": before}]
    line = dh.check_guidance(Cur(), _dt.datetime(2026, 11, 24, 12, 30, tzinfo=_dt.timezone.utc))[0]
    assert line["status"] == "ok" and "comparison not shown: ZM eps" in line["detail"]


def test_v74_headlines_of_24_11():
    import news_links as nl
    import translation_fixes as tf
    for t in ("Earnings Calendar for:", "Primary Health Properties (LSE:PHP) Stock Looks Reasonably Priced On Current Earnings",
              "Westpac (ASX: WBC) profit rises as rates stay high"):
        assert nl.is_unwanted_headline(t), t
    for t in ("BP (LSE:BP) (NYSE:BP) shares jump after earnings", "ASE Technology (NYSE: ASX) jumps 21% on AI demand",
              "Chip, AI stocks retreat after record-breaking rally on Huang's return", "Stocks are priced for perfection before earnings season"):
        assert not nl.is_unwanted_headline(t), t
    p = tf.prep_en
    assert p("ECB’s Simkus Says It’s ‘Prudent’ to Hike as Inflation Broadens").endswith("to raise interest rates as Inflation Broadens")
    assert "to raise interest rates again" in p("Fed is ready to hike again, Williams says")
    for same in ("Fed officials see firms ready to hike prices", "Thousands to hike in Yosemite as parks reopen", "Fed hikes rates by a quarter point"):
        assert "to raise interest rates" not in p(same), same
    assert p("AI market whipsaw puts Kevin Warsh's rate-hike strategy to the test").startswith("AI market volatility puts")
    assert p("Chip, AI stocks retreat").lower().startswith("chip and ai stocks")
    assert "rally led by Nvidia" in p("US Stock Futures Mixed After Nvidia-Led Rally as Investors Assess").replace("Rally", "rally")
    assert "comes in below forecasts" in p("core measure undershoots forecasts")
    for bad, good in (("סימקוס של ה-ECB אומר שזה 'שפוי' לעלות עם התרחבות האינפלציה", "'נבון' להעלות את הריבית עם"),
                      ("צ'יפ, מניות בינה מלאכותית נסוגות", "מניות שבבים ובינה מלאכותית נסוגות"),
                      ("מעורבים לאחר עליית Nvidia-Led כאשר", "לאחר עלייה בהובלת Nvidia כאשר"),
                      ("שוט שוק AI מעמיד את", "תנודתיות בשוק ה-AI מעמידה את")):
        assert good in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad)


def test_v75_eps_per_todays_share():
    """24.11.2026: NFLX's EPS of Dec 2025 was -17.14 (the year after the 10-for-1 split minus nine months before it), and the P/E
    history of every company that split in five years was wrong. Rows below are NFLX's real ones."""
    import fundamentals as f
    row = lambda s, e, v, form, filed: {"start": s, "end": e, "val": v, "form": form, "filed": filed}
    rows = [row("2025-01-01", "2025-03-31", 6.61, "10-Q", "2025-04-18"), row("2025-01-01", "2025-03-31", 0.66, "10-Q", "2026-04-17"),
            row("2025-01-01", "2025-06-30", 13.8, "10-Q", "2025-07-18"), row("2025-04-01", "2025-06-30", 7.19, "10-Q", "2025-07-18"),
            row("2025-01-01", "2025-09-30", 19.67, "10-Q", "2025-10-22"), row("2025-07-01", "2025-09-30", 5.87, "10-Q", "2025-10-22"),
            row("2025-01-01", "2025-12-31", 2.53, "10-K", "2026-01-23"), row("2026-01-01", "2026-03-31", 1.23, "10-Q", "2026-04-17")]
    splits = [("2015-07-15", 7.0), ("2025-11-17", 10.0)]
    got = f._flow_quarters(f.for_splits(rows, splits))
    assert {d: round(v, 2) for d, v in got.items()} == {"2025-03-31": 0.66, "2025-06-30": 0.72, "2025-09-30": 0.59, "2025-12-31": 0.56, "2026-03-31": 1.23}
    assert rows[0]["val"] == 6.61 and f.for_splits(rows, []) is rows
    facts = {"EarningsPerShareDiluted": {"units": {"USD/shares": rows}}}
    assert round(f.sec_quarterly_series(facts, f.EPS_TAGS, instant=False)["2025-12-31"], 2) == -17.14        # as it was
    assert round(f.sec_quarterly_series(facts, f.EPS_TAGS, instant=False, splits=splits)["2025-12-31"], 2) == 0.56
    # a reverse split, and two splits after one filing (NVDA: 4-for-1 and 10-for-1)
    assert f.for_splits([row("", "", 2.0, "10-Q", "2021-01-01")], [("2021-08-02", 0.125)])[0]["val"] == 16.0
    assert f.for_splits([row("", "", 4.0, "10-Q", "2021-05-01")], [("2021-07-20", 4.0), ("2024-06-10", 10.0)])[0]["val"] == 0.1


def test_v75_seventeen_week_quarter_and_gaps():
    import fundamentals as f
    row = lambda s, e, v, form="10-Q": {"start": s, "end": e, "val": v, "form": form}
    # Costco, fiscal 2023 (53 weeks): the last quarter is 119 days, and was missing
    rows = [row("2022-08-29", "2022-11-20", 1364), row("2022-08-29", "2023-02-12", 2830), row("2022-08-29", "2023-05-07", 4132),
            row("2022-08-29", "2023-09-03", 6292, "10-K")]
    assert f._flow_quarters(rows) == {"2022-11-20": 1364, "2023-02-12": 1466.0, "2023-05-07": 1302.0, "2023-09-03": 2160.0}
    assert f._flow_quarters([row("2023-01-01", "2023-05-02", 100)]) == {}                      # a 4-month transition period is not a quarter
    assert f.consecutive(["2022-11-20", "2023-02-12", "2023-05-07", "2023-09-03"])
    assert f.consecutive(["2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"])
    assert not f.consecutive(["2023-02-12", "2023-05-07", "2023-11-26", "2024-02-18"])           # the hole: 15 months
    assert not f.consecutive(["2026-03-31", "2026-06-30", "2026-09-30"]) and not f.consecutive([None] * 4)


def test_v75_pe_history():
    import pandas as pd
    import fundamentals as f
    days = pd.bdate_range("2024-01-01", "2026-11-20", tz="America/New_York")
    hist = pd.DataFrame({"Close": [100.0] * len(days), "Stock Splits": [0.0] * len(days)}, index=days)
    hist.loc[pd.Timestamp("2025-11-17", tz="America/New_York"), "Stock Splits"] = 10.0
    assert f.splits_of(hist) == [("2025-11-17", 10.0)] and f.splits_of(None) == []
    ends = [d.strftime("%Y-%m-%d") for d in pd.date_range("2023-03-31", "2026-09-30", freq="QE")]
    band = f.compute_pe_band_sec("X", ends, [1.0] * len(ends), hist)
    assert set(band["values"]) == {25.0} and band["current"] == 25.0 and band["dates"][-1] == "2026-11-16"
    # a missing quarter: no P/E from a "year" of 15 months, and none from an old year
    holes = [1.0] * len(ends)
    holes[ends.index("2025-06-30")] = None
    gap = f.compute_pe_band_sec("X", ends, holes, hist)
    assert len(gap["dates"]) < len(band["dates"]) and "2026-02-02" not in gap["dates"] and set(gap["values"]) == {25.0}
    assert f.compute_pe_band_sec("X", ends, [1.0] * len(ends), None) is None
    assert f.compute_pe_band_sec("X", ends[:3], [1.0] * 3, hist) is None


def test_v75_health_eps_splits():
    import data_health as dh

    class Cur:
        def __init__(self, rows): self.rows = rows
        def execute(self, q, p): self.p = p
        def fetchall(self): return self.rows
    c = Cur([])
    assert dh.check_eps_splits(c)[0]["status"] == "ok" and c.p == (0.2, 5)
    assert dh.check_eps_splits(Cur([{"ticker": "A", "ratio": 0.1}]))[0]["status"] == "info"
    line = dh.check_eps_splits(Cur([{"ticker": f"T{i}", "ratio": 0.1} for i in range(9)]))[0]
    assert line["status"] == "warn" and "T0 (0.10x)" in line["detail"]


def test_v76_a_quarter_read_as_the_year():
    """Dycom, 24.11.2026: "Fourth Quarter Outlook ... EPS $2.48 to $2.78" was shown as the year, -84.6% from the consensus."""
    import datetime as _dt
    import data_health as dh
    import earnings_guidance as eg
    quote = "Non-GAAP Adjusted Diluted EPS (excluding amortization expense) $2.48 to $2.78"
    item = lambda lo, hi, est, q: {"metric": "eps", "period": "year", "quarter": None, "fiscal_year": 2027, "est": est, "low": lo, "high": hi,
                                   "basis": "adjusted", "quote": q, "change": None, "vs_pct": round(((lo + hi) / 2 / est - 1) * 100, 2)}
    rev = {"metric": "revenue", "period": "year", "quarter": None, "fiscal_year": 2027, "est": 7591255740.0, "low": 7.66e9, "high": 7.73e9,
           "basis": None, "quote": "Contract revenues $7.66 billion to $7.73 billion", "change": None, "vs_pct": 1.37}
    dy = [rev, item(2.48, 2.78, 17.077, quote)]
    assert eg.fair_comparisons(dy) == [rev] and len(dy) == 2
    # the words name a year: a real (bad) forecast stays; so does an ordinary gap, and a tiny or missing consensus
    for keep in (item(2.48, 2.78, 17.077, "full-year adjusted EPS of $2.48 to $2.78"), item(8.0, 8.2, 10.0, quote), item(0.05, 0.07, 0.15, quote),
                 dict(item(2.48, 2.78, 17.077, quote), est=None, vs_pct=None)):
        assert eg.fair_comparisons([keep]) == [keep], keep
    assert eg._says_year("Adjusted EPS for fiscal 2027 of $3") and eg._says_year("FY27 EPS $3") and not eg._says_year("fiscal 2027 fourth quarter EPS $3")
    # when it is read: below the single quarter just reported
    text = "Outlook: the Company expects the following. Quarter Ending January 30, 2027 " + quote
    it = {"period": "year", "fiscal_year": 2027, "metric": "eps", "basis": "adjusted", "low": "2.48", "high": "2.78", "unit": "dollars", "quote": quote}
    ref = {"eps_estimate": 4.57, "revenue_estimate": 1.945e9}
    got = eg._check_item(it, eg._norm(text), ref, _dt.date(2026, 11, 24))
    assert isinstance(got, str) and "quarter's forecast" in got
    assert isinstance(eg._check_item(dict(it, period="quarter", quarter=4), eg._norm(text), ref, _dt.date(2026, 11, 24)), dict)
    assert isinstance(eg._check_item(it, eg._norm(text), {"eps_estimate": 0.7}, _dt.date(2026, 11, 24)), dict)

    class Cur:
        def execute(self, *a): pass
        def fetchone(self): return {"ok": True}
        def fetchall(self): return [{"ticker": "DY", "status": "found", "items": dy}]
    line = dh.check_guidance(Cur(), _dt.datetime(2026, 11, 25, 12, 30, tzinfo=_dt.timezone.utc))[0]
    assert line["status"] == "ok" and "DY eps: a quarter's figure read as the year (-85%)" in line["detail"]


def test_v76_postponed_report(scanner, monkeypatch):
    """VEEV stood on 19.11.2026 with no filing and no results, Yahoo had 3.12: tagged "3 trading days after a report"."""
    import datetime as _dt
    d = lambda n, m=11: _dt.date(2026, m, n)
    keys = {("VEEV", d(19)), ("DONE", d(19)), ("SEC", d(19)), ("NEXTQ", d(19)), ("NOYAHOO", d(19)), ("TODAY", d(24)), ("SAME", d(19))}
    nxt = {"VEEV": d(3, 12), "DONE": d(3, 12), "SEC": d(3, 12), "NEXTQ": _dt.date(2027, 2, 18), "TODAY": d(3, 12), "SAME": d(19)}
    got = scanner.postponed_reports(keys, nxt, {("DONE", d(19)), ("SEC", d(19))}, d(24))
    assert got == {("VEEV", d(19))} and scanner.postponed_reports(keys, {}, set(), d(24)) == set()
    sql = []

    class Cur:
        def execute(self, q, p=None): sql.append((" ".join(q.split()), p))
        def fetchall(self): return [("ADI", d(24), "bmo")]
        def close(self): pass
    scanner._drop_postponed(Cur(), got)
    assert len(sql) == 1 and sql[0][1] == ("VEEV", d(19)) and "eps_actual IS NULL AND revenue_actual IS NULL" in sql[0][0] and "<> 'sec'" in sql[0][0]
    scanner._drop_postponed(Cur(), set())
    assert len(sql) == 1

    class Conn:
        def cursor(self): return Cur()
        def close(self): pass
    monkeypatch.setattr(scanner, "get_conn", lambda: Conn())
    assert scanner.recent_reports() == {"ADI": (d(24), "bmo")}
    assert "report_date >= CURRENT_DATE - 1 OR eps_actual IS NOT NULL OR revenue_actual IS NOT NULL OR session_source = 'sec'" in sql[-1][0]


def test_v76_digest_of_24_11():
    import json as _json
    import ai_digest as d
    assert d._own_guess("Kevin Warsh's potential Fed role is cited", "AI boom looms over Warsh's Fed") == "potential Fed role"
    assert d._own_guess("his potential Fed role", "Markets weigh potential Fed role for X") is None
    assert d._own_guess("The Fed chair said rates may rise", "") is None
    assert d._no_dates("Trump backed it and U.S. stocks surged. the rest fell") == "Trump backed it and U.S. stocks surged. The rest fell"
    assert d._no_dates("On 2026-11-10 stocks rose").startswith("Stocks rose") or d._no_dates("On 2026-11-10 stocks rose")[0].isupper()
    en = {"overview": "Dollar Tree jumped on a Q3 beat.", "topics": []}
    he = lambda t: _json.dumps({"overview": t, "topics": []}, ensure_ascii=False)
    with pytest.raises(ValueError, match="above the forecast"):
        d.clean_hebrew(he("Dollar Tree קפצה על הכאה ברבעון 3."), en)
    assert d.clean_hebrew(he("Dollar Tree קפצה אחרי תוצאות טובות מהצפוי ברבעון 3."), en)


def test_v76b_the_forecast_is_not_the_result(scanner):
    """DELL, 24.11.2026: the site showed EPS 8.40, its forecast for the next quarter; the result was 9.45. Text of the real release."""
    import datetime as _dt
    import data_health as dh
    p = scanner._parse_press_release_eps
    summary = ("Third-Quarter Summary • Record revenue of $54.9 billion, up 103% year over year • Record diluted earnings per share (EPS) of $8.82, "
               "up 287% year over year, and record non-GAAP diluted EPS of $9.45, up 265% • Record third-quarter cash flow. ")
    table = ("Guidance Summary (in billions, except percentages and per share amounts) Fourth-Quarter Guidance Q4FY27 (% Y/Y) Revenue $ 56.3 69 % "
             "GAAP diluted EPS $ 7.90 134 % Non-GAAP diluted EPS $ 8.40 116 % Full-Year Guidance FY27 Previous")
    assert p(summary + table, 6.59) is None                    # the result is too far from the estimate: blank until Yahoo has it
    assert p(table, 6.59) is None                              # a guidance table alone
    assert p(summary + table, 9.0) == 9.45                     # the result fits: taken
    assert p("Third-Quarter Results (in millions) Revenue $ 56.3 69 % Non-GAAP diluted EPS $ 8.40 116 %", 6.59) == 8.40
    assert p("Company Raises Guidance. Third Quarter Highlights: Non-GAAP EPS $ 2.10, up 4%", 2.0) == 2.10

    class Cur:
        def __init__(self, rel): self.rel = rel
        def execute(self, *a): pass
        def fetchone(self): return {"ok": True}
        def fetchall(self):
            return [{"ticker": "DELL", "status": "found", "eps_actual": 8.4, "rel": self.rel,
                     "items": [{"metric": "eps", "period": "quarter", "quarter": 4, "fiscal_year": 2027, "low": 8.4, "high": 8.4, "est": 6.7424, "vs_pct": 24.58, "quote": "x"}]}]
    now = _dt.datetime(2026, 11, 25, 12, 30, tzinfo=_dt.timezone.utc)
    line = dh.check_guidance(Cur(True), now)[0]
    assert line["status"] == "warn" and "equals the forecast" in line["detail"] and "DELL" in line["detail"]
    assert dh.check_guidance(Cur(False), now)[0]["status"] == "ok"


def test_v76_headlines_of_24_11_evening():
    import news_links as nl
    import translation_fixes as tf
    for t in ("Medtronic (MDT) Q2 Earnings: Taking a Look at Key Metrics Versus Estimates", "Dycom Industries (DY) Reports Q3 Earnings: What Key Metrics Have to Say",
              "Globe Life (GL) Stock Could Be Undervalued On Its Earnings Strength", "After Earnings Beat, Can Dollar Tree Make You Some Cash?",
              "Are Utilities Stocks Lagging WEC Energy Group (WEC) This Year?", "Is MediaAlpha (MAX) Outperforming Other Business Services Stocks This Year?",
              "Agilent Q4 Earnings Call Maps Out Growth From Lab Upgrades", "Perimeter Medical Imaging AI Q3 GAAP EPS of $0.02, revenue of $0.6M",
              "Germany Eyes EU Sanctions to Bar Russia From Hockey Championship", "Could Russia-Ukraine war threaten Libya’s fragile reunification process?",
              "Brazil's tax revenue extends record run in October, buoyed by higher oil prices",
              "IREN Fell 42% This Month: This Wall Street Pro Maintains That 440% Returns Are Coming",
              "Rate Hike Risks and PCE Loom Over QQQ as Options Volatility Stays Elevated | Options Market Statistics"):
        assert nl.is_unwanted_headline(t), t
    for t in ("Dell Non-GAAP EPS of $2.59 beats by $0.10, revenue of $27.0B", "Is the Fed lagging behind inflation?", "Tech stocks are outperforming the S&P 500 this year",
              "Libya oil output falls as field shuts down", "US sanctions Russian oil tankers", "Nvidia earnings call shakes chip stocks",
              "Snowflake adjusted EPS of $0.30, revenue of $1.2B", "Why Are Oil Prices Falling to 3-Month Lows?"):
        assert not nl.is_unwanted_headline(t), t
    strip = nl.strip_outlet_suffix
    assert strip("Trump Tariffs Return: WSJ - NVIDIA (NASDAQ:NVDA) - Benzinga", "Benzinga") == "Trump Tariffs Return: WSJ"
    assert strip("Trump Tariffs Return: WSJ - NVIDIA (NASDAQ:NVDA)", "Benzinga") == "Trump Tariffs Return: WSJ"
    assert strip("Nvidia (NASDAQ:NVDA) jumps 5%", "Benzinga") == "Nvidia (NASDAQ:NVDA) jumps 5%"
    p = tf.prep_en
    assert p("Wall St opens lower as Nvidia-fueled AI rally fades") == "Wall Street opens lower as Nvidia-driven surge in AI stocks fades"
    assert p("Wall Street falls as the AI rally fades") == "Wall Street falls as the surge in AI stocks fades"
    assert p("Chip Stocks Just Had Their Fourth-Best Day") == "chip stocks Just Had Their Fourth-Best Day"
    assert p("Stocks Slide as Tech Stocks Partly Reverse Nvidia-Sparked Rally").endswith("Rally led by Nvidia")
    assert "considers EU Sanctions" in p("Germany Eyes EU Sanctions to Bar Russia") and "aims for $5 trillion" in p("Apple eyes $5 trillion value")
    for t in ("Even After Big Gains, Insiders Are Loading Up on These 5 Stocks", "These 4 Utility Stocks Pay Dividends While AI Drives Demand",
              "Salesforce (CRM) Stock Looks Cheap With Earnings In Focus", "Macy's Thanksgiving Day Parade balloon inflation 2026: How to watch"):
        assert nl.is_unwanted_headline(t), t
    assert not nl.is_unwanted_headline("GoodRx Holdings Non-GAAP EPS of $0.10 beats by $0.02, revenue of $218.4M beats by $16.31M")
    assert p("GoodRx EPS of $0.10 beats by $0.02, revenue of $218.4M misses by $16.31M") == (
        "GoodRx EPS of $0.10 is $0.02 above estimates, revenue of $218.4M is $16.31M below estimates")
    assert "Open short positions in" in p("Open Short Interest Positions in Nasdaq Stocks") and p("interest rates rise") == "interest rates rise"
    for same in ("All eyes on Nvidia earnings", "Wall Street's eyes turn to the Fed", "Investors keep their eyes open", "Trump rallies support for tariffs"):
        assert p(same) == same, same
    assert "pressure on profit margins" in p("HPE faces margin pressure amid strong AI demand")
    assert p("Gold gains on bargain buying; US inflation data awaited") == "Gold gains on buying at low prices; markets await US inflation data"
    assert p("Asian stocks rally as Huang's return revives hopes").startswith("Asian stocks climb as") and "jumps on Q3" in p("Dell rallies on Q3 results")
    for bad, good in (("למניות צ'יפ היה היום הרביעי הטוב ביותר", "למניות השבבים היה"), ("גרמניה מבטלת סנקציות של האיחוד האירופי", "שוקלת"),
                      ("לחץ שולי על רקע", "לחץ על שולי הרווח על רקע"), ("Wall St נפתח נמוך יותר", "וול סטריט נפתחת נמוך יותר"),
                      ("מניית של אנתרופיק יורדת", "מניית אנתרופיק יורדת"), ("נתוני האינפלציה בארה\"ב ממתינים", "השווקים ממתינים לנתוני האינפלציה")):
        assert good in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad), bad

def test_v77_adjusted_eps_in_real_releases(scanner):
    """Measured on 321 real releases (28.10-24.11.2026) against Yahoo: right 130 -> 179, wrong 10 -> 3 (and those 3 are the company's
    adjusted figure where Yahoo holds GAAP: AMRZ, UBER, WSM). The sentences are the companies' own."""
    p = scanner._parse_press_release_eps
    for want, est, text in (
            (2.82, 2.74, "impacted by restructuring expenses of $0.21 per share. Non-GAAP diluted net income per share was $2.82, compared to non-GAAP diluted net income per share of $2.32 in the same period last year."),
            (0.83, 0.74, "• Fourth quarter non-GAAP diluted net EPS of $0.83, inclusive of tariff refunds, and down 10.8% from the prior year period"),
            (3.52, 3.55, "• Reported EPS decreased 11.7% to $1.96 • Adjusted EPS (1) increased 10.3% to $3.52 Third Quarter Net Sales Growth"),
            (0.87, 0.74, "Third-Quarter 2026 Reported (4) Diluted EPS of $0.56, and Adjusted (3) Diluted EPS of $0.87 ■ Raises Full-Year"),
            (2.66, 2.20, "EPS of $3.02 increased 57.3%. • Adjusted-diluted EPS of $2.66 increased 35.7%."),
            (0.80, 0.73, "● GAAP Diluted EPS of $0.79, up 61% compared to prior year ● Adjusted Diluted EPS of $0.80, up 74% compared to prior year"),
            (0.80, 0.74, "• Q3 EPS of $0.53 1 ; adjusted 2 EPS of $0.80 1 which grew 19% versus prior year 2026 Outlook • Increasing adjusted EPS guidan ce to $2.97 t o $3.05"),
            (1.90, 1.88, "Third quarter diluted earnings per share were $1.00, a decrease of 13.8%; adjusted 1 diluted earnings per share were $1.90, consistent"),
            (3.52, 3.49, "GAAP net income was $95.1 million, or $2.33 per diluted share, up 12.7%. Non-GAAP net income was $143.4 million, or $3.52 per diluted share, up 10.0%."),
            (4.57, 4.22, "Non-GAAP net income for the first quarter of fiscal year 2027 was $461.1 million, or $4.57 per diluted share."),
            (1.15, 1.19, "Non-GAAP Operating Earnings of $1.15 per share in Q3 2026 and $3.56 per share YTD"),
            (0.78, 0.71, "Diluted EPS increased +78.9% to $1.02 Adjusted EPS was $0.78 which increased +5.5%"),
            (2.21, 1.86, "Earnings per share 2,3 $2.29 Adjusted earnings per share 1,2,3 $2.21 Cash flow"),                 # was 2.29
            (2.21, 1.82, "and adjusted diluted EPS (1) grew 78% to $2.21 compared to third quarter 2025. Adjusted diluted EPS (1) $2.22 - $2.28 Adjusted diluted EPS growth"),
            (0.44, 0.35, "Third quarter 2026 diluted earnings per share and Adjusted Diluted EPS¹ were $0.22 and $0.44, respectively."),   # was 0.22
            (1.86, 1.85, "Adjusted Earnings Per Share 5 $1.62 $1.86 15% $6.01 $6.59 10%"),                                   # was 1.62, last year's
            (0.49, 0.40, "Adjusted EPS (Diluted) $ 0.45 $ 0.49 $ 0.04 $ 0.96"),
            (1.05, 1.00, "Adjusted diluted earnings per share5 $ 1.05 $ 1.02 2.9% $ 1.78"),                                  # this year first: stays
            (None, 2.77, "Adjusted earnings per share up 22% to $2.85 , excluding total notable items"),
            # Zoom: the only figure in this shape is last year's
            (None, 1.49, "Third Quarter of Fiscal Year 2027 results. Non-GAAP net income for the third quarter of fiscal year 2026 was $462.8 million, or $1.52 per share."),
            (None, 1.49, "In the third quarter of fiscal year 2026, non-GAAP net income was $462.8 million, or $1.52 per share.")):
        assert p(text, est) == want, (want, p(text, est), text)


def test_v77_a_release_figure_is_read_again(scanner, monkeypatch):
    """DELL: 8.40 (the forecast) stayed on the site for hours after the reader was fixed."""
    import datetime as _dt
    answers = {}
    monkeypatch.setattr(scanner, "_read_release_eps", lambda t, d, e: answers[t])
    d = _dt.date(2026, 11, 24)
    answers.update(GONE=(None, True), DOWN=(None, False), SAME=(3.2, True), FIXED=(9.45, True))
    assert scanner.recheck_release_eps("GONE", d, 6.59, 8.4) == "clear"
    assert scanner.recheck_release_eps("DOWN", d, 6.59, 8.4) == "same"          # the SEC did not answer: nothing changes
    assert scanner.recheck_release_eps("SAME", d, 3.07, 3.2) == "same"
    assert scanner.recheck_release_eps("FIXED", d, 9.0, 8.4) == 9.45


def test_v77_the_release_could_not_be_read(scanner, monkeypatch):
    import datetime as _dt
    d = _dt.date(2026, 11, 24)
    monkeypatch.setattr(scanner, "_sec_cik_for", lambda t: 1)
    monkeypatch.setattr(scanner, "_sec_release_accessions", lambda c, dd: ["a"])
    monkeypatch.setattr(scanner, "_sec_release_text", lambda c, a: "no figure here")
    assert scanner._read_release_eps("X", d, 1.0) == (None, True) and scanner._eps_from_press_release("X", d, 1.0) is None
    monkeypatch.setattr(scanner, "_sec_release_text", lambda c, a: None)
    assert scanner._read_release_eps("X", d, 1.0) == (None, False)
    monkeypatch.setattr(scanner, "_sec_release_text", lambda c, a: "Adjusted EPS of $1.05 for the quarter")
    assert scanner._read_release_eps("X", d, 1.0) == (1.05, True)

    def down(*a):
        raise OSError("SEC")
    monkeypatch.setattr(scanner, "_sec_release_accessions", down)
    assert scanner._read_release_eps("X", d, 1.0) == (None, False)


def test_v77_part_of_the_revenue_is_not_the_revenue():
    """Workday, 24.11.2026: subscription revenue against the consensus of all revenue, shown as -6.4% while the forecast was raised."""
    import earnings_guidance as eg
    item = lambda q, vs=-6.4, metric="revenue", est=2778376840.0: {"metric": metric, "period": "quarter", "quarter": 4, "fiscal_year": 2027, "est": est,
                                                                   "low": 2.6e9, "high": 2.6e9, "basis": None, "quote": q, "change": None, "vs_pct": vs}
    w = [item("Subscription revenues of $2.600 billion, representing growth of 10%")]
    got = eg.fair_comparisons(w)
    assert got[0]["part"] == "subscription" and got[0]["est"] is None and got[0]["vs_pct"] is None and "subscription" in got[0]["unfair"]
    assert "part" not in w[0] and eg.fair_comparisons(got) == got
    assert eg.fair_comparisons([item("subscription revenue growth of 13%", None, "revenue_growth", None)])[0]["part"] == "subscription"
    assert eg.fair_comparisons([item("Cloud revenue of $2.6 billion")])[0]["part"] == "cloud"
    for q in ("Contract revenues $7.66 billion to $7.73 billion", "Total revenues of $2.6 billion, of which subscription revenues of $2.4 billion",
              "Net sales of $5.7 billion to $5.8 billion", "Revenue is expected to be between $70.0 million and $75.0 million", "products and services revenue of $2 billion"):
        assert eg.fair_comparisons([item(q)]) == [item(q)], q


def test_v77_headlines_of_25_11():
    import news_links as nl
    import translation_fixes as tf
    for t in ("Ahead of Polestar Earnings, Here's What Barchart Data Says Comes Next for PSNY Stock", "GDS Holdings And 2 Other AI Infrastructure Stocks",
              "AeroVironment, Inc. to Announce Second Quarter of Fiscal Year 2027 Earnings and Host Conference Call", "THOR Industries to Release 2027 Q1 Earnings on Dec. 8",
              "MIND Technology Announces Fiscal 2027 Third Quarter Earnings Release and Conference Call Schedule", "Medtronic plc Q2 2027 Earnings Call Summary",
              "Strapped for cash, a divided St. Marie clashes over water rate hike", "PNM requests new rate hike months after the last increase went into effect",
              "ICC approves $74 million rate hike for Illinois American Water Co. Customers", "Henderson commissioners vote in favor of utility rate hike",
              "If You Invest $5,000 Into These 3 Stocks, You Could Have $10,000 by 2030", "Gas Prices Are Weak, but EQT (EQT) Made Our List of Dirt-Cheap Stocks for 2027",
              "Josh Brown's best stocks in the market: SharkNinja, Target and eBay", "Jim Cramer sizes up six retail stocks after earnings — and names his two favorites",
              "Most Stocks Are Losers. Here's How You Can Still Win", "Jim Cramer and Tom Lee are Buying More AI Stocks. Are They Right?",
              "Watch Retailers Post Strong Earnings Ahead of Holidays", "META Stock Quote Price and Forecast", "Caleb Martin posts 13 points, seven ‘stocks’",
              "UNL Husker Pantry stocks up ahead of holiday closures", "How the Federal Reserve Bank stays connected to you", "Tax Interest Rates To Rise In 1st Quarter",
              "Li Auto (LI) Could Be 29% Below Fair Value On Q3 Earnings Watch", "Learn Why The Bull Case For IQVIA Stock Could Change Following Earnings Beat",
              "Is MiniMed Group (MMED) A Bargain As Earnings Recover And Flex Launches?", "Earnings Upgrades Could Be A Big Deal For EOG Stock",
              "Poste Italiane (BIT:PST) Reports Stronger Q3 Earnings", "SanDisk Trades at 4.6x Earnings—And Still Got Cut in Half",
              "NRR: Earnings and rents grew, while H2 disposals target a return toward sub-40% LTV", "Dimeco announces third-quarter earnings",
              "GoodRx Holdings, Inc. reports results for the quarter ended September 30 - Earnings Summary"):
        assert nl.is_unwanted_headline(t), t
    for t in ("U.S. Stocks Mixed as Huang Relief Rally Fades", "Fed rate hike is driving the 'story of the year': Morgan Stanley", "Utility stocks fall as rate hike bets build",
              "Dell reports Q3 beat on revenue and earnings, announces $25 billion share buyback", "CPI: Core inflation rose while food fell, and earnings lagged",
              "Nvidia announces $50 billion buyback with third-quarter results", "Workday (WDAY) Tops Q3 Earnings and Revenue Estimates", "Dow jumps 500 points, led by banks",
              "Investors watch tariffs and inflation in Europe", "Gold price falls 2% on hawkish Fed bets", "Illinois Tool Works raises outlook", "Mortgage rate hike hits customers",
              "Berkshire is buying more Apple stock", "Wall Street strategists lay out the bull case for the S&P 500", "Nvidia to report earnings on Wednesday"):
        assert not nl.is_unwanted_headline(t), t
    strip = nl.strip_outlet_suffix
    assert strip("Global stocks mixed | Daily Sabah", "Daily Sabah") == "Global stocks mixed"
    assert strip("Dollar up ahead of inflation data By Reuters - Investing.com", "Investing.com") == "Dollar up ahead of inflation data"
    p = tf.prep_en
    for a, b in (("U.S. Stocks Mixed as Huang Relief Rally Fades", "U.S. Stocks Mixed as Huang rebound Fades"),
                 ("WTI trades below settlement as API reports small U.S. crude build", "WTI trades below the closing price as API reports small U.S. rise in crude oil inventories"),
                 ("Stocks Settle Mostly Lower on a Rout in Chipmakers", "Stocks close Mostly Lower on plunge in Chipmakers"), ("Oil settles at three-month low", "Oil closes at three-month low"),
                 ("Bond Traders Rush to Hedge Risk of Fed Hikes Stalling Economy", "Bond Traders Rush to Hedge risk that Fed interest rate Hikes stall the economy"),
                 ("Gold holds near 15-mth lows on Fed hike bets; PCE inflation awaited", "Gold holds near 15-mth lows on Fed interest rate hike bets; markets await PCE inflation"),
                 ("BOJ's Indicator Backs Case for Another Hike", "BOJ's Indicator Backs Case for Another interest rate hike"),
                 ("Dell reports Q3 beat on revenue and earnings", "Dell reports Q3 revenue and earnings above estimates"), ("Investors sour on Anthropic", "Investors lose faith in Anthropic"),
                 ("Workday (WDAY) Tops Q3 Earnings and Revenue Estimates", "Workday Inc (WDAY) reports Q3 Earnings and Revenue above estimates"),
                 ("Ambarella (AMBA) Misses Q3 Earnings and Revenue Estimates", "Ambarella (AMBA) reports Q3 Earnings and Revenue below estimates"),
                 ("Autodesk shares fall despite strong guidance", "Autodesk shares fall despite strong outlook"), ("Traders Eye US Inflation", "Traders watch US Inflation"),
                 ("Aussie slumps before inflation release", "Australian dollar slumps before inflation data"), ("Dell stock tanks after blowout earnings", "Dell stock plunges after very strong earnings")):
        assert p(a).lower() == b.lower(), (p(a), b)
    for same in ("Fed hikes rates by a quarter point", "Fed forward guidance stays unchanged", "Aussie shares end lower", "Traders settle lawsuit with the SEC", "All eyes on the Fed"):
        assert p(same) == same, p(same)
    assert tf.half_translated("Dow Jones Futures: Nvidia, Anthropic Lead AI Retreat אבל מגזרים רבים חזקים")
    assert not tf.half_translated("מניות Dick's Sporting Goods ו-Best Buy עולות")
    assert tf.fix_he("Workday Inc (WDAY) מדווחת על רווחים") == "Workday (WDAY) מדווחת על רווחים"


def test_v77b_headlines_of_25_11_afternoon():
    import news_links as nl
    import translation_fixes as tf
    for t in ("How Upcoming Earnings At Deere (DE) Has Changed Its Investment Story", "Forbes Daily: Inflation, Chip Shortages And A $1 Trillion Holiday Season",
              "Macy's Thanksgiving Day Parade Inflation Celebration set as winds could impact balloons", "New Zealand King Salmon posts record earnings",
              "Why Space Stocks Could Be The Next Frontier For AI Investors", "Ten undervalued energy stocks after Q3 earnings, led by Par Pacific",
              "Five materials stocks with attractive valuations following Q3 earnings", "Ingles Markets GAAP EPS of $0.97, revenue of $1.37B",
              "Anthropic, Nvidia, Dell, Workday, Deere, and More Stocks That Explain Today’s Market", "'This is a very difficult time' for more tariffs | Market Talk"):
        assert nl.is_unwanted_headline(t), t
    for t in ("Nasdaq futures slide over 2% as AI losses deepen; inflation data in focus", "Your Thanksgiving Meal Costs More as US Food Inflation Returns",
              "New Zealand central bank raises rates as global inflation rises", "Two Fed officials back a hike as stocks fall", "Seven of 11 S&P sectors fall, led by tech stocks",
              "Five Fed officials say stocks are stretched", "Nvidia results change the story for AI stocks", "Deere (DE) Q4 Earnings Miss Estimates",
              "Ballooning deficit adds to inflation worries", "Dollar is undervalued, Trump adviser says"):
        assert not nl.is_unwanted_headline(t), t
    p = tf.prep_en
    for a, b in (("Stock Sell-Off Set to Continue as A.I. Players Fall Again", "sharp fall in stocks Set to Continue as AI companies Fall Again"),
                 ("Nasdaq futures fall as tech sell-off intensifies", "Nasdaq futures fall as sharp fall in tech stocks intensifies"),
                 ("Bond selloff eases", "sharp fall in Bonds eases"), ("Tech stocks under pressure as AI jitters persist", "Tech stocks under pressure as AI worries persist"),
                 ("Chip stocks fall again as AI trade struggles to regain footing", "chip stocks fall again as AI stocks struggle to recover"),
                 ("Dollar takes breather ahead of U.S. data on Fed path; Aussie jumps on CPI", "Dollar pauses ahead of U.S. data on Fed path; Australian dollar jumps after CPI data"),
                 ("World stocks fall as new US tariffs loom, AI rebound fades", "World stocks fall as new US tariffs approach, AI rebound fades"),
                 ("CleanSpark falls as Revenue Miss and delays weigh", "CleanSpark falls as lower-than-expected revenue and delays weigh"),
                 ("Tariff Risks and Rate Expectations Weigh", "Tariff Risks and interest rate expectations Weigh"), ("Chip Stocks Extend Losses", "chip stocks continue to fall"),
                 ("Fed policymakers at odds over causes of high inflation", "Fed policymakers disagree over causes of high inflation"),
                 ("HP falls as PC Margin Pressure and Cautious Earnings Outlook Overshadow Quarterly Beat",
                  "HP falls as pressure on PC profit margins and cautious profit forecast Overshadow better-than-expected quarter"),
                 ("Inflation report to provide Fed's preferred gauge", "Inflation report will provide Fed's preferred gauge")):
        assert p(a).lower() == b.lower(), (p(a), b)
    for same in ("Fed officials are at odds over rates", "Inflation rate expectations rise", "Profit, revenue miss the consensus", "Tariff risk looms over Europe"):
        assert p(same) == same, p(same)
    for he, want in (("העלייה בתעריפים בארה\"ב מתנשאים", "העלייה בתעריפים בארה\"ב מתקרבים"), ("הדולר האוסטרלי זינק במדד המחירים לצרכן", "הדולר האוסטרלי זינק אחרי נתוני מדד המחירים לצרכן")):
        assert tf.fix_he(he) == want and tf.fix_he(want) == want
    for bad, good in (("המניות בארה\"ב התערבבו כשעצרת הסיוע של הואנג דועכת", "כשהריבאונד של הואנג דועך"), ("WTI נסחרת מתחת להתנחלות מכיוון", "מתחת למחיר הסגירה"),
                      ("ממהרים לגדר את הסיכון לעליית עליות הפד בכלכלה", "העלאות ריבית של הפד"), ("המשקיעים מחממים על אנתרופיק", "מאבדים אמון באנתרופיק")):
        assert good in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad), bad


def test_v77c_cut_translation_and_headlines_of_26_11():
    import news_links as nl
    import translation_fixes as tf
    en = "Hawkish Fed risks deeper market selloff as real yields climb, Brooks warns"
    assert tf.cut_short(en, "ברוקס מזהיר")
    for he in ("ברוקס מזהיר כי הפד הניצי מסתכן במכירת שוק עמוקה יותר כאשר התשואות האמיתיות מטפסות",
               "האם הפד עושה טעות ענקית בהעלאת הריבית?"):
        assert not tf.cut_short(en if "ברוקס" in he else "Is the Fed making a huge mistake in raising interest rates?", he), he
    assert not tf.cut_short("Oil rises", "הנפט עולה") and not tf.cut_short(en, en)     # short text; no Hebrew at all = another check
    for t in ("Sanmina (SANM) Stock Still Looks Like A Bargain On Earnings",
              "President Trump Just Hit Stock Market Investors With Bad News at a Bad Time",
              "【The First Insurance FY2026 Q3 Earnings Call】Underwriting Profit Surges 68.9%, Lifting Pre-Tax Net Income to NT$1.369 Billion"):
        assert nl.is_unwanted_headline(t), t
    for t in ("Trump hits investors with new tariffs on China", "Gold drifts lower as Fed rate-hike fears loom", "Asian stocks enjoy another bounce as tech holds court",
              "Hawkish Fed risks deeper market selloff as real yields climb, Brooks warns", "EUR/JPY Steadies as Markets Await Tokyo Inflation Data"):
        assert not nl.is_unwanted_headline(t), t
    p = tf.prep_en
    for a, b in (("Hawkish Fed sentiment coincides with sharp drop in S&P 500 valuation", "expectations of a hawkish Fed coincides with sharp drop in S&P 500 valuation"),
                 ("Hawkish Fed risks deeper selloff", "a hawkish Fed risks deeper selloff"), ("A hawkish Fed risks more", "A hawkish Fed risks more"),
                 ("Asian stocks enjoy another bounce as tech holds court", "Asian stocks enjoy another bounce as tech stocks lead"),
                 ("Tech billionaire takes his case against red tape to the Federal Reserve", "Tech billionaire takes his case against bureaucracy to the Federal Reserve"),
                 ("Gold drifts lower as Fed rate-hike fears loom", "Gold falls slightly as Fed interest rate hike fears grow"), ("Dollar drifts higher", "Dollar rises slightly")):
        assert p(a).lower() == b.lower(), (p(a), b)
    for same in ("Gold price falls 2% on hawkish Fed bets, US inflation data in focus", "Hawkish Fed comments put PCE inflation in focus."):
        assert p(same) == same, p(same)
    for bad, good in (("סנטימנט הפד ההוקיש עולה בקנה אחד", "הציפיות לפד נצי"), ("מניות אסיה נהנות מקפיצה נוספת כשהטכנולוגיה מחזיקה במשפט", "כשמניות הטכנולוגיה מובילות"),
                      ("הזהב נסחף נמוך יותר כאשר החששות מהעלאת הריבית של הפד מתנשאים", "הזהב יורד מעט כאשר החששות מהעלאת הריבית של הפד גוברים")):
        assert good in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad), bad


def test_v78_home_page_hides_headlines_at_the_bottom_of_the_score():
    import news_links as nl
    import ai_digest as ad
    low = (("Alabama Power Rate Cut | Cullman Today Shorts", "The Cullman Tribune", "מאקרו"),
           ("Bitrue Research Institute Publishes Review of Five AI Trading Bot Platforms in 2026", "Investing.com", "AI"),
           ("Meat woes force Orior to cut annual sales and earnings outlook", "www.just-food.com", "דוחות"),
           ("Why Is Nucor (NUE) Down 8.4% Since Last Earnings Report?", "Yahoo Finance", "דוחות"))
    for t, s, tag in low:
        assert nl.home_score(t, s, tag) < nl.HOME_MIN_SCORE or nl.is_unwanted_headline(t), t
    # the owner (26.11.2026): one company's report with its result or move stays on the home page
    for t in ("Deere Shares Fall 3.8% as Fourth-Quarter Earnings Miss Expectations", "Workday Shares Fall 10.2% as Slowing Subscription Growth Overshadows",
              "Autodesk Q3 Earnings Beat on Strong Renewals, Revenues Rise Y/Y"):
        assert nl.home_score(t, "Yahoo Finance", "דוחות") >= nl.HOME_MIN_SCORE and not nl.is_unwanted_headline(t), t
    for t in ("Why Is Nucor (NUE) Down 8.4% Since Last Earnings Report?", "HLBANK: Q1 profit rose 4.6% as associate earnings and lower loan impairments supported results",
              "AURA SMART AIR LTD Rights 2026 - 27.11.26 For shares Earnings and Revenue – TASE:AUSA.R1"):
        assert nl.is_unwanted_headline(t), t
    kept = (("Ukraine says five jets damaged in attack on Russia’s Savasleyka airbase", "Al Jazeera", "גאופוליטי"),
            ("Europe markets climb as easing AI fears lift investor sentiment", "Seeking Alpha", "שוק"),
            ("BOJ's Tamura signals chance of faster-pace interest rate hikes", "Yahoo Finance", "ריבית"),
            ("Oil Prices Rise as Renewed Middle East Attacks Threaten Persian Gulf Supply Recovery", "Yahoo Finance", "אנרגיה"),
            ("Trump, NATO chief caution Russia as attacks on Ukraine’s energy infrastructure intensify", "CNBC", "גאופוליטי"),
            ("Nvidia shipment halt threatens to stall the AI rally", "CoinDesk", "AI"),
            ("Airlines strained by Iran-war fuel price shock", "Reuters", "אנרגיה"))
    for t, s, tag in kept:
        assert nl.home_score(t, s, tag) >= nl.HOME_MIN_SCORE, t
    # the daily summary's score is the same function (moved, not changed, except the two additions of v78)
    it = {"title": "Deere Q4 Earnings Miss Estimates on Large Agriculture Weakness", "source": "Yahoo Finance", "tag": "דוחות", "outlets": 1}
    assert ad.item_score(it) == nl.headline_score(it["title"], it["source"], it["tag"]) == -4


def test_v78b_pares_losses_and_holiday_headlines():
    import news_links as nl
    import translation_fixes as tf
    p = tf.prep_en
    for a, b in (("Asian shares rebound led by AI stocks after Wall Street pares sharp losses", "Asian shares rebound led by AI stocks after Wall Street cuts sharp losses"),
                 ("Sterling Steady as Dollar Pauses; BoE Rate Hike Bets Rise", "British pound holds steady as the dollar pauses; traders raise bets on a BoE interest rate hike"),
                 ("Gold slips as Fed rate-hike bets rise", "Gold falls slightly as traders raise bets on a Fed interest rate hike"),
                 ("investingLive European session wrap: European stocks ride Wall Street's comeback", "Summary of the European trading session: European stocks ride Wall Street's comeback"),
                 ("Stocks pared gains", "Stocks cut gains")):
        assert p(a).lower() == b.lower(), (p(a), b)
    for same in ("Sterling Bancorp reports results", "Dollar steady", "The Sterling Silver market"):
        assert p(same) == same, p(same)
    for t in ("HP (HPQ) Stock May Be 42% Undervalued After Earnings Focus Returns",
              "Warren Buffett vs. Greg Abel: 1 Berkshire CEO Called Buying Airline Stocks a ‘Mistake.’ The Other Keeps Buying This Ticker."):
        assert nl.is_unwanted_headline(t), t
    for o in ("Barchart.com", "BigGo Finance", "finance.biggo.com", "Bioengineer.org"):
        assert nl.is_advice_outlet(o), o
    bad = "מניות אסיה התאוששו בהובלת מניות בינה מלאכותית לאחר שוול סטריט ספגה הפסדים חדים"
    assert "צמצמה הפסדים" in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad)
    assert tf.fix_he("וול סטריט ספגה הפסדים חדים ביום שישי") == "וול סטריט ספגה הפסדים חדים ביום שישי"
    boe = "הפאונד הבריטי יציב כשהדולר עוצר; העלאת הריבית של BoE ההימורים עולים"       # v78c, the row saved on 26.11
    assert tf.fix_he(boe) == "הפאונד הבריטי יציב כשהדולר עוצר; סוחרים מעלים הימורים על העלאת ריבית של BoE"


def test_v77c_cut_translation_shown_in_english(api):
    en = "Hawkish Fed risks deeper market selloff as real yields climb, Brooks warns"
    r = api._tidy_news_row({"summary_en": en, "summary_he": "ברוקס מזהיר", "source_name": "Seeking Alpha"})
    assert r["summary_he"] == en
    ok = api._tidy_news_row({"summary_en": "Oil Prices Rise as Renewed Middle East Attacks Threaten Supply", "summary_he": "מחירי הנפט עולים כאשר התקפות מחודשות במזרח התיכון מאיימות על האספקה", "source_name": "Reuters"})
    assert ok["summary_he"].startswith("מחירי הנפט")


def test_v77b_headlines_of_the_holiday_26_11():
    import news_links as nl
    import translation_fixes as tf
    for t in ("Recruiters Lured Brazilians to Fight Russia’s War, Then Stole Their Money", "Could EU delisting of Chinese firm pave way for wider sanctions relief?",
              "Sen. Tim Kaine, D-Va., shares why he opposes Trump's proposed tariffs", "‘War Is Hell:’ A Foreign Soldier Writes Home From Ukraine",
              "Holiday staples still surging ahead of Thanksgiving dinner. Economist shares how to save on the spread",
              "Taiwan Stocks Rebound 956 Points, but Foreign Investors Still Pull NT$49.18 Billion; Taiwan Glass Hit with 81,000-Share Selloff",
              "Canadian inflation could end the year below Bank of Canada forecast under renewed 2% target",
              "Corcept Therapeutics (NASDAQ:CORT): A Growth-at-a-Reasonable-Price Stock With Strong Forward Earnings Potential",
              "Opinion | The Next Fed Leader Should Be a Bot", "What CMC Markets (CMCX) Said on Its H1 Earnings Call",
              "Rwanda hikes rates for fourth time this year with inflation far above target",
              "India swaps, treasury bills signal possibility of outsized RBI rate hike next week"):
        assert nl.is_unwanted_headline(t), t
    for t in ("Trump administration proposes new tariffs on goods from major U.S. trading partners", "Stocks climb as traders shrug off latest US tariff salvo",
              "Hawkish Bank of Japan member seeks quick rate hikes on inflation", "OPEC+ Considers Sticking With Current Oil Quotas, Delegates Say",
              "Bitcoin’s bear market is getting old, but may not be done", "Will the Fed hike in December?",
              "Can Iran Withstand Trump’s Naval Blockade as Inflation and Food Prices Soar?",
              "Michael Burry Sells Remaining Nasdaq-100 Puts, Eyes Fresh Nvidia Short Near $200: ‘I Am Long Undervalued Common Stocks’","Australian dollar slides as RBA holds rates",
              "Bank of Canada holds rates as US tariffs weigh", "Treasury bills rally as Fed cut bets rise", "European stocks rally as tech, bank stocks rebound, tariff fears ease",
              "UK tells 100,000 military reservists to prepare for war amid Russia threats in first since Cold War"):
        assert not nl.is_unwanted_headline(t), t
    assert nl.is_advice_outlet("Investorideas.com") and nl.is_advice_outlet("ChartMill") and not nl.is_advice_outlet("Reuters")
    p = tf.prep_en
    for a, b in (("Bitcoin’s bear market is getting old, but may not be done", "Bitcoin’s bear market is getting old, but may not be over"),
                 ("UK tells reservists to prepare for war amid Russia threats in first since Cold War", "UK tells reservists to prepare for war amid Russia threats, the first time since Cold War"),
                 ("Stocks climb as traders shrug off latest US tariff salvo", "Stocks climb as traders ignore latest US round of tariffs"),
                 ("Hawkish Bank of Japan member seeks quick rate hikes on inflation", "Bank of Japan policymaker with a hawkish stance seeks quick interest rate hikes on inflation"),
                 ("Bank of Japan's Tamura Eyes December Rate Hike, to Monitor Stock Slump", "Bank of Japan policymaker Tamura considers December interest rate hike, to Monitor stock market slump"),
                 ("European stocks rally as tech, bank stocks rebound", "European stocks climb as tech stocks and bank stocks rebound"),
                 ("FTSE 100 Dip Over Tech Stocks Selloff", "FTSE 100 Dip Over sharp fall in Tech stocks")):
        assert p(a).lower() == b.lower(), (p(a), b)
    for same in ("Bank of Japan's December decision looms over yen", "Bank of England's Rate Decision", "The deal is done", "Inflation is not done yet"):
        assert p(same) == same, p(same)
    for bad, good in (("ממשל טראמפ מציע מכסים חדשים על סחורות משותפות סחר גדולות בארה\"ב", "סחורות של שותפות הסחר הגדולות של ארה\"ב"),
                      ("חבר בבנק הוקיש ביפן מבקש העלאות ריבית", "חבר ניצי בבנק יפן"), ("שוק דובי של ביטקוין מזדקן, אבל אולי לא נעשה", "אולי לא נגמר"),
                      ("המניות מטפסות כאשר סוחרים מתנערים מהמחסום האחרון בארה\"ב", "מתעלמים מסבב המכסים האחרון של ארה\"ב")):
        assert good in tf.fix_he(bad) and tf.fix_he(tf.fix_he(bad)) == tf.fix_he(bad), bad


def test_v79_digest_tells_how_the_session_went():
    """v79 (owner, 26.11.2026): on 25.11 the S&P 500 opened 1.96% lower, was down 3.21%, and closed 0.25% lower (+1.74% from its
    open; the Nasdaq +2.78%). The digest said "the S&P 500 fell 0.25%". Real bars of 19, 20 and 25.11.2026."""
    import datetime as _dt
    import json as _json
    from zoneinfo import ZoneInfo
    import ai_digest as d
    import data_health as dh
    D, et = _dt.date, ZoneInfo("America/New_York")
    sp = d.session_path(7201.1, (7060.3, 7199.3, 6970.1, 7182.8))
    assert sp["open_pct"] == pytest.approx(-1.96, abs=0.011) and sp["low_pct"] == pytest.approx(-3.21, abs=0.011)
    assert sp["close_pct"] == pytest.approx(-0.25, abs=0.011) and sp["from_open_pct"] == pytest.approx(1.74, abs=0.011)
    assert d.day_shape({"^GSPC": sp}) == "rebound"
    assert d.day_shape({"^IXIC": d.session_path(23733.5, (22948.5, 23627.4, 22632.4, 23585.8))}) == "rebound"
    assert d.day_shape({"^GSPC": d.session_path(7175.8, (7150.4, 7198.7, 6882.7, 6910.4))}) is None      # 20.11: a plain fall
    assert d.day_shape({"^GSPC": d.session_path(7323.6, (7389.3, 7415.6, 7173.6, 7175.8))}) == "fade"    # 19.11: up 1.26%, closed -2%
    closes = {"^GSPC": [(D(2026, 11, 24), 7201.1), (D(2026, 11, 25), 7182.8)], "^IXIC": [(D(2026, 11, 24), 23733.5), (D(2026, 11, 25), 23585.8)]}

    def hist(symbol):
        if symbol not in closes:
            raise RuntimeError("not in this test")
        return closes[symbol]

    class NoTables:
        def cursor(self):
            return self
        def execute(self, *a):
            raise RuntimeError("no table")
        def rollback(self): pass
        def close(self): pass
    real, real_ohlc = d._history, dict(d._OHLC)
    d._history = hist
    d._OHLC.update({"^GSPC": {D(2026, 11, 25): (7060.3, 7199.3, 6970.1, 7182.8)}, "^IXIC": {D(2026, 11, 25): (22948.5, 23627.4, 22632.4, 23585.8)}})
    try:
        m = d.market_facts(NoTables(), D(2026, 11, 25), now=_dt.datetime(2026, 11, 25, 20, 0, tzinfo=et))
    finally:
        d._history = real
        d._OHLC.clear()
        d._OHLC.update(real_ohlc)
    assert m["move"] == "normal" and m["shape"] == "rebound" and d.big_day(m) and [p["symbol"] for p in m["path"]] == ["^GSPC", "^IXIC"]
    text = d._market_text(m, D(2026, 11, 25))
    assert "TURNAROUND UP" in text and "low -3.21%" in text and "+1.74% from its open" in text and "SIZE OF THE DAY" not in text
    items = [dict(id=1, title="Wall Street claws back from steep losses as tech stocks rebound", tag="שוק", url="https://x.example/1", title_he="", body=""),
             dict(id=2, title="Trump proposes new tariffs on China", tag="מכסים", url="https://x.example/2", title_he="", body="")]
    answer = lambda overview: _json.dumps({"overview": overview, "why": d.NO_REASON, "why_items": [], "topics": [
        {"title": "Stocks claw back from steep losses", "summary": "Wall Street clawed back from steep losses as tech stocks rebounded.",
         "impact": "", "items": [1]}]})
    flat = "The S&P 500 fell 0.25% and the Nasdaq fell 0.62%."
    with pytest.raises(ValueError, match="turnaround"):
        d.clean_answer(answer(flat), items, m)
    good = d.clean_answer(answer("The S&P 500 fell as much as 3.21% and then recovered to close 0.25% lower."), items, m)
    assert good["overview_en"].startswith("The S&P 500 fell as much as 3.21%")
    saved = d.clean_answer(answer(flat), items, m, strict=False)
    assert saved["overview_en"].startswith("The S&P 500 fell as much as 3.21% during the session and then recovered: it closed 0.25% lower")
    assert any("turnaround" in p for p in dh.digest_problems({"overview_en": flat, "market": m}))
    assert dh.digest_problems({"overview_en": good["overview_en"], "why_en": d.NO_REASON, "market": m}) == []
    # the day's own path is not "an earlier day's story", on a turnaround day only
    up = dict(m, indices=[dict(i, change_pct=1.5) if i["symbol"] == "^IXIC" else i for i in m["indices"]])
    assert d.contradicts_market("The Nasdaq fell as much as 4.64% at the open.", up) is None
    assert d.contradicts_market("The Nasdaq fell 4.64%.", up)
    assert d.contradicts_market("The Nasdaq fell as much as 4.64% at the open.", dict(up, shape=None))


def test_v79_digest_before_the_open_and_the_days_earnings():
    """v79 (owner): "before the open there can be interesting news, and the summary only told yesterday's moves"; and one line
    about the day's notable reports."""
    import datetime as _dt
    import json as _json
    from zoneinfo import ZoneInfo
    import ai_digest as d
    D, et = _dt.date, ZoneInfo("America/New_York")
    assert d.prev_session(D(2026, 11, 27)) == D(2026, 11, 25) and d.prev_session(D(2026, 11, 30)) == D(2026, 11, 27)
    deere = {"ticker": "DE", "name": "Deere", "eps": 4.1, "eps_estimate": 3.9, "revenue_surprise_pct": 3.1, "move": {"state": "pre", "pct": 4.1}}
    m = {"day": "2026-11-27", "session_day": "2026-11-25", "same_day": False, "status": "pre_open", "move": "closed", "last_move": "normal",
         "indices": [{"symbol": "^GSPC", "name_en": "S&P 500", "name_he": "S&P 500", "close": 7182.8, "change_pct": -0.25, "change_points": -18.3}],
         "before_open": {"futures": [{"symbol": "ES=F", "name_en": "S&P 500 futures", "name_he": "חוזי S&P 500", "change_pct": 0.67}],
                         "events": [{"time_et": "08:30", "name_en": "Initial Jobless Claims", "actual": "215K", "forecast": "220K", "previous": None}],
                         "reports": [deere]}}
    text = d._market_text(m, D(2026, 11, 27))
    assert "S&P 500 futures +0.67%" in text and "08:30 Initial Jobless Claims (actual 215K, forecast 220K)" in text and "TODAY COMES FIRST" in text
    assert "Deere (DE): EPS 4.10 against a forecast of 3.90, revenue 3.1% above the forecast, shares +4.10% before the open" in text
    items = [dict(id=1, title="Deere shares jump premarket after results top forecasts", tag="דוחות", url="https://x.example/1", title_he="", body="")]
    ok = d.clean_answer(_json.dumps({"overview": "S&P 500 futures rose 0.67% before the open, and Deere shares rose 4.1% before the open.",
                                     "topics": [{"title": "Deere results", "summary": "Deere shares rose before the open after its results.",
                                                 "impact": "", "items": [1]}]}), items, m)
    assert len(ok["topics"]) == 1
    # the share moves
    c = [(D(2026, 11, 24), 100.0), (D(2026, 11, 25), 110.0)]
    at = lambda day, h, mi=0: _dt.datetime(2026, 11, day, h, mi, tzinfo=et)
    assert d.reaction(c, [], D(2026, 11, 24), D(2026, 11, 25), at(26, 1)) == {"state": "closed", "pct": 10.0}
    assert d.reaction(c, [], D(2026, 11, 24), D(2026, 11, 25), at(25, 11)) == {"state": "live", "pct": 10.0}
    assert d.reaction(c[:1], [(at(25, 8), 104.0)], D(2026, 11, 24), D(2026, 11, 25), at(25, 8, 30)) == {"state": "pre", "pct": 4.0}
    assert d.reaction(c, [(at(25, 15, 45), 110.0), (at(25, 17, 30), 99.0)], D(2026, 11, 25), D(2026, 11, 26), at(25, 18)) == {"state": "post", "pct": -10.0}
    assert d.reaction(c, [], D(2026, 11, 20), D(2026, 11, 25), at(26, 1)) is None          # no close before the report
    # the rows: since the last close (reacting today) / after today's close
    rows = [("DE", D(2026, 11, 25), "bmo", 1.2e11, "Deere & Company", 4.1, 3.9, 5.13, 1.0e10, 9.7e9, 3.09),
            ("ADSK", D(2026, 11, 24), "amc", 6e10, "Autodesk, Inc.", 3.2, 2.9, 10.3, 1.8e9, 1.7e9, 5.9),
            ("HPQ", D(2026, 11, 25), "amc", 3e10, "HP Inc.", 0.83, 0.8, 3.75, None, 1.4e10, None),
            ("HMC", D(2026, 11, 25), "bmo", 4e10, "Honda Motor Co., Ltd.", 1.0, 0.9, 11.1, 5.8e12, 5.5e12, 5.4)]

    class Conn:
        def cursor(self):
            return self
        def execute(self, sql, params):
            assert params == (d.EARN_MIN_CAP, D(2026, 11, 25), D(2026, 11, 24))
        def fetchall(self):
            return rows
        def close(self): pass
    e = d.earnings_rows(Conn(), D(2026, 11, 25))
    assert [r["ticker"] for r in e["reacted"]] == ["DE", "ADSK", "HMC"] and [r["ticker"] for r in e["tonight"]] == ["HPQ"]
    assert [r["name"] for r in e["reacted"]] == ["Deere", "Autodesk", "Honda Motor"]
    assert e["reacted"][0]["revenue_surprise_pct"] == 3.1 and e["reacted"][2]["revenue_surprise_pct"] is None   # yen, not dollars
    assert e["tonight"][0]["revenue_surprise_pct"] is None and e["tonight"][0]["eps_surprise_pct"] == 3.8
    kept = d.notable_earnings({"reacted": e["reacted"] * 3, "tonight": []})
    assert len(kept["reacted"]) == d.EARN_SHOWN and kept["reacted_more"] == 3 and kept["tonight"] == []
    assert d.notable_earnings({"reacted": [], "tonight": []}) is None

def test_v79_stock_lookup_runs_its_parts_together(api, monkeypatch):
    """v79 (owner: "it takes a while"): a first visit to a stock took 5.2 s, its parts one after another (.info tried and
    blocked, two database reads, up to 2.5 s for a description). Now they run together and .info is not asked."""
    import sys as _sys
    import time as _t
    import pandas as pd
    if _sys.platform == "emscripten":
        pytest.skip("no threads in the browser check; GitHub Actions runs it")
    idx = pd.date_range("2026-11-20", periods=4, freq="D")
    hist = pd.DataFrame({"Open": [1, 2, 3, 4.0], "High": [2, 3, 4, 5.0], "Low": [0.5, 1, 2, 3.0], "Close": [1.5, 2.5, 3.5, 4.5],
                         "Volume": [10, 10, 10, 10]}, index=idx)
    slow = lambda value, sec: (lambda *a: (_t.sleep(sec), value)[1])
    monkeypatch.setattr(api, "_lookup_history", slow((hist, None), 0.4))
    monkeypatch.setattr(api, "_next_report", slow({"date": None}, 0.4))
    monkeypatch.setattr(api, "_fund_peek_safe", slow(None, 0.4))
    monkeypatch.setattr(api, "_sec_fallback_description", slow("late", 3))
    monkeypatch.setattr(api, "get_sp500_info_map", lambda: {"ZZLOOK": {"name": "Test Co", "sector": "Energy"}})

    def no_info(*a):
        raise AssertionError(".info was asked")
    monkeypatch.setattr(api, "_get_info_with_retry", no_info)
    t0 = _t.time()
    out = api.lookup_stock("ZZLOOK")
    assert _t.time() - t0 < 1.5
    assert out["name"] == "Test Co" and out["sector"] == "Energy" and out["description"] is None
    assert out["price"] == 4.5 and out["change_pct"] == 28.57 and out["next_report"] == {"date": None}


def test_v79_ticker_list_for_the_search_box(api, monkeypatch):
    """v79: the site's own stocks with names, for suggestions as you type: the scanner's universe (largest first), then
    the S&P 500 names it lacks; read from the database once in 12 hours, and a cut list is not kept."""
    from contextlib import contextmanager
    calls = {"n": 0}
    rows = [("NVDA", "NVIDIA Corporation"), ("ZETA", None), ("bad ticker!", "x")] + [(f"T{i:03d}", f"Company {i}") for i in range(450)]

    @contextmanager
    def cursor(*a, **k):
        calls["n"] += 1

        class C:
            def execute(self, sql, params=None):
                assert "universe_movers" in sql and "stock_leaders" in sql
            def fetchall(self):
                return rows
        yield None, C()
    monkeypatch.setattr(api, "db_cursor", cursor)
    monkeypatch.setattr(api, "_sp500_map_nowait", lambda: {"ZETA": {"name": "Zeta Global"}, "KO": {"name": "Coca-Cola"}})
    monkeypatch.setattr(api, "_TICKER_LIST", {"data": None, "ts": 0.0, "tried": 0.0})
    out = api.get_ticker_list()
    assert out["available"] and out["tickers"][:2] == [["NVDA", "NVIDIA Corporation"], ["ZETA", "Zeta Global"]]
    assert out["tickers"][-1] == ["KO", "Coca-Cola"] and "_names" not in out and not any(t[0] == "bad ticker!" for t in out["tickers"])
    assert api._known_names()["ZETA"] == "Zeta Global"
    api.get_ticker_list()
    assert calls["n"] == 1                                   # kept in memory
    monkeypatch.setattr(api, "_TICKER_LIST", {"data": None, "ts": 0.0, "tried": 0.0})
    rows[:] = rows[:3]
    assert api.get_ticker_list() == {"available": False, "tickers": []} and api._TICKER_LIST["data"] is None

def test_v79c_sectors_by_the_day_and_the_week(api, monkeypatch):
    """v79c (owner): the sectors by the day, and by the week "from the start of the week to now, not 7 days back"."""
    import datetime as _dt
    import sys as _sys
    from zoneinfo import ZoneInfo
    D, ny = _dt.date, ZoneInfo("America/New_York")
    at = lambda d, h, m=0: _dt.datetime(2026, 11, d, h, m, tzinfo=ny)
    daily = {D(2026, 11, 20): 100.0, D(2026, 11, 23): 104.0, D(2026, 11, 24): 101.0, D(2026, 11, 25): 102.0}
    bars = [(at(23, 10), 103.0), (at(24, 15, 30), 101.0), (at(25, 9, 30), 99.0), (at(25, 15, 30), 102.0)]
    assert api.sector_window("1D", daily, bars) == (D(2026, 11, 25), 101.0, D(2026, 11, 25))     # against Tuesday's close
    assert api.sector_window("1W", daily, bars) == (D(2026, 11, 23), 100.0, D(2026, 11, 25))     # from Monday, against Friday's
    assert api.sector_window("1W", {D(2026, 11, 23): 1.0}, bars) is None                            # no close before the week
    assert api.sector_window("1D", daily, []) is None
    params = {r.path: r for r in api.app.routes if hasattr(r, "dependant")}
    assert "/api/sector-performance" in params
    if _sys.platform == "emscripten":
        pytest.skip("no threads in the browser check; GitHub Actions runs the rest")
    monkeypatch.setattr(api, "_sector_bars", lambda t, step: (daily, bars) if t != "XLE" else (_ for _ in ()).throw(RuntimeError("down")))
    out = api._fetch_sector_intraday("1W")
    assert out["intraday"] and out["since"] == "2026-11-23" and out["session_day"] == "2026-11-25" and len(out["stamps"]) == 4
    assert len(out["series"]) == len(api.SECTOR_ETFS) - 1 and out["series"][0]["values"] == [3.0, 1.0, -1.0, 2.0]
    row = out["table"][0]
    assert row["period_change_pct"] == 2.0 and row["change_pct"] == 0.99 and row["price"] == 102.0
    day = api._fetch_sector_intraday("1D")
    assert len(day["stamps"]) == 2 and day["series"][0]["values"] == [-1.98, 0.99] and day["since"] == "2026-11-25"

# ------------------------------------------------------------------ v80: hot market themes (themes.py, /api/themes)
def _theme_frames(n=80):
    days = pd.date_range("2026-07-01", periods=n, freq="B")
    flat = np.full(n, 100.0)
    up = np.concatenate([np.full(n - 5, 100.0), np.linspace(102, 110, 5)])       # +10% in the last 5 sessions
    close = pd.DataFrame({"SPY": flat, "A": up, "B": up, "C": flat, "D": flat}, index=days)
    vol = pd.DataFrame(1000.0, index=days, columns=close.columns)
    vol.iloc[-5:] = 2000.0                                                          # twice the usual volume this week
    return close, vol


def test_v80_theme_list_is_clean():
    import themes as th
    keys = [t["key"] for t in th.THEMES]
    assert len(keys) == len(set(keys)) and len(keys) >= 20
    for t in th.THEMES:
        m = th.members_of(t)
        assert len(m) == len(set(m)) and len(m) >= 5, t["key"]
        assert all(x == x.upper() for x in m)
        assert t["he"] and t["en"] and t["wiki"] and t["news"]
    # checked against Yahoo 27.11.2026: these are other companies now, or give no prices
    gone = {"GOLD", "SATS", "CYBR", "PSTG", "INFN", "TERN", "ZEAL"}
    assert not gone & set(th.all_tickers())
    assert "SPY" in th.all_tickers()


def test_v80_theme_news_patterns():
    """The news patterns are Postgres regexes (\\y = word boundary); here they run in Python with \\b."""
    import re
    import themes as th
    pat = {t["key"]: re.compile(t["news"].replace(r"\y", r"\b"), re.I) for t in th.THEMES}
    hits = {
        "cyber": ["CrowdStrike shares jump after strong quarter", "Ransomware attack hits hospital chain", "Hackers stole data"],
        "quantum": ["Quantum computing stocks rally as IonQ wins contract"],
        "gold": ["Gold hits a record as the dollar slips"],
        "nuclear": ["Oklo surges on new reactor deal", "Uranium prices climb"],
        "evtol": ["Joby begins air taxi tests in Dubai"],
        "obesity": ["Eli Lilly weight-loss pill wins approval"],
    }
    for key, titles in hits.items():
        for title in titles:
            assert pat[key].search(title), (key, title)
    misses = {"gold": "Golden State Warriors owner buys team", "cyber": "Hackett Group beats estimates",
              "solar": "Solarwinds shares fall", "dc_power": "Gridlock in Congress over the budget"}
    for key, title in misses.items():
        assert not pat[key].search(title), (key, title)


def test_v80_theme_labels():
    import themes as th
    base = dict(n=10, up_1w=7, above50=7, vol_ratio=1.5)
    assert th.theme_label(dict(base, rel_1w=1.0, rel_1m=9.0, rel_3m=20.0)) == "hot"
    assert th.theme_label(dict(base, rel_1w=3.4, rel_1m=-15.0, rel_3m=-20.0)) == "heating"     # cyber, 27.11.2026
    assert th.theme_label(dict(base, rel_1w=3.4, rel_1m=0.0, rel_3m=0.0, vol_ratio=1.1)) == "quiet"   # no volume, no heating
    assert th.theme_label(dict(base, rel_1w=-4.0, rel_1m=-6.0, rel_3m=25.0)) == "cooling"
    assert th.theme_label(dict(base, rel_1w=-4.0, rel_1m=-28.0, rel_3m=-20.0)) == "weak"       # quantum, 27.11.2026
    assert th.theme_label(dict(base, rel_1w=None, rel_1m=None, rel_3m=None)) == "quiet"
    assert th.theme_label(dict(n=0)) == "quiet"


def test_v80_theme_numbers():
    import themes as th
    close, vol = _theme_frames()
    spec = dict(key="x", he="x", en="x", etf=None, members="A B C D MISSING", wiki=["X"], news=r"\yx\y")
    spy = {"r1w": 0.0, "r1m": 0.0, "r3m": 0.0}
    st = th.theme_stats(spec, close, vol, spy)
    assert st["missing"] == ["MISSING"] and st["n"] == 4
    assert st["r1w"] == 5.0 and st["rel_1w"] == 5.0                    # (10 + 10 + 0 + 0) / 4
    assert st["up_1w"] == 2 and 2.0 <= st["vol_ratio"] <= 2.2           # dollar volume: twice the shares, a bit higher price
    # v80b: one line per period, starting at 0, ending at the table's return for that period
    for p, n in th.PERIOD_DAYS.items():
        line = st["series"][p]
        assert len(line) == n + 1 and line[0] == 0.0 and line[-1] == st["r" + p] == 5.0, p
    assert st["members"][0]["ticker"] in ("A", "B") and st["members"][0]["from_high"] == 0.0
    assert st["label"] == "quiet"                                        # only half the stocks went up: not "heating"
    assert th.theme_stats(dict(spec, members="A MISSING"), close, vol, spy) is None     # fewer than 3 members = left out
    out = th.build(close, vol)
    assert out["day"] == close.index[-1].date().isoformat() and out["spy"]["r1w"] == 0.0
    assert {p: len(v) for p, v in out["series_days"].items()} == {p: n + 1 for p, n in th.PERIOD_DAYS.items()}
    assert out["spy_series"]["1m"][-1] == 0.0


def test_v80_theme_attention():
    import themes as th
    days = {f"202611{d:02d}": 100 for d in range(1, 29)}
    days.update({f"202611{d:02d}": 300 for d in range(22, 29)})
    days.update({f"202610{d:02d}": 100 for d in range(20, 32)})
    a = th.attention(days)
    assert a["last7"] == 2100 and a["prev_week_avg"] == 700 and a["ratio"] == 3.0
    assert th.attention({"20261101": 5}) is None                         # a page with a short history = no number


def test_v80_theme_rules_in_words_match_the_code():
    import themes as th
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    he = html[html.index("var THEME_LABEL_RULES_TEXT"):]
    he = he[:he.index("};")]
    r = th.THEME_LABEL_RULES
    assert f"לפחות {r['hot']['rel_1m_min']:g} נקודות" in he and f"{r['hot']['above50_share_min']:.0%}" in he
    assert f"לפחות {r['heating']['rel_1w_min']:g} נקודות" in he and f"פי {r['heating']['vol_ratio_min']:g}" in he
    assert f"ב-{r['cooling']['rel_3m_min']:g} נקודות" in he and f"ב-{-r['cooling']['rel_1m_max']:g} לפחות" in he
    assert f"חלש ב-{-r['weak']['rel_1m_max']:g} נקודות" in he
    if (ROOT / ".github").exists():                                     # GitHub: the job the scheduler starts must exist
        assert (ROOT / ".github" / "workflows" / "themes.yml").exists()
    import scheduler as s
    assert next(j for j in s.JOBS if j["name"] == "themes")["market_days_only"]


def test_v80_themes_endpoint(api):
    params = {r.path for r in api.app.routes if hasattr(r, "dependant")}
    assert "/api/themes" in params


def test_v80c_a_missing_day_counts_the_same_week():
    """v80c (27.11): a stock with no price on the last day (a run 2 minutes after the open) — the table used the stock's own
    last 5 rows (another week) while the chart used the calendar: memory 1W -9.76% in the table, -7.3% on the line."""
    import themes as th
    close, vol = _theme_frames()
    close.loc[close.index[-1], "A"] = np.nan
    spec = dict(key="x", he="x", en="x", etf=None, members="A B C D", wiki=["X"], news=r"\yx\y")
    st = th.theme_stats(spec, close, vol, {"r1w": 0.0, "r1m": 0.0, "r3m": 0.0})
    for p in th.PERIOD_DAYS:
        assert abs(st["series"][p][-1] - st["r" + p]) < 0.06, p


# ------------------------------------------------------------------ v80d: stage 2 — Reddit, headline phrases, S&P groups, tags
def test_v80d_reddit_counts_leave_out_word_tickers():
    import themes as th
    rd = {"IONQ": {"m": 30, "m24": 10, "name": "IonQ"}, "AI": {"m": 9, "m24": 4, "name": "C3.ai"},
          "NOW": {"m": 7, "m24": 30, "name": "ServiceNow"}, "PLTR": {"m": 40, "m24": 40, "name": "Palantir"},
          "MOD": {"m": 50, "m24": 50, "name": "Modine"}}
    sums = th.reddit_theme_sums(rd)
    assert sums["quantum"] == {"mentions": 30, "prev_day": 10}
    # v80e: ApeWisdom counts a word ticker only as $NOW / $AI, so they count; "MOD" (moderators) is shorthand in capitals
    assert sums["ai_software"]["mentions"] == 56 and sums["dc_power"]["mentions"] == 0
    assert {"DTE", "ES", "IT", "ALL", "MOD"} <= th.REDDIT_SKIP and not {"AI", "NOW", "S", "B", "RUN", "NET"} & th.REDDIT_SKIP


def test_v80d_talked_about():
    import themes as th
    rd = {"RGTI": {"m": 60, "m24": 10, "name": "Rigetti Computing"},
          "NVDA": {"m": 220, "m24": 440, "name": "NVIDIA"},                       # down, not a jump
          "SOXL": {"m": 90, "m24": 5, "name": "Direxion Daily Semiconductor Bull 3X Shares ETF"},   # a fund
          "DTE": {"m": 80, "m24": 5, "name": "DTE Energy"},                       # "days to expiration"
          "ZZZZ": {"m": 16, "m24": 1, "name": "Small Co"},
          "TINY": {"m": 9, "m24": 0, "name": "Tiny"}}                              # under the minimum
    out = th.talked_about(rd, [])
    assert [r["ticker"] for r in out] == ["RGTI", "ZZZZ"]      # by the jump: 60 / 10 = 6.0, then 16 / max(1, 3) = 5.3
    assert out[0]["themes"] == ["quantum"] and out[1]["themes"] == [] and out[0]["base_kind"] == "day_before"
    # with 5+ stored days the usual day is their average, not the 24 hours before
    hist = [{"RGTI": 25, "NVDA": 400}] * 5                      # a stock missing from the stored days = under 3 mentions then
    out = th.talked_about(rd, hist)
    assert [r["ticker"] for r in out] == ["ZZZZ"] and out[0]["base_kind"] == "usual"


def test_v80d_rising_phrases():
    import datetime as _dt
    import themes as th
    now = _dt.datetime(2026, 11, 27, 21, tzinfo=_dt.timezone.utc)
    day = lambda n: now - _dt.timedelta(days=n)
    rows = [(f"Humanoid robots draw record orders at maker {i}", day(1)) for i in range(7)]
    rows += [("Humanoid robots shown at fair", day(20))]
    rows += [(f"Fed officials weigh rate path {i}", day(2)) for i in range(8)]
    rows += [(f"Fed officials speak on rates {i}", day(10 + i % 20)) for i in range(40)]      # every week the same
    rows += [("Stock market rises as Nasdaq gains", day(1))] * 9
    out = th.rising_phrases(rows, now)
    phrases = [r["phrase"] for r in out]
    assert any("humanoid robots" in p for p in phrases) and not any("fed officials" in p for p in phrases)
    assert not any("market" in p or "nasdaq" in p for p in phrases)
    r = next(r for r in out if "humanoid robots" in r["phrase"])            # v80e: "humanoid robots draw" (joined pairs)
    assert r["last7"] == 7 and r["prev_week_avg"] == 0.2 and "Humanoid" in r["example"]
    assert th.headline_phrases("Rare-earth stocks: MP Materials, USA Rare Earth soar") >= {"mp materials", "rare earth"}


def test_v80d_subindustries():
    import themes as th
    close, _ = _theme_frames()
    groups = {"A": {"sub": "Semis", "sector": "IT"}, "B": {"sub": "Semis", "sector": "IT"}, "C": {"sub": "Semis", "sector": "IT"},
              "D": {"sub": "Banks", "sector": "Fin"}, "SPY": {"sub": "", "sector": ""}}
    out = th.subindustry_stats(close, groups, {"r1w": 0.0, "r1m": 0.0, "r3m": 0.0})
    assert len(out) == 1 and out[0]["name"] == "Semis" and out[0]["n"] == 3          # Banks has fewer than 3
    assert out[0]["r1w"] == round(20 / 3, 2) and out[0]["members"][0] in ("A", "B")


def test_v80d_theme_sources_in_the_health_check():
    import data_health as dh
    ok = {"themes": [{"key": str(i), "wiki": {"ratio": 1}, "reddit": {"mentions": 1}, "news": {"last7": 1}} for i in range(21)],
          "subind": [{"name": "x"}]}
    assert dh.theme_sources_problems(ok) == []
    bad = {"themes": [{"key": str(i), "wiki": None, "news": None} for i in range(21)]}
    odd = dh.theme_sources_problems(bad)
    assert any("Wikipedia" in o for o in odd) and any("Reddit" in o for o in odd) and any("sub-industries" in o for o in odd)


def test_v80d_theme_changes_in_the_daily_summary(api):
    import datetime as _dt
    import json as _json

    class Cur:
        def __init__(self, rows): self.rows, self.n = rows, 0
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a): self.n += 1
        def fetchone(self): return (True,)
        def fetchall(self): return self.rows

    class Conn:
        def __init__(self, rows): self.rows = rows
        def cursor(self): return Cur(self.rows)

    d = _dt.date(2026, 11, 30)
    rows = [(d, _json.dumps({"quantum": "heating", "nuclear": "quiet", "cyber": "cooling", "gold": "quiet"})),
            (_dt.date(2026, 11, 27), _json.dumps({"quantum": "quiet", "nuclear": "weak", "cyber": "cooling", "gold": "hot"}))]
    out = api._theme_changes_for_day(Conn(rows), d)
    assert {(c["key"], c["from"], c["to"]) for c in out} == {("quantum", "quiet", "heating"), ("gold", "hot", "quiet")}
    assert out[0]["he"] and out[0]["en"]                       # weak -> quiet (nuclear) is noise, left out
    assert api._theme_changes_for_day(Conn(rows[1:]), d) is None   # no row for that day yet
    params = {r.path for r in api.app.routes if hasattr(r, "dependant")}
    assert "/api/themes/tags" in params


def test_v80e_phrases_count_each_headline_once_and_keep_its_hebrew():
    """27.11: "aren't enough" = 15 copies of one headline from several outlets; "nvidia's ceo" / "huang's return" fragments."""
    import datetime as _dt
    import themes as th
    now = _dt.datetime(2026, 11, 27, 21, tzinfo=_dt.timezone.utc)
    ts = now - _dt.timedelta(days=1)
    rows = [("Lower energy prices aren't enough to cool inflation, raising pressure on Fed", "מחירי אנרגיה נמוכים לא מספיקים", ts)] * 15
    rows += [(f"Credit card rate cap {i}: banks push back", f"תקרה על ריבית כרטיסי אשראי {i}", ts) for i in range(7)]
    out = th.rising_phrases(rows, now)
    phrases = [r["phrase"] for r in out]
    assert "aren't enough" not in phrases and "inflation raising" not in phrases
    card = next(r for r in out if "credit card" in r["phrase"])
    assert card["phrase"] == "credit card rate cap"                         # three overlapping pairs = one phrase
    assert card["last7"] == 7 and card["example_he"].startswith("תקרה")
    assert "nvidia ceo" in th.headline_phrases("Jensen Huang returns as Nvidia's CEO")


def test_v80e_every_sp500_group_has_a_hebrew_name():
    import re
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    block = html[html.index("var SUBIND_HE = {"):]
    block = block[:block.index("};")]
    names = re.findall(r'"([^"]+)":\s*"', block)
    assert len(names) == len(set(names)) == 126
    for n in ("Semiconductors", "Casinos & Gaming", "Data Center REITs", "Insurance Brokers", "Electric Utilities"):
        assert n in names
