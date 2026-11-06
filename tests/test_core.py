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
    db = _FakeDb([first, [dict(first[0], summary_he="הפד")]])
    monkeypatch.setattr(api, "db_cursor", db)
    events = api._recent_key_events()
    assert api._key_cache["last_id"] == 4 and 4 not in api._key_cache["rows"]        # kept in memory: only key-event headlines
    assert db.sql[0][1][0] == 0 and "SELECT *" not in db.sql[0][0]
    if events:
        assert "ANY" in db.sql[1][0] and events[0]["row"].get("summary_he") == "הפד"
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
    full = lambda i: dict(id=i, summary_en=f"A long enough headline about the economy number {i}", summary_he="כותרת", source_name="Reuters",
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
        {"overview": overview, "topics": [{"title": "AI selloff", "summary": summary, "impact": "", "items": [1]}]})
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


def test_digest_market_text_and_size():
    import ai_digest as d
    text = d._market_text(_CRASH_MARKET, __import__("datetime").date(2026, 11, 5))
    assert "S&P 500: -4.36%" in text and "73 / 430" in text and "NVDA -9.10%" in text and "EXTREME" in text and "4.50% (change -0.05" in text
    closed = dict(_CRASH_MARKET, same_day=False, move="closed")
    assert "LAST session" in d._market_text(closed, __import__("datetime").date(2026, 11, 7))
    assert "not available" in d._market_text(None, __import__("datetime").date(2026, 11, 7))
    size = lambda sp, nq: d.move_size([{"symbol": "^GSPC", "change_pct": sp}, {"symbol": "^IXIC", "change_pct": nq}], True)
    assert size(-4.36, -5.52) == "extreme" and size(-0.5, 2.6) == "extreme" and size(1.2, 1.0) == "notable" and size(0.2, 0.3) == "normal"
    assert d.move_size([], False) == "closed"


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


def test_digest_health_rule():
    import data_health as dh
    assert dh.digest_problems({"overview_en": "Technology dominated.", "market": _CRASH_MARKET})
    assert dh.digest_problems({"overview_en": "The S&P 500 fell 4.4%.", "market": _CRASH_MARKET}) == []
    assert dh.digest_problems({"overview_en": "Technology dominated.", "market": dict(_CRASH_MARKET, move="normal")}) == []
    assert dh.digest_problems({"overview_en": "An older day."}) == []


def test_headline_filters_of_this_round():
    from news_links import is_unwanted_headline as bad
    assert bad("Philippine Inflation Surges to 7.8%, Boosting Case for Rate Hike")
    assert bad("Magal: Q3 Earnings Snapshot") and bad("Under Armour: Fiscal Q2 Earnings Snapshot")
    assert bad("Aflac Q3 Earnings Call Highlights") and bad("What Wendy's (WEN) Said on Its Q3 Earnings Call")
    assert not bad("ConocoPhillips Q3 Earnings Beat Estimates on Higher Commodity Prices")
    assert not bad("Asian stocks fall as India and Indonesia follow Wall Street lower")


def test_key_events_of_this_round(api):
    of = api._key_event_of
    assert of("Stray Drones Spawn a Growing Area of the Insurance Market") is None
    assert of("A prolonged war in Iran is the market's real black swan, says this strategist") is None
    assert of("Dollar edges up as oil prices bounce, payrolls eyed") is None
    assert of("What CPI Card Group (PMTS) said on its earnings call") is None
    assert of("US payrolls rose by 150,000 in October")[0] == "jobs"
    assert of("Oil jumps as missiles hit tankers in the Strait of Hormuz")[0].startswith("geo")
    crash = of("A brutal sell-off sends Wall Street reeling, and the S&P 500 tumbles 4.4%")
    assert crash[0] == "market" and crash[1] > of("ECB holds rates steady, Lagarde says")[1]


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
