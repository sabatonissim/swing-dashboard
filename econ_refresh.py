"""Economic-data refresh — runs in GitHub Actions (econ-refresh.yml), NOT on Render.

WHY: from Render the FRED download host times out and the keyless BLS API quota is shared with other Render
customers, so the "actual"/"previous" numbers of the economic calendar never arrived. This job downloads the series
the calendar needs from a different network and stores them in Postgres (table econ_snapshots, key
"series-<id>"); the server reads them from there (econ_calendar.fred_csv).

Order of work (fast and bounded — a hanging source can never stall the run):
  1. ONE request to the BLS public API for all BLS-owned series (CPI, core CPI, PPI, jobs, unemployment, JOLTS).
  2. FRED for everything that is still missing (PCE, GDP, retail, claims, Fed rate), in parallel, one short attempt
     per URL form, hard time limit for the whole step.
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
    raise last


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

    for s, (src, rows) in sorted(stored.items()):
        ec.series_to_db(get_conn, s, rows)
        log(f"{s}: saved {len(rows)} points from {src}, latest {rows[-1][0]} = {rows[-1][1]}")
    missing = [s for s in series if s not in stored]
    log(f"refreshed {len(stored)}/{len(series)} series" + (f"; NOT refreshed: {', '.join(missing)}" if missing else ""))
    sys.stdout.flush()
    os._exit(0 if stored else 1)     # don't wait for threads stuck in a slow download


if __name__ == "__main__":
    main()
