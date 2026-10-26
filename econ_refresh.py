"""Economic-data refresh — runs in GitHub Actions (econ-refresh.yml), NOT on Render.

WHY: from Render the FRED download host times out and the keyless BLS API quota is shared with other Render
customers, so the "actual"/"previous" numbers of the economic calendar never arrived. This job downloads the series
the calendar needs from a different network and stores them in Postgres (table econ_snapshots, key
"series-<id>"); the server reads them from there (econ_calendar.fred_csv).

Order of work (fast and bounded — a hanging source can never stall the run):
  1. ONE request to the BLS public API for all BLS-owned series (CPI, core CPI, PPI, jobs, unemployment, JOLTS).
  2. FRED for everything that is still missing (PCE, GDP, retail, claims, Fed rate), in parallel, one short attempt
     per URL form, hard time limit for the whole step.
  3. Keyless stand-ins for what FRED did not deliver: NY Fed (Fed rate), DBnomics (PCE, core PCE, GDP), the Labor
     Department (jobless claims) and the Census release page (retail sales).
A series that cannot be downloaded keeps its previous stored copy. The run fails (red) only if NOTHING refreshed.
Needs only the DATABASE_URL secret (FRED_API_KEY / BLS_API_KEY optional).     Manual run: python -u econ_refresh.py
"""
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

import psycopg2

import econ_calendar as ec

FRED_STEP_BUDGET_S = 150        # whole FRED step
FRED_ATTEMPT_TIMEOUT_S = 20     # per HTTP attempt


def log(msg):
    print(f"[econ_refresh] {msg}", flush=True)


def get_conn():
    return psycopg2.connect(os.environ["DATABASE_URL"])


def fred_download(series):
    """Tries the official API (if a key exists), then the CSV download in two URL forms. Short timeouts."""
    cosd = (date.today() - timedelta(days=900)).isoformat()
    last = None
    if (os.environ.get("FRED_API_KEY") or "").strip():
        try:
            return ec._fred_api_rows(series, cosd)
        except Exception as e:  # noqa: BLE001
            last = e
    for url in (f"{ec.FRED_CSV}?id={series}&cosd={cosd}",
                f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"):
        try:
            return ec._parse_fred_csv(ec._http_text(url, timeout=FRED_ATTEMPT_TIMEOUT_S), series)
        except Exception as e:  # noqa: BLE001
            last = e
    # Third form: FRED's static data file. It is a plain file (not generated per request like the CSV download), so it can
    # answer when the CSV host times out. Only the last ~900 days are kept, like the CSV.
    try:
        rows = ec._parse_fred_txt(ec._http_text(ec.FRED_TXT.format(series=series), timeout=FRED_ATTEMPT_TIMEOUT_S), series)
        keep = [r for r in rows if r[0].isoformat() >= cosd]
        if keep:
            return keep
    except Exception as e:  # noqa: BLE001
        last = e
    raise last


# --------------------------------------------------------------------------
# Keyless stand-ins for FRED (v18, 26.10.2026). fred.stlouisfed.org did not answer from Render, from GitHub, nor from the
# owner's home PC (every URL timed out) — so 6 series never arrived. These official-data sources answered at once, with
# no key and no sign-up. Used only for series FRED did not deliver.
#   * New York Fed rates API: the Fed's target range (upper bound) published with every daily EFFR rate = DFEDTARU.
#   * DBnomics: a public mirror of BEA's NIPA tables (same index base and numbers as FRED). It re-reads BEA once a day
#     (~01:30 UTC), so a new PCE/GDP "actual" shows up about a day after the release, not within minutes.
#   * Labor Department (jobless claims) and the Census release page (retail sales) — see dol_claims / census_retail below.
# --------------------------------------------------------------------------
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/search.json?startDate={start}&endDate={end}"
DBNOMICS = "https://api.db.nomics.world/v22/series/{code}?observations=1&format=json"
DBNOMICS_SERIES = {                         # FRED id -> DBnomics series (BEA table / line)
    "PCEPI": "BEA/NIPA-T20804/DPCERG-M",                # PCE price index, Table 2.8.4 line 1
    "PCEPILFE": "BEA/NIPA-T20804/DPCCRG-M",             # PCE excluding food and energy, line 25
    "A191RL1Q225SBEA": "BEA/NIPA-T10101/A191RL-Q",      # real GDP, % change at annual rate, Table 1.1.1 line 1
}


def nyfed_target_upper():
    import json
    end = date.today()
    start = end - timedelta(days=900)
    js = json.loads(ec._http_text(NYFED_EFFR.format(start=start.isoformat(), end=end.isoformat()), timeout=20))
    rows = {}
    for r in js.get("refRates", []):
        try:
            rows[date.fromisoformat(r["effectiveDate"])] = float(r["targetRateTo"])
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        raise RuntimeError("NY Fed API returned no target-rate rows")
    return sorted(rows.items())


def dbnomics_rows(series):
    import json
    js = json.loads(ec._http_text(DBNOMICS.format(code=DBNOMICS_SERIES[series]), timeout=20))
    doc = js["series"]["docs"][0]
    cosd = date.today() - timedelta(days=900)
    rows = []
    for start, value in zip(doc.get("period_start_day", []), doc.get("value", [])):
        try:
            d, v = date.fromisoformat(start), float(value)       # "NA" = no value -> skipped
        except (TypeError, ValueError):
            continue
        if d >= cosd:
            rows.append((d, v))
    if not rows:
        raise RuntimeError(f"DBnomics returned no data for {series}")
    return rows


# Jobless claims (ICSA): the Labor Department's own weekly-claims report, national, as XML (the form on
# oui.doleta.gov/unemploy/claims.asp). "SA" = seasonally adjusted initial claims, the figure FRED's ICSA carries; the week
# date is the Saturday the week ended, like FRED. Weeks not yet published come back as &#160; and are skipped.
DOL_CLAIMS = "https://oui.doleta.gov/unemploy/wkclaims/report.asp"


def dol_claims():
    import re
    import requests
    year = date.today().year
    r = requests.post(DOL_CLAIMS, data={"level": "us", "final_yr": str(year + 1), "strtdate": str(year - 2),
                                        "enddate": str(year), "filetype": "xml", "submit": "Submit"},
                      headers=ec.HTTP_HEADERS_ALT, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for oui.doleta.gov")
    rows = []
    for wk in re.findall(r"(?s)<week>(.*?)</week>", r.text):
        m_d = re.search(r"<weekEnded>(\d\d)/(\d\d)/(\d{4})</weekEnded>", wk)
        block = re.search(r"(?s)<InitialClaims>(.*?)</InitialClaims>", wk)     # not the ContinuedClaims <SA>
        m_v = re.search(r"<SA>([\d,]+)</SA>", block.group(1)) if block else None
        if m_d and m_v:
            rows.append((date(int(m_d.group(3)), int(m_d.group(1)), int(m_d.group(2))), float(m_v.group(1).replace(",", ""))))
    if not rows:
        raise RuntimeError("DOL claims report had no seasonally adjusted rows")
    return sorted(rows)


# Retail sales (RSAFS): the Census release page states the newest month in one sentence — "...for September 2026 ... were
# $745.0 billion, up 0.8 percent ... from the previous month ... The July 2026 to August 2026 percent change was revised
# from up 1.1 percent ... to up 1.3 percent". The calendar shows month-on-month % changes, so three monthly levels are
# rebuilt from the level and the two official % changes (the older two levels are DERIVED, only the % changes are
# official; they reproduce exactly the published figures for "actual" and "previous").
CENSUS_RETAIL = "https://www.census.gov/retail/sales.html"
_MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}


def _census_pct(phrase):
    import re
    if re.match(r"\s*(?:virtually\s+)?unchanged", phrase, re.I):
        return 0.0
    m = re.match(r"\s*(up|down)\s+([\d.]+)\s+percent", phrase, re.I)
    if not m:
        return None
    return float(m.group(2)) * (1 if m.group(1).lower() == "up" else -1)


def census_retail():
    import html
    import re
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", ec._http_text(CENSUS_RETAIL, timeout=25))))
    m = re.search(r"retail and food services sales for (\w+) (\d{4}),.{0,200}?were \$([\d,.]+) billion,"
                  r"((?:\s*(?:virtually\s+)?unchanged|\s*(?:up|down) [\d.]+ percent)).{0,60}?from the previous month", text, re.I)
    if not m or m.group(1).lower() not in _MONTHS:
        raise RuntimeError("Census retail page: headline sentence not found")
    month = date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
    level = float(m.group(3).replace(",", "")) * 1000          # millions of dollars, like FRED's RSAFS
    pct = _census_pct(m.group(4))
    if pct is None:
        raise RuntimeError("Census retail page: monthly change not understood")
    prev_month = (month.replace(day=1) - timedelta(days=1)).replace(day=1)
    rows = [(prev_month, level / (1 + pct / 100)), (month, level)]
    r = re.search(r"percent change was (?:revised|unrevised) from (?:(?:virtually\s+)?unchanged|(?:up|down) [\d.]+ percent)"
                  r"(?:\s*\([^)]{0,30}\))?(?:\s+to\b((?:\s*(?:virtually\s+)?unchanged|\s*(?:up|down) [\d.]+ percent)))?", text, re.I)
    if r:
        prev_pct = _census_pct(r.group(1)) if r.group(1) else _census_pct(re.search(
            r"from ((?:virtually\s+)?unchanged|(?:up|down) [\d.]+ percent)", r.group(0), re.I).group(1))
        if prev_pct is not None:
            older = (prev_month - timedelta(days=1)).replace(day=1)
            rows.insert(0, (older, rows[0][1] / (1 + prev_pct / 100)))
    return rows


def main():
    series = sorted({v["fred"]["series"] for v in ec.CATALOG.values() if v.get("fred")})
    stored = {}

    # 1) BLS public API — one request covers every BLS-owned series
    try:
        ec._bls_api_load()
        for s in series:
            if s in ec.BLS_FALLBACK_SERIES and ec._cache.get(("blsapi", s)):
                stored[s] = ("BLS API", ec._cache[("blsapi", s)][1])
        log(f"BLS API: {len(stored)} series")
    except Exception as e:  # noqa: BLE001
        log(f"BLS API failed — {type(e).__name__}: {str(e)[:160]}")

    # 2) FRED for the rest, parallel and time-boxed
    todo = [s for s in series if s not in stored]
    if todo:
        log(f"FRED: trying {len(todo)} series ({', '.join(todo)}) — up to {FRED_STEP_BUDGET_S}s")
        pool = ThreadPoolExecutor(max_workers=6)
        futures = {pool.submit(fred_download, s): s for s in todo}
        deadline = time.time() + FRED_STEP_BUDGET_S
        try:
            for fut in as_completed(futures, timeout=max(1, deadline - time.time())):
                s = futures[fut]
                try:
                    stored[s] = ("FRED", fut.result())
                except Exception as e:  # noqa: BLE001
                    log(f"FRED {s}: failed — {type(e).__name__}: {str(e)[:120]}")
        except Exception:  # noqa: BLE001  (time budget used up)
            log("FRED: time budget used up — the rest keeps its stored copy")
        pool.shutdown(wait=False, cancel_futures=True)

    # 3) keyless stand-ins for whatever FRED did not deliver
    fallbacks = {"DFEDTARU": ("NY Fed", nyfed_target_upper), "ICSA": ("DOL", dol_claims),
                 "RSAFS": ("Census", census_retail)}
    fallbacks.update({s: ("DBnomics (BEA)", lambda s=s: dbnomics_rows(s)) for s in DBNOMICS_SERIES})
    for s, (src, fn) in fallbacks.items():
        if s in series and s not in stored:
            try:
                stored[s] = (src, fn())
            except Exception as e:  # noqa: BLE001
                log(f"{src} {s}: failed — {type(e).__name__}: {str(e)[:120]}")

    for s, (src, rows) in sorted(stored.items()):
        ec.series_to_db(get_conn, s, rows)
        log(f"{s}: saved {len(rows)} points from {src}, latest {rows[-1][0]} = {rows[-1][1]}")
    missing = [s for s in series if s not in stored]
    log(f"refreshed {len(stored)}/{len(series)} series" + (f"; NOT refreshed: {', '.join(missing)}" if missing else ""))
    sys.stdout.flush()
    os._exit(0 if stored else 1)     # don't wait for threads stuck in a slow download


if __name__ == "__main__":
    main()
