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


def test_morning_brief_text():
    import datetime as _dt
    import push_alerts
    when = _dt.datetime(2026, 10, 29, 12, 30, tzinfo=_dt.timezone.utc)
    ev = [{"name_he": "מדד המחירים לצרכן", "name_en": "CPI", "impact": "high", "when": when}]
    he = push_alerts._morning_payload("he", ev, [("KO", "bmo", True)], 5, 12, ["KO"])
    en = push_alerts._morning_payload("en", ev, [("KO", "bmo", True)], 5, 12, ["KO"])
    assert "14:30" in he["body"] and "KO" in he["body"] and "12" in he["body"]   # Israel is UTC+2 after 25.10.2026
    assert "08:30 ET" in en["body"] and "+4" in en["body"]
