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
    ("TRENDLINE_MIN_TOUCHES", "_TL_MIN_TOUCHES"),
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


FIXTURE_TICKERS = ["KO", "ANET", "NOW", "NVDA"]
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

        s, a = scanner.detect_ma150_breakout(h), api._scanner_rule_ma150_breakout(h)
        assert (s is None) == (a is None), f"150-MA reclaim: {where}"
        if s:
            assert a["days_since_cross"] == (1 if s["held"] else 0) and s["ma150"] == a["key_level"], f"150-MA reclaim: {where}"


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


def test_fundamentals_stale_series():
    """v27: a series the company stopped reporting (JPM debt 2014, BRK EPS 2013) is dropped, a recent one is kept."""
    import fundamentals
    assert fundamentals._drop_if_stale({"2014-06-30": 1.0}, "2026-09-30") == {}
    assert fundamentals._drop_if_stale({"2026-06-30": 1.0}, "2026-09-30") == {"2026-06-30": 1.0}
    assert fundamentals._drop_if_stale({}, "2026-09-30") == {}


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
    assert params["/api/macro-news/search"] == {"q", "days", "limit"}
    assert params["/api/daily-digest"] == {"day", "top_n"}
    assert params["/api/heatmap"] == {"index"}
    assert params["/api/demo-portfolio"] == {"min_score", "top_per_day", "amount", "capital"}
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
